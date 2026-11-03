"""Acceptance test suite for Antigravity Autonomous Pipeline Orchestrator (SPEC.md)."""

from pathlib import Path
import subprocess
import unittest

# Target classes to be implemented in scripts/nightly_orchestrator.py
from scripts.nightly_orchestrator import (
    Task,
    TaskDispatcher,
    GitGuard,
    DeterministicVerifier,
    MorningReporter,
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
            task1 = [t for t in updated_tasks if t.task_id == "1"][0]
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
        import tempfile
        import sys
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
            )
            self.assertNotEqual(proc_commit_replay.returncode, 0)

    def test_feature_f_morning_report_generation(self):
        """機能F: NIGHTLY_REPORT.md が所定のスキーマで生成される。"""
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
                    "phase": "VERIFIER_ERROR",
                    "log_path": "logs/nightly/task_2_failure.log",
                    "root_cause_summary": "AssertionError: expected 200 but got 500",
                },
            }

            reporter.write_report(report_file, summary_data)

            self.assertTrue(report_file.exists())
            content = report_file.read_text(encoding="utf-8")
            self.assertIn("# Nightly Execution Report", content)
            self.assertIn("* **Status**: STOPPED (FAIL-FAST)", content)
            self.assertIn("| task-1 | 基盤設定 | `def5678` |", content)
            self.assertIn("* **Failed Task ID**: task-2", content)
            self.assertIn("logs/nightly/task_2_failure.log", content)
            self.assertIn("AssertionError: expected 200 but got 500", content)


if __name__ == "__main__":
    unittest.main()
