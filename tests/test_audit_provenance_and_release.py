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
            "provider": "anthropic",
            "model": "claude-opus-5.5",
            "execution_id": "msg_01X9Z8A7B6C5D4E3",
            "status": "COMPLETED",
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




if __name__ == "__main__":
    unittest.main()
