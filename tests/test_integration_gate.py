from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.bundler import BundleVerificationResult, EvidenceBundler
from auto_developer.integration_gate import (
    IntegrationGate,
    IntegrationResult,
    TeamIntegrationInput,
)


class TestIntegrationGateAudit(unittest.TestCase):
    """IntegrationGate の基本構造および事前監査ロジックの単体テスト。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name).resolve()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_team_integration_input_and_result_dataclasses(self) -> None:
        bundle_path = self.work_dir / "bundle.zip"
        bundle_path.touch()
        inp = TeamIntegrationInput(team_id="team-a", bundle_path=bundle_path, branch="feature/team-a")
        self.assertEqual(inp.team_id, "team-a")
        self.assertEqual(inp.bundle_path, bundle_path)
        self.assertEqual(inp.branch, "feature/team-a")

        res = IntegrationResult(success=True, status="SUCCESS")
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.errors, [])

    def test_audit_team_inputs_success(self) -> None:
        mock_bundler = MagicMock(spec=EvidenceBundler)
        valid_res = BundleVerificationResult(
            is_valid=True,
            status="VERIFIED",
            job_id="job-1",
            head_commit="commit-123",
            tasks=[{"id": "1", "title": "task 1", "evidence": {"token": "tok1"}}],
        )
        mock_bundler.verify_bundle.return_value = valid_res

        gate = IntegrationGate(repo_dir=self.work_dir, bundler=mock_bundler)
        bundle_path = self.work_dir / "bundle.zip"
        bundle_path.touch()
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=bundle_path, branch="feature/core", head_commit="commit-123")
        ]
        is_valid, audits, errors = gate.audit_team_inputs(inputs)
        self.assertTrue(is_valid)
        self.assertEqual(errors, [])
        self.assertIn("core", audits)
        self.assertEqual(audits["core"]["status"], "VERIFIED")

    def test_audit_team_inputs_rejects_unverified_bundle(self) -> None:
        mock_bundler = MagicMock(spec=EvidenceBundler)
        invalid_res = BundleVerificationResult(
            is_valid=False,
            status="INVALID",
            job_id="job-bad",
            errors=["Tamper detected in Tree SHA"],
        )
        mock_bundler.verify_bundle.return_value = invalid_res

        gate = IntegrationGate(repo_dir=self.work_dir, bundler=mock_bundler)
        bundle_path = self.work_dir / "bad_bundle.zip"
        bundle_path.touch()
        inputs = [
            TeamIntegrationInput(team_id="ui", bundle_path=bundle_path, branch="feature/ui")
        ]
        is_valid, _audits, errors = gate.audit_team_inputs(inputs)
        self.assertFalse(is_valid)
        self.assertGreaterEqual(len(errors), 1)
        self.assertIn("Tamper detected", errors[0])

    def test_audit_team_inputs_rejects_missing_bundle_file(self) -> None:
        mock_bundler = MagicMock(spec=EvidenceBundler)
        gate = IntegrationGate(repo_dir=self.work_dir, bundler=mock_bundler)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=self.work_dir / "non_existent.zip", branch="feature/core")
        ]
        is_valid, _audits, errors = gate.audit_team_inputs(inputs)
        self.assertFalse(is_valid)
        self.assertIn("not found", errors[0])


if __name__ == "__main__":
    unittest.main()
