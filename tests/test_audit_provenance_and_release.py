from __future__ import annotations

import json
import subprocess
import sys
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
        # TTT.1: プロバイダ名、実行識別子、ステータスの拘束
        self.assertEqual(meta.get("provider"), "anthropic")
        self.assertIsNotNone(meta.get("execution_id"))
        self.assertEqual(meta.get("status"), "COMPLETED")

    def test_audit_runner_extracts_envelope_provider_metadata(self) -> None:
        """TTT.1: プロバイダからのエンベロープレスポンス（実行ID・プロバイダ名入り）を正しく解析・抽出できること。"""
        envelope_json = json.dumps({
            "mode": "LIVE",
            "provider": "anthropic",
            "model": "claude-opus-5.5",
            "execution_id": "msg_01X9Z8A7B6C5D4E3",
            "status": "COMPLETED",
            "stop_reason": "end_turn",
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
        self.assertEqual(meta.get("status"), "COMPLETED")

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
        """TTT.3: MorningReporter が実外部モデルの監査証跡（Provider, Execution ID, 実測64文字SHA-256）を表形式で出力すること。"""
        reporter = MorningReporter(repo_dir=self.repo_dir)
        report_path = self.repo_dir / "NIGHTLY_REPORT.md"

        full_prompt_hash = "a1b2c3d4" * 8  # 64 chars
        full_resp_hash = "e5f6a7b8" * 8    # 64 chars
        full_spec_hash = "11223344" * 8    # 64 chars
        full_diff_hash = "55667788" * 8    # 64 chars

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
                "provider": "anthropic",
                "auditor_model": "claude-opus-5.5",
                "execution_id": "exec_test_99887766",
                "fallback_occurred": False,
                "maker_model": "gemini-3.8-flash",
                "independence_verified": True,
                "raw_prompt_hash": full_prompt_hash,
                "raw_response_hash": full_resp_hash,
                "spec_sha256": full_spec_hash,
                "diff_sha256": full_diff_hash,
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
        self.assertIn("anthropic", content)
        self.assertIn("claude-opus-5.5", content)
        self.assertIn("exec_test_99887766", content)
        self.assertIn("gemini-3.8-flash", content)
        self.assertIn(full_prompt_hash, content)
        self.assertIn(full_resp_hash, content)
        self.assertIn(full_spec_hash, content)
        self.assertIn(full_diff_hash, content)
        # プレースホルダー文字列が残存していないこと
        self.assertNotIn("verified-crypto-hash", content)
        self.assertNotIn("commit-bound (", content)

    def test_bundler_includes_audit_stage_evidence(self) -> None:
        """TTT.2: EvidenceBundler が audit_stage.json を収録し、Manifest と verify_bundle で検証可能であること。"""
        import hashlib
        evidence_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        tree_res = subprocess.run(
            ["git", "rev-parse", f"{self.target_sha}^{{tree}}"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        target_tree = tree_res.stdout.strip()
        spec_hash = hashlib.sha256((self.repo_dir / "SPEC.md").read_bytes()).hexdigest()

        # タスク証跡 task_1.json
        task_file = evidence_dir / "task_1.json"
        task_file.write_text(
            json.dumps({
                "task_id": "1",
                "status": "COMPLETED",
                "commit": self.target_sha,
                "tree_sha": target_tree,
                "spec_sha": spec_hash,
                "checker": {"decision": "APPROVE"},
            }),
            encoding="utf-8",
        )

        audit_file = evidence_dir / "audit_stage.json"
        audit_file.write_text(
            json.dumps({
                "status": "SUCCESS",
                "mode": "LIVE",
                "provider": "anthropic",
                "provider_request_id": "msg_live_1234567890",
                "audit_metadata": {
                    "mode": "LIVE",
                    "provider": "anthropic",
                    "auditor_model": "claude-opus-5.5",
                    "provider_request_id": "msg_live_1234567890",
                    "execution_id": "msg_live_1234567890",
                    "target_commit": self.target_sha,
                    "raw_prompt_hash": "a" * 64,
                    "raw_response_hash": "b" * 64,
                },
                "dispatched_count": 0,
                "rejected_count": 0,
            }),
            encoding="utf-8",
        )

        bundler = EvidenceBundler(repo_dir=self.repo_dir)
        bundle_path = bundler.create_bundle(job_id="test_job")

        import zipfile
        with zipfile.ZipFile(bundle_path, "r") as z:
            names = z.namelist()
            self.assertIn("evidence/audit_stage.json", names)
            manifest = json.loads(z.read("bundle_manifest.json").decode("utf-8"))
            self.assertIn("evidence/audit_stage.json", manifest.get("file_hashes", {}))
            self.assertIn("audit_stage", manifest)
            self.assertEqual(manifest["audit_stage"].get("status"), "SUCCESS")

        # verify_bundle での監査ステージ検証
        res = bundler.verify_bundle(bundle_path)
        self.assertTrue(res.is_valid)
        self.assertTrue(getattr(res, "audit_stage_verified", False))
        self.assertIsNotNone(getattr(res, "audit_stage_info", None))

    def test_auditor_client_fails_closed_when_api_key_missing(self) -> None:
        """UUU.1 / Sol氏 P0指摘: APIキー未設定時に findings: [] で成功偽装せず、Fail-Closed (Exit != 0 または status=FAILED) で安全停止すること。"""
        import os
        env = dict(os.environ)
        env.pop("ANTHROPIC_API_KEY", None)
        env.pop("OPENAI_API_KEY", None)

        proc = subprocess.run(
            [
                "python3",
                "scripts/auditor_client.py",
                "--repo-dir",
                str(self.repo_dir),
                "--model",
                "claude-opus-5.5",
                "--prompt",
                "Audit this impossible bug",
            ],
            check=False,
            capture_output=True,
            text=True,
            env=env,
        )
        # 終了コード非ゼロ、または JSON 出力時の status が FAILED / AUDIT_PENDING であること
        if proc.returncode == 0:
            data = json.loads(proc.stdout)
            self.assertIn(data.get("status"), ["FAILED", "AUDIT_PENDING"])
            self.assertNotEqual(data.get("status"), "COMPLETED")
        else:
            self.assertNotEqual(proc.returncode, 0)

    def test_auditor_client_mock_mode_distinction(self) -> None:
        """UUU.1 / Sol氏 P1指摘: --mock 指定時は mode=MOCK かつ provider_request_id が null または mock_ 接頭辞であること。"""
        proc = subprocess.run(
            ["python3", "scripts/auditor_client.py", "--model", "claude-opus-5.5", "--prompt", "Dummy prompt", "--mock"],
            capture_output=True,
            text=True,
            check=True,
        )
        data = json.loads(proc.stdout)
        self.assertEqual(data.get("mode"), "MOCK")
        self.assertEqual(data.get("status"), "COMPLETED")
        self.assertTrue(str(data.get("execution_id", "")).startswith("mock_"))
        self.assertIsNone(data.get("provider_request_id"))

    def test_auditor_client_live_api_success_provenance(self) -> None:
        """UUU.1: Anthropic API 成功時に mode=LIVE, 本物 provider_request_id (msg_...), execution_id が拘束されること。"""
        import io
        from unittest.mock import MagicMock, patch

        fake_api_response = {
            "id": "msg_01XFDUDYJACGeCrJwHGQAngV",
            "type": "message",
            "role": "assistant",
            "model": "claude-3-opus-20240229",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "```json\n[]\n```"}],
            "usage": {"input_tokens": 120, "output_tokens": 15},
        }

        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fake_api_response).encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = None

        with patch("urllib.request.urlopen", return_value=mock_resp):
            from scripts.auditor_client import main as auditor_main
            old_stdout = sys.stdout
            sys.stdout = buffer = io.StringIO()
            try:
                code = auditor_main(["--model", "claude-opus-5.5", "--prompt", "Audit spec", "--api-key", "test-key-1234"])
            finally:
                sys.stdout = old_stdout

        self.assertEqual(code, 0)
        data = json.loads(buffer.getvalue())
        self.assertEqual(data.get("mode"), "LIVE")
        self.assertEqual(data.get("provider"), "anthropic")
        self.assertEqual(data.get("provider_request_id"), "msg_01XFDUDYJACGeCrJwHGQAngV")
        self.assertEqual(data.get("execution_id"), "msg_01XFDUDYJACGeCrJwHGQAngV")
        self.assertEqual(data.get("status"), "COMPLETED")
        self.assertEqual(data.get("findings"), [])

    def test_bundler_verify_rejects_mock_audit_for_release_validation(self) -> None:
        """UUU.2 / Sol氏 Phase 60 P1指摘: mode=MOCK または provider_request_id 欠落/モック時は audit_stage_verified=False とし、正式リリース判定を拒絶すること。"""
        evidence_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)

        target_tree_res = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        target_tree = target_tree_res.stdout.strip()
        import hashlib
        spec_hash = hashlib.sha256((self.repo_dir / "SPEC.md").read_bytes()).hexdigest()

        # 有効な task_1.json
        (evidence_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "status": "COMPLETED",
                "commit": self.target_sha,
                "tree_sha": target_tree,
                "spec_sha": spec_hash,
                "checker": {"decision": "APPROVE"},
            }),
            encoding="utf-8",
        )

        # MOCK モードの audit_stage.json
        audit_file = evidence_dir / "audit_stage.json"
        audit_file.write_text(
            json.dumps({
                "status": "SUCCESS",
                "mode": "MOCK",
                "audit_metadata": {
                    "mode": "MOCK",
                    "provider": "anthropic",
                    "auditor_model": "claude-opus-5.5",
                    "execution_id": "mock_1234567890",
                    "provider_request_id": None,
                    "raw_prompt_hash": "a" * 64,
                    "raw_response_hash": "b" * 64,
                },
            }),
            encoding="utf-8",
        )

        bundler = EvidenceBundler(repo_dir=self.repo_dir)
        bundle_path = bundler.create_bundle(job_id="test_mock_job")
        res = bundler.verify_bundle(bundle_path)

        # MOCK モードは改ざん検証（is_valid）には通過しても、正式リリース監査（audit_stage_verified）としては False と判定されること
        self.assertFalse(res.audit_stage_verified)
        self.assertTrue(any("not live verified" in w or "MOCK" in w for w in res.warnings))

    def test_extract_findings_fails_closed_on_unparseable_or_prose_text(self) -> None:
        """VVV.1: 有効なJSON配列を含まないテキストや不完全なJSONに対し、[]（欠陥ゼロ偽装）ではなく例外を送出してFail-Closed化すること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            from auditor_client import _extract_findings_from_text
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        # 1. 散文のみでJSONなし（Sol氏指摘ケース）
        with self.assertRaises(ValueError):
            _extract_findings_from_text("I found a critical bug but could not fit JSON")

        # 2. 途中で途切れた不完全なJSON（max_tokens打ち切り等）
        with self.assertRaises(ValueError):
            _extract_findings_from_text('[{"finding_id": "AUD-001", "description": "truncated')

        # 3. 正常な空配列（欠陥なし）は [] を返すこと
        self.assertEqual(_extract_findings_from_text("```json\n[]\n```"), [])
        self.assertEqual(_extract_findings_from_text("[]"), [])

        # 4. 正常な指摘配列は正しくパースされること
        valid_json = '[{"finding_id": "AUD-001", "severity": "P0", "description": "test"}]'
        findings = _extract_findings_from_text(f"```json\n{valid_json}\n```")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["finding_id"], "AUD-001")

    def test_auditor_client_handles_max_tokens_stop_reason_as_pending(self) -> None:
        """VVV.1: Anthropic API の stop_reason が max_tokens の場合、AUDIT_PENDING となり安全停止（Exit 1）すること。"""
        from unittest import mock

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        mock_resp_body = json.dumps({
            "id": "msg_truncated_12345",
            "model": "claude-opus-5-5",
            "stop_reason": "max_tokens",
            "content": [{"type": "text", "text": '[{"finding_id": "AUD-001"'}],
            "usage": {"input_tokens": 15000, "output_tokens": 4096},
        }).encode("utf-8")

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_resp_body
        mock_resp.__enter__.return_value = mock_resp

        env_file = self.repo_dir / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=test_key\n", encoding="utf-8")

        with mock.patch("urllib.request.urlopen", return_value=mock_resp), mock.patch("sys.stderr"):
            exit_code = auditor_client.main([
                "--model", "claude-opus-5.5",
                "--prompt", "test prompt",
                "--repo-dir", str(self.repo_dir),
            ])
            self.assertEqual(exit_code, 1)

    def test_auditor_client_records_stop_reason_in_envelope(self) -> None:
        """VVV.1: 正常完了時（stop_reason=end_turn）、envelope に stop_reason が記録されること。"""
        import io
        from unittest import mock

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        mock_resp_body = json.dumps({
            "id": "msg_success_12345",
            "model": "claude-opus-5-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "[]"}],
            "usage": {"input_tokens": 1000, "output_tokens": 10},
        }).encode("utf-8")

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_resp_body
        mock_resp.__enter__.return_value = mock_resp

        env_file = self.repo_dir / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=test_key\n", encoding="utf-8")

        stdout_capture = io.StringIO()
        with mock.patch("urllib.request.urlopen", return_value=mock_resp), mock.patch("sys.stdout", stdout_capture):
            exit_code = auditor_client.main([
                "--model", "claude-opus-5.5",
                "--prompt", "test prompt",
                "--repo-dir", str(self.repo_dir),
            ])
            self.assertEqual(exit_code, 0)
            output_json = json.loads(stdout_capture.getvalue())
            self.assertEqual(output_json.get("stop_reason"), "end_turn")
            self.assertEqual(output_json.get("status"), "COMPLETED")

    def test_extract_findings_rejects_embedded_empty_list_in_prose(self) -> None:
        """AUD-001: clean_text の途中に [] が含まれるだけの散文応答はゼロ指摘とみなさず ValueError を送出すること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        # Prose containing [] must NOT return []
        prose_with_brackets = "Review summary: failed to inspect modules with [] due to syntax errors"
        with self.assertRaises(ValueError):
            auditor_client._extract_findings_from_text(prose_with_brackets)

        # Strictly [] or ```json [] ``` must return []
        self.assertEqual(auditor_client._extract_findings_from_text("[]"), [])
        self.assertEqual(auditor_client._extract_findings_from_text("```json\n[]\n```"), [])

    def test_auditor_client_fails_closed_when_stop_reason_missing(self) -> None:
        """AUD-003: プロバイダ応答に stop_reason が含まれていない場合、fabricateせず AUDIT_PENDING で終了すること。"""
        import io
        from unittest import mock

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        mock_resp_body = json.dumps({
            "id": "msg_success_12345",
            "model": "claude-opus-5-5",
            # stop_reason is omitted/null
            "content": [{"type": "text", "text": "[]"}],
            "usage": {"input_tokens": 1000, "output_tokens": 10},
        }).encode("utf-8")

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_resp_body
        mock_resp.__enter__.return_value = mock_resp

        env_file = self.repo_dir / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=test_key\n", encoding="utf-8")

        stderr_capture = io.StringIO()
        with mock.patch("urllib.request.urlopen", return_value=mock_resp), mock.patch("sys.stderr", stderr_capture):
            exit_code = auditor_client.main([
                "--model", "claude-opus-5.5",
                "--prompt", "test prompt",
                "--repo-dir", str(self.repo_dir),
            ])
            self.assertEqual(exit_code, 1)
            err_output = json.loads(stderr_capture.getvalue())
            self.assertEqual(err_output.get("status"), "AUDIT_PENDING")
            self.assertIn("MISSING_STOP_REASON", err_output.get("error", ""))

    def test_auditor_client_fails_closed_when_stop_reason_is_not_end_turn(self) -> None:
        """AUD-002: stop_reason が end_turn 以外の値（refusal, stop_sequence, pause_turn等）の場合、AUDIT_PENDING で安全停止すること。"""
        import io
        from unittest import mock

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        for bad_reason in ["refusal", "stop_sequence", "pause_turn", "tool_use"]:
            mock_resp_body = json.dumps({
                "id": "msg_success_12345",
                "model": "claude-opus-5-5",
                "stop_reason": bad_reason,
                "content": [{"type": "text", "text": "[]"}],
                "usage": {"input_tokens": 1000, "output_tokens": 10},
            }).encode("utf-8")

            mock_resp = mock.MagicMock()
            mock_resp.read.return_value = mock_resp_body
            mock_resp.__enter__.return_value = mock_resp

            env_file = self.repo_dir / ".env"
            env_file.write_text("ANTHROPIC_API_KEY=test_key\n", encoding="utf-8")

            stderr_capture = io.StringIO()
            with mock.patch("urllib.request.urlopen", return_value=mock_resp), mock.patch("sys.stderr", stderr_capture):
                exit_code = auditor_client.main([
                    "--model", "claude-opus-5.5",
                    "--prompt", "test prompt",
                    "--repo-dir", str(self.repo_dir),
                ])
                self.assertEqual(exit_code, 1, f"bad stop_reason '{bad_reason}' must exit with code 1")
                err_output = json.loads(stderr_capture.getvalue())
                self.assertEqual(err_output.get("status"), "AUDIT_PENDING")
                self.assertIn("AUDIT_INTERRUPTED", err_output.get("error", ""))

    def test_auditor_client_fails_closed_when_prompt_is_empty(self) -> None:
        """AUD-001: プロンプトが空または未置換の {prompt} で標準入力も空の場合、汎用メッセージへ逃げず Exit 1 (FAILED) で安全停止すること。"""
        import io
        from unittest import mock

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        stderr_capture = io.StringIO()
        with mock.patch("sys.stdin", io.StringIO("")), mock.patch("sys.stderr", stderr_capture):
            exit_code = auditor_client.main([
                "--model", "claude-opus-5.5",
                "--prompt", "{prompt}",
                "--repo-dir", str(self.repo_dir),
            ])
            self.assertEqual(exit_code, 1)
            err_output = json.loads(stderr_capture.getvalue())
            self.assertEqual(err_output.get("status"), "FAILED")
            self.assertIn("EMPTY_AUDIT_PROMPT", err_output.get("error", ""))

    def test_auditor_client_default_max_tokens_is_8192(self) -> None:
        """AUD-002: SPEC VVV.1 に従い、--max-tokens のデフォルト値が 8192 であること。"""
        from unittest import mock

        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        mock_resp_body = json.dumps({
            "id": "msg_success_12345",
            "model": "claude-opus-5-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "[]"}],
            "usage": {"input_tokens": 1000, "output_tokens": 10},
        }).encode("utf-8")

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_resp_body
        mock_resp.__enter__.return_value = mock_resp

        env_file = self.repo_dir / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=test_key\n", encoding="utf-8")

        sent_payload = {}

        def fake_urlopen(req, timeout=120):
            nonlocal sent_payload
            sent_payload = json.loads(req.data.decode("utf-8"))
            return mock_resp

        with mock.patch("urllib.request.urlopen", side_effect=fake_urlopen):
            exit_code = auditor_client.main([
                "--model", "claude-opus-5.5",
                "--prompt", "valid prompt",
                "--repo-dir", str(self.repo_dir),
            ])
            self.assertEqual(exit_code, 0)
            self.assertEqual(sent_payload.get("max_tokens"), 8192)

    def test_runner_parse_response_rejects_prose_with_embedded_empty_brackets(self) -> None:
        """AUD-001 / VVV.1: parse_response は地の文の中に含まれる '[]' を空配列として誤認せず、Fail-Closed（ValueError）で拒絶すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        prose = "Review summary: failed to inspect modules with [] due to syntax errors"
        with self.assertRaises(ValueError):
            runner.parse_response(prose)

    def test_auditor_client_missing_provider_id_fails_closed(self) -> None:
        """AUD-002 / UUU.1 / VVV.4: Anthropic API レスポンスに本物の id が含まれない場合、msg_unknown などの自前IDを捏造せず Exit 1 で安全停止すること。"""
        from unittest import mock
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        mock_resp_body = json.dumps({
            # id が欠落
            "model": "claude-opus-5-5",
            "stop_reason": "end_turn",
            "content": [{"type": "text", "text": "[]"}],
            "usage": {"input_tokens": 1000, "output_tokens": 10},
        }).encode("utf-8")

        mock_resp = mock.MagicMock()
        mock_resp.read.return_value = mock_resp_body
        mock_resp.__enter__.return_value = mock_resp

        env_file = self.repo_dir / ".env"
        env_file.write_text("ANTHROPIC_API_KEY=test_key\n", encoding="utf-8")

        with mock.patch("urllib.request.urlopen", return_value=mock_resp):
            exit_code = auditor_client.main([
                "--model", "claude-opus-5.5",
                "--prompt", "valid prompt",
                "--repo-dir", str(self.repo_dir),
            ])
            self.assertEqual(exit_code, 1)

    def test_extract_findings_from_text_accepts_empty_array_in_codeblock_with_prose(self) -> None:
        """AUD-001 / VVV.1: レビュー本文に前置きテキストが存在する場合でも、```json\n[]\n``` が含まれていれば正常に 0件の指摘（[]）として抽出されること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        text = "I have completed the review.\n\n```json\n[]\n```\n\nNo issues found."
        findings = auditor_client._extract_findings_from_text(text)
        self.assertEqual(findings, [])

    def test_extract_findings_from_text_prioritizes_actual_findings_over_preceding_empty_codeblock(self) -> None:
        """AUD-001: 応答文中に例示等の空配列コードブロックが先行して存在する場合でも、後続の実指摘コードブロックを正しく抽出し、欠陥ゼロ偽装（[]）を防ぐこと。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        text = (
            "Example format: ```json\n[]\n```\n\n"
            "Actual findings:\n```json\n"
            '[{"finding_id": "AUD-001", "description": "real issue", "severity": "P1"}]\n'
            "```"
        )
        findings = auditor_client._extract_findings_from_text(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["finding_id"], "AUD-001")

    def test_runner_parse_response_rejects_live_envelope_with_non_end_turn_stop_reason(self) -> None:
        """AUD-002: IndependentAuditRunner.parse_response は mode: LIVE のエンベロープで stop_reason が 'end_turn' 以外（'max_tokens' 等）の場合、Fail-Closed（ValueError）で拒絶すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "model": "claude-opus-5.5",
            "status": "COMPLETED",
            "stop_reason": "max_tokens",  # truncated!
            "findings": [],
        }
        output = json.dumps(envelope)
        with self.assertRaises(ValueError) as cm:
            runner.parse_response(output)
        self.assertIn("AUDIT_INTERRUPTED", str(cm.exception))

    def test_runner_parse_response_rejects_truncated_live_envelope_without_explicit_mode_key(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 8): エンベロープに 'mode': 'LIVE' キーが明示されていない場合でも、provider/execution_id から LIVE と判定された場合は stop_reason 検証をスキップせず Fail-Closed で拒絶すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "provider": "anthropic",
            "model": "claude-opus-5.5",
            "provider_request_id": "msg_01TRUNCATED",
            "execution_id": "msg_01TRUNCATED",
            "status": "COMPLETED",
            "stop_reason": "max_tokens",  # truncated!
            "findings": [],
        }
        output = json.dumps(envelope)
        with self.assertRaises(ValueError) as cm:
            runner.parse_response(output)
        self.assertIn("AUDIT_INTERRUPTED", str(cm.exception))

    def test_extract_findings_fails_closed_when_codeblock_has_malformed_json_despite_preceding_empty_block(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 8): 応答文中に例示空配列コードブロックが存在しても、後続の指摘コードブロックが構文不正（JSONDecodeError）の場合は空配列（欠陥ゼロ）にフォールバックせず Fail-Closed（ValueError）とすること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        text = (
            "Example format:\n```json\n[]\n```\n\n"
            "Actual findings:\n```json\n"
            '[{"finding_id": "AUD-001", "severity": "P0", "description": "broken JSON",}]\n'
            "```"
        )
        with self.assertRaises(ValueError) as cm:
            auditor_client._extract_findings_from_text(text)
        self.assertIn("Malformed JSON", str(cm.exception))

    def test_extract_findings_allows_empty_json_with_non_json_explanation_codeblock(self) -> None:
        """AUD-003 (Sol Phase 62 / Cycle 9): 有効な ```json [] ``` の後に説明文コードブロック（非JSON言語タグまたはプレーンテキスト）が存在しても、誤ってJSONパース失敗として拒絶せず、正しく空配列を抽出できること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        text = (
            "Here is the audit result:\n```json\n[]\n```\n\n"
            "Explanation:\n```\nAll specifications met.\n```\n"
        )
        findings = auditor_client._extract_findings_from_text(text)
        self.assertEqual(findings, [])

    def test_extract_findings_handles_quoted_instructions_with_ellipsis_codeblock_and_empty_findings(self) -> None:
        """AUD-004 (Sol Phase 62 / Cycle 10): 指示引用文等に含まれる ```json ... ``` のような非JSONコードブロックが存在しても、後続の正当な ```json [] ``` を正しく抽出し、誤判定を防ぐこと。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        text = (
            "I will output only the JSON array in ```json ... ``` as instructed.\n\n"
            "```json\n[]\n```"
        )
        findings = auditor_client._extract_findings_from_text(text)
        self.assertEqual(findings, [])

    def test_extract_findings_prioritizes_raw_findings_outside_empty_example_codeblock(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 11): コードブロック内の例示空配列（```json [] ```）が存在しても、ブロック外に生のJSON指摘配列が存在する場合は破棄せず正しく抽出すること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        text = (
            "If there were no defects I would reply ```json\n[]\n```.\n"
            "However I found the following defects:\n"
            '[{"finding_id": "AUD-001", "severity": "P0", "description": "real bug"}]'
        )
        findings = auditor_client._extract_findings_from_text(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["finding_id"], "AUD-001")

    def test_runner_parse_response_rejects_envelope_with_non_completed_status(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 11): IndependentAuditRunner.parse_response は status が COMPLETED 以外のエンベロープ（FAILED, AUDIT_PENDING 等）を空指摘（欠陥ゼロ）として処理せず、Fail-Closed（ValueError）で拒絶すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "model": "claude-opus-5.5",
            "status": "FAILED",
            "error": "UNPARSEABLE_AUDIT_RESPONSE: No valid JSON array found",
            "stop_reason": "end_turn",
            "findings": [],
        }
        with self.assertRaises(ValueError) as cm:
            runner.parse_response(json.dumps(envelope))
        self.assertIn("AUDIT_FAILED", str(cm.exception))

    def test_parse_response_example_empty_block_must_not_mask_real_findings(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 12): IndependentAuditRunner.parse_response は、先頭に例示空配列コードブロックが存在しても、後続の指摘コードブロックを破棄せず抽出すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        text = (
            "Example format: ```json\n[]\n```\n\n"
            "Actual findings:\n```json\n"
            '[{"finding_id": "AUD-001", "severity": "P0", "spec_reference": "VVV.1", '
            '"location": "a.py:1", "description": "real bug", '
            '"reproduction_test_code": "import unittest", "status": "REPORTED"}]\n'
            "```"
        )
        findings = runner.parse_response(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "AUD-001")

    def test_failed_status_envelope_without_provider_keys_is_rejected(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 12): provider や execution_id が含まれないエンベロープでも status: FAILED の場合は欠陥ゼロとみなさず拒絶すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {"status": "FAILED", "error": "UNPARSEABLE_AUDIT_RESPONSE", "findings": []}
        with self.assertRaises(ValueError) as cm:
            runner.parse_response(json.dumps(envelope))
        self.assertIn("AUDIT_FAILED", str(cm.exception))

    def test_explicit_empty_block_with_bracketed_prose_returns_empty(self) -> None:
        """AUD-004 (Sol Phase 62 / Cycle 12): 有効な ```json [] ``` の後に角括弧を含む散文（例: [src/foo.py]）が存在しても、誤って構文エラーとせず空配列を抽出できること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        text = "```json\n[]\n```\nNo defects found in [src/foo.py]."
        findings = auditor_client._extract_findings_from_text(text)
        self.assertEqual(findings, [])

    def test_parse_response_malformed_findings_block_after_empty_example_fails_closed(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 13): parse_response は例示空配列コードブロックが存在しても、後続の指摘コードブロックが構文不正の場合は欠陥ゼロにフォールバックせず Fail-Closed（ValueError）とすること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        text = (
            "Example format:\n```json\n[]\n```\n\n"
            "Actual findings:\n```json\n"
            '[{"finding_id": "AUD-001", "severity": "P0", "description": "broken",}]\n'
            "```"
        )
        with self.assertRaises(ValueError):
            runner.parse_response(text)

    def test_malformed_raw_findings_after_empty_example_block_fails_closed(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 13): auditor_client は例示空配列コードブロックが存在しても、後続のブロック外生の指摘JSON（[{...}]）が構文不正の場合は欠陥ゼロにフォールバックせず Fail-Closed（ValueError）とすること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        text = (
            "Example format: ```json\n[]\n```\n"
            "Findings:\n"
            '[{"finding_id": "AUD-001", "severity": "P0", "description": "real bug",}]'
        )
        with self.assertRaises(ValueError):
            auditor_client._extract_findings_from_text(text)

    def test_raw_findings_outside_empty_example_block_not_discarded(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 14): IndependentAuditRunner.parse_response は、例示空配列コードブロックが存在しても、ブロック外の生JSON指摘を破棄せず抽出すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        finding = {
            "finding_id": "AUD-RAW-001",
            "severity": "P0",
            "spec_reference": "VVV.1",
            "location": "a.py:1",
            "description": "real bug",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = (
            "If there were no defects I would reply ```json\n[]\n```.\n"
            "However I found the following defects:\n" + json.dumps([finding])
        )
        findings = runner.parse_response(text)
        self.assertEqual(len(findings), 1, "real findings were discarded as zero defects")
        self.assertEqual(findings[0].finding_id, "AUD-RAW-001")

    def test_findings_after_bracketed_prose_and_empty_example_not_discarded(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 14): auditor_client は、例示空配列コードブロックの後に角括弧散文（[src/a.py]）が先行しても、後続の生JSON指摘を破棄せず抽出すること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        finding = {"finding_id": "AUD-BR-001", "severity": "P0", "description": "real bug"}
        text = (
            "Example format: ```json\n[]\n```\n"
            "I inspected [src/a.py] and found:\n" + json.dumps([finding])
        )
        findings = auditor_client._extract_findings_from_text(text)
        self.assertEqual(len(findings), 1, f"real findings silently discarded: {findings!r}")
        self.assertEqual(findings[0]["finding_id"], "AUD-BR-001")

    def test_parse_response_with_bracketed_prose_and_raw_findings_not_discarded(self) -> None:
        """AUD-001/AUD-002: parse_response は、例示空配列コードブロック＋角括弧散文（[src/a.py]）＋生JSON指摘の組み合わせでも指摘を正しく抽出すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        finding = {
            "finding_id": "AUD-BR-002",
            "severity": "P0",
            "spec_reference": "VVV.1",
            "location": "b.py:1",
            "description": "bug",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = (
            "Example format: ```json\n[]\n```\n"
            "I checked [src/b.py] and found:\n" + json.dumps([finding])
        )
        findings = runner.parse_response(text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "AUD-BR-002")

    def test_parse_response_malformed_raw_findings_after_bracketed_prose_fails_closed(self) -> None:
        """AUD-001/AUD-002: parse_response は、例示空配列コードブロック＋角括弧散文＋構文不正生JSONの場合、Fail-Closedで例外を送出すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        text = (
            "Example format: ```json\n[]\n```\n"
            "I checked [src/b.py] and found:\n"
            '[{"finding_id": "AUD-BR-002", "severity": "P0", "description": "broken",}]'
        )
        with self.assertRaises(ValueError):
            runner.parse_response(text)

    def test_fenced_truncated_live_envelope_is_rejected(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 15): fenced された truncated (stop_reason=max_tokens) LIVE envelope が内部 findings スライスによりエンベロープ検証をバイパスせず拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "model": "claude-opus-5.5",
            "provider_request_id": "msg_01TRUNC",
            "execution_id": "msg_01TRUNC",
            "status": "COMPLETED",
            "stop_reason": "max_tokens",
            "findings": [{
                "finding_id": "AUD-T-001",
                "severity": "P1",
                "spec_reference": "VVV.1",
                "location": "a.py:1",
                "description": "partial finding before truncation",
                "reproduction_test_code": "import unittest",
                "status": "REPORTED",
            }],
        }
        output = "```json\n" + json.dumps(envelope) + "\n```"
        with self.assertRaises(ValueError):
            runner.parse_response(output)

    def test_failed_envelope_after_log_line_is_rejected(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 15): ログ行の後に続く status=FAILED の envelope が内部 findings スライスによりエンベロープ検証をバイパスせず拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_01FAIL",
            "status": "FAILED",
            "error": "UNPARSEABLE",
            "stop_reason": "end_turn",
            "findings": [{
                "finding_id": "AUD-T-001",
                "severity": "P1",
                "spec_reference": "VVV.1",
                "location": "a.py:1",
                "description": "partial finding before truncation",
                "reproduction_test_code": "import unittest",
                "status": "REPORTED",
            }],
        }
        output = "auditor: warning: retrying request\n" + json.dumps(envelope)
        with self.assertRaises(ValueError):
            runner.parse_response(output)

    def test_uppercase_json_fence_after_empty_example_not_discarded(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 16): 大文字 ```JSON フェンス内の指摘が Step 3 でスキップされゼロ指摘に偽装されないこと。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        finding = {
            "finding_id": "AUD-UP-001",
            "severity": "P0",
            "spec_reference": "VVV.1",
            "location": "a.py:1",
            "description": "real bug",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = (
            "Example format: ```json\n[]\n```\n\n"
            "Actual findings:\n```JSON\n" + json.dumps([finding]) + "\n```"
        )
        findings = runner.parse_response(text)
        self.assertEqual(len(findings), 1, f"real findings silently discarded as zero defects: {findings!r}")
        self.assertEqual(findings[0].finding_id, "AUD-UP-001")

    def test_single_finding_object_with_reported_status_is_accepted(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 16): 単一の finding オブジェクト（status: REPORTED）が envelope status と誤認されて拒絶されず、正常に受理されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        finding = {
            "finding_id": "AUD-SINGLE-001",
            "severity": "P1",
            "spec_reference": "VVV.1",
            "location": "a.py:1",
            "description": "single finding object",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        findings = runner.parse_response(json.dumps(finding))
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "AUD-SINGLE-001")

    def test_failed_envelope_after_bracketed_log_line_is_rejected(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 17): 角括弧付きログ行（[auditor]）の後に続く status=FAILED の envelope がバイパスされず拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_01FAIL",
            "status": "FAILED",
            "error": "UNPARSEABLE",
            "stop_reason": "end_turn",
            "findings": [{
                "finding_id": "AUD-T-001",
                "severity": "P1",
                "spec_reference": "VVV.1",
                "location": "a.py:1",
                "description": "partial finding",
                "reproduction_test_code": "import unittest",
                "status": "REPORTED",
            }],
        }
        output = "[auditor] warning: retrying request\n" + json.dumps(envelope)
        with self.assertRaises(ValueError):
            runner.parse_response(output)

    def test_truncated_live_envelope_after_bracketed_log_line_is_rejected(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 17): 角括弧付きログ行（[info]）の後に続く stop_reason=max_tokens の envelope がバイパスされず拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_01TRUNC",
            "execution_id": "msg_01TRUNC",
            "status": "COMPLETED",
            "stop_reason": "max_tokens",
            "findings": [{
                "finding_id": "AUD-T-001",
                "severity": "P1",
                "spec_reference": "VVV.1",
                "location": "a.py:1",
                "description": "partial finding",
                "reproduction_test_code": "import unittest",
                "status": "REPORTED",
            }],
        }
        output = "[info] auditor started\n" + json.dumps(envelope)
        with self.assertRaises(ValueError):
            runner.parse_response(output)

    def test_single_finding_object_block_not_discarded(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 17): 例示空コードブロックの後に続くコードブロック内の単一指摘オブジェクトが破棄されず抽出されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        finding = {
            "finding_id": "AUD-OBJ-001",
            "severity": "P0",
            "spec_reference": "VVV.1",
            "location": "a.py:1",
            "description": "real bug",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = (
            "Example format: ```json\n[]\n```\n\n"
            "Actual finding:\n```json\n" + json.dumps(finding) + "\n```"
        )
        findings = runner.parse_response(text)
        self.assertEqual(len(findings), 1, f"real finding silently discarded as zero defects: {findings!r}")
        self.assertEqual(findings[0].finding_id, "AUD-OBJ-001")

    def test_failed_envelope_after_brace_log_line_is_rejected(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 18): 中括弧を含む先行ログ行が存在しても、status: FAILED エンベロープが確実に棄却されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_01FAIL",
            "status": "FAILED",
            "error": "UNPARSEABLE",
            "stop_reason": "end_turn",
            "findings": [{
                "finding_id": "AUD-T-001",
                "severity": "P1",
                "spec_reference": "VVV.1",
                "location": "a.py:1",
                "description": "partial finding",
                "reproduction_test_code": "import unittest",
                "status": "REPORTED",
            }],
        }
        output = "auditor: retry {attempt=2}\n" + json.dumps(envelope)
        with self.assertRaises(ValueError):
            runner.parse_response(output)

    def test_truncated_envelope_after_brace_log_line_is_rejected(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 18): 中括弧を含む先行ログ行が存在しても、stop_reason: max_tokens エンベロープが確実に棄却されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_01TRUNC",
            "execution_id": "msg_01TRUNC",
            "status": "COMPLETED",
            "stop_reason": "max_tokens",
            "findings": [{
                "finding_id": "AUD-T-001",
                "severity": "P1",
                "spec_reference": "VVV.1",
                "location": "a.py:1",
                "description": "partial finding",
                "reproduction_test_code": "import unittest",
                "status": "REPORTED",
            }],
        }
        output = "auditor: retry {attempt=2}\n" + json.dumps(envelope)
        with self.assertRaises(ValueError):
            runner.parse_response(output)

    def test_auditor_client_single_object_block_after_empty_example_not_discarded(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 18): auditor_client._extract_findings_from_text において例示空配列の後の単一指摘オブジェクトが抽出されること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))
        finding = {
            "finding_id": "AUD-OBJ-001",
            "severity": "P0",
            "description": "real bug",
            "status": "REPORTED",
        }
        text = (
            "Example format: ```json\n[]\n```\n\n"
            "Actual finding:\n```json\n" + json.dumps(finding) + "\n```"
        )
        result = auditor_client._extract_findings_from_text(text)
        self.assertNotEqual(result, [], "real finding silently converted to zero defects")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].get("finding_id"), "AUD-OBJ-001")

    def test_fenced_multi_finding_array_returns_all_findings(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 19): コードブロックで囲まれた複数要素の指摘配列において全件抽出され、第2要素以降が欠落しないこと。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        finding1 = {
            "finding_id": "AUD-M-001",
            "severity": "P1",
            "spec_reference": "VVV.1",
            "location": "a.py:1",
            "description": "bug 1",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        finding2 = {
            "finding_id": "AUD-M-002",
            "severity": "P1",
            "spec_reference": "VVV.1",
            "location": "a.py:2",
            "description": "bug 2",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = "```json\n" + json.dumps([finding1, finding2]) + "\n```"
        findings = runner.parse_response(text)
        self.assertEqual([f.finding_id for f in findings], ["AUD-M-001", "AUD-M-002"], "second finding silently discarded")

    def test_prose_prefixed_multi_finding_array_returns_all_findings(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 19): 散文が先行する複数要素の指摘配列において全件抽出され、第2要素以降が欠落しないこと。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        finding3 = {
            "finding_id": "AUD-M-003",
            "severity": "P1",
            "spec_reference": "VVV.1",
            "location": "a.py:3",
            "description": "bug 3",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        finding4 = {
            "finding_id": "AUD-M-004",
            "severity": "P1",
            "spec_reference": "VVV.1",
            "location": "a.py:4",
            "description": "bug 4",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = "Here are the findings:\n" + json.dumps([finding3, finding4])
        findings = runner.parse_response(text)
        self.assertEqual([f.finding_id for f in findings], ["AUD-M-003", "AUD-M-004"], "second finding silently discarded")

    def test_malformed_single_object_after_empty_example_fails_closed(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 20): 例示空コードブロックの後に不正な単一指摘JSONが続く場合、ゼロ件偽装せずFail-Closed例外を送出すること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        text = (
            "Example format: ```json\n[]\n```\n"
            "Finding:\n"
            '{"finding_id": "AUD-X", "severity": "P0", "description": "real bug",}'
        )
        with self.assertRaises(ValueError):
            runner.parse_response(text)

    def test_auditor_client_malformed_single_object_after_empty_example_fails_closed(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 20): auditor_client において不正な単一指摘JSONが続く場合、Fail-Closed例外を送出すること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))
        text = (
            "Example format: ```json\n[]\n```\n"
            "Finding:\n"
            '{"finding_id": "AUD-X", "severity": "P0", "description": "real bug",}'
        )
        with self.assertRaises(ValueError):
            auditor_client._extract_findings_from_text(text)

    def test_truncated_minimal_envelope_without_provider_keys_rejected(self) -> None:
        """AUD-003 (Sol Phase 62 / Cycle 20): プロバイダメタデータキーを持たない最小限のエンベロープでも stop_reason: max_tokens は確実に棄却されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        envelope = {"stop_reason": "max_tokens", "findings": []}
        with self.assertRaises(ValueError):
            runner.parse_response(json.dumps(envelope))

    def test_example_findings_dict_does_not_mask_real_findings(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 21): 例示形式辞書 {"findings": []} が実指摘配列の前に存在しても、実指摘がゼロ件偽装されず抽出されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        finding = {
            "finding_id": "AUD-EX-001",
            "severity": "P0",
            "spec_reference": "VVV.1",
            "location": "a.py:1",
            "description": "real bug",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = 'Expected format: {"findings": []}\nActual result:\n' + json.dumps([finding])
        findings = runner.parse_response(text)
        self.assertEqual(len(findings), 1, f"real findings silently discarded as zero defects: {findings!r}")
        self.assertEqual(findings[0].finding_id, "AUD-EX-001")

    def test_all_raw_findings_after_empty_example_block_are_returned(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 21): auditor_client において例示空コードブロックに続く生JSON配列の全指摘が抽出されること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))
        f1 = {"finding_id": "AUD-A", "severity": "P0", "description": "bug a"}
        f2 = {"finding_id": "AUD-B", "severity": "P1", "description": "bug b"}
        text = "Example format: ```json\n[]\n```\nFindings:\n" + json.dumps([f1, f2])
        result = auditor_client._extract_findings_from_text(text)
        self.assertEqual([r.get("finding_id") for r in result], ["AUD-A", "AUD-B"], "second finding silently discarded")

    def test_findings_split_across_multiple_code_blocks_are_all_returned(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 22): 複数のコードブロックに分割された指摘が全て集約され欠落しないこと。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        f1 = {
            "finding_id": "AUD-MB-001",
            "severity": "P0",
            "spec_reference": "VVV.1",
            "location": "a.py:1",
            "description": "bug 1",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        f2 = {
            "finding_id": "AUD-MB-002",
            "severity": "P1",
            "spec_reference": "VVV.1",
            "location": "a.py:2",
            "description": "bug 2",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = (
            "P0 findings:\n```json\n" + json.dumps([f1]) + "\n```\n\n"
            "P1 findings:\n```json\n" + json.dumps([f2]) + "\n```"
        )
        findings = runner.parse_response(text)
        ids = sorted(f.finding_id for f in findings)
        self.assertEqual(ids, ["AUD-MB-001", "AUD-MB-002"])

    def test_all_code_blocks_are_considered_in_auditor_client(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 22): auditor_client において複数コードブロックの指摘が全て集約され欠落しないこと。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))
        f1 = {"finding_id": "AUD-MB-001", "severity": "P0", "description": "bug one"}
        f2 = {"finding_id": "AUD-MB-002", "severity": "P1", "description": "bug two"}
        text = (
            "P0 findings:\n```json\n" + json.dumps([f1]) + "\n```\n\n"
            "P1 findings:\n```json\n" + json.dumps([f2]) + "\n```"
        )
        result = auditor_client._extract_findings_from_text(text)
        ids = sorted(r.get("finding_id") for r in result)
        self.assertEqual(ids, ["AUD-MB-001", "AUD-MB-002"])

    def test_truncated_envelope_block_followed_by_findings_block_is_rejected(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 23): エンベロープブロックが stop_reason: max_tokens の場合、後続ブロックに指摘配列があっても envelope validation により AUDIT_INTERRUPTED で拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        env = {
            "mode": "LIVE",
            "provider": "anthropic",
            "status": "COMPLETED",
            "stop_reason": "max_tokens",
            "execution_id": "msg_trunc_123",
            "provider_request_id": "msg_trunc_123",
        }
        f1 = {
            "finding_id": "AUD-TRUNC-001",
            "severity": "P1",
            "spec_reference": "VVV.1",
            "location": "src/a.py:10",
            "description": "bug description",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = (
            "Envelope:\n```json\n" + json.dumps(env) + "\n```\n\n"
            "Findings:\n```json\n" + json.dumps([f1]) + "\n```"
        )
        with self.assertRaises(ValueError) as ctx:
            runner.parse_response(text)
        self.assertIn("AUDIT_INTERRUPTED", str(ctx.exception))

    def test_failed_envelope_block_followed_by_findings_block_is_rejected(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 23): エンベロープブロックが status: FAILED の場合、後続ブロックに指摘があっても envelope validation により AUDIT_FAILED で拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        env = {
            "mode": "LIVE",
            "provider": "anthropic",
            "status": "FAILED",
            "error": "Rate limit exceeded",
            "stop_reason": "end_turn",
            "execution_id": "msg_fail_123",
            "provider_request_id": "msg_fail_123",
        }
        f1 = {
            "finding_id": "AUD-FAIL-001",
            "severity": "P1",
            "spec_reference": "VVV.1",
            "location": "src/a.py:10",
            "description": "bug description",
            "reproduction_test_code": "import unittest",
            "status": "REPORTED",
        }
        text = (
            "Envelope:\n```json\n" + json.dumps(env) + "\n```\n\n"
            "Findings:\n```json\n" + json.dumps([f1]) + "\n```"
        )
        with self.assertRaises(ValueError) as ctx:
            runner.parse_response(text)
        self.assertIn("AUDIT_FAILED", str(ctx.exception))

    def test_error_only_object_is_not_zero_findings(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 24): error のみを含むオブジェクトや findings が非リストのオブジェクトがゼロ欠陥に偽装されず、Fail-Closed（ValueError）で拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        with self.assertRaises(ValueError):
            runner.parse_response('{"error": "Rate limit exceeded"}')

        with self.assertRaises(ValueError):
            runner.parse_response('{"findings": "none"}')

    def test_error_envelope_with_empty_findings_is_rejected(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 25): error キーを持つエンベロープは、空の findings 配列を含んでいてもゼロ欠陥として扱われず Fail-Closed（ValueError）で拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        output = '{"error": "Rate limit exceeded", "findings": []}'
        with self.assertRaises(ValueError):
            runner.parse_response(output)

    def test_fenced_error_object_after_empty_example_is_rejected(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 25): 例示空コードブロックの後に error オブジェクトのコードブロックが続く場合、例示空配列でゼロ欠陥偽装されず Fail-Closed（ValueError）で拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        text = (
            "Example format: ```json\n[]\n```\n"
            "Result:\n```json\n{\"error\": \"Rate limit exceeded\"}\n```"
        )
        with self.assertRaises(ValueError):
            runner.parse_response(text)

    def test_auditor_client_fenced_error_object_after_empty_example_is_rejected(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 25): auditor_client において例示空コードブロックの後に error オブジェクトのコードブロックが続く場合、Fail-Closed（ValueError）で拒絶されること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))
        text = (
            "Example format: ```json\n[]\n```\n"
            "Result:\n```json\n{\"error\": \"Rate limit exceeded\"}\n```"
        )
        with self.assertRaises(ValueError):
            auditor_client._extract_findings_from_text(text)

    def test_auditor_client_rejects_unrendered_prompt_file_placeholder(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 26): auditor_client に未置換の {prompt_file} または {prompt} が渡された場合、空プロンプトとして Fail-Closed（終了コード 1）で拒絶されること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))

        ret = auditor_client.main(["--model", "claude-opus-5.5", "--prompt", "{prompt_file}", "--mock"])
        self.assertEqual(ret, 1)

    def test_auditor_runner_substitutes_prompt_file_template(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 26): IndependentAuditRunner は auditor_cmd_template 中の {prompt_file} を実在する一時ファイルパスに置換して実行し、実行後に安全に削除すること。"""
        cmd_template = (
            "test -f {prompt_file} && python3 scripts/auditor_client.py --model {model} --prompt {prompt_file} --mock"
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
        self.assertEqual(findings, [])

    def test_non_dict_list_in_code_block_fails_closed(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 27): 例示空ブロックの後に文字列リストなどの非dict配列ブロックが続く場合、ゼロ欠陥偽装されず Fail-Closed（ValueError）で拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        text = (
            "Example format: ```json\n[]\n```\n\n"
            "Findings:\n```json\n[\"P0: SPEC.md VVV.3 violated in bundler.py\", \"P1: stop_reason not recorded\"]\n```"
        )
        with self.assertRaises(ValueError):
            runner.parse_response(text)

    def test_auditor_client_non_dict_list_in_code_block_fails_closed(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 27): auditor_client において例示空ブロックの後に文字列リストなどの非dict配列ブロックが続く場合、Fail-Closed（ValueError）で拒絶されること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))
        text = (
            "Example format: ```json\n[]\n```\n\n"
            "Findings:\n```json\n[\"P0: SPEC.md VVV.3 violated in bundler.py\", \"P1: stop_reason not recorded\"]\n```"
        )
        with self.assertRaises(ValueError):
            auditor_client._extract_findings_from_text(text)

    def test_fenced_mixed_finding_array_must_fail_closed(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 28): auditor_client._extract_findings_from_text において、実指摘と非dict要素が混在する配列が渡された場合、非dict要素を黙殺して部分配列を返さず Fail-Closed（ValueError）で拒絶されること。"""
        sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
        try:
            import auditor_client
        finally:
            if str(Path(__file__).parent.parent / "scripts") in sys.path:
                sys.path.remove(str(Path(__file__).parent.parent / "scripts"))
        finding = {"finding_id": "AUD-X", "severity": "P0", "description": "real bug"}
        text = "```json\n" + json.dumps([finding, "P0: second defect reported as plain string"]) + "\n```"
        with self.assertRaises(ValueError):
            auditor_client._extract_findings_from_text(text)

    def test_later_failed_envelope_block_must_be_rejected(self) -> None:
        """AUD-002 (Sol Phase 62 / Cycle 28): 複数のEnvelopeブロックが存在し、後続のEnvelopeが非COMPLETED（FAILED等）である場合、先行Envelopeで成功偽装されず Fail-Closed（ValueError）で拒絶されること。"""
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")
        ok_env = {
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_ok_1",
            "execution_id": "msg_ok_1",
            "status": "COMPLETED",
            "stop_reason": "end_turn",
            "findings": [],
        }
        failed_env = {
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_fail_2",
            "execution_id": "msg_fail_2",
            "status": "FAILED",
            "stop_reason": "end_turn",
            "findings": [],
        }
        text = (
            "Attempt 1:\n```json\n" + json.dumps(ok_env) + "\n```\n\n"
            "Attempt 2:\n```json\n" + json.dumps(failed_env) + "\n```"
        )
        with self.assertRaises(ValueError):
            runner.parse_response(text)


if __name__ == "__main__":
    unittest.main()
