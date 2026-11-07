from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from auto_developer.bundler import EvidenceBundler
from auto_developer.guard import GitGuard
from auto_developer.verifier import DeterministicVerifier

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
    ) -> None:
        self.repo_dir = Path(repo_dir or Path.cwd()).resolve()
        self.bundler = bundler or EvidenceBundler(repo_dir=self.repo_dir)
        self.git_guard = git_guard or GitGuard(repo_dir=self.repo_dir)
        self.verifier = verifier or DeterministicVerifier()

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

            if (
                team_input.head_commit
                and verification.head_commit
                and team_input.head_commit != verification.head_commit
            ):
                    err = (
                        f"Team '{team_input.team_id}' head commit mismatch: "
                        f"expected {team_input.head_commit}, bundle has {verification.head_commit}"
                    )
                    errors.append(err)

        is_valid = len(errors) == 0
        return is_valid, audits, errors

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def create_integration_branch(self, job_id: str, base_commit: str = "HEAD") -> str:
        """ベースコミットから新規統合ブランチ integration/{job_id} を作成・チェックアウトする。"""
        branch_name = f"integration/{job_id}"
        self._run_git(["checkout", "-B", branch_name, base_commit])
        return branch_name

    def merge_team_branches(
        self,
        integration_branch: str,
        inputs: list[TeamIntegrationInput],
        original_branch: str | None = None,
    ) -> tuple[bool, str | None, list[str]]:
        """統合ブランチ上で各チームのブランチを順次マージする。

        コンフリクト発生時は即座に推測なき Fail-Closed を発動し、マージを abort して
        original_branch に安全に戻る。
        """
        errors: list[str] = []
        for team_input in inputs:
            branch = team_input.branch
            team_id = team_input.team_id
            msg = f"chore(integration): merge team '{team_id}' ({branch}) into {integration_branch}"
            proc = self._run_git(["merge", "--no-ff", branch, "-m", msg], check=False)
            if proc.returncode != 0:
                self._run_git(["merge", "--abort"], check=False)
                if original_branch:
                    self._run_git(["checkout", original_branch], check=False)
                err = (
                    f"Merge conflict detected while merging team '{team_id}' ({branch}): "
                    f"speculation-free Fail-Closed triggered. Output:\n{proc.stderr or proc.stdout}"
                )
                errors.append(err)
                return False, team_id, errors

        return True, None, []

    def run_post_integration_verification(
        self,
        base_commit: str = "main",
        verify_script: Path | None = None,
        timeout_sec: int = 300,
    ) -> tuple[bool, int, str]:
        """マージ後の統合ブランチ上で全体検証スクリプトを実行する。

        保護ファイル（.github/**, SPEC.md, scripts/verify_local.sh, tests/immunity/**）が
        意図せず変更されていないかも検証する。
        """
        diff_proc = self._run_git(
            ["diff", "--name-only", base_commit, "--", ".github/", "SPEC.md", "scripts/verify_local.sh", "tests/immunity/"],
            check=False,
        )
        if diff_proc.stdout.strip():
            return False, 10, f"Protected files modified during integration:\n{diff_proc.stdout.strip()}"

        script_path = verify_script or (self.repo_dir / "scripts" / "verify_local.sh")
        code, out = self.verifier.run_verification(
            script_path, timeout_sec=timeout_sec, cwd=self.repo_dir
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

        1. 全チーム Evidence Bundle 事前監査 (Fail-Closed)
        2. 統合ブランチ (integration/{job_id}) 作成
        3. 全フィーチャーブランチの順次マージ (衝突時は推測排除で隔離停止)
        4. 全体検証ゲートの実行
        5. 合格時: Release Candidate (rc/{job_id}) 発行
           不合格時: main ブランチへ退避し統合ブランチを隔離
        """
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

        integration_branch = self.create_integration_branch(job_id, base_commit=base_commit)

        merge_ok, conflict_team, merge_errors = self.merge_team_branches(
            integration_branch, inputs, original_branch=original_branch
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
        )
        if not verify_ok:
            self._run_git(["checkout", original_branch], check=False)
            quarantine_branch = f"quarantine/{integration_branch.replace('/', '-')}"
            self._run_git(["branch", "-m", integration_branch, quarantine_branch], check=False)
            return IntegrationResult(
                success=False,
                status="VERIFICATION_FAILED",
                integration_branch=quarantine_branch,
                errors=[f"Post-integration verification failed (Exit {code}):\n{out}"],
                audited_bundles=audits,
                details={"exit_code": code, "output": out},
            )

        rc_commit = self._run_git(["rev-parse", "HEAD"]).stdout.strip()
        rc_tag = f"rc/{job_id}"
        self._run_git(["tag", "-f", rc_tag, rc_commit])

        return IntegrationResult(
            success=True,
            status="SUCCESS",
            integration_branch=integration_branch,
            release_candidate_commit=rc_commit,
            audited_bundles=audits,
            details={"rc_tag": rc_tag, "verified_at_commit": rc_commit},
        )
