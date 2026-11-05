from __future__ import annotations

import json
import subprocess
import tarfile
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from auto_developer.evidence import EvidenceStore
from auto_developer.guard import GitGuard
from auto_developer.models import JobState


class RecoveryDiagnosis(str, Enum):
    """クラッシュ整合性診断の結果ステータス。"""

    HEALTHY = "HEALTHY"
    COMMITTED_AHEAD_OF_STATE = "COMMITTED_AHEAD_OF_STATE"  # Scenario 03: コミット確定済みだがJobState未更新
    QUARANTINE_INCOMPLETE = "QUARANTINE_INCOMPLETE"        # Scenario 04: Quarantine途中でクラッシュ
    CRASHED_IN_FLIGHT = "CRASHED_IN_FLIGHT"                # Scenario 01, 02: コミット前実行中にクラッシュ
    DIRTY_UNTRACKED_CONFLICT = "DIRTY_UNTRACKED_CONFLICT"  # 意図しない未追跡変更による競合
    UNKNOWN_INCONSISTENCY = "UNKNOWN_INCONSISTENCY"


@dataclass
class InspectionReport:
    """非破壊診断レポート。"""

    diagnosis: RecoveryDiagnosis
    can_auto_recover: bool
    current_branch: str
    base_branch: str
    head_commit: str
    checkpoint_commit: str
    tree_clean: bool
    uncommitted_files: list[str] = field(default_factory=list)
    task_id: str | None = None
    matching_evidence: dict[str, Any] | None = None
    committed_tree_sha: str | None = None
    evidence_tree_sha: str | None = None
    quarantine_snapshot: str | None = None
    explanation: str = ""
    recommended_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": self.diagnosis.value,
            "can_auto_recover": self.can_auto_recover,
            "current_branch": self.current_branch,
            "base_branch": self.base_branch,
            "head_commit": self.head_commit,
            "checkpoint_commit": self.checkpoint_commit,
            "tree_clean": self.tree_clean,
            "uncommitted_files": self.uncommitted_files,
            "task_id": self.task_id,
            "committed_tree_sha": self.committed_tree_sha,
            "evidence_tree_sha": self.evidence_tree_sha,
            "quarantine_snapshot": self.quarantine_snapshot,
            "explanation": self.explanation,
            "recommended_action": self.recommended_action,
        }


@dataclass
class RecoveryAction:
    """個別の復旧操作アクション。"""

    action_type: str
    description: str
    params: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "description": self.description,
            "params": self.params,
        }


@dataclass
class RecoveryPlan:
    """実行予定の復旧計画。"""

    diagnosis: RecoveryDiagnosis
    can_auto_recover: bool
    actions: list[RecoveryAction] = field(default_factory=list)
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "diagnosis": self.diagnosis.value,
            "can_auto_recover": self.can_auto_recover,
            "actions": [a.to_dict() for a in self.actions],
            "explanation": self.explanation,
        }


@dataclass
class RecoveryResult:
    """復旧適用の結果。"""

    success: bool
    applied_actions: list[str] = field(default_factory=list)
    resulting_state: str = "UNCHANGED"
    message: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "applied_actions": self.applied_actions,
            "resulting_state": self.resulting_state,
            "message": self.message,
        }


class RecoveryEngine:
    """Git、JobState、Evidence Record を照合し、クラッシュ後の安全な復旧を診断・計画・実行するエンジン。"""

    def __init__(
        self,
        repo_dir: Path | None = None,
        state_path: Path | None = None,
        evidence_store: EvidenceStore | None = None,
        git_guard: GitGuard | None = None,
    ) -> None:
        self.repo_dir = repo_dir or Path.cwd()
        self.state_path = state_path or (self.repo_dir / ".dev" / "job_state.json")
        self.evidence_store = evidence_store or EvidenceStore(self.repo_dir)
        self.git_guard = git_guard or GitGuard(self.repo_dir)

    def inspect(self) -> InspectionReport:
        """Git、JobState、Evidence を非破壊で読み取り、整合性を診断する。"""
        current_branch = self.git_guard.get_current_branch()
        head_commit = self.git_guard.get_current_checkpoint()
        status_proc = self.git_guard._run_git(["status", "--porcelain"])
        uncommitted_files = [line.strip() for line in status_proc.stdout.splitlines() if line.strip()]
        tree_clean = len(uncommitted_files) == 0

        if not self.state_path.exists():
            return InspectionReport(
                diagnosis=RecoveryDiagnosis.HEALTHY,
                can_auto_recover=False,
                current_branch=current_branch,
                base_branch=current_branch,
                head_commit=head_commit,
                checkpoint_commit=head_commit,
                tree_clean=tree_clean,
                uncommitted_files=uncommitted_files,
                explanation="No job_state.json found. System is idle or healthy.",
                recommended_action="None",
            )

        try:
            job_state = JobState.load(self.state_path)
        except (OSError, json.JSONDecodeError, ValueError) as e:
            return InspectionReport(
                diagnosis=RecoveryDiagnosis.UNKNOWN_INCONSISTENCY,
                can_auto_recover=False,
                current_branch=current_branch,
                base_branch=current_branch,
                head_commit=head_commit,
                checkpoint_commit=head_commit,
                tree_clean=tree_clean,
                uncommitted_files=uncommitted_files,
                explanation=f"Corrupted job_state.json: {e}",
                recommended_action="Inspect state file manually.",
            )

        base_branch = job_state.base_branch or current_branch
        checkpoint = job_state.checkpoint_hash or head_commit
        task_id = job_state.current_task_id

        # 1. Quarantine 途中クラッシュ (Scenario 04 相当)
        # 現在のブランチが quarantine/ で始まっている、または base_branch と異なり quarantine ブランチが存在
        quarantine_snapshots = sorted(self.repo_dir.glob(".dev/quarantines/snapshot_task_*.tar.gz"))
        valid_snapshots: list[str] = []
        for s in quarantine_snapshots:
            try:
                if tarfile.is_tarfile(s):
                    valid_snapshots.append(str(s))
            except (OSError, tarfile.TarError):
                continue
        latest_snapshot = valid_snapshots[-1] if valid_snapshots else None

        if current_branch.startswith("quarantine/") or (
            job_state.base_branch and current_branch != job_state.base_branch and "quarantine" in current_branch
        ):
            return InspectionReport(
                diagnosis=RecoveryDiagnosis.QUARANTINE_INCOMPLETE,
                can_auto_recover=True,
                current_branch=current_branch,
                base_branch=base_branch,
                head_commit=head_commit,
                checkpoint_commit=checkpoint,
                tree_clean=tree_clean,
                uncommitted_files=uncommitted_files,
                task_id=task_id,
                quarantine_snapshot=latest_snapshot,
                explanation=(
                    f"Process crashed during quarantine. Currently on quarantine branch '{current_branch}'. "
                    f"Work artifacts are captured in snapshot: {latest_snapshot or 'none'}."
                ),
                recommended_action=(
                    f"Switch safely back to base branch '{base_branch}', restore clean working tree "
                    f"to checkpoint '{checkpoint}', and finalize JobState as FAILED."
                ),
            )

        # 2. コミット確定直後クラッシュ (Scenario 03 相当)
        # Git HEAD が前進しており、Evidence Record の Tree SHA と一致している場合
        if task_id and head_commit != checkpoint and job_state.status == "RUNNING":
            evidence_rec = self.evidence_store.get_record(task_id)
            if not evidence_rec:
                # records になければ、未消費の evidence_dir のトークンも確認
                token_path = self.evidence_store.evidence_dir / f"task_{task_id}.json"
                if token_path.exists():
                    try:
                        evidence_rec = json.loads(token_path.read_text(encoding="utf-8"))
                    except (OSError, json.JSONDecodeError, ValueError):
                        evidence_rec = None

            committed_tree = self.git_guard.get_commit_tree_sha("HEAD")
            evidence_tree = (
                evidence_rec.get("commit", {}).get("tree_sha")
                or evidence_rec.get("tree_sha")
                if evidence_rec
                else None
            )

            if evidence_rec and evidence_tree and committed_tree == evidence_tree and tree_clean:
                # 親コミットが checkpoint であることを確認
                try:
                    parent_proc = self.git_guard._run_git(["rev-parse", "HEAD^"])
                    parent_sha = parent_proc.stdout.strip()
                    parent_matches = (parent_sha == checkpoint)
                except subprocess.SubprocessError:
                    parent_matches = False

                if parent_matches:
                    return InspectionReport(
                        diagnosis=RecoveryDiagnosis.COMMITTED_AHEAD_OF_STATE,
                        can_auto_recover=True,
                        current_branch=current_branch,
                        base_branch=base_branch,
                        head_commit=head_commit,
                        checkpoint_commit=checkpoint,
                        tree_clean=tree_clean,
                        uncommitted_files=uncommitted_files,
                        task_id=task_id,
                        matching_evidence=evidence_rec,
                        committed_tree_sha=committed_tree,
                        evidence_tree_sha=evidence_tree,
                        explanation=(
                            f"Task {task_id} was successfully committed to Git ({head_commit[:7]}) "
                            f"with verified Tree SHA ({committed_tree[:7]}), but JobState was not updated "
                            "before SIGKILL/crash."
                        ),
                        recommended_action=(
                            f"Advance JobState to COMPLETED incorporating commit {head_commit[:7]} "
                            "without rolling back verified work."
                        ),
                    )

        # 3. コミット前実行中クラッシュ (Scenario 01 & 02 相当)
        if job_state.status == "RUNNING" and head_commit == checkpoint:
            return InspectionReport(
                diagnosis=RecoveryDiagnosis.CRASHED_IN_FLIGHT,
                can_auto_recover=True,
                current_branch=current_branch,
                base_branch=base_branch,
                    head_commit=head_commit,
                    checkpoint_commit=checkpoint,
                    tree_clean=tree_clean,
                    uncommitted_files=uncommitted_files,
                    task_id=task_id,
                    explanation=(
                        f"Process crashed during phase {job_state.current_phase} (Attempt {job_state.current_attempt}) "
                        f"for task {task_id}. Git HEAD matches checkpoint ({checkpoint[:7]})."
                    ),
                    recommended_action=(
                        "Isolate uncommitted changes to quarantine branch if dirty, reset cleanly "
                        f"to checkpoint ({checkpoint[:7]}), and mark job ready for safe retry."
                    ),
                )

        # 4. 正常な状態 (JobState が正常完了または失敗ステータスであり、作業ツリー・ブランチ・コミットが整合)
        is_job_finished = job_state.status in ("COMPLETED", "FAILED")
        last_completed_commit = (
            job_state.completed_tasks[-1].get("commit")
            if job_state.completed_tasks
            else checkpoint
        )
        is_commit_consistent = (head_commit == last_completed_commit) or (head_commit == checkpoint)

        if is_job_finished and tree_clean and current_branch == base_branch and is_commit_consistent:
            return InspectionReport(
                diagnosis=RecoveryDiagnosis.HEALTHY,
                can_auto_recover=False,
                current_branch=current_branch,
                base_branch=base_branch,
                head_commit=head_commit,
                checkpoint_commit=checkpoint,
                tree_clean=tree_clean,
                uncommitted_files=uncommitted_files,
                task_id=task_id,
                explanation="System is healthy. No recovery action needed.",
                recommended_action="None",
            )

        explanation = "Unrecognized state inconsistency detected."
        if job_state.status == "RUNNING" and head_commit != checkpoint:
            explanation = (
                f"JobState is RUNNING with checkpoint {checkpoint[:7]}, but Git HEAD has advanced "
                f"to {head_commit[:7]} without matching verified evidence. Auto-recovery refused for safety."
            )
        elif job_state.status == "RUNNING":
            explanation = (
                f"JobState is unexpectedly RUNNING for task {task_id} with unexplained inconsistency."
            )

        return InspectionReport(
            diagnosis=RecoveryDiagnosis.UNKNOWN_INCONSISTENCY,
            can_auto_recover=False,
            current_branch=current_branch,
            base_branch=base_branch,
            head_commit=head_commit,
            checkpoint_commit=checkpoint,
            tree_clean=tree_clean,
            uncommitted_files=uncommitted_files,
            task_id=task_id,
            explanation=explanation,
            recommended_action="Manual inspection required. Do not force auto-recovery.",
        )

    def plan(self) -> RecoveryPlan:
        """非破壊診断結果から、適用すべき復旧計画（Plan）を生成する。"""
        report = self.inspect()
        actions: list[RecoveryAction] = []

        if report.diagnosis == RecoveryDiagnosis.COMMITTED_AHEAD_OF_STATE:
            actions.append(
                RecoveryAction(
                    action_type="ADVANCE_JOB_STATE",
                    description=f"Advance JobState to COMPLETED incorporating commit {report.head_commit[:7]}",
                    params={
                        "task_id": report.task_id,
                        "commit": report.head_commit,
                        "evidence_tree_sha": report.evidence_tree_sha,
                        "committed_tree_sha": report.committed_tree_sha,
                    },
                )
            )
            actions.append(
                RecoveryAction(
                    action_type="FINALIZE_EVIDENCE",
                    description=f"Archive evidence record for task {report.task_id} as COMPLETED",
                    params={"task_id": report.task_id},
                )
            )

        elif report.diagnosis == RecoveryDiagnosis.QUARANTINE_INCOMPLETE:
            actions.append(
                RecoveryAction(
                    action_type="SWITCH_BASE_BRANCH",
                    description=f"Switch Git branch back to base branch '{report.base_branch}'",
                    params={"base_branch": report.base_branch},
                )
            )
            actions.append(
                RecoveryAction(
                    action_type="CLEAN_RESTORE_TREE",
                    description=f"Restore working tree cleanly to checkpoint '{report.checkpoint_commit[:7]}'",
                    params={"checkpoint": report.checkpoint_commit},
                )
            )
            actions.append(
                RecoveryAction(
                    action_type="FINALIZE_QUARANTINE_STATE",
                    description=f"Finalize JobState as FAILED (Quarantined on {report.current_branch})",
                    params={"task_id": report.task_id, "quarantined_branch": report.current_branch},
                )
            )

        elif report.diagnosis == RecoveryDiagnosis.CRASHED_IN_FLIGHT:
            actions.append(
                RecoveryAction(
                    action_type="CLEAN_RESET_WORK",
                    description=f"Reset uncommitted changes cleanly to checkpoint '{report.checkpoint_commit[:7]}'",
                    params={"checkpoint": report.checkpoint_commit},
                )
            )
            actions.append(
                RecoveryAction(
                    action_type="RESET_FOR_RETRY",
                    description="Reset JobState to INIT for safe resumption",
                    params={"task_id": report.task_id},
                )
            )

        return RecoveryPlan(
            diagnosis=report.diagnosis,
            can_auto_recover=report.can_auto_recover,
            actions=actions,
            explanation=report.explanation,
        )

    def apply(self) -> RecoveryResult:
        """計画された復旧操作を安全・アトミック・冪等に適用する。"""
        # 排他制御: オーケストレータープロセスが現在実行中か検査 (P1-2)
        import fcntl
        import os

        for lock_name in ("auto_developer.lock", "orchestrator.lock"):
            lock_file = self.repo_dir / ".dev" / lock_name
            if lock_file.exists():
                # 1. PID チェック
                try:
                    raw = lock_file.read_text(encoding="utf-8").strip()
                    if raw:
                        pid = int(raw)
                        os.kill(pid, 0)
                        return RecoveryResult(
                            success=False,
                            applied_actions=[],
                            resulting_state="UNCHANGED",
                            message=f"Orchestrator process (PID: {pid}) is actively running. Recovery apply rejected.",
                        )
                except (ProcessLookupError, ValueError):
                    pass
                except OSError:
                    pass

                # 2. flock 排他ロックチェック
                try:
                    with open(lock_file, "a+") as f:
                        fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except (BlockingIOError, OSError):
                    return RecoveryResult(
                        success=False,
                        applied_actions=[],
                        resulting_state="UNCHANGED",
                        message="Orchestrator process is actively running (exclusive lock held). Recovery apply rejected.",
                    )

        report = self.inspect()
        applied: list[str] = []

        if report.diagnosis == RecoveryDiagnosis.HEALTHY:
            return RecoveryResult(
                success=True,
                applied_actions=[],
                resulting_state="UNCHANGED",
                message="System is already healthy. No recovery action needed.",
            )

        if not report.can_auto_recover:
            return RecoveryResult(
                success=False,
                applied_actions=[],
                resulting_state="UNCHANGED",
                message=f"Cannot auto-recover from {report.diagnosis.value}: {report.explanation}",
            )

        # 1. Scenario 03 復旧: コミットを再利用して JobState を前進 (P1-1)
        if report.diagnosis == RecoveryDiagnosis.COMMITTED_AHEAD_OF_STATE:
            job_state = JobState.load(self.state_path)
            task_info = {
                "task_id": report.task_id or "1",
                "title": f"Task {report.task_id}",
                "commit": report.head_commit,
                "evidence_tree_sha": report.evidence_tree_sha,
                "committed_tree_sha": report.committed_tree_sha,
                "verifier": "verify_local.sh (pre-crash verified)",
            }
            # 重複追加を避ける
            if not any(t.get("task_id") == report.task_id for t in job_state.completed_tasks):
                job_state.completed_tasks.append(task_info)

            # PLAN.md の確定タスクを考慮し、後続未完了タスクを確認 (P1-1)
            plan_path = self.repo_dir / "PLAN.md"
            from auto_developer.dispatcher import TaskDispatcher

            dispatcher = TaskDispatcher()
            pending_tasks = []
            if plan_path.exists():
                all_tasks = dispatcher.parse_plan(plan_path)
                # report.task_id はコミット確定済みのため pending から除外
                pending_tasks = [
                    t for t in all_tasks
                    if t.task_id != report.task_id and not t.is_completed and not t.is_failed
                ]

            if pending_tasks:
                job_state.status = "RUNNING"
                job_state.transition_phase("INIT")
                job_state.checkpoint_hash = report.head_commit
                resulting_state = "RUNNING"
                applied.append(
                    f"Advanced task {report.task_id} with commit {report.head_commit[:7]}; "
                    f"{len(pending_tasks)} task(s) remaining."
                )
            else:
                job_state.status = "COMPLETED"
                job_state.transition_phase("COMMIT")
                resulting_state = "COMPLETED"
                applied.append(f"Advanced task {report.task_id} with commit {report.head_commit[:7]}; all tasks completed.")

            job_state.save(self.state_path)
            applied.append(f"Updated job_state.json with commit {report.head_commit[:7]}")

            if report.task_id:
                self.evidence_store.archive_record(
                    task_id=report.task_id,
                    status="COMPLETED",
                    commit_info={
                        "sha": report.head_commit,
                        "tree_sha": report.committed_tree_sha,
                        "tree_verified": True,
                    },
                )
                applied.append(f"Archived evidence record for task {report.task_id}")

            # HEAD コミットと作業ツリーを完全同期してクリーン性を保証
            self.git_guard._run_git(["checkout", report.head_commit, "--", "."])

            return RecoveryResult(
                success=True,
                applied_actions=applied,
                resulting_state=resulting_state,
                message=f"Successfully advanced task {report.task_id} with verified commit {report.head_commit[:7]}.",
            )

        # 2. Scenario 04 復旧: 隔離ブランチへのコミット確定、tar検証、ベースブランチ復帰 (P0-2)
        if report.diagnosis == RecoveryDiagnosis.QUARANTINE_INCOMPLETE:
            # 2-1. 隔離ブランチ上で未コミット成果（未追跡ファイル・変更）が存在する場合、まず隔離ブランチに確実にコミット
            status_proc = self.git_guard._run_git(["status", "--porcelain"])
            has_uncommitted = bool(status_proc.stdout.strip())
            if has_uncommitted:
                self.git_guard._run_git(["add", "-A"])
                self.git_guard._run_git([
                    "commit",
                    "-m",
                    f"quarantine: preserve interrupted work for task {report.task_id or 'unknown'}",
                ])
                applied.append(f"Committed uncommitted work to quarantine branch '{report.current_branch}'")

            # 2-2. tar アーカイブの整合性を検証。無効または未作成なら正規 tar アーカイブを生成
            quarantine_dir = self.repo_dir / ".dev" / "quarantines"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            snapshots = sorted(quarantine_dir.glob("snapshot_task_*.tar.gz"))
            valid_snapshot = None
            for s in snapshots:
                try:
                    if tarfile.is_tarfile(s):
                        with tarfile.open(s, "r:gz") as tar:
                            tar.getmembers()
                        valid_snapshot = s
                except (OSError, tarfile.TarError):
                    continue

            if not valid_snapshot:
                # アーカイブが存在しないか破損しているため、新規にスナップショットを生成
                import time

                new_snap_path = quarantine_dir / f"snapshot_task_{report.task_id or 'recovered'}_{int(time.time())}.tar.gz"
                with tarfile.open(new_snap_path, "w:gz") as tar:
                    for p in sorted(self.repo_dir.rglob("*")):
                        if not p.is_file():
                            continue
                        rel_path = p.relative_to(self.repo_dir)
                        str_rel = str(rel_path)
                        if str_rel.startswith(".git") or str_rel.startswith(".dev"):
                            continue
                        tar.add(p, arcname=str_rel)
                valid_snapshot = new_snap_path
                applied.append(f"Created verified quarantine archive at {new_snap_path.name}")
            else:
                applied.append(f"Verified quarantine archive integrity at {valid_snapshot.name}")

            # 2-3. コミット・アーカイブの保全が確認できてから、ベースブランチへの復帰
            self.git_guard._run_git(["checkout", report.base_branch])
            applied.append(f"Switched branch to base branch '{report.base_branch}'")

            # 2-4. 本線作業ツリーを checkpoint に完全クリーン復元
            self.git_guard._run_git(["checkout", report.checkpoint_commit, "--", "."])
            self.git_guard._run_git(["clean", "-fd"])
            applied.append(f"Cleanly restored base branch to checkpoint {report.checkpoint_commit[:7]}")

            # 2-5. JobState を FAILED / QUARANTINE 完了として保存
            job_state = JobState.load(self.state_path)
            job_state.status = "FAILED"
            job_state.transition_phase("QUARANTINE")
            job_state.quarantined_task = {
                "task_id": report.task_id or "1",
                "phase": "QUARANTINE",
                "quarantined_branch": report.current_branch,
                "log_path": "logs/nightly/quarantine_recovered.log",
                "root_cause_summary": "Recovered from interrupted quarantine crash.",
            }
            job_state.save(self.state_path)
            applied.append(f"Finalized job_state.json as FAILED (Quarantined on {report.current_branch})")

            return RecoveryResult(
                success=True,
                applied_actions=applied,
                resulting_state="FAILED",
                message=f"Safely preserved quarantine work to '{report.current_branch}' and restored base branch.",
            )

        # 3. Scenario 01 & 02 復旧: 実行中クラッシュからのクリーン復元
        if report.diagnosis == RecoveryDiagnosis.CRASHED_IN_FLIGHT:
            # 未コミット差分や未追跡ファイルが存在する場合、先に確実に quarantine 退避する (P0-1)
            if not report.tree_clean:
                quarantine_branch = self.git_guard.quarantine_and_restore(
                    checkpoint=report.checkpoint_commit,
                    task_id=report.task_id or "crashed",
                )
                applied.append(f"Preserved uncommitted in-flight work to branch '{quarantine_branch}' and snapshot")
            else:
                self.git_guard._run_git(["checkout", report.checkpoint_commit, "--", "."])
                self.git_guard._run_git(["clean", "-fd"])
                applied.append(f"Confirmed clean working tree at checkpoint {report.checkpoint_commit[:7]}")

            job_state = JobState.load(self.state_path)
            job_state.status = "RUNNING"
            job_state.transition_phase("INIT")
            job_state.save(self.state_path)
            applied.append("Reset JobState phase to INIT for safe resumption")

            return RecoveryResult(
                success=True,
                applied_actions=applied,
                resulting_state="RESET_FOR_RETRY",
                message="Safely preserved in-flight work to quarantine and restored clean working tree.",
            )

        return RecoveryResult(
            success=False,
            applied_actions=[],
            resulting_state="UNCHANGED",
            message=f"Unhandled recovery diagnosis: {report.diagnosis}",
        )
