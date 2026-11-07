from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from auto_developer.bundler import EvidenceBundler
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
        self.reporter = MorningReporter(repo_dir=self.repo_dir)
        self.evidence_store = EvidenceStore(repo_dir=self.repo_dir)

    def run(self) -> int:
        if not self.plan_path.exists():
            print(f"PRE-FLIGHT ERROR: Plan file not found: {self.plan_path}", file=sys.stderr)
            return 2

        # 0. プロセス排他ロック (同一ワークスペースでの二重起動防止)
        import fcntl
        import os

        lock_file = self.repo_dir / ".dev" / "auto_developer.lock"
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(lock_file, "a+") as lock_fd:
                try:
                    fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    lock_fd.seek(0)
                    lock_fd.truncate()
                    lock_fd.write(str(os.getpid()))
                    lock_fd.flush()
                except (BlockingIOError, OSError):
                    print(
                        f"CONCURRENCY ERROR: Another auto-dev process is currently executing in {self.repo_dir}.\n"
                        f"Acquisition of exclusive lock {lock_file} failed. Exiting to prevent concurrent state corruption.",
                        file=sys.stderr,
                    )
                    return 2

                try:
                    return self._run_loop()
                finally:
                    try:
                        lock_fd.seek(0)
                        lock_fd.truncate()
                        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
        except OSError as e:
            print(f"LOCK ERROR: Could not open lock file {lock_file}: {e}", file=sys.stderr)
            return 2

    def _run_loop(self) -> int:

        # 1. JobState の初期化または再開
        if self.resume and self.state_path.exists():
            job_state = JobState.load(self.state_path)
            if job_state.status == "COMPLETED":
                print("RESUME INFO: Job is already COMPLETED. Nothing to resume.")
                return 0
            if job_state.status == "FAILED":
                print(
                    f"RESUME ERROR: Job {job_state.job_id} previously ended with FAILED status. "
                    "Quarantined task must be inspected/resolved before starting a new run.",
                    file=sys.stderr,
                )
                return 2
            if not job_state.can_resume():
                print(
                    f"RESUME ERROR: Job {job_state.job_id} is in status '{job_state.status}' and cannot be resumed.",
                    file=sys.stderr,
                )
                return 2

            print(f"Resuming job {job_state.job_id} from phase {job_state.current_phase}")
            # HEAD 照合: 現在の作業ツリーの親コミット (HEAD) が保存された checkpoint_hash と一致するか
            current_head = self.git_guard.get_current_checkpoint()
            if job_state.checkpoint_hash and current_head != job_state.checkpoint_hash:
                print(
                    f"RESUME ERROR: Current git HEAD ({current_head}) does not match job checkpoint ({job_state.checkpoint_hash}).\n"
                    "Manual commits or branch changes detected. Refusing destructive rollback.",
                    file=sys.stderr,
                )
                return 2

            # クラッシュ時の残骸作業ツリーがあれば隔離退避してクリーン復元
            if job_state.checkpoint_hash and not self.dry_run:
                print(f"Resetting uncommitted work back to checkpoint {job_state.checkpoint_hash} for safe resume...")
                self.git_guard.quarantine_and_restore(
                    job_state.checkpoint_hash, task_id=job_state.current_task_id or "interrupted"
                )
                if not job_state.base_branch:
                    job_state.base_branch = self.git_guard.get_current_branch()
        else:
            job_id = f"job-{int(time.time())}"
            current_branch = self.git_guard.get_current_branch()
            job_state = JobState(job_id=job_id, status="RUNNING", base_branch=current_branch)
            if not self.dry_run:
                job_state.save(self.state_path)

        # 2. クリーンツリー事前検証
        if not self.dry_run:
            try:
                self.git_guard.assert_clean_working_tree()
            except RuntimeError as e:
                print(f"PRE-FLIGHT ERROR: {e}", file=sys.stderr)
                return 2

        initial_checkpoint = self.git_guard.get_current_checkpoint()
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

            # タスクごとの試行単位履歴
            task_attempts: list[dict[str, Any]] = []
            attempt_1_log: str = ""
            attempt_2_log: str = ""

            # Phase A: Maker 実装呼び出し
            print(f"  [Phase A: Maker] Running Maker for task {task.task_id}...")
            maker_code, maker_out = self.runner_adapter.run_maker(task, context={"attempt": 1})
            if maker_code != 0:
                print(f"  Maker failed with exit {maker_code}:\n{maker_out}", file=sys.stderr)
                return self._handle_failure(
                    task,
                    checkpoint,
                    total_tasks,
                    succeeded_tasks,
                    job_state,
                    "MAKER_CRASH",
                    maker_out,
                    attempt_1_log=maker_out,
                    attempts=[{"attempt": 1, "phase": "MAKER", "exit_code": maker_code, "output_summary": maker_out.splitlines()[-5:]}],
                )

            # Phase A-2: 差分存在検査 (No-Op Reject)
            if not self.git_guard.has_changes_since(checkpoint):
                print(f"  REJECT: Maker completed with exit 0 but produced no modifications for task {task.task_id}!", file=sys.stderr)
                return self._handle_failure(
                    task,
                    checkpoint,
                    total_tasks,
                    succeeded_tasks,
                    job_state,
                    "MAKER_NO_DIFF",
                    "Maker completed with exit 0 but produced no changes (diff or new files)",
                    attempt_1_log=maker_out,
                )

            # Phase B: 不変条件検査 (Circuit Breaker)
            if not self.git_guard.check_protected_files_untouched(checkpoint):
                print(f"  CIRCUIT BREAKER: Protected file modified in task {task.task_id}!", file=sys.stderr)
                return self._handle_failure(
                    task,
                    checkpoint,
                    total_tasks,
                    succeeded_tasks,
                    job_state,
                    "INVARIANT_BREACH",
                    "Protected file modification detected",
                    attempt_1_log=maker_out,
                )

            # 確定対象のツリーを検証前に反映（PLAN.md のタスク状態を completed に更新）
            self.dispatcher.mark_task_status(self.plan_path, task, status="completed")

            # Phase C: 決定論的検証 & 再試行 (Verification Gate)
            print(f"  [Phase C: Verifier] Running {self.verify_script}...")
            code, out = self.verifier.run_verification(
                self.verify_script, timeout_sec=self.timeout_sec, cwd=self.repo_dir
            )
            last_out_lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
            task_attempts.append({
                "attempt": 1,
                "phase": "VERIFY",
                "exit_code": code,
                "output_summary": last_out_lines[-10:] if last_out_lines else [],
            })
            attempt_1_log = f"Maker (Attempt 1) exit {maker_code}\n{maker_out}\n\nVerifier (Attempt 1) exit {code}\n{out}"

            if code != 0:
                print(f"  Verification failed on attempt 1 (Exit {code}). Attempting 1 retry with Maker...", file=sys.stderr)
                job_state.transition_phase("MAKER", attempt=2)
                job_state.save(self.state_path)

                # Attempt 2: Maker retry with verification failure output
                maker_code2, maker_out2 = self.runner_adapter.run_maker(task, context={"attempt": 2, "prev_error": out})
                if maker_code2 != 0:
                    task_attempts.append({
                        "attempt": 2,
                        "phase": "MAKER",
                        "exit_code": maker_code2,
                        "output_summary": maker_out2.splitlines()[-5:],
                    })
                    return self._handle_failure(
                        task,
                        checkpoint,
                        total_tasks,
                        succeeded_tasks,
                        job_state,
                        "MAKER_CRASH",
                        maker_out2,
                        attempt_1_log=attempt_1_log,
                        attempt_2_log=maker_out2,
                        attempts=task_attempts,
                    )

                if not self.git_guard.has_changes_since(checkpoint):
                    return self._handle_failure(
                        task,
                        checkpoint,
                        total_tasks,
                        succeeded_tasks,
                        job_state,
                        "MAKER_NO_DIFF",
                        "Maker retry produced no changes",
                        attempt_1_log=attempt_1_log,
                        attempt_2_log=maker_out2,
                        attempts=task_attempts,
                    )

                if not self.git_guard.check_protected_files_untouched(checkpoint):
                    return self._handle_failure(
                        task,
                        checkpoint,
                        total_tasks,
                        succeeded_tasks,
                        job_state,
                        "INVARIANT_BREACH",
                        "Protected file modified on retry",
                        attempt_1_log=attempt_1_log,
                        attempt_2_log=maker_out2,
                        attempts=task_attempts,
                    )

                self.dispatcher.mark_task_status(self.plan_path, task, status="completed")
                code, out = self.verifier.run_verification(
                    self.verify_script, timeout_sec=self.timeout_sec, cwd=self.repo_dir
                )
                last_out_lines2 = [l.strip() for l in out.strip().splitlines() if l.strip()]
                task_attempts.append({
                    "attempt": 2,
                    "phase": "VERIFY",
                    "exit_code": code,
                    "output_summary": last_out_lines2[-10:] if last_out_lines2 else [],
                })
                attempt_2_log = f"Maker (Attempt 2) exit {maker_code2}\n{maker_out2}\n\nVerifier (Attempt 2) exit {code}\n{out}"

                if code != 0:
                    print(f"  Verification failed on attempt 2 (Exit {code}). Strict Fail-Fast triggered.", file=sys.stderr)
                    return self._handle_failure(
                        task,
                        checkpoint,
                        total_tasks,
                        succeeded_tasks,
                        job_state,
                        "VERIFIER_ERROR",
                        out,
                        attempt_1_log=attempt_1_log,
                        attempt_2_log=attempt_2_log,
                        attempts=task_attempts,
                    )

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
                return self._handle_failure(
                    task,
                    checkpoint,
                    total_tasks,
                    succeeded_tasks,
                    job_state,
                    "CHECKER_REJECT",
                    checker_reason,
                    attempt_1_log=attempt_1_log,
                    attempt_2_log=attempt_2_log,
                    attempts=task_attempts,
                )

            # Phase E: 確定とコミット (Evidence & Commit)
            print("  [Phase E: Commit] Checker approved. Finalizing plan and issuing evidence...")
            job_state.transition_phase("COMMIT")

            # 1. すでに検証・査読を通過した作業ツリー全体の Tree SHA と SPEC SHA を取得
            final_tree_sha = self.git_guard.get_working_tree_sha()
            spec_sha = self.git_guard.get_spec_sha(self.spec_path)

            # 2. 検証済みツリーに対する証跡を発行
            last_out_lines = [l.strip() for l in out.strip().splitlines() if l.strip()]
            verification_meta = {
                "exit_code": code,
                "output_summary": last_out_lines[-10:] if last_out_lines else [],
            }
            self.evidence_store.issue_evidence(
                task.task_id,
                checkpoint,
                tree_sha=final_tree_sha,
                spec_sha=spec_sha,
                details={
                    "checker_decision": checker_reason,
                    "verification": verification_meta,
                    "attempts": task_attempts,
                },
            )

            # 3. 証跡の検証と消費（ツリー整合性照合）
            current_tree = self.git_guard.get_working_tree_sha()
            current_spec = self.git_guard.get_spec_sha(self.spec_path)
            if not self.evidence_store.verify_and_consume(
                task.task_id,
                checkpoint,
                expected_tree_sha=current_tree,
                expected_spec_sha=current_spec,
            ):
                print(f"  EVIDENCE ERROR: Could not verify evidence for task {task.task_id}", file=sys.stderr)
                return 3

            # 4. コミット実行（固定されたインデックスから直接コミット）
            commit_hash = self.git_guard.commit_task(task)

            # 5. コミットされた Git Commit の Tree SHA と Evidence Tree SHA の完全一致を検証
            committed_tree_sha = self.git_guard.get_commit_tree_sha(commit_hash)
            if committed_tree_sha != final_tree_sha:
                print(
                    f"  FATAL INVARIANT BREACH: Committed tree {committed_tree_sha} does not match verified tree {final_tree_sha}!",
                    file=sys.stderr,
                )
                return 3

            # 6. 永続監査レコード（Evidence Record）の保存 (機能N / P1-1, 機能U)
            self.evidence_store.archive_record(
                task_id=task.task_id,
                status="COMPLETED",
                commit_info={
                    "sha": commit_hash,
                    "tree_sha": committed_tree_sha,
                    "tree_verified": True,
                },
                checker_info={
                    "decision": "APPROVE",
                    "reason": checker_reason,
                },
                verification_info=verification_meta,
                attempts=task_attempts,
            )

            task_info = {
                "task_id": task.task_id,
                "title": task.title,
                "commit": commit_hash,
                "evidence_tree_sha": final_tree_sha,
                "committed_tree_sha": committed_tree_sha,
                "verifier": "verify_local.sh & Checker",
            }
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
            # 意思決定支援型 Morning Brief サマリーの組み立て (機能P, 機能R)
            exec_summary = (
                f"全 {len(succeeded_tasks)} 件のタスクが検証ゲートおよび独立Checker査読を通過し、正常に完了しました。"
                if len(succeeded_tasks) == total_tasks
                else f"{len(succeeded_tasks)} / {total_tasks} 件のタスクが正常に完了しました。"
            )
            verify_cmd = "bash scripts/verify_local.sh" if (self.repo_dir / "scripts" / "verify_local.sh").exists() else "python3 -m unittest discover tests"

            # 成果物利用コマンド (Run) の自動推測
            run_cmd = None
            if (self.repo_dir / "src").is_dir():
                pkgs = [p.name for p in (self.repo_dir / "src").iterdir() if p.is_dir() and (p / "__main__.py").exists()]
                if pkgs:
                    run_cmd = f"python3 -m {pkgs[0]} --help"
            if not run_cmd:
                root_pkgs = [p.name for p in self.repo_dir.iterdir() if p.is_dir() and (p / "__main__.py").exists()]
                if root_pkgs:
                    run_cmd = f"python3 -m {root_pkgs[0]} --help"

            current_head = self.git_guard.get_current_checkpoint()

            bundle_rel_path = None
            try:
                bundler = EvidenceBundler(repo_dir=self.repo_dir)
                b_path = bundler.create_bundle(job_id=job_state.job_id)
                bundle_rel_path = str(b_path.resolve().relative_to(self.repo_dir.resolve()))
            except (OSError, subprocess.SubprocessError) as e:
                print(f"Warning: Failed to create evidence bundle: {e}", file=sys.stderr)

            self.reporter.write_report(
                self.report_path,
                {
                    "status": "COMPLETED",
                    "total_tasks": total_tasks,
                    "succeeded_count": len(succeeded_tasks),
                    "quarantined_count": 0,
                    "remaining_count": 0,
                    "checkpoint": current_head,
                    "initial_checkpoint": initial_checkpoint,
                    "final_commit": current_head,
                    "completed_tasks": succeeded_tasks,
                    "executive_summary": exec_summary,
                    "run_command": run_cmd,
                    "verify_command": verify_cmd,
                    "bundle_path": bundle_rel_path,
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
        attempt_1_log: str | None = None,
        attempt_2_log: str | None = None,
        attempts: list[dict[str, Any]] | None = None,
    ) -> int:
        self.evidence_store.clear_evidence(task.task_id)
        job_state.status = "FAILED"
        job_state.transition_phase("QUARANTINE")
        self.dispatcher.mark_task_status(self.plan_path, task, status="failed")
        quarantine_branch = self.git_guard.quarantine_and_restore(checkpoint, task_id=task.task_id)

        log_path = self.repo_dir / "logs" / "nightly" / f"task_{task.task_id}_failure.log"
        self.reporter.write_failure_log(
            log_path,
            {
                "task_id": task.task_id,
                "title": task.title,
                "checkpoint": checkpoint,
                "quarantined_branch": quarantine_branch,
                "attempt_1_log": attempt_1_log or reason,
                "attempt_2_log": attempt_2_log or "(none)",
                "attempts": attempts or [],
            },
        )
        quarantined_info = {
            "task_id": task.task_id,
            "phase": phase,
            "quarantined_branch": quarantine_branch,
            "log_path": str(log_path),
            "root_cause_summary": reason[:300],
            "attempts": attempts or [],
        }
        job_state.quarantined_task = quarantined_info
        job_state.save(self.state_path)

        bundle_rel_path = None
        try:
            bundler = EvidenceBundler(repo_dir=self.repo_dir)
            b_path = bundler.create_bundle(job_id=job_state.job_id)
            bundle_rel_path = str(b_path.resolve().relative_to(self.repo_dir.resolve()))
        except (OSError, subprocess.SubprocessError) as e:
            print(f"Warning: Failed to create evidence bundle during failure quarantine: {e}", file=sys.stderr)

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
                "bundle_path": bundle_rel_path,
            },
        )
        return 1
