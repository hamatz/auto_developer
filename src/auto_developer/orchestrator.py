from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Any

from auto_developer.dispatcher import TaskDispatcher
from auto_developer.evidence import EvidenceStore
from auto_developer.guard import GitGuard
from auto_developer.models import JobState, Task
from auto_developer.reporter import MorningReporter
from auto_developer.runner import MockRunnerAdapter, RunnerAdapter
from auto_developer.verifier import DeterministicVerifier


class NightlyOrchestrator:
    """自律稼働パイプラインの親オーケストレーター。"""

    def __init__(
        self,
        repo_dir: Path | None = None,
        plan_path: Path | None = None,
        spec_path: Path | None = None,
        verify_script: Path | None = None,
        report_path: Path | None = None,
        state_path: Path | None = None,
        runner_adapter: RunnerAdapter | None = None,
        dry_run: bool = False,
        timeout_sec: int = 300,
        resume: bool = False,
    ) -> None:
        self.repo_dir = repo_dir or Path.cwd()
        self.plan_path = plan_path or (self.repo_dir / "PLAN.md")
        self.spec_path = spec_path or (self.repo_dir / "SPEC.md")
        self.verify_script = verify_script or (self.repo_dir / "scripts" / "verify_local.sh")
        self.report_path = report_path or (self.repo_dir / "NIGHTLY_REPORT.md")
        self.state_path = state_path or (self.repo_dir / ".dev" / "job_state.json")
        self.runner_adapter = runner_adapter or MockRunnerAdapter()
        self.dry_run = dry_run
        self.timeout_sec = timeout_sec
        self.resume = resume

        self.git_guard = GitGuard(repo_dir=self.repo_dir)
        self.dispatcher = TaskDispatcher()
        self.verifier = DeterministicVerifier()
        self.reporter = MorningReporter()
        self.evidence_store = EvidenceStore(repo_dir=self.repo_dir)

    def run(self) -> int:
        if not self.plan_path.exists():
            print(f"PRE-FLIGHT ERROR: Plan file not found: {self.plan_path}", file=sys.stderr)
            return 2

        # 1. JobState の初期化または再開
        if self.resume and self.state_path.exists():
            job_state = JobState.load(self.state_path)
            print(f"Resuming job {job_state.job_id} from phase {job_state.current_phase}")
            # クラッシュ時の残骸作業ツリーがあれば隔離退避してクリーン復元
            if job_state.checkpoint_hash and not self.dry_run:
                print(f"Resetting uncommitted work back to checkpoint {job_state.checkpoint_hash} for safe resume...")
                self.git_guard.quarantine_and_restore(
                    job_state.checkpoint_hash, task_id=job_state.current_task_id or "interrupted"
                )
        else:
            job_id = f"job-{int(time.time())}"
            job_state = JobState(job_id=job_id, status="RUNNING")
            if not self.dry_run:
                job_state.save(self.state_path)

        # 2. クリーンツリー事前検証
        if not self.dry_run:
            try:
                self.git_guard.assert_clean_working_tree()
            except RuntimeError as e:
                print(f"PRE-FLIGHT ERROR: {e}", file=sys.stderr)
                return 2

        tasks = self.dispatcher.parse_plan(self.plan_path)
        total_tasks = len(tasks)
        succeeded_tasks = job_state.completed_tasks

        print(f"=== Starting Autonomous Orchestrator (Job: {job_state.job_id}, Total Tasks: {total_tasks}, Dry-Run: {self.dry_run}) ===")

        while True:
            task = self.dispatcher.get_next_pending_task(tasks)
            if not task:
                print("All pending tasks completed!")
                break

            print(f"\n▶ Processing Task [{task.task_id}]: {task.title}")

            if self.dry_run:
                succeeded_tasks.append(
                    {"task_id": task.task_id, "title": task.title, "commit": "simulated_hash", "verifier": "dry-run"}
                )
                tasks = [
                    Task(t.task_id, t.title, t.line_number, True if t.task_id == task.task_id else t.is_completed, t.is_failed)
                    for t in tasks
                ]
                continue

            checkpoint = self.git_guard.get_current_checkpoint()
            job_state.current_task_id = task.task_id
            job_state.checkpoint_hash = checkpoint
            job_state.transition_phase("MAKER", attempt=1)
            job_state.save(self.state_path)

            # Phase A: Maker 実装呼び出し
            print(f"  [Phase A: Maker] Running Maker for task {task.task_id}...")
            maker_code, maker_out = self.runner_adapter.run_maker(task, context={"attempt": 1})
            if maker_code != 0:
                print(f"  Maker failed with exit {maker_code}:\n{maker_out}", file=sys.stderr)
                return self._handle_failure(task, checkpoint, total_tasks, succeeded_tasks, job_state, "MAKER_CRASH", maker_out)

            # Phase B: 不変条件検査 (Circuit Breaker)
            if not self.git_guard.check_protected_files_untouched(checkpoint):
                print(f"  CIRCUIT BREAKER: Protected file modified in task {task.task_id}!", file=sys.stderr)
                return self._handle_failure(task, checkpoint, total_tasks, succeeded_tasks, job_state, "INVARIANT_BREACH", "Protected file modification detected")

            # Phase C: 決定論的検証 & 再試行 (Verification Gate)
            print(f"  [Phase C: Verifier] Running {self.verify_script}...")
            code, out = self.verifier.run_verification(
                self.verify_script, timeout_sec=self.timeout_sec, cwd=self.repo_dir
            )
            if code != 0:
                print(f"  Verification failed on attempt 1 (Exit {code}). Attempting 1 retry with Maker...", file=sys.stderr)
                job_state.transition_phase("MAKER", attempt=2)
                job_state.save(self.state_path)

                # Attempt 2: Maker retry with verification failure output
                maker_code2, maker_out2 = self.runner_adapter.run_maker(task, context={"attempt": 2, "prev_error": out})
                if maker_code2 != 0:
                    return self._handle_failure(task, checkpoint, total_tasks, succeeded_tasks, job_state, "MAKER_CRASH", maker_out2)

                if not self.git_guard.check_protected_files_untouched(checkpoint):
                    return self._handle_failure(task, checkpoint, total_tasks, succeeded_tasks, job_state, "INVARIANT_BREACH", "Protected file modified on retry")

                code, out = self.verifier.run_verification(
                    self.verify_script, timeout_sec=self.timeout_sec, cwd=self.repo_dir
                )
                if code != 0:
                    print(f"  Verification failed on attempt 2 (Exit {code}). Strict Fail-Fast triggered.", file=sys.stderr)
                    return self._handle_failure(task, checkpoint, total_tasks, succeeded_tasks, job_state, "VERIFIER_ERROR", out)

            # Phase D: 独立査読 (Checker Review)
            print("  [Phase D: Checker] Running independent review...")
            job_state.transition_phase("CHECKER")
            job_state.save(self.state_path)
            diff_proc = self.git_guard._run_git(["diff", checkpoint])
            diff_text = diff_proc.stdout

            # 未追跡の新規作成ファイルも Checker への差分に含める
            status_proc = self.git_guard._run_git(["status", "--porcelain"])
            untracked_diffs: list[str] = []
            for line in status_proc.stdout.splitlines():
                if line.startswith("?? "):
                    u_path = line[3:].strip().strip('"')
                    full_p = self.repo_dir / u_path
                    if full_p.is_file():
                        try:
                            content = full_p.read_text(encoding="utf-8")
                            untracked_diffs.append(f"--- /dev/null\n+++ b/{u_path}\n@@ -0,0 +1 @@\n{content}")
                        except (OSError, UnicodeDecodeError):
                            pass

            if untracked_diffs:
                diff_text = (diff_text + "\n" + "\n".join(untracked_diffs)).strip()

            spec_text = self.spec_path.read_text(encoding="utf-8") if self.spec_path.exists() else ""
            approved, checker_reason = self.runner_adapter.run_checker(task, diff=diff_text, spec_content=spec_text)
            if not approved:
                print(f"  Checker REJECTED task {task.task_id}: {checker_reason}", file=sys.stderr)
                return self._handle_failure(task, checkpoint, total_tasks, succeeded_tasks, job_state, "CHECKER_REJECT", checker_reason)

            # Phase E: 確定とコミット (Evidence & Commit)
            print("  [Phase E: Commit] Checker approved. Issuing evidence and committing...")
            job_state.transition_phase("COMMIT")
            self.evidence_store.issue_evidence(task.task_id, checkpoint)
            if not self.evidence_store.verify_and_consume(task.task_id, checkpoint):
                print(f"  EVIDENCE ERROR: Could not verify evidence for task {task.task_id}", file=sys.stderr)
                return 3

            self.dispatcher.mark_task_status(self.plan_path, task, status="completed")
            commit_hash = self.git_guard.commit_task(task)
            task_info = {"task_id": task.task_id, "title": task.title, "commit": commit_hash, "verifier": "verify_local.sh & Checker"}
            succeeded_tasks.append(task_info)
            job_state.completed_tasks = succeeded_tasks
            job_state.save(self.state_path)

            tasks = self.dispatcher.parse_plan(self.plan_path)

        # 完了処理
        job_state.status = "COMPLETED"
        job_state.current_task_id = None
        job_state.transition_phase("COMPLETED")
        if not self.dry_run:
            job_state.save(self.state_path)
            self.reporter.write_report(
                self.report_path,
                {
                    "status": "COMPLETED",
                    "total_tasks": total_tasks,
                    "succeeded_count": len(succeeded_tasks),
                    "quarantined_count": 0,
                    "remaining_count": 0,
                    "checkpoint": self.git_guard.get_current_checkpoint(),
                    "completed_tasks": succeeded_tasks,
                },
            )
            print(f"\nExecution complete! Report written to {self.report_path}")
        else:
            print(f"\n[DRY-RUN] Execution simulation complete. {len(succeeded_tasks)} tasks simulated. Report was not modified.")
        return 0

    def _handle_failure(
        self,
        task: Task,
        checkpoint: str,
        total_tasks: int,
        succeeded_tasks: list[dict[str, Any]],
        job_state: JobState,
        phase: str,
        reason: str,
    ) -> int:
        self.evidence_store.clear_evidence(task.task_id)
        job_state.status = "STOPPED_FAIL_FAST"
        job_state.transition_phase("QUARANTINE")
        quarantine_branch = self.git_guard.quarantine_and_restore(checkpoint, task_id=task.task_id)
        self.dispatcher.mark_task_status(self.plan_path, task, status="failed")

        log_path = self.repo_dir / "logs" / "nightly" / f"task_{task.task_id}_failure.log"
        self.reporter.write_failure_log(
            log_path,
            {
                "task_id": task.task_id,
                "title": task.title,
                "checkpoint": checkpoint,
                "quarantined_branch": quarantine_branch,
                "attempt_1_log": reason,
            },
        )
        quarantined_info = {
            "task_id": task.task_id,
            "phase": phase,
            "quarantined_branch": quarantine_branch,
            "log_path": str(log_path),
            "root_cause_summary": reason[:300],
        }
        job_state.quarantined_task = quarantined_info
        job_state.save(self.state_path)

        self.reporter.write_report(
            self.report_path,
            {
                "status": "STOPPED (FAIL-FAST)",
                "total_tasks": total_tasks,
                "succeeded_count": len(succeeded_tasks),
                "quarantined_count": 1,
                "remaining_count": total_tasks - len(succeeded_tasks) - 1,
                "checkpoint": checkpoint,
                "completed_tasks": succeeded_tasks,
                "quarantined_task": quarantined_info,
            },
        )
        return 1
