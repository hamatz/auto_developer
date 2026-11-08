from __future__ import annotations

import logging
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_developer.bundler import EvidenceBundler
from auto_developer.guard import GitGuard
from auto_developer.verifier import DeterministicVerifier
from auto_developer.worktree import WorktreeError, WorktreeManager

logger = logging.getLogger(__name__)


@dataclass
class TeamIntegrationInput:
    """統合対象のチーム成果物定義。"""

    team_id: str
    bundle_path: Path
    branch: str
    head_commit: str | None = None

    def __post_init__(self) -> None:
        self.bundle_path = Path(self.bundle_path)


@dataclass
class IntegrationResult:
    """統合ゲートの実行結果。"""

    success: bool
    status: str  # "SUCCESS" | "AUDIT_FAILED" | "CONFLICT" | "VERIFICATION_FAILED" | "ERROR"
    integration_branch: str | None = None
    release_candidate_commit: str | None = None
    errors: list[str] = field(default_factory=list)
    audited_bundles: dict[str, Any] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": self.success,
            "status": self.status,
            "integration_branch": self.integration_branch,
            "release_candidate_commit": self.release_candidate_commit,
            "errors": self.errors,
            "audited_bundles": self.audited_bundles,
            "details": self.details,
        }


class IntegrationGate:
    """独立自律チーム統合ゲート (Stage 2 IntegrationGate)。

    複数の独立チームが生成した Evidence Bundle を事前監査し、
    安全な隔離統合ブランチ上で順次マージおよび統合後全体検証を実行する。
    """

    def __init__(
        self,
        repo_dir: Path | None = None,
        bundler: EvidenceBundler | None = None,
        git_guard: GitGuard | None = None,
        verifier: DeterministicVerifier | None = None,
        worktree_manager: WorktreeManager | None = None,
    ) -> None:
        self.repo_dir = Path(repo_dir or Path.cwd()).resolve()
        self.bundler = bundler or EvidenceBundler(repo_dir=self.repo_dir)
        self.git_guard = git_guard or GitGuard(repo_dir=self.repo_dir)
        self.verifier = verifier or DeterministicVerifier()
        self.worktree_manager = worktree_manager or WorktreeManager(repo_dir=self.repo_dir)

    def audit_team_inputs(
        self, inputs: list[TeamIntegrationInput]
    ) -> tuple[bool, dict[str, Any], list[str]]:
        """全チームの Evidence Bundle を独立監査する (Fail-Closed)。

        改ざん検知、未承認タスク、バンドル欠損がある場合は即座に False を返す。
        """
        audits: dict[str, Any] = {}
        errors: list[str] = []

        if not inputs:
            return False, {}, ["No team inputs provided for integration"]

        for team_input in inputs:
            bundle_file = Path(team_input.bundle_path).resolve()
            if not bundle_file.is_file():
                err = f"Team '{team_input.team_id}' bundle not found: {bundle_file}"
                errors.append(err)
                continue

            verification = self.bundler.verify_bundle(bundle_file)
            audits[team_input.team_id] = verification.to_dict()

            if not verification.is_valid or verification.status != "VERIFIED":
                err_detail = "; ".join(verification.errors) if verification.errors else f"status is {verification.status}"
                err = f"Team '{team_input.team_id}' bundle audit failed: {err_detail}"
                errors.append(err)
                continue

            bundle_head = (verification.head_commit or "").strip()
            if not bundle_head or len(bundle_head) != 40:
                errors.append(
                    f"Team '{team_input.team_id}' bundle missing valid 40-character head commit: '{bundle_head}'"
                )
                continue

            # P0-1: Git リポジトリ上のブランチ先端コミットを解決し、Bundle 監査済みコミットと完全一致するか照合
            rev_proc = self._run_git(["rev-parse", "--verify", team_input.branch], check=False)
            if rev_proc.returncode != 0:
                errors.append(
                    f"Team '{team_input.team_id}' branch '{team_input.branch}' cannot be resolved in git: {rev_proc.stderr.strip()}"
                )
                continue

            branch_head = rev_proc.stdout.strip()
            if branch_head != bundle_head:
                errors.append(
                    f"Team '{team_input.team_id}' branch '{team_input.branch}' HEAD ({branch_head}) "
                    f"does not match audited bundle HEAD ({bundle_head}): unapproved commit detected"
                )
                continue

            if team_input.head_commit and team_input.head_commit != bundle_head:
                errors.append(
                    f"Team '{team_input.team_id}' head commit mismatch: "
                    f"expected {team_input.head_commit}, bundle has {bundle_head}"
                )
                continue

            audits[team_input.team_id]["verified_commit"] = bundle_head

        is_valid = len(errors) == 0
        return is_valid, audits, errors

    def _run_git(
        self, args: list[str], check: bool = True, cwd: Path | None = None
    ) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=cwd or self.repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def create_integration_branch(self, job_id: str, base_commit: str = "HEAD") -> str:
        """ベースコミットから新規統合ブランチ integration/{job_id} を作成・チェックアウトする。

        P0-2: 既存の統合ブランチが存在する場合は上書きリセット (-B) せず例外を送出する。
        """
        branch_name = f"integration/{job_id}"
        check_proc = self._run_git(["rev-parse", "--verify", f"refs/heads/{branch_name}"], check=False)
        if check_proc.returncode == 0:
            raise ValueError(
                f"Integration branch '{branch_name}' already exists. Refusing to overwrite previous integration run."
            )

        self._run_git(["checkout", "-b", branch_name, base_commit], check=True)
        return branch_name

    def merge_team_branches(
        self,
        integration_branch: str,
        inputs: list[TeamIntegrationInput],
        verified_commits: dict[str, str] | None = None,
        original_branch: str | None = None,
        target_dir: Path | None = None,
    ) -> tuple[bool, str | None, list[str]]:
        """統合ブランチ上で各チームのコミットを順次マージする。

        P0-1: ブランチ名ではなく監査済み固定 Commit SHA を直接指定してマージし、
        マージコミットの第2親 (HEAD^2) が監査済みコミットと完全一致することを検証する。
        コンフリクト発生時は即座に推測なき Fail-Closed を発動し、マージを abort して
        original_branch に安全に戻る。
        """
        run_dir = target_dir or self.repo_dir
        errors: list[str] = []
        for team_input in inputs:
            team_id = team_input.team_id
            commit_target = (verified_commits or {}).get(team_id) or team_input.head_commit or team_input.branch
            msg = f"chore(integration): merge team '{team_id}' ({commit_target[:7]}) into {integration_branch}"
            proc = self._run_git(["merge", "--no-ff", commit_target, "-m", msg], check=False, cwd=run_dir)
            if proc.returncode != 0:
                self._run_git(["merge", "--abort"], check=False, cwd=run_dir)
                if original_branch and not target_dir:
                    self._run_git(["checkout", original_branch], check=False, cwd=run_dir)
                err = (
                    f"Merge conflict detected while merging team '{team_id}' ({commit_target}): "
                    f"speculation-free Fail-Closed triggered. Output:\n{proc.stderr or proc.stdout}"
                )
                errors.append(err)
                return False, team_id, errors

            # P0-1: マージ親 (HEAD^2) の検証
            p2_proc = self._run_git(["rev-parse", "HEAD^2"], check=False, cwd=run_dir)
            if p2_proc.returncode == 0:
                actual_p2 = p2_proc.stdout.strip()
                target_hash_proc = self._run_git(["rev-parse", commit_target], check=False, cwd=run_dir)
                expected_p2 = target_hash_proc.stdout.strip() if target_hash_proc.returncode == 0 else commit_target
                if actual_p2 != expected_p2:
                    self._run_git(["reset", "--hard", "HEAD~1"], check=False, cwd=run_dir)
                    if original_branch and not target_dir:
                        self._run_git(["checkout", original_branch], check=False, cwd=run_dir)
                    err = (
                        f"Merge parent invariant breached for team '{team_id}': "
                        f"expected parent {expected_p2}, but HEAD^2 is {actual_p2}"
                    )
                    errors.append(err)
                    return False, team_id, errors

        return True, None, []

    def run_post_integration_verification(
        self,
        base_commit: str = "main",
        verify_script: Path | None = None,
        timeout_sec: int = 300,
        target_dir: Path | None = None,
    ) -> tuple[bool, int, str]:
        """マージ後の統合ブランチ上で全体検証スクリプトを実行する。

        保護ファイル（.github/**, SPEC.md, scripts/verify_local.sh, tests/immunity/**）が
        意図せず変更されていないかも検証する。
        """
        run_dir = target_dir or self.repo_dir
        diff_proc = self._run_git(
            ["diff", "--name-only", base_commit, "--", ".github/", "SPEC.md", "scripts/verify_local.sh", "tests/immunity/"],
            check=False,
            cwd=run_dir,
        )
        if diff_proc.returncode != 0:
            return False, 10, f"Git diff execution failed during protected file invariant check:\n{diff_proc.stderr or diff_proc.stdout}"
        if diff_proc.stdout.strip():
            return False, 10, f"Protected files modified during integration:\n{diff_proc.stdout.strip()}"

        script_path = verify_script or (run_dir / "scripts" / "verify_local.sh")
        code, out = self.verifier.run_verification(
            script_path, timeout_sec=timeout_sec, cwd=run_dir
        )
        return code == 0, code, out

    def execute_integration(
        self,
        job_id: str,
        inputs: list[TeamIntegrationInput],
        base_commit: str = "main",
        verify_script: Path | None = None,
        timeout_sec: int = 300,
    ) -> IntegrationResult:
        """独立自律チーム統合パイプラインのエンドツーエンド実行。

        BB.1: 専用 Integration Worktree を用いて親の作業ツリーを完全非汚染に保持する。
        BB.2: try / finally により例外発生時も Worktree を確実に破棄し安全に結果を返却する。
        """
        # P0-2: 実行開始前の作業ツリーがクリーンであることを検査
        clean_proc = self._run_git(["status", "--porcelain"], check=False)
        if clean_proc.returncode != 0 or clean_proc.stdout.strip():
            return IntegrationResult(
                success=False,
                status="UNCLEAN_TREE",
                errors=["Working tree contains uncommitted changes. Clean working tree is required before integration."],
            )

        current_branch_proc = self._run_git(["branch", "--show-current"], check=False)
        original_branch = current_branch_proc.stdout.strip() or base_commit

        audit_ok, audits, audit_errors = self.audit_team_inputs(inputs)
        if not audit_ok:
            return IntegrationResult(
                success=False,
                status="AUDIT_FAILED",
                errors=audit_errors,
                audited_bundles=audits,
            )

        verified_commits = {
            tid: info["verified_commit"]
            for tid, info in audits.items()
            if isinstance(info, dict) and "verified_commit" in info
        }

        integration_branch = f"integration/{job_id}"
        # P0-2: 既存の統合ブランチが存在する場合は上書きリセット (-B) せず拒絶
        check_proc = self._run_git(["rev-parse", "--verify", f"refs/heads/{integration_branch}"], check=False)
        if check_proc.returncode == 0:
            return IntegrationResult(
                success=False,
                status="BRANCH_ALREADY_EXISTS",
                errors=[f"Integration branch '{integration_branch}' already exists. Refusing to overwrite previous integration run."],
                audited_bundles=audits,
            )

        # BB.1: 専用 Worktree 用の team_id を生成
        cleaned_id = re.sub(r"[^a-z0-9_-]", "-", job_id.lower()).strip("-")
        wt_team_id = f"int-{cleaned_id}"[:64]

        worktree_created = False
        target_dir: Path | None = None
        try:
            target_dir = self.worktree_manager.create_worktree(
                team_id=wt_team_id,
                base_branch=base_commit,
                branch_name=integration_branch,
            )
            worktree_created = True

            merge_ok, conflict_team, merge_errors = self.merge_team_branches(
                integration_branch,
                inputs,
                verified_commits=verified_commits,
                original_branch=original_branch,
                target_dir=target_dir,
            )
            if not merge_ok:
                return IntegrationResult(
                    success=False,
                    status="CONFLICT",
                    integration_branch=integration_branch,
                    errors=merge_errors,
                    audited_bundles=audits,
                    details={"conflict_team": conflict_team},
                )

            verify_ok, code, out = self.run_post_integration_verification(
                base_commit=base_commit,
                verify_script=verify_script,
                timeout_sec=timeout_sec,
                target_dir=target_dir,
            )
            if not verify_ok:
                # Worktree を先に削除してから隔離ブランチへリネーム
                self.worktree_manager.remove_worktree(wt_team_id, force=True, delete_branch=False)
                worktree_created = False
                timestamp = int(time.time())
                quarantine_branch = f"quarantine/{integration_branch.replace('/', '-')}-{timestamp}"
                self._run_git(["branch", "-m", integration_branch, quarantine_branch], check=False)
                return IntegrationResult(
                    success=False,
                    status="VERIFICATION_FAILED",
                    integration_branch=quarantine_branch,
                    errors=[f"Post-integration verification failed (Exit {code}):\n{out}"],
                    audited_bundles=audits,
                    details={"exit_code": code, "output": out},
                )

            rc_commit = self._run_git(["rev-parse", "HEAD"], check=True, cwd=target_dir).stdout.strip()
            rc_tag = f"rc/{job_id}"
            # P1-2: Release Candidate タグの冪等性と不変性検査
            tag_check = self._run_git(["rev-parse", "--verify", f"refs/tags/{rc_tag}"], check=False)
            if tag_check.returncode == 0:
                existing_commit = tag_check.stdout.strip()
                if existing_commit != rc_commit:
                    return IntegrationResult(
                        success=False,
                        status="RC_TAG_IMMUTABLE_BREACH",
                        integration_branch=integration_branch,
                        errors=[
                            (
                                f"RC tag '{rc_tag}' already exists pointing to different commit {existing_commit}. "
                                f"Overwriting release candidate tags is forbidden (Immutable RC Invariant)."
                            )
                        ],
                        audited_bundles=audits,
                    )
            else:
                self._run_git(["tag", rc_tag, rc_commit], check=True)

            return IntegrationResult(
                success=True,
                status="SUCCESS",
                integration_branch=integration_branch,
                release_candidate_commit=rc_commit,
                audited_bundles=audits,
                details={"rc_tag": rc_tag, "verified_at_commit": rc_commit},
            )

        except Exception as e:
            logger.exception("Unexpected error during integration execution")
            return IntegrationResult(
                success=False,
                status="ERROR",
                integration_branch=integration_branch,
                errors=[f"Unexpected error during integration execution: {e}"],
                audited_bundles=audits,
            )
        finally:
            if worktree_created:
                try:
                    self.worktree_manager.remove_worktree(wt_team_id, force=True, delete_branch=False)
                except (OSError, subprocess.CalledProcessError, WorktreeError) as err:
                    logger.warning("Failed to clean up integration worktree '%s': %s", wt_team_id, err)
