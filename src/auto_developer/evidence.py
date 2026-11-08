from __future__ import annotations

from pathlib import Path
from typing import Any, cast


class EvidenceStore:
    """検証合格の証跡（Evidence Token）の発行・検証・失効および監査レコード（Evidence Record）の永続化を管理するモジュール。"""

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()
        self.evidence_dir = self.repo_dir / ".git" / "nightly_evidence"
        self.records_dir = self.evidence_dir / "records"

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

    def clear_evidence(self, task_id: str | None = None) -> None:
        """証跡ファイルを破棄する。"""
        if task_id:
            path = self.evidence_dir / f"task_{task_id}.json"
            if path.exists():
                path.unlink()
        elif self.evidence_dir.exists():
            for p in self.evidence_dir.glob("task_*.json"):
                p.unlink()
