from __future__ import annotations

import concurrent.futures
import hashlib
import logging
import os
import re
import shutil
import subprocess
import uuid
from collections.abc import Callable
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
    extra_args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)

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
        seen_team_ids: set[str] = set()
        for team in self.teams:
            if team.team_id in seen_team_ids:
                raise ValueError(f"Duplicate team_id '{team.team_id}' found in MultiTeamConfig")
            seen_team_ids.add(team.team_id)


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
    evidence_preserved: bool = False


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
        """各チーム用の独立した専用 Worktree を作成し、パスの辞書を返す。

        HH.2 / P1-2: トランザクション的準備。作成途中で失敗した場合は、作成済みの Worktree を自動ロールバックする。
        """
        paths: dict[str, Path] = {}
        created_team_ids: list[str] = []
        try:
            for team in config.teams:
                branch_name = f"team/{team.team_id}"
                wt_path = self.worktree_manager.create_worktree(
                    team_id=team.team_id,
                    base_branch=team.base_branch or config.base_branch,
                    branch_name=branch_name,
                )
                created_team_ids.append(team.team_id)
                paths[team.team_id] = wt_path
            return paths
        except Exception as err:
            logger.warning("Preparation failed during worktree creation; rolling back created worktrees: %s", err)
            for tid in created_team_ids:
                try:
                    self.worktree_manager.remove_worktree(tid, force=True, delete_branch=True, purge_unmerged=True)
                except (OSError, subprocess.CalledProcessError, WorktreeError, RuntimeError) as rb_err:
                    logger.error("Failed to rollback worktree for team '%s': %s", tid, rb_err)
            raise

    def cleanup_workers(
        self,
        config: MultiTeamConfig,
        force: bool = True,
        team_results: dict[str, TeamExecutionResult] | None = None,
    ) -> dict[str, bool]:
        """作成された全チームの Worktree を解放する。

        HH.1 / P1-1: 削除成否判定を Worktree ディレクトリの消滅および Git 実態に基づいて判定する。
        NN.1 / P0: Evidence 退避失敗または未退避の成果物が残存する Worktree は削除せず原本保護する。
        """
        cleanup_statuses: dict[str, bool] = {}
        for team in config.teams:
            wt_path = self.worktree_manager.worktree_base / team.team_id

            if team_results is not None:
                res = team_results.get(team.team_id)
                if res is not None:
                    # 原本保護: ワーカー成果バンドルが存在していたにもかかわらず、
                    # 恒久保存先への Evidence 退避が未完了（または失敗）の場合は削除を抑止
                    has_bundle_in_worktree = (
                        res.status in ("BUNDLE_PRESERVATION_FAILED", "BUNDLE_CORRUPTED")
                        or (res.bundle_path is not None and not res.evidence_preserved)
                    )
                    if has_bundle_in_worktree and not res.evidence_preserved:
                        logger.warning(
                            "Preserving original worktree for team '%s' at '%s': evidence bundle was not successfully preserved (status=%s)",
                            team.team_id,
                            wt_path,
                            res.status,
                        )
                        cleanup_statuses[team.team_id] = False
                        continue

            try:
                self.worktree_manager.remove_worktree(team.team_id, force=force)
            except (OSError, subprocess.CalledProcessError, WorktreeError, RuntimeError) as err:
                logger.warning("Failed to remove worktree for team '%s': %s", team.team_id, err)

            # 実態照合による判定 (ディレクトリが存在せず、Git管理下のWorktree一覧からも消滅していること)
            still_exists = wt_path.exists() or any(
                wt.team_id == team.team_id for wt in self.worktree_manager.list_worktrees()
            )
            cleanup_statuses[team.team_id] = not still_exists
        return cleanup_statuses

    def run_workers(
        self,
        config: MultiTeamConfig,
        worker_cmd_factory: Callable[[str, Path], list[str]] | None = None,
        worker_fn: Callable[[str, Path], TeamExecutionResult] | None = None,
        timeout_sec: float = 120.0,
    ) -> dict[str, TeamExecutionResult]:
        """II.1, II.2: 各チームワーカーを並行実行し、結果・コミット・成果物バンドルを収集する。"""
        results: dict[str, TeamExecutionResult] = {}
        paths = {
            team.team_id: self.worktree_manager.worktree_base / team.team_id
            for team in config.teams
        }

        def execute_single_team(team: TeamWorkerSpec) -> TeamExecutionResult:
            wt_path = paths[team.team_id]
            if not wt_path.exists():
                return TeamExecutionResult(
                    team_id=team.team_id,
                    success=False,
                    status="WORKTREE_NOT_FOUND",
                    details={"error": f"Worktree not found at {wt_path}"},
                )

            if worker_fn is not None:
                try:
                    return worker_fn(team.team_id, wt_path)
                except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as err:
                    logger.error("Worker function for team '%s' crashed: %s", team.team_id, err)
                    return TeamExecutionResult(
                        team_id=team.team_id,
                        success=False,
                        status="CRASHED",
                        details={"error": str(err)},
                    )

            cmd = None
            if worker_cmd_factory is not None:
                cmd = worker_cmd_factory(team.team_id, wt_path)
            else:
                cmd = ["python3", "-m", "auto_developer.cli", "--work-dir", str(wt_path), "run", *team.extra_args]

            if not cmd:
                return TeamExecutionResult(
                    team_id=team.team_id,
                    success=False,
                    status="NO_COMMAND",
                    details={"error": "No command or worker function provided"},
                )

            try:
                merged_env = os.environ.copy()
                if team.env:
                    merged_env.update(team.env)
                proc = subprocess.run(
                    cmd,
                    cwd=wt_path,
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                    check=False,
                    env=merged_env,
                )
                head_rev = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=wt_path,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                head_commit = head_rev.stdout.strip() if head_rev.returncode == 0 else None

                bundle_path = None
                bundles_dir = wt_path / ".dev" / "bundles"
                if bundles_dir.exists():
                    zips = sorted(bundles_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if zips:
                        bundle_path = zips[0]

                success = proc.returncode == 0
                return TeamExecutionResult(
                    team_id=team.team_id,
                    success=success,
                    status="SUCCESS" if success else "FAILED",
                    branch=f"team/{team.team_id}",
                    head_commit=head_commit,
                    bundle_path=bundle_path,
                    details={
                        "exit_code": proc.returncode,
                        "stdout": proc.stdout[-500:] if proc.stdout else "",
                        "stderr": proc.stderr[-500:] if proc.stderr else "",
                    },
                )
            except subprocess.TimeoutExpired:
                logger.error("Team '%s' timed out after %s seconds", team.team_id, timeout_sec)
                return TeamExecutionResult(
                    team_id=team.team_id,
                    success=False,
                    status="TIMEOUT",
                    details={"error": f"Timed out after {timeout_sec}s"},
                )
            except (OSError, subprocess.CalledProcessError, RuntimeError) as err:
                logger.error("Unexpected error executing team '%s': %s", team.team_id, err)
                return TeamExecutionResult(
                    team_id=team.team_id,
                    success=False,
                    status="CRASHED",
                    details={"error": str(err)},
                )

        with concurrent.futures.ThreadPoolExecutor(max_workers=len(config.teams) or 1) as executor:
            future_to_team = {
                executor.submit(execute_single_team, team): team.team_id
                for team in config.teams
            }
            for future in concurrent.futures.as_completed(future_to_team):
                tid = future_to_team[future]
                try:
                    res = future.result()
                    results[tid] = res
                except (OSError, subprocess.CalledProcessError, RuntimeError, ValueError) as err:
                    results[tid] = TeamExecutionResult(
                        team_id=tid,
                        success=False,
                        status="CRASHED",
                        details={"error": str(err)},
                    )

        return results

    def execute_multi_team_pipeline(
        self,
        config: MultiTeamConfig,
        worker_cmd_factory: Callable[[str, Path], list[str]] | None = None,
        worker_fn: Callable[[str, Path], TeamExecutionResult] | None = None,
        verify_script: Path | None = None,
        timeout_sec: float = 120.0,
        cleanup: bool = True,
    ) -> MultiTeamResult:
        """複数チームの Worktree 準備・並行実行・成果収集・統合を包括実行する。"""
        team_results: dict[str, TeamExecutionResult] = {}
        try:
            self.prepare_workers(config)
            team_results = self.run_workers(
                config,
                worker_cmd_factory=worker_cmd_factory,
                worker_fn=worker_fn,
                timeout_sec=timeout_sec,
            )
            multi_result = self.integrate_team_results(
                config=config,
                team_results=team_results,
                verify_script=verify_script,
            )
            return multi_result
        finally:
            if cleanup:
                self.cleanup_workers(config, team_results=team_results)

    def preserve_team_bundles(
        self,
        config: MultiTeamConfig,
        team_results: dict[str, TeamExecutionResult],
    ) -> None:
        """LL.1, LL.2: Worktree 削除前に各チームの Evidence Bundle を恒久退避先に保全し、SHA-256 照合とパス更新を行う。"""
        dest_base = self.repo_dir / ".dev" / "integration_jobs" / config.job_id / "evidence"
        dest_base.mkdir(parents=True, exist_ok=True)

        for team in config.teams:
            res = team_results.get(team.team_id)
            if res is None or res.bundle_path is None or not res.bundle_path.exists():
                continue

            dest_path = dest_base / f"{res.team_id}.zip"
            tmp_path = dest_base / f"{res.team_id}.zip.tmp_{uuid.uuid4().hex[:8]}"
            try:
                orig_bytes = res.bundle_path.read_bytes()
                orig_sha = hashlib.sha256(orig_bytes).hexdigest()

                # NN.2: 保存先ファイルを直接上書きせず、同一ディレクトリの一時ファイルへコピー
                shutil.copy2(res.bundle_path, tmp_path)
                tmp_bytes = tmp_path.read_bytes()
                tmp_sha = hashlib.sha256(tmp_bytes).hexdigest()
                if orig_sha != tmp_sha:
                    if tmp_path.exists():
                        tmp_path.unlink()
                    logger.error(
                        "Preserved bundle SHA-256 mismatch for team '%s': original=%s, preserved=%s",
                        res.team_id,
                        orig_sha,
                        tmp_sha,
                    )
                    res.success = False
                    res.status = "BUNDLE_CORRUPTED"
                    res.details["bundle_error"] = "SHA-256 mismatch during bundle preservation"
                    continue

                # 原子的置換 (os.replace) により既存ファイルを壊さずに確定
                os.replace(tmp_path, dest_path)

                # パスを恒久保存先に更新 & 退避成功を記録
                res.bundle_path = dest_path
                res.evidence_preserved = True
            except (OSError, shutil.Error) as err:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
                logger.error("Failed to preserve bundle for team '%s': %s", res.team_id, err)
                res.success = False
                res.status = "BUNDLE_PRESERVATION_FAILED"
                res.details["bundle_error"] = str(err)

    def integrate_team_results(
        self,
        config: MultiTeamConfig,
        team_results: dict[str, TeamExecutionResult],
        verify_script: Path | None = None,
        resume: bool = False,
    ) -> MultiTeamResult:
        """全ワーカーの実行結果を収集・照合し、IntegrationGate へ安全に引き渡す。"""
        # LL.1, LL.2: 統合および Worktree 削除前に各チームの Evidence Bundle を退避保全
        self.preserve_team_bundles(config, team_results)

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
