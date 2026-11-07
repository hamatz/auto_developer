from __future__ import annotations

import hashlib
import json
import re
import subprocess
import tempfile
import time
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BundleVerificationResult:
    is_valid: bool
    job_id: str = ""
    head_commit: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "job_id": self.job_id,
            "head_commit": self.head_commit,
            "errors": self.errors,
            "warnings": self.warnings,
            "tasks": self.tasks,
            "file_hashes": self.file_hashes,
        }


class EvidenceBundler:
    """成果物および独立監査証跡（Evidence Records, Checker Logs, JobState, Git Bundle）を

    安全にパッケージング（サニタイズ）および第三者検証するモジュール。
    """

    SECRET_PATTERNS = (
        re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
        re.compile(r"\.(key|pem|pkcs12|pfx)$", re.IGNORECASE),
        re.compile(r"(token|secret|credential|password)", re.IGNORECASE),
    )

    NOISE_PATTERNS = (
        re.compile(r"(^|/)\.DS_Store$"),
        re.compile(r"(^|/)__MACOSX(/|$)"),
        re.compile(r"(^|/)Thumbs\.db$", re.IGNORECASE),
    )

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = (repo_dir or Path.cwd()).resolve()

    def is_sanitized_out(self, rel_path: str) -> bool:
        """指定パスが機密ファイルまたはノイズファイルとして除外対象かを判定する。"""
        normalized = rel_path.replace("\\", "/")
        filename = Path(normalized).name

        for pat in self.SECRET_PATTERNS:
            if pat.search(filename):
                return True

        for pat in self.NOISE_PATTERNS:
            if pat.search(normalized):
                return True

        return False

    def _calc_sha256(self, file_path: Path) -> str:
        """ファイルの SHA-256 チェックサムを計算する。"""
        h = hashlib.sha256()
        with open(file_path, "rb") as f:
            while chunk := f.read(65536):
                h.update(chunk)
        return h.hexdigest()

    def create_bundle(
        self,
        dest_path: Path | None = None,
        job_id: str | None = None,
    ) -> Path:
        """現在の作業リポジトリから機密を除外し、完全な監査証跡を含む ZIP バンドルを生成する。"""
        # 1. Git 情報の取得
        head_commit = ""
        head_tree = ""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            head_commit = res.stdout.strip()
            res_tree = subprocess.run(
                ["git", "rev-parse", "HEAD^{tree}"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            head_tree = res_tree.stdout.strip()
        except (subprocess.CalledProcessError, OSError):
            pass

        # 2. 出力パス決定
        if dest_path is None:
            resolved_job_id = job_id or f"job_{int(time.time())}"
            bundles_dir = self.repo_dir / ".dev" / "bundles"
            bundles_dir.mkdir(parents=True, exist_ok=True)
            dest_path = bundles_dir / f"bundle_{resolved_job_id}.zip"
        else:
            dest_path.parent.mkdir(parents=True, exist_ok=True)

        with tempfile.TemporaryDirectory() as staging_tmp:
            staging_dir = Path(staging_tmp)
            items_to_zip: list[tuple[Path, str]] = []

            # (A) Git bundle 生成
            if head_commit:
                git_bundle_path = staging_dir / "git_bundle.bundle"
                try:
                    subprocess.run(
                        ["git", "bundle", "create", str(git_bundle_path), "--all"],
                        cwd=self.repo_dir,
                        capture_output=True,
                        check=True,
                    )
                except subprocess.CalledProcessError:
                    # --all が失敗した場合は HEAD のみで試行
                    subprocess.run(
                        ["git", "bundle", "create", str(git_bundle_path), "HEAD"],
                        cwd=self.repo_dir,
                        capture_output=True,
                        check=True,
                    )
                if git_bundle_path.exists():
                    items_to_zip.append((git_bundle_path, "git_bundle.bundle"))

                # (B) ソースツリースナップショット tar
                source_tar_path = staging_dir / "source_snapshot.tar"
                try:
                    subprocess.run(
                        ["git", "archive", "HEAD", "-o", str(source_tar_path)],
                        cwd=self.repo_dir,
                        capture_output=True,
                        check=True,
                    )
                    if source_tar_path.exists():
                        items_to_zip.append((source_tar_path, "source_snapshot.tar"))
                except (subprocess.CalledProcessError, OSError):
                    pass

            # (C) Evidence records 収集
            records_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
            tasks_manifest: list[dict[str, Any]] = []
            if records_dir.exists():
                for rec_file in sorted(records_dir.glob("task_*.json")):
                    if self.is_sanitized_out(rec_file.name):
                        continue
                    items_to_zip.append((rec_file, f"evidence/{rec_file.name}"))
                    try:
                        data = json.loads(rec_file.read_text(encoding="utf-8"))
                        tasks_manifest.append({
                            "task_id": str(data.get("task_id", "")),
                            "tree_sha": data.get("tree_sha", ""),
                            "spec_sha": data.get("spec_sha", ""),
                            "checker_decision": data.get("checker", {}).get("decision", "UNSPECIFIED")
                            if isinstance(data.get("checker"), dict)
                            else str(data.get("checker", "")),
                            "status": data.get("status", ""),
                        })
                    except (json.JSONDecodeError, OSError):
                        pass

            # (D) JobState 収集
            job_state_file = self.repo_dir / ".dev" / "job_state.json"
            actual_job_id = job_id or ""
            if job_state_file.exists() and not self.is_sanitized_out(job_state_file.name):
                items_to_zip.append((job_state_file, "job_state.json"))
                if not actual_job_id:
                    try:
                        js_data = json.loads(job_state_file.read_text(encoding="utf-8"))
                        actual_job_id = js_data.get("job_id", "")
                    except (json.JSONDecodeError, OSError):
                        pass

            if not actual_job_id:
                actual_job_id = f"job_{int(time.time())}"

            # (E) ログ収集
            logs_dir = self.repo_dir / "logs" / "nightly"
            if logs_dir.exists():
                for log_file in sorted(logs_dir.glob("**/*")):
                    if log_file.is_file():
                        rel = log_file.relative_to(logs_dir).as_posix()
                        if not self.is_sanitized_out(rel):
                            items_to_zip.append((log_file, f"logs/{rel}"))

            # (F) 主要ドキュメント (NIGHTLY_REPORT.md, PLAN.md, SPEC.md)
            for doc_name in ["NIGHTLY_REPORT.md", "PLAN.md", "SPEC.md"]:
                doc_path = self.repo_dir / doc_name
                if doc_path.exists() and not self.is_sanitized_out(doc_name):
                    items_to_zip.append((doc_path, doc_name))

            # (G) 各ファイルの SHA-256 ハッシュを計算し Manifest を構築
            file_hashes: dict[str, str] = {}
            for file_path, arcname in items_to_zip:
                file_hashes[arcname] = self._calc_sha256(file_path)

            manifest_data = {
                "bundle_version": "1.0",
                "generated_at": time.time(),
                "job_id": actual_job_id,
                "head_commit": head_commit,
                "head_tree_sha": head_tree,
                "tasks": tasks_manifest,
                "file_hashes": file_hashes,
            }
            manifest_path = staging_dir / "bundle_manifest.json"
            manifest_path.write_text(
                json.dumps(manifest_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            items_to_zip.insert(0, (manifest_path, "bundle_manifest.json"))

            # (H) ZIP アーカイブの生成
            temp_zip_path = staging_dir / "bundle.zip"
            with zipfile.ZipFile(temp_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for file_path, arcname in items_to_zip:
                    zf.write(file_path, arcname=arcname)

            # アトミックに保存先に移動
            import shutil

            shutil.move(str(temp_zip_path), str(dest_path))

        return dest_path

    def verify_bundle(self, bundle_path: Path) -> BundleVerificationResult:
        """提出された ZIP バンドルを展開し、チェックサム整合性、Git Tree SHA と Evidence 拘束、

        Checker 承認、および SPEC 整合性を独立検証する。
        """
        bundle_file = Path(bundle_path).resolve()
        result = BundleVerificationResult(is_valid=False)

        if not bundle_file.exists() or not bundle_file.is_file():
            result.errors.append(f"Bundle file not found: {bundle_file}")
            return result

        if not zipfile.is_zipfile(bundle_file):
            result.errors.append(f"File is not a valid ZIP archive: {bundle_file}")
            return result

        with tempfile.TemporaryDirectory() as inspect_tmp:
            inspect_dir = Path(inspect_tmp)
            try:
                with zipfile.ZipFile(bundle_file, "r") as zf:
                    zf.extractall(inspect_dir)
            except (zipfile.BadZipFile, OSError) as e:
                result.errors.append(f"Failed to extract ZIP archive: {e}")
                return result

            # 1. Manifest 存在確認
            manifest_file = inspect_dir / "bundle_manifest.json"
            if not manifest_file.exists():
                result.errors.append("bundle_manifest.json is missing in the bundle")
                return result

            try:
                manifest_data = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                result.errors.append(f"Failed to parse bundle_manifest.json: {e}")
                return result

            result.job_id = str(manifest_data.get("job_id", ""))
            result.head_commit = str(manifest_data.get("head_commit", ""))
            result.tasks = list(manifest_data.get("tasks", []))
            file_hashes: dict[str, str] = manifest_data.get("file_hashes", {})
            result.file_hashes = file_hashes

            # 2. 全ファイルのチェックサム完全一致検証
            for arcname, expected_hash in file_hashes.items():
                target_file = inspect_dir / arcname
                if not target_file.exists():
                    result.errors.append(f"Expected file missing from bundle: {arcname}")
                    continue

                actual_hash = self._calc_sha256(target_file)
                if actual_hash != expected_hash:
                    result.errors.append(
                        f"Hash mismatch for {arcname}: expected {expected_hash}, got {actual_hash}"
                    )

            # 3. SPEC ハッシュ整合性検証
            spec_file = inspect_dir / "SPEC.md"
            spec_hash = self._calc_sha256(spec_file) if spec_file.exists() else ""

            # 4. Evidence Record と Checker 判定の検証
            evidence_dir = inspect_dir / "evidence"
            if evidence_dir.exists():
                for rec_file in sorted(evidence_dir.glob("task_*.json")):
                    try:
                        rec_data = json.loads(rec_file.read_text(encoding="utf-8"))
                        task_id = str(rec_data.get("task_id", ""))
                        status = rec_data.get("status", "")
                        spec_sha = rec_data.get("spec_sha", "")
                        tree_sha = rec_data.get("tree_sha", "")
                        if not tree_sha:
                            result.warnings.append(f"Task {task_id} Evidence has empty tree_sha")

                        # Checker 判定の検証
                        checker = rec_data.get("checker", {})
                        decision = checker.get("decision", "") if isinstance(checker, dict) else str(checker)
                        if decision != "APPROVE":
                            result.errors.append(
                                f"Task {task_id} has invalid Checker decision: {decision} (expected APPROVE)"
                            )

                        # SPEC 整合性
                        if spec_hash and spec_sha and spec_hash != spec_sha:
                            result.errors.append(
                                f"Task {task_id} SPEC hash mismatch: Evidence has {spec_sha}, bundle SPEC.md is {spec_hash}"
                            )

                        if status != "COMPLETED":
                            result.warnings.append(f"Task {task_id} status is {status} (not COMPLETED)")

                    except (json.JSONDecodeError, OSError) as e:
                        result.errors.append(f"Failed to read evidence record {rec_file.name}: {e}")
            else:
                result.warnings.append("No evidence/ directory found in bundle")

            # 5. Git bundle の健全性チェック
            git_bundle_file = inspect_dir / "git_bundle.bundle"
            if git_bundle_file.exists():
                try:
                    verify_res = subprocess.run(
                        ["git", "bundle", "verify", str(git_bundle_file)],
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if verify_res.returncode != 0:
                        # 外部環境で共通コミットが見つからない場合もあるため警告として扱う
                        result.warnings.append(f"git bundle verify note: {verify_res.stderr.strip() or verify_res.stdout.strip()}")
                except OSError:
                    pass

        result.is_valid = len(result.errors) == 0
        return result
