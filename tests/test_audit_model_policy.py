from __future__ import annotations

import sys
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import (
    AuditConfig,
    detect_model_family,
    verify_model_independence,
)
from auto_developer.orchestrator import NightlyOrchestrator


class TestAuditModelPolicy(unittest.TestCase):
    def test_detect_model_family(self) -> None:
        """機能EEE.1: モデル文字列からモデルファミリを正確に判定できること。"""
        self.assertEqual(detect_model_family("gemini-3.8-flash"), "gemini")
        self.assertEqual(detect_model_family("gemini-2.5-pro"), "gemini")
        self.assertEqual(detect_model_family("claude-opus-5.5"), "claude")
        self.assertEqual(detect_model_family("claude-3-7-sonnet"), "claude")
        self.assertEqual(detect_model_family("gpt-4o"), "openai")
        self.assertEqual(detect_model_family("o3-mini"), "openai")
        self.assertEqual(detect_model_family("mock-maker"), "mock")
        self.assertEqual(detect_model_family("mock-checker"), "mock")

    def test_verify_model_independence_distinct_families(self) -> None:
        """機能EEE.1: 異系統LLM（Gemini Maker vs Claude Opus Auditor）は独立監査ポリシーを満たすこと。"""
        ok, reason = verify_model_independence("gemini-3.8-flash", "claude-opus-5.5")
        self.assertTrue(ok)
        self.assertIn("gemini", reason.lower())
        self.assertIn("claude", reason.lower())

        ok, reason = verify_model_independence("gemini-3.8-flash", "gpt-4o")
        self.assertTrue(ok)

    def test_verify_model_independence_rejects_identical_model(self) -> None:
        """機能EEE.1: 同一モデルの指定は自己承認（癒着）として拒絶されること。"""
        ok, reason = verify_model_independence("gemini-3.8-flash", "gemini-3.8-flash")
        self.assertFalse(ok)
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", reason)
        self.assertIn("identical", reason.lower())

    def test_verify_model_independence_rejects_same_family(self) -> None:
        """機能EEE.1: 同一ファミリ内の別モデル（Gemini Flash vs Gemini Pro）もバイアス癒着として拒絶されること。"""
        ok, reason = verify_model_independence("gemini-3.8-flash", "gemini-2.5-pro")
        self.assertFalse(ok)
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", reason)
        self.assertIn("same family 'gemini'", reason)

        ok, reason = verify_model_independence("claude-3-5-sonnet", "claude-opus-5.5")
        self.assertFalse(ok)
        self.assertIn("same family 'claude'", reason)

    def test_audit_config_validation(self) -> None:
        """機能EEE.1: AuditConfig のバリデーションが異系統ポリシーを強制すること。"""
        valid_cfg = AuditConfig(maker_model="gemini-3.8-flash", auditor_model="claude-opus-5.5")
        self.assertTrue(valid_cfg.validate())

        invalid_cfg = AuditConfig(maker_model="gemini-3.8-flash", auditor_model="gemini-3.8-flash")
        with self.assertRaises(ValueError) as ctx:
            invalid_cfg.validate()
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", str(ctx.exception))

        # 明示的な override フラグがある場合のみ許可
        override_cfg = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="gemini-3.8-flash",
            allow_same_model_override=True,
        )
        self.assertTrue(override_cfg.validate())

    def test_orchestrator_rejects_same_model_configuration(self) -> None:
        """機能EEE.1: NightlyOrchestrator が同一モデル構成を検知した場合に Exit 2 で起動拒否すること。"""
        import tempfile

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            plan = repo / "PLAN.md"
            plan.write_text("- [ ] 1. task\n")
            spec = repo / "SPEC.md"
            spec.write_text("# SPEC\n")
            verify = repo / "verify.sh"
            verify.write_text("#!/bin/bash\nexit 0\n")
            verify.chmod(0o755)

            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=plan,
                spec_path=spec,
                verify_script=verify,
                maker_model="gemini-3.8-flash",
                checker_model="gemini-2.5-pro",
            )
            code = orch.run()
            self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
