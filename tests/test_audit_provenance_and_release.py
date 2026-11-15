from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.audit import AuditConfig, IndependentAuditRunner
from auto_developer.bundler import EvidenceBundler
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter


class TestAuditProvenanceAndRelease(unittest.TestCase):
    """SSS.4 / Sol氏 Phase 58 指摘: 実外部モデル監査実績の証跡記録と v0.7.0 リリース判定のテスト。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp.name)
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test Runner"],
            cwd=self.repo_dir,
            check=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_dir,
            check=True,
        )

        (self.repo_dir / "SPEC.md").write_text("# Spec\nInitial spec", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# Plan\n- [ ] Task 1", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Initial commit"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        base_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        self.baseline_sha = base_res.stdout.strip()

        (self.repo_dir / "SPEC.md").write_text("# Spec\nUpdated spec for audit", encoding="utf-8")
        (self.repo_dir / "src").mkdir(exist_ok=True)
        (self.repo_dir / "src" / "app.py").write_text("print('hello')", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(
            ["git", "commit", "-m", "Feature commit"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        target_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        self.target_sha = target_res.stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_audit_runner_records_provenance_metadata(self) -> None:
        """SSS.4: 独立監査実行後にプロンプトハッシュ、レスポンスハッシュ、監査役モデル等の証跡メタデータが保持されること。"""
        cmd_template = (
            'echo \'[{"finding_id": "PROV-001", "severity": "P1", "spec_reference": "SPEC.md", '
            '        "location": "src/app.py:1", "description": "Audit provenance test", '
            '        "reproduction_test_code": "import unittest\\nclass T(unittest.TestCase):\\n    def test_t(self): self.fail()", '
            '        "status": "REPORTED"}]\''
        )
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
            target_commit=self.target_sha,
            baseline_commit=self.baseline_sha,
        )
        self.assertEqual(len(findings), 1)

        # last_run_metadata が保持されていること
        meta = getattr(runner, "last_run_metadata", None)
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta.get("auditor_model"), "claude-opus-5.5")
        self.assertFalse(meta.get("fallback_occurred"))
        self.assertIsNotNone(meta.get("raw_prompt_hash"))
        self.assertIsNotNone(meta.get("raw_response_hash"))
        self.assertIsNotNone(meta.get("spec_sha256"))
        self.assertIsNotNone(meta.get("diff_sha256"))
        self.assertEqual(meta.get("target_commit"), self.target_sha)
        self.assertEqual(meta.get("baseline_commit"), self.baseline_sha)
        # TTT.1: プロバイダ名および実行識別子の拘束
        self.assertEqual(meta.get("provider"), "anthropic")
        self.assertIsNotNone(meta.get("execution_id"))

    def test_audit_runner_extracts_envelope_provider_metadata(self) -> None:
        """TTT.1: プロバイダからのエンベロープレスポンス（実行ID・プロバイダ名入り）を正しく解析・抽出できること。"""
        envelope_json = json.dumps({
            "provider": "anthropic",
            "model": "claude-opus-5.5",
            "execution_id": "msg_01X9Z8A7B6C5D4E3",
            "findings": [
                {
                    "finding_id": "PROV-ENV-001",
                    "severity": "P1",
                    "spec_reference": "SPEC.md",
                    "location": "src/app.py:1",
                    "description": "Envelope finding",
                    "reproduction_test_code": "import unittest\nclass T(unittest.TestCase):\n    def test_t(self): self.fail()",
                    "status": "REPORTED",
                }
            ],
        })
        cmd_template = f"echo '{envelope_json}'"
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
            target_commit=self.target_sha,
            baseline_commit=self.baseline_sha,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "PROV-ENV-001")

        meta = runner.last_run_metadata
        self.assertIsNotNone(meta)
        assert meta is not None
        self.assertEqual(meta.get("provider"), "anthropic")
        self.assertEqual(meta.get("execution_id"), "msg_01X9Z8A7B6C5D4E3")

    def test_orchestrator_audit_stage_includes_provenance_metadata(self) -> None:
        """SSS.4: Orchestrator の run_audit_stage が監査証跡メタデータを含んで結果を返すこと。"""
        cmd_template = "echo '[]'"  # 指摘なし（合格）
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

        orch = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            enable_audit_stage=True,
            auditor_runner=runner,
            maker_model="gemini-3.8-flash",
        )

        res = orch.run_audit_stage(
            baseline_commit=self.baseline_sha,
            target_commit=self.target_sha,
        )
        self.assertEqual(res["status"], "SUCCESS")
        self.assertIn("audit_metadata", res)
        meta = res["audit_metadata"]
        self.assertEqual(meta.get("auditor_model"), "claude-opus-5.5")
        self.assertIsNotNone(meta.get("raw_prompt_hash"))
        self.assertIsNotNone(meta.get("raw_response_hash"))
        self.assertEqual(res["genuine_count"], 0)

    def test_morning_reporter_renders_audit_provenance_table(self) -> None:
        """SSS.4: MorningReporter が実外部モデルの監査証跡を表形式でレポートに出力すること。"""
        reporter = MorningReporter(repo_dir=self.repo_dir)
        report_path = self.repo_dir / "NIGHTLY_REPORT.md"

        audit_stage_data = {
            "status": "SUCCESS",
            "dispatched": [],
            "rejected": [],
            "total_processed": 0,
            "genuine_count": 0,
            "rejected_count": 0,
            "baseline_commit": self.baseline_sha,
            "target_commit": self.target_sha,
            "audit_metadata": {
                "auditor_model": "claude-opus-5.5",
                "fallback_occurred": False,
                "maker_model": "gemini-3.8-flash",
                "independence_verified": True,
                "raw_prompt_hash": "a" * 64,
                "raw_response_hash": "b" * 64,
                "spec_sha256": "c" * 64,
                "diff_sha256": "d" * 64,
            },
        }

        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "completed_tasks": [
                    {
                        "task_id": "1",
                        "title": "Task 1",
                        "commit": self.target_sha,
                        "checker_decision": "APPROVE",
                    }
                ],
                "audit_stage": audit_stage_data,
            },
        )

        content = report_path.read_text(encoding="utf-8")
        self.assertIn("独立監査", content)
        self.assertIn("claude-opus-5.5", content)
        self.assertIn("gemini-3.8-flash", content)
        self.assertIn("a" * 12, content)  # ハッシュのプレフィックスまたは完全値が含まれること
        self.assertIn("b" * 12, content)

    def test_bundler_includes_audit_stage_evidence(self) -> None:
        """SSS.4: EvidenceBundler が独立監査証跡ファイル（audit_stage.json 等）を bundle に収録すること。"""
        evidence_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        audit_file = evidence_dir / "audit_stage.json"
        audit_file.write_text(
            json.dumps({
                "auditor_model": "claude-opus-5.5",
                "status": "SUCCESS",
                "prompt_hash": "dummy_prompt",
                "response_hash": "dummy_response",
            }),
            encoding="utf-8",
        )

        bundler = EvidenceBundler(repo_dir=self.repo_dir)
        bundle_path = bundler.create_bundle(job_id="test_job")

        import zipfile
        with zipfile.ZipFile(bundle_path, "r") as z:
            names = z.namelist()
            self.assertIn("evidence/audit_stage.json", names)


if __name__ == "__main__":
    unittest.main()
