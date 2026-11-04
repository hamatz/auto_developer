from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Task:
    task_id: str
    title: str
    line_number: int
    is_completed: bool
    is_failed: bool


@dataclass
class ExecutionResult:
    success: bool
    task_id: str
    error_message: str = ""
    quarantined: bool = False


@dataclass
class JobState:
    """オーケストレーターの実行状態（Job State）を保持・永続化するモジュール。"""

    job_id: str
    status: str = "IDLE"  # IDLE, QUEUED, RUNNING, SUSPENDED, COMPLETED, FAILED
    current_task_id: str | None = None
    current_phase: str = "INIT"  # INIT, MAKER, VERIFY, CHECKER, COMMIT, QUARANTINE
    current_attempt: int = 1
    checkpoint_hash: str = ""
    completed_tasks: list[dict[str, Any]] = None  # type: ignore
    quarantined_task: dict[str, Any] | None = None
    last_updated_at: float = 0.0
    base_branch: str = ""

    def __post_init__(self) -> None:
        if self.completed_tasks is None:
            self.completed_tasks = []
        if self.last_updated_at == 0.0:
            import time

            self.last_updated_at = time.time()

    def transition_phase(self, new_phase: str, attempt: int | None = None) -> None:
        """フェーズ遷移を記録しタイムスタンプを更新する。"""
        import time

        self.current_phase = new_phase
        if attempt is not None:
            self.current_attempt = attempt
        self.last_updated_at = time.time()

    def can_resume(self) -> bool:
        """中断から安全に再開可能か判定する。"""
        return self.status in ("RUNNING", "SUSPENDED")

    def save(self, path: Path) -> None:
        """ディスク（JSON）に状態をアトミックに永続化する (一時ファイル + os.replace)。"""
        import json
        import os
        import time

        self.last_updated_at = time.time()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "job_id": self.job_id,
            "status": self.status,
            "current_task_id": self.current_task_id,
            "current_phase": self.current_phase,
            "current_attempt": self.current_attempt,
            "checkpoint_hash": self.checkpoint_hash,
            "completed_tasks": self.completed_tasks,
            "quarantined_task": self.quarantined_task,
            "last_updated_at": self.last_updated_at,
            "base_branch": self.base_branch,
        }
        tmp_path = path.with_suffix(".tmp")
        tmp_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(tmp_path, path)

    @classmethod
    def load(cls, path: Path) -> JobState:
        """ディスクから状態を復元する。"""
        import json

        if not path.exists():
            raise FileNotFoundError(f"State file not found: {path}")
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(
            job_id=data.get("job_id", ""),
            status=data.get("status", "IDLE"),
            current_task_id=data.get("current_task_id"),
            current_phase=data.get("current_phase", "INIT"),
            current_attempt=data.get("current_attempt", 1),
            checkpoint_hash=data.get("checkpoint_hash", ""),
            completed_tasks=data.get("completed_tasks", []),
            quarantined_task=data.get("quarantined_task"),
            last_updated_at=data.get("last_updated_at", 0.0),
            base_branch=data.get("base_branch", ""),
        )
