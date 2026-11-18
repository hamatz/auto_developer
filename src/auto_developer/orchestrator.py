from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from auto_developer.boundary import BoundaryGuard
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
        team_id: str | None = None,
        owns_patterns: list[str] | None = None,
        maker_model: str | None = None,
        checker_model: str | None = None,
        allow_same_model_override: bool = False,
        auditor_runner: Any | None = None,
        auditor_model: str | None = None,
        auditor_cmd_template: str | None = None,
        fallback_models: list[str] | None = None,
        enable_audit_stage: bool = False,
        audit_baseline_commit: str | None = None,
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
        self.team_id = team_id
        self.owns_patterns = owns_patterns
        self.maker_model = maker_model
        self.checker_model = checker_model
        self.allow_same_model_override = allow_same_model_override
        self.auditor_runner = auditor_runner
        self.auditor_model = auditor_model
        self.auditor_cmd_template = auditor_cmd_template
        self.fallback_models = list(fallback_models or [])
        self.enable_audit_stage = enable_audit_stage
        self.audit_baseline_commit = audit_baseline_commit

        self.git_guard = GitGuard(repo_dir=self.repo_dir)
        self.dispatcher = TaskDispatcher()
        self.verifier = DeterministicVerifier()
        self.reporter = MorningReporter(repo_dir=self.repo_dir)
        self.evidence_store = EvidenceStore(repo_dir=self.repo_dir)

    def run(self) -> int:
        if self.maker_model and self.checker_model:
            from auto_developer.audit import verify_model_independence

            ok, reason = verify_model_independence(
                self.maker_model,
                self.checker_model,
                self.allow_same_model_override,
            )
            if not ok:
                print(f"PRE-FLIGHT MODEL POLICY ERROR: {reason}", file=sys.stderr)
                return 2

        if self.enable_audit_stage:
            from auto_developer.audit import verify_model_independence
            from auto_developer.runner import MockRunnerAdapter

            if (
                not self.auditor_runner
                and not self.auditor_cmd_template
                and not isinstance(self.runner_adapter, MockRunnerAdapter)
            ):
                print(
                    "PRE-FLIGHT CONFIG ERROR: In production mode with independent audit enabled, auditor_cmd_template must be provided.",
                    file=sys.stderr,
                )
                return 2

            if self.maker_model and self.auditor_model:
                ok, reason = verify_model_independence(
                    self.maker_model,
                    self.auditor_model,
                    self.allow_same_model_override,
                )
                if not ok:
                    print(f"PRE-FLIGHT AUDIT POLICY ERROR: {reason}", file=sys.stderr)
                    return 2

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

            # 教訓の自動検索・注入 (FFF.4 / Level 7 Rule 10)
            from auto_developer.audit import LessonInjector

            injector = LessonInjector(repo_dir=self.repo_dir)
            matching_lessons = injector.find_relevant_lessons(task.title)
            lessons_injection = injector.format_injection(matching_lessons)
            if matching_lessons:
                print(
                    f"  [Level 7: Rule 10] Injected {len(matching_lessons)} relevant lessons from LESSONS.md into Maker context"
                )

            maker_code, maker_out = self.runner_adapter.run_maker(
                task,
                context={
                    "attempt": 1,
                    "lessons": lessons_injection,
                    "lesson_entries": [e.title for e in matching_lessons],
                },
            )
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
            allow_immunity = str(task.task_id).startswith("AUD-")
            if not self.git_guard.check_protected_files_untouched(
                checkpoint, allow_immunity_additions=allow_immunity
            ):
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

            # Phase B-2: チーム領域（Boundary）検査
            violations = self._check_boundary(checkpoint)
            if violations:
                print(f"  BOUNDARY VIOLATION: Unauthorized modifications outside team owns: {violations}", file=sys.stderr)
                return self._handle_failure(
                    task,
                    checkpoint,
                    total_tasks,
                    succeeded_tasks,
                    job_state,
                    "BOUNDARY_VIOLATION",
                    f"Unauthorized modifications outside team owns: {violations}",
                    attempt_1_log=maker_out,
                )

            # Phase B-3: AUDタスクの解決と免疫テスト自動昇格 (EEE.3)
            red_green_proof = None
            if str(task.task_id).startswith("AUD-"):
                from auto_developer.audit import AuditResolutionManager

                res_mgr = AuditResolutionManager(repo_dir=self.repo_dir)
                res = res_mgr.resolve_task(str(task.task_id))
                if not res.get("success"):
                    print(f"  AUDIT RESOLUTION ERROR: {res.get('reason')}", file=sys.stderr)
                    return self._handle_failure(
                        task,
                        checkpoint,
                        total_tasks,
                        succeeded_tasks,
                        job_state,
                        "AUDIT_RESOLUTION_FAILED",
                        str(res.get("reason")),
                        attempt_1_log=maker_out,
                    )
                red_green_proof = res.get("proof")

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
                maker_code2, maker_out2 = self.runner_adapter.run_maker(
                    task,
                    context={
                        "attempt": 2,
                        "prev_error": out,
                        "lessons": lessons_injection,
                        "lesson_entries": [e.title for e in matching_lessons],
                    },
                )
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

                violations2 = self._check_boundary(checkpoint)
                if violations2:
                    print(f"  BOUNDARY VIOLATION on retry: {violations2}", file=sys.stderr)
                    return self._handle_failure(
                        task,
                        checkpoint,
                        total_tasks,
                        succeeded_tasks,
                        job_state,
                        "BOUNDARY_VIOLATION",
                        f"Unauthorized modifications outside team owns on retry: {violations2}",
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
            verification_meta: dict[str, Any] = {
                "exit_code": code,
                "output_summary": last_out_lines[-10:] if last_out_lines else [],
            }
            if red_green_proof:
                verification_meta["red_green_proof"] = red_green_proof
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
            final_violations = self._check_boundary(checkpoint)
            if final_violations:
                print(f"  BOUNDARY ERROR: Unauthorized files detected at commit gate: {final_violations}", file=sys.stderr)
                return 3

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
                    "hash": commit_hash,
                    "sha": commit_hash,
                    "commit_sha": commit_hash,
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

            # 独立監査ステージの自動実行 (RRR.2 / Sol氏 Phase 57 P1指摘)
            audit_stage_result: dict[str, Any] | None = None
            if self.enable_audit_stage and (self.auditor_runner or self.auditor_model):
                base_c = self.audit_baseline_commit or initial_checkpoint
                print(f"\n[Independent Audit Stage] Running repository audit against baseline {base_c[:7]}...")
                audit_stage_result = self.run_audit_stage(baseline_commit=base_c, target_commit=current_head)

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
                    "audit_stage": audit_stage_result,
                },
            )
            print(f"\nExecution complete! Report written to {self.report_path}")
        else:
            print(f"\n[DRY-RUN] Execution simulation complete. {len(succeeded_tasks)} tasks simulated. Report was not modified.")
        return 0

    def run_audit_stage(
        self,
        baseline_commit: str,
        target_commit: str = "HEAD",
        auditor_runner: Any | None = None,
    ) -> dict[str, Any]:
        """ジョブ完了時に外部独立監査役を起動し、多層防壁で選別した真正指摘を次期 PLAN.md に登録する (RRR.2)。"""
        from auto_developer.audit import IndependentAuditRunner
        from auto_developer.dispatcher import AuditDispatcher
        from auto_developer.verifier import RedTestVerifier

        auditor = auditor_runner or self.auditor_runner
        if not auditor and (self.auditor_model or self.auditor_cmd_template):
            auditor = IndependentAuditRunner(
                auditor_cmd_template=self.auditor_cmd_template or "",
                model_name=self.auditor_model or "claude-opus-5.5",
                fallback_models=self.fallback_models,
                maker_model=self.maker_model,
            )

        if not auditor:
            return {"status": "SKIPPED", "reason": "No auditor configured"}

        try:
            verifier = RedTestVerifier(repo_dir=self.repo_dir)
            dispatcher = AuditDispatcher(repo_dir=self.repo_dir, verifier=verifier)

            findings = auditor.audit_repository(
                repo_dir=self.repo_dir,
                target_commit=target_commit,
                baseline_commit=baseline_commit,
            )
            screen_res = auditor.screen_and_dispatch_findings(
                findings=findings,
                dispatcher=dispatcher,
                plan_path=self.plan_path,
            )
            audit_metadata = getattr(auditor, "last_run_metadata", {}) or {}

            # 監査証跡レコードの保存 (SSS.4)
            audit_rec_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
            if not audit_rec_dir.exists():
                audit_rec_dir = self.repo_dir / ".dev" / "evidence" / "records"
            try:
                audit_rec_dir.mkdir(parents=True, exist_ok=True)
                audit_stage_file = audit_rec_dir / "audit_stage.json"
                audit_stage_file.write_text(
                    json.dumps({
                        "status": "SUCCESS",
                        "mode": audit_metadata.get("mode", "LIVE"),
                        "provider": audit_metadata.get("provider"),
                        "provider_request_id": audit_metadata.get("provider_request_id"),
                        "audit_metadata": audit_metadata,
                        "dispatched_count": len(screen_res["dispatched"]),
                        "rejected_count": len(screen_res["rejected"]),
                        "backlogged_count": len(screen_res.get("backlogged", [])),
                        "escalated_count": len(screen_res.get("escalated", [])),
                        "duplicate_count": len(screen_res.get("duplicates", [])),
                        "timestamp": time.time(),
                    }, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass

            return {
                "status": "SUCCESS",
                "dispatched": screen_res["dispatched"],
                "rejected": screen_res["rejected"],
                "backlogged": screen_res.get("backlogged", []),
                "escalated": screen_res.get("escalated", []),
                "duplicates": screen_res.get("duplicates", []),
                "total_processed": screen_res["total_processed"],
                "genuine_count": screen_res["genuine_count"],
                "rejected_count": screen_res["rejected_count"],
                "duplicate_count": len(screen_res.get("duplicates", [])),
                "baseline_commit": baseline_commit,
                "target_commit": target_commit,
                "audit_metadata": audit_metadata,
            }
        except (subprocess.SubprocessError, OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
            # 外部監査役の失敗時にも通常開発成果物は破棄せず安全に保全する (Fail-Safe)
            err_msg = str(e)
            status = "AUDIT_PENDING" if "NO_INDEPENDENT_AUDITOR_AVAILABLE" in err_msg else "AUDIT_FAILED"
            print(f"Warning: Independent audit stage {status.lower()}: {err_msg}", file=sys.stderr)
            audit_rec_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
            if not audit_rec_dir.exists():
                audit_rec_dir = self.repo_dir / ".dev" / "evidence" / "records"
            try:
                audit_rec_dir.mkdir(parents=True, exist_ok=True)
                (audit_rec_dir / "audit_stage.json").write_text(
                    json.dumps({
                        "status": status,
                        "mode": "LIVE",
                        "error": err_msg,
                        "baseline_commit": baseline_commit,
                        "target_commit": target_commit,
                        "timestamp": time.time(),
                    }, indent=2),
                    encoding="utf-8",
                )
            except OSError:
                pass
            return {
                "status": status,
                "error": err_msg,
                "baseline_commit": baseline_commit,
                "target_commit": target_commit,
                "findings": [],
            }

    def _check_boundary(self, checkpoint: str) -> list[str]:
        """チーム所有スコープ（owns）が指定されている場合、領空侵犯を検査し違反ファイルリストを返す。"""
        if not self.owns_patterns:
            return []
        guard = BoundaryGuard(
            repo_dir=self.repo_dir,
            owns_patterns=self.owns_patterns,
            base_checkpoint=checkpoint,
        )
        res = guard.inspect_changes()
        return res.violations

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
        quarantine_branch = self.git_guard.quarantine_and_restore(
            checkpoint,
            task_id=task.task_id,
            job_id=job_state.job_id,
            team_id=self.team_id or "",
        )

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
