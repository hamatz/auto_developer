from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.audit import AuditConfig, IndependentAuditRunner
from auto_developer.bundler import EvidenceBundler
from auto_developer.cli import main as cli_main
from auto_developer.orchestrator import NightlyOrchestrator

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

from review_test_helpers import ensure_synthetic_review_evidence


class TestAudit7Chunking(unittest.TestCase):
    """AUDIT-7: 監査の入力の分割（--audit-max-diff-bytes、chunks[]、verify-bundle）の受入テスト。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        (self.repo_dir / "SPEC.md").write_text("# Spec\nSPEC_MARKER_FULL_CONTENT\n", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# Plan\n- [x] 1. Done\n", encoding="utf-8")
        (self.repo_dir / "BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")
        (self.repo_dir / "src").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
        (self.repo_dir / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")
        (self.repo_dir / "src" / "c.py").write_text("C = 1\n", encoding="utf-8")
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

    def _file_diff_bytes(self, target_sha: str, rel_path: str) -> int:
        res = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", rel_path],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )
        return len(res.stdout.encode("utf-8"))

    def _make_auditor_script(self, script_path: Path, handler_body: str) -> str:
        script_path.write_text(
            "import json, sys\n"
            "prompt = sys.stdin.read()\n"
            f"{handler_body}\n",
            encoding="utf-8",
        )
        return f"{sys.executable} {script_path}"

    def test_no_chunking_when_diff_within_max_bytes(self) -> None:
        """差分が --audit-max-diff-bytes 以下の場合は分割せず1回だけ監査役を呼ぶこと。"""
        (self.repo_dir / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
        (self.repo_dir / "src" / "b.py").write_text("B = 2\n", encoding="utf-8")
        target_sha = self._commit_all("small diff")

        calls_log = self.repo_dir / "calls.jsonl"
        script = self.repo_dir / "auditor.py"
        cmd = self._make_auditor_script(
            script,
            f"with open({str(calls_log)!r}, 'a', encoding='utf-8') as f:\n"
            "    f.write(json.dumps({'len': len(prompt)}) + '\\n')\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
            "'provider_request_id': 'msg_single_001', 'usage': {'input_tokens': 100, 'output_tokens': 20}, 'findings': []}))",
        )

        config = AuditConfig(maker_model="gemini-3.8-flash", auditor_model="claude-opus-5.5", baseline_commit=self.baseline_sha)
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd,
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.baseline_sha,
            audit_max_diff_bytes=200000,
        )
        findings = runner.audit_repository(repo_dir=self.repo_dir, target_commit=target_sha, baseline_commit=self.baseline_sha)
        self.assertEqual(findings, [])
        lines = calls_log.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)

    def test_chunking_packs_files_in_path_order_and_includes_context(self) -> None:
        """差分が上限を超える場合、パス順に前から詰め、各組にSPEC全体・自組差分・何組目か・他組ファイル一覧を渡すこと。"""
        (self.repo_dir / "src" / "a.py").write_text("A = '" + ("x" * 120) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "b.py").write_text("B = '" + ("y" * 120) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "c.py").write_text("C = '" + ("z" * 120) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("three medium files")

        ba = self._file_diff_bytes(target_sha, "src/a.py")
        bb = self._file_diff_bytes(target_sha, "src/b.py")
        bc = self._file_diff_bytes(target_sha, "src/c.py")
        # a+b は入るが a+b+c は入らない上限にする
        max_bytes = ba + bb + 10
        self.assertLess(max_bytes, ba + bb + bc)

        prompts_dir = self.repo_dir / "prompts"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        script = self.repo_dir / "auditor.py"
        cmd = self._make_auditor_script(
            script,
            "import pathlib\n"
            f"pdir = pathlib.Path({str(prompts_dir)!r})\n"
            "idx = len(list(pdir.glob('prompt_*.txt'))) + 1\n"
            "(pdir / f'prompt_{idx}.txt').write_text(prompt, encoding='utf-8')\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
            "'provider_request_id': f'msg_chunk_{idx}', 'usage': {'input_tokens': 100 * idx, 'output_tokens': 10 * idx}, 'findings': []}))",
        )

        config = AuditConfig(maker_model="gemini-3.8-flash", auditor_model="claude-opus-5.5", baseline_commit=self.baseline_sha)
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd,
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.baseline_sha,
            audit_max_diff_bytes=max_bytes,
        )
        findings = runner.audit_repository(repo_dir=self.repo_dir, target_commit=target_sha, baseline_commit=self.baseline_sha)
        self.assertEqual(findings, [])

        prompt_files = sorted(prompts_dir.glob("prompt_*.txt"))
        self.assertEqual(len(prompt_files), 2)

        p1 = prompt_files[0].read_text(encoding="utf-8")
        p2 = prompt_files[1].read_text(encoding="utf-8")

        # 両方に SPEC.md 全体が含まれる
        self.assertIn("SPEC_MARKER_FULL_CONTENT", p1)
        self.assertIn("SPEC_MARKER_FULL_CONTENT", p2)

        # 1組目は a.py と b.py の差分を持ち、c.py の差分本体は持たず、他組ファイル一覧に src/c.py を持つ
        self.assertIn("x" * 120, p1)
        self.assertIn("y" * 120, p1)
        self.assertNotIn("z" * 120, p1)
        self.assertIn("src/c.py", p1)
        self.assertIn("1", p1)
        self.assertIn("2", p1)

        # 2組目は c.py の差分を持ち、a.py/b.py の差分本体は持たず、他組ファイル一覧に src/a.py, src/b.py を持つ
        self.assertIn("z" * 120, p2)
        self.assertNotIn("x" * 120, p2)
        self.assertNotIn("y" * 120, p2)
        self.assertIn("src/a.py", p2)
        self.assertIn("src/b.py", p2)

        meta = runner.last_run_metadata or {}
        chunks = meta.get("chunks", [])
        self.assertEqual(len(chunks), 2)
        self.assertEqual(chunks[0]["files"], ["src/a.py", "src/b.py"])
        self.assertEqual(chunks[1]["files"], ["src/c.py"])
        self.assertEqual(meta.get("provider_request_id"), "msg_chunk_1")

    def test_single_file_exceeding_max_bytes_forms_own_chunk(self) -> None:
        """1つのファイルだけで上限を超える場合、ファイルの途中で分けずそのファイルだけで1組とし上限超過を示すこと。"""
        (self.repo_dir / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
        (self.repo_dir / "src" / "b.py").write_text("B = '" + ("b" * 500) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "c.py").write_text("C = 2\n", encoding="utf-8")
        target_sha = self._commit_all("oversized middle file")

        ba = self._file_diff_bytes(target_sha, "src/a.py")
        bb = self._file_diff_bytes(target_sha, "src/b.py")
        max_bytes = ba + 50
        self.assertGreater(bb, max_bytes)

        prompts_dir = self.repo_dir / "prompts_over"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        script = self.repo_dir / "auditor_over.py"
        cmd = self._make_auditor_script(
            script,
            "import pathlib\n"
            f"pdir = pathlib.Path({str(prompts_dir)!r})\n"
            "idx = len(list(pdir.glob('prompt_*.txt'))) + 1\n"
            "(pdir / f'prompt_{idx}.txt').write_text(prompt, encoding='utf-8')\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
            "'provider_request_id': f'msg_over_{idx}', 'usage': {'input_tokens': 50, 'output_tokens': 10}, 'findings': []}))",
        )

        config = AuditConfig(maker_model="gemini-3.8-flash", auditor_model="claude-opus-5.5", baseline_commit=self.baseline_sha)
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd,
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.baseline_sha,
            audit_max_diff_bytes=max_bytes,
        )
        runner.audit_repository(repo_dir=self.repo_dir, target_commit=target_sha, baseline_commit=self.baseline_sha)
        meta = runner.last_run_metadata or {}
        chunks = meta.get("chunks", [])
        self.assertEqual(len(chunks), 3)
        self.assertEqual(chunks[0]["files"], ["src/a.py"])
        self.assertFalse(chunks[0]["exceeds_max_diff_bytes"])
        self.assertEqual(chunks[1]["files"], ["src/b.py"])
        self.assertTrue(chunks[1]["exceeds_max_diff_bytes"])
        self.assertEqual(chunks[2]["files"], ["src/c.py"])
        self.assertFalse(chunks[2]["exceeds_max_diff_bytes"])

        p2 = (prompts_dir / "prompt_2.txt").read_text(encoding="utf-8")
        self.assertIn("b" * 500, p2)
        self.assertIn("exceed", p2.lower())

    def test_findings_from_multiple_chunks_are_aggregated_and_dispatched(self) -> None:
        """すべての組の指摘をまとめて振り分け(AUDIT-3)すること。"""
        (self.repo_dir / "src" / "a.py").write_text("A = '" + ("x" * 150) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "b.py").write_text("B = '" + ("y" * 150) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("two chunks with findings")

        ba = self._file_diff_bytes(target_sha, "src/a.py")
        script = self.repo_dir / "auditor_findings.py"
        cmd = self._make_auditor_script(
            script,
            "if 'src/a.py' in prompt and ('" + ("x" * 150) + "' in prompt):\n"
            "    finding = {'finding_id': 'AUD-C1', 'severity': 'P2', 'spec_reference': 'SPEC', 'location': 'src/a.py:1', 'description': 'p2 from c1', 'reproduction_test_code': 'import unittest\\nclass T(unittest.TestCase):\\n def test_x(self): self.fail()\\n', 'status': 'REPORTED'}\n"
            "    rid = 'msg_f_1'\n"
            "else:\n"
            "    finding = {'finding_id': 'AUD-C2', 'severity': 'P2', 'spec_reference': 'SPEC', 'location': 'src/b.py:1', 'description': 'p2 from c2', 'reproduction_test_code': 'import unittest\\nclass T(unittest.TestCase):\\n def test_y(self): self.fail()\\n', 'status': 'REPORTED'}\n"
            "    rid = 'msg_f_2'\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', 'provider_request_id': rid, 'usage': {'input_tokens': 100, 'output_tokens': 20}, 'findings': [finding]}))",
        )

        orch = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            enable_audit_stage=True,
            auditor_cmd_template=cmd,
            auditor_model="claude-opus-5.5",
            maker_model="gemini-3.8-flash",
            audit_max_diff_bytes=ba + 5,
        )
        res = orch.run_audit_stage(baseline_commit=self.baseline_sha, target_commit=target_sha)
        self.assertEqual(res["status"], "SUCCESS")
        self.assertEqual(len(res["backlogged"]), 2)
        backlog_text = (self.repo_dir / "BACKLOG.md").read_text(encoding="utf-8")
        self.assertIn("AUD-C1", backlog_text)
        self.assertIn("AUD-C2", backlog_text)

    def test_partial_chunk_truncation_returns_audit_pending_with_chunk_number(self) -> None:
        """1組でも途中切れ(max_tokens等)・形式不正・失敗なら全体をAUDIT_PENDINGとしどの組かを示すこと。"""
        (self.repo_dir / "src" / "a.py").write_text("A = '" + ("x" * 150) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "b.py").write_text("B = '" + ("y" * 150) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("two chunks second truncated")

        ba = self._file_diff_bytes(target_sha, "src/a.py")
        script = self.repo_dir / "auditor_trunc.py"
        cmd = self._make_auditor_script(
            script,
            "if '" + ("x" * 150) + "' in prompt:\n"
            "    print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', 'provider_request_id': 'msg_ok_1', 'usage': {'input_tokens': 80, 'output_tokens': 10}, 'findings': []}))\n"
            "else:\n"
            "    print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'max_tokens', 'provider_request_id': 'msg_trunc_2', 'usage': {'input_tokens': 90, 'output_tokens': 8192}, 'findings': []}))",
        )

        orch = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            enable_audit_stage=True,
            auditor_cmd_template=cmd,
            auditor_model="claude-opus-5.5",
            maker_model="gemini-3.8-flash",
            audit_max_diff_bytes=ba + 5,
        )
        res = orch.run_audit_stage(baseline_commit=self.baseline_sha, target_commit=target_sha)
        self.assertEqual(res["status"], "AUDIT_PENDING")
        self.assertIn("2", str(res.get("error", "")))

        audit_file = self.repo_dir / ".git" / "nightly_evidence" / "records" / "audit_stage.json"
        if not audit_file.exists():
            audit_file = self.repo_dir / ".dev" / "evidence" / "records" / "audit_stage.json"
        data = json.loads(audit_file.read_text(encoding="utf-8"))
        self.assertEqual(data["status"], "AUDIT_PENDING")
        self.assertIn("2", str(data.get("error", "")))

    def test_audit_stage_json_records_chunks_and_top_level_provider_request_id(self) -> None:
        """audit_stage.json に chunks[] と1組目の provider_request_id が残ること。"""
        (self.repo_dir / "src" / "a.py").write_text("A = '" + ("x" * 150) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "b.py").write_text("B = '" + ("y" * 150) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("two chunks success")

        ba = self._file_diff_bytes(target_sha, "src/a.py")
        script = self.repo_dir / "auditor_meta.py"
        cmd = self._make_auditor_script(
            script,
            "if '" + ("x" * 150) + "' in prompt:\n"
            "    print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', 'provider_request_id': 'msg_first_111', 'usage': {'input_tokens': 111, 'output_tokens': 21}, 'findings': []}))\n"
            "else:\n"
            "    print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', 'provider_request_id': 'msg_second_222', 'usage': {'input_tokens': 222, 'output_tokens': 32}, 'findings': []}))",
        )

        orch = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            enable_audit_stage=True,
            auditor_cmd_template=cmd,
            auditor_model="claude-opus-5.5",
            maker_model="gemini-3.8-flash",
            audit_max_diff_bytes=ba + 5,
        )
        res = orch.run_audit_stage(baseline_commit=self.baseline_sha, target_commit=target_sha)
        self.assertEqual(res["status"], "SUCCESS")

        audit_file = self.repo_dir / ".git" / "nightly_evidence" / "records" / "audit_stage.json"
        if not audit_file.exists():
            audit_file = self.repo_dir / ".dev" / "evidence" / "records" / "audit_stage.json"
        data = json.loads(audit_file.read_text(encoding="utf-8"))
        self.assertEqual(data.get("provider_request_id"), "msg_first_111")
        chunks = data.get("chunks")
        self.assertIsInstance(chunks, list)
        self.assertEqual(len(chunks), 2)

        diff_a = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", "src/a.py"],
            cwd=self.repo_dir, capture_output=True, text=True, check=True,
        ).stdout
        diff_b = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", "src/b.py"],
            cwd=self.repo_dir, capture_output=True, text=True, check=True,
        ).stdout

        self.assertEqual(chunks[0]["chunk_index"], 1)
        self.assertEqual(chunks[0]["files"], ["src/a.py"])
        self.assertEqual(chunks[0]["diff_sha256"], hashlib.sha256(diff_a.encode("utf-8")).hexdigest())
        self.assertEqual(chunks[0]["provider_request_id"], "msg_first_111")
        self.assertEqual(chunks[0]["stop_reason"], "end_turn")
        self.assertEqual(chunks[0]["input_tokens"], 111)
        self.assertEqual(chunks[0]["output_tokens"], 21)

        self.assertEqual(chunks[1]["chunk_index"], 2)
        self.assertEqual(chunks[1]["files"], ["src/b.py"])
        self.assertEqual(chunks[1]["diff_sha256"], hashlib.sha256(diff_b.encode("utf-8")).hexdigest())
        self.assertEqual(chunks[1]["provider_request_id"], "msg_second_222")
        self.assertEqual(chunks[1]["stop_reason"], "end_turn")
        self.assertEqual(chunks[1]["input_tokens"], 222)
        self.assertEqual(chunks[1]["output_tokens"], 32)

    def _prepare_bundle_repo(self, audit_stage_payload: dict) -> Path:
        (self.repo_dir / "src" / "a.py").write_text("A = 2\n", encoding="utf-8")
        target_sha = self._commit_all("task 1")
        evidence_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        tree_sha = subprocess.run(
            ["git", "rev-parse", f"{target_sha}^{{tree}}"],
            cwd=self.repo_dir, capture_output=True, text=True, check=True,
        ).stdout.strip()
        spec_hash = hashlib.sha256((self.repo_dir / "SPEC.md").read_bytes()).hexdigest()
        (evidence_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "status": "COMPLETED",
                "commit": target_sha,
                "tree_sha": tree_sha,
                "spec_sha": spec_hash,
                "checker": {"decision": "APPROVE"},
            }),
            encoding="utf-8",
        )
        ensure_synthetic_review_evidence(self.repo_dir, "1")
        audit_stage_payload.setdefault("audit_metadata", {})
        audit_stage_payload["audit_metadata"].setdefault("target_commit", target_sha)
        (evidence_dir / "audit_stage.json").write_text(json.dumps(audit_stage_payload), encoding="utf-8")
        bundler = EvidenceBundler(repo_dir=self.repo_dir)
        return bundler.create_bundle(job_id="chunk_bundle_test")

    def test_verify_bundle_checks_all_chunks_live_and_real_request_id(self) -> None:
        """verify-bundle は chunks[] 全組が LIVE かつ本物の provider_request_id のときだけ verified にすること。"""
        bundle_ok = self._prepare_bundle_repo({
            "status": "SUCCESS",
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_chunk_1_ok",
            "chunks": [
                {"chunk_index": 1, "mode": "LIVE", "provider_request_id": "msg_chunk_1_ok"},
                {"chunk_index": 2, "mode": "LIVE", "provider_request_id": "msg_chunk_2_ok"},
            ],
        })
        res_ok = EvidenceBundler(repo_dir=self.repo_dir).verify_bundle(bundle_ok)
        self.assertTrue(res_ok.is_valid)
        self.assertTrue(res_ok.audit_stage_verified)

        # 2組目が MOCK / mock_ の場合は audit_stage_verified=False
        bundle_bad = self._prepare_bundle_repo({
            "status": "SUCCESS",
            "mode": "LIVE",
            "provider": "anthropic",
            "provider_request_id": "msg_chunk_1_ok",
            "chunks": [
                {"chunk_index": 1, "mode": "LIVE", "provider_request_id": "msg_chunk_1_ok"},
                {"chunk_index": 2, "mode": "MOCK", "provider_request_id": "mock_chunk_2"},
            ],
        })
        res_bad = EvidenceBundler(repo_dir=self.repo_dir).verify_bundle(bundle_bad)
        self.assertTrue(res_bad.is_valid)
        self.assertFalse(res_bad.audit_stage_verified)

    def test_cli_audit_max_diff_bytes_option_and_validation(self) -> None:
        """CLI が --audit-max-diff-bytes を受け取り、0以下を拒否すること。"""
        with patch("auto_developer.cli.NightlyOrchestrator") as mock_orch:
            mock_orch.return_value.run.return_value = 0
            rc = cli_main([
                "--work-dir", str(self.repo_dir),
                "--audit-max-diff-bytes", "50000",
                "run", "--mock",
            ])
            self.assertEqual(rc, 0)
            self.assertEqual(mock_orch.call_args.kwargs.get("audit_max_diff_bytes"), 50000)

        rc_bad = cli_main([
            "--work-dir", str(self.repo_dir),
            "--audit-max-diff-bytes", "0",
            "run", "--mock",
        ])
        self.assertEqual(rc_bad, 2)


if __name__ == "__main__":
    unittest.main()
