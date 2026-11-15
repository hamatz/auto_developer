from __future__ import annotations

import io
import unittest
from unittest.mock import MagicMock, patch

from auto_developer.cli import main
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import MockRunnerAdapter


class TestCliAuditOptions(unittest.TestCase):
    """機能SSS.1: CLI からの独立監査有効化・設定オプションと起動前設定バリデーション (Fail-Closed)。"""

    def test_cli_audit_help_contains_options(self) -> None:
        """--help または run --help で独立監査オプションが提示されること。"""
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), self.assertRaises(SystemExit) as cm:
            main(["--help"])
        self.assertEqual(cm.exception.code, 0)
        help_text = stdout_buf.getvalue()
        self.assertIn("--enable-audit", help_text)
        self.assertIn("--auditor-cmd", help_text)
        self.assertIn("--auditor-model", help_text)
        self.assertIn("--audit-baseline", help_text)

    def test_cli_audit_enabled_without_auditor_cmd_fails_closed(self) -> None:
        """実動モードで --enable-audit が指定されているにもかかわらず --auditor-cmd が空の場合はジョブ開始前に Exit 2 で拒絶されること。"""
        stderr_buf = io.StringIO()
        with patch("sys.stderr", stderr_buf):
            ret = main([
                "run",
                "--enable-audit",
                "--maker-cmd", "echo maker",
                "--checker-cmd", "echo checker",
            ])
        self.assertEqual(ret, 2)
        err = stderr_buf.getvalue()
        self.assertIn("CONFIG ERROR", err)
        self.assertIn("--auditor-cmd", err)

    def test_cli_audit_enabled_passes_options_to_orchestrator(self) -> None:
        """--enable-audit および監査オプションが NightlyOrchestrator に安全に渡されること。"""
        with patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls:
            mock_orch_instance = MagicMock()
            mock_orch_instance.run.return_value = 0
            mock_orch_cls.return_value = mock_orch_instance

            ret = main([
                "run",
                "--mock",
                "--enable-audit",
                "--auditor-cmd", "claude --prompt {prompt}",
                "--auditor-model", "claude-opus-5.5",
                "--auditor-fallback-models", "gpt-4o,claude-3-7-sonnet",
                "--audit-baseline", "commit-abc1234",
            ])
            self.assertEqual(ret, 0)
            mock_orch_cls.assert_called_once()
            _, kwargs = mock_orch_cls.call_args
            self.assertTrue(kwargs.get("enable_audit_stage"))
            self.assertEqual(kwargs.get("auditor_cmd_template"), "claude --prompt {prompt}")
            self.assertEqual(kwargs.get("auditor_model"), "claude-opus-5.5")
            self.assertEqual(kwargs.get("fallback_models"), ["gpt-4o", "claude-3-7-sonnet"])
            self.assertEqual(kwargs.get("audit_baseline_commit"), "commit-abc1234")

    def test_orchestrator_pre_flight_auditor_same_family_fails_closed(self) -> None:
        """Maker と Auditor が同系統ファミリー（例: Gemini Maker vs Gemini Auditor）の場合はジョブ開始前に拒絶されること。"""
        stderr_buf = io.StringIO()
        orch = NightlyOrchestrator(
            runner_adapter=MockRunnerAdapter(),
            enable_audit_stage=True,
            maker_model="gemini-3.8-flash",
            auditor_model="gemini-4.0-argon",
            auditor_cmd_template="dummy-cmd",
        )
        with patch("sys.stderr", stderr_buf):
            ret = orch.run()
        self.assertEqual(ret, 2)
        err = stderr_buf.getvalue()
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", err)
        self.assertIn("gemini", err.lower())


if __name__ == "__main__":
    unittest.main()
