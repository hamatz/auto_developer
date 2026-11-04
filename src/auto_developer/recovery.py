from __future__ import annotations

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
        except Exception as e:
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
        quarantine_snapshots = list(self.repo_dir.glob(".dev/quarantines/snapshot_task_*.tar.gz"))
        latest_snapshot = str(quarantine_snapshots[-1]) if quarantine_snapshots else None

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
                        import json

                        evidence_rec = json.loads(token_path.read_text(encoding="utf-8"))
                    except Exception:
                        pass

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
                except Exception:
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
        if job_state.status == "RUNNING":
            if head_commit == checkpoint:
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

        # 4. 正常な状態
        if tree_clean and current_branch == base_branch:
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
            explanation="Unrecognized state inconsistency detected.",
            recommended_action="Manual inspection required.",
        )
