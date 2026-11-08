from __future__ import annotations

import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path

from auto_developer.multi_team import (
    MultiTeamConfig,
    MultiTeamOrchestrator,
    TeamExecutionResult,
    TeamWorkerSpec,
)


class TestMultiTeamOrchestration(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        self._init_git_repo()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def _init_git_repo(self) -> None:
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "MultiTeam Test"])
        self._run_git(["config", "user.email", "multiteam@example.com"])
        readme = self.repo_dir / "README.md"
        readme.write_text("MultiTeam Repository\n", encoding="utf-8")
        self._run_git(["add", "README.md"])
        self._run_git(["commit", "-m", "init: base commit"])
        res = self._run_git(["rev-parse", "HEAD"])
        self.base_commit = res.stdout.strip()

    def test_multi_team_data_structures_validation(self) -> None:
        """GG.1: TeamWorkerSpec, MultiTeamConfig, MultiTeamResult の型安全とバリデーション。"""
        # 不正な team_id の拒絶
        with self.assertRaises(ValueError):
            TeamWorkerSpec(team_id="../evil_team")

        spec_a = TeamWorkerSpec(team_id="team-alpha", assigned_tasks=["task-1"])
        self.assertEqual(spec_a.team_id, "team-alpha")

        config = MultiTeamConfig(
            job_id="job-multi-1",
            teams=[spec_a],
            base_branch="main",
        )
        self.assertEqual(config.job_id, "job-multi-1")
        self.assertEqual(len(config.teams), 1)

    def test_prepare_workers_allocates_isolated_worktrees(self) -> None:
        """GG.2: 複数ワーカー用の専用 Worktree を分離作成し管理できることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-multi-wt",
            teams=[
                TeamWorkerSpec(team_id="team-frontend"),
                TeamWorkerSpec(team_id="team-backend"),
            ],
            base_branch="main",
        )

        wt_paths = orchestrator.prepare_workers(config)
        self.assertEqual(len(wt_paths), 2)
        self.assertTrue(wt_paths["team-frontend"].exists())
        self.assertTrue(wt_paths["team-backend"].exists())
        self.assertNotEqual(wt_paths["team-frontend"], wt_paths["team-backend"])

        # 後片付け
        orchestrator.cleanup_workers(config)
        self.assertFalse(wt_paths["team-frontend"].exists())
        self.assertFalse(wt_paths["team-backend"].exists())

    def test_collect_team_inputs_handles_worker_failure(self) -> None:
        """GG.2: いずれかのチームワーカーが失敗した際、安全に WORKER_FAILED となることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-multi-fail",
            teams=[
                TeamWorkerSpec(team_id="team-a"),
                TeamWorkerSpec(team_id="team-b"),
            ],
        )

        results = {
            "team-a": TeamExecutionResult(team_id="team-a", success=True, status="SUCCESS", bundle_path=Path("/tmp/a.zip")),
            "team-b": TeamExecutionResult(team_id="team-b", success=False, status="FAILED", details={"error": "Test failure"}),
        }

        res = orchestrator.integrate_team_results(config, results)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "WORKER_FAILED")
        self.assertIn("team-b", res.details.get("failed_teams", []))

    def test_multi_team_config_rejects_duplicate_team_ids(self) -> None:
        """HH.2 / P1-2: MultiTeamConfig は重複する team_id を拒絶することの実証。"""
        with self.assertRaises(ValueError):
            MultiTeamConfig(
                job_id="job-dup",
                teams=[
                    TeamWorkerSpec(team_id="team-a"),
                    TeamWorkerSpec(team_id="team-a"),
                ],
            )

    def test_cleanup_workers_reports_true_when_worktrees_removed(self) -> None:
        """HH.1 / P1-1: cleanup_workers は Worktree が正常に解放された場合に True を報告することの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-clean-test",
            teams=[
                TeamWorkerSpec(team_id="team-x"),
                TeamWorkerSpec(team_id="team-y"),
            ],
            base_branch="main",
        )
        orchestrator.prepare_workers(config)
        statuses = orchestrator.cleanup_workers(config)
        self.assertEqual(statuses, {"team-x": True, "team-y": True})

    def test_prepare_workers_atomic_rollback_on_failure(self) -> None:
        """HH.2 / P1-2: prepare_workers の途中で失敗した場合、作成済みの Worktree が自動ロールバックされることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-atomic-prep",
            teams=[
                TeamWorkerSpec(team_id="team-good"),
                TeamWorkerSpec(team_id="team-bad"),
            ],
            base_branch="main",
        )

        orig_create = orchestrator.worktree_manager.create_worktree

        def mock_create(team_id: str, *args: object, **kwargs: object) -> Path:
            if team_id == "team-bad":
                raise RuntimeError("Injected worktree failure for team-bad")
            return orig_create(team_id, *args, **kwargs)  # type: ignore[arg-type]

        with (
            unittest.mock.patch.object(orchestrator.worktree_manager, "create_worktree", side_effect=mock_create),
            self.assertRaises(RuntimeError),
        ):
            orchestrator.prepare_workers(config)

        # 最初の team-good の Worktree もロールバックされて残存しないことの実証
        good_dir = orchestrator.worktree_manager.worktree_base / "team-good"
        self.assertFalse(good_dir.exists())

    def test_run_workers_executes_in_parallel_and_collects_results(self) -> None:
        """II.1, II.2: run_workers が各チームを並行実行し、結果と成果物バンドルを収集できることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-parallel-test",
            teams=[
                TeamWorkerSpec(team_id="team-alpha"),
                TeamWorkerSpec(team_id="team-beta"),
            ],
            base_branch="main",
        )
        orchestrator.prepare_workers(config)

        def mock_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
            f = wt_path / f"{team_id}.txt"
            f.write_text(f"work done by {team_id}\n", encoding="utf-8")
            subprocess.run(["git", "add", f"{team_id}.txt"], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", f"feat: {team_id} work"], cwd=wt_path, check=True)
            rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
            bundle_p = wt_path / f"bundle_{team_id}.zip"
            bundle_p.write_text("dummy bundle", encoding="utf-8")
            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                head_commit=rev,
                bundle_path=bundle_p,
            )

        results = orchestrator.run_workers(config, worker_fn=mock_worker)
        self.assertEqual(len(results), 2)
        self.assertTrue(results["team-alpha"].success)
        self.assertTrue(results["team-beta"].success)
        self.assertIsNotNone(results["team-alpha"].head_commit)
        self.assertIsNotNone(results["team-beta"].head_commit)

        orchestrator.cleanup_workers(config)

    def test_run_workers_handles_worker_crash_or_failure(self) -> None:
        """II.1, II.2: いずれかのワーカーが例外や失敗を起こした際、安全にエラー結果を記録することの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-crash-test",
            teams=[
                TeamWorkerSpec(team_id="team-ok"),
                TeamWorkerSpec(team_id="team-fail"),
            ],
            base_branch="main",
        )
        orchestrator.prepare_workers(config)

        def mock_failing_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
            if team_id == "team-fail":
                raise RuntimeError("Worker process crashed unexpectedly")
            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                bundle_path=wt_path / "bundle.zip",
            )

        results = orchestrator.run_workers(config, worker_fn=mock_failing_worker)
        self.assertTrue(results["team-ok"].success)
        self.assertFalse(results["team-fail"].success)
        self.assertEqual(results["team-fail"].status, "CRASHED")

        orchestrator.cleanup_workers(config)


if __name__ == "__main__":
    unittest.main()
