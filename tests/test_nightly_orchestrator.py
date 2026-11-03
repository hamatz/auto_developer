"""Acceptance test suite for Antigravity Autonomous Pipeline Orchestrator (SPEC.md)."""

import subprocess
import sys
import unittest
from pathlib import Path

# Target classes to be implemented in scripts/nightly_orchestrator.py
from scripts.nightly_orchestrator import (
    CommandRunnerAdapter,
    GitGuard,
    JobState,
    MockRunnerAdapter,
    MorningReporter,
    NightlyOrchestrator,
    RunnerAdapter,
    Task,
    TaskDispatcher,
)


class TestNightlyOrchestrator(unittest.TestCase):
    def test_feature_a_task_dispatcher_parse_and_update(self):
        """機能A: PLAN.md から未実行タスクを順次抽出し、状態を更新する。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            plan_content = """# PLAN.md
## ステップ
- [ ] 1. タスク一 (基盤実装)
- [ ] 2. タスク二 (API作成)
- [x] 0. 前提タスク (完了済)
"""
            plan_file = tmp_path / "PLAN.md"
            plan_file.write_text(plan_content, encoding="utf-8")

            dispatcher = TaskDispatcher()
            tasks = dispatcher.parse_plan(plan_file)

            self.assertEqual(len(tasks), 3)
            next_task = dispatcher.get_next_pending_task(tasks)
            self.assertIsNotNone(next_task)
            assert next_task is not None
            self.assertEqual(next_task.task_id, "1")
            self.assertIn("タスク一", next_task.title)
            self.assertFalse(next_task.is_completed)

            # 完了状態に更新
            dispatcher.mark_task_status(plan_file, next_task, status="completed")
            updated_tasks = dispatcher.parse_plan(plan_file)
            task1 = next(t for t in updated_tasks if t.task_id == "1")
            self.assertTrue(task1.is_completed)

            # 失敗状態に更新
            task2 = dispatcher.get_next_pending_task(updated_tasks)
            self.assertIsNotNone(task2)
            assert task2 is not None
            self.assertEqual(task2.task_id, "2")
            dispatcher.mark_task_status(plan_file, task2, status="failed")

            final_content = plan_file.read_text(encoding="utf-8")
            self.assertIn("- [ ] 2. タスク二 (API作成) **[FAILED]**", final_content)

    def test_feature_b_dirty_working_tree_assertion(self):
        """機能B: 未コミットの変更が存在する場合に起動を拒否する。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

            init_file = repo_dir / "init.txt"
            init_file.write_text("initial", encoding="utf-8")
            subprocess.run(["git", "add", "init.txt"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

            git_guard = GitGuard(repo_dir=repo_dir)
            git_guard.assert_clean_working_tree()

            init_file.write_text("dirty change", encoding="utf-8")
            with self.assertRaises(RuntimeError):
                git_guard.assert_clean_working_tree()

    def test_feature_c_protected_files_diff_inspection(self):
        """機能C: 保護対象アセットへの変更を検知して不変条件を保護する。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

            spec_file = repo_dir / "SPEC.md"
            spec_file.write_text("initial spec", encoding="utf-8")
            subprocess.run(["git", "add", "SPEC.md"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "add spec"], cwd=repo_dir, check=True)

            git_guard = GitGuard(repo_dir=repo_dir)
            checkpoint = git_guard.get_current_checkpoint()

            app_file = repo_dir / "app.py"
            app_file.write_text("print('hello')", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
            self.assertTrue(git_guard.check_protected_files_untouched(checkpoint))

            spec_file.write_text("tampered spec", encoding="utf-8")
            subprocess.run(["git", "add", "SPEC.md"], cwd=repo_dir, check=True)
            self.assertFalse(git_guard.check_protected_files_untouched(checkpoint))

            # 未追跡（Untracked）の保護ファイル作成も検知して拒否できること (P0脆弱性の再現)
            subprocess.run(["git", "reset", "--hard", checkpoint], cwd=repo_dir, check=True)
            github_dir = repo_dir / ".github" / "workflows"
            github_dir.mkdir(parents=True)
            untracked_wf = github_dir / "unauthorized.yml"
            untracked_wf.write_text("evil", encoding="utf-8")
            self.assertFalse(
                git_guard.check_protected_files_untouched(checkpoint),
                "GitGuard must detect untracked protected files!",
            )

    def test_feature_e_rollback_and_clean_untracked_files(self):
        """機能E: 障害ロールバック時に未追跡ファイルも含めて作業ツリーを完全初期化する。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

            tracked_file = repo_dir / "tracked.txt"
            tracked_file.write_text("original", encoding="utf-8")
            subprocess.run(["git", "add", "tracked.txt"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "base commit"], cwd=repo_dir, check=True)

            git_guard = GitGuard(repo_dir=repo_dir)
            checkpoint = git_guard.get_current_checkpoint()

            tracked_file.write_text("broken change", encoding="utf-8")
            orphan_file = repo_dir / "orphan_scratch.py"
            orphan_file.write_text("# orphan garbage", encoding="utf-8")
            orphan_dir = repo_dir / "orphan_dir"
            orphan_dir.mkdir()
            (orphan_dir / "temp.o").write_text("binary", encoding="utf-8")

            self.assertEqual(tracked_file.read_text(encoding="utf-8"), "broken change")
            self.assertTrue(orphan_file.exists())
            self.assertTrue((orphan_dir / "temp.o").exists())

            git_guard.rollback_and_clean(checkpoint)

            self.assertEqual(tracked_file.read_text(encoding="utf-8"), "original")
            self.assertFalse(orphan_file.exists())
            self.assertFalse(orphan_dir.exists())

    def test_feature_e_quarantine_preserves_work_in_branch(self):
        """機能E拡張: ロールバック時に作業成果を完全破棄せず quarantine ブランチに退避する。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

            tracked_file = repo_dir / "app.py"
            tracked_file.write_text("def run(): pass", encoding="utf-8")
            subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "base commit"], cwd=repo_dir, check=True)

            git_guard = GitGuard(repo_dir=repo_dir)
            checkpoint = git_guard.get_current_checkpoint()

            # 作業ツリーで実装を試行
            tracked_file.write_text("def run(): return 'almost working'", encoding="utf-8")
            helper_file = repo_dir / "helper.py"
            helper_file.write_text("# helper logic", encoding="utf-8")

            # 隔離退避 & 復元を実行
            quarantine_branch = git_guard.quarantine_and_restore(checkpoint=checkpoint, task_id="task-99")

            # 本線作業ツリーはクリーンに復元されていること
            self.assertEqual(tracked_file.read_text(encoding="utf-8"), "def run(): pass")
            self.assertFalse(helper_file.exists())
            git_guard.assert_clean_working_tree()

            # 隔離ブランチに成果が保存されていること
            proc = subprocess.run(
                ["git", "show", f"{quarantine_branch}:helper.py"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("# helper logic", proc.stdout)

            proc_diff = subprocess.run(
                ["git", "diff", f"{checkpoint}..{quarantine_branch}", "--", "app.py"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertIn("almost working", proc_diff.stdout)

    def test_feature_evidence_gated_commit(self):
        """機能追加: verify-step の証跡なしで commit-step を呼ぶと拒否されること (P0対応)。"""
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

            plan_file = repo_dir / "PLAN.md"
            plan_file.write_text("- [ ] 1. テストタスク\n", encoding="utf-8")
            verify_script = repo_dir / "verify.sh"
            verify_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            verify_script.chmod(0o755)

            subprocess.run(["git", "add", "PLAN.md", "verify.sh"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

            # 新規コードを作成
            (repo_dir / "app.py").write_text("print('hello')", encoding="utf-8")

            # 1. 証跡なしで commit-step を呼び出すと失敗すること
            proc_commit_fail = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parents[1] / "scripts" / "nightly_orchestrator.py"),
                    "--plan",
                    str(plan_file),
                    "commit-step",
                    "--task-id",
                    "1",
                ],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(
                proc_commit_fail.returncode,
                0,
                "commit-step must fail without verification evidence",
            )
            self.assertIn("EVIDENCE", proc_commit_fail.stderr)

            # 2. verify-step を実行すると証跡が発行されること
            checkpoint = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
            ).stdout.strip()

            proc_verify = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parents[1] / "scripts" / "nightly_orchestrator.py"),
                    "--plan",
                    str(plan_file),
                    "--verify-script",
                    str(verify_script),
                    "verify-step",
                    "--checkpoint",
                    checkpoint,
                    "--task-id",
                    "1",
                ],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_verify.returncode, 0, f"verify-step failed: {proc_verify.stderr}")
            self.assertIn("VERIFICATION PASSED", proc_verify.stdout)

            # 3. 証跡発行後は commit-step が成功し、証跡が消費されること
            proc_commit_success = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parents[1] / "scripts" / "nightly_orchestrator.py"),
                    "--plan",
                    str(plan_file),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    checkpoint,
                ],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                proc_commit_success.returncode,
                0,
                f"commit-step should succeed with valid evidence: {proc_commit_success.stderr}",
            )
            self.assertIn("COMMITTED", proc_commit_success.stdout)

            # 4. コミット後は証跡が消費されているため、再度 commit-step を呼ぶと拒否されること (リプレイ防止)
            proc_commit_replay = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).parents[1] / "scripts" / "nightly_orchestrator.py"),
                    "--plan",
                    str(plan_file),
                    "commit-step",
                    "--task-id",
                    "1",
                ],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(proc_commit_replay.returncode, 0)

    def test_job_state_persistence_and_recovery(self):
        """JobState の永続化と中断復旧（Crash Recovery）の検証。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            state_file = Path(tmp_dir) / "job_state.json"

            # 1. 状態の初期化と保存
            state = JobState(
                job_id="job-20261103",
                status="RUNNING",
                current_task_id="1",
                current_phase="MAKER",
                current_attempt=1,
                checkpoint_hash="abc1234",
            )
            state.save(state_file)
            self.assertTrue(state_file.exists())

            # 2. クラッシュ後の再読み込み（復元）
            loaded = JobState.load(state_file)
            self.assertEqual(loaded.job_id, "job-20261103")
            self.assertEqual(loaded.status, "RUNNING")
            self.assertEqual(loaded.current_task_id, "1")
            self.assertEqual(loaded.current_phase, "MAKER")
            self.assertTrue(loaded.can_resume())

            # 3. 状態遷移と完了の保存
            loaded.transition_phase("COMMIT")
            loaded.status = "COMPLETED"
            loaded.save(state_file)

            final_state = JobState.load(state_file)
            self.assertEqual(final_state.status, "COMPLETED")
            self.assertFalse(final_state.can_resume())

    def test_runner_adapter_mock_and_command(self):
        """RunnerAdapter の Mock および Command 実行の検証。"""
        task = Task(task_id="1", title="モックタスク", line_number=0, is_completed=False, is_failed=False)

        # 1. MockRunnerAdapter
        mock_runner = MockRunnerAdapter(maker_exit_code=0, checker_approve=True)
        code, out = mock_runner.run_maker(task, context={"prompt": "implement task 1"})
        self.assertEqual(code, 0)
        approved, _ = mock_runner.run_checker(task, diff="some diff", spec_content="spec")
        self.assertTrue(approved)

        # Checker REJECT のケース
        rejecting_runner = MockRunnerAdapter(maker_exit_code=0, checker_approve=False, checker_reason="Bad style")
        app, rsn = rejecting_runner.run_checker(task, diff="some diff", spec_content="spec")
        self.assertFalse(app)
        self.assertEqual(rsn, "Bad style")

        # 2. CommandRunnerAdapter (bash -c でシミュレート)
        cmd_runner = CommandRunnerAdapter(
            maker_cmd_template="echo 'Making task {task_id}'",
            checker_cmd_template="echo 'APPROVE: Looks solid'",
        )
        code, out = cmd_runner.run_maker(task, context={})
        self.assertEqual(code, 0)
        self.assertIn("Making task 1", out)

        app, rsn = cmd_runner.run_checker(task, diff="+ new code", spec_content="spec")
        self.assertTrue(app)
        self.assertIn("Looks solid", rsn)

    def test_autonomous_orchestrator_full_cycle(self):
        """オーケストレーター自律走行: Maker起動 -> 検証 -> Checker査読 -> コミット -> State更新。"""
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

            (repo_dir / ".gitignore").write_text(".dev/\nNIGHTLY_REPORT.md\nlogs/\n", encoding="utf-8")
            plan_file = repo_dir / "PLAN.md"
            plan_file.write_text("- [ ] 1. 自動実装タスク\n", encoding="utf-8")
            spec_file = repo_dir / "SPEC.md"
            spec_file.write_text("# SPEC\nMust create feature.py with hello()\n", encoding="utf-8")
            python_bin = sys.executable
            verify_script = repo_dir / "verify.sh"
            verify_script.write_text(
                f"#!/usr/bin/env bash\n{python_bin} -c 'import feature; assert feature.hello() == \"world\"'\n",
                encoding="utf-8",
            )
            verify_script.chmod(0o755)

            subprocess.run(["git", "add", ".gitignore", "PLAN.md", "SPEC.md", "verify.sh"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

            # Maker 実装を行うカスタム RunnerAdapter
            class CodeGeneratingRunner(RunnerAdapter):
                def run_maker(self, task: Task, context: dict):
                    (repo_dir / "feature.py").write_text('def hello(): return "world"\n', encoding="utf-8")
                    return 0, "Created feature.py"

                def run_checker(self, task: Task, diff: str, spec_content: str):
                    if "feature.py" in diff:
                        return True, "APPROVE: feature.py correctly implemented"
                    return False, "REJECT: feature.py missing"

            state_file = repo_dir / ".dev" / "job_state.json"
            report_file = repo_dir / "NIGHTLY_REPORT.md"

            orchestrator = NightlyOrchestrator(
                repo_dir=repo_dir,
                plan_path=plan_file,
                spec_path=spec_file,
                verify_script=verify_script,
                report_path=report_file,
                state_path=state_file,
                runner_adapter=CodeGeneratingRunner(),
            )

            exit_code = orchestrator.run()
            self.assertEqual(exit_code, 0)

            # 1. PLAN.md が [x] になっていること
            updated_plan = plan_file.read_text(encoding="utf-8")
            self.assertIn("- [x] 1. 自動実装タスク", updated_plan)

            # 2. Git コミットが作成されていること
            log_proc = subprocess.run(
                ["git", "log", "-1", "--oneline"], cwd=repo_dir, capture_output=True, text=True, check=True
            )
            self.assertIn("feat: 自動実装タスク", log_proc.stdout)

            # 3. JobState が COMPLETED になっていること
            saved_state = JobState.load(state_file)
            self.assertEqual(saved_state.status, "COMPLETED")
            self.assertEqual(len(saved_state.completed_tasks), 1)

            # 4. レポートが生成されていること
            self.assertTrue(report_file.exists())
            self.assertIn("COMPLETED", report_file.read_text(encoding="utf-8"))

    def test_job_resume_after_interruption(self):
        """プロセス中断後の --resume による安全な再開の受入検証。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

            (repo_dir / ".gitignore").write_text(".dev/\nNIGHTLY_REPORT.md\nlogs/\n", encoding="utf-8")
            plan_file = repo_dir / "PLAN.md"
            plan_file.write_text("- [ ] 1. タスク一\n- [ ] 2. タスク二\n", encoding="utf-8")
            spec_file = repo_dir / "SPEC.md"
            spec_file.write_text("# SPEC\n", encoding="utf-8")
            verify_script = repo_dir / "verify.sh"
            verify_script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            verify_script.chmod(0o755)

            subprocess.run(["git", "add", ".gitignore", "PLAN.md", "SPEC.md", "verify.sh"], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

            state_file = repo_dir / ".dev" / "job_state.json"
            report_file = repo_dir / "NIGHTLY_REPORT.md"

            # 1回目の実行: タスク1完了後、タスク2のMakerで例外発生（クラッシュを模擬）
            class CrashingRunner(RunnerAdapter):
                def __init__(self):
                    self.task1_done = False

                def run_maker(self, task: Task, context: dict):
                    if task.task_id == "1":
                        (repo_dir / "task1.txt").write_text("done", encoding="utf-8")
                        self.task1_done = True
                        return 0, "Task 1 completed"
                    # タスク2でクラッシュ
                    raise SystemExit("Simulated Crash during Task 2")

                def run_checker(self, task: Task, diff: str, spec_content: str):
                    return True, "APPROVE"

            orch1 = NightlyOrchestrator(
                repo_dir=repo_dir,
                plan_path=plan_file,
                spec_path=spec_file,
                verify_script=verify_script,
                report_path=report_file,
                state_path=state_file,
                runner_adapter=CrashingRunner(),
            )

            with self.assertRaises(SystemExit):
                orch1.run()

            # タスク1はコミット済みであること
            plan_after_crash = plan_file.read_text(encoding="utf-8")
            self.assertIn("- [x] 1. タスク一", plan_after_crash)
            self.assertIn("- [ ] 2. タスク二", plan_after_crash)

            # 状態ファイルが残っていること
            state_after_crash = JobState.load(state_file)
            self.assertEqual(len(state_after_crash.completed_tasks), 1)

            # 2回目の実行: --resume で再開（クラッシュしない正常なRunnerを使用）
            class HealthyRunner(RunnerAdapter):
                def run_maker(self, task: Task, context: dict):
                    (repo_dir / f"task{task.task_id}.txt").write_text("done", encoding="utf-8")
                    return 0, "OK"

                def run_checker(self, task: Task, diff: str, spec_content: str):
                    return True, "APPROVE"

            orch2 = NightlyOrchestrator(
                repo_dir=repo_dir,
                plan_path=plan_file,
                spec_path=spec_file,
                verify_script=verify_script,
                report_path=report_file,
                state_path=state_file,
                runner_adapter=HealthyRunner(),
                resume=True,
            )

            exit_code = orch2.run()
            self.assertEqual(exit_code, 0)

            # 最終的にタスク1・2両方が [x] になり完了していること
            final_plan = plan_file.read_text(encoding="utf-8")
            self.assertIn("- [x] 1. タスク一", final_plan)
            self.assertIn("- [x] 2. タスク二", final_plan)

            final_state = JobState.load(state_file)
            self.assertEqual(final_state.status, "COMPLETED")
            self.assertEqual(len(final_state.completed_tasks), 2)

    def test_feature_f_morning_report_generation(self):
        """機能F刷新: NIGHTLY_REPORT.md が意思決定支援型 Morning Brief として生成される。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            reporter = MorningReporter()
            report_file = Path(tmp_dir) / "NIGHTLY_REPORT.md"

            summary_data = {
                "status": "STOPPED (FAIL-FAST)",
                "total_tasks": 3,
                "succeeded_count": 1,
                "quarantined_count": 1,
                "remaining_count": 1,
                "checkpoint": "abc1234",
                "completed_tasks": [
                    {"task_id": "1", "title": "基盤設定", "commit": "def5678", "verifier": "verify_local.sh & Checker"}
                ],
                "quarantined_task": {
                    "task_id": "2",
                    "title": "API認証処理",
                    "phase": "VERIFIER_ERROR",
                    "quarantined_branch": "quarantine/task-2-1793705000",
                    "log_path": "logs/nightly/task_2_failure.log",
                    "root_cause_summary": "AssertionError: expected 200 but got 500",
                },
            }

            reporter.write_report(report_file, summary_data)

            self.assertTrue(report_file.exists())
            content = report_file.read_text(encoding="utf-8")
            self.assertIn("# AUTO DEVELOPER / MORNING BRIEF", content)
            self.assertIn("STOPPED (FAIL-FAST)", content)
            self.assertIn("今回の成果", content)
            self.assertIn("基盤設定", content)
            self.assertIn("`def5678`", content)
            self.assertIn("要確認・隔離事項", content)
            self.assertIn("quarantine/task-2-1793705000", content)
            self.assertIn("AssertionError: expected 200 but got 500", content)
            self.assertIn("あなたに判断してほしいこと", content)
            self.assertIn("git diff", content)
            self.assertIn("--resume", content)

    def test_cli_morning_brief_subcommand(self):
        """CLI サブコマンド morning-brief によるコンソールブリーフィング表示の検証。"""
        import sys
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo_dir = Path(tmp_dir) / "repo"
            repo_dir.mkdir()
            state_file = repo_dir / ".dev" / "job_state.json"
            state = JobState(
                job_id="job-brief-test",
                status="STOPPED_FAIL_FAST",
                current_task_id="2",
                checkpoint_hash="c0ffee1",
                completed_tasks=[{"task_id": "1", "title": "初期タスク", "commit": "c0ffee1"}],
                quarantined_task={
                    "task_id": "2",
                    "title": "障害タスク",
                    "phase": "VERIFIER_ERROR",
                    "quarantined_branch": "quarantine/task-2-12345",
                    "log_path": "logs/nightly/task_2_failure.log",
                    "root_cause_summary": "AssertionError: expected 1 got 2",
                },
            )
            state.save(state_file)

            script_path = Path(__file__).parents[1] / "scripts" / "nightly_orchestrator.py"
            proc = subprocess.run(
                [sys.executable, str(script_path), "--state-file", str(state_file), "morning-brief"],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"morning-brief failed: {proc.stderr}")
            self.assertIn("AUTO DEVELOPER / MORNING BRIEF", proc.stdout)
            self.assertIn("STOPPED_FAIL_FAST", proc.stdout)
            self.assertIn("quarantine/task-2-12345", proc.stdout)
            self.assertIn("--resume", proc.stdout)

    def test_cli_init_command(self):
        """auto-dev init コマンドによるプロジェクトひな形生成の受入検証。"""
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            target_project = Path(tmp_dir) / "my_new_app"
            target_project.mkdir()

            script_path = Path(__file__).parents[1] / "scripts" / "nightly_orchestrator.py"

            # 1. 空のプロジェクトで auto-dev init を実行
            proc = subprocess.run(
                [sys.executable, str(script_path), "--work-dir", str(target_project), "init"],
                cwd=target_project,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 0, f"init failed: {proc.stderr}")
            self.assertIn("Initialization complete", proc.stdout)

            # 生成されたファイルを検証
            self.assertTrue((target_project / "PLAN.md").exists())
            self.assertTrue((target_project / "SPEC.md").exists())
            verify_sh = target_project / "scripts" / "verify_local.sh"
            self.assertTrue(verify_sh.exists())
            self.assertTrue(os.access(verify_sh, os.X_OK))

            gitignore = target_project / ".gitignore"
            self.assertTrue(gitignore.exists())
            gitignore_text = gitignore.read_text(encoding="utf-8")
            self.assertIn(".dev/", gitignore_text)
            self.assertIn("NIGHTLY_REPORT.md", gitignore_text)
            self.assertIn("logs/", gitignore_text)

            # 2. 再度実行しても既存ファイルを破壊・上書きしないこと (Idempotent)
            (target_project / "PLAN.md").write_text("# Custom Plan\n", encoding="utf-8")
            proc2 = subprocess.run(
                [sys.executable, str(script_path), "--work-dir", str(target_project), "init"],
                cwd=target_project,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc2.returncode, 0)
            self.assertEqual((target_project / "PLAN.md").read_text(encoding="utf-8"), "# Custom Plan\n")

    def test_external_project_workflow(self):
        """外部プロジェクトにおける auto-dev init -> git commit -> auto-dev run -> brief の全フロー検証。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            ext_app = Path(tmp_dir) / "external_project"
            ext_app.mkdir()

            script_path = Path(__file__).parents[1] / "scripts" / "nightly_orchestrator.py"

            # 1. 外部アプリで init
            subprocess.run([sys.executable, str(script_path), "init"], cwd=ext_app, check=True)

            # 2. Git 初期化と初期コミット
            subprocess.run(["git", "init"], cwd=ext_app, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=ext_app, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=ext_app, check=True)
            subprocess.run(["git", "add", "-A"], cwd=ext_app, check=True)
            subprocess.run(["git", "commit", "-m", "init external app"], cwd=ext_app, check=True)

            # 3. PLAN.md を作成
            (ext_app / "PLAN.md").write_text("- [ ] 1. 新規モジュール作成\n", encoding="utf-8")
            subprocess.run(["git", "add", "PLAN.md"], cwd=ext_app, check=True)
            subprocess.run(["git", "commit", "-m", "add plan"], cwd=ext_app, check=True)

            # 4. 外部アプリ内で auto-dev run を実行（外部Makerコマンドを指定）
            python_bin = sys.executable
            maker_code = f'{python_bin} -c "open(\'module.py\', \'w\').write(\'def ok(): return True\\n\')"'
            checker_code = 'echo "APPROVE: Looks good"'

            proc_run = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "run",
                    "--maker-cmd",
                    maker_code,
                    "--checker-cmd",
                    checker_code,
                ],
                cwd=ext_app,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_run.returncode, 0, f"auto-dev run failed:\nSTDOUT:\n{proc_run.stdout}\nSTDERR:\n{proc_run.stderr}")
            self.assertIn("Execution complete", proc_run.stdout)

            # 5. 外部アプリの Git 履歴とファイルを検証
            log_proc = subprocess.run(["git", "log", "-1", "--oneline"], cwd=ext_app, capture_output=True, text=True, check=True)
            self.assertIn("feat: 新規モジュール作成", log_proc.stdout)
            self.assertTrue((ext_app / "module.py").exists())
            self.assertIn("- [x] 1. 新規モジュール作成", (ext_app / "PLAN.md").read_text(encoding="utf-8"))

            # 6. auto-dev brief で外部アプリのブリーフィングが確認できること
            proc_brief = subprocess.run([sys.executable, str(script_path), "brief"], cwd=ext_app, capture_output=True, text=True, check=False)
            self.assertEqual(proc_brief.returncode, 0)
            self.assertIn("AUTO DEVELOPER / MORNING BRIEF", proc_brief.stdout)
            self.assertIn("新規モジュール作成", proc_brief.stdout)

    def test_feature_h_default_verify_sh_fails_on_broken_test(self):
        """機能H-1: init が生成する scripts/verify_local.sh はテスト失敗を握りつぶさず非ゼロで終了する。"""
        import tempfile
        from auto_developer.cli import DEFAULT_VERIFY_SH
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            (repo / "SPEC.md").write_text("spec", encoding="utf-8")
            (repo / "scripts").mkdir(parents=True, exist_ok=True)
            verify_sh = repo / "scripts" / "verify_local.sh"
            verify_sh.write_text(DEFAULT_VERIFY_SH, encoding="utf-8")
            verify_sh.chmod(0o755)

            # 失敗するテストを配置
            tests_dir = repo / "tests"
            tests_dir.mkdir(parents=True, exist_ok=True)
            (tests_dir / "test_failing.py").write_text(
                "import unittest\nclass FailTest(unittest.TestCase):\n    def test_fail(self):\n        self.fail('Intentionally failing')\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init with failing test"], cwd=repo, check=True)

            res = subprocess.run(["bash", str(verify_sh)], cwd=repo, capture_output=True, text=True, check=False)
            self.assertNotEqual(res.returncode, 0, f"verify_local.sh must fail on broken tests! Output:\n{res.stdout}\n{res.stderr}")
            test_section = DEFAULT_VERIFY_SH.split("Running Project Tests")[1]
            self.assertNotIn("|| true", test_section)

    def test_feature_h_runner_fails_closed_when_unconfigured(self):
        """機能H-2: 本番モード (--mock 未指定) で Maker/Checker コマンドが未指定なら起動拒否 (Exit 2)。"""
        import tempfile
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "nightly_orchestrator.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            (repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("- [ ] 1. タスク\n", encoding="utf-8")
            (repo / "SPEC.md").write_text("spec\n", encoding="utf-8")
            (repo / "scripts").mkdir(parents=True, exist_ok=True)
            v_sh = repo / "scripts" / "verify_local.sh"
            v_sh.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            v_sh.chmod(0o755)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            # run without --maker-cmd, --checker-cmd, or --mock
            proc = subprocess.run(
                [sys.executable, str(script_path), "run"],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc.returncode, 2, f"Expected Exit 2 when runner unconfigured, got {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")
            self.assertIn("Maker and Checker commands must be configured", proc.stderr)

    def test_feature_h_maker_no_diff_fails_and_quarantines(self):
        """機能H-3: Maker が成功終了しても作業ツリーに差分が一切ない場合、タスク未達成として隔離・停止する。"""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            (repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("- [ ] 1. 何も変更しないタスク\n", encoding="utf-8")
            (repo / "SPEC.md").write_text("spec\n", encoding="utf-8")
            (repo / "scripts").mkdir(parents=True, exist_ok=True)
            v_sh = repo / "scripts" / "verify_local.sh"
            v_sh.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            v_sh.chmod(0o755)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            # Mock runner that returns exit 0 but produces NO file changes
            mock_runner = MockRunnerAdapter(maker_exit_code=0, maker_output="I did nothing")
            orchestrator = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=v_sh,
                report_path=repo / "NIGHTLY_REPORT.md",
                state_path=repo / ".dev" / "job_state.json",
                runner_adapter=mock_runner,
                dry_run=False,
            )
            exit_code = orchestrator.run()
            self.assertEqual(exit_code, 1, "Must exit with code 1 when Maker produces no diff")
            # PLAN.md should mark task as failed
            plan_text = (repo / "PLAN.md").read_text(encoding="utf-8")
            self.assertIn("[FAILED]", plan_text)

    def test_feature_i_evidence_binding_detects_tampered_tree_after_verify(self):
        """機能I: verify-step 通過後に作業ツリーが改ざんされた場合、commit-step はコミットを拒否 (Exit 3) する。"""
        import tempfile
        script_path = Path(__file__).resolve().parents[1] / "scripts" / "nightly_orchestrator.py"
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
            (repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("- [ ] 1. タスク\n", encoding="utf-8")
            (repo / "SPEC.md").write_text("spec\n", encoding="utf-8")
            (repo / "scripts").mkdir(parents=True, exist_ok=True)
            v_sh = repo / "scripts" / "verify_local.sh"
            v_sh.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
            v_sh.chmod(0o755)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # 正常な変更を加えて verify-step を実行
            (repo / "valid_feature.py").write_text("print('valid')", encoding="utf-8")
            proc_verify = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "verify-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    checkpoint,
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_verify.returncode, 0, f"verify-step should pass:\n{proc_verify.stdout}\n{proc_verify.stderr}")
            self.assertIn("VERIFICATION PASSED", proc_verify.stdout)

            # 検証通過後に、未検証の改ざんコードを混入させる！
            (repo / "tampered_payload.py").write_text("print('malicious backdoor')", encoding="utf-8")

            # commit-step を実行
            proc_commit = subprocess.run(
                [
                    sys.executable,
                    str(script_path),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    checkpoint,
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
            )
            # 現在の実装ではツリー未拘束のため Exit 0 でコミットされてしまうはず (脆弱性)
            self.assertEqual(
                proc_commit.returncode,
                3,
                f"Expected Exit 3 (Evidence tree mismatch/tampering), but got {proc_commit.returncode}:\nSTDOUT:\n{proc_commit.stdout}\nSTDERR:\n{proc_commit.stderr}",
            )
            self.assertIn("EVIDENCE ERROR", proc_commit.stderr)


if __name__ == "__main__":
    unittest.main()
