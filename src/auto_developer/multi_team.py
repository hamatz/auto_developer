from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_developer.integration_gate import IntegrationGate, IntegrationResult, TeamIntegrationInput
from auto_developer.worktree import WorktreeError, WorktreeManager

logger = logging.getLogger(__name__)

VALID_TEAM_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]+$")


@dataclass
class TeamWorkerSpec:
    """単一チームワーカーの実行仕様。"""

    team_id: str
    assigned_tasks: list[str] = field(default_factory=list)
    worktree_name: str | None = None
    base_branch: str = "main"

    def __post_init__(self) -> None:
        if not VALID_TEAM_ID_PATTERN.match(self.team_id):
            raise ValueError(f"Invalid team_id '{self.team_id}': must match '^[a-zA-Z0-9_-]+$'")


@dataclass
class MultiTeamConfig:
    """複数チーム並行実行・統合の全体設定。"""

    job_id: str
    teams: list[TeamWorkerSpec]
    base_branch: str = "main"
    integration_verify_script: Path | None = None
    worktree_base: Path | None = None

    def __post_init__(self) -> None:
        if not VALID_TEAM_ID_PATTERN.match(self.job_id):
            raise ValueError(f"Invalid job_id '{self.job_id}': must match '^[a-zA-Z0-9_-]+$'")


@dataclass
class TeamExecutionResult:
    """単一チームワーカーの実行完了結果。"""

    team_id: str
    success: bool
    status: str
    branch: str = "main"
    head_commit: str | None = None
    bundle_path: Path | None = None
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class MultiTeamResult:
    """複数チーム並行実行および統合ゲートの全体判定結果。"""

    job_id: str
    success: bool
    status: str
    team_results: dict[str, TeamExecutionResult] = field(default_factory=dict)
    integration_result: IntegrationResult | None = None
    release_candidate_commit: str | None = None
    release_candidate_tag: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


class MultiTeamOrchestrator:
    """Stage 3: 複数チームワーカーの独立 Worktree 分割・並行実行・成果統合を統括する。"""

    def __init__(
        self,
        repo_dir: Path,
        worktree_manager: WorktreeManager | None = None,
        integration_gate: IntegrationGate | None = None,
    ) -> None:
        self.repo_dir = repo_dir
        self.worktree_manager = worktree_manager or WorktreeManager(repo_dir=repo_dir)
        self.integration_gate = integration_gate or IntegrationGate(
            repo_dir=repo_dir,
            worktree_manager=self.worktree_manager,
        )

    def prepare_workers(self, config: MultiTeamConfig) -> dict[str, Path]:
        """各チーム用の独立した専用 Worktree を作成し、パスの辞書を返す。"""
        paths: dict[str, Path] = {}
        for team in config.teams:
            branch_name = f"team/{team.team_id}"
            wt_path = self.worktree_manager.create_worktree(
                team_id=team.team_id,
                base_branch=team.base_branch or config.base_branch,
                branch_name=branch_name,
            )
            paths[team.team_id] = wt_path
        return paths

    def cleanup_workers(self, config: MultiTeamConfig, force: bool = True) -> dict[str, bool]:
        """作成された全チームの Worktree を解放する。"""
        cleanup_statuses: dict[str, bool] = {}
        for team in config.teams:
            try:
                res = self.worktree_manager.remove_worktree(team.team_id, force=force)
                cleanup_statuses[team.team_id] = res
            except (OSError, subprocess.CalledProcessError, WorktreeError, RuntimeError) as err:
                logger.warning("Failed to remove worktree for team '%s': %s", team.team_id, err)
                cleanup_statuses[team.team_id] = False
        return cleanup_statuses

    def integrate_team_results(
        self,
        config: MultiTeamConfig,
        team_results: dict[str, TeamExecutionResult],
        verify_script: Path | None = None,
        resume: bool = False,
    ) -> MultiTeamResult:
        """全ワーカーの実行結果を収集・照合し、IntegrationGate へ安全に引き渡す。"""
        failed_teams: list[str] = []
        inputs: list[TeamIntegrationInput] = []

        for team in config.teams:
            res = team_results.get(team.team_id)
            if res is None or not res.success or res.bundle_path is None:
                failed_teams.append(team.team_id)
            else:
                inputs.append(
                    TeamIntegrationInput(
                        team_id=res.team_id,
                        bundle_path=res.bundle_path,
                        branch=res.branch or f"team/{res.team_id}",
                        head_commit=res.head_commit,
                    )
                )

        if failed_teams:
            return MultiTeamResult(
                job_id=config.job_id,
                success=False,
                status="WORKER_FAILED",
                team_results=team_results,
                details={
                    "error": f"One or more team workers failed: {failed_teams}",
                    "failed_teams": failed_teams,
                },
            )

        # IntegrationGate による統合実行
        int_result = self.integration_gate.execute_integration(
            job_id=config.job_id,
            inputs=inputs,
            base_commit=config.base_branch,
            verify_script=verify_script or config.integration_verify_script,
            resume=resume,
        )

        rc_tag = str(int_result.details.get("rc_tag")) if int_result.details.get("rc_tag") else None
        return MultiTeamResult(
            job_id=config.job_id,
            success=int_result.success,
            status=int_result.status,
            team_results=team_results,
            integration_result=int_result,
            release_candidate_commit=int_result.release_candidate_commit,
            release_candidate_tag=rc_tag,
            details=int_result.details,
        )
