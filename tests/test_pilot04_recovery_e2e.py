"""Pilot 04: 実際の障害注入データ (4つのSIGKILLクラッシュ) に対する E2E 復旧実証テスト.

ChatGPT 6.1-sol 氏のレビュー推奨に基づき、実物のクラッシュリポジトリ
(scenario_maker, scenario_after_verify, scenario_after_commit, scenario_during_quarantine)
に対して `recover inspect` -> `plan` -> `apply` を実行し、復旧の正しさと冪等性を検証する。
"""

import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from auto_developer.models import JobState
from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

ZIP_PATH = Path("/Users/hamatsumakoto/.gemini/antigravity/brain/d5a0cd95-dc79-481e-828c-48e50566f41a/.user_uploaded/media_1793812425094_fb637520.zip")


@unittest.skipUnless(ZIP_PATH.exists(), "Pilot 04 result zip file is not available in local test environment")
class TestPilot04RecoveryE2E(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.work_root = Path(self.tmpdir.name)
        # ZIP を Unix パーミッション (executable bit) を復元して展開
        with zipfile.ZipFile(ZIP_PATH, "r") as zf:
            for info in zf.infolist():
                extracted_path = zf.extract(info, self.work_root)
                perm = (info.external_attr >> 16) & 0o777
                if perm:
                    Path(extracted_path).chmod(perm)
        self.results_dir = self.work_root / "pilot04_results"

    def tearDown(self):
        self.tmpdir.cleanup()

    def _get_head(self, repo: Path) -> str:
        proc = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    def _get_branch(self, repo: Path) -> str:
        proc = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True, text=True, check=True)
        return proc.stdout.strip()

    def _is_tree_clean(self, repo: Path) -> bool:
        proc = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True)
        return not bool(proc.stdout.strip())

    def test_e2e_scenario_maker_crashed_in_flight(self):
        """実証1: Maker実行中SIGKILLクラッシュからの復旧と冪等性."""
        repo = self.results_dir / "scenario_maker"
        state_file = repo / ".dev" / "job_state.json"
        self.assertTrue(repo.exists() and state_file.exists())

        initial_head = self._get_head(repo)
        engine = RecoveryEngine(repo_dir=repo, state_path=state_file)

        # 1. Inspect
        report = engine.inspect()
        self.assertEqual(report.diagnosis, RecoveryDiagnosis.CRASHED_IN_FLIGHT)
        self.assertTrue(report.can_auto_recover)

        # 2. Plan
        plan = engine.plan()
        self.assertEqual(plan.diagnosis, RecoveryDiagnosis.CRASHED_IN_FLIGHT)
        self.assertTrue(len(plan.actions) > 0)

        # 3. Apply (1回目)
        result1 = engine.apply()
        self.assertTrue(result1.success, f"Apply failed: {result1.message}")
        self.assertEqual(result1.resulting_state, "RESET_FOR_RETRY")

        # 適用後検証:
        # - HEAD は checkpoint のまま前進・後退していないこと
        self.assertEqual(self._get_head(repo), initial_head)
        # - 作業ツリーはクリーンに復元されていること
        self.assertTrue(self._is_tree_clean(repo))
        # - 未コミットの仕掛かり成果がスナップショットおよび隔離ブランチへ退避されていること
        quarantines = list((repo / ".dev" / "quarantines").glob("snapshot_task_*.tar.gz"))
        self.assertTrue(len(quarantines) > 0, "WIP work must be safely preserved in quarantine snapshot")
        # - JobState が INIT / RUNNING に安全リセットされていること
        state = JobState.load(state_file)
        self.assertEqual(state.status, "RUNNING")
        self.assertEqual(state.current_phase, "INIT")

        # 4. Apply (2回目: 冪等性検証)
        result2 = engine.apply()
        self.assertTrue(result2.success)
        self.assertEqual(self._get_head(repo), initial_head)
        self.assertTrue(self._is_tree_clean(repo))

    def test_e2e_scenario_after_verify_crashed_in_flight(self):
        """実証2: 検証成功直後・コミット前SIGKILLクラッシュからの復旧と冪等性."""
        repo = self.results_dir / "scenario_after_verify"
        state_file = repo / ".dev" / "job_state.json"
        self.assertTrue(repo.exists() and state_file.exists())

        initial_head = self._get_head(repo)
        engine = RecoveryEngine(repo_dir=repo, state_path=state_file)

        # 1. Inspect
        report = engine.inspect()
        self.assertEqual(report.diagnosis, RecoveryDiagnosis.CRASHED_IN_FLIGHT)
        self.assertTrue(report.can_auto_recover)

        # 2. Plan
        plan = engine.plan()
        self.assertEqual(plan.diagnosis, RecoveryDiagnosis.CRASHED_IN_FLIGHT)

        # 3. Apply (1回目)
        result1 = engine.apply()
        self.assertTrue(result1.success, f"Apply failed: {result1.message}")

        # 適用後検証:
        self.assertEqual(self._get_head(repo), initial_head)
        self.assertTrue(self._is_tree_clean(repo))
        quarantines = list((repo / ".dev" / "quarantines").glob("snapshot_task_*.tar.gz"))
        self.assertTrue(len(quarantines) > 0)

        # 4. Apply (2回目: 冪等性)
        result2 = engine.apply()
        self.assertTrue(result2.success)
        self.assertEqual(self._get_head(repo), initial_head)

    def test_e2e_scenario_after_commit_committed_ahead_of_state(self):
        """実証3: Gitコミット直後SIGKILLクラッシュからのコミット再利用・復旧と冪等性."""
        repo = self.results_dir / "scenario_after_commit"
        state_file = repo / ".dev" / "job_state.json"
        self.assertTrue(repo.exists() and state_file.exists())

        c273804_head = self._get_head(repo)
        engine = RecoveryEngine(repo_dir=repo, state_path=state_file)

        # 1. Inspect: HEAD が checkpoint より先行しており、Evidence と Tree SHA が一致
        report = engine.inspect()
        self.assertEqual(report.diagnosis, RecoveryDiagnosis.COMMITTED_AHEAD_OF_STATE)
        self.assertTrue(report.can_auto_recover)
        self.assertEqual(report.head_commit, c273804_head)

        # 2. Plan
        plan = engine.plan()
        self.assertEqual(plan.diagnosis, RecoveryDiagnosis.COMMITTED_AHEAD_OF_STATE)
        self.assertTrue(any(a.action_type == "ADVANCE_JOB_STATE" for a in plan.actions))

        # 3. Apply (1回目)
        result1 = engine.apply()
        self.assertTrue(result1.success, f"Apply failed: {result1.message}")
        self.assertEqual(result1.resulting_state, "COMPLETED")

        # 適用後検証:
        # - コミットが巻き戻されず、二重コミットも作られず、HEAD が同一であること
        self.assertEqual(self._get_head(repo), c273804_head)
        # - 作業ツリーが完全にクリーンであること
        self.assertTrue(self._is_tree_clean(repo))
        # - JobState が COMPLETED となり、コミットが completed_tasks に正しく記録されていること
        state = JobState.load(state_file)
        self.assertEqual(state.status, "COMPLETED")
        self.assertEqual(len(state.completed_tasks), 1)
        self.assertEqual(state.completed_tasks[0]["commit"], c273804_head)
        # - Evidence Record が COMPLETED に更新されていること
        evidence_file = repo / ".git" / "nightly_evidence" / "records" / "task_1.json"
        ev_data = json.loads(evidence_file.read_text(encoding="utf-8"))
        self.assertEqual(ev_data.get("status"), "COMPLETED")

        # 4. Apply (2回目: 冪等性)
        post_report = engine.inspect()
        self.assertEqual(post_report.diagnosis, RecoveryDiagnosis.HEALTHY)
        result2 = engine.apply()
        self.assertTrue(result2.success)
        self.assertEqual(result2.resulting_state, "UNCHANGED")
        self.assertEqual(self._get_head(repo), c273804_head)

    def test_e2e_scenario_during_quarantine_incomplete(self):
        """実証4: Quarantine処理途中SIGKILLクラッシュからの隔離完了・本線復帰と冪等性."""
        repo = self.results_dir / "scenario_during_quarantine"
        state_file = repo / ".dev" / "job_state.json"
        self.assertTrue(repo.exists() and state_file.exists())

        engine = RecoveryEngine(repo_dir=repo, state_path=state_file)

        # クラッシュ直後は隔離ブランチにいること
        initial_branch = self._get_branch(repo)
        self.assertTrue(initial_branch.startswith("quarantine/"))

        # 1. Inspect
        report = engine.inspect()
        self.assertEqual(report.diagnosis, RecoveryDiagnosis.QUARANTINE_INCOMPLETE)
        self.assertTrue(report.can_auto_recover)

        # 2. Plan
        plan = engine.plan()
        self.assertEqual(plan.diagnosis, RecoveryDiagnosis.QUARANTINE_INCOMPLETE)

        # 3. Apply (1回目)
        result1 = engine.apply()
        self.assertTrue(result1.success, f"Apply failed: {result1.message}")
        self.assertEqual(result1.resulting_state, "FAILED")

        # 適用後検証:
        # - カレントブランチが main に復帰していること
        self.assertEqual(self._get_branch(repo), "main")
        # - 本線作業ツリーがクリーンであること
        self.assertTrue(self._is_tree_clean(repo))
        # - JobState が FAILED となり、quarantined_task が設定されていること
        state = JobState.load(state_file)
        self.assertEqual(state.status, "FAILED")
        self.assertIsNotNone(state.quarantined_task)
        self.assertEqual(state.quarantined_task.get("quarantined_branch"), initial_branch)
        # - 隔離ブランチが存在し、tar スナップショットと Manifest が確実に保全されていること
        branches_out = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True, check=True).stdout
        self.assertIn(initial_branch, branches_out)
        snapshots = list((repo / ".dev" / "quarantines").glob("snapshot_task_*.tar.gz"))
        self.assertTrue(len(snapshots) > 0)

        # 4. Apply (2回目: 冪等性)
        post_report = engine.inspect()
        self.assertEqual(post_report.diagnosis, RecoveryDiagnosis.HEALTHY)
        result2 = engine.apply()
        self.assertTrue(result2.success)
        self.assertEqual(result2.resulting_state, "UNCHANGED")
        self.assertEqual(self._get_branch(repo), "main")

    def test_e2e_cli_commands_across_all_four_scenarios(self):
        """実証5: ChatGPT 6.1-sol 氏推奨の CLI コマンド (inspect --json -> plan --json -> apply) を4シナリオすべてで実行."""
        from auto_developer.cli import main

        scenarios = [
            ("scenario_maker", "CRASHED_IN_FLIGHT"),
            ("scenario_after_verify", "CRASHED_IN_FLIGHT"),
            ("scenario_after_commit", "COMMITTED_AHEAD_OF_STATE"),
            ("scenario_during_quarantine", "QUARANTINE_INCOMPLETE"),
        ]

        for s_dir, expected_diag in scenarios:
            repo = self.results_dir / s_dir
            # 1. auto-dev recover inspect --json (Exit 1: 不整合検出)
            exit_code = main(["--work-dir", str(repo), "recover", "inspect", "--json"])
            self.assertEqual(exit_code, 1, f"Inspect should exit 1 for {s_dir}")

            # 2. auto-dev recover plan --json (Exit 0)
            exit_code = main(["--work-dir", str(repo), "recover", "plan", "--json"])
            self.assertEqual(exit_code, 0, f"Plan should exit 0 for {s_dir}")

            # 3. auto-dev recover apply (Exit 0)
            exit_code = main(["--work-dir", str(repo), "recover", "apply"])
            self.assertEqual(exit_code, 0, f"Apply should exit 0 for {s_dir}")

            # 4. apply 後の再 inspect は HEALTHY (Exit 0)
            # (ただし CRASHED_IN_FLIGHT は仕掛品を退避して初期化されたため再試行待ち)
            engine = RecoveryEngine(repo_dir=repo, state_path=repo / ".dev" / "job_state.json")
            report = engine.inspect()
            self.assertIn(report.diagnosis, (RecoveryDiagnosis.HEALTHY, RecoveryDiagnosis.CRASHED_IN_FLIGHT))

            # 5. apply 2回目 (完全な冪等性: Exit 0)
            exit_code = main(["--work-dir", str(repo), "recover", "apply"])
            self.assertEqual(exit_code, 0, f"Second apply should exit 0 for {s_dir}")


if __name__ == "__main__":
    unittest.main()
