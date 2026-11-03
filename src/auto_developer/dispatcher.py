from __future__ import annotations

from pathlib import Path
import re

from auto_developer.models import Task


class TaskDispatcher:
    """PLAN.md のタスクパースと状態管理を担当するモジュール。"""

    # Matches "- [ ] 1. タスク名" or "- [x] 1. タスク名"
    TASK_PATTERN = re.compile(
        r"^\s*-\s*\[([ xX])\]\s*(\d+|[a-zA-Z0-9_\-]+)\.\s*(.+?)(?:\s*\*\*\[FAILED\]\*\*)?\s*$"
    )

    def parse_plan(self, plan_path: Path) -> list[Task]:
        """PLAN.md を行単位で読み込み、Taskオブジェクトのリストを生成する。"""
        if not plan_path.exists():
            raise FileNotFoundError(f"PLAN file not found: {plan_path}")

        tasks: list[Task] = []
        lines = plan_path.read_text(encoding="utf-8").splitlines()

        for idx, line in enumerate(lines):
            match = self.TASK_PATTERN.match(line)
            if match:
                check_box, task_id, title = match.groups()
                is_completed = check_box.lower() == "x"
                is_failed = "**[FAILED]**" in line

                tasks.append(
                    Task(
                        task_id=task_id.strip(),
                        title=title.strip(),
                        line_number=idx,
                        is_completed=is_completed,
                        is_failed=is_failed,
                    )
                )

        return tasks

    def get_next_pending_task(self, tasks: list[Task]) -> Task | None:
        """未完了かつ未失敗の最初のタスクを取得する。"""
        for task in tasks:
            if not task.is_completed and not task.is_failed:
                return task
        return None

    def mark_task_status(self, plan_path: Path, task: Task, status: str) -> None:
        """PLAN.md 内の指定タスクのステータス記法を更新する。"""
        lines = plan_path.read_text(encoding="utf-8").splitlines()
        if task.line_number >= len(lines):
            raise IndexError(f"Task line number {task.line_number} exceeds plan length.")

        target_line = lines[task.line_number]
        match = self.TASK_PATTERN.match(target_line)
        if not match:
            # Fallback: search by task_id and title
            for idx, line in enumerate(lines):
                if f"{task.task_id}." in line and task.title in line:
                    task = Task(task.task_id, task.title, idx, task.is_completed, task.is_failed)
                    target_line = lines[idx]
                    break

        if status == "completed":
            new_line = f"- [x] {task.task_id}. {task.title}"
        elif status == "failed":
            new_line = f"- [ ] {task.task_id}. {task.title} **[FAILED]**"
        else:
            raise ValueError(f"Unsupported status: {status}")

        lines[task.line_number] = new_line
        plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
