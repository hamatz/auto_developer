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

BUNDLE_FORMAT_VERSION = 3


def _get_tool_version() -> str:
    """パッケージのバージョン文字列を循環参照なしで取得する。"""
    import auto_developer

    version = getattr(auto_developer, "__version__", None)
    if isinstance(version, str) and version:
        return version
    from auto_developer.cli import VERSION

    return VERSION


def find_release_blockers(plan_text: str) -> list[str]:
    """未完了の - [ ] 行のうち (Severity: P0 または (Severity: P1 を含む行、および - ESCALATED 行を返す (WWW.3)。"""
    from auto_developer.dispatcher import (
        extract_plan_scope_lines,
        strip_html_comments_preserving_lines,
    )

    blockers: list[str] = []
    for _, line in extract_plan_scope_lines(plan_text):
        stripped = line.strip()
        if stripped.startswith("- [ ]") and ("(Severity: P0" in line or "(Severity: P1" in line):
            blockers.append(stripped)
    for line in strip_html_comments_preserving_lines(plan_text).splitlines():
        stripped = line.strip()
        if stripped.startswith("- ESCALATED"):
            blockers.append(stripped)
    return blockers


@dataclass
class BundleVerificationResult:
    is_valid: bool
    status: str = "INVALID"  # "VERIFIED" | "INTEGRITY_ONLY" | "INVALID"
    job_id: str = ""
    head_commit: str = ""
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tasks: list[dict[str, Any]] = field(default_factory=list)
    file_hashes: dict[str, str] = field(default_factory=dict)
    audit_stage_verified: bool = False
    audit_stage_info: dict[str, Any] | None = None
    backlog_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "status": self.status,
            "job_id": self.job_id,
            "head_commit": self.head_commit,
            "errors": self.errors,
            "warnings": self.warnings,
            "tasks": self.tasks,
            "file_hashes": self.file_hashes,
            "audit_stage_verified": self.audit_stage_verified,
            "audit_stage_info": self.audit_stage_info,
            "backlog_count": self.backlog_count,
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

    def __init__(self, repo_dir: Path | None = None, plan_path: Path | None = None) -> None:
        self.repo_dir = (repo_dir or Path.cwd()).resolve()
        self.plan_path = plan_path

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
        plan_path: Path | None = None,
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
            candidate_dirs: list[Path] = [
                self.repo_dir / ".git" / "nightly_evidence" / "records",
                self.repo_dir / ".git" / "nightly_evidence" / "archive",
                self.repo_dir / ".git" / "nightly_evidence",
                self.repo_dir / ".dev" / "evidence" / "records",
                self.repo_dir / ".dev" / "evidence",
            ]

            # Git common-dir (worktree 対応) の解決
            try:
                c_res = subprocess.run(
                    ["git", "rev-parse", "--git-common-dir"],
                    cwd=self.repo_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if c_res.returncode == 0 and c_res.stdout.strip():
                    common_p = Path(c_res.stdout.strip())
                    if not common_p.is_absolute():
                        common_p = (self.repo_dir / common_p).resolve()
                    candidate_dirs.extend([
                        common_p / "nightly_evidence" / "records",
                        common_p / "nightly_evidence" / "archive",
                        common_p / "nightly_evidence",
                    ])
            except OSError:
                pass

            from auto_developer.dispatcher import get_plan_task_states, resolve_target_plan_path
            from auto_developer.evidence import EvidenceStore

            evidence_store = EvidenceStore(repo_dir=self.repo_dir)

            target_plan_path = resolve_target_plan_path(
                self.repo_dir,
                plan_path if plan_path is not None else self.plan_path,
            )
            has_plan_tasks, completed_task_ids, pending_task_ids, failed_task_ids = (
                get_plan_task_states(target_plan_path)
            )

            tasks_manifest: list[dict[str, Any]] = []
            excluded_records: list[dict[str, str]] = []
            audit_stage_manifest: dict[str, Any] | None = None
            seen_rec_names: set[str] = set()

            for rec_dir in candidate_dirs:
                if not rec_dir.exists() or not rec_dir.is_dir():
                    continue
                # task_*.json および audit_*.json を収集 (SSS.4, TTT.2)
                for pat in ["task_*.json", "audit_*.json"]:
                    for rec_file in sorted(rec_dir.glob(pat)):
                        if rec_file.name in seen_rec_names:
                            continue
                        if self.is_sanitized_out(rec_file.name):
                            continue
                        try:
                            data = json.loads(rec_file.read_text(encoding="utf-8"))
                            seen_rec_names.add(rec_file.name)
                            if rec_file.name.startswith("task_"):
                                rec_task_id = (
                                    str(data.get("task_id", "")).strip()
                                    or rec_file.stem.removeprefix("task_")
                                )
                                if has_plan_tasks and rec_task_id not in completed_task_ids:
                                    if rec_task_id in failed_task_ids:
                                        excl_reason = "task is not completed in target plan (failed)"
                                    elif rec_task_id in pending_task_ids:
                                        excl_reason = "task is not completed in target plan (pending)"
                                    else:
                                        excl_reason = "task is not in target plan"
                                    excluded_records.append({
                                        "task_id": rec_task_id,
                                        "reason": excl_reason,
                                    })
                                    continue

                                checker_obj = data.get("checker", {})
                                review_meta = (
                                    checker_obj.get("review")
                                    if isinstance(checker_obj, dict)
                                    else None
                                )
                                reviews_task_dir = evidence_store.evidence_dir / "reviews" / f"task_{rec_task_id}"
                                rev_out = reviews_task_dir / "checker_output.txt"
                                rev_diff = reviews_task_dir / "diff.patch"
                                rev_spec = reviews_task_dir / "spec_excerpt.md"
                                if (
                                    not isinstance(review_meta, dict)
                                    or not rev_out.is_file()
                                    or not rev_diff.is_file()
                                    or not rev_spec.is_file()
                                ):
                                    excluded_records.append({
                                        "task_id": rec_task_id,
                                        "reason": "NO_REVIEW_EVIDENCE",
                                    })
                                    continue

                                items_to_zip.append((rev_out, f"evidence/reviews/task_{rec_task_id}/checker_output.txt"))
                                items_to_zip.append((rev_diff, f"evidence/reviews/task_{rec_task_id}/diff.patch"))
                                items_to_zip.append((rev_spec, f"evidence/reviews/task_{rec_task_id}/spec_excerpt.md"))

                                commit_val = data.get("commit", "")
                                if isinstance(commit_val, dict):
                                    commit_str = str(
                                        commit_val.get(
                                            "sha",
                                            commit_val.get("hash", commit_val.get("commit_sha", "")),
                                        )
                                    ).strip()
                                else:
                                    commit_str = str(commit_val or "").strip()

                                tasks_manifest.append({
                                    "task_id": str(data.get("task_id", "")),
                                    "commit": commit_str,
                                    "tree_sha": data.get("tree_sha", ""),
                                    "spec_sha": data.get("spec_sha", ""),
                                    "checker": data.get("checker", {}),
                                    "checker_decision": data.get("checker", {}).get("decision", "UNSPECIFIED")
                                    if isinstance(data.get("checker"), dict)
                                    else str(data.get("checker", "")),
                                    "status": data.get("status", ""),
                                })
                            elif rec_file.name == "audit_stage.json":
                                audit_stage_manifest = data

                            items_to_zip.append((rec_file, f"evidence/{rec_file.name}"))
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

            # (F) 主要ドキュメント (NIGHTLY_REPORT.md, PLAN.md, SPEC.md, BACKLOG.md)
            for doc_name in ["NIGHTLY_REPORT.md", "PLAN.md", "SPEC.md", "BACKLOG.md"]:
                doc_path = self.repo_dir / doc_name
                if doc_name == "NIGHTLY_REPORT.md":
                    job_report_path = self.repo_dir / ".dev" / "job_report.md"
                    if job_report_path.exists():
                        doc_path = job_report_path
                if doc_path.exists() and not self.is_sanitized_out(doc_name):
                    items_to_zip.append((doc_path, doc_name))

            backlog_count = 0
            backlog_file = self.repo_dir / "BACKLOG.md"
            if backlog_file.is_file():
                for b_line in backlog_file.read_text(encoding="utf-8").splitlines():
                    b_stripped = b_line.strip()
                    if (
                        b_stripped.startswith("- [")
                        and not b_stripped.startswith("- [ ]")
                        and not b_stripped.startswith("- [x]")
                    ):
                        backlog_count += 1

            # (G) 各ファイルの SHA-256 ハッシュを計算し Manifest を構築
            file_hashes: dict[str, str] = {}
            for file_path, arcname in items_to_zip:
                file_hashes[arcname] = self._calc_sha256(file_path)

            manifest_data: dict[str, Any] = {
                "bundle_version": "1.0",
                "format_version": BUNDLE_FORMAT_VERSION,
                "tool_version": _get_tool_version(),
                "generated_at": time.time(),
                "job_id": actual_job_id,
                "head_commit": head_commit,
                "head_tree_sha": head_tree,
                "tasks": tasks_manifest,
                "excluded_records": excluded_records,
                "file_hashes": file_hashes,
                "backlog_count": backlog_count,
            }
            if audit_stage_manifest is not None:
                manifest_data["audit_stage"] = audit_stage_manifest
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

            if not isinstance(manifest_data, dict):
                result.errors.append("bundle_manifest.json must contain a JSON object")
                return result

            result.job_id = str(manifest_data.get("job_id", ""))
            result.head_commit = str(manifest_data.get("head_commit", ""))

            format_version = manifest_data.get("format_version")
            if isinstance(format_version, bool) or not isinstance(format_version, int):
                result.errors.append(
                    "Unsupported bundle format (format_version missing or invalid): "
                    "v0.8 以前の形式です。v0.8.0 の auto-dev で検証してください。"
                )
                return result
            if format_version < BUNDLE_FORMAT_VERSION:
                if format_version < 2:
                    result.errors.append(
                        f"Unsupported bundle format_version={format_version}: "
                        "v0.8 以前の形式です。v0.8.0 の auto-dev で検証してください。"
                    )
                else:
                    result.errors.append(
                        f"Unsupported bundle format_version={format_version}: "
                        "v0.9 開発版までの形式です。v0.9 系の auto-dev で検証してください。"
                    )
                return result
            if format_version > BUNDLE_FORMAT_VERSION:
                result.errors.append(
                    f"Unsupported bundle format_version={format_version}: "
                    f"expected format_version={BUNDLE_FORMAT_VERSION}."
                )
                return result

            result.tasks = list(manifest_data.get("tasks", []))
            file_hashes: dict[str, str] = manifest_data.get("file_hashes", {})
            result.file_hashes = file_hashes
            result.backlog_count = int(manifest_data.get("backlog_count", 0))

            # 2. ZIP 内全ファイルと Manifest 記載ファイルの完全一致検証 (P1: 未知ファイル・パストラバーサル遮断)
            with zipfile.ZipFile(bundle_path, "r") as zf:
                zip_entries = [name for name in zf.namelist() if not name.endswith("/")]
                for entry in zip_entries:
                    if ".." in entry.replace("\\", "/").split("/") or entry.startswith(("/", "\\")):
                        result.errors.append(f"Insecure or invalid path in bundle ZIP: {entry}")
                zip_files = set(zip_entries)

            manifest_declared_files = set(file_hashes.keys()) | {"bundle_manifest.json"}
            extra_files = zip_files - manifest_declared_files
            if extra_files:
                result.errors.append(
                    f"Undeclared extra files found in bundle ZIP: {sorted(extra_files)}"
                )
            missing_zip_files = manifest_declared_files - zip_files
            if missing_zip_files:
                result.errors.append(
                    f"Declared files missing from bundle ZIP: {sorted(missing_zip_files)}"
                )

            # 3. 全ファイルのチェックサム完全一致検証
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

            # 4. SPEC ハッシュ整合性検証
            spec_file = inspect_dir / "SPEC.md"
            spec_hash = self._calc_sha256(spec_file) if spec_file.exists() else ""

            # 5. Git bundle の展開と HEAD / Commit / Tree SHA 解決
            git_bundle_file = inspect_dir / "git_bundle.bundle"
            valid_trees: set[str] = set()
            commit_to_tree: dict[str, str] = {}
            actual_head_commit = ""
            actual_head_tree = ""

            if not git_bundle_file.exists():
                result.errors.append("git_bundle.bundle is missing from bundle")
            else:
                clone_dir = inspect_dir / "_git_verify_repo"
                clone_res = subprocess.run(
                    ["git", "clone", str(git_bundle_file), str(clone_dir)],
                    check=False,
                    capture_output=True,
                    text=True,
                )
                if clone_res.returncode != 0:
                    # clone 失敗時は Warning ではなく必ず errors に追加して INVALID (P0 Fail-Closed)
                    result.errors.append(
                        f"Failed to clone/verify git_bundle.bundle: {clone_res.stderr.strip() or clone_res.stdout.strip()}"
                    )
                else:
                    rev_res = subprocess.run(
                        ["git", "rev-parse", "HEAD"],
                        cwd=clone_dir,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if rev_res.returncode == 0:
                        actual_head_commit = rev_res.stdout.strip()

                    rev_tree_res = subprocess.run(
                        ["git", "rev-parse", "HEAD^{tree}"],
                        cwd=clone_dir,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if rev_tree_res.returncode == 0:
                        actual_head_tree = rev_tree_res.stdout.strip()

                    log_res = subprocess.run(
                        ["git", "log", "--format=%H %T", "--all"],
                        cwd=clone_dir,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    if log_res.returncode == 0:
                        for line in log_res.stdout.splitlines():
                            parts = line.strip().split()
                            if len(parts) >= 2:
                                commit_to_tree[parts[0]] = parts[1]
                                valid_trees.add(parts[1])

                    if not commit_to_tree:
                        result.errors.append("git_bundle.bundle contains no valid commits")

            # 6. Manifest 必須項目の厳格検証および実 Git オブジェクト照合 (P1 Fail-Closed)
            expected_job_id = str(manifest_data.get("job_id", "")).strip()
            expected_head_commit = str(manifest_data.get("head_commit", "")).strip()
            expected_head_tree = str(manifest_data.get("head_tree_sha", "")).strip()

            if not expected_job_id:
                result.errors.append("Manifest missing required 'job_id'")
            if not expected_head_commit or len(expected_head_commit) != 40:
                result.errors.append(
                    f"Manifest missing or invalid required 'head_commit': '{expected_head_commit}'"
                )
            if not expected_head_tree or len(expected_head_tree) != 40:
                result.errors.append(
                    f"Manifest missing or invalid required 'head_tree_sha': '{expected_head_tree}'"
                )

            if expected_head_commit != actual_head_commit:
                result.errors.append(
                    f"Manifest head_commit mismatch: manifest has '{expected_head_commit}', git bundle HEAD is '{actual_head_commit}'"
                )
            if expected_head_tree != actual_head_tree:
                result.errors.append(
                    f"Manifest head_tree_sha mismatch: manifest has '{expected_head_tree}', git bundle HEAD^{{tree}} is '{actual_head_tree}'"
                )

            # 5. Manifest タスクと Evidence ファイルの完全性・フィールド突合検証 (P1 Fail-Closed)
            manifest_tasks = list(manifest_data.get("tasks", []))
            seen_task_ids: set[str] = set()
            manifest_tasks_by_id: dict[str, dict] = {}
            for t in manifest_tasks:
                tid = str(t.get("task_id", "")).strip()
                if not tid:
                    result.errors.append("Manifest task entry missing 'task_id'")
                    continue
                if tid in seen_task_ids:
                    result.errors.append(f"Duplicate task '{tid}' found in manifest tasks")
                seen_task_ids.add(tid)
                manifest_tasks_by_id[tid] = t

            evidence_dir = inspect_dir / "evidence"
            evidence_files = list(evidence_dir.glob("task_*.json")) if evidence_dir.exists() else []
            evidence_records_by_id: dict[str, dict] = {}

            for rec_file in evidence_files:
                try:
                    data = json.loads(rec_file.read_text(encoding="utf-8"))
                    tid = str(data.get("task_id", "")).strip()
                    if tid:
                        if tid in evidence_records_by_id:
                            result.errors.append(f"Duplicate task '{tid}' found in evidence files")
                        evidence_records_by_id[tid] = data
                except (json.JSONDecodeError, OSError):
                    result.errors.append(f"Failed to read/parse evidence record: {rec_file.name}")

            missing_in_files = seen_task_ids - set(evidence_records_by_id.keys())
            if missing_in_files:
                result.errors.append(f"Evidence files missing for manifest tasks: {sorted(missing_in_files)}")

            extra_files = set(evidence_records_by_id.keys()) - seen_task_ids
            if extra_files:
                result.errors.append(f"Unexpected unmanifested evidence files: {sorted(extra_files)}")

            # 共通タスクの全必須フィールド一致照合 (Manifest vs Evidence Record: P1 Fail-Closed)
            for tid in sorted(seen_task_ids & set(evidence_records_by_id.keys())):
                m_task = manifest_tasks_by_id[tid]
                e_task = evidence_records_by_id[tid]

                # 1. Commit 比較 (40文字 hex 必須)
                m_commit_raw = m_task.get("commit")
                m_commit = str(
                    m_commit_raw.get("sha", m_commit_raw.get("hash", m_commit_raw.get("commit_sha", "")))
                    if isinstance(m_commit_raw, dict)
                    else (m_commit_raw or "")
                ).strip()
                e_commit_raw = e_task.get("commit")
                e_commit = str(
                    e_commit_raw.get("sha", e_commit_raw.get("hash", e_commit_raw.get("commit_sha", "")))
                    if isinstance(e_commit_raw, dict)
                    else (e_commit_raw or "")
                ).strip()
                if not m_commit or len(m_commit) != 40:
                    result.errors.append(f"Task '{tid}' manifest missing or invalid required 'commit': '{m_commit}'")
                if not e_commit or len(e_commit) != 40:
                    result.errors.append(f"Task '{tid}' evidence missing or invalid required 'commit': '{e_commit}'")
                if m_commit != e_commit:
                    result.errors.append(
                        f"Task '{tid}' commit mismatch: manifest has '{m_commit}', evidence has '{e_commit}'"
                    )

                # 2. Tree SHA 比較 (40文字 hex 必須)
                m_tree = str(m_task.get("tree_sha", "")).strip()
                e_tree = str(e_task.get("tree_sha", "")).strip()
                if not m_tree or len(m_tree) != 40 or m_tree.startswith("00000000"):
                    result.errors.append(f"Task '{tid}' manifest missing or invalid required 'tree_sha': '{m_tree}'")
                if not e_tree or len(e_tree) != 40 or e_tree.startswith("00000000"):
                    result.errors.append(f"Task '{tid}' evidence missing or invalid required 'tree_sha': '{e_tree}'")
                if m_tree != e_tree:
                    result.errors.append(
                        f"Task '{tid}' tree_sha mismatch: manifest has '{m_tree}', evidence has '{e_tree}'"
                    )

                # 3. SPEC SHA 比較 (64文字 hex 必須)
                m_spec = str(m_task.get("spec_sha", "")).strip()
                e_spec = str(e_task.get("spec_sha", "")).strip()
                if not m_spec or len(m_spec) != 64:
                    result.errors.append(f"Task '{tid}' manifest missing or invalid required 'spec_sha': '{m_spec}'")
                if not e_spec or len(e_spec) != 64:
                    result.errors.append(f"Task '{tid}' evidence missing or invalid required 'spec_sha': '{e_spec}'")
                if m_spec != e_spec:
                    result.errors.append(
                        f"Task '{tid}' spec_sha mismatch: manifest has '{m_spec}', evidence has '{e_spec}'"
                    )

                # 4. Status 比較 (COMPLETED 必須)
                m_status = str(m_task.get("status", "")).strip()
                e_status = str(e_task.get("status", "")).strip()
                if not m_status:
                    result.errors.append(f"Task '{tid}' manifest missing required 'status'")
                if not e_status:
                    result.errors.append(f"Task '{tid}' evidence missing required 'status'")
                if m_status != e_status:
                    result.errors.append(
                        f"Task '{tid}' status mismatch: manifest has '{m_status}', evidence has '{e_status}'"
                    )

                # 5. Checker 比較 (APPROVE 必須)
                m_dec = (
                    m_task.get("checker_decision")
                    or (m_task.get("checker", {}).get("decision") if isinstance(m_task.get("checker"), dict) else str(m_task.get("checker", "")))
                    or ""
                ).strip()
                e_dec = (
                    e_task.get("checker_decision")
                    or (e_task.get("checker", {}).get("decision") if isinstance(e_task.get("checker"), dict) else str(e_task.get("checker", "")))
                    or ""
                ).strip()
                if not m_dec:
                    result.errors.append(f"Task '{tid}' manifest missing required checker decision")
                if not e_dec:
                    result.errors.append(f"Task '{tid}' evidence missing required checker decision")
                if m_dec != e_dec:
                    result.errors.append(
                        f"Task '{tid}' checker decision mismatch: manifest has '{m_dec}', evidence has '{e_dec}'"
                    )

            # 6. Evidence Record と Checker 判定および物理 Git Tree / Commit / SPEC 拘束の検証
            approved_records_count = 0
            if evidence_dir.exists():
                for rec_file in sorted(evidence_dir.glob("task_*.json")):
                    try:
                        rec_data = json.loads(rec_file.read_text(encoding="utf-8"))
                        task_id = str(rec_data.get("task_id", ""))
                        status = rec_data.get("status", "")
                        spec_sha = str(rec_data.get("spec_sha", "")).strip()
                        tree_sha = str(rec_data.get("tree_sha", "")).strip()

                        # Commit SHA 取得 (dict または string)
                        commit_field = rec_data.get("commit")
                        if isinstance(commit_field, dict):
                            commit_sha = str(
                                commit_field.get(
                                    "sha",
                                    commit_field.get("hash", commit_field.get("commit_sha", "")),
                                )
                            ).strip()
                        else:
                            commit_sha = str(commit_field or "").strip()

                        # Checker 判定の検証
                        checker = rec_data.get("checker", {})
                        decision = checker.get("decision", "") if isinstance(checker, dict) else str(checker)
                        if decision != "APPROVE":
                            result.errors.append(
                                f"Task {task_id} has invalid Checker decision: {decision} (expected APPROVE)"
                            )

                        # BDL-4: 査読の証跡（checker.review、3ファイルのSHA-256、出力ファイルのCORE-6判定）を検証
                        review_meta = checker.get("review") if isinstance(checker, dict) else None
                        if not isinstance(review_meta, dict):
                            result.errors.append(
                                f"Task {task_id} is missing checker.review metadata"
                            )
                        else:
                            from auto_developer.runner import parse_checker_decision

                            rev_dir = evidence_dir / "reviews" / f"task_{task_id}"
                            rev_files = {
                                "output_sha256": rev_dir / "checker_output.txt",
                                "diff_sha256": rev_dir / "diff.patch",
                                "spec_sha256": rev_dir / "spec_excerpt.md",
                            }
                            for sha_key, f_path in rev_files.items():
                                expected_sha = str(review_meta.get(sha_key, "")).strip()
                                if not f_path.is_file():
                                    result.errors.append(
                                        f"Task {task_id} missing bundled review file: {f_path.name}"
                                    )
                                elif not expected_sha or self._calc_sha256(f_path) != expected_sha:
                                    result.errors.append(
                                        f"Task {task_id} review file hash mismatch for {f_path.name}"
                                    )
                            out_file = rev_files["output_sha256"]
                            if out_file.is_file():
                                try:
                                    out_txt = out_file.read_text(encoding="utf-8")
                                except OSError:
                                    out_txt = ""
                                parsed_out_dec = parse_checker_decision(out_txt)
                                if parsed_out_dec != "APPROVE":
                                    result.errors.append(
                                        f"Task {task_id} bundled checker_output.txt decision is not APPROVE (got {parsed_out_dec})"
                                    )

                        # Status の検証
                        if status != "COMPLETED":
                            result.errors.append(
                                f"Task {task_id} status is invalid: '{status}' (expected COMPLETED)"
                            )

                        # SPEC 整合性の厳格検証
                        if not spec_hash:
                            result.errors.append(f"Task {task_id}: SPEC.md is missing from bundle")
                        elif not spec_sha or spec_sha != spec_hash:
                            result.errors.append(
                                f"Task {task_id} SPEC hash mismatch: Evidence has '{spec_sha}', bundle SPEC.md is '{spec_hash}'"
                            )

                        # Commit SHA と Tree SHA の 1対1 物理照合 (P1)
                        if not commit_sha or commit_sha == "0000000000000000000000000000000000000000" or len(commit_sha) < 40:
                            result.errors.append(
                                f"Task {task_id} has missing or invalid commit SHA: '{commit_sha}'"
                            )
                        elif commit_to_tree and commit_sha not in commit_to_tree:
                            result.errors.append(
                                f"Task {task_id} commit '{commit_sha}' does not exist in git_bundle.bundle"
                            )
                        elif commit_to_tree and commit_sha in commit_to_tree:
                            actual_tree_for_commit = commit_to_tree[commit_sha]
                            if tree_sha != actual_tree_for_commit:
                                result.errors.append(
                                    f"Task {task_id} commit/tree mismatch: commit {commit_sha} has tree '{actual_tree_for_commit}', but evidence claims '{tree_sha}'"
                                )

                        # Tree SHA 物理拘束の厳格検証 (P0)
                        if not tree_sha or tree_sha == "0000000000000000000000000000000000000000" or len(tree_sha) < 40:
                            result.errors.append(
                                f"Task {task_id} has invalid or synthetic tree_sha: '{tree_sha}'"
                            )
                        elif not valid_trees or tree_sha not in valid_trees:
                            result.errors.append(
                                f"Task {task_id} tree_sha '{tree_sha}' does not match any commit in git_bundle.bundle"
                            )

                        is_record_valid = (
                            decision == "APPROVE"
                            and status == "COMPLETED"
                            and bool(spec_hash)
                            and spec_sha == spec_hash
                            and bool(valid_trees)
                            and tree_sha in valid_trees
                            and bool(commit_sha)
                            and commit_sha in commit_to_tree
                            and commit_to_tree.get(commit_sha) == tree_sha
                        )
                        if is_record_valid:
                            approved_records_count += 1

                    except (json.JSONDecodeError, OSError) as e:
                        result.errors.append(f"Failed to read evidence record {rec_file.name}: {e}")
            else:
                result.warnings.append("No evidence/ directory found in bundle")

            # 7. レポート鮮度・整合性検査 (NIGHTLY_REPORT.md がある場合)
            if "NIGHTLY_REPORT.md" in file_hashes and expected_head_commit and len(expected_head_commit) >= 7:
                report_file = inspect_dir / "NIGHTLY_REPORT.md"
                if report_file.exists():
                    try:
                        report_text = report_file.read_text(encoding="utf-8")
                        short_head = expected_head_commit[:7]
                        if "最新コミット" in report_text and short_head not in report_text:
                            result.warnings.append(
                                f"NIGHTLY_REPORT.md may be stale: current HEAD '{short_head}' not found in report"
                            )
                    except OSError:
                        pass

            # 7.5 リリースブロッカー検査 (WWW.3: PLAN.md 内の未完了 P0/P1 および ESCALATED)
            plan_file = inspect_dir / "PLAN.md"
            if plan_file.exists():
                try:
                    plan_text = plan_file.read_text(encoding="utf-8")
                    blockers = find_release_blockers(plan_text)
                    if blockers:
                        result.errors.append(
                            f"RELEASE_BLOCKED: Unresolved release blockers found in PLAN.md: {blockers}"
                        )
                except OSError as e:
                    result.errors.append(f"Failed to read PLAN.md: {e}")

            # 8. 独立監査ステージ検証 (evidence/audit_stage.json がある場合: TTT.2 / UUU.2)
            audit_stage_file = inspect_dir / "evidence" / "audit_stage.json"
            if audit_stage_file.exists():
                try:
                    audit_stage_data = json.loads(audit_stage_file.read_text(encoding="utf-8"))
                    result.audit_stage_info = audit_stage_data
                    audit_meta = audit_stage_data.get("audit_metadata", {})
                    mode = audit_stage_data.get("mode") or audit_meta.get("mode") or "MOCK"
                    prov_req_id = (
                        audit_stage_data.get("provider_request_id")
                        or audit_meta.get("provider_request_id")
                        or audit_meta.get("execution_id")
                    )

                    # Sol氏 Phase 60 P1 指摘:
                    # mode == LIVE かつ provider_request_id が本物（exec_ や mock_ ではなく msg_... 等の実リクエストID）であることを検証
                    if audit_stage_data.get("status") == "SUCCESS":
                        is_mock_id = (
                            not prov_req_id
                            or str(prov_req_id).startswith("mock_")
                            or str(prov_req_id).startswith("exec_")
                        )
                        if mode == "LIVE" and not is_mock_id:
                            # VVV.3 / Sol氏 Phase 61 考察: ポスト監査差分検査 (Post-Audit Diff Integrity)
                            # 監査対象コミット以降にプロダクトコード（src/**, scripts/** 等）が変更されていないかを検証
                            target_commit = audit_meta.get("target_commit") or audit_stage_data.get("target_commit")
                            target_c = str(target_commit).strip() if target_commit else ""
                            if not target_c or not re.fullmatch(r"[0-9a-fA-F]{40}", target_c) or target_c == "0" * 40:
                                result.audit_stage_verified = False
                                result.warnings.append(
                                    f"AUDIT_TARGET_COMMIT_INVALID: Live audit record lacks a valid 40-hex target_commit SHA binding (got '{target_c}')."
                                )
                            elif not actual_head_commit or not clone_dir.exists():
                                result.audit_stage_verified = False
                                result.warnings.append(
                                    "AUDIT_VERIFICATION_FAILED: Cannot verify audit diff because repository HEAD or clone directory is missing."
                                )
                            elif target_c == actual_head_commit:
                                result.audit_stage_verified = True
                            else:
                                diff_res = subprocess.run(
                                    ["git", "diff", "--no-renames", "--name-only", target_c, actual_head_commit],
                                    cwd=clone_dir,
                                    capture_output=True,
                                    text=True,
                                    check=False,
                                )
                                if diff_res.returncode != 0:
                                    result.audit_stage_verified = False
                                    result.warnings.append(
                                        f"AUDIT_DIFF_FAILED: git diff failed between target commit '{target_c[:7]}' and HEAD '{actual_head_commit[:7]}': {diff_res.stderr.strip()}"
                                    )
                                else:
                                    modified_files = [f.strip() for f in diff_res.stdout.splitlines() if f.strip()]
                                    unverified_code = [
                                        f for f in modified_files
                                        if f.startswith(("src/", "scripts/")) or (f.endswith(".py") and not f.startswith("tests/"))
                                    ]
                                    if unverified_code:
                                        result.audit_stage_verified = False
                                        result.warnings.append(
                                            f"AUDIT_OUTDATED: Implementation code was modified after audit target commit '{target_c[:7]}' without re-audit. "
                                            f"Unverified modified files: {unverified_code}"
                                        )
                                    else:
                                        result.audit_stage_verified = True
                        else:
                            result.audit_stage_verified = False
                            result.warnings.append(
                                f"Audit stage is not live verified: mode='{mode}', provider_request_id='{prov_req_id}' "
                                f"(Simulation/MOCK cannot be verified for release)"
                            )
                except (json.JSONDecodeError, OSError) as e:
                    result.warnings.append(f"Failed to parse evidence/audit_stage.json: {e}")

        excluded_list = manifest_data.get("excluded_records", []) if isinstance(manifest_data, dict) else []
        no_review_ids = [
            str(e.get("task_id", "")).strip()
            for e in excluded_list
            if isinstance(e, dict) and e.get("reason") == "NO_REVIEW_EVIDENCE"
        ]
        if no_review_ids:
            result.warnings.append(
                f"NO_REVIEW_EVIDENCE: Tasks excluded due to missing review evidence: {', '.join(no_review_ids)}"
            )

        if result.errors:
            result.status = "INVALID"
            result.is_valid = False
        elif approved_records_count == 0 or len(result.tasks) == 0 or no_review_ids:
            result.status = "INTEGRITY_ONLY"
            result.is_valid = False
            if approved_records_count == 0 or len(result.tasks) == 0:
                result.warnings.append(
                    "Audit rejected: No verified Evidence Records found in bundle (Integrity preserved, but approval cannot be proven)"
                )
        else:
            result.status = "VERIFIED"
            result.is_valid = True

        return result
