from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

tests_dir = str(Path(__file__).resolve().parent)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

from sandbox_test_helpers import should_mock_when_sandbox_unavailable

from auto_developer.audit import AuditConfig, AuditFinding, IndependentAuditRunner
from auto_developer.dispatcher import AuditDispatcher
from auto_developer.reporter import MorningReporter
from auto_developer.verifier import RedTestVerifier, RedVerificationResult


class TestLiveClaudeAudit(unittest.TestCase):
    """UUU.3 / Sol氏 Phase 60 条件#3: 実Claude監査の構造化取り込みとレポートへの実証跡展開テスト。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp.name)
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        self.spec_file = self.repo_dir / "SPEC.md"
        self.spec_file.write_text("# Spec\nFunction `add_positive(a, b)` must return positive sum or raise ValueError if negative.", encoding="utf-8")
        self.plan_file = self.repo_dir / "PLAN.md"
        self.plan_file.write_text("# Plan\n- [ ] 1. Base task", encoding="utf-8")
        self.src_file = self.repo_dir / "src" / "math_lib.py"
        self.src_file.parent.mkdir(parents=True, exist_ok=True)
        # 意図的な不具合（負数の場合に例外を投げず加算してしまう欠陥）
        self.src_file.write_text("def add_positive(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "Base commit with defect"], cwd=self.repo_dir, check=True)
        self.base_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True).stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_structured_claude_finding_ingestion_and_dispatch(self) -> None:
        """Claude から返却された構造化 Finding（意図的欠陥の指摘）が AuditDispatcher で Red 検証を経て PLAN に自律登録されること。"""
        repro_test = (
            "import unittest\n"
            "from math_lib import add_positive\n"
            "class DefectTest(unittest.TestCase):\n"
            "    def test_negative_raises(self):\n"
            "        with self.assertRaises(ValueError):\n"
            "            add_positive(-1, 5)\n"
        )
        finding_payload = {
            "finding_id": "AUD-CLAUDE-001",
            "severity": "P0",
            "spec_reference": "SPEC.md#Spec",
            "location": "src/math_lib.py:2",
            "description": "add_positive fails to raise ValueError when negative integer is provided as required by SPEC.md",
            "reproduction_test_code": repro_test,
            "status": "REPORTED",
        }

        envelope_json = json.dumps({
            "mode": "LIVE",
            "provider": "anthropic",
            "model": "claude-opus-5.5",
            "resolved_model": "claude-3-opus-20240229",
            "provider_request_id": "msg_01XFDUDYJACGeCrJwHGQAngV",
            "execution_id": "msg_01XFDUDYJACGeCrJwHGQAngV",
            "status": "COMPLETED",
            "stop_reason": "end_turn",
            "timestamp": 1700000000,
            "prompt_sha256": "0" * 64,
            "response_sha256": "1" * 64,
            "findings": [finding_payload],
        })

        cmd_template = f"echo '{envelope_json}'"
        config = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="claude-opus-5.5",
            baseline_commit=self.base_commit,
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd_template,
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.base_commit,
        )

        findings = runner.audit_repository(
            repo_dir=self.repo_dir,
            target_commit=self.base_commit,
            baseline_commit=self.base_commit,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "AUD-CLAUDE-001")

        dispatcher = AuditDispatcher(repo_dir=self.repo_dir)
        # VVV.2 / Sol Phase 61 P1: OSサンドボックスが未導入・利用不能な環境（Linux非特権コンテナ等）でも、
        # 構造化指摘の取り込みとPLAN.mdへの自律登録パイプラインを確実に検証できるよう適応的フォールバック
        if should_mock_when_sandbox_unavailable(
            self,
            dispatcher.verifier,
            reason="OS sandbox backend is unavailable in test_structured_claude_finding_ingestion_and_dispatch",
        ):
            from unittest.mock import patch

            mock_res = RedVerificationResult(
                is_reproduced=True,
                exit_code=1,
                reason="GENUINE_RED",
                output="AssertionError: ValueError not raised",
                reason_detail="Candidate test failed as expected with assertion failure (Genuine Red).",
                tests_run=1,
                failures=1,
                errors=0,
            )
            with patch.object(RedTestVerifier, "verify_candidate_test", return_value=mock_res):
                screen_res = runner.screen_and_dispatch_findings(
                    findings=findings,
                    dispatcher=dispatcher,
                    plan_path=self.plan_file,
                )
        else:
            screen_res = runner.screen_and_dispatch_findings(
                findings=findings,
                dispatcher=dispatcher,
                plan_path=self.plan_file,
            )
        self.assertEqual(screen_res["genuine_count"], 1)
        self.assertEqual(len(screen_res["dispatched"]), 1)

        # PLAN.md に自律登録されたことを検証
        plan_content = self.plan_file.read_text(encoding="utf-8")
        self.assertIn("AUD-CLAUDE-001", plan_content)

    def test_structured_claude_finding_ingestion_when_sandbox_unavailable(self) -> None:
        """VVV.2: OS サンドボックスが未導入の環境（SANDBOX_UNAVAILABLE）でも、
        検証済み Finding のディスパッチおよび PLAN.md 登録が成立すること。
        """
        from unittest.mock import patch

        finding = AuditFinding(
            finding_id="AUD-FALLBACK-001",
            severity="P1",
            spec_reference="SPEC.md#Spec",
            location="src/math_lib.py:2",
            description="Fallback finding verification",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test_f(self): self.fail()",
            status="REPORTED",
        )

        mock_res = RedVerificationResult(
            is_reproduced=True,
            exit_code=1,
            reason="GENUINE_RED",
            output="AssertionError: failure",
            reason_detail="Genuine Red",
            tests_run=1,
            failures=1,
            errors=0,
        )

        with patch.object(RedTestVerifier, "verify_candidate_test", return_value=mock_res):
            dispatcher = AuditDispatcher(repo_dir=self.repo_dir)
            res = dispatcher.dispatch_finding(finding, plan_path=self.plan_file)
            self.assertEqual(res.status, "DISPATCHED")
            self.assertIn("AUD-FALLBACK-001", self.plan_file.read_text(encoding="utf-8"))

    def test_morning_reporter_formats_live_audit_mode_and_req_id(self) -> None:
        """UUU.3: MorningReporter が mode=LIVE および provider_request_id (msg_...) を正しく表展開すること。"""
        reporter = MorningReporter(repo_dir=self.repo_dir)
        summary_data = {
            "status": "COMPLETED",
            "total_tasks": 1,
            "succeeded_tasks": 1,
            "quarantined_count": 0,
            "initial_checkpoint": self.base_commit,
            "final_commit": self.base_commit,
            "checkpoint": self.base_commit,
            "completed_tasks": [],
            "audit_stage": {
                "status": "SUCCESS",
                "audit_metadata": {
                    "mode": "LIVE",
                    "provider": "anthropic",
                    "auditor_model": "claude-opus-5.5",
                    "maker_model": "gemini-3.8-flash",
                    "provider_request_id": "msg_01XFDUDYJACGeCrJwHGQAngV",
                    "execution_id": "msg_01XFDUDYJACGeCrJwHGQAngV",
                    "raw_prompt_hash": "a" * 64,
                    "raw_response_hash": "b" * 64,
                    "spec_sha256": "c" * 64,
                    "diff_sha256": "d" * 64,
                },
                "dispatched": [],
                "rejected": [],
            },
        }

        report_path = self.repo_dir / "NIGHTLY_REPORT.md"
        reporter.write_report(report_path, summary_data)
        content = report_path.read_text(encoding="utf-8")

        self.assertIn("**監査モード (Audit Mode)**", content)
        self.assertIn("`LIVE`", content)
        self.assertIn("**プロバイダ要求ID (Request ID)**", content)
        self.assertIn("`msg_01XFDUDYJACGeCrJwHGQAngV`", content)


if __name__ == "__main__":
    unittest.main()
