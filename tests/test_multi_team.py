from __future__ import annotations

import subprocess
import tempfile
import unittest
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


if __name__ == "__main__":
    unittest.main()
