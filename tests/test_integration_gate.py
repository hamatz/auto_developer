from __future__ import annotations

import subprocess
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


class TestIntegrationGateMerge(unittest.TestCase):
    """統合用ブランチの作成、順次マージ、およびマージ競合遮断の単体テスト。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name).resolve()
        # Git初期化
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "Tester"])
        self._run_git(["config", "user.email", "tester@example.com"])
        (self.repo_dir / "base.txt").write_text("base content\n", encoding="utf-8")
        self._run_git(["add", "base.txt"])
        self._run_git(["commit", "-m", "initial commit"])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_create_integration_branch_and_merge_success(self) -> None:
        # feature/core ブランチ作成
        self._run_git(["checkout", "-b", "feature/core", "main"])
        (self.repo_dir / "core.txt").write_text("core file\n", encoding="utf-8")
        self._run_git(["add", "core.txt"])
        self._run_git(["commit", "-m", "feat: core"])

        # feature/ui ブランチ作成
        self._run_git(["checkout", "-b", "feature/ui", "main"])
        (self.repo_dir / "ui.txt").write_text("ui file\n", encoding="utf-8")
        self._run_git(["add", "ui.txt"])
        self._run_git(["commit", "-m", "feat: ui"])

        # main に戻る
        self._run_git(["checkout", "main"])

        gate = IntegrationGate(repo_dir=self.repo_dir)
        branch_name = gate.create_integration_branch("job-test-1", base_commit="main")
        self.assertEqual(branch_name, "integration/job-test-1")

        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=Path("dummy.zip"), branch="feature/core"),
            TeamIntegrationInput(team_id="ui", bundle_path=Path("dummy.zip"), branch="feature/ui"),
        ]
        ok, conflict_team, errors = gate.merge_team_branches(branch_name, inputs)
        self.assertTrue(ok)
        self.assertIsNone(conflict_team)
        self.assertEqual(errors, [])
        self.assertTrue((self.repo_dir / "core.txt").exists())
        self.assertTrue((self.repo_dir / "ui.txt").exists())

    def test_merge_team_branches_conflict_fails_closed(self) -> None:
        # feature/core ブランチで shared.txt を作成
        self._run_git(["checkout", "-b", "feature/core", "main"])
        (self.repo_dir / "shared.txt").write_text("core conflict line\n", encoding="utf-8")
        self._run_git(["add", "shared.txt"])
        self._run_git(["commit", "-m", "feat: core conflict"])

        # feature/ui ブランチで shared.txt を競合内容で作成
        self._run_git(["checkout", "-b", "feature/ui", "main"])
        (self.repo_dir / "shared.txt").write_text("ui conflict line\n", encoding="utf-8")
        self._run_git(["add", "shared.txt"])
        self._run_git(["commit", "-m", "feat: ui conflict"])

        # main に戻る
        self._run_git(["checkout", "main"])

        gate = IntegrationGate(repo_dir=self.repo_dir)
        branch_name = gate.create_integration_branch("job-conflict-1", base_commit="main")

        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=Path("dummy.zip"), branch="feature/core"),
            TeamIntegrationInput(team_id="ui", bundle_path=Path("dummy.zip"), branch="feature/ui"),
        ]
        ok, conflict_team, errors = gate.merge_team_branches(branch_name, inputs, original_branch="main")
        self.assertFalse(ok)
        self.assertEqual(conflict_team, "ui")
        self.assertGreaterEqual(len(errors), 1)
        self.assertIn("conflict", errors[0].lower())

        # main ブランチに安全に戻っていること（main が汚染されていないこと）
        current_branch = self._run_git(["branch", "--show-current"]).stdout.strip()
        self.assertEqual(current_branch, "main")
        self.assertFalse((self.repo_dir / "shared.txt").exists())


class TestIntegrationGateExecution(unittest.TestCase):
    """統合実行パイプライン（事前監査、マージ、全体検証、RC発行・隔離）の単体テスト。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name).resolve()
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "Tester"])
        self._run_git(["config", "user.email", "tester@example.com"])
        (self.repo_dir / "main.txt").write_text("main\n", encoding="utf-8")
        self._run_git(["add", "main.txt"])
        self._run_git(["commit", "-m", "init main"])

        # feature/core
        self._run_git(["checkout", "-b", "feature/core", "main"])
        (self.repo_dir / "core.txt").write_text("core\n", encoding="utf-8")
        self._run_git(["add", "core.txt"])
        self._run_git(["commit", "-m", "feat: core"])

        # feature/ui
        self._run_git(["checkout", "-b", "feature/ui", "main"])
        (self.repo_dir / "ui.txt").write_text("ui\n", encoding="utf-8")
        self._run_git(["add", "ui.txt"])
        self._run_git(["commit", "-m", "feat: ui"])

        self._run_git(["checkout", "main"])

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    def test_execute_integration_success_issues_rc(self) -> None:
        mock_bundler = MagicMock(spec=EvidenceBundler)
        valid_res = BundleVerificationResult(is_valid=True, status="VERIFIED", job_id="job-1")
        mock_bundler.verify_bundle.return_value = valid_res

        mock_verifier = MagicMock()
        mock_verifier.run_verification.return_value = (0, "All checks passed!")

        gate = IntegrationGate(repo_dir=self.repo_dir, bundler=mock_bundler, verifier=mock_verifier)
        bundle_core = self.repo_dir / "core.zip"
        bundle_ui = self.repo_dir / "ui.zip"
        bundle_core.touch()
        bundle_ui.touch()

        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=bundle_core, branch="feature/core"),
            TeamIntegrationInput(team_id="ui", bundle_path=bundle_ui, branch="feature/ui"),
        ]

        res = gate.execute_integration("job-succ-1", inputs, base_commit="main")
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertIsNotNone(res.release_candidate_commit)
        # タグ rc/job-succ-1 が存在すること
        tag_commit = self._run_git(["rev-parse", "rc/job-succ-1"]).stdout.strip()
        self.assertEqual(tag_commit, res.release_candidate_commit)

    def test_execute_integration_post_verification_failure_rolls_back(self) -> None:
        mock_bundler = MagicMock(spec=EvidenceBundler)
        valid_res = BundleVerificationResult(is_valid=True, status="VERIFIED", job_id="job-1")
        mock_bundler.verify_bundle.return_value = valid_res

        mock_verifier = MagicMock()
        mock_verifier.run_verification.return_value = (1, "Integration tests failed!")

        gate = IntegrationGate(repo_dir=self.repo_dir, bundler=mock_bundler, verifier=mock_verifier)
        bundle_core = self.repo_dir / "core.zip"
        bundle_ui = self.repo_dir / "ui.zip"
        bundle_core.touch()
        bundle_ui.touch()

        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=bundle_core, branch="feature/core"),
            TeamIntegrationInput(team_id="ui", bundle_path=bundle_ui, branch="feature/ui"),
        ]

        res = gate.execute_integration("job-fail-1", inputs, base_commit="main")
        self.assertFalse(res.success)
        self.assertEqual(res.status, "VERIFICATION_FAILED")
        # 元のブランチ (main) に戻っていること
        current_branch = self._run_git(["branch", "--show-current"]).stdout.strip()
        self.assertEqual(current_branch, "main")


if __name__ == "__main__":
    unittest.main()
