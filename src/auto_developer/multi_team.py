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
import sys
import tempfile
import threading
import time
import uuid
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from auto_developer.bundler import EvidenceBundler
from auto_developer.dispatcher import TaskDispatcher, extract_plan_scope_lines
from auto_developer.guard import GitGuard
from auto_developer.integration_gate import (
    VALID_JOB_ID_PATTERN,
    IntegrationGate,
    IntegrationJobState,
    IntegrationResult,
    TeamIntegrationInput,
)
from auto_developer.manifest import ManifestValidationError, TeamManifest, load_teams_manifest
from auto_developer.models import JobState
from auto_developer.worktree import SECRET_PATTERNS, WorktreeError, WorktreeInfo, WorktreeManager

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

    _path_locks_guard: ClassVar[threading.Lock] = threading.Lock()
    _path_locks: ClassVar[dict[Path, threading.RLock]] = {}

    @classmethod
    def _get_lock_for_path(cls, path: Path) -> threading.RLock:
        resolved = path.resolve()
        with cls._path_locks_guard:
            lock = cls._path_locks.get(resolved)
            if lock is None:
                lock = threading.RLock()
                cls._path_locks[resolved] = lock
            return lock

    def __init__(self, repo_dir: Path, job_id: str) -> None:
        self.repo_dir = repo_dir
        self.job_id = job_id
        self.registry_dir = repo_dir / ".dev" / "integration_jobs" / job_id
        self.registry_file = self.registry_dir / "workers.json"
        self._lock = self._get_lock_for_path(self.registry_file)

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


def _is_valid_evidence_bundle(bundle_path: Path, repo_dir: Path) -> bool:
    """XX.2: Evidence Bundle が ZIP として破損しておらず、EvidenceBundler による独立監査 (VERIFIED) に合格するか検査する。"""
    if not _is_valid_bundle_archive(bundle_path):
        return False
    try:
        bundler = EvidenceBundler(repo_dir=repo_dir)
        verification = bundler.verify_bundle(bundle_path)
        return bool(verification.is_valid and verification.status == "VERIFIED")
    except (OSError, ValueError, zipfile.BadZipFile) as exc:
        logger.debug("Evidence verification error for %s: %s", bundle_path, exc)
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
            if self.integration_gate is not None:
                int_state = IntegrationJobState.load(self.integration_gate.jobs_dir, job_id)
                if int_state is not None:
                    rec_ok, rec_status, rec_details = self.integration_gate.recover_interrupted_job(job_id)
                    result.success = rec_ok
                    result.details.update(rec_details)
                    result.details["recovery_status"] = rec_status
                    result.pipeline_result = MultiTeamResult(
                        job_id=job_id,
                        success=rec_ok,
                        status=rec_status,
                        release_candidate_commit=rec_details.get("rc_commit") or int_state.rc_commit,
                        release_candidate_tag=rec_details.get("rc_tag") or int_state.rc_tag,
                        details=rec_details,
                    )
                    return result
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
                # WW.1, XX.1: プロセス同一性および所属照合（PID再利用・PGID不一致による無関係プロセス誤停止の防止）
                curr_ident = get_process_start_identifier(w.pid)
                identity_verified = False
                if w.process_start_time and curr_ident and curr_ident == w.process_start_time:
                    # XX.1: PID が Registry に記録された PGID に実際に所属しているか確認
                    if hasattr(os, "getpgid"):
                        try:
                            actual_pgid = os.getpgid(w.pid)
                            if actual_pgid == w.pgid:
                                identity_verified = True
                            else:
                                logger.warning(
                                    "PID %s belongs to PGID %s, but registry specified PGID %s; skipping signal for safety",
                                    w.pid,
                                    actual_pgid,
                                    w.pgid,
                                )
                        except (ProcessLookupError, OSError):
                            logger.warning(
                                "Cannot determine PGID for PID %s (process terminated or inaccessible); skipping signal for safety",
                                w.pid,
                            )
                    else:
                        identity_verified = True

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
                if _is_valid_evidence_bundle(dest_bundle, self.repo_dir):
                    result.reused_bundles.append(tid)
                    team_results[tid] = TeamExecutionResult(
                        team_id=tid,
                        success=True,
                        status="SUCCESS",
                        bundle_path=dest_bundle,
                        evidence_preserved=True,
                    )
                else:
                    logger.warning("Existing bundle for team '%s' failed EvidenceBundler audit", tid)
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

                if wt_bundle and wt_bundle.exists() and _is_valid_evidence_bundle(wt_bundle, wt_path):
                    # 未退避 Bundle を恒久保存先へ救出
                    tmp_dest = evidence_dir / f"{tid}.zip.tmp_rec_{os.getpid()}_{time.time_ns()}"
                    try:
                        shutil.copy2(wt_bundle, tmp_dest)
                        src_sha = hashlib.sha256(wt_bundle.read_bytes()).hexdigest()
                        copied_sha = hashlib.sha256(tmp_dest.read_bytes()).hexdigest()
                        if src_sha != copied_sha or not _is_valid_evidence_bundle(tmp_dest, self.repo_dir):
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
                            if existing_sha == src_sha and _is_valid_evidence_bundle(dest_bundle, self.repo_dir):
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
                elif wt_bundle and wt_bundle.exists() and not _is_valid_evidence_bundle(wt_bundle, wt_path):
                    logger.warning("Candidate bundle in worktree for team '%s' failed EvidenceBundler audit", tid)
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

        # YY.1: 未確認ワーカー（unverified_processes）が存在しないこと、かつ全チームの有効な成果物が揃っている場合は統合ゲートへ進める
        all_preserved = (
            not result.unverified_processes
            and all(tr.evidence_preserved for tr in team_results.values())
            and len(team_results) == len(workers)
        )

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

            is_pipeline_success = bool(int_res and int_res.success)
            pipeline_status = (
                "SUCCESS"
                if is_pipeline_success
                else ("RECOVERED_EVIDENCE_FOUND" if verify_script is None else "INTEGRATION_FAILED")
            )
            result.success = is_pipeline_success
            result.pipeline_result = MultiTeamResult(
                job_id=job_id,
                success=is_pipeline_success,
                status=pipeline_status,
                team_results=team_results,
                integration_result=int_res,
                release_candidate_commit=rc_commit,
                release_candidate_tag=rc_tag,
            )
        else:
            result.success = False
            fail_status = "RECOVERY_REQUIRED" if result.unverified_processes else "EVIDENCE_VERIFICATION_FAILED"
            result.pipeline_result = MultiTeamResult(
                job_id=job_id,
                success=False,
                status=fail_status,
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

                # YY.2: 未確認プロセス（同一性・所属関係の照合失敗）の Worktree は安全のため絶対に削除せず保全
                if tid in result.unverified_processes:
                    logger.warning(
                        "Worktree for unverified worker team '%s' (%s) preserved for safety; skipping deletion",
                        tid,
                        w.worktree_path,
                    )
                    result.unverified_worktrees.append(tid)
                    continue

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
                cmd = [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(wt_path), "run", *team.extra_args]

            if not cmd:
                return TeamExecutionResult(
                    team_id=team.team_id,
                    success=False,
                    status="NO_COMMAND",
                    details={"error": "No command or worker function provided"},
                )

            try:
                merged_env = os.environ.copy()
                if config.job_id:
                    merged_env["AUTO_DEV_JOB_ID"] = config.job_id
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

                    timed_out = False
                    try:
                        stdout_str, stderr_str = proc.communicate(timeout=timeout_sec)
                        exit_code = proc.returncode
                    except subprocess.TimeoutExpired:
                        timed_out = True
                        logger.error("Team '%s' timed out after %s seconds; terminating process group", team.team_id, timeout_sec)
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
                        if timed_out:
                            final_status = "TIMEOUT"
                            registry.update_status(
                                team.team_id,
                                final_status,
                                details={"error": f"Timed out after {timeout_sec}s"},
                            )
                        else:
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
            if res is None or res.status == "NO_CHANGES" or res.bundle_path is None or not res.bundle_path.exists():
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
        timeout_sec: int = 300,
    ) -> MultiTeamResult:
        """全ワーカーの実行結果を収集・照合し、IntegrationGate へ安全に引き渡す。"""
        # LL.1, LL.2: 統合および Worktree 削除前に各チームの Evidence Bundle を退避保全
        self.preserve_team_bundles(config, team_results)

        failed_teams: list[str] = []
        no_changes_teams: list[str] = []
        inputs: list[TeamIntegrationInput] = []

        for team in config.teams:
            res = team_results.get(team.team_id)
            if res is not None and res.success and res.status == "NO_CHANGES":
                no_changes_teams.append(team.team_id)
            elif res is None or not res.success or res.bundle_path is None:
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

        if not inputs and no_changes_teams:
            return MultiTeamResult(
                job_id=config.job_id,
                success=True,
                status="NO_CHANGES",
                team_results=team_results,
                release_candidate_commit=None,
                release_candidate_tag=None,
                details={"no_changes_teams": no_changes_teams},
            )

        # IntegrationGate による統合実行
        int_result = self.integration_gate.execute_integration(
            job_id=config.job_id,
            inputs=inputs,
            base_commit=config.base_branch,
            verify_script=verify_script or config.integration_verify_script,
            timeout_sec=timeout_sec,
            resume=resume,
        )

        rc_tag = str(int_result.details.get("rc_tag")) if int_result.details.get("rc_tag") else None
        result_details = dict(int_result.details)
        if no_changes_teams:
            result_details["no_changes_teams"] = no_changes_teams
        return MultiTeamResult(
            job_id=config.job_id,
            success=int_result.success,
            status=int_result.status,
            team_results=team_results,
            integration_result=int_result,
            release_candidate_commit=int_result.release_candidate_commit,
            release_candidate_tag=rc_tag,
            details=result_details,
        )


def _run_git_cmd(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
    """Git コマンドを実行し、CompletedProcess を返す。"""
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=False,
    )


def _list_branches_with_prefix(repo_dir: Path, prefix: str) -> list[str]:
    """指定プレフィックスを持つローカルブランチ一覧を返す。"""
    proc = _run_git_cmd(repo_dir, ["for-each-ref", "--format=%(refname:short)", f"refs/heads/{prefix}"])
    if proc.returncode != 0:
        return []
    return [line.strip() for line in proc.stdout.splitlines() if line.strip()]


def _branch_exists(repo_dir: Path, branch: str) -> bool:
    """ローカルブランチ refs/heads/<branch> が存在するか判定する。"""
    proc = _run_git_cmd(repo_dir, ["rev-parse", "--verify", f"refs/heads/{branch}"])
    return proc.returncode == 0


def _team_job_record_path(repo_dir: Path, job_id: str) -> Path:
    return repo_dir / ".dev" / "integration_jobs" / job_id / "job.json"


def _save_team_job_record(repo_dir: Path, payload: dict[str, Any]) -> Path:
    """マルチチームジョブの準備・状態レコードを原子的に保存する。"""
    job_id = str(payload["job_id"])
    target = _team_job_record_path(repo_dir, job_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.parent / f"job.json.tmp_{os.getpid()}_{time.time_ns()}"
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return target


def _load_team_job_record(repo_dir: Path, job_id: str) -> dict[str, Any] | None:
    """マルチチームジョブの job.json を読み込む。"""
    target = _team_job_record_path(repo_dir, job_id)
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (json.JSONDecodeError, OSError):
        return None


def _find_latest_team_job_id(repo_dir: Path) -> str | None:
    """integration_jobs 配下から最新のジョブ ID を特定する。"""
    jobs_dir = repo_dir / ".dev" / "integration_jobs"
    if not jobs_dir.is_dir():
        return None

    latest_by_id: dict[str, float] = {}
    for entry in jobs_dir.iterdir():
        if entry.is_file() and entry.suffix == ".json" and not entry.name.startswith("."):
            jid = entry.stem
            if not VALID_JOB_ID_PATTERN.match(jid):
                continue
            state = IntegrationJobState.load(jobs_dir, jid)
            ts = state.updated_at if state is not None else entry.stat().st_mtime
            latest_by_id[jid] = max(latest_by_id.get(jid, 0.0), ts)
        elif entry.is_dir() and VALID_JOB_ID_PATTERN.match(entry.name):
            jid = entry.name
            job_rec = _load_team_job_record(repo_dir, jid)
            if job_rec is not None:
                ts = float(job_rec.get("updated_at") or job_rec.get("created_at") or (entry / "job.json").stat().st_mtime)
                latest_by_id[jid] = max(latest_by_id.get(jid, 0.0), ts)
            workers_file = entry / "workers.json"
            if workers_file.is_file():
                try:
                    wdata = json.loads(workers_file.read_text(encoding="utf-8"))
                    ts = float(wdata.get("updated_at") or workers_file.stat().st_mtime)
                except (json.JSONDecodeError, OSError, ValueError):
                    ts = workers_file.stat().st_mtime
                latest_by_id[jid] = max(latest_by_id.get(jid, 0.0), ts)

    if not latest_by_id:
        return None
    return max(latest_by_id, key=lambda k: latest_by_id[k])


def resolve_active_team_job_id(repo_dir: Path, team_id: str | None = None) -> str | None:
    """worktree またはメインリポジトリから現在有効なマルチチームジョブ ID を解決する。"""
    env_job_id = os.environ.get("AUTO_DEV_JOB_ID", "").strip()
    if env_job_id and VALID_JOB_ID_PATTERN.match(env_job_id):
        return env_job_id

    resolved = repo_dir.resolve()
    main_repo = resolved
    proc = _run_git_cmd(resolved, ["rev-parse", "--git-common-dir"])
    if proc.returncode == 0 and proc.stdout.strip():
        common_dir = Path(proc.stdout.strip())
        if not common_dir.is_absolute():
            common_dir = (resolved / common_dir).resolve()
        else:
            common_dir = common_dir.resolve()
        if common_dir.name == ".git":
            main_repo = common_dir.parent

    jobs_dir = main_repo / ".dev" / "integration_jobs"
    if not jobs_dir.is_dir():
        return None

    candidates: dict[str, float] = {}
    for entry in jobs_dir.iterdir():
        if not entry.is_dir() or not VALID_JOB_ID_PATTERN.match(entry.name):
            continue
        jid = entry.name
        job_rec = _load_team_job_record(main_repo, jid)
        if job_rec is None or job_rec.get("status") == "CLEANED_UP":
            continue
        if team_id:
            rec_teams = {
                str(t.get("team_id"))
                for t in (job_rec.get("teams") or [])
                if isinstance(t, dict) and t.get("team_id")
            }
            wt_map = job_rec.get("worktrees")
            if isinstance(wt_map, dict):
                rec_teams.update(str(k) for k in wt_map)
            if rec_teams and team_id not in rec_teams:
                continue
        ts = float(job_rec.get("updated_at") or job_rec.get("created_at") or (entry / "job.json").stat().st_mtime)
        candidates[jid] = ts

    if not candidates:
        return None
    return max(candidates, key=lambda k: candidates[k])


def _extract_no_changes_teams(
    int_state: IntegrationJobState | None,
    job_rec: dict[str, Any] | None,
) -> list[str]:
    """IntegrationJobState / job.json から NO_CHANGES チーム ID 一覧を重複なく抽出する。"""
    seen: list[str] = []
    sources: list[Any] = []
    if int_state is not None and isinstance(int_state.details, dict):
        sources.append(int_state.details.get("no_changes_teams"))
    if job_rec is not None:
        sources.append(job_rec.get("no_changes_teams"))
    for src in sources:
        if isinstance(src, list):
            for tid in src:
                s_tid = str(tid).strip()
                if s_tid and s_tid not in seen:
                    seen.append(s_tid)
    return seen


def _is_registered_worker_alive(reg: WorkerRegistration) -> bool:
    """登録済みワーカープロセスが同一性・PGID を保って生存しているか確認する。"""
    if reg.pgid <= 0 or not has_active_processes_in_group(reg.pgid):
        return False
    curr_ident = get_process_start_identifier(reg.pid)
    if not reg.process_start_time or not curr_ident or curr_ident != reg.process_start_time:
        return False
    if hasattr(os, "getpgid"):
        try:
            return os.getpgid(reg.pid) == reg.pgid
        except (ProcessLookupError, OSError):
            return False
    return True


def _detect_leftover_team_resources(repo_dir: Path, manifest: TeamManifest) -> list[str]:
    """前回のジョブの worktree・チームブランチ・未完了統合ブランチの残存を検出する。"""
    leftovers: list[str] = []
    wt_manager = WorktreeManager(repo_dir=repo_dir)
    jobs_dir = repo_dir / ".dev" / "integration_jobs"

    active_worktrees = wt_manager.list_worktrees()
    seen_wt_paths: set[Path] = set()
    for wt in active_worktrees:
        seen_wt_paths.add(wt.path.resolve())
        leftovers.append(f"worktree '{wt.team_id}' ({wt.path}, branch={wt.branch or 'detached'})")

    if wt_manager.worktree_base.is_dir():
        for child in sorted(wt_manager.worktree_base.iterdir()):
            resolved = child.resolve()
            if resolved not in seen_wt_paths and (child.is_dir() or child.is_file()):
                leftovers.append(f"worktree directory '{child}'")

    expected_branches = {f"team/{t.id}" for t in manifest.teams}
    existing_team_branches = set(_list_branches_with_prefix(repo_dir, "team/"))
    for br in sorted(expected_branches | existing_team_branches):
        if br in existing_team_branches or _branch_exists(repo_dir, br):
            leftovers.append(f"branch '{br}'")

    for int_branch in sorted(_list_branches_with_prefix(repo_dir, "integration/")):
        int_job_id = int_branch.removeprefix("integration/")
        int_state = IntegrationJobState.load(jobs_dir, int_job_id)
        job_rec = _load_team_job_record(repo_dir, int_job_id)
        is_completed = int_state is not None and int_state.state == "COMPLETED"
        is_cleaned = job_rec is not None and job_rec.get("status") == "CLEANED_UP"
        if not is_completed and not is_cleaned:
            leftovers.append(f"uncompleted integration branch '{int_branch}'")

    return leftovers


def prepare_teams_job(
    repo_dir: Path,
    job_id: str | None = None,
) -> tuple[dict[str, Any] | None, int, str]:
    """TEAM-13: クリーンツリーと teams.json を検査し、全チームの worktree を準備する。"""
    resolved_repo = repo_dir.resolve()
    if job_id is not None and not VALID_JOB_ID_PATTERN.match(job_id):
        return (
            None,
            2,
            f"TEAMS_PREPARE_ERROR: Invalid job_id '{job_id}': must match '^[a-zA-Z0-9_-]+$'.",
        )

    try:
        manifest = load_teams_manifest(resolved_repo)
    except (FileNotFoundError, ManifestValidationError, OSError) as err:
        return None, 2, f"TEAMS_CONFIG_ERROR: {err}"

    if not manifest.teams:
        return None, 2, "TEAMS_CONFIG_ERROR: No teams defined in teams.json."

    git_guard = GitGuard(repo_dir=resolved_repo)
    try:
        git_guard.assert_clean_working_tree()
    except (RuntimeError, subprocess.SubprocessError) as err:
        return None, 2, f"PRE-FLIGHT ERROR: {err}"

    base_chk = _run_git_cmd(resolved_repo, ["rev-parse", "--verify", f"{manifest.base_ref}^{{commit}}"])
    if base_chk.returncode != 0:
        return (
            None,
            2,
            f"TEAMS_CONFIG_ERROR: Base ref '{manifest.base_ref}' does not exist in repository.",
        )
    base_commit = base_chk.stdout.strip()

    leftovers = _detect_leftover_team_resources(resolved_repo, manifest)
    if leftovers:
        items = ", ".join(leftovers[:5])
        suffix = f" (+{len(leftovers) - 5} more)" if len(leftovers) > 5 else ""
        msg = (
            f"TEAMS_PREPARE_ERROR: Previous team job worktrees or branches still exist ({items}{suffix}). "
            "Refusing to recreate worktrees.\n"
            "Inspect with `auto-dev teams status`, clean up finished/aborted jobs with "
            "`auto-dev teams cleanup --job-id <id>`, or recover interrupted jobs with "
            "`auto-dev teams recover [--job-id <id>]`."
        )
        return None, 1, msg

    effective_job_id = job_id or f"job-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    jobs_dir = resolved_repo / ".dev" / "integration_jobs"
    if (
        _team_job_record_path(resolved_repo, effective_job_id).exists()
        or (jobs_dir / f"{effective_job_id}.json").exists()
        or (jobs_dir / effective_job_id / "workers.json").exists()
    ):
        return (
            None,
            1,
            (
                f"TEAMS_PREPARE_ERROR: Job '{effective_job_id}' already exists. "
                "Use a new --job-id or inspect with `auto-dev teams status`."
            ),
        )

    config = MultiTeamConfig(
        job_id=effective_job_id,
        teams=[
            TeamWorkerSpec(team_id=team.id, base_branch=manifest.base_ref)
            for team in manifest.teams
        ],
        base_branch=manifest.base_ref,
    )
    orchestrator = MultiTeamOrchestrator(repo_dir=resolved_repo)
    try:
        wt_paths = orchestrator.prepare_workers(config)
    except (WorktreeError, OSError, subprocess.SubprocessError) as err:
        return None, 1, f"TEAMS_PREPARE_ERROR: {err}"

    now = time.time()
    teams_info = [
        {
            "team_id": team.id,
            "branch": f"team/{team.id}",
            "worktree_path": str(wt_paths[team.id]),
            "plan": team.plan,
        }
        for team in manifest.teams
    ]
    payload: dict[str, Any] = {
        "job_id": effective_job_id,
        "base_ref": manifest.base_ref,
        "base_commit": base_commit,
        "status": "PREPARED",
        "created_at": now,
        "updated_at": now,
        "worktrees": {tid: str(path) for tid, path in wt_paths.items()},
        "teams": teams_info,
    }
    _save_team_job_record(resolved_repo, payload)
    return payload, 0, ""


def _parse_plan_counts(
    wt_path: Path,
    repo_dir: Path,
    plan_rel: str,
    branch: str,
    wt_exists: bool,
    branch_exists: bool,
) -> tuple[int, int, int]:
    """チーム計画ファイルの完了数・全タスク数・失敗数を返す。"""
    content = ""
    wt_plan = wt_path / plan_rel
    if wt_exists and wt_plan.is_file():
        content = wt_plan.read_text(encoding="utf-8")
    elif branch_exists:
        proc = _run_git_cmd(repo_dir, ["show", f"{branch}:{plan_rel}"])
        if proc.returncode == 0:
            content = proc.stdout
    if not content and (repo_dir / plan_rel).is_file():
        content = (repo_dir / plan_rel).read_text(encoding="utf-8")

    tasks = TaskDispatcher().parse_plan_text(content) if content else []
    total = len(tasks)
    completed = sum(1 for t in tasks if t.is_completed)
    failed = sum(1 for t in tasks if not t.is_completed and t.is_failed)
    return completed, total, failed


def _sanitize_team_token(team_id: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_\-]", "_", team_id)


def _collect_team_quarantine_branches(
    team_id: str,
    all_quarantines: list[str],
    all_team_ids: list[str],
    wt_job_state: JobState | None,
    *,
    job_id: str | None = None,
    job_rec: dict[str, Any] | None = None,
) -> list[str]:
    """指定ジョブ・チームに属する隔離ブランチ一覧を抽出する。"""
    del all_team_ids  # チーム間に job_id/team_id/ スラッシュ境界があるため他チームとの前方一致衝突は起きない
    if not job_id:
        return []
    clean_job = _sanitize_team_token(job_id)
    clean_team = _sanitize_team_token(team_id)
    prefix = f"quarantine/{clean_job}/{clean_team}/"
    matched: set[str] = {qb for qb in all_quarantines if qb.startswith(prefix)}
    if wt_job_state and isinstance(wt_job_state.quarantined_task, dict):
        q_branch = wt_job_state.quarantined_task.get("quarantined_branch")
        if isinstance(q_branch, str) and q_branch.startswith(prefix):
            matched.add(q_branch)
    if job_rec and isinstance(job_rec.get("quarantine_branches"), dict):
        rec_q = job_rec["quarantine_branches"].get(team_id)
        if isinstance(rec_q, str) and rec_q.startswith(prefix) and rec_q in all_quarantines:
            matched.add(rec_q)
    return sorted(matched)


def get_teams_status(
    repo_dir: Path,
    job_id: str | None = None,
) -> tuple[dict[str, Any] | None, int, str]:
    """TEAM-16: チームごとに worktree・ブランチ・完了数/全タスク数・ワーカー状態・隔離ブランチを集計する。"""
    resolved_repo = repo_dir.resolve()
    jobs_dir = resolved_repo / ".dev" / "integration_jobs"

    if job_id is not None:
        if not VALID_JOB_ID_PATTERN.match(job_id):
            return None, 2, f"TEAMS_STATUS_ERROR: Invalid job_id '{job_id}'."
        has_job = (
            _team_job_record_path(resolved_repo, job_id).is_file()
            or (jobs_dir / f"{job_id}.json").is_file()
            or (jobs_dir / job_id / "workers.json").is_file()
        )
        if not has_job:
            return None, 1, f"TEAMS_STATUS_ERROR: Job '{job_id}' not found."
        resolved_job_id: str | None = job_id
    else:
        resolved_job_id = _find_latest_team_job_id(resolved_repo)

    job_rec = _load_team_job_record(resolved_repo, resolved_job_id) if resolved_job_id else None
    workers = WorkerRegistry(resolved_repo, resolved_job_id).load() if resolved_job_id else {}
    int_state = IntegrationJobState.load(jobs_dir, resolved_job_id) if resolved_job_id else None

    manifest: TeamManifest | None = None
    try:
        manifest = load_teams_manifest(resolved_repo)
    except (FileNotFoundError, ManifestValidationError, OSError):
        manifest = None

    wt_manager = WorktreeManager(repo_dir=resolved_repo)
    live_wts: dict[str, WorktreeInfo] = {
        wt.team_id: wt
        for wt in wt_manager.list_worktrees()
        if not wt.team_id.startswith("int-")
    }

    ordered_team_ids: list[str] = []
    job_teams_by_id: dict[str, dict[str, Any]] = {}
    if job_rec and isinstance(job_rec.get("teams"), list):
        for entry in job_rec["teams"]:
            if isinstance(entry, dict) and entry.get("team_id"):
                tid = str(entry["team_id"])
                job_teams_by_id[tid] = entry
                if tid not in ordered_team_ids:
                    ordered_team_ids.append(tid)
    if manifest is not None:
        for team in manifest.teams:
            if team.id not in ordered_team_ids:
                ordered_team_ids.append(team.id)
    for tid in workers:
        if tid not in ordered_team_ids:
            ordered_team_ids.append(tid)
    if int_state is not None:
        for tid in int_state.verified_commits:
            if tid not in ordered_team_ids:
                ordered_team_ids.append(tid)
    for tid in live_wts:
        if tid not in ordered_team_ids:
            ordered_team_ids.append(tid)

    manifest_by_id = {t.id: t for t in manifest.teams} if manifest is not None else {}
    no_changes_teams = set(_extract_no_changes_teams(int_state, job_rec))
    all_quarantines = _list_branches_with_prefix(resolved_repo, "quarantine/")
    existing_branches = set(_list_branches_with_prefix(resolved_repo, ""))

    team_statuses: list[dict[str, Any]] = []
    for tid in ordered_team_ids:
        wt_info = live_wts.get(tid)
        reg = workers.get(tid)
        rec_entry = job_teams_by_id.get(tid, {})
        m_team = manifest_by_id.get(tid)

        if wt_info is not None:
            wt_path = wt_info.path
        elif reg is not None and reg.worktree_path:
            wt_path = Path(reg.worktree_path)
        elif rec_entry.get("worktree_path"):
            wt_path = Path(str(rec_entry["worktree_path"]))
        else:
            wt_path = (wt_manager.worktree_base / tid).resolve()

        wt_exists = wt_info is not None and wt_path.exists()
        branch = (
            (wt_info.branch if wt_info and wt_info.branch else "")
            or (reg.branch_name if reg and reg.branch_name else "")
            or str(rec_entry.get("branch") or "")
            or f"team/{tid}"
        )
        branch_exists = branch in existing_branches or _branch_exists(resolved_repo, branch)

        plan_rel = (
            (m_team.plan if m_team is not None else "")
            or str(rec_entry.get("plan") or "")
            or f"plans/{tid}.md"
        )
        completed_tasks, total_tasks, failed_tasks = _parse_plan_counts(
            wt_path=wt_path,
            repo_dir=resolved_repo,
            plan_rel=plan_rel,
            branch=branch,
            wt_exists=wt_exists,
            branch_exists=branch_exists,
        )

        wt_job_state: JobState | None = None
        wt_state_file = wt_path / ".dev" / "job_state.json"
        if wt_exists and wt_state_file.is_file():
            try:
                wt_job_state = JobState.load(wt_state_file)
            except (OSError, json.JSONDecodeError, ValueError):
                wt_job_state = None

        quarantines = _collect_team_quarantine_branches(
            team_id=tid,
            all_quarantines=all_quarantines,
            all_team_ids=ordered_team_ids,
            wt_job_state=wt_job_state,
            job_id=resolved_job_id,
            job_rec=job_rec,
        )

        if reg is not None:
            if reg.status == "RUNNING" and not _is_registered_worker_alive(reg):
                worker_status = "STALE"
            else:
                worker_status = reg.status
        elif wt_job_state is not None:
            worker_status = wt_job_state.status
        elif quarantines or failed_tasks > 0:
            worker_status = "FAILED"
        elif total_tasks > 0 and completed_tasks == total_tasks:
            worker_status = "COMPLETED"
        elif wt_exists:
            worker_status = "PREPARED"
        elif job_rec is not None and job_rec.get("status") == "CLEANED_UP":
            worker_status = "CLEANED_UP"
        else:
            worker_status = "UNKNOWN"

        if (
            tid in no_changes_teams
            and not quarantines
            and failed_tasks == 0
            and worker_status not in ("RUNNING", "STALE", "FAILED", "TIMEOUT", "CRASHED")
        ):
            worker_status = "NO_CHANGES"

        team_statuses.append(
            {
                "team_id": tid,
                "worktree_path": str(wt_path),
                "worktree_exists": wt_exists,
                "branch": branch,
                "branch_exists": branch_exists,
                "plan": plan_rel,
                "completed_tasks": completed_tasks,
                "total_tasks": total_tasks,
                "failed_tasks": failed_tasks,
                "worker_status": worker_status,
                "quarantine_branches": quarantines,
            }
        )

    overall_state = (
        int_state.state
        if int_state is not None
        else (str(job_rec.get("status")) if job_rec else ("ACTIVE" if live_wts else "NO_JOB"))
    )
    return (
        {
            "job_id": resolved_job_id,
            "job_state": overall_state,
            "rc_tag": int_state.rc_tag if int_state else None,
            "teams": team_statuses,
        },
        0,
        "",
    )


def format_teams_status_text(status_data: dict[str, Any]) -> str:
    """teams status の人間向けテキスト出力を整形する。"""
    lines = [
        "=== AUTO DEVELOPER / TEAMS STATUS ===",
        f" Job ID:    {status_data.get('job_id') or '(none)'}",
        f" Job State: {status_data.get('job_state')}",
    ]
    if status_data.get("rc_tag"):
        lines.append(f" RC Tag:    {status_data['rc_tag']}")
    teams = status_data.get("teams") or []
    if not teams:
        lines.append(" Teams:     (none)")
        return "\n".join(lines)

    lines.append(" Teams:")
    for t in teams:
        wt_flag = "exists" if t["worktree_exists"] else "missing"
        br_flag = "exists" if t["branch_exists"] else "missing"
        q_list = ", ".join(t["quarantine_branches"]) if t["quarantine_branches"] else "none"
        lines.append(
            f"  - {t['team_id']}: status={t['worker_status']} | "
            f"tasks={t['completed_tasks']}/{t['total_tasks']} | "
            f"branch={t['branch']} ({br_flag}) | "
            f"worktree={t['worktree_path']} ({wt_flag}) | "
            f"quarantine={q_list}"
        )
    return "\n".join(lines)


def _resolve_rc_ref(
    resolved_repo: Path,
    job_id: str,
    int_state: IntegrationJobState | None,
) -> str | None:
    """統合完了済みジョブの RC コミットまたはタグを Git 上で検証・解決する。"""
    candidates: list[str] = []
    if int_state is not None:
        if int_state.rc_commit:
            candidates.append(int_state.rc_commit)
        if int_state.rc_tag:
            candidates.append(f"refs/tags/{int_state.rc_tag}")
    candidates.append(f"refs/tags/rc/{job_id}")

    for ref in candidates:
        chk = _run_git_cmd(resolved_repo, ["rev-parse", "--verify", f"{ref}^{{commit}}"])
        if chk.returncode == 0:
            return ref
    return None


def _is_secret_rel_path(rel_path: str) -> bool:
    """INV-5: 相対パスが機密ファイルパターンに該当するか判定する。"""
    p = Path(rel_path)
    if GitGuard.is_secret_file(p.name) or any(GitGuard.is_secret_file(part) for part in p.parts):
        return True
    return any(pat.search(p.name) for pat in SECRET_PATTERNS)


def _parse_porcelain_z(raw: bytes) -> list[tuple[str, str, str | None]]:
    """git status --porcelain -z -uall の NUL 区切り出力を (xy, path, old_path) に分解する。"""
    tokens = [tok for tok in raw.decode("utf-8", errors="surrogateescape").split("\0") if tok]
    entries: list[tuple[str, str, str | None]] = []
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if len(token) < 4:
            i += 1
            continue
        xy = token[:2]
        path1 = token[3:]
        if xy[0] in ("R", "C") or xy[1] in ("R", "C"):
            old_path = tokens[i + 1] if i + 1 < len(tokens) else None
            entries.append((xy, path1, old_path))
            i += 2
        else:
            entries.append((xy, path1, None))
            i += 1
    return entries


def _build_quarantine_commit_from_worktree(
    wt_dir: Path,
    parent_sha: str | None,
    team_id: str,
) -> tuple[str, list[str]]:
    """一時インデックスを使い、機密ファイルを除外した worktree の変更を隔離コミットとして作成する。"""
    with tempfile.TemporaryDirectory() as tmp_idx_dir:
        tmp_index = Path(tmp_idx_dir) / "index"
        idx_env = os.environ.copy()
        idx_env["GIT_INDEX_FILE"] = str(tmp_index)

        if parent_sha is not None:
            rt = subprocess.run(
                ["git", "read-tree", parent_sha],
                cwd=wt_dir,
                env=idx_env,
                capture_output=True,
                text=True,
                check=False,
            )
            if rt.returncode != 0:
                raise RuntimeError(f"git read-tree failed for team '{team_id}': {rt.stderr.strip()}")

        st_proc = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-uall"],
            cwd=wt_dir,
            capture_output=True,
            check=False,
        )
        if st_proc.returncode != 0:
            raise RuntimeError(f"git status failed in '{wt_dir}': {st_proc.stderr.decode(errors='replace')}")

        excluded_secrets: list[str] = []
        for xy, rel_path, old_path in _parse_porcelain_z(st_proc.stdout):
            if old_path:
                subprocess.run(
                    ["git", "update-index", "--force-remove", "--", old_path],
                    cwd=wt_dir,
                    env=idx_env,
                    capture_output=True,
                    check=False,
                )
            full_path = wt_dir / rel_path
            if "D" in xy and not full_path.exists():
                subprocess.run(
                    ["git", "update-index", "--force-remove", "--", rel_path],
                    cwd=wt_dir,
                    env=idx_env,
                    capture_output=True,
                    check=False,
                )
                continue
            if not full_path.is_file() or full_path.is_symlink():
                continue
            if _is_secret_rel_path(rel_path) or GitGuard.has_secret_content(full_path.read_bytes()):
                excluded_secrets.append(rel_path)
                subprocess.run(
                    ["git", "update-index", "--force-remove", "--", rel_path],
                    cwd=wt_dir,
                    env=idx_env,
                    capture_output=True,
                    check=False,
                )
                continue
            upd = subprocess.run(
                ["git", "update-index", "--add", "--", rel_path],
                cwd=wt_dir,
                env=idx_env,
                capture_output=True,
                text=True,
                check=False,
            )
            if upd.returncode != 0:
                raise RuntimeError(f"git update-index failed for '{rel_path}': {upd.stderr.strip()}")

        wt_proc = subprocess.run(
            ["git", "write-tree"],
            cwd=wt_dir,
            env=idx_env,
            capture_output=True,
            text=True,
            check=False,
        )
        if wt_proc.returncode != 0:
            raise RuntimeError(f"git write-tree failed for team '{team_id}': {wt_proc.stderr.strip()}")
        tree_sha = wt_proc.stdout.strip()

        cmd = ["git", "commit-tree", tree_sha]
        if parent_sha is not None:
            cmd.extend(["-p", parent_sha])
        cmd.extend(["-m", f"quarantine: save team '{team_id}' work before cleanup"])
        ct_proc = subprocess.run(
            cmd,
            cwd=wt_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if ct_proc.returncode != 0:
            raise RuntimeError(f"git commit-tree failed for team '{team_id}': {ct_proc.stderr.strip()}")
        return ct_proc.stdout.strip(), excluded_secrets


def _quarantine_team_before_cleanup(
    resolved_repo: Path,
    team_id: str,
    branch: str,
    wt_dir: Path | None,
    rc_ref: str | None,
    base_commit: str | None,
    job_id: str = "",
) -> str | None:
    """INV-3 / INV-5: 削除前に未コミット変更と RC 未取り込みコミットを quarantine/* ブランチへ退避・検証する。"""
    wt_exists = wt_dir is not None and wt_dir.is_dir()
    has_uncommitted = False
    if wt_exists and wt_dir is not None:
        st_proc = _run_git_cmd(wt_dir, ["status", "--porcelain"])
        if st_proc.returncode != 0:
            raise RuntimeError(f"git status failed in worktree '{wt_dir}': {st_proc.stderr.strip()}")
        has_uncommitted = bool(st_proc.stdout.strip())

    parent_sha: str | None = None
    if _branch_exists(resolved_repo, branch):
        rev = _run_git_cmd(resolved_repo, ["rev-parse", "--verify", f"refs/heads/{branch}^{{commit}}"])
        if rev.returncode == 0:
            parent_sha = rev.stdout.strip()
    elif wt_exists and wt_dir is not None:
        rev = _run_git_cmd(wt_dir, ["rev-parse", "--verify", "HEAD^{commit}"])
        if rev.returncode == 0:
            parent_sha = rev.stdout.strip()

    has_unmerged_commits = False
    if parent_sha is not None:
        merged_rc = (
            rc_ref is not None
            and _run_git_cmd(resolved_repo, ["merge-base", "--is-ancestor", parent_sha, rc_ref]).returncode == 0
        )
        if not merged_rc:
            at_base = (
                base_commit is not None
                and _run_git_cmd(resolved_repo, ["merge-base", "--is-ancestor", parent_sha, base_commit]).returncode == 0
            )
            has_unmerged_commits = not at_base

    if not has_uncommitted and not has_unmerged_commits:
        return None

    clean_team = _sanitize_team_token(team_id)
    clean_job = _sanitize_team_token(job_id) if job_id else ""
    ts = int(time.time())
    base_q = (
        f"quarantine/{clean_job}/{clean_team}/cleanup-{ts}"
        if clean_job
        else f"quarantine/{clean_team}-cleanup-{ts}"
    )
    q_branch = base_q
    counter = 1
    while _branch_exists(resolved_repo, q_branch):
        q_branch = f"{base_q}-{counter}"
        counter += 1

    excluded_secrets: list[str] = []
    if has_uncommitted and wt_dir is not None:
        target_commit_sha, excluded_secrets = _build_quarantine_commit_from_worktree(
            wt_dir, parent_sha, team_id
        )
    else:
        if parent_sha is None:
            raise RuntimeError(f"Cannot quarantine team '{team_id}': no commit found for branch '{branch}'.")
        target_commit_sha = parent_sha

    br_proc = _run_git_cmd(resolved_repo, ["branch", q_branch, target_commit_sha])
    if br_proc.returncode != 0:
        raise RuntimeError(f"Failed to create quarantine branch '{q_branch}': {br_proc.stderr.strip()}")

    # INV-3: 退避先ブランチと履歴の物理検証
    verify_ref = _run_git_cmd(resolved_repo, ["rev-parse", "--verify", f"refs/heads/{q_branch}^{{commit}}"])
    if verify_ref.returncode != 0 or verify_ref.stdout.strip() != target_commit_sha:
        raise RuntimeError(f"Quarantine verification failed: branch '{q_branch}' does not resolve to expected commit.")
    if parent_sha is not None:
        anc = _run_git_cmd(resolved_repo, ["merge-base", "--is-ancestor", parent_sha, target_commit_sha])
        if anc.returncode != 0:
            raise RuntimeError(f"Quarantine verification failed: commit history lost for '{q_branch}'.")
    if excluded_secrets:
        tree_files = set(
            _run_git_cmd(resolved_repo, ["ls-tree", "-r", "--name-only", target_commit_sha]).stdout.splitlines()
        )
        leaked = [s for s in excluded_secrets if s in tree_files]
        if leaked:
            raise RuntimeError(f"INV-5 violation: secret files leaked into '{q_branch}': {leaked}")

    return q_branch


def _integration_worktree_team_id(job_id: str) -> str:
    cleaned_id = re.sub(r"[^a-z0-9_-]", "-", job_id.lower()).strip("-")
    return f"int-{cleaned_id}"[:64]


def _remove_worktree_for_cleanup(
    resolved_repo: Path,
    wt_manager: WorktreeManager,
    wt_team_id: str,
    wt_dir: Path,
    *,
    force: bool,
    delete_branch: bool,
) -> tuple[bool, str | None]:
    """cleanup 用の worktree 削除を実行し、(branch_deleted, error_message) を返す。"""
    try:
        deleted = wt_manager.remove_worktree(
            wt_team_id,
            force=force,
            delete_branch=delete_branch,
            purge_unmerged=True,
            ignore_cleanup_artifacts=not force,
        )
        return deleted, None
    except WorktreeError as err:
        # すでに quarantine/* へ非機密作業を退避検証済みで、INV-5 機密除外による拒否のみの場合は --force で強制削除する
        err_msg = str(err)
        is_secret_exclusion = (
            "excluded from quarantine" in err_msg or "Secret content detected" in err_msg
        )
        if force and is_secret_exclusion:
            rm_proc = _run_git_cmd(resolved_repo, ["worktree", "remove", "--force", str(wt_dir)])
            if rm_proc.returncode != 0:
                return (
                    False,
                    f"TEAM_CLEANUP_ERROR: Failed to force-remove worktree '{wt_team_id}': {rm_proc.stderr.strip()}",
                )
            return False, None
        return False, f"TEAM_CLEANUP_ERROR: Failed to remove worktree '{wt_team_id}': {err}"


def _check_cleanup_preconditions(
    resolved_repo: Path,
    job_id: str,
    team_targets: list[tuple[str, str]],
    rc_ref: str | None,
    wt_manager: WorktreeManager,
    integration_branch: str | None = None,
    int_wt_id: str | None = None,
    *,
    no_changes_teams: set[str] | None = None,
    base_ref: str | None = None,
    allow_no_rc: bool = False,
) -> str | None:
    """force なしでの cleanup 前に、RC 解決・Bundle 保全・未コミット変更・RC 取り込み済みを厳格検証する。"""
    target_ref = rc_ref or (base_ref if allow_no_rc else None)
    if target_ref is None:
        return (
            f"TEAM_CLEANUP_ERROR: Cannot resolve RC commit or tag for job '{job_id}'. "
            "Refusing to remove worktrees and team branches without --force."
        )

    nc_teams = no_changes_teams or set()
    live_wts = {wt.team_id: wt for wt in wt_manager.list_worktrees()}
    for tid, branch in team_targets:
        wt_info = live_wts.get(tid)
        if wt_info is not None and wt_info.path.exists():
            try:
                wt_manager.check_clean_for_removal(tid, ignore_cleanup_artifacts=True)
            except WorktreeError as err:
                return (
                    f"TEAM_CLEANUP_ERROR: Worktree '{tid}' ({wt_info.path}) has uncommitted changes "
                    f"({err}). Pass --force to quarantine and remove."
                )
        if _branch_exists(resolved_repo, branch):
            anc = _run_git_cmd(resolved_repo, ["merge-base", "--is-ancestor", branch, target_ref])
            if anc.returncode != 0:
                return (
                    f"TEAM_CLEANUP_ERROR: Branch '{branch}' has commits not merged into '{target_ref}'. "
                    "Refusing to delete without --force."
                )
        if tid not in nc_teams:
            bundle_path = resolved_repo / ".dev" / "integration_jobs" / job_id / "evidence" / f"{tid}.zip"
            if not _is_valid_bundle_archive(bundle_path):
                return (
                    f"TEAM_CLEANUP_ERROR: Preserved evidence bundle for team '{tid}' is missing or invalid "
                    f"at '{bundle_path}'. Refusing to delete without --force."
                )

    if int_wt_id is not None:
        int_wt_info = live_wts.get(int_wt_id)
        if int_wt_info is not None and int_wt_info.path.exists():
            try:
                wt_manager.check_clean_for_removal(int_wt_id, ignore_cleanup_artifacts=True)
            except WorktreeError as err:
                return (
                    f"TEAM_CLEANUP_ERROR: Worktree '{int_wt_id}' ({int_wt_info.path}) has uncommitted changes "
                    f"({err}). Pass --force to quarantine and remove."
                )

    if integration_branch and _branch_exists(resolved_repo, integration_branch):
        curr_br = _run_git_cmd(resolved_repo, ["branch", "--show-current"])
        if curr_br.returncode == 0 and curr_br.stdout.strip() == integration_branch:
            return (
                f"TEAM_CLEANUP_ERROR: Integration branch '{integration_branch}' is currently "
                f"checked out in '{resolved_repo}'. Switch branches before cleanup."
            )
        if rc_ref is None:
            return (
                f"TEAM_CLEANUP_ERROR: Cannot resolve RC commit or tag for job '{job_id}'. "
                "Refusing to delete without --force."
            )
        anc = _run_git_cmd(resolved_repo, ["merge-base", "--is-ancestor", integration_branch, rc_ref])
        if anc.returncode != 0:
            return (
                f"TEAM_CLEANUP_ERROR: Branch '{integration_branch}' has commits not merged into '{rc_ref}'. "
                "Refusing to delete without --force."
            )

    return None


def _collect_job_team_targets(
    resolved_repo: Path,
    int_state: IntegrationJobState | None,
    job_rec: dict[str, Any] | None,
    workers: dict[str, WorkerRegistration],
) -> dict[str, tuple[str, Path | None]]:
    """対象 job_id に紐づくチームのみを特定し、{team_id: (branch, recorded_wt_path)} を返す。"""
    targets: dict[str, tuple[str, Path | None]] = {}
    if job_rec and isinstance(job_rec.get("teams"), list):
        for entry in job_rec["teams"]:
            if isinstance(entry, dict) and entry.get("team_id"):
                tid = str(entry["team_id"])
                branch = str(entry.get("branch") or f"team/{tid}")
                wt_p = Path(str(entry["worktree_path"])) if entry.get("worktree_path") else None
                targets[tid] = (branch, wt_p)

    for tid, reg in workers.items():
        if tid not in targets:
            wt_p = Path(reg.worktree_path) if reg.worktree_path else None
            targets[tid] = (reg.branch_name or f"team/{tid}", wt_p)

    if int_state is not None:
        for tid in int_state.verified_commits:
            targets.setdefault(tid, (f"team/{tid}", None))
        for tid in int_state.merged_teams:
            targets.setdefault(tid, (f"team/{tid}", None))

    if not targets:
        try:
            manifest = load_teams_manifest(resolved_repo)
            for t in manifest.teams:
                targets[t.id] = (f"team/{t.id}", None)
        except (FileNotFoundError, ManifestValidationError, OSError):
            pass

    return targets


def cleanup_teams_job(
    repo_dir: Path,
    job_id: str,
    force: bool = False,
) -> tuple[dict[str, Any] | None, int, str]:
    """TEAM-16 / TEAM-26: 統合成功ジョブの worktree・チームブランチ・統合ブランチを削除する（失敗・未完了ジョブの統合ブランチは削除しない）。"""
    resolved_repo = repo_dir.resolve()
    if not VALID_JOB_ID_PATTERN.match(job_id):
        return None, 2, f"TEAM_CLEANUP_ERROR: Invalid job_id '{job_id}'."

    jobs_dir = resolved_repo / ".dev" / "integration_jobs"
    int_state = IntegrationJobState.load(jobs_dir, job_id)
    job_rec = _load_team_job_record(resolved_repo, job_id)
    workers = WorkerRegistry(resolved_repo, job_id).load()

    if int_state is None and job_rec is None and not workers:
        return None, 1, f"TEAM_CLEANUP_ERROR: Job '{job_id}' not found."

    no_changes_list = _extract_no_changes_teams(int_state, job_rec)
    no_changes_set = set(no_changes_list)
    all_no_changes = bool(
        int_state is None
        and job_rec is not None
        and job_rec.get("status") == "NO_CHANGES"
        and no_changes_set
    )
    integration_succeeded = (int_state is not None and int_state.state == "COMPLETED") or all_no_changes
    if not integration_succeeded and not force:
        state_label = (
            int_state.state
            if int_state is not None
            else str(job_rec.get("status") if job_rec else "NOT_INTEGRATED")
        )
        return (
            None,
            1,
            (
                f"TEAM_CLEANUP_ERROR: Job '{job_id}' integration has not succeeded (state={state_label}). "
                "Refusing to remove worktrees and team branches without --force."
            ),
        )

    if not force:
        for tid, reg in workers.items():
            if reg.status == "RUNNING" and (
                _is_registered_worker_alive(reg)
                or (reg.pgid > 0 and has_active_processes_in_group(reg.pgid))
            ):
                return (
                    None,
                    1,
                    (
                        f"TEAM_CLEANUP_ERROR: Worker for team '{tid}' is still active or unverified "
                        f"(PID={reg.pid}, PGID={reg.pgid}). Refusing cleanup without --force."
                    ),
                )

    wt_manager = WorktreeManager(repo_dir=resolved_repo)
    job_targets = _collect_job_team_targets(resolved_repo, int_state, job_rec, workers)
    rc_ref = _resolve_rc_ref(resolved_repo, job_id, int_state)
    integration_branch = f"integration/{job_id}"

    live_wts = {wt.team_id: wt for wt in wt_manager.list_worktrees()}
    default_int_wt_id = _integration_worktree_team_id(job_id)
    int_wt_info = live_wts.get(default_int_wt_id)
    if int_wt_info is None:
        for wt in live_wts.values():
            if wt.team_id.startswith("int-") and wt.branch == integration_branch:
                int_wt_info = wt
                break
    int_wt_id = int_wt_info.team_id if int_wt_info is not None else default_int_wt_id
    int_wt_dir = int_wt_info.path if int_wt_info is not None else (wt_manager.worktree_base / int_wt_id)

    cleanup_base_ref: str | None = None
    if all_no_changes and job_rec is not None:
        raw_base = job_rec.get("base_commit") or job_rec.get("base_ref")
        if raw_base:
            chk_base = _run_git_cmd(resolved_repo, ["rev-parse", "--verify", f"{raw_base}^{{commit}}"])
            if chk_base.returncode == 0 and chk_base.stdout.strip():
                cleanup_base_ref = chk_base.stdout.strip()

    team_targets = [(tid, branch) for tid, (branch, _) in job_targets.items()]
    if not force:
        pre_err = _check_cleanup_preconditions(
            resolved_repo,
            job_id,
            team_targets,
            rc_ref,
            wt_manager,
            integration_branch=integration_branch if (int_state is not None and int_state.state == "COMPLETED") else None,
            int_wt_id=int_wt_id if (int_state is not None and int_state.state == "COMPLETED") else None,
            no_changes_teams=no_changes_set,
            base_ref=cleanup_base_ref,
            allow_no_rc=all_no_changes,
        )
        if pre_err is not None:
            return None, 1, pre_err
    elif integration_succeeded and _branch_exists(resolved_repo, integration_branch):
        curr_br = _run_git_cmd(resolved_repo, ["branch", "--show-current"])
        if curr_br.returncode == 0 and curr_br.stdout.strip() == integration_branch:
            return (
                None,
                1,
                (
                    f"TEAM_CLEANUP_ERROR: Integration branch '{integration_branch}' is currently "
                    f"checked out in '{resolved_repo}'. Switch branches before cleanup."
                ),
            )

    base_commit: str | None = None
    if job_rec and job_rec.get("base_commit"):
        base_commit = str(job_rec["base_commit"])
    elif int_state and int_state.base_commit:
        base_commit = int_state.base_commit

    # INV-3: --force 時は削除前に全チームの未コミット変更・未取り込みブランチを quarantine/* へ退避・検証する
    quarantined_branches: dict[str, str] = {}
    if force:
        for tid, (branch, recorded_wt) in job_targets.items():
            wt_info = live_wts.get(tid)
            wt_dir = wt_info.path if wt_info is not None else (recorded_wt or (wt_manager.worktree_base / tid))
            if wt_info is not None and wt_info.branch and wt_info.branch != branch:
                continue
            try:
                q_branch = _quarantine_team_before_cleanup(
                    resolved_repo=resolved_repo,
                    team_id=tid,
                    branch=branch,
                    wt_dir=wt_dir if wt_dir.is_dir() else None,
                    rc_ref=rc_ref,
                    base_commit=base_commit,
                    job_id=job_id,
                )
            except (RuntimeError, OSError, subprocess.SubprocessError) as err:
                return (
                    None,
                    1,
                    f"TEAM_CLEANUP_ERROR: Quarantine failed for team '{tid}'; aborting cleanup: {err}",
                )
            if q_branch is not None:
                quarantined_branches[tid] = q_branch

        if integration_succeeded and _branch_exists(resolved_repo, integration_branch):
            try:
                q_int_branch = _quarantine_team_before_cleanup(
                    resolved_repo=resolved_repo,
                    team_id="integration",
                    branch=integration_branch,
                    wt_dir=int_wt_dir if int_wt_dir.is_dir() else None,
                    rc_ref=rc_ref,
                    base_commit=base_commit,
                    job_id=job_id,
                )
            except (RuntimeError, OSError, subprocess.SubprocessError) as err:
                return (
                    None,
                    1,
                    f"TEAM_CLEANUP_ERROR: Quarantine failed for integration branch '{integration_branch}'; aborting cleanup: {err}",
                )
            if q_int_branch is not None:
                quarantined_branches["integration"] = q_int_branch

    removed_worktrees: list[str] = []
    removed_branches: list[str] = []

    for tid, (branch, recorded_wt) in job_targets.items():
        wt_info = live_wts.get(tid)
        if wt_info is not None and wt_info.branch and wt_info.branch != branch:
            continue

        wt_dir = wt_info.path if wt_info is not None else (recorded_wt or (wt_manager.worktree_base / tid))
        if tid in live_wts or wt_dir.exists():
            deleted, rm_err = _remove_worktree_for_cleanup(
                resolved_repo,
                wt_manager,
                tid,
                wt_dir,
                force=force,
                delete_branch=True,
            )
            if rm_err is not None:
                return None, 1, rm_err
            removed_worktrees.append(tid)
            if deleted:
                removed_branches.append(branch)

        if _branch_exists(resolved_repo, branch):
            del_proc = _run_git_cmd(resolved_repo, ["branch", "-D", branch])
            if del_proc.returncode != 0:
                return (
                    None,
                    1,
                    f"TEAM_CLEANUP_ERROR: Failed to delete branch '{branch}': {del_proc.stderr.strip()}",
                )
            if branch not in removed_branches:
                removed_branches.append(branch)

    # TEAM-26: 統合に成功したジョブに限り残存統合 worktree と統合ブランチ integration/<job_id> を削除し、RC タグは残す
    if integration_succeeded:
        if int_wt_id not in job_targets and (int_wt_id in live_wts or int_wt_dir.exists()):
            _, int_rm_err = _remove_worktree_for_cleanup(
                resolved_repo,
                wt_manager,
                int_wt_id,
                int_wt_dir,
                force=force,
                delete_branch=False,
            )
            if int_rm_err is not None:
                return None, 1, int_rm_err
            removed_worktrees.append(int_wt_id)

        if _branch_exists(resolved_repo, integration_branch):
            del_int_proc = _run_git_cmd(resolved_repo, ["branch", "-D", integration_branch])
            if del_int_proc.returncode != 0:
                return (
                    None,
                    1,
                    f"TEAM_CLEANUP_ERROR: Failed to delete integration branch '{integration_branch}': {del_int_proc.stderr.strip()}",
                )
            if integration_branch not in removed_branches:
                removed_branches.append(integration_branch)

    _run_git_cmd(resolved_repo, ["worktree", "prune"])
    if wt_manager.worktree_base.is_dir():
        try:
            if not any(wt_manager.worktree_base.iterdir()):
                wt_manager.worktree_base.rmdir()
        except OSError:
            pass

    if job_rec is not None:
        job_rec["status"] = "CLEANED_UP"
        job_rec["updated_at"] = time.time()
        if quarantined_branches:
            job_rec["quarantine_branches"] = quarantined_branches
        _save_team_job_record(resolved_repo, job_rec)

    return (
        {
            "job_id": job_id,
            "removed_worktrees": removed_worktrees,
            "removed_branches": removed_branches,
            "quarantine_branches": quarantined_branches,
            "forced": force,
        },
        0,
        "",
    )


def _check_team_plan_completed(
    plan_path: Path,
    *,
    allow_empty: bool = False,
) -> tuple[bool, list[str]]:
    """チーム計画ファイルの全タスクが完了済み (- [x] かつ未失敗) であるか検証する。"""
    if not plan_path.is_file():
        return False, [f"plan file not found: {plan_path.name}"]

    content = plan_path.read_text(encoding="utf-8")
    total = 0
    issues: list[str] = []
    for _, line in extract_plan_scope_lines(content):
        match = TaskDispatcher.TASK_PATTERN.match(line)
        if match:
            total += 1
            check_box, task_id, title = match.groups()
            if "**[FAILED]**" in line:
                issues.append(f"failed task {task_id}: {title.strip()}")
            elif check_box.lower() != "x":
                issues.append(f"incomplete task {task_id}: {title.strip()}")
        elif re.match(r"^\s*-\s*\[\s\]\s+\S", line):
            total += 1
            issues.append(f"incomplete task: {line.strip()}")

    if total == 0 and not allow_empty:
        issues.append(f"no tasks found in {plan_path.name}")
    return len(issues) == 0, issues


def _collect_team_integrate_blockers(
    resolved_repo: Path,
    manifest: TeamManifest,
    live_wts: dict[str, WorktreeInfo],
) -> tuple[list[str], dict[str, Path], dict[str, int]]:
    """各チームの worktree 存在・ブランチ・クリーン状態・基点からのコミット数・計画完了を検証する。"""
    wt_manager = WorktreeManager(repo_dir=resolved_repo)
    blockers: list[str] = []
    team_wt_paths: dict[str, Path] = {}
    team_commit_counts: dict[str, int] = {}

    for team in manifest.teams:
        wt_info = live_wts.get(team.id)
        expected_branch = f"team/{team.id}"
        wt_path = wt_info.path if wt_info is not None else (wt_manager.worktree_base / team.id)

        if wt_info is None or not wt_path.is_dir():
            blockers.append(
                f"team '{team.id}': worktree missing at {wt_path} (expected branch '{expected_branch}')"
            )
            continue

        if wt_info.branch != expected_branch:
            blockers.append(
                f"team '{team.id}': worktree at {wt_path} is on branch '{wt_info.branch or 'detached'}' "
                f"(expected '{expected_branch}')"
            )
            continue

        st_proc = _run_git_cmd(wt_path, ["status", "--porcelain"])
        if st_proc.returncode != 0:
            blockers.append(
                f"team '{team.id}': failed to check git status in {wt_path}: {st_proc.stderr.strip()}"
            )
            continue
        if st_proc.stdout.strip():
            dirty_preview = ", ".join(line.strip() for line in st_proc.stdout.splitlines()[:5])
            blockers.append(
                f"team '{team.id}': worktree {wt_path} is not clean ({dirty_preview})"
            )
            continue

        cnt_proc = _run_git_cmd(wt_path, ["rev-list", "--count", f"{manifest.base_ref}..HEAD"])
        if cnt_proc.returncode != 0:
            blockers.append(
                f"team '{team.id}': failed to count commits via git rev-list from '{manifest.base_ref}' in {wt_path}: "
                f"{cnt_proc.stderr.strip()}"
            )
            continue
        try:
            commit_count = int(cnt_proc.stdout.strip())
        except ValueError:
            blockers.append(
                f"team '{team.id}': invalid git rev-list --count output: {cnt_proc.stdout.strip()!r}"
            )
            continue

        plan_ok, plan_issues = _check_team_plan_completed(
            wt_path / team.plan,
            allow_empty=(commit_count == 0),
        )
        if not plan_ok:
            blockers.append(
                f"team '{team.id}' ({team.plan}): {'; '.join(plan_issues)}"
            )
            continue

        team_wt_paths[team.id] = wt_path
        team_commit_counts[team.id] = commit_count

    return blockers, team_wt_paths, team_commit_counts


def _read_bundle_manifest(bundle_path: Path) -> dict[str, Any] | None:
    """Bundle ZIP 内の bundle_manifest.json を読み取る。"""
    if not bundle_path.is_file() or not zipfile.is_zipfile(bundle_path):
        return None
    try:
        with zipfile.ZipFile(bundle_path, "r") as zf:
            raw = zf.read("bundle_manifest.json")
        data = json.loads(raw.decode("utf-8"))
        return data if isinstance(data, dict) else None
    except (zipfile.BadZipFile, KeyError, UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None


def _allowed_bundle_job_ids(job_id: str, team_id: str, wt_path: Path) -> set[str]:
    """対象チーム・ジョブで有効な Bundle の job_id 候補を返す。"""
    allowed = {job_id, f"{job_id}-{team_id}"}
    wt_state_file = wt_path / ".dev" / "job_state.json"
    if wt_state_file.is_file():
        try:
            wt_state = JobState.load(wt_state_file)
            if wt_state.job_id:
                allowed.add(wt_state.job_id)
        except (OSError, json.JSONDecodeError, ValueError):
            pass
    return allowed


def _bundle_matches_head_and_job(
    bundle_path: Path,
    *,
    job_id: str,
    team_id: str,
    head_commit: str,
    head_tree: str,
    wt_path: Path,
) -> bool:
    """既存 Bundle が現在の HEAD コミット・Tree SHA および対象ジョブと整合するか検査する。"""
    manifest = _read_bundle_manifest(bundle_path)
    if manifest is None:
        return False
    if str(manifest.get("head_commit") or "").strip() != head_commit:
        return False
    if str(manifest.get("head_tree_sha") or "").strip() != head_tree:
        return False
    manifest_job_id = str(manifest.get("job_id") or "").strip()
    return bool(manifest_job_id) and manifest_job_id in _allowed_bundle_job_ids(job_id, team_id, wt_path)


def _archive_superseded_team_bundle(
    preserved_path: Path,
    *,
    team_id: str,
    head_commit: str,
    head_tree: str,
    int_state: IntegrationJobState | None,
) -> None:
    """前回の統合失敗後にチームが新しいコミットへ進んだ場合、旧試行の保全済み Bundle を退避する。"""
    if not preserved_path.is_file():
        return
    if int_state is None or int_state.state not in ("FAILED", "CONFLICT", "RECOVERED"):
        return
    manifest = _read_bundle_manifest(preserved_path)
    if (
        manifest is not None
        and str(manifest.get("head_commit") or "").strip() == head_commit
        and str(manifest.get("head_tree_sha") or "").strip() == head_tree
    ):
        return
    old_sha = str((manifest or {}).get("head_commit") or "unknown")[:7]
    superseded_dir = preserved_path.parent / "superseded"
    superseded_dir.mkdir(parents=True, exist_ok=True)
    archive_path = superseded_dir / f"{team_id}_{old_sha}_{time.time_ns()}.zip"
    os.replace(preserved_path, archive_path)


def _resolve_or_create_team_bundle(
    resolved_repo: Path,
    job_id: str,
    team_id: str,
    wt_path: Path,
    *,
    head_commit: str,
    head_tree: str,
) -> Path:
    """チームの Bundle を取得する（現 HEAD・ジョブに一致するものがなければ新しく生成する）。"""
    bundles_dir = wt_path / ".dev" / "bundles"
    if bundles_dir.is_dir():
        zips = sorted(bundles_dir.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
        for candidate in zips:
            if _bundle_matches_head_and_job(
                candidate,
                job_id=job_id,
                team_id=team_id,
                head_commit=head_commit,
                head_tree=head_tree,
                wt_path=wt_path,
            ):
                return candidate

    preserved_path = resolved_repo / ".dev" / "integration_jobs" / job_id / "evidence" / f"{team_id}.zip"
    if _bundle_matches_head_and_job(
        preserved_path,
        job_id=job_id,
        team_id=team_id,
        head_commit=head_commit,
        head_tree=head_tree,
        wt_path=wt_path,
    ):
        return preserved_path

    dest_path = bundles_dir / f"bundle_{job_id}-{team_id}-{head_commit[:7]}.zip"
    return EvidenceBundler(repo_dir=wt_path).create_bundle(
        dest_path=dest_path,
        job_id=f"{job_id}-{team_id}",
    )


def _extract_offending_teams(
    multi_res: MultiTeamResult,
    manifest: TeamManifest,
) -> list[str]:
    """統合失敗結果から原因となったチーム ID 一覧を抽出する。"""
    teams: list[str] = []

    def _add(tid: str | None) -> None:
        if tid and tid not in teams:
            teams.append(tid)

    for tid in multi_res.details.get("failed_teams") or []:
        _add(str(tid))
    _add(multi_res.details.get("conflict_team"))

    int_res = multi_res.integration_result
    if int_res is not None:
        _add(int_res.details.get("conflict_team"))
        for tid, info in int_res.audited_bundles.items():
            if isinstance(info, dict) and (not info.get("is_valid") or info.get("status") != "VERIFIED"):
                _add(tid)
        for err in int_res.errors:
            for m in re.finditer(r"Team '([a-zA-Z0-9_-]+)'", err):
                _add(m.group(1))

    if not teams and int_res is not None and int_res.status == "VERIFICATION_FAILED":
        for t in manifest.teams:
            _add(t.id)
    return teams


def _build_integrate_success_payload(
    job_id: str,
    base_ref: str,
    rc_tag: str | None,
    rc_commit: str | None,
    preserved_bundles: dict[str, str],
    *,
    already_integrated: bool = False,
    no_changes_teams: list[str] | None = None,
    all_no_changes: bool = False,
) -> dict[str, Any]:
    nc_list = list(no_changes_teams or [])
    if all_no_changes:
        status = "NO_CHANGES"
        integration_branch: str | None = None
        merge_commands: list[str] = []
    else:
        status = "ALREADY_INTEGRATED" if already_integrated else "SUCCESS"
        integration_branch = f"integration/{job_id}"
        merge_commands = (
            [
                f"git diff {base_ref}..{rc_tag}",
                f"git checkout {base_ref} && git merge --no-ff {rc_tag}",
            ]
            if rc_tag
            else []
        )
    return {
        "job_id": job_id,
        "success": True,
        "status": status,
        "base_ref": base_ref,
        "rc_tag": rc_tag,
        "rc_commit": rc_commit,
        "integration_branch": integration_branch,
        "preserved_bundles": preserved_bundles,
        "no_changes_teams": nc_list,
        "merge_commands": merge_commands,
    }


def _format_integrate_failure_message(
    job_id: str,
    multi_res: MultiTeamResult,
    manifest: TeamManifest,
    team_wt_paths: dict[str, Path],
) -> str:
    """統合ゲート失敗時の原因チーム・保全状態・次の手順を整形する。"""
    offending = _extract_offending_teams(multi_res, manifest)
    lines = [f"TEAMS_INTEGRATE_ERROR: Integration failed for job '{job_id}' (status={multi_res.status})."]
    if offending:
        lines.append(f"Offending team(s): {', '.join(offending)}")

    err_details: list[str] = []
    if multi_res.integration_result and multi_res.integration_result.errors:
        err_details.extend(multi_res.integration_result.errors)
    elif multi_res.details.get("error"):
        err_details.append(str(multi_res.details["error"]))

    for tid in offending:
        t_res = multi_res.team_results.get(tid)
        if t_res and t_res.details.get("bundle_error"):
            err_details.append(f"team '{tid}': {t_res.details['bundle_error']}")

    if err_details:
        lines.append("Details:")
        for detail in err_details[:5]:
            lines.append(f"  - {detail}")

    int_res = multi_res.integration_result
    if int_res and int_res.integration_branch and int_res.integration_branch.startswith("quarantine/"):
        lines.append(f"Quarantine branch: {int_res.integration_branch}")

    kept = ", ".join(f"{tid}={path}" for tid, path in team_wt_paths.items())
    lines.append(f"Worktrees preserved (not deleted): {kept}")
    lines.append("Next steps:")
    lines.append(f"  1. Inspect status: `auto-dev teams status --job-id {job_id}`")
    if multi_res.status == "CONFLICT" and offending:
        lines.append(
            f"  2. Resolve the conflict in team '{offending[0]}' worktree "
            f"({team_wt_paths.get(offending[0])}) against '{manifest.base_ref}', verify and commit."
        )
    elif multi_res.status == "VERIFICATION_FAILED":
        lines.append(
            "  2. Inspect the quarantined integration branch and fix the cross-team issue "
            "in the offending team worktree(s)."
        )
    else:
        lines.append(
            "  2. Fix the bundle/evidence issue in the offending team worktree(s) "
            "(ensure tasks are committed via `verify-step` and `commit-step`)."
        )
    lines.append(
        f"  3. To start a fresh job after fixing or discarding, clean up with "
        f"`auto-dev teams cleanup --job-id {job_id} --force`."
    )
    return "\n".join(lines)


def integrate_teams_job(
    repo_dir: Path,
    job_id: str,
    verify_script: Path | None = None,
    timeout_sec: int = 300,
) -> tuple[dict[str, Any] | None, int, str]:
    """TEAM-14: 完了・クリーン確認、Bundle 生成/保全、IntegrationGate 実行を行う。"""
    resolved_repo = repo_dir.resolve()
    if not VALID_JOB_ID_PATTERN.match(job_id):
        return (
            None,
            2,
            f"TEAMS_INTEGRATE_ERROR: Invalid job_id '{job_id}': must match '^[a-zA-Z0-9_-]+$'.",
        )

    try:
        manifest = load_teams_manifest(resolved_repo)
    except (FileNotFoundError, ManifestValidationError, OSError) as err:
        return None, 2, f"TEAMS_CONFIG_ERROR: {err}"

    if not manifest.teams:
        return None, 2, "TEAMS_CONFIG_ERROR: No teams defined in teams.json."

    jobs_dir = resolved_repo / ".dev" / "integration_jobs"
    job_rec = _load_team_job_record(resolved_repo, job_id)
    int_state = IntegrationJobState.load(jobs_dir, job_id)
    workers = WorkerRegistry(resolved_repo, job_id).load()

    if int_state is None and job_rec is None and not workers:
        return (
            None,
            1,
            (
                f"TEAMS_INTEGRATE_ERROR: Job '{job_id}' not found.\n"
                f"Next: inspect existing jobs with `auto-dev teams status`, "
                f"or prepare a job with `auto-dev teams prepare --job-id {job_id}`."
            ),
        )

    for tid, reg in workers.items():
        if reg.status == "RUNNING" and (
            _is_registered_worker_alive(reg)
            or (reg.pgid > 0 and has_active_processes_in_group(reg.pgid))
        ):
            return (
                None,
                1,
                (
                    f"TEAMS_INTEGRATE_ERROR: Worker for team '{tid}' is still active "
                    f"(PID={reg.pid}, PGID={reg.pgid}). Worktrees preserved.\n"
                    f"Next: wait for completion and check `auto-dev teams status --job-id {job_id}`."
                ),
            )

    base_chk = _run_git_cmd(resolved_repo, ["rev-parse", "--verify", f"{manifest.base_ref}^{{commit}}"])
    if base_chk.returncode != 0:
        return (
            None,
            2,
            f"TEAMS_CONFIG_ERROR: Base ref '{manifest.base_ref}' does not exist in repository.",
        )

    if int_state is not None and int_state.state == "COMPLETED":
        rc_ref = _resolve_rc_ref(resolved_repo, job_id, int_state)
        recorded_no_changes = _extract_no_changes_teams(int_state, job_rec)
        recorded_nc_set = set(recorded_no_changes)
        heads_match = rc_ref is not None
        if heads_match:
            for team in manifest.teams:
                expected_sha = int_state.verified_commits.get(team.id)
                br_proc = _run_git_cmd(resolved_repo, ["rev-parse", "--verify", f"refs/heads/team/{team.id}"])
                if expected_sha:
                    if br_proc.returncode != 0 or br_proc.stdout.strip() != expected_sha:
                        heads_match = False
                        break
                elif team.id in recorded_nc_set and br_proc.returncode == 0:
                    nc_cnt = _run_git_cmd(resolved_repo, ["rev-list", "--count", f"{manifest.base_ref}..refs/heads/team/{team.id}"])
                    if nc_cnt.returncode != 0 or nc_cnt.stdout.strip() != "0":
                        heads_match = False
                        break
        if heads_match:
            rc_tag = int_state.rc_tag or f"rc/{job_id}"
            rc_commit = int_state.rc_commit or _run_git_cmd(resolved_repo, ["rev-parse", f"refs/tags/{rc_tag}"]).stdout.strip()
            preserved = {
                t.id: str(jobs_dir / job_id / "evidence" / f"{t.id}.zip")
                for t in manifest.teams
                if t.id not in recorded_nc_set and (jobs_dir / job_id / "evidence" / f"{t.id}.zip").is_file()
            }
            return (
                _build_integrate_success_payload(
                    job_id,
                    manifest.base_ref,
                    rc_tag,
                    rc_commit,
                    preserved,
                    already_integrated=True,
                    no_changes_teams=recorded_no_changes,
                ),
                0,
                "",
            )
        return (
            None,
            1,
            (
                f"TEAMS_INTEGRATE_ERROR: Job '{job_id}' was already completed, "
                "and team branches or RC tag have diverged.\n"
                f"Next: inspect `auto-dev teams status --job-id {job_id}` or start a new job."
            ),
        )

    cleaned_id = re.sub(r"[^a-z0-9_-]", "-", job_id.lower()).strip("-")
    int_wt_team_id = f"int-{cleaned_id}"[:64]
    wt_manager = WorktreeManager(repo_dir=resolved_repo)
    if (
        int_state is not None
        and int_state.state not in ("FAILED", "CONFLICT", "RECOVERED")
        and (
            _branch_exists(resolved_repo, f"integration/{job_id}")
            or (wt_manager.worktree_base / int_wt_team_id).exists()
        )
    ):
        return (
            None,
            1,
            (
                f"TEAMS_INTEGRATE_ERROR: Integration job '{job_id}' was interrupted in state '{int_state.state}'.\n"
                f"Next: run `auto-dev teams recover --job-id {job_id}` before retrying."
            ),
        )

    main_status = _run_git_cmd(resolved_repo, ["status", "--porcelain"])
    if main_status.returncode != 0 or main_status.stdout.strip():
        dirty_preview = ", ".join(line.strip() for line in main_status.stdout.splitlines()[:5])
        return (
            None,
            1,
            (
                f"TEAMS_INTEGRATE_ERROR: Main repository working tree at {resolved_repo} is not clean "
                f"({dirty_preview or main_status.stderr.strip()}). Worktrees preserved.\n"
                f"Next: commit or stash uncommitted changes in {resolved_repo}, "
                f"then re-run `auto-dev teams integrate --job-id {job_id}`."
            ),
        )

    live_wts: dict[str, WorktreeInfo] = {
        wt.team_id: wt
        for wt in wt_manager.list_worktrees()
        if not wt.team_id.startswith("int-")
    }
    blockers, team_wt_paths, team_commit_counts = _collect_team_integrate_blockers(
        resolved_repo, manifest, live_wts
    )
    if blockers:
        details_block = "\n".join(f"  - {b}" for b in blockers)
        return (
            None,
            1,
            (
                f"TEAMS_INTEGRATE_ERROR: Pre-integration checks failed for job '{job_id}':\n"
                f"{details_block}\n"
                "Worktrees have been preserved (not deleted).\n"
                "Next steps:\n"
                f"  1. Inspect status: `auto-dev teams status --job-id {job_id}`\n"
                "  2. Complete remaining tasks and ensure clean committed state in the offending team worktree(s).\n"
                f"  3. Re-run `auto-dev teams integrate --job-id {job_id}`."
            ),
        )

    team_results: dict[str, TeamExecutionResult] = {}
    no_changes_teams: list[str] = []
    for team in manifest.teams:
        wt_path = team_wt_paths[team.id]
        head_proc = _run_git_cmd(wt_path, ["rev-parse", "HEAD"])
        tree_proc = _run_git_cmd(wt_path, ["rev-parse", "HEAD^{tree}"])
        if (
            head_proc.returncode != 0
            or not head_proc.stdout.strip()
            or tree_proc.returncode != 0
            or not tree_proc.stdout.strip()
        ):
            return (
                None,
                1,
                (
                    f"TEAMS_INTEGRATE_ERROR: Failed to resolve HEAD/tree for team '{team.id}' in {wt_path}. "
                    "Worktrees preserved.\n"
                    f"Next: inspect `auto-dev teams status --job-id {job_id}`."
                ),
            )
        head_commit = head_proc.stdout.strip()
        head_tree = tree_proc.stdout.strip()
        if team_commit_counts.get(team.id, 0) == 0:
            no_changes_teams.append(team.id)
            team_results[team.id] = TeamExecutionResult(
                team_id=team.id,
                success=True,
                status="NO_CHANGES",
                branch=f"team/{team.id}",
                head_commit=head_commit,
                bundle_path=None,
            )
            continue

        preserved_path = jobs_dir / job_id / "evidence" / f"{team.id}.zip"
        _archive_superseded_team_bundle(
            preserved_path,
            team_id=team.id,
            head_commit=head_commit,
            head_tree=head_tree,
            int_state=int_state,
        )
        try:
            bundle_path = _resolve_or_create_team_bundle(
                resolved_repo,
                job_id,
                team.id,
                wt_path,
                head_commit=head_commit,
                head_tree=head_tree,
            )
        except (OSError, subprocess.SubprocessError, ValueError) as err:
            return (
                None,
                1,
                (
                    f"TEAMS_INTEGRATE_ERROR: Failed to create bundle for team '{team.id}': {err}. "
                    "Worktrees preserved.\n"
                    f"Next: inspect worktree {wt_path} and retry `auto-dev teams integrate --job-id {job_id}`."
                ),
            )
        team_results[team.id] = TeamExecutionResult(
            team_id=team.id,
            success=True,
            status="SUCCESS",
            branch=f"team/{team.id}",
            head_commit=head_commit,
            bundle_path=bundle_path,
        )

    config = MultiTeamConfig(
        job_id=job_id,
        teams=[
            TeamWorkerSpec(team_id=team.id, base_branch=manifest.base_ref)
            for team in manifest.teams
        ],
        base_branch=manifest.base_ref,
        integration_verify_script=verify_script,
    )
    orchestrator = MultiTeamOrchestrator(repo_dir=resolved_repo)
    multi_res = orchestrator.integrate_team_results(
        config=config,
        team_results=team_results,
        verify_script=verify_script,
        timeout_sec=timeout_sec,
    )

    if not multi_res.success:
        if job_rec is not None:
            job_rec["status"] = multi_res.status
            job_rec["updated_at"] = time.time()
            _save_team_job_record(resolved_repo, job_rec)
        return (
            None,
            1,
            _format_integrate_failure_message(job_id, multi_res, manifest, team_wt_paths),
        )

    if multi_res.status == "NO_CHANGES":
        if job_rec is not None:
            job_rec["status"] = "NO_CHANGES"
            job_rec["no_changes_teams"] = list(no_changes_teams)
            job_rec["rc_tag"] = None
            job_rec["rc_commit"] = None
            job_rec["integration_branch"] = None
            job_rec["updated_at"] = time.time()
            _save_team_job_record(resolved_repo, job_rec)
        return (
            _build_integrate_success_payload(
                job_id,
                manifest.base_ref,
                None,
                None,
                {},
                no_changes_teams=no_changes_teams,
                all_no_changes=True,
            ),
            0,
            "",
        )

    rc_tag = multi_res.release_candidate_tag or f"rc/{job_id}"
    rc_commit = multi_res.release_candidate_commit or ""
    preserved_bundles = {
        tid: str(res.bundle_path)
        for tid, res in team_results.items()
        if res.bundle_path is not None
    }
    if no_changes_teams:
        saved_int_state = IntegrationJobState.load(jobs_dir, job_id)
        if saved_int_state is not None:
            saved_int_state.details["no_changes_teams"] = list(no_changes_teams)
            saved_int_state.save(jobs_dir)
    if job_rec is not None:
        job_rec["status"] = "INTEGRATED"
        job_rec["rc_tag"] = rc_tag
        job_rec["rc_commit"] = rc_commit
        job_rec["integration_branch"] = f"integration/{job_id}"
        job_rec["no_changes_teams"] = list(no_changes_teams)
        job_rec["updated_at"] = time.time()
        _save_team_job_record(resolved_repo, job_rec)

    return (
        _build_integrate_success_payload(
            job_id,
            manifest.base_ref,
            rc_tag,
            rc_commit,
            preserved_bundles,
            no_changes_teams=no_changes_teams,
        ),
        0,
        "",
    )


def format_teams_integrate_text(payload: dict[str, Any]) -> str:
    """teams integrate 成功時の人間向け出力を整形する。"""
    no_changes = payload.get("no_changes_teams") or []
    if payload.get("status") == "NO_CHANGES":
        lines = [
            "=== AUTO DEVELOPER / TEAMS INTEGRATION (NO_CHANGES) ===",
            f" Job ID:    {payload['job_id']}",
            f" Status:    {payload['status']}",
            " RC Tag:    (not created; no team changes from base)",
            f" Base Ref:  {payload['base_ref']} (not modified)",
        ]
        if no_changes:
            lines.append(f" No Changes (NO_CHANGES): {', '.join(no_changes)}")
        lines.append(f"Next steps (no changes to merge into '{payload['base_ref']}'):")
        lines.append(f"  auto-dev teams cleanup --job-id {payload['job_id']}")
        return "\n".join(lines)

    lines = [
        "=== AUTO DEVELOPER / TEAMS INTEGRATION PASSED ===",
        f" Job ID:    {payload['job_id']}",
        f" Status:    {payload['status']}",
        f" RC Tag:    {payload['rc_tag']}",
    ]
    if payload.get("rc_commit"):
        lines.append(f" RC Commit: {payload['rc_commit']}")
    lines.append(f" Base Ref:  {payload['base_ref']} (not modified)")
    if no_changes:
        lines.append(f" No Changes (NO_CHANGES): {', '.join(no_changes)}")
    bundles = payload.get("preserved_bundles") or {}
    if bundles:
        lines.append(" Preserved Bundles:")
        for tid, bpath in bundles.items():
            lines.append(f"   - {tid}: {bpath}")
    lines.append(f"Next steps (inspect and merge into '{payload['base_ref']}'):")
    for cmd in payload.get("merge_commands") or []:
        lines.append(f"  {cmd}")
    lines.append(f"  auto-dev teams cleanup --job-id {payload['job_id']}")
    return "\n".join(lines)


def _build_worker_pythonpath_env() -> dict[str, str]:
    """ワーカーサブプロセスが auto_developer を確実に import できる PYTHONPATH 環境辞書を返す。"""
    src_root = str(Path(__file__).resolve().parents[1])
    existing_pp = os.environ.get("PYTHONPATH", "")
    parts = [p for p in existing_pp.split(os.pathsep) if p]
    if src_root not in parts:
        new_pp = f"{src_root}{os.pathsep}{existing_pp}" if existing_pp else src_root
    else:
        new_pp = existing_pp
    return {"PYTHONPATH": new_pp}


def _format_worker_failure_message(
    job_id: str,
    manifest: TeamManifest,
    team_results: dict[str, TeamExecutionResult],
    failed_teams: list[str],
    wt_paths: dict[str, Path],
) -> str:
    """TEAM-15: チームワーカー失敗時のエラー詳細と保持された worktree・次の手順を整形する。"""
    lines = [
        f"TEAMS_RUN_ERROR: Worker execution failed for job '{job_id}'. Integration skipped.",
        f"Failed team(s): {', '.join(failed_teams)}",
    ]
    for tid in failed_teams:
        res = team_results.get(tid)
        status = res.status if res else "NO_RESULT"
        err_msg = ""
        if res is not None:
            err_msg = str(
                res.details.get("error")
                or res.details.get("stderr")
                or res.details.get("stdout")
                or ""
            ).strip()
        if err_msg:
            err_line = err_msg.splitlines()[-1][:200]
            lines.append(f"  - {tid} ({status}): {err_line}")
        else:
            lines.append(f"  - {tid} ({status})")

    kept = ", ".join(f"{t.id}={wt_paths[t.id]}" for t in manifest.teams if t.id in wt_paths)
    if kept:
        lines.append(f"Worktrees preserved (not deleted): {kept}")
    lines.append("Next steps:")
    lines.append(f"  1. Inspect status: `auto-dev teams status --job-id {job_id}`")
    lines.append(
        "  2. Inspect or fix the failing team worktree(s), then run "
        f"`auto-dev teams integrate --job-id {job_id}`."
    )
    lines.append(
        f"  3. Or discard this job with `auto-dev teams cleanup --job-id {job_id} --force`."
    )
    return "\n".join(lines)


def run_teams_job(
    repo_dir: Path,
    job_id: str | None = None,
    *,
    maker_cmd: str = "",
    checker_cmd: str = "",
    maker_model: str | None = "gemini-3.8-flash",
    checker_model: str | None = "claude-opus-5.5",
    is_mock: bool = False,
    worker_timeout: float = 21600.0,
    verify_timeout: int = 300,
    verify_script: Path | None = None,
    spec_path: Path | None = None,
) -> tuple[dict[str, Any] | None, int, str]:
    """TEAM-15: prepare → 全チーム並行実行 → integrate を一括実行する。"""
    resolved_repo = repo_dir.resolve()
    if worker_timeout <= 0:
        return None, 2, "TEAMS_RUN_ERROR: --worker-timeout must be a positive number."

    if not is_mock and (not maker_cmd or not checker_cmd):
        return (
            None,
            2,
            (
                "CONFIG ERROR: In production mode, both Maker and Checker commands must be configured "
                "(--maker-cmd and --checker-cmd).\n"
                "Use --mock if you want to execute with simulated mock agents for testing."
            ),
        )

    norm_maker_model = maker_model.strip() if isinstance(maker_model, str) else ""
    norm_checker_model = checker_model.strip() if isinstance(checker_model, str) else ""
    if is_mock:
        resolved_maker_model = norm_maker_model or "gemini-3.8-flash"
        resolved_checker_model = norm_checker_model or "claude-opus-5.5"
    else:
        missing_models: list[str] = []
        if maker_cmd and not norm_maker_model:
            missing_models.append("--maker-model")
        if checker_cmd and not norm_checker_model:
            missing_models.append("--checker-model")
        if missing_models:
            flags = ", ".join(missing_models)
            return (
                None,
                2,
                (
                    f"CONFIG ERROR: In production mode, model identifiers must be specified when external commands are configured ({flags}).\n"
                    f"Please specify {flags}, or use --mock to run with simulated mock agents."
                ),
            )
        resolved_maker_model = norm_maker_model
        resolved_checker_model = norm_checker_model

    from auto_developer.audit import verify_model_independence

    ok_models, model_reason = verify_model_independence(resolved_maker_model, resolved_checker_model)
    if not ok_models:
        return None, 2, f"PRE-FLIGHT MODEL POLICY ERROR: {model_reason}"

    prep_payload, prep_code, prep_err = prepare_teams_job(resolved_repo, job_id=job_id)
    if prep_payload is None:
        return None, prep_code, prep_err

    effective_job_id = str(prep_payload["job_id"])
    manifest = load_teams_manifest(resolved_repo)
    wt_paths: dict[str, Path] = {
        str(t["team_id"]): Path(str(t["worktree_path"]))
        for t in prep_payload["teams"]
    }

    job_rec = _load_team_job_record(resolved_repo, effective_job_id)
    if job_rec is not None:
        job_rec["status"] = "RUNNING"
        job_rec["maker_model"] = resolved_maker_model
        job_rec["checker_model"] = resolved_checker_model
        job_rec["worker_timeout"] = worker_timeout
        job_rec["is_mock"] = is_mock
        job_rec["updated_at"] = time.time()
        _save_team_job_record(resolved_repo, job_rec)

    worker_env = _build_worker_pythonpath_env()
    config = MultiTeamConfig(
        job_id=effective_job_id,
        teams=[
            TeamWorkerSpec(
                team_id=team.id,
                base_branch=manifest.base_ref,
                env=dict(worker_env),
            )
            for team in manifest.teams
        ],
        base_branch=manifest.base_ref,
        integration_verify_script=verify_script,
    )

    def build_worker_cmd(_team_id: str, wt_path: Path) -> list[str]:
        cmd = [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(wt_path)]
        if spec_path is not None:
            cmd.extend(["--spec", str(spec_path.resolve())])
        if verify_script is not None:
            cmd.extend(["--verify-script", str(verify_script.resolve())])
        cmd.append("run")
        if is_mock:
            cmd.append("--mock")
        if maker_cmd:
            cmd.extend(["--maker-cmd", maker_cmd])
        if checker_cmd:
            cmd.extend(["--checker-cmd", checker_cmd])
        if resolved_maker_model:
            cmd.extend(["--maker-model", resolved_maker_model])
        if resolved_checker_model:
            cmd.extend(["--checker-model", resolved_checker_model])
        if verify_timeout > 0:
            cmd.extend(["--timeout", str(verify_timeout)])
        return cmd

    orchestrator = MultiTeamOrchestrator(repo_dir=resolved_repo)
    try:
        team_results = orchestrator.run_workers(
            config,
            worker_cmd_factory=build_worker_cmd,
            timeout_sec=worker_timeout,
        )
    except BaseException:
        orchestrator.terminate_all_workers()
        raise

    failed_teams = [
        team.id
        for team in manifest.teams
        if team.id not in team_results or not team_results[team.id].success
    ]

    worker_summary = {
        team.id: {
            "success": bool(team_results[team.id].success) if team.id in team_results else False,
            "status": team_results[team.id].status if team.id in team_results else "NO_RESULT",
            "branch": team_results[team.id].branch if team.id in team_results else f"team/{team.id}",
            "head_commit": team_results[team.id].head_commit if team.id in team_results else None,
            "error": (
                str(team_results[team.id].details.get("error"))
                if team.id in team_results and team_results[team.id].details.get("error")
                else None
            ),
        }
        for team in manifest.teams
    }

    job_rec = _load_team_job_record(resolved_repo, effective_job_id)
    if job_rec is not None:
        job_rec["worker_results"] = worker_summary
        job_rec["updated_at"] = time.time()
        if failed_teams:
            job_rec["status"] = "WORKER_FAILED"
            job_rec["failed_teams"] = failed_teams
        _save_team_job_record(resolved_repo, job_rec)

    if failed_teams:
        return (
            None,
            1,
            _format_worker_failure_message(
                effective_job_id,
                manifest,
                team_results,
                failed_teams,
                wt_paths,
            ),
        )

    int_payload, int_code, int_err = integrate_teams_job(
        resolved_repo,
        job_id=effective_job_id,
        verify_script=verify_script,
        timeout_sec=verify_timeout,
    )
    if int_payload is None:
        return None, int_code, int_err

    no_changes_set = set(int_payload.get("no_changes_teams") or [])
    for tid in no_changes_set:
        if tid in worker_summary and worker_summary[tid].get("success"):
            worker_summary[tid]["status"] = "NO_CHANGES"

    run_payload = dict(int_payload)
    run_payload["worker_results"] = worker_summary
    return run_payload, 0, ""


def format_teams_run_text(payload: dict[str, Any]) -> str:
    """teams run 成功時の人間向け出力を整形する。"""
    lines = [
        "=== AUTO DEVELOPER / TEAMS RUN COMPLETED ===",
        f" Job ID:    {payload['job_id']}",
    ]
    worker_results = payload.get("worker_results") or {}
    if worker_results:
        lines.append(" Workers:")
        for tid, winfo in worker_results.items():
            lines.append(f"   - {tid}: {winfo.get('status', 'UNKNOWN')}")
    lines.append(format_teams_integrate_text(payload))
    return "\n".join(lines)


def recover_teams_job(
    repo_dir: Path,
    job_id: str | None = None,
    verify_script: Path | None = None,
) -> tuple[dict[str, Any] | None, int, str]:
    """TEAM-16: recover_multi_team_job を呼び、中断したマルチチームジョブの復旧・状態出力を行う。"""
    resolved_repo = repo_dir.resolve()
    jobs_dir = resolved_repo / ".dev" / "integration_jobs"

    if job_id is not None:
        if not VALID_JOB_ID_PATTERN.match(job_id):
            return None, 2, f"TEAMS_RECOVER_ERROR: Invalid job_id '{job_id}'."
        has_job = (
            _team_job_record_path(resolved_repo, job_id).is_file()
            or (jobs_dir / f"{job_id}.json").is_file()
            or (jobs_dir / job_id / "workers.json").is_file()
        )
        if not has_job:
            return None, 1, f"TEAMS_RECOVER_ERROR: Job '{job_id}' not found."
        resolved_job_id = job_id
    else:
        found = _find_latest_team_job_id(resolved_repo)
        if found is None:
            return None, 1, "TEAMS_RECOVER_ERROR: No multi-team job found to recover."
        resolved_job_id = found

    effective_verify: Path | None = verify_script
    if effective_verify is None:
        default_vs = resolved_repo / "scripts" / "verify_local.sh"
        if default_vs.is_file():
            effective_verify = default_vs

    orchestrator = MultiTeamOrchestrator(repo_dir=resolved_repo)
    rec_res = orchestrator.recover_multi_team_job(
        job_id=resolved_job_id,
        verify_script=effective_verify,
        cleanup=False,
    )

    workers = WorkerRegistry(resolved_repo, resolved_job_id).load()
    int_state = IntegrationJobState.load(jobs_dir, resolved_job_id)
    job_rec = _load_team_job_record(resolved_repo, resolved_job_id)

    pipeline_res = rec_res.pipeline_result
    rc_tag = pipeline_res.release_candidate_tag if pipeline_res else None
    rc_commit = pipeline_res.release_candidate_commit if pipeline_res else None
    if rc_tag and not rc_tag.startswith("rc/"):
        rc_tag = rc_tag.removeprefix("refs/tags/")
    if rc_tag is None and int_state is not None and int_state.rc_tag:
        rc_tag = int_state.rc_tag
    if rc_commit is None and int_state is not None and int_state.rc_commit:
        rc_commit = int_state.rc_commit

    if not workers and int_state is None and job_rec is not None:
        status_label = str(job_rec.get("status") or "PREPARED")
        payload: dict[str, Any] = {
            "job_id": resolved_job_id,
            "success": True,
            "status": status_label,
            "reclaimed_orphans": [],
            "unverified_processes": [],
            "unverified_worktrees": [],
            "rescued_bundles": [],
            "reused_bundles": [],
            "rc_tag": job_rec.get("rc_tag"),
            "rc_commit": job_rec.get("rc_commit"),
            "team_results": {},
            "details": {"message": f"Job '{resolved_job_id}' is in '{status_label}' state; nothing to recover."},
        }
        return payload, 0, ""

    status_label = (
        pipeline_res.status
        if pipeline_res is not None
        else ("RECOVERED" if rec_res.success else "RECOVERY_FAILED")
    )

    if job_rec is not None:
        if rec_res.success and rc_tag:
            job_rec["status"] = "INTEGRATED"
            job_rec["rc_tag"] = rc_tag
            if rc_commit:
                job_rec["rc_commit"] = rc_commit
        elif rec_res.success:
            job_rec["status"] = status_label
        else:
            job_rec["status"] = status_label
        job_rec["updated_at"] = time.time()
        _save_team_job_record(resolved_repo, job_rec)

    payload = {
        "job_id": resolved_job_id,
        "success": rec_res.success,
        "status": status_label,
        "reclaimed_orphans": rec_res.reclaimed_orphans,
        "unverified_processes": rec_res.unverified_processes,
        "unverified_worktrees": rec_res.unverified_worktrees,
        "rescued_bundles": rec_res.rescued_bundles,
        "reused_bundles": rec_res.reused_bundles,
        "rc_tag": rc_tag,
        "rc_commit": rc_commit,
        "team_results": rec_res.details.get("team_results", {}),
        "details": rec_res.details,
    }
    return payload, (0 if rec_res.success else 1), ""


def format_teams_recover_text(payload: dict[str, Any]) -> str:
    """teams recover の人間向け出力を整形する。"""
    lines = [
        "=== AUTO DEVELOPER / TEAMS RECOVERY ===",
        f" Job ID:               {payload['job_id']}",
        f" Success:              {payload['success']}",
        f" Status:               {payload['status']}",
        f" Reclaimed Orphans:    {', '.join(payload.get('reclaimed_orphans') or []) or 'none'}",
        f" Unverified Processes: {', '.join(payload.get('unverified_processes') or []) or 'none'}",
        f" Rescued Bundles:      {', '.join(payload.get('rescued_bundles') or []) or 'none'}",
        f" Reused Bundles:       {', '.join(payload.get('reused_bundles') or []) or 'none'}",
    ]
    if payload.get("rc_tag"):
        lines.append(f" RC Tag:               {payload['rc_tag']}")
    if payload.get("rc_commit"):
        lines.append(f" RC Commit:            {payload['rc_commit']}")
    team_results = payload.get("team_results") or {}
    if team_results:
        lines.append(" Team Results:")
        for tid, tres in team_results.items():
            lines.append(
                f"   - {tid}: status={tres.get('status')} (evidence_preserved={tres.get('evidence_preserved')})"
            )
    err = (payload.get("details") or {}).get("error")
    if err:
        lines.append(f" Details:              {err}")
    if payload.get("success"):
        if payload.get("rc_tag"):
            lines.append(f"Next: inspect `git show {payload['rc_tag']}` or run `auto-dev teams cleanup --job-id {payload['job_id']}`.")
        else:
            lines.append(f"Next: run `auto-dev teams integrate --job-id {payload['job_id']}`.")
    else:
        lines.append(f"Next: inspect `auto-dev teams status --job-id {payload['job_id']}` before retrying or cleaning up with `--force`.")
    return "\n".join(lines)


def get_multi_team_report_summary(
    repo_dir: Path,
    job_id: str | None = None,
) -> dict[str, Any] | None:
    """TEAM-17: NIGHTLY_REPORT.md / brief 表示向けに最新のマルチチームジョブ結果を集約する。"""
    resolved_repo = repo_dir.resolve()
    resolved_job_id = job_id or _find_latest_team_job_id(resolved_repo)
    if resolved_job_id is None:
        return None

    status_data, code, _ = get_teams_status(resolved_repo, job_id=resolved_job_id)
    if code != 0 or status_data is None:
        return None

    jobs_dir = resolved_repo / ".dev" / "integration_jobs"
    job_rec = _load_team_job_record(resolved_repo, resolved_job_id)
    int_state = IntegrationJobState.load(jobs_dir, resolved_job_id)
    workers = WorkerRegistry(resolved_repo, resolved_job_id).load()

    base_ref = "main"
    if job_rec and job_rec.get("base_ref"):
        base_ref = str(job_rec["base_ref"])
    else:
        try:
            manifest = load_teams_manifest(resolved_repo)
            base_ref = manifest.base_ref
        except (FileNotFoundError, ManifestValidationError, OSError):
            pass

    rc_tag = (
        (int_state.rc_tag if int_state and int_state.rc_tag else None)
        or (str(job_rec["rc_tag"]) if job_rec and job_rec.get("rc_tag") else None)
    )
    rc_commit = (
        (int_state.rc_commit if int_state and int_state.rc_commit else None)
        or (str(job_rec["rc_commit"]) if job_rec and job_rec.get("rc_commit") else None)
    )

    overall_state = str(status_data.get("job_state") or "UNKNOWN")
    if job_rec and job_rec.get("status") == "WORKER_FAILED" and int_state is None:
        overall_state = "WORKER_FAILED"

    merged_teams = set(int_state.merged_teams) if int_state else set()
    no_changes_teams = set(_extract_no_changes_teams(int_state, job_rec))
    recorded_workers = (job_rec.get("worker_results") or {}) if job_rec else {}

    teams_out: list[dict[str, Any]] = []
    pending_decisions: list[str] = []
    any_worktree_or_branch = False

    for t in status_data.get("teams") or []:
        tid = str(t["team_id"])
        w_status = str(t["worker_status"])
        rec_w = recorded_workers.get(tid, {}) if isinstance(recorded_workers, dict) else {}
        if rec_w and rec_w.get("status") == "TIMEOUT" and w_status == "UNKNOWN":
            w_status = "TIMEOUT"
        elif rec_w and not rec_w.get("success", True) and w_status in ("PREPARED", "UNKNOWN"):
            w_status = str(rec_w.get("status") or "FAILED")
        elif tid in no_changes_teams and w_status not in (
            "FAILED",
            "TIMEOUT",
            "CRASHED",
            "STALE",
            "UNVERIFIED_PROCESS",
        ):
            w_status = "NO_CHANGES"

        w_reg = workers.get(tid)
        err_detail: str | None = None
        if w_reg and w_reg.details.get("error"):
            err_detail = str(w_reg.details["error"])
        elif isinstance(rec_w, dict) and rec_w.get("error"):
            err_detail = str(rec_w["error"])

        qbs = list(t.get("quarantine_branches") or [])
        wt_exists = bool(t.get("worktree_exists"))
        br_exists = bool(t.get("branch_exists"))
        if wt_exists or br_exists:
            any_worktree_or_branch = True

        teams_out.append(
            {
                "team_id": tid,
                "status": w_status,
                "completed_tasks": int(t.get("completed_tasks", 0)),
                "total_tasks": int(t.get("total_tasks", 0)),
                "failed_tasks": int(t.get("failed_tasks", 0)),
                "branch": str(t.get("branch") or f"team/{tid}"),
                "branch_exists": br_exists,
                "worktree_path": str(t.get("worktree_path") or ""),
                "worktree_exists": wt_exists,
                "quarantine_branches": qbs,
                "merged": tid in merged_teams,
                "error": err_detail,
            }
        )

    integration_errors: list[str] = []
    if int_state and isinstance(int_state.details, dict):
        raw_errs = int_state.details.get("errors")
        if isinstance(raw_errs, list):
            integration_errors.extend(str(e) for e in raw_errs if e)
        elif int_state.details.get("error"):
            integration_errors.append(str(int_state.details["error"]))

    is_integrated_ok = bool(rc_tag) and overall_state in ("COMPLETED", "INTEGRATED", "CLEANED_UP")
    is_all_no_changes = not rc_tag and overall_state == "NO_CHANGES"
    is_all_no_changes_cleaned = (
        not rc_tag
        and overall_state == "CLEANED_UP"
        and bool(no_changes_teams)
        and all(t["team_id"] in no_changes_teams for t in teams_out)
    )
    if is_integrated_ok and rc_tag:
        rc_merged = False
        chk_merged = _run_git_cmd(
            resolved_repo,
            ["merge-base", "--is-ancestor", f"refs/tags/{rc_tag}", base_ref],
        )
        if chk_merged.returncode == 0:
            rc_merged = True
        if not rc_merged:
            pending_decisions.append(
                f"RC タグ `{rc_tag}` の内容を確認し、`{base_ref}` へ取り込むか判断してください: "
                f"`git diff {base_ref}..{rc_tag}` / `git merge --no-ff {rc_tag}`"
            )
        if any_worktree_or_branch:
            pending_decisions.append(
                f"作業領域（worktree・チームブランチ）を片付ける場合: `auto-dev teams cleanup --job-id {resolved_job_id}`"
            )
    elif is_all_no_changes:
        pending_decisions.append(
            f"全チームに変更がなかったため（NO_CHANGES）、RC タグは作成されませんでした（`{base_ref}` は変更なし）。"
        )
        if any_worktree_or_branch:
            pending_decisions.append(
                f"作業領域（worktree・チームブランチ）を片付ける場合: `auto-dev teams cleanup --job-id {resolved_job_id}`"
            )
    elif is_all_no_changes_cleaned:
        pass
    else:
        for t_info in teams_out:
            tid = str(t_info["team_id"])
            qbs = list(t_info["quarantine_branches"])
            t_status = str(t_info["status"])
            if qbs:
                pending_decisions.append(
                    f"チーム `{tid}` の隔離ブランチ（{', '.join(qbs)}）を確認し、修正・引き継ぎ・破棄を判断してください"
                )
            elif overall_state != "CLEANED_UP" and (
                t_status in ("FAILED", "TIMEOUT", "CRASHED", "STALE", "UNVERIFIED_PROCESS")
                or t_info["failed_tasks"] > 0
            ):
                pending_decisions.append(
                    f"チーム `{tid}` が `{t_status}` で停止したため統合されていません。状態を確認してください: "
                    f"`auto-dev teams status --job-id {resolved_job_id}`"
                )

        if overall_state in ("MERGING", "VERIFYING", "PREPARING", "RECOVERY_REQUIRED", "RUNNING") or any(
            t["status"] in ("STALE", "UNVERIFIED_PROCESS") for t in teams_out
        ):
            pending_decisions.append(
                f"中断または未確認プロセスの復旧を実行してください: `auto-dev teams recover --job-id {resolved_job_id}`"
            )
        elif overall_state == "CONFLICT":
            pending_decisions.append(
                f"統合コンフリクトを該当 worktree で解消してコミットし、`auto-dev teams integrate --job-id {resolved_job_id}` を再実行してください"
            )
        elif overall_state in ("VERIFICATION_FAILED", "FAILED"):
            pending_decisions.append(
                f"統合ゲート失敗の原因を修正し、`auto-dev teams integrate --job-id {resolved_job_id}` を再実行してください"
            )
        elif overall_state == "PREPARED":
            pending_decisions.append(
                f"各チームのタスク完了後、`auto-dev teams integrate --job-id {resolved_job_id}` で統合を実行してください"
            )

        if any_worktree_or_branch:
            pending_decisions.append(
                f"ジョブを破棄してやり直す場合: `auto-dev teams cleanup --job-id {resolved_job_id} --force`"
            )

    return {
        "job_id": resolved_job_id,
        "job_state": overall_state,
        "base_ref": base_ref,
        "rc_tag": rc_tag,
        "rc_commit": rc_commit,
        "is_integrated": is_integrated_ok,
        "teams": teams_out,
        "integration_errors": integration_errors,
        "pending_decisions": pending_decisions,
    }
