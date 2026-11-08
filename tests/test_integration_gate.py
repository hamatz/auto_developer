from __future__ import annotations

import hashlib
import json
import multiprocessing
import os
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.bundler import BundleVerificationResult, EvidenceBundler
from auto_developer.integration_gate import (
    IntegrationGate,
    IntegrationJobState,
    IntegrationResult,
    TeamIntegrationInput,
)


def _child_gate_sigkill_worker(
    repo_dir: Path,
    job_id: str,
    inputs: list[TeamIntegrationInput],
    base_commit: str,
    verify_script: Path,
    fault_checkpoint: str,
) -> None:
    def _fault_hook(checkpoint: str, context: dict[str, Any]) -> None:
        if checkpoint == fault_checkpoint:
            os.kill(os.getpid(), signal.SIGKILL)

    gate = IntegrationGate(repo_dir=repo_dir, fault_hook=_fault_hook)
    gate.execute_integration(
        job_id=job_id,
        inputs=inputs,
        base_commit=base_commit,
        verify_script=verify_script,
        resume=False,
    )


class TestIntegrationGateAudit(unittest.TestCase):
    """IntegrationGate の基本構造および事前監査ロジックの単体テスト。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.work_dir = Path(self.temp_dir.name).resolve()
        subprocess.run(["git", "init", "-b", "main"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.name", "Tester"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=self.work_dir, capture_output=True, check=True)
        (self.work_dir / "init.txt").write_text("init\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.work_dir, capture_output=True, check=True)

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
        # feature/core ブランチ作成
        subprocess.run(["git", "checkout", "-b", "feature/core"], cwd=self.work_dir, capture_output=True, check=True)
        (self.work_dir / "core.txt").write_text("core\n", encoding="utf-8")
        subprocess.run(["git", "add", "core.txt"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "core"], cwd=self.work_dir, capture_output=True, check=True)
        core_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.work_dir, capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "checkout", "main"], cwd=self.work_dir, capture_output=True, check=True)

        mock_bundler = MagicMock(spec=EvidenceBundler)
        valid_res = BundleVerificationResult(
            is_valid=True,
            status="VERIFIED",
            job_id="job-1",
            head_commit=core_head,
            tasks=[{"id": "1", "title": "task 1", "evidence": {"token": "tok1"}}],
        )
        mock_bundler.verify_bundle.return_value = valid_res

        gate = IntegrationGate(repo_dir=self.work_dir, bundler=mock_bundler)
        bundle_path = self.work_dir / "bundle.zip"
        bundle_path.touch()
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=bundle_path, branch="feature/core", head_commit=core_head)
        ]
        is_valid, audits, errors = gate.audit_team_inputs(inputs)
        self.assertTrue(is_valid)
        self.assertEqual(errors, [])
        self.assertIn("core", audits)
        self.assertEqual(audits["core"]["status"], "VERIFIED")
        self.assertEqual(audits["core"]["verified_commit"], core_head)

    def test_audit_team_inputs_rejects_branch_head_mismatch_with_bundle(self) -> None:
        """P0-1: ブランチ先端が Bundle 監査済みコミットから進んでいたり異なる場合、即座に拒絶する。"""
        # feature/core ブランチでコミットA作成
        subprocess.run(["git", "checkout", "-b", "feature/core"], cwd=self.work_dir, capture_output=True, check=True)
        (self.work_dir / "core.txt").write_text("core commit A\n", encoding="utf-8")
        subprocess.run(["git", "add", "core.txt"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "core A"], cwd=self.work_dir, capture_output=True, check=True)
        commit_a = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.work_dir, capture_output=True, text=True, check=True).stdout.strip()

        # その後ブランチが勝手に進んでコミットBになる
        (self.work_dir / "core.txt").write_text("core unapproved commit B\n", encoding="utf-8")
        subprocess.run(["git", "add", "core.txt"], cwd=self.work_dir, capture_output=True, check=True)
        subprocess.run(["git", "commit", "-m", "core B"], cwd=self.work_dir, capture_output=True, check=True)
        _commit_b = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.work_dir, capture_output=True, text=True, check=True).stdout.strip()
        subprocess.run(["git", "checkout", "main"], cwd=self.work_dir, capture_output=True, check=True)

        mock_bundler = MagicMock(spec=EvidenceBundler)
        # Bundle は commit A を検証している
        bundle_res = BundleVerificationResult(
            is_valid=True,
            status="VERIFIED",
            job_id="job-1",
            head_commit=commit_a,
            tasks=[{"id": "1", "title": "task 1", "evidence": {"token": "tok1"}}],
        )
        mock_bundler.verify_bundle.return_value = bundle_res

        gate = IntegrationGate(repo_dir=self.work_dir, bundler=mock_bundler)
        bundle_path = self.work_dir / "bundle.zip"
        bundle_path.touch()
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=bundle_path, branch="feature/core", head_commit=commit_a)
        ]
        is_valid, _audits, errors = gate.audit_team_inputs(inputs)
        self.assertFalse(is_valid, "Branch HEAD (commit B) differing from Bundle (commit A) must fail audit")
        self.assertTrue(any("does not match audited bundle HEAD" in err for err in errors))

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

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def test_create_integration_branch_refuses_to_overwrite_existing(self) -> None:
        """P0-2: 既存の統合ブランチが存在する場合、-B でリセット・上書きせず拒絶する。"""
        gate = IntegrationGate(repo_dir=self.repo_dir)
        # 初回作成
        gate.create_integration_branch("job-existing-1", base_commit="main")
        self._run_git(["checkout", "main"])

        # 同一 Job ID で再作成を試みた場合、上書きせず例外を送出すること
        with self.assertRaises(ValueError) as ctx:
            gate.create_integration_branch("job-existing-1", base_commit="main")
        self.assertIn("already exists", str(ctx.exception))

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
        (self.repo_dir / ".gitignore").write_text("*.zip\n.dev/\n", encoding="utf-8")
        (self.repo_dir / "main.txt").write_text("main\n", encoding="utf-8")
        self._run_git(["add", "."])
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

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def test_execute_integration_success_issues_rc(self) -> None:
        core_commit = self._run_git(["rev-parse", "feature/core"]).stdout.strip()
        ui_commit = self._run_git(["rev-parse", "feature/ui"]).stdout.strip()

        mock_bundler = MagicMock(spec=EvidenceBundler)
        def mock_verify(b_path: Path) -> BundleVerificationResult:
            if "core" in str(b_path):
                return BundleVerificationResult(is_valid=True, status="VERIFIED", job_id="job-core", head_commit=core_commit)
            return BundleVerificationResult(is_valid=True, status="VERIFIED", job_id="job-ui", head_commit=ui_commit)
        mock_bundler.verify_bundle.side_effect = mock_verify

        mock_verifier = MagicMock()
        mock_verifier.run_verification.return_value = (0, "All checks passed!")

        gate = IntegrationGate(repo_dir=self.repo_dir, bundler=mock_bundler, verifier=mock_verifier)
        bundle_core = self.repo_dir / "core.zip"
        bundle_ui = self.repo_dir / "ui.zip"
        bundle_core.touch()
        bundle_ui.touch()

        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=bundle_core, branch="feature/core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=bundle_ui, branch="feature/ui", head_commit=ui_commit),
        ]

        res = gate.execute_integration("job-succ-1", inputs, base_commit="main")
        self.assertTrue(res.success)
        self.assertEqual(res.status, "SUCCESS")
        self.assertIsNotNone(res.release_candidate_commit)
        # タグ rc/job-succ-1 が存在すること
        tag_commit = self._run_git(["rev-parse", "rc/job-succ-1"]).stdout.strip()
        self.assertEqual(tag_commit, res.release_candidate_commit)

    def test_execute_integration_rejects_unclean_working_tree(self) -> None:
        """P0-2: 未コミット変更が存在する場合、統合実行を即座に拒絶する。"""
        gate = IntegrationGate(repo_dir=self.repo_dir)
        (self.repo_dir / "dirty_untracked.txt").write_text("dirty\n", encoding="utf-8")
        res = gate.execute_integration("job-dirty", inputs=[])
        self.assertFalse(res.success)
        self.assertEqual(res.status, "UNCLEAN_TREE")
        (self.repo_dir / "dirty_untracked.txt").unlink()

    def test_execute_integration_post_verification_failure_rolls_back(self) -> None:
        core_commit = self._run_git(["rev-parse", "feature/core"]).stdout.strip()
        ui_commit = self._run_git(["rev-parse", "feature/ui"]).stdout.strip()

        mock_bundler = MagicMock(spec=EvidenceBundler)
        def mock_verify(b_path: Path) -> BundleVerificationResult:
            if "core" in str(b_path):
                return BundleVerificationResult(is_valid=True, status="VERIFIED", job_id="job-core", head_commit=core_commit)
            return BundleVerificationResult(is_valid=True, status="VERIFIED", job_id="job-ui", head_commit=ui_commit)
        mock_bundler.verify_bundle.side_effect = mock_verify

        mock_verifier = MagicMock()
        mock_verifier.run_verification.return_value = (1, "Integration tests failed!")

        gate = IntegrationGate(repo_dir=self.repo_dir, bundler=mock_bundler, verifier=mock_verifier)
        bundle_core = self.repo_dir / "core.zip"
        bundle_ui = self.repo_dir / "ui.zip"
        bundle_core.touch()
        bundle_ui.touch()

        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=bundle_core, branch="feature/core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=bundle_ui, branch="feature/ui", head_commit=ui_commit),
        ]

        res = gate.execute_integration("job-fail-1", inputs, base_commit="main")
        self.assertFalse(res.success)
        self.assertEqual(res.status, "VERIFICATION_FAILED")
        # 元のブランチ (main) に戻っていること
        current_branch = self._run_git(["branch", "--show-current"]).stdout.strip()
        self.assertEqual(current_branch, "main")
        # 隔離ブランチ名にタイムスタンプが含まれること
        self.assertIsNotNone(res.integration_branch)
        self.assertTrue(res.integration_branch.startswith("quarantine/integration-job-fail-1-"))

    def test_post_integration_verification_diff_failure_is_fail_closed(self) -> None:
        """P1-1: 保護ファイル差分検査で git diff がエラー終了した場合、Fail-Closed で遮断する。"""
        gate = IntegrationGate(repo_dir=self.repo_dir)
        # 存在しないコミットを指定して git diff を失敗させる
        ok, code, out = gate.run_post_integration_verification(base_commit="0000000000000000000000000000000000000000")
        self.assertFalse(ok)
        self.assertEqual(code, 10)
        self.assertIn("Git diff execution failed", out)

    def test_rc_tag_idempotence_and_immutability(self) -> None:
        """P1-2: 同一コミットへの RC タグ発行は冪等成功し、異なるコミットへの上書きは拒絶される。"""
        core_commit = self._run_git(["rev-parse", "feature/core"]).stdout.strip()
        ui_commit = self._run_git(["rev-parse", "feature/ui"]).stdout.strip()

        mock_bundler = MagicMock(spec=EvidenceBundler)
        def mock_verify(b_path: Path) -> BundleVerificationResult:
            if "core" in str(b_path):
                return BundleVerificationResult(is_valid=True, status="VERIFIED", job_id="job-core", head_commit=core_commit)
            return BundleVerificationResult(is_valid=True, status="VERIFIED", job_id="job-ui", head_commit=ui_commit)
        mock_bundler.verify_bundle.side_effect = mock_verify

        mock_verifier = MagicMock()
        mock_verifier.run_verification.return_value = (0, "All checks passed!")

        gate = IntegrationGate(repo_dir=self.repo_dir, bundler=mock_bundler, verifier=mock_verifier)
        bundle_core = self.repo_dir / "core.zip"
        bundle_ui = self.repo_dir / "ui.zip"
        bundle_core.touch()
        bundle_ui.touch()

        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=bundle_core, branch="feature/core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=bundle_ui, branch="feature/ui", head_commit=ui_commit),
        ]

        # 1回目実行 -> 成功
        res1 = gate.execute_integration("job-rc-test", inputs, base_commit="main")
        self.assertTrue(res1.success)
        self.assertEqual(res1.status, "SUCCESS")

        # 既存 RC タグを強制的に別のコミットに変更して模擬衝突
        self._run_git(["tag", "-f", "rc/job-rc-conflict", "main"])
        res2 = gate.execute_integration("job-rc-conflict", inputs, base_commit="main")
        self.assertFalse(res2.success)
        self.assertEqual(res2.status, "RC_TAG_IMMUTABLE_BREACH")
        self.assertIn("Overwriting release candidate tags is forbidden", res2.errors[0])


class TestIntegrationGateAcceptance(unittest.TestCase):
    """2チーム統合（Core & UI）の決定論的受入テスト (AA.5)。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name).resolve()
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "IntegrationTester"])
        self._run_git(["config", "user.email", "integration@example.com"])

        # 初期リポジトリ構造の構築
        (self.repo_dir / "SPEC.md").write_text("# SPEC\nCore Requirements", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# PLAN\n- [x] Initial setup", encoding="utf-8")
        (self.repo_dir / ".gitignore").write_text(".dev/\n*.zip\n", encoding="utf-8")
        scripts_dir = self.repo_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        verify_sh = scripts_dir / "verify_local.sh"
        verify_sh.write_text(
            f"#!/usr/bin/env bash\nset -e\n{sys.executable} -m unittest discover -s tests -p 'test_*.py'\n",
            encoding="utf-8",
        )
        verify_sh.chmod(0o755)

        # 結合契約テスト (P1-3): 偽陽性を排除し、インポートや契約の不整合は必ずテスト失敗とする
        tests_dir = self.repo_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_contract.py").write_text(
            "import unittest\n"
            "import sys\n"
            "from pathlib import Path\n"
            "src_path = str(Path(__file__).resolve().parent.parent / 'src')\n"
            "if src_path not in sys.path:\n"
            "    sys.path.insert(0, src_path)\n\n"
            "class ContractTest(unittest.TestCase):\n"
            "    def test_core_ui_contract(self):\n"
            "        from core.app import get_core\n"
            "        from ui.view import render\n"
            "        self.assertEqual(render(), 'Render: 42')\n",
            encoding="utf-8",
        )

        self._run_git(["add", "."])
        self._run_git(["commit", "-m", "init: repository structure"])
        self.base_commit = self._run_git(["rev-parse", "HEAD"]).stdout.strip()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def _create_team_work_and_bundle(
        self, team_id: str, branch: str, file_path: str, content: str
    ) -> tuple[Path, str]:
        # ブランチ作成
        self._run_git(["checkout", "-b", branch, "main"])
        target = self.repo_dir / file_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        self._run_git(["add", file_path])
        self._run_git(["commit", "-m", f"feat({team_id}): implement {file_path}"])

        head_commit = self._run_git(["rev-parse", "HEAD"]).stdout.strip()
        head_tree = self._run_git(["rev-parse", "HEAD^{tree}"]).stdout.strip()
        spec_sha = hashlib.sha256((self.repo_dir / "SPEC.md").read_bytes()).hexdigest()

        # Evidence Record 作成
        records_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
        records_dir.mkdir(parents=True, exist_ok=True)
        for old_file in records_dir.glob("*.json"):
            old_file.unlink()
        (records_dir / f"task_{team_id}.json").write_text(
            json.dumps({
                "task_id": team_id,
                "checkpoint": self.base_commit,
                "commit": head_commit,
                "tree_sha": head_tree,
                "spec_sha": spec_sha,
                "checker": {"decision": "APPROVE", "reason": f"Team {team_id} tests pass"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )

        bundler = EvidenceBundler(repo_dir=self.repo_dir)
        bundle_path = self.repo_dir / f"bundle_{team_id}.zip"
        bundler.create_bundle(dest_path=bundle_path)

        # main に戻る
        self._run_git(["checkout", "main"])
        return bundle_path, head_commit

    def test_two_team_integration_success_scenario(self) -> None:
        """P1-3: Core と UI の実機能結合（Core 関数呼び出し）が全体検証を通過し RC が発行される。"""
        # Core チームの成果物とバンドル作成
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic\ndef get_core():\n    return 42\n",
        )

        # UI チームの成果物とバンドル作成 (Core の get_core() を実際にインポート呼び出し)
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )

        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        result = gate.execute_integration(
            job_id="job-integration-e2e",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.integration_branch, "integration/job-integration-e2e")
        self.assertIsNotNone(result.release_candidate_commit)

        # 統合ブランチ上に両チームのファイルが存在し、実機能結合が機能すること
        self._run_git(["checkout", result.integration_branch])
        self.assertTrue((self.repo_dir / "src" / "core" / "app.py").exists())
        self.assertTrue((self.repo_dir / "src" / "ui" / "view.py").exists())

        # Release Candidate タグが一致すること
        rc_tag_commit = self._run_git(["rev-parse", "rc/job-integration-e2e"]).stdout.strip()
        self.assertEqual(rc_tag_commit, result.release_candidate_commit)

        # main ブランチが不変・無汚染であること
        self._run_git(["checkout", "main"])
        main_commit = self._run_git(["rev-parse", "HEAD"]).stdout.strip()
        self.assertEqual(main_commit, self.base_commit)

    def test_integration_preserves_parent_workspace_and_branch(self) -> None:
        """P1-1: execute_integration 実行中・実行後も呼び出し元のブランチおよび作業ツリーが一切切り替わらない。"""
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic\ndef get_core():\n    return 42\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )
        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        # 呼び出し前: main ブランチにいること
        self.assertEqual(self._run_git(["branch", "--show-current"]).stdout.strip(), "main")

        result = gate.execute_integration(
            job_id="job-preserves-parent",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )
        self.assertTrue(result.success)

        # 呼び出し後: 親リポジトリのカレントブランチは依然として main であり、HEAD も不変であること
        current_branch = self._run_git(["branch", "--show-current"]).stdout.strip()
        self.assertEqual(current_branch, "main", f"Parent branch was modified to '{current_branch}'!")
        current_head = self._run_git(["rev-parse", "HEAD"]).stdout.strip()
        self.assertEqual(current_head, self.base_commit)

    def test_integration_rc_breach_preserves_parent_workspace_and_cleans_worktree(self) -> None:
        """P1-2: RCタグ衝突時にも親の作業ツリーは保護され、Worktree が適切に破棄される。"""
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic\ndef get_core():\n    return 42\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )
        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        # 異なるコミットを指す既存の RC タグを作成しておく
        self._run_git(["tag", "rc/job-rc-conflict", self.base_commit])

        result = gate.execute_integration(
            job_id="job-rc-conflict",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "RC_TAG_IMMUTABLE_BREACH")

        # 親リポジトリのカレントブランチは依然として main であること
        current_branch = self._run_git(["branch", "--show-current"]).stdout.strip()
        self.assertEqual(current_branch, "main", f"Parent branch was modified to '{current_branch}'!")

    def test_two_team_integration_semantic_mismatch_quarantine_scenario(self) -> None:
        """P1-3: Gitコンフリクトはないが API 不整合（意味的競合）で結合テストが失敗するケースの安全隔離。"""
        # Core チームは数値を返すべき関数で文字列を返す (API 契約違反)
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic returning invalid contract type\ndef get_core():\n    return 'NOT_A_NUMBER'\n",
        )

        # UI チームは戻り値が 42 であることを期待する
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )

        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        result = gate.execute_integration(
            job_id="job-integration-semantic-fail",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )

        # Git マージ自体は通るが、統合後全体検証でテストが落ちて隔離されること
        self.assertFalse(result.success)
        self.assertEqual(result.status, "VERIFICATION_FAILED")
        self.assertIsNotNone(result.integration_branch)
        self.assertTrue(result.integration_branch.startswith("quarantine/integration-job-integration-semantic-fail-"))

        # main ブランチが不変・無汚染であること
        current_branch = self._run_git(["branch", "--show-current"]).stdout.strip()
        self.assertEqual(current_branch, "main")
        main_commit = self._run_git(["rev-parse", "HEAD"]).stdout.strip()
        self.assertEqual(main_commit, self.base_commit)
        self.assertFalse((self.repo_dir / "src" / "core" / "app.py").exists())

    def test_two_team_integration_import_error_quarantine_scenario(self) -> None:
        """P1-3: ImportError などの結合欠陥がスキップされず確実に検証失敗・安全隔離される。"""
        # Core チームのモジュール名または関数名が合っていない (get_core ではなく missing_func)
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Missing expected function\ndef other_func():\n    return 42\n",
        )

        # UI チームは get_core をインポートしようとする
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )

        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        result = gate.execute_integration(
            job_id="job-integration-import-fail",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )

        # ImportError (ImportError: cannot import name 'get_core') が握りつぶされず失敗すること
        self.assertFalse(result.success)
        self.assertEqual(result.status, "VERIFICATION_FAILED")
        self.assertIsNotNone(result.integration_branch)
        self.assertTrue(result.integration_branch.startswith("quarantine/integration-job-integration-import-fail-"))
        self.assertTrue(any("ImportError" in err or "Exit 1" in err for err in result.errors))

    def test_two_team_integration_conflict_quarantine_scenario(self) -> None:
        # Core チームが shared.py を作成
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/shared.py",
            content="SHARED_VERSION = 'core_v1'\n",
        )

        # UI チームが shared.py を競合内容で作成
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/shared.py",
            content="SHARED_VERSION = 'ui_v2'\n",
        )

        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        result = gate.execute_integration(
            job_id="job-integration-conflict",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )

        self.assertFalse(result.success)
        self.assertEqual(result.status, "CONFLICT")
        self.assertIn("ui", result.details.get("conflict_team", ""))
        self.assertIsNotNone(result.integration_branch)
        self.assertTrue(
            result.integration_branch.startswith("quarantine/integration-job-integration-conflict-conflict-"),
            f"Expected quarantine branch with conflict prefix, got: {result.integration_branch}",
        )

        # 通常の統合ブランチ名は残っていないこと
        chk = self._run_git(["rev-parse", "--verify", "refs/heads/integration/job-integration-conflict"], check=False)
        self.assertNotEqual(chk.returncode, 0)

        # main ブランチが不変・無汚染であること
        current_branch = self._run_git(["branch", "--show-current"]).stdout.strip()
        self.assertEqual(current_branch, "main")
        main_commit = self._run_git(["rev-parse", "HEAD"]).stdout.strip()
        self.assertEqual(main_commit, self.base_commit)
        self.assertFalse((self.repo_dir / "src" / "shared.py").exists())

    def test_merge_conflict_recovery_failure_triggers_recovery_required(self) -> None:
        """P1-4: merge conflict 後の abort/復旧に失敗した場合、推測で進めず RECOVERY_REQUIRED を返す。"""
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/shared.py",
            content="SHARED_VERSION = 'core_v1'\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/shared.py",
            content="SHARED_VERSION = 'ui_v2'\n",
        )
        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        # merge --abort が失敗した状態（未解決コンフリクト等で作業ツリーがクリーンに戻らない状態）を模倣
        def faulty_merge(*args: Any, **kwargs: Any) -> tuple[bool, str | None, list[str]]:
            # target_dir 内に未解決ファイルを残して False を返す
            target_dir = kwargs.get("target_dir")
            if target_dir:
                (target_dir / "unresolved_conflict.txt").write_text("conflict left uncleaned\n", encoding="utf-8")
            return False, "ui", ["Simulated abort failure leaving dirty tree"]

        gate.merge_team_branches = faulty_merge  # type: ignore[assignment]

        result = gate.execute_integration(
            job_id="job-recovery-fail",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "RECOVERY_REQUIRED")
        self.assertTrue(result.details.get("recovery_failed", False))

    def test_execute_integration_cleanup_status_recorded_in_details(self) -> None:
        """CC.1 / P1-A: Worktree クリーンアップ成否が IntegrationResult.details に記録・明示される。"""
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic\ndef get_core():\n    return 42\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )
        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        # 正常時
        result = gate.execute_integration(
            job_id="job-cleanup-success",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )
        self.assertTrue(result.success)
        self.assertEqual(result.details.get("cleanup_status"), "SUCCESS")
        self.assertFalse(result.details.get("cleanup_failed", False))
        self.assertIsNotNone(result.details.get("worktree_path"))

        # クリーンアップ失敗時（モック例外）
        def faulty_remove(*args: Any, **kwargs: Any) -> bool:
            raise RuntimeError("Disk locked, failed to remove worktree")

        gate.worktree_manager.remove_worktree = faulty_remove  # type: ignore[assignment]
        result_fail = gate.execute_integration(
            job_id="job-cleanup-fail",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )
        self.assertEqual(result_fail.details.get("cleanup_status"), "FAILED")
        self.assertTrue(result_fail.details.get("cleanup_failed", False))
        self.assertIn("Disk locked", result_fail.details.get("cleanup_error", ""))

    def test_quarantine_rename_failure_triggers_recovery_required(self) -> None:
        """CC.1 / P1-B: 隔離ブランチへのリネームに失敗した場合、安全に RECOVERY_REQUIRED を返す。"""
        # Core チームの契約違反（検証失敗シナリオ）
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Invalid return\ndef get_core():\n    return 'INVALID'\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )
        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        orig_run_git = gate._run_git

        def mock_run_git(args: list[str], *a: Any, **kw: Any) -> subprocess.CompletedProcess[str]:
            if len(args) >= 3 and args[0] == "branch" and args[1] == "-m":
                return subprocess.CompletedProcess(
                    args=["git", *args],
                    returncode=1,
                    stdout="",
                    stderr="fatal: A branch named 'quarantine/...' already exists or cannot be created.",
                )
            return orig_run_git(args, *a, **kw)

        gate._run_git = mock_run_git  # type: ignore[assignment]

        result = gate.execute_integration(
            job_id="job-rename-fail",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "RECOVERY_REQUIRED")
        self.assertTrue(result.details.get("quarantine_rename_failed", False))

    def test_execute_integration_persists_job_state(self) -> None:
        """CC.2 / P1-C: 統合処理実行中に IntegrationJobState がアトミックに永続化される。"""
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic\ndef get_core():\n    return 42\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )
        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        result = gate.execute_integration(
            job_id="job-state-persist",
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
        )
        self.assertTrue(result.success)

        # 状態ファイルが存在し、COMPLETED となっていること
        state_file = self.repo_dir / ".dev" / "integration_jobs" / "job-state-persist.json"
        self.assertTrue(state_file.exists(), f"State file {state_file} does not exist!")
        data = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("job_id"), "job-state-persist")
        self.assertEqual(data.get("state"), "COMPLETED")
        self.assertIsNotNone(data.get("rc_tag"))

    def test_recover_interrupted_job_quarantines_crashed_state(self) -> None:
        """CC.3 / P1-C: 途中で強制終了されたジョブの残存 Worktree とブランチを安全に復旧・隔離する。"""
        gate = IntegrationGate(repo_dir=self.repo_dir)
        job_id = "job-crashed-1"
        branch_name = f"integration/{job_id}"
        wt_id = "int-job-crashed-1"

        # 模擬クラッシュ状態の作成: Worktree とブランチを作成し、状態ファイルを MERGING で保存
        target_dir = gate.worktree_manager.create_worktree(team_id=wt_id, base_branch="main", branch_name=branch_name)
        state_dir = self.repo_dir / ".dev" / "integration_jobs"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / f"{job_id}.json"
        state_file.write_text(
            json.dumps({
                "job_id": job_id,
                "state": "MERGING",
                "integration_branch": branch_name,
                "worktree_path": str(target_dir),
                "base_commit": self.base_commit,
                "updated_at": time.time(),
            }),
            encoding="utf-8",
        )

        # 復旧処理を実行
        recovered, status, details = gate.recover_interrupted_job(job_id)
        self.assertTrue(recovered)
        self.assertEqual(status, "RECOVERED")

        # 孤立 Worktree が解除されていること
        self.assertFalse(target_dir.exists())

        # 元の統合ブランチは残っておらず、quarantine/...-crashed-... へ退避されていること
        chk = self._run_git(["rev-parse", "--verify", f"refs/heads/{branch_name}"], check=False)
        self.assertNotEqual(chk.returncode, 0)
        quarantine_branch = details.get("quarantine_branch", "")
        self.assertTrue(quarantine_branch.startswith(f"quarantine/integration-{job_id}-crashed-"))

        # 状態ファイルが更新されていること
        updated_data = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(updated_data.get("state"), "RECOVERED")

    def test_execute_integration_recovers_and_resumes_after_simulated_sigkill_crash(self) -> None:
        """CC.4 / P1-D: マージ途中の強制終了（クラッシュ模擬）後、resume=True で安全に復旧・統合完遂できることの実証。"""
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic\ndef get_core():\n    return 42\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )
        gate = IntegrationGate(repo_dir=self.repo_dir)
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]

        job_id = "job-sigkill-resume"
        branch_name = f"integration/{job_id}"
        wt_id = "int-job-sigkill-resume"

        # 模擬クラッシュの作成: 孤立 Worktree と未完了ブランチを生成し、状態を MERGING で保存（SIGKILL 発生をシミュレート）
        target_dir = gate.worktree_manager.create_worktree(team_id=wt_id, base_branch="main", branch_name=branch_name)
        state_dir = self.repo_dir / ".dev" / "integration_jobs"
        state_dir.mkdir(parents=True, exist_ok=True)
        state_file = state_dir / f"{job_id}.json"
        state_file.write_text(
            json.dumps({
                "job_id": job_id,
                "state": "MERGING",
                "integration_branch": branch_name,
                "worktree_path": str(target_dir),
                "base_commit": self.base_commit,
                "updated_at": time.time(),
            }),
            encoding="utf-8",
        )

        # 復旧再開実行 (resume=True)
        result = gate.execute_integration(
            job_id=job_id,
            inputs=inputs,
            base_commit="main",
            verify_script=self.repo_dir / "scripts" / "verify_local.sh",
            resume=True,
        )

        # クラッシュが安全に復旧され、統合が最後まで正常に完遂すること
        self.assertTrue(result.success)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.integration_branch, branch_name)
        self.assertIsNotNone(result.release_candidate_commit)

        # 過去のクラッシュしたブランチが quarantine/...-crashed-... に退避されていること
        branch_list = self._run_git(["branch", "--list", "quarantine/*"]).stdout
        self.assertIn(f"quarantine/integration-{job_id}-crashed-", branch_list)

        # 最終状態ファイルが COMPLETED に更新されていること
        final_state = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(final_state.get("state"), "COMPLETED")

    def test_recover_interrupted_job_fails_closed_when_worktree_removal_fails(self) -> None:
        """DD.1 / P0: Worktree 削除に失敗した際、Fail-Closed で RECOVERY_REQUIRED を返し RECOVERED に偽陽性しないことの実証。"""
        gate = IntegrationGate(repo_dir=self.repo_dir)
        job_id = "job-wt-fail-closed"
        state = IntegrationJobState(
            job_id=job_id,
            state="MERGING",
            base_commit=self.base_commit,
            integration_branch=f"integration/{job_id}",
        )
        state.save(gate.jobs_dir)

        # 削除処理で障害が発生（Sol氏の独立検証シナリオ）
        with unittest.mock.patch.object(
            gate.worktree_manager,
            "remove_worktree",
            side_effect=RuntimeError("injected cleanup failure"),
        ):
            success, status, details = gate.recover_interrupted_job(job_id)

        self.assertFalse(success)
        self.assertEqual(status, "RECOVERY_REQUIRED")
        self.assertTrue(details.get("worktree_cleanup_failed"))

        persisted = IntegrationJobState.load(gate.jobs_dir, job_id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.state, "RECOVERY_REQUIRED")

    def test_recover_interrupted_job_fails_closed_when_worktree_still_exists(self) -> None:
        """DD.1 / P0: 例外が出なくても Worktree 実態が残存している場合は RECOVERY_REQUIRED となることの実証。"""
        gate = IntegrationGate(repo_dir=self.repo_dir)
        job_id = "job-wt-still-exists"
        branch_name = f"integration/{job_id}"
        wt_team_id = f"int-{job_id}"
        gate.worktree_manager.create_worktree(team_id=wt_team_id, base_branch="main", branch_name=branch_name)

        state = IntegrationJobState(
            job_id=job_id,
            state="MERGING",
            base_commit=self.base_commit,
            integration_branch=branch_name,
        )
        state.save(gate.jobs_dir)

        # remove_worktree が何もしなかった（削除されずにディレクトリが残存）ケースを模擬
        with unittest.mock.patch.object(gate.worktree_manager, "remove_worktree", return_value=False):
            success, status, details = gate.recover_interrupted_job(job_id)

        self.assertFalse(success)
        self.assertEqual(status, "RECOVERY_REQUIRED")
        self.assertTrue(details.get("worktree_cleanup_failed"))

        # クリーンアップ
        gate.worktree_manager.remove_worktree(wt_team_id, force=True, delete_branch=True, purge_unmerged=True)

    def test_integration_job_id_validation_rejects_invalid_chars(self) -> None:
        """DD.2 / P1: job_id のパストラバーサルや不正文字を厳格に拒否することの実証。"""
        # 不正な job_id によるステート作成拒否
        for invalid_id in ["../job", "job/1", "job;rm", "job test", "job$1"]:
            with self.assertRaises(ValueError):
                IntegrationJobState(
                    job_id=invalid_id,
                    state="INITIALIZED",
                    base_commit="main",
                    integration_branch="integration/test",
                )

        gate = IntegrationGate(repo_dir=self.repo_dir)
        # 不正な job_id による recover 拒否
        success, status, details = gate.recover_interrupted_job("../../etc/passwd")
        self.assertFalse(success)
        self.assertEqual(status, "INVALID_JOB_ID")
        self.assertIn("Invalid job_id", details.get("error", ""))

        # 不正な job_id による execute 拒否
        result = gate.execute_integration(
            job_id="../../bad_job",
            inputs=[],
            base_commit="main",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.status, "ERROR")
        self.assertIn("Invalid job_id", result.errors[0])

    def test_real_process_sigkill_during_merge_recovers_and_completes(self) -> None:
        """DD.3 / P1: マージ途中の実プロセス SIGKILL 障害注入から resume=True で安全に復旧・完遂することの実証。"""
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic\ndef get_core():\n    return 42\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )
        job_id = "job-real-sigkill-merge"
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]
        verify_script = self.repo_dir / "scripts" / "verify_local.sh"

        # 別プロセスで IntegrationGate を起動し、第1チームマージ直後に SIGKILL で即死させる
        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=_child_gate_sigkill_worker,
            args=(self.repo_dir, job_id, inputs, "main", verify_script, "after_first_merge"),
        )
        proc.start()
        proc.join(timeout=15)

        # プロセスが実際に SIGKILL (シグナル 9) で異常終了したことを確認
        self.assertFalse(proc.is_alive())
        self.assertEqual(proc.exitcode, -signal.SIGKILL)

        # 親プロセス側から状態ファイルを確認 (MERGING 途中で停止していること)
        state_file = self.repo_dir / ".dev" / "integration_jobs" / f"{job_id}.json"
        self.assertTrue(state_file.exists())
        state_data = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(state_data.get("state"), "MERGING")

        # 親プロセス側で resume=True を実行し、安全な復旧と統合完遂を実証
        gate = IntegrationGate(repo_dir=self.repo_dir)
        result = gate.execute_integration(
            job_id=job_id,
            inputs=inputs,
            base_commit="main",
            verify_script=verify_script,
            resume=True,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, "SUCCESS")
        self.assertIsNotNone(result.release_candidate_commit)

        # 過去のクラッシュブランチが隔離されていること
        branch_list = self._run_git(["branch", "--list", "quarantine/*"]).stdout
        self.assertIn(f"quarantine/integration-{job_id}-crashed-", branch_list)

        # 最終状態が COMPLETED であること
        final_state = json.loads(state_file.read_text(encoding="utf-8"))
        self.assertEqual(final_state.get("state"), "COMPLETED")

    def test_real_process_sigkill_after_rc_tag_recovers_as_completed(self) -> None:
        """DD.3 / P1: RC タグ発行後・状態更新前の実プロセス SIGKILL 障害注入から安全に COMPLETED 復旧できることの実証。"""
        core_bundle, core_commit = self._create_team_work_and_bundle(
            team_id="core",
            branch="feature/team-core",
            file_path="src/core/app.py",
            content="# Core logic\ndef get_core():\n    return 42\n",
        )
        ui_bundle, ui_commit = self._create_team_work_and_bundle(
            team_id="ui",
            branch="feature/team-ui",
            file_path="src/ui/view.py",
            content="from core.app import get_core\n\ndef render():\n    return f'Render: {get_core()}'\n",
        )
        job_id = "job-real-sigkill-rctag"
        inputs = [
            TeamIntegrationInput(team_id="core", bundle_path=core_bundle, branch="feature/team-core", head_commit=core_commit),
            TeamIntegrationInput(team_id="ui", bundle_path=ui_bundle, branch="feature/team-ui", head_commit=ui_commit),
        ]
        verify_script = self.repo_dir / "scripts" / "verify_local.sh"

        ctx = multiprocessing.get_context("spawn")
        proc = ctx.Process(
            target=_child_gate_sigkill_worker,
            args=(self.repo_dir, job_id, inputs, "main", verify_script, "after_rc_tag"),
        )
        proc.start()
        proc.join(timeout=15)

        self.assertFalse(proc.is_alive())
        self.assertEqual(proc.exitcode, -signal.SIGKILL)

        # Git に RC タグが存在することを確認
        rc_tag = f"rc/{job_id}"
        tag_chk = self._run_git(["rev-parse", "--verify", f"refs/tags/{rc_tag}"])
        self.assertEqual(tag_chk.returncode, 0)
        expected_rc_commit = tag_chk.stdout.strip()

        # 親プロセス側で resume=True を実行
        gate = IntegrationGate(repo_dir=self.repo_dir)
        result = gate.execute_integration(
            job_id=job_id,
            inputs=inputs,
            base_commit="main",
            verify_script=verify_script,
            resume=True,
        )

        self.assertTrue(result.success)
        self.assertEqual(result.status, "SUCCESS")
        self.assertEqual(result.release_candidate_commit, expected_rc_commit)

    def test_recover_interrupted_job_fails_closed_when_rc_exists_but_worktree_removal_fails(self) -> None:
        """EE.1 / P0: RC タグが存在する状態でも Worktree 削除に失敗した場合は Fail-Closed で RECOVERY_REQUIRED となることの実証。"""
        gate = IntegrationGate(repo_dir=self.repo_dir)
        job_id = "job-rc-wt-fail-closed"
        rc_tag = f"rc/{job_id}"
        # RC タグを作成
        self._run_git(["tag", rc_tag, self.base_commit], check=True)

        state = IntegrationJobState(
            job_id=job_id,
            state="MERGING",
            base_commit=self.base_commit,
            integration_branch=f"integration/{job_id}",
            rc_commit=self.base_commit,
            rc_tag=rc_tag,
        )
        state.save(gate.jobs_dir)

        # Worktree 削除処理に例外を注入 (Sol氏の再現シナリオ)
        with unittest.mock.patch.object(
            gate.worktree_manager,
            "remove_worktree",
            side_effect=RuntimeError("injected worktree failure"),
        ):
            success, status, details = gate.recover_interrupted_job(job_id)

        self.assertFalse(success)
        self.assertEqual(status, "RECOVERY_REQUIRED")
        self.assertTrue(details.get("worktree_cleanup_failed"))

        persisted = IntegrationJobState.load(gate.jobs_dir, job_id)
        self.assertIsNotNone(persisted)
        self.assertEqual(persisted.state, "RECOVERY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
