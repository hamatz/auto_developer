from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.audit import DEFAULT_AUDIT_PATHSPECS, AuditConfig, IndependentAuditRunner
from auto_developer.cli import main as cli_main
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter


class TestAudit6Scope(unittest.TestCase):
    """AUDIT-6: 監査の入力の範囲（--audit-paths と既定の除外、空差分の扱い）の受入テスト。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        (self.repo_dir / "SPEC.md").write_text("# Spec\nInitial spec\n", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# Plan\n- [x] 1. Done\n", encoding="utf-8")
        (self.repo_dir / "src").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "src" / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.repo_dir, check=True, capture_output=True)
        self.baseline_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _commit_all(self, msg: str) -> str:
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", msg], cwd=self.repo_dir, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_default_audit_excludes_tests_only_diff_and_skips_auditor(self) -> None:
        """tests/ のみを変更した差分は既定で除外され、範囲が空になるため監査役を呼ばずに SUCCESS となること。"""
        (self.repo_dir / "tests").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "tests" / "test_app.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
        (self.repo_dir / "docs").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "docs" / "guide.txt").write_text("guide\n", encoding="utf-8")
        (self.repo_dir / "examples").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "examples" / "ex.py").write_text("print(1)\n", encoding="utf-8")
        (self.repo_dir / "README.md").write_text("# Readme\n", encoding="utf-8")
        target_sha = self._commit_all("add excluded files only")

        marker_file = self.repo_dir / "auditor_was_called.txt"
        cmd_template = f"touch {marker_file} && echo '[]'"
        config = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="claude-opus-5.5",
            baseline_commit=self.baseline_sha,
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd_template,
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.baseline_sha,
        )

        findings = runner.audit_repository(
            repo_dir=self.repo_dir,
            target_commit=target_sha,
            baseline_commit=self.baseline_sha,
        )
        self.assertEqual(findings, [])
        self.assertFalse(marker_file.exists(), "Auditor should NOT be called when filtered diff is empty")
        meta = runner.last_run_metadata or {}
        self.assertTrue(meta.get("empty_diff"))
        self.assertEqual(meta.get("diff_files_count"), 0)
        self.assertEqual(meta.get("diff_bytes"), 0)
        self.assertEqual(meta.get("audit_paths"), list(DEFAULT_AUDIT_PATHSPECS))

    def test_default_audit_includes_src_and_computes_filtered_diff_sha256(self) -> None:
        """src/ と tests/ の両方を変えた場合、tests/ は除かれ diff_sha256 は絞り込み後の差分の SHA-256 になること。"""
        (self.repo_dir / "src" / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8")
        (self.repo_dir / "tests").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "tests" / "test_app.py").write_text("def test_add():\n    assert True\n", encoding="utf-8")
        (self.repo_dir / "README.md").write_text("# Updated\n", encoding="utf-8")
        target_sha = self._commit_all("change src and tests and md")

        captured_prompt = self.repo_dir / "captured_prompt.txt"
        cmd_template = f"cp {{prompt_file}} {captured_prompt} && echo '[]'"
        config = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="claude-opus-5.5",
            baseline_commit=self.baseline_sha,
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd_template,
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.baseline_sha,
        )

        findings = runner.audit_repository(
            repo_dir=self.repo_dir,
            target_commit=target_sha,
            baseline_commit=self.baseline_sha,
        )
        self.assertEqual(findings, [])
        prompt_text = captured_prompt.read_text(encoding="utf-8")
        self.assertIn("src/app.py", prompt_text)
        self.assertNotIn("test_app.py", prompt_text)
        self.assertNotIn("README.md", prompt_text)

        expected_diff = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", *DEFAULT_AUDIT_PATHSPECS],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        expected_sha = hashlib.sha256(expected_diff.encode("utf-8")).hexdigest()
        meta = runner.last_run_metadata or {}
        self.assertEqual(meta.get("diff_sha256"), expected_sha)
        self.assertEqual(meta.get("diff_files_count"), 1)
        self.assertEqual(meta.get("diff_bytes"), len(expected_diff.encode("utf-8")))
        self.assertFalse(meta.get("empty_diff"))

    def test_explicit_audit_paths_includes_specified_pathspecs(self) -> None:
        """--audit-paths を指定した場合は、その pathspec に当たるファイルの差分だけを渡すこと。"""
        (self.repo_dir / "src" / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a * b\n", encoding="utf-8")
        (self.repo_dir / "tests").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "tests" / "test_app.py").write_text("def test_mul():\n    assert True\n", encoding="utf-8")
        target_sha = self._commit_all("change src and tests")

        captured_prompt = self.repo_dir / "captured_prompt.txt"
        cmd_template = f"cp {{prompt_file}} {captured_prompt} && echo '[]'"
        config = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="claude-opus-5.5",
            baseline_commit=self.baseline_sha,
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd_template,
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.baseline_sha,
            audit_paths=["tests/"],
        )

        findings = runner.audit_repository(
            repo_dir=self.repo_dir,
            target_commit=target_sha,
            baseline_commit=self.baseline_sha,
        )
        self.assertEqual(findings, [])
        prompt_text = captured_prompt.read_text(encoding="utf-8")
        self.assertIn("tests/test_app.py", prompt_text)
        self.assertNotIn("src/app.py", prompt_text)
        meta = runner.last_run_metadata or {}
        self.assertEqual(meta.get("audit_paths"), ["tests/"])
        self.assertEqual(meta.get("diff_files_count"), 1)

    def test_orchestrator_records_scope_and_empty_diff_and_reporter_renders_them(self) -> None:
        """audit_stage.json と NIGHTLY_REPORT.md に範囲・ファイル数・バイト数・空差分のスキップが残ること。"""
        (self.repo_dir / "tests").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "tests" / "test_only.py").write_text("x = 1\n", encoding="utf-8")
        target_sha = self._commit_all("tests only")

        runner = IndependentAuditRunner(
            auditor_cmd_template="echo 'SHOULD_NOT_RUN' && exit 1",
            model_name="claude-opus-5.5",
            maker_model="gemini-3.8-flash",
            baseline_commit=self.baseline_sha,
        )
        orch = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            enable_audit_stage=True,
            auditor_runner=runner,
            maker_model="gemini-3.8-flash",
        )
        res = orch.run_audit_stage(baseline_commit=self.baseline_sha, target_commit=target_sha)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertTrue(res["empty_diff"])
        self.assertEqual(res["diff_files_count"], 0)
        self.assertEqual(res["diff_bytes"], 0)
        self.assertEqual(res["audit_paths"], list(DEFAULT_AUDIT_PATHSPECS))

        audit_stage_file = self.repo_dir / ".git" / "nightly_evidence" / "records" / "audit_stage.json"
        if not audit_stage_file.exists():
            audit_stage_file = self.repo_dir / ".dev" / "evidence" / "records" / "audit_stage.json"
        self.assertTrue(audit_stage_file.exists())
        saved = json.loads(audit_stage_file.read_text(encoding="utf-8"))
        self.assertEqual(saved["status"], "SUCCESS")
        self.assertTrue(saved["empty_diff"])
        self.assertEqual(saved["diff_files_count"], 0)
        self.assertEqual(saved["diff_bytes"], 0)
        self.assertEqual(saved["audit_paths"], list(DEFAULT_AUDIT_PATHSPECS))

        reporter = MorningReporter(repo_dir=self.repo_dir)
        report_path = self.repo_dir / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": target_sha,
                "completed_tasks": [],
                "audit_stage": res,
            },
        )
        report_text = report_path.read_text(encoding="utf-8")
        self.assertIn("監査対象範囲 (Audit Paths)", report_text)
        self.assertIn("0 ファイル, 0 バイト", report_text)
        self.assertIn("対象範囲の差分が空のため、監査役を呼ばずに `SUCCESS`", report_text)
        self.assertNotIn("Independent Multi-LLM Audit Provenance", report_text)

    def test_cli_parses_audit_paths_and_passes_to_orchestrator(self) -> None:
        """CLI の --audit-paths がカンマ区切りで解釈され NightlyOrchestrator に渡ること。"""
        with patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls:
            mock_orch_cls.return_value.run.return_value = 0
            rc = cli_main([
                "--work-dir",
                str(self.repo_dir),
                "--mock",
                "--enable-audit",
                "--audit-paths",
                "src/, scripts/",
                "run",
                "--dry-run",
            ])
        self.assertEqual(rc, 0)
        mock_orch_cls.assert_called_once()
        _, kwargs = mock_orch_cls.call_args
        self.assertEqual(kwargs.get("audit_paths"), ["src/", "scripts/"])
