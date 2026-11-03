#!/usr/bin/env bash
"""Antigravity Autonomous Pipeline Orchestrator (夜間自律稼働エンジン)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import subprocess
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


class GitGuard:
    """Git作業ツリーの整合性管理、不変条件外部検査、およびロールバックを担当。"""

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()

    def assert_clean_working_tree(self) -> None:
        raise NotImplementedError

    def get_current_checkpoint(self) -> str:
        raise NotImplementedError

    def check_protected_files_untouched(self, checkpoint: str) -> bool:
        raise NotImplementedError

    def rollback_and_clean(self, checkpoint: str) -> None:
        raise NotImplementedError


class DeterministicVerifier:
    """ローカル検証スクリプトの実行と判定を担当。"""

    def run_verification(self, script_path: Path, timeout_sec: int = 300) -> tuple[int, str]:
        raise NotImplementedError


class MorningReporter:
    """NIGHTLY_REPORT.md および隔離ログの生成を担当。"""

    def write_report(self, report_path: Path, summary_data: dict[str, Any]) -> None:
        raise NotImplementedError

    def write_failure_log(self, log_path: Path, failure_data: dict[str, Any]) -> None:
        raise NotImplementedError
