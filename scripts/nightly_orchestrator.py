#!/usr/bin/env bash
"""Antigravity Autonomous Pipeline Orchestrator (夜間自律稼働エンジン)."""

from __future__ import annotations

from abc import ABC, abstractmethod
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


@dataclass
class JobState:
    """オーケストレーターの実行状態（Job State）を保持・永続化するモジュール。"""

    job_id: str
    status: str = "IDLE"  # IDLE, RUNNING, STOPPED_FAIL_FAST, COMPLETED
    current_task_id: str | None = None
    current_phase: str = "INIT"  # INIT, MAKER, VERIFY, CHECKER, COMMIT, QUARANTINE
    current_attempt: int = 1
    checkpoint_hash: str = ""
    completed_tasks: list[dict[str, Any]] = None  # type: ignore
    quarantined_task: dict[str, Any] | None = None
    last_updated_at: float = 0.0

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
        return self.status == "RUNNING" and self.current_task_id is not None

    def save(self, path: Path) -> None:
        """ディスク（JSON）に状態を永続化する。"""
        import json
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
        }
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

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
        )


class RunnerAdapter(ABC):
    """AI エージェントセッション（Maker / Checker）の実行を抽象化するアダプター基底クラス。"""

    @abstractmethod
    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        """Maker セッションを起動してタスクを実装させ、(exit_code, output) を返す。"""
        pass

    @abstractmethod
    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        """Checker セッションを起動して独立査読を行わせ、(is_approved, explanation) を返す。"""
        pass


class MockRunnerAdapter(RunnerAdapter):
    """テストおよびドライラン用の決定論的 RunnerAdapter。"""

    def __init__(
        self,
        maker_exit_code: int = 0,
        maker_output: str = "Mock Maker executed successfully",
        checker_approve: bool = True,
        checker_reason: str = "Mock Checker approved",
    ) -> None:
        self.maker_exit_code = maker_exit_code
        self.maker_output = maker_output
        self.checker_approve = checker_approve
        self.checker_reason = checker_reason
        self.maker_calls: list[tuple[Task, dict[str, Any]]] = []
        self.checker_calls: list[tuple[Task, str, str]] = []

    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        self.maker_calls.append((task, context))
        return self.maker_exit_code, self.maker_output

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        self.checker_calls.append((task, diff, spec_content))
        return self.checker_approve, self.checker_reason


class CommandRunnerAdapter(RunnerAdapter):
    """外部CLIコマンドやスクリプトを呼び出してエージェントを実行するアダプター。"""

    def __init__(
        self,
        maker_cmd_template: str = "",
        checker_cmd_template: str = "",
        timeout_sec: int = 600,
    ) -> None:
        self.maker_cmd_template = maker_cmd_template
        self.checker_cmd_template = checker_cmd_template
        self.timeout_sec = timeout_sec

    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        if not self.maker_cmd_template:
            return 0, "(No maker command configured, simulated no-op)"
        cmd_str = self.maker_cmd_template.format(
            task_id=task.task_id,
            task_title=task.title,
            attempt=context.get("attempt", 1),
            prev_error=context.get("prev_error", ""),
        )
        try:
            proc = subprocess.run(
                ["bash", "-c", cmd_str],
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
            return proc.returncode, proc.stdout + "\n" + proc.stderr
        except subprocess.TimeoutExpired:
            return 124, f"Maker execution timed out after {self.timeout_sec}s"
        except Exception as e:
            return 1, f"Failed to execute Maker: {e}"

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        if not self.checker_cmd_template:
            return True, "(No checker command configured, auto-approved)"
        cmd_str = self.checker_cmd_template.format(
            task_id=task.task_id,
            task_title=task.title,
        )
        try:
            proc = subprocess.run(
                ["bash", "-c", cmd_str],
                input=f"--- DIFF ---\n{diff}\n--- SPEC ---\n{spec_content}",
                capture_output=True,
                text=True,
                timeout=self.timeout_sec,
            )
            output = proc.stdout + "\n" + proc.stderr
            if proc.returncode != 0:
                return False, f"Checker process failed with exit {proc.returncode}:\n{output}"
            is_approved = "APPROVE" in output.upper() and "REJECT" not in output.upper()
            return is_approved, output.strip()
        except subprocess.TimeoutExpired:
            return False, f"Checker timed out after {self.timeout_sec}s"
        except Exception as e:
            return False, f"Failed to execute Checker: {e}"


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

    PROTECTED_PATTERNS = [
        re.compile(r"^\.github/"),
        re.compile(r"^SPEC\.md$"),
        re.compile(r"^scripts/verify_local\.sh$"),
        re.compile(r"^tests/immunity/"),
    ]

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + args,
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    def assert_clean_working_tree(self) -> None:
        """作業ツリーがクリーンである（未コミットの変更がない）ことを検証する。"""
        proc = self._run_git(["status", "--porcelain"])
        if proc.stdout.strip():
            raise RuntimeError(
                f"Dirty working tree: uncommitted changes detected in {self.repo_dir}:\n{proc.stdout}"
            )

    def get_current_checkpoint(self) -> str:
        """現在の HEAD コミットハッシュを取得する。"""
        proc = self._run_git(["rev-parse", "HEAD"])
        return proc.stdout.strip()

    def check_protected_files_untouched(self, checkpoint: str) -> bool:
        """チェックポイント以降に変更されたファイル（未追跡・ステージ済を含む）に保護対象が含まれていないか検査する。"""
        files_to_check: set[str] = set()

        # 1. Diff against checkpoint (tracked modifications)
        proc_diff = self._run_git(["diff", "--name-only", checkpoint])
        for line in proc_diff.stdout.splitlines():
            if line.strip():
                files_to_check.add(line.strip())

        # 2. Porcelain status (untracked files ??, staged files, uncommitted changes)
        proc_status = self._run_git(["status", "--porcelain"])
        for line in proc_status.stdout.splitlines():
            if not line.strip():
                continue
            # Format: XY filename or XY orig -> renamed
            raw_path = line[3:].strip()
            if " -> " in raw_path:
                for p in raw_path.split(" -> "):
                    files_to_check.add(p.strip().strip('"'))
            else:
                files_to_check.add(raw_path.strip('"'))

        for file_path in files_to_check:
            for pattern in self.PROTECTED_PATTERNS:
                if pattern.search(file_path):
                    return False
        return True

    def commit_task(self, task: Task) -> str:
        """タスクの変更をアトミックにコミットする。"""
        self._run_git(["add", "-A"])
        msg = f"feat: {task.title} [refs: {task.task_id}]"
        self._run_git(["commit", "-m", msg])
        return self.get_current_checkpoint()

    def get_current_branch(self) -> str:
        """現在のブランチ名を取得する。detached HEAD の場合は空文字。"""
        proc = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        branch = proc.stdout.strip()
        return "" if branch == "HEAD" else branch

    def quarantine_and_restore(self, checkpoint: str, task_id: str) -> str:
        """作業ツリーの変更を破棄せず隔離ブランチに退避コミットし、本線をチェックポイントに復元する。"""
        import time

        proc_status = self._run_git(["status", "--porcelain"])
        has_changes = bool(proc_status.stdout.strip())

        quarantine_branch = ""
        current_branch = self.get_current_branch()

        if has_changes:
            clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
            timestamp = int(time.time())
            quarantine_branch = f"quarantine/{clean_id}-{timestamp}"

            # 新規ブランチを作成して退避コミット
            self._run_git(["checkout", "-b", quarantine_branch])
            self._run_git(["add", "-A"])
            self._run_git(["commit", "-m", f"quarantine: save failed work for task {task_id} before rollback"])

            # 元のブランチに戻る
            if current_branch:
                self._run_git(["checkout", current_branch])
            else:
                self._run_git(["checkout", checkpoint])

        # 作業ツリーをチェックポイントに完全復元
        self._run_git(["reset", "--hard", checkpoint])
        self._run_git(["clean", "-fd"])

        return quarantine_branch

    def rollback_and_clean(self, checkpoint: str) -> None:
        """作業ツリーをチェックポイントに巻き戻し、未追跡ファイルを強制削除して完全初期化する。"""
        self._run_git(["reset", "--hard", checkpoint])
        self._run_git(["clean", "-fd"])



class DeterministicVerifier:
    """ローカル検証スクリプトの実行と判定を担当。"""

    def run_verification(self, script_path: Path, timeout_sec: int = 300) -> tuple[int, str]:
        """検証スクリプトを実行し、(exit_code, output) を返す。"""
        if not script_path.exists():
            return 1, f"Verification script not found: {script_path}"

        try:
            proc = subprocess.run(
                ["bash", str(script_path)],
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            output = proc.stdout + "\n" + proc.stderr
            return proc.returncode, output.strip()
        except subprocess.TimeoutExpired as e:
            return 124, f"Verification timed out after {timeout_sec}s: {e}"
        except Exception as e:
            return 1, f"Failed to execute verification script: {e}"


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


class MorningReporter:
    """NIGHTLY_REPORT.md および隔離ログの生成を担当。"""

    def write_report(self, report_path: Path, summary_data: dict[str, Any]) -> None:
        """NIGHTLY_REPORT.md を所定のスキーマで作成・更新する。"""
        lines = [
            "# Nightly Execution Report",
            "",
            "## 1. Summary",
            f"* **Status**: {summary_data.get('status', 'UNKNOWN')}",
            f"* **Total Tasks**: {summary_data.get('total_tasks', 0)}",
            f"* **Succeeded**: {summary_data.get('succeeded_count', 0)}",
            f"* **Quarantined**: {summary_data.get('quarantined_count', 0)}",
            f"* **Remaining**: {summary_data.get('remaining_count', 0)}",
            f"* **Checkpoint Restored**: {summary_data.get('checkpoint', 'N/A')}",
            "",
            "## 2. Completed Tasks",
            "| Task ID | Description | Commit Hash | Verified By |",
            "| --- | --- | --- | --- |",
        ]

        completed_tasks = summary_data.get("completed_tasks", [])
        if completed_tasks:
            for item in completed_tasks:
                lines.append(
                    f"| task-{item['task_id']} | {item['title']} | `{item['commit']}` | {item.get('verifier', 'verify_local.sh')} |"
                )
        else:
            lines.append("| - | (なし) | - | - |")

        lines.extend(["", "## 3. Quarantined Task (If Failed)"])
        quarantined = summary_data.get("quarantined_task")
        if quarantined:
            lines.extend(
                [
                    f"* **Failed Task ID**: task-{quarantined['task_id']}",
                    f"* **Failure Phase**: {quarantined.get('phase', 'UNKNOWN')}",
                    f"* **Quarantined Branch**: `{quarantined.get('quarantined_branch', 'N/A')}`",
                    f"* **Failure Log**: {quarantined.get('log_path', 'N/A')}",
                    "* **Root Cause Summary**:",
                    "  ```text",
                    f"  {quarantined.get('root_cause_summary', 'No details')}",
                    "  ```",
                ]
            )
        else:
            lines.append("No tasks were quarantined.")

        lines.extend(
            [
                "",
                "## 4. Next Actions for Developer",
                "- [ ] Review quarantined task failure log",
                "- [ ] Inspect git diff of succeeded commits: `git log --oneline -M`",
                "",
            ]
        )

        report_path.write_text("\n".join(lines), encoding="utf-8")

    def write_failure_log(self, log_path: Path, failure_data: dict[str, Any]) -> None:
        """logs/nightly/task_<id>_failure.log を作成する。"""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        content = [
            "=== TASK FAILURE DUMP ===",
            f"Task ID: {failure_data.get('task_id')}",
            f"Task Title: {failure_data.get('title')}",
            f"Checkpoint Commit: {failure_data.get('checkpoint')}",
            "",
            "--- ATTEMPT 1 LOG ---",
            failure_data.get("attempt_1_log", "(none)"),
            "",
            "--- ATTEMPT 2 LOG ---",
            failure_data.get("attempt_2_log", "(none)"),
            "",
            "--- WORKSPACE GIT DIFF BEFORE ROLLBACK ---",
            failure_data.get("git_diff", "(empty)"),
            "",
        ]
        log_path.write_text("\n".join(content), encoding="utf-8")


def main() -> int:
    """CLI エントリーポイント。"""
    import argparse
    import json
    import sys

    VERSION = "0.1.0"

    parser = argparse.ArgumentParser(description="Antigravity Autonomous Pipeline Orchestrator")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--plan", type=Path, default=Path("PLAN.md"), help="Path to PLAN.md")
    parser.add_argument(
        "--verify-script",
        type=Path,
        default=Path("scripts/verify_local.sh"),
        help="Path to verification script",
    )
    parser.add_argument(
        "--report", type=Path, default=Path("NIGHTLY_REPORT.md"), help="Path to NIGHTLY_REPORT.md"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate execution without modifying git or state"
    )
    parser.add_argument(
        "--timeout", type=int, default=300, help="Max verification timeout in seconds"
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand actions")

    # Subcommand: pre-flight
    subparsers.add_parser("pre-flight", help="Check clean working tree and print next pending task")

    # Subcommand: verify-step
    verify_parser = subparsers.add_parser("verify-step", help="Check protected files and run verify script")
    verify_parser.add_argument("--checkpoint", type=str, required=True, help="Checkpoint commit hash")
    verify_parser.add_argument("--task-id", type=str, default="default", help="Task ID being verified")

    # Subcommand: commit-step
    commit_parser = subparsers.add_parser("commit-step", help="Commit current task and mark [x] in PLAN.md")
    commit_parser.add_argument("--task-id", type=str, required=True, help="Task ID to commit")
    commit_parser.add_argument("--checkpoint", type=str, default=None, help="Optional checkpoint commit hash to verify")

    # Subcommand: rollback-step
    rollback_parser = subparsers.add_parser("rollback-step", help="Rollback working tree and mark task as failed")
    rollback_parser.add_argument("--checkpoint", type=str, required=True, help="Checkpoint commit hash")
    rollback_parser.add_argument("--task-id", type=str, required=True, help="Task ID to mark failed")
    rollback_parser.add_argument("--reason", type=str, default="Verification failed", help="Failure reason")

    args = parser.parse_args()

    git_guard = GitGuard()
    dispatcher = TaskDispatcher()
    verifier = DeterministicVerifier()
    reporter = MorningReporter()
    evidence_store = EvidenceStore()

    # Handling Subcommands:
    if args.subcommand == "pre-flight":
        try:
            git_guard.assert_clean_working_tree()
        except RuntimeError as e:
            print(f"PRE-FLIGHT ERROR: {e}", file=sys.stderr)
            return 2
        tasks = dispatcher.parse_plan(args.plan)
        next_task = dispatcher.get_next_pending_task(tasks)
        checkpoint = git_guard.get_current_checkpoint()
        result = {
            "clean": True,
            "checkpoint": checkpoint,
            "next_task": {
                "id": next_task.task_id,
                "title": next_task.title,
            }
            if next_task
            else None,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0

    elif args.subcommand == "verify-step":
        if not git_guard.check_protected_files_untouched(args.checkpoint):
            print(f"CIRCUIT BREAKER: Protected file modified since {args.checkpoint}", file=sys.stderr)
            return 10
        code, out = verifier.run_verification(args.verify_script, timeout_sec=args.timeout)
        if code != 0:
            print(f"VERIFICATION FAILED (Exit {code}):\n{out}", file=sys.stderr)
            return code
        evidence = evidence_store.issue_evidence(task_id=args.task_id, checkpoint=args.checkpoint)
        print(f"VERIFICATION PASSED (Evidence Token: {evidence['token']})")
        return 0

    elif args.subcommand == "commit-step":
        tasks = dispatcher.parse_plan(args.plan)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        if not target:
            print(f"ERROR: Task {args.task_id} not found in {args.plan}", file=sys.stderr)
            return 1
        if not evidence_store.verify_and_consume(task_id=args.task_id, checkpoint=args.checkpoint):
            print(
                f"EVIDENCE ERROR: No valid verification evidence found for task {args.task_id}. Must pass verify-step first.",
                file=sys.stderr,
            )
            return 3
        dispatcher.mark_task_status(args.plan, target, status="completed")
        commit_hash = git_guard.commit_task(target)
        print(f"COMMITTED: {commit_hash}")
        return 0

    elif args.subcommand == "rollback-step":
        tasks = dispatcher.parse_plan(args.plan)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        task_id = target.task_id if target else args.task_id
        evidence_store.clear_evidence(task_id=task_id)
        quarantine_branch = git_guard.quarantine_and_restore(args.checkpoint, task_id=task_id)
        if target:
            dispatcher.mark_task_status(args.plan, target, status="failed")
            log_path = Path(f"logs/nightly/task_{target.task_id}_failure.log")
            reporter.write_failure_log(
                log_path,
                {
                    "task_id": target.task_id,
                    "title": target.title,
                    "checkpoint": args.checkpoint,
                    "quarantined_branch": quarantine_branch,
                    "attempt_1_log": args.reason,
                },
            )
        print(f"ROLLED BACK TO {args.checkpoint} (Work quarantined to: {quarantine_branch or 'none'})")
        return 0

    # Default standalone loop (dry-run or direct batch):
    try:
        if not args.dry_run:
            git_guard.assert_clean_working_tree()
    except RuntimeError as e:
        print(f"PRE-FLIGHT ERROR: {e}", file=sys.stderr)
        return 2

    if not args.plan.exists():
        print(f"PRE-FLIGHT ERROR: Plan file not found: {args.plan}", file=sys.stderr)
        return 2

    tasks = dispatcher.parse_plan(args.plan)
    total_tasks = len(tasks)
    succeeded_tasks: list[dict[str, Any]] = []

    print(f"=== Starting Nightly Orchestrator (Total Tasks: {total_tasks}, Dry-Run: {args.dry_run}) ===")

    while True:
        task = dispatcher.get_next_pending_task(tasks)
        if not task:
            print("All pending tasks completed!")
            break

        print(f"Processing Task [{task.task_id}]: {task.title}")

        if args.dry_run:
            succeeded_tasks.append(
                {"task_id": task.task_id, "title": task.title, "commit": "simulated_hash", "verifier": "dry-run"}
            )
            tasks = [
                Task(t.task_id, t.title, t.line_number, True if t.task_id == task.task_id else t.is_completed, t.is_failed)
                for t in tasks
            ]
            continue

        checkpoint = git_guard.get_current_checkpoint()

        if not git_guard.check_protected_files_untouched(checkpoint):
            print(f"CIRCUIT BREAKER: Protected file modified in task {task.task_id}. Rollback triggered.", file=sys.stderr)
            evidence_store.clear_evidence(task.task_id)
            quarantine_branch = git_guard.quarantine_and_restore(checkpoint, task_id=task.task_id)
            dispatcher.mark_task_status(args.plan, task, status="failed")
            reporter.write_report(
                args.report,
                {
                    "status": "STOPPED (FAIL-FAST)",
                    "total_tasks": total_tasks,
                    "succeeded_count": len(succeeded_tasks),
                    "quarantined_count": 1,
                    "remaining_count": total_tasks - len(succeeded_tasks) - 1,
                    "checkpoint": checkpoint,
                    "completed_tasks": succeeded_tasks,
                    "quarantined_task": {
                        "task_id": task.task_id,
                        "phase": "INVARIANT_BREACH",
                        "quarantined_branch": quarantine_branch,
                        "log_path": f"logs/nightly/task_{task.task_id}_failure.log",
                        "root_cause_summary": "Protected file modification detected.",
                    },
                },
            )
            return 1

        code, out = verifier.run_verification(args.verify_script, timeout_sec=args.timeout)
        if code != 0:
            print(f"Verification failed on task {task.task_id} (Exit {code}).", file=sys.stderr)
            evidence_store.clear_evidence(task.task_id)
            quarantine_branch = git_guard.quarantine_and_restore(checkpoint, task_id=task.task_id)
            dispatcher.mark_task_status(args.plan, task, status="failed")
            log_path = Path(f"logs/nightly/task_{task.task_id}_failure.log")
            reporter.write_failure_log(
                log_path,
                {
                    "task_id": task.task_id,
                    "title": task.title,
                    "checkpoint": checkpoint,
                    "quarantined_branch": quarantine_branch,
                    "attempt_1_log": out,
                },
            )
            reporter.write_report(
                args.report,
                {
                    "status": "STOPPED (FAIL-FAST)",
                    "total_tasks": total_tasks,
                    "succeeded_count": len(succeeded_tasks),
                    "quarantined_count": 1,
                    "remaining_count": total_tasks - len(succeeded_tasks) - 1,
                    "checkpoint": checkpoint,
                    "completed_tasks": succeeded_tasks,
                    "quarantined_task": {
                        "task_id": task.task_id,
                        "phase": "VERIFIER_ERROR",
                        "quarantined_branch": quarantine_branch,
                        "log_path": str(log_path),
                        "root_cause_summary": out[:300],
                    },
                },
            )
            return 1

        evidence = evidence_store.issue_evidence(task.task_id, checkpoint)
        if not evidence_store.verify_and_consume(task.task_id, checkpoint):
            print(f"EVIDENCE ERROR: Failed to verify evidence for task {task.task_id}", file=sys.stderr)
            return 3

        dispatcher.mark_task_status(args.plan, task, status="completed")
        commit_hash = git_guard.commit_task(task)
        succeeded_tasks.append(
            {"task_id": task.task_id, "title": task.title, "commit": commit_hash, "verifier": "verify_local.sh"}
        )
        tasks = dispatcher.parse_plan(args.plan)

    if not args.dry_run:
        reporter.write_report(
            args.report,
            {
                "status": "COMPLETED",
                "total_tasks": total_tasks,
                "succeeded_count": len(succeeded_tasks),
                "quarantined_count": 0,
                "remaining_count": 0,
                "checkpoint": git_guard.get_current_checkpoint(),
                "completed_tasks": succeeded_tasks,
            },
        )
        print(f"Execution complete. Report written to {args.report}")
    else:
        print(f"[DRY-RUN] Execution simulation complete. {len(succeeded_tasks)} tasks simulated. Report was not modified.")
    return 0



if __name__ == "__main__":
    import sys
    sys.exit(main())


