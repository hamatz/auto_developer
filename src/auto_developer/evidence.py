from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast


def compute_canonical_diff(work_dir: Path, checkpoint: str) -> bytes:
    """GATE-5: 作業ツリーのインデックスを変更せず、checkpoint から未追跡ファイルを含む正規の差分を算出する。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_index = Path(tmp_dir) / "index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(tmp_index)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=work_dir,
            env=env,
            check=True,
            capture_output=True,
        )
        proc = subprocess.run(
            ["git", "diff", "--cached", "--no-color", "--no-ext-diff", "--no-renames", checkpoint],
            cwd=work_dir,
            env=env,
            check=True,
            capture_output=True,
        )
        return proc.stdout


def resolve_evidence_records_dir(repo_dir: Path | None = None) -> Path:
    """BDL-5: EvidenceStore の保存規則に従ってレコード格納ディレクトリを1つの関数で解決する。"""
    resolved_repo = repo_dir or Path.cwd()
    git_path = resolved_repo / ".git"
    if git_path.is_dir():
        return git_path / "nightly_evidence" / "records"
    return resolved_repo / ".dev" / "evidence"


class EvidenceStore:
    """検証合格の証跡（Evidence Token）の発行・検証・失効および監査レコード（Evidence Record）の永続化を管理するモジュール。"""

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()
        git_path = self.repo_dir / ".git"
        if git_path.is_dir():
            self.evidence_dir = git_path / "nightly_evidence"
        else:
            self.evidence_dir = self.repo_dir / ".dev" / "evidence"
        self.records_dir = resolve_evidence_records_dir(self.repo_dir)

    def issue_evidence(
        self,
        task_id: str,
        checkpoint: str,
        tree_sha: str = "",
        spec_sha: str = "",
        details: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """検証合格の証跡を発行し、.git 配下に永続化する。"""
        import hashlib
        import json
        import time

        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        raw = f"{task_id}:{checkpoint}:{tree_sha}:{spec_sha}:{now}"
        token = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        evidence = {
            "task_id": task_id,
            "checkpoint": checkpoint,
            "tree_sha": tree_sha,
            "spec_sha": spec_sha,
            "issued_at": now,
            "token": token,
            "details": details or {},
        }
        path = self.evidence_dir / f"task_{task_id}.json"
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence

    def verify_and_consume(
        self,
        task_id: str,
        checkpoint: str | None = None,
        expected_tree_sha: str | None = None,
        expected_spec_sha: str | None = None,
    ) -> bool:
        """指定タスクの有効な証跡が存在するか検証し、Tree SHA と SPEC ハッシュが一致すれば消費して監査レコードを作成する。"""
        import json
        import time

        path = self.evidence_dir / f"task_{task_id}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("task_id")) != str(task_id):
                return False
            if checkpoint and str(data.get("checkpoint")) != str(checkpoint):
                return False
            if expected_tree_sha and str(data.get("tree_sha")) != str(expected_tree_sha):
                return False
            if expected_spec_sha and str(data.get("spec_sha")) != str(expected_spec_sha):
                return False

            # トークンを失効（ワンタイム消費・リプレイ防止）
            path.unlink()

            # 永続監査レコードを作成・保存 (機能N / P1-1)
            self.records_dir.mkdir(parents=True, exist_ok=True)
            record_path = self.records_dir / f"task_{task_id}.json"
            record_data = dict(data)
            record_data["consumed_at"] = time.time()
            record_data["status"] = "CONSUMED"
            record_path.write_text(
                json.dumps(record_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except (OSError, json.JSONDecodeError):
            return False

    def archive_record(
        self,
        task_id: str,
        status: str = "COMPLETED",
        commit_info: dict[str, Any] | None = None,
        checker_info: dict[str, Any] | None = None,
        verification_info: dict[str, Any] | None = None,
        attempts: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        """コミット完了後に監査レコードを最終更新する。"""
        import json

        self.records_dir.mkdir(parents=True, exist_ok=True)
        record_path = self.records_dir / f"task_{task_id}.json"
        if not record_path.exists():
            import time

            data = {
                "task_id": task_id,
                "status": status,
                "created_at": int(time.time()),
            }
        else:
            try:
                data = cast(dict[str, Any], json.loads(record_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                data = {"task_id": task_id, "status": status}

        try:
            data["status"] = status
            if commit_info is not None:
                data["commit"] = commit_info
                if commit_info.get("tree_sha"):
                    data["tree_sha"] = commit_info["tree_sha"]
            if checker_info is not None:
                data["checker"] = checker_info
            if verification_info is not None:
                data["verification"] = verification_info
            elif (
                "verification" not in data
                and "details" in data
                and isinstance(data["details"], dict)
                and "verification" in data["details"]
            ):
                data["verification"] = data["details"]["verification"]

            if attempts is not None:
                data["attempts"] = attempts
            elif (
                "attempts" not in data
                and "details" in data
                and isinstance(data["details"], dict)
                and "attempts" in data["details"]
            ):
                data["attempts"] = data["details"]["attempts"]

            record_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def get_records(self) -> list[dict[str, Any]]:
        """永続化されたすべての監査レコードをタスクID順で取得する。"""
        import json

        if not self.records_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for p in sorted(self.records_dir.glob("task_*.json")):
            try:
                data = cast(dict[str, Any], json.loads(p.read_text(encoding="utf-8")))
                records.append(data)
            except (OSError, json.JSONDecodeError):
                continue

        # task_id の数値順にソート（数値変換可能な場合）
        def _sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
            tid = str(item.get("task_id", ""))
            return (0, int(tid), "") if tid.isdigit() else (1, 0, tid)

        records.sort(key=_sort_key)
        return records

    def get_record(self, task_id: str) -> dict[str, Any] | None:
        """指定タスクの監査レコードを取得する。"""
        import json

        record_path = self.records_dir / f"task_{task_id}.json"
        if not record_path.exists():
            return None
        try:
            return cast(dict[str, Any], json.loads(record_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None

    def get_evidence(self, task_id: str) -> dict[str, Any] | None:
        """指定タスクの有効な検証証跡（未消費）を取得する。"""
        import json

        path = self.evidence_dir / f"task_{task_id}.json"
        if not path.exists():
            return None
        try:
            data = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None
        if str(data.get("task_id")) != str(task_id):
            return None
        return data

    def record_review(
        self,
        *,
        task_id: str,
        evidence: dict[str, Any],
        decision: str,
        checker_output_path: Path,
        diff_path: Path,
        spec_path: Path,
        maker_model: str,
        maker_family: str,
        maker_family_source: str,
        checker_model: str,
        checker_family: str,
        checker_family_source: str,
        reason: str = "",
    ) -> dict[str, Any]:
        """GATE-5: 査読の3ファイルの写しを Evidence 保存先へ置き、照合用メタデータを記録する。"""
        import hashlib
        import json
        import shutil
        import time

        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        reviews_dir = self.evidence_dir / "reviews"
        copies_dir = reviews_dir / f"task_{task_id}"
        copies_dir.mkdir(parents=True, exist_ok=True)

        out_copy = copies_dir / "checker_output.txt"
        diff_copy = copies_dir / "diff.patch"
        spec_copy = copies_dir / "spec_excerpt.md"
        shutil.copyfile(checker_output_path, out_copy)
        shutil.copyfile(diff_path, diff_copy)
        shutil.copyfile(spec_path, spec_copy)

        def _sha256(p: Path) -> str:
            return hashlib.sha256(p.read_bytes()).hexdigest()

        record: dict[str, Any] = {
            "task_id": str(task_id),
            "evidence_token": str(evidence.get("token", "")),
            "evidence_issued_at": evidence.get("issued_at"),
            "checkpoint": str(evidence.get("checkpoint", "")),
            "decision": decision,
            "checker_output_path": str(checker_output_path.resolve()),
            "checker_output_sha256": _sha256(checker_output_path),
            "diff_path": str(diff_path.resolve()),
            "diff_sha256": _sha256(diff_path),
            "spec_path": str(spec_path.resolve()),
            "spec_sha256": _sha256(spec_path),
            "copies": {
                "checker_output": str(out_copy),
                "diff": str(diff_copy),
                "spec": str(spec_copy),
            },
            "maker_model": maker_model,
            "maker_family": maker_family,
            "maker_family_source": maker_family_source,
            "checker_model": checker_model,
            "checker_family": checker_family,
            "checker_family_source": checker_family_source,
            "reason": reason,
            "recorded_at": time.time(),
        }
        review_path = reviews_dir / f"task_{task_id}.json"
        review_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def get_review_record(self, task_id: str) -> dict[str, Any] | None:
        """指定タスクの査読記録を取得する。"""
        import json

        review_path = self.evidence_dir / "reviews" / f"task_{task_id}.json"
        if not review_path.exists():
            return None
        try:
            return cast(dict[str, Any], json.loads(review_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None

    def clear_evidence(self, task_id: str | None = None) -> None:
        """証跡ファイルを破棄する。"""
        if task_id:
            path = self.evidence_dir / f"task_{task_id}.json"
            if path.exists():
                path.unlink()
        elif self.evidence_dir.exists():
            for p in self.evidence_dir.glob("task_*.json"):
                p.unlink()
