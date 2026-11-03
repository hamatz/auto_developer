from __future__ import annotations

from pathlib import Path
from typing import Any


class EvidenceStore:
    """検証合格の証跡（Evidence Token）の発行・検証・失効を管理するモジュール。"""

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()
        self.evidence_dir = self.repo_dir / ".git" / "nightly_evidence"

    def issue_evidence(
        self, task_id: str, checkpoint: str, details: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """検証合格の証跡を発行し、.git 配下に永続化する。"""
        import hashlib
        import json
        import time

        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        raw = f"{task_id}:{checkpoint}:{now}"
        token = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        evidence = {
            "task_id": task_id,
            "checkpoint": checkpoint,
            "issued_at": now,
            "token": token,
            "details": details or {},
        }
        path = self.evidence_dir / f"task_{task_id}.json"
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence

    def verify_and_consume(self, task_id: str, checkpoint: str | None = None) -> bool:
        """指定タスクの有効な証跡が存在するか検証し、存在すれば消費（削除）する。"""
        import json

        path = self.evidence_dir / f"task_{task_id}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("task_id")) != str(task_id):
                return False
            if checkpoint and str(data.get("checkpoint")) != str(checkpoint):
                return False
            path.unlink()
            return True
        except Exception:
            return False

    def clear_evidence(self, task_id: str | None = None) -> None:
        """証跡ファイルを破棄する。"""
        if task_id:
            path = self.evidence_dir / f"task_{task_id}.json"
            if path.exists():
                path.unlink()
        elif self.evidence_dir.exists():
            for p in self.evidence_dir.glob("task_*.json"):
                p.unlink()
