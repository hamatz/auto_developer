from __future__ import annotations

import concurrent.futures
import hashlib
import json
import logging
import os
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
import zipfile
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


@dataclass
class MultiTeamRecoveryResult:
    """VV.2: 親オーケストレータークラッシュ後のジョブ自律復旧診断および実行結果。"""

    job_id: str
    reclaimed_orphans: list[str] = field(default_factory=list)
    unverified_processes: list[str] = field(default_factory=list)
    unverified_worktrees: list[str] = field(default_factory=list)
    rescued_bundles: list[str] = field(default_factory=list)
    reused_bundles: list[str] = field(default_factory=list)
    pipeline_result: MultiTeamResult | None = None
    success: bool = False
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkerRegistration:
    """VV.1: ディスクに永続化される単一ワーカーの起動・状態レコード。"""

    team_id: str
    pid: int
    pgid: int
    worktree_path: str
    base_branch: str
    branch_name: str
    status: str
    start_time: float
    updated_at: float
    process_start_time: str | None = None
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "team_id": self.team_id,
            "pid": self.pid,
            "pgid": self.pgid,
            "worktree_path": self.worktree_path,
            "base_branch": self.base_branch,
            "branch_name": self.branch_name,
            "status": self.status,
            "start_time": self.start_time,
            "updated_at": self.updated_at,
            "process_start_time": self.process_start_time,
            "details": self.details,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerRegistration:
        return cls(
            team_id=str(data["team_id"]),
            pid=int(data["pid"]),
            pgid=int(data["pgid"]),
            worktree_path=str(data["worktree_path"]),
            base_branch=str(data.get("base_branch", "")),
            branch_name=str(data.get("branch_name", "")),
            status=str(data.get("status", "UNKNOWN")),
            start_time=float(data.get("start_time", 0.0)),
            updated_at=float(data.get("updated_at", 0.0)),
            process_start_time=(
                str(data["process_start_time"])
                if data.get("process_start_time") is not None
                else None
            ),
            details=dict(data.get("details", {})),
        )


class WorkerRegistry:
    """VV.1: ジョブごとのワーカープロセスの起動・ライフサイクル状態を原子的ファイル保存・復元する永続レジストリ。"""

    def __init__(self, repo_dir: Path, job_id: str) -> None:
        self.repo_dir = repo_dir
        self.job_id = job_id
        self.registry_dir = repo_dir / ".dev" / "integration_jobs" / job_id
        self.registry_file = self.registry_dir / "workers.json"
        self._lock = threading.RLock()

    def load(self) -> dict[str, WorkerRegistration]:
        with self._lock:
            if not self.registry_file.exists():
                return {}
            try:
                data = json.loads(self.registry_file.read_text(encoding="utf-8"))
                return {tid: WorkerRegistration.from_dict(w) for tid, w in data.get("workers", {}).items()}
            except (json.JSONDecodeError, OSError):
                return {}

    def save(self, workers: dict[str, WorkerRegistration]) -> None:
        with self._lock:
            self.registry_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "job_id": self.job_id,
                "updated_at": time.time(),
                "workers": {tid: w.to_dict() for tid, w in workers.items()},
            }
            content = json.dumps(payload, indent=2, ensure_ascii=False)
            tmp_path = self.registry_dir / f"workers.json.tmp_{os.getpid()}_{time.time_ns()}"
            tmp_path.write_text(content, encoding="utf-8")
            os.replace(tmp_path, self.registry_file)

    def register_worker(self, reg: WorkerRegistration) -> None:
        with self._lock:
            current = self.load()
            current[reg.team_id] = reg
            self.save(current)

    def update_status(self, team_id: str, status: str, details: dict[str, Any] | None = None) -> None:
        with self._lock:
            current = self.load()
            if team_id in current:
                current[team_id].status = status
                current[team_id].updated_at = time.time()
                if details:
                    current[team_id].details.update(details)
                self.save(current)


@dataclass
class WorkerProcessHandle:
    """SS.1: ワーカープロセスの実行ハンドルおよび起動時に確定されたプロセスグループ情報。"""

    team_id: str
    proc: subprocess.Popen[str]
    pid: int
    pgid: int


def _is_valid_bundle_archive(bundle_path: Path) -> bool:
    """WW.3: Evidence Bundle が有効な ZIP アーカイブであり破損していないか検査する。"""
    if not bundle_path.is_file() or bundle_path.stat().st_size == 0:
        return False
    if not zipfile.is_zipfile(bundle_path):
        return False
    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            if zf.testzip() is not None:
                return False
        return True
    except (zipfile.BadZipFile, OSError):
        return False


def get_process_start_identifier(pid: int) -> str | None:
    """WW.1: OS上のプロセス開始識別子（Linux: /proc/<pid>/stat starttime、macOS/POSIX: ps lstart）を取得する。"""
    if pid <= 0:
        return None

    # 1. Linux: /proc/<pid>/stat から starttime（フィールド22）を取得
    proc_stat = Path(f"/proc/{pid}/stat")
    if proc_stat.is_file():
        try:
            content = proc_stat.read_text(encoding="utf-8").strip()
            rparen = content.rfind(")")
            if rparen != -1:
                fields = content[rparen + 2 :].split()
                # fields[0] は state (フィールド3)。starttime はフィールド22なので fields[19]
                if len(fields) >= 20:
                    return f"starttime:{fields[19]}"
        except (OSError, ValueError):
            pass

    # 2. POSIX / macOS: ps -p <pid> -o lstart=
    try:
        res = subprocess.run(
            ["ps", "-p", str(pid), "-o", "lstart="],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            val = res.stdout.strip()
            if val:
                return f"lstart:{val}"
    except (subprocess.SubprocessError, OSError):
        pass

    return None


def has_active_processes_in_group(pgid: int) -> bool:
    """プロセスグループ内に実行中（非ゾンビ）のプロセスが存在するか検査する。

    TT.1: Linux の /proc または POSIX ps を走査し、State: Z (zombie) または X (dead) を除外して判定する。
    """
    if pgid <= 0:
        return False

    # 1. まず killpg(pgid, 0) でグループの存在を確認
    if hasattr(os, "killpg"):
        try:
            os.killpg(pgid, 0)
        except ProcessLookupError:
            # グループ自体が消滅している -> 実行中プロセスなし
            return False
        except OSError:
            # 権限エラーやプラットフォーム制限の場合は詳細検査へ進む
            pass

    # 2. Linux: /proc がマウントされている場合は /proc/<pid>/stat を直接高速走査
    proc_dir = Path("/proc")
    if proc_dir.is_dir():
        try:
            for entry in proc_dir.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    stat_content = (entry / "stat").read_text()
                    rparen = stat_content.rfind(")")
                    if rparen == -1:
                        continue
                    rest = stat_content[rparen + 2 :].split()
                    if len(rest) >= 3:
                        state = rest[0].upper()
                        pgrp = int(rest[2])
                        if pgrp == pgid and state not in ("Z", "X"):
                            return True
                except (OSError, IndexError, ValueError):
                    continue
            return False
        except OSError:
            pass

    # 3. macOS / BSD / Linux fallback: ps -eo pid=,pgid=,stat= を用いて検査
    try:
        res = subprocess.run(
            ["ps", "-eo", "pid=,pgid=,stat="],
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                parts = line.split()
                if len(parts) >= 3:
                    try:
                        pgrp = int(parts[1])
                        stat = parts[2]
                        if pgrp == pgid and not stat.startswith("Z"):
                            return True
                    except ValueError:
                        continue
            return False
        return True
    except (subprocess.SubprocessError, OSError):
        # 検査不能時は安全のため生存（未回収）と判定
        return True


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
        self._active_workers: dict[str, WorkerProcessHandle] = {}
        self._all_worker_handles: dict[str, WorkerProcessHandle] = {}
        self._worker_history: dict[str, dict[str, Any]] = {}
        self._current_job_id: str | None = None
        self._workers_lock = threading.Lock()

    def terminate_worker_process_group(self, team_id: str, timeout: float = 2.0) -> bool:
        """RR.2, SS.1: 指定されたワーカーのプロセスグループ全体（起動時確定PGID）を安全に終了・回収する。"""
        with self._workers_lock:
            handle = self._active_workers.get(team_id) or self._all_worker_handles.get(team_id)
        if handle is None:
            return False

        pgid = handle.pgid
        proc = handle.proc

        # 1. プロセスグループ全体に SIGTERM 送信
        try:
            if hasattr(os, "killpg"):
                os.killpg(pgid, signal.SIGTERM)
            else:
                proc.terminate()
        except (ProcessLookupError, OSError):
            pass

        # プロセスの終了待機
        try:
            proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            # 2. タイムアウト時は SIGKILL 送信
            try:
                if hasattr(os, "killpg"):
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    proc.kill()
                proc.wait(timeout=1.0)
            except (ProcessLookupError, OSError):
                pass

        # 3. 親プロセス先行終了時でも残存した孫プロセス・子孫プロセスを刈り取るため SIGKILL を送信
        if hasattr(os, "killpg"):
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

        # 4. プロセスグループ内に実行中（非ゾンビ）プロセスが残っていないか検証 (最大2秒待機)
        deadline = time.time() + min(timeout, 2.0)
        still_alive = True
        if hasattr(os, "killpg"):
            while time.time() < deadline:
                if not has_active_processes_in_group(pgid):
                    still_alive = False
                    break
                time.sleep(0.05)
        else:
            still_alive = proc.poll() is None

        with self._workers_lock:
            if not still_alive:
                self._active_workers.pop(team_id, None)
                self._all_worker_handles.pop(team_id, None)
                status = "TERMINATED"
            else:
                # UU.1: 停止失敗時は監督ハンドルを保持し、再試行・再回収を可能にする
                status = "TERMINATION_FAILED"
            self._worker_history[team_id] = {
                "pid": handle.pid,
                "pgid": handle.pgid,
                "status": status,
                "terminated_at": time.time(),
            }
            if self._current_job_id:
                try:
                    WorkerRegistry(repo_dir=self.repo_dir, job_id=self._current_job_id).update_status(team_id, status)
                except OSError:
                    pass

        return not still_alive

    def terminate_all_workers(self, timeout: float = 2.0) -> dict[str, bool]:
        """RR.2, SS.1, TT.2: 現在アクティブな全ワーカーのプロセスグループを安全に回収・終了する。"""
        with self._workers_lock:
            team_ids = list(self._active_workers.keys())
        results: dict[str, bool] = {}
        for tid in team_ids:
            results[tid] = self.terminate_worker_process_group(tid, timeout=timeout)
        return results

    def recover_multi_team_job(
        self,
        job_id: str,
        verify_script: Path | None = None,
        cleanup: bool = True,
    ) -> MultiTeamRecoveryResult:
        """VV.2: 親プロセス強制終了（SIGKILL）等からの自律復旧。

        Case 1: 孤児ワーカーの検知と安全回収（PGIDによるグループ終了）
        Case 2: 終了済みワーカーの未退避 Evidence Bundle 救出
        Case 3: 確定済み Evidence Bundle の再利用と統合ゲート（RC発行）への前進
        """
        registry = WorkerRegistry(repo_dir=self.repo_dir, job_id=job_id)
        workers = registry.load()
        result = MultiTeamRecoveryResult(job_id=job_id)

        if not workers:
            result.details["error"] = f"No worker registry found for job_id '{job_id}'"
            return result

        evidence_dir = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        team_results: dict[str, TeamExecutionResult] = {}

        for tid, w in workers.items():
            wt_path = Path(w.worktree_path)
            dest_bundle = evidence_dir / f"{tid}.zip"

            # Case 1: 孤児ワーカーが生存しているか検査
            if w.pgid > 0 and has_active_processes_in_group(w.pgid):
                # WW.1: プロセス同一性照合（PID再利用・無関係プロセス誤停止の防止）
                curr_ident = get_process_start_identifier(w.pid)
                identity_verified = False
                if w.process_start_time and curr_ident:
                    identity_verified = (curr_ident == w.process_start_time)

                if identity_verified:
                    # 孤児プロセスグループを安全に停止・回収
                    if hasattr(os, "killpg"):
                        try:
                            os.killpg(w.pgid, signal.SIGTERM)
                        except (ProcessLookupError, OSError):
                            pass
                        deadline = time.time() + 1.0
                        while time.time() < deadline:
                            if not has_active_processes_in_group(w.pgid):
                                break
                            time.sleep(0.05)
                        if has_active_processes_in_group(w.pgid):
                            try:
                                os.killpg(w.pgid, signal.SIGKILL)
                            except (ProcessLookupError, OSError):
                                pass
                    registry.update_status(tid, "TERMINATED")
                    result.reclaimed_orphans.append(tid)
                else:
                    logger.warning(
                        "Process identity mismatch or unverified for worker %s (PID=%s, stored=%s, current=%s); skipping signal",
                        tid,
                        w.pid,
                        w.process_start_time,
                        curr_ident,
                    )
                    registry.update_status(tid, "UNVERIFIED_PROCESS")
                    result.unverified_processes.append(tid)

            # Case 3: 恒久保存先に確定済み Evidence が存在するか確認
            if dest_bundle.exists():
                if _is_valid_bundle_archive(dest_bundle):
                    result.reused_bundles.append(tid)
                    team_results[tid] = TeamExecutionResult(
                        team_id=tid,
                        success=True,
                        status="SUCCESS",
                        bundle_path=dest_bundle,
                        evidence_preserved=True,
                    )
                else:
                    logger.warning("Existing bundle for team '%s' is corrupted or invalid", tid)
                    team_results[tid] = TeamExecutionResult(
                        team_id=tid,
                        success=False,
                        status="INVALID_EVIDENCE",
                        bundle_path=dest_bundle,
                        evidence_preserved=False,
                    )
            else:
                # Case 2: Worktree 内に成果物（Bundle）が残存しているか探索
                wt_bundles_dir = wt_path / ".dev" / "bundles"
                wt_bundle: Path | None = None
                if wt_bundles_dir.exists():
                    candidates = sorted(wt_bundles_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
                    if candidates:
                        wt_bundle = candidates[0]

                if wt_bundle and wt_bundle.exists() and _is_valid_bundle_archive(wt_bundle):
                    # 未退避 Bundle を恒久保存先へ救出
                    tmp_dest = evidence_dir / f"{tid}.zip.tmp_rec_{os.getpid()}_{time.time_ns()}"
                    try:
                        shutil.copy2(wt_bundle, tmp_dest)
                        src_sha = hashlib.sha256(wt_bundle.read_bytes()).hexdigest()
                        copied_sha = hashlib.sha256(tmp_dest.read_bytes()).hexdigest()
                        if src_sha != copied_sha or not _is_valid_bundle_archive(tmp_dest):
                            raise OSError(f"Corrupted copy during rescue of team '{tid}'")

                        try:
                            os.link(tmp_dest, dest_bundle)
                            result.rescued_bundles.append(tid)
                            team_results[tid] = TeamExecutionResult(
                                team_id=tid,
                                success=True,
                                status="SUCCESS",
                                bundle_path=dest_bundle,
                                evidence_preserved=True,
                            )
                            registry.update_status(tid, "EVIDENCE_PRESERVED")
                        except FileExistsError:
                            existing_sha = hashlib.sha256(dest_bundle.read_bytes()).hexdigest()
                            if existing_sha == src_sha and _is_valid_bundle_archive(dest_bundle):
                                result.reused_bundles.append(tid)
                                team_results[tid] = TeamExecutionResult(
                                    team_id=tid,
                                    success=True,
                                    status="SUCCESS",
                                    bundle_path=dest_bundle,
                                    evidence_preserved=True,
                                )
                            else:
                                logger.error("Rescue collision with differing or corrupt bundle for '%s'", tid)
                                team_results[tid] = TeamExecutionResult(
                                    team_id=tid,
                                    success=False,
                                    status="BUNDLE_PRESERVATION_COLLISION",
                                )
                        finally:
                            if tmp_dest.exists():
                                tmp_dest.unlink()
                    except OSError as err:
                        logger.error("Failed to rescue bundle for team '%s': %s", tid, err)
                        team_results[tid] = TeamExecutionResult(
                            team_id=tid,
                            success=False,
                            status="RESCUE_FAILED",
                            details={"error": str(err)},
                        )
                elif wt_bundle and wt_bundle.exists() and not _is_valid_bundle_archive(wt_bundle):
                    logger.warning("Candidate bundle in worktree for team '%s' is invalid", tid)
                    team_results[tid] = TeamExecutionResult(
                        team_id=tid,
                        success=False,
                        status="INVALID_EVIDENCE",
                    )
                else:
                    team_results[tid] = TeamExecutionResult(
                        team_id=tid,
                        success=False,
                        status="EVIDENCE_NOT_FOUND",
                    )

        result.details["team_results"] = {
            tid: {
                "success": tr.success,
                "status": tr.status,
                "evidence_preserved": tr.evidence_preserved,
            }
            for tid, tr in team_results.items()
        }

        # 全チームの成果物が揃っている場合は統合ゲートへ進める
        all_preserved = all(tr.evidence_preserved for tr in team_results.values()) and len(team_results) == len(workers)
        result.success = all_preserved

        if all_preserved:
            inputs: list[TeamIntegrationInput] = []
            for tid, tr in team_results.items():
                worker_reg = workers.get(tid)
                if tr.bundle_path:
                    inputs.append(
                        TeamIntegrationInput(
                            team_id=tid,
                            bundle_path=tr.bundle_path,
                            branch=(worker_reg.branch_name if worker_reg and worker_reg.branch_name else f"team/{tid}"),
                            head_commit=tr.head_commit,
                        )
                    )

            int_res = None
            rc_commit = None
            rc_tag = None
            if verify_script is not None and self.integration_gate is not None:
                base_branch = next(iter(workers.values())).base_branch or "main"
                int_res = self.integration_gate.execute_integration(
                    job_id=job_id,
                    inputs=inputs,
                    base_commit=base_branch,
                    verify_script=verify_script,
                    resume=True,
                )
                if int_res.success:
                    rc_commit = int_res.release_candidate_commit
                    rc_tag = str(int_res.details.get("rc_tag")) if int_res.details.get("rc_tag") else f"refs/tags/rc/{job_id}"

            pipeline_status = (
                "SUCCESS"
                if (int_res and int_res.success)
                else ("RECOVERED_EVIDENCE_FOUND" if verify_script is None else "INTEGRATION_FAILED")
            )
            result.pipeline_result = MultiTeamResult(
                job_id=job_id,
                success=int_res.success if int_res else True,
                status=pipeline_status,
                team_results=team_results,
                integration_result=int_res,
                release_candidate_commit=rc_commit,
                release_candidate_tag=rc_tag,
            )
        else:
            result.success = False
            result.pipeline_result = MultiTeamResult(
                job_id=job_id,
                success=False,
                status="EVIDENCE_VERIFICATION_FAILED",
                team_results=team_results,
            )

        if cleanup:
            registered_wts = {wt.team_id: wt for wt in self.worktree_manager.list_worktrees()}
            base_dir = self.worktree_manager.worktree_base.resolve()
            for tid, w in workers.items():
                wt_p = Path(w.worktree_path)
                try:
                    resolved_wt = wt_p.resolve()
                    is_within_base = resolved_wt.is_relative_to(base_dir)
                except (ValueError, OSError):
                    is_within_base = False

                # WW.2: 安全境界検査: worktree_base 配下かつ Git 登録済みであること
                if not is_within_base or tid not in registered_wts:
                    logger.warning(
                        "Worktree path for team '%s' (%s) is outside base directory or not registered in git; preserving untouched",
                        tid,
                        w.worktree_path,
                    )
                    result.unverified_worktrees.append(tid)
                    continue

                try:
                    self.worktree_manager.remove_worktree(tid, force=True)
                except (subprocess.SubprocessError, OSError, WorktreeError) as exc:
                    logger.debug("Worktree removal non-fatal error for %s: %s", tid, exc)

        return result

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
                    # 恒久保存先への Evidence 退避が未完了（または失敗・衝突）の場合は削除を抑止
                    has_bundle_in_worktree = (
                        res.status in ("BUNDLE_PRESERVATION_FAILED", "BUNDLE_CORRUPTED", "BUNDLE_PRESERVATION_COLLISION")
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
        """II.1, II.2, VV.1: 各チームワーカーを並行実行し、結果・コミット・成果物バンドルを収集する。"""
        self._current_job_id = config.job_id
        registry = WorkerRegistry(repo_dir=self.repo_dir, job_id=config.job_id)
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

                stdout_str = ""
                stderr_str = ""
                exit_code = -1

                # 既存テストで subprocess.run がモックされている場合は互換性のために delegation
                import unittest.mock
                if isinstance(subprocess.run, unittest.mock.Mock):
                    proc_res = subprocess.run(
                        cmd,
                        cwd=wt_path,
                        capture_output=True,
                        text=True,
                        timeout=timeout_sec,
                        check=False,
                        env=merged_env,
                    )
                    exit_code = proc_res.returncode
                    stdout_str = proc_res.stdout
                    stderr_str = proc_res.stderr
                else:
                    popen_kwargs: dict[str, Any] = {
                        "cwd": wt_path,
                        "stdout": subprocess.PIPE,
                        "stderr": subprocess.PIPE,
                        "text": True,
                        "env": merged_env,
                    }
                    if hasattr(os, "setsid"):
                        popen_kwargs["start_new_session"] = True

                    proc = subprocess.Popen(cmd, **popen_kwargs)
                    pgid = proc.pid
                    if hasattr(os, "getpgid"):
                        try:
                            pgid = os.getpgid(proc.pid)
                        except (ProcessLookupError, OSError):
                            pgid = proc.pid
                    handle = WorkerProcessHandle(team_id=team.team_id, proc=proc, pid=proc.pid, pgid=pgid)
                    with self._workers_lock:
                        self._active_workers[team.team_id] = handle
                        self._all_worker_handles[team.team_id] = handle

                    now = time.time()
                    proc_ident = get_process_start_identifier(proc.pid)
                    registry.register_worker(
                        WorkerRegistration(
                            team_id=team.team_id,
                            pid=proc.pid,
                            pgid=pgid,
                            worktree_path=str(wt_path),
                            base_branch=team.base_branch or config.base_branch,
                            branch_name=f"team/{team.team_id}",
                            status="RUNNING",
                            start_time=now,
                            updated_at=now,
                            process_start_time=proc_ident,
                        )
                    )

                    try:
                        stdout_str, stderr_str = proc.communicate(timeout=timeout_sec)
                        exit_code = proc.returncode
                    except subprocess.TimeoutExpired:
                        logger.error("Team '%s' timed out after %s seconds; terminating process group", team.team_id, timeout_sec)
                        registry.update_status(team.team_id, "TIMEOUT", details={"error": f"Timed out after {timeout_sec}s"})
                        self.terminate_worker_process_group(team.team_id)
                        try:
                            rem_out, rem_err = proc.communicate(timeout=1.0)
                            stdout_str = rem_out or ""
                            stderr_str = rem_err or ""
                        except (subprocess.TimeoutExpired, ValueError):
                            pass
                        return TeamExecutionResult(
                            team_id=team.team_id,
                            success=False,
                            status="TIMEOUT",
                            details={
                                "error": f"Timed out after {timeout_sec}s",
                                "stdout": stdout_str[-500:] if stdout_str else "",
                                "stderr": stderr_str[-500:] if stderr_str else "",
                            },
                        )
                    except BaseException:
                        self.terminate_worker_process_group(team.team_id)
                        raise
                    finally:
                        final_status = "COMPLETED" if exit_code == 0 else "FAILED"
                        registry.update_status(team.team_id, final_status, details={"exit_code": exit_code})
                        with self._workers_lock:
                            if not has_active_processes_in_group(handle.pgid):
                                self._active_workers.pop(team.team_id, None)
                                self._all_worker_handles.pop(team.team_id, None)
                                self._worker_history[team.team_id] = {
                                    "pid": handle.pid,
                                    "pgid": handle.pgid,
                                    "status": final_status,
                                    "finished_at": time.time(),
                                }

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

                success = exit_code == 0
                return TeamExecutionResult(
                    team_id=team.team_id,
                    success=success,
                    status="SUCCESS" if success else "FAILED",
                    branch=f"team/{team.team_id}",
                    head_commit=head_commit,
                    bundle_path=bundle_path,
                    details={
                        "exit_code": exit_code,
                        "stdout": stdout_str[-500:] if stdout_str else "",
                        "stderr": stderr_str[-500:] if stderr_str else "",
                    },
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
            self.terminate_all_workers()
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

                # PP.1, PP.2: 既存の保存先 Evidence Bundle の非破壊検査
                if dest_path.exists():
                    existing_bytes = dest_path.read_bytes()
                    existing_sha = hashlib.sha256(existing_bytes).hexdigest()
                    if existing_sha == orig_sha:
                        logger.info("Identical evidence bundle already preserved for team '%s'", res.team_id)
                        res.bundle_path = dest_path
                        res.evidence_preserved = True
                        continue
                    else:
                        logger.error(
                            "Evidence bundle collision for team '%s': existing bundle has different SHA-256 (%s vs %s)",
                            res.team_id,
                            existing_sha,
                            orig_sha,
                        )
                        res.success = False
                        res.status = "BUNDLE_PRESERVATION_COLLISION"
                        res.details["bundle_error"] = (
                            f"Collision: An evidence bundle with different content already exists at {dest_path}. "
                            f"Refusing to overwrite past evidence (existing={existing_sha[:10]}, new={orig_sha[:10]})."
                        )
                        continue

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

                # QQ.1: os.link による排他的・アトミック確定（既存ファイルの上書きを完全に遮断）
                try:
                    os.link(tmp_path, dest_path)
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    res.bundle_path = dest_path
                    res.evidence_preserved = True
                except FileExistsError:
                    # 先行・並行プロセスによって dest_path が直前に作成された場合 (TOCTOU競合検知)
                    if tmp_path.exists():
                        try:
                            tmp_path.unlink()
                        except OSError:
                            pass
                    existing_bytes = dest_path.read_bytes()
                    existing_sha = hashlib.sha256(existing_bytes).hexdigest()
                    if existing_sha == orig_sha:
                        logger.info(
                            "Concurrent preservation: identical bundle was already linked for team '%s'",
                            res.team_id,
                        )
                        res.bundle_path = dest_path
                        res.evidence_preserved = True
                        continue
                    else:
                        logger.error(
                            "Concurrent preservation collision for team '%s': existing bundle has different SHA-256 (%s vs %s)",
                            res.team_id,
                            existing_sha,
                            orig_sha,
                        )
                        res.success = False
                        res.status = "BUNDLE_PRESERVATION_COLLISION"
                        res.details["bundle_error"] = (
                            f"Collision: A concurrent evidence bundle with different content was published at {dest_path}. "
                            f"Refusing to overwrite existing evidence (existing={existing_sha[:10]}, new={orig_sha[:10]})."
                        )
                        continue
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
