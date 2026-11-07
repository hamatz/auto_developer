from __future__ import annotations

import logging
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
