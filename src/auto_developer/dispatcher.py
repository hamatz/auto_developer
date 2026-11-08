from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from auto_developer.audit import AuditFinding
    from auto_developer.verifier import RedTestVerifier

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
                    target_line = line
                    break

        if status == "completed":
            new_line = f"- [x] {task.task_id}. {task.title}"
        elif status == "failed":
            new_line = f"- [ ] {task.task_id}. {task.title} **[FAILED]**"
        else:
            raise ValueError(f"Unsupported status: {status}")

        lines[task.line_number] = new_line
        plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class AuditDispatcher:
    """監査指摘 (AuditFinding) を RedTestVerifier で検証し、真正な Red を PLAN.md に自動登録する。"""

    def __init__(
        self,
        repo_dir: Path | str | None = None,
        verifier: RedTestVerifier | None = None,
    ) -> None:
        self.repo_dir = Path(repo_dir) if repo_dir else Path.cwd()
        if verifier is None:
            from auto_developer.verifier import RedTestVerifier

            self.verifier = RedTestVerifier(repo_dir=self.repo_dir)
        else:
            self.verifier = verifier

    def dispatch_finding(
        self,
        finding: AuditFinding,
        plan_path: Path | str | None = None,
    ) -> AuditFinding:
        """単一の AuditFinding を検証し、GENUINE_RED であれば PLAN.md に修正タスクを登録する。"""
        target_plan = Path(plan_path) if plan_path else self.repo_dir / "PLAN.md"

        # 1. 再現テストコードの Red 検証
        verification = self.verifier.verify_candidate_test(finding.reproduction_test_code)
        if not (verification.is_reproduced and verification.reason == "GENUINE_RED"):
            finding.status = "REJECTED"
            return finding

        finding.status = "VERIFIED_RED"

        if not target_plan.exists():
            target_plan.write_text("# PLAN.md\n\n## ステップ\n", encoding="utf-8")

        content = target_plan.read_text(encoding="utf-8")
        token = f"[{finding.finding_id}]"

        # 2. 二重登録防止 (Idempotency)
        if token in content:
            finding.status = "DISPATCHED"
            return finding

        # 3. タスク行の作成と挿入
        task_line = (
            f"- [ ] Fix: [{finding.finding_id}] {finding.description} "
            f"(Severity: {finding.severity}, Loc: {finding.location})"
        )

        lines = content.splitlines()
        step_idx = -1
        stop_idx = -1
        for idx, line in enumerate(lines):
            if line.startswith("## ステップ"):
                step_idx = idx
            elif line.startswith("## ") and step_idx != -1 and idx > step_idx:
                stop_idx = idx
                break

        if stop_idx != -1:
            lines.insert(stop_idx, task_line)
        else:
            lines.append(task_line)

        target_plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
        finding.status = "DISPATCHED"
        return finding

    def process_findings(
        self,
        findings: list[AuditFinding],
        plan_path: Path | str | None = None,
    ) -> list[AuditFinding]:
        """複数の AuditFinding を処理し、正常に登録 (DISPATCHED) された指摘のリストを返す。"""
        dispatched: list[AuditFinding] = []
        for finding in findings:
            res = self.dispatch_finding(finding, plan_path=plan_path)
            if res.status == "DISPATCHED":
                dispatched.append(res)
        return dispatched

