from __future__ import annotations

import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from auto_developer.audit import IndependentAuditRunner
from auto_developer.cli import main as cli_main
from auto_developer.orchestrator import NightlyOrchestrator


def _init_repo_with_three_files(repo_dir: Path) -> tuple[str, str, int]:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# Spec\nMust work.\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Plan\n- [x] 1. Done\n", encoding="utf-8")
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
    (repo_dir / "src" / "b.py").write_text("B = 1\n", encoding="utf-8")
    (repo_dir / "src" / "c.py").write_text("C = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=repo_dir, check=True, capture_output=True)
    baseline_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    ).stdout.strip()

    (repo_dir / "src" / "a.py").write_text("A = '" + ("x" * 160) + "'\n", encoding="utf-8")
    (repo_dir / "src" / "b.py").write_text("B = '" + ("y" * 160) + "'\n", encoding="utf-8")
    (repo_dir / "src" / "c.py").write_text("C = '" + ("z" * 160) + "'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "modify three files"], cwd=repo_dir, check=True, capture_output=True)
    target_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    ).stdout.strip()

    diff_a = subprocess.run(
        ["git", "diff", baseline_sha, target_sha, "--", "src/a.py"],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    max_bytes = len(diff_a.encode("utf-8")) + 10
    return baseline_sha, target_sha, max_bytes


def _read_audit_stage_json(repo_dir: Path) -> dict[str, Any]:
    audit_file = repo_dir / ".git" / "nightly_evidence" / "records" / "audit_stage.json"
    if not audit_file.exists():
        audit_file = repo_dir / ".dev" / "evidence" / "records" / "audit_stage.json"
    loaded: dict[str, Any] = json.loads(audit_file.read_text(encoding="utf-8"))
    return loaded


class TestAudit9FailedAuditRecord(unittest.TestCase):
    """AUDIT-9: 失敗した監査の記録と報告の監査の深さ。"""

    def test_first_of_three_chunks_failure_records_scope_and_reports_full_depth(self) -> None:
        """3組のうち1組目で失敗したとき、audit_stage.json に範囲・大きさ・全組数・実行/未実行が残り、報告に「3 組のうち 1 組目で失敗（2・3 組目は実行していない）」が出ること。"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as script_tmp:
            repo = Path(tmp)
            baseline_sha, _target_sha, max_bytes = _init_repo_with_three_files(repo)

            auditor_script = Path(script_tmp) / "fail_first.py"
            auditor_script.write_text(
                "import json\n"
                "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'max_tokens', "
                "'provider_request_id': 'msg_fail_1', 'usage': {'input_tokens': 100, 'output_tokens': 8192}, 'findings': []}))\n",
                encoding="utf-8",
            )
            runner = IndependentAuditRunner(
                auditor_cmd_template=f"{sys.executable} {auditor_script} {{prompt_file}}",
                model_name="claude-opus-5-5",
                maker_model="gemini-4-hydrogen-medium",
                audit_paths=["src/"],
                audit_max_diff_bytes=max_bytes,
            )
            orch = NightlyOrchestrator(
                repo_dir=repo,
                enable_audit_stage=True,
                auditor_runner=runner,
                audit_baseline_commit=baseline_sha,
                audit_paths=["src/"],
                audit_max_diff_bytes=max_bytes,
            )
            rc = orch.run()
            self.assertEqual(rc, 0)

            data = _read_audit_stage_json(repo)
            self.assertEqual(data["status"], "AUDIT_PENDING")
            self.assertEqual(data.get("audit_paths"), ["src/"])
            self.assertEqual(data.get("diff_files_count"), 3)
            self.assertGreater(int(data.get("diff_bytes") or 0), 0)
            self.assertEqual(data.get("total_chunks"), 3)
            self.assertEqual(data.get("failed_chunk_index"), 1)
            self.assertEqual(data.get("executed_chunks"), [1])
            self.assertEqual(data.get("not_executed_chunks"), [2, 3])
            chunks = data.get("chunks")
            assert isinstance(chunks, list)
            self.assertEqual(len(chunks), 3)
            self.assertEqual(chunks[0]["status"], "AUDIT_PENDING")
            self.assertEqual(chunks[1]["status"], "NOT_EXECUTED")
            self.assertEqual(chunks[2]["status"], "NOT_EXECUTED")

            report_text = (repo / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("`src/`", report_text)
            self.assertIn("3 ファイル", report_text)
            self.assertIn("3 組のうち 1 組目で失敗（2・3 組目は実行していない）", report_text)

            buf = io.StringIO()
            with patch("sys.stdout", buf):
                brief_rc = cli_main(["--work-dir", str(repo), "brief"])
            self.assertEqual(brief_rc, 0)
            brief_out = buf.getvalue()
            self.assertIn("src/", brief_out)
            self.assertIn("3 files", brief_out)
            self.assertIn("3 組のうち 1 組目で失敗（2・3 組目は実行していない）", brief_out)

    def test_second_of_three_chunks_failure_records_executed_and_unexecuted_chunks(self) -> None:
        """3組のうち2組目で失敗したとき、1組目は完了・2組目は失敗・3組目は未実行として記録・報告されること。"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as script_tmp:
            repo = Path(tmp)
            baseline_sha, _target_sha, max_bytes = _init_repo_with_three_files(repo)

            auditor_script = Path(script_tmp) / "fail_second.py"
            auditor_script.write_text(
                "import json, sys\n"
                "prompt = open(sys.argv[1], encoding='utf-8').read()\n"
                "if 'src/a.py' in prompt and '" + ("x" * 160) + "' in prompt:\n"
                "    print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
                "'provider_request_id': 'msg_ok_1', 'usage': {'input_tokens': 12000, 'output_tokens': 300, "
                "'output_tokens_details': {'thinking_tokens': 200}}, 'findings': []}))\n"
                "else:\n"
                "    print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'max_tokens', "
                "'provider_request_id': 'msg_fail_2', 'usage': {'input_tokens': 100, 'output_tokens': 8192}, 'findings': []}))\n",
                encoding="utf-8",
            )
            runner = IndependentAuditRunner(
                auditor_cmd_template=f"{sys.executable} {auditor_script} {{prompt_file}}",
                model_name="claude-opus-5-5",
                maker_model="gemini-4-hydrogen-medium",
                audit_paths=["src/"],
                audit_max_diff_bytes=max_bytes,
            )
            orch = NightlyOrchestrator(
                repo_dir=repo,
                enable_audit_stage=True,
                auditor_runner=runner,
                audit_baseline_commit=baseline_sha,
                audit_paths=["src/"],
                audit_max_diff_bytes=max_bytes,
            )
            rc = orch.run()
            self.assertEqual(rc, 0)

            data = _read_audit_stage_json(repo)
            self.assertEqual(data["status"], "AUDIT_PENDING")
            self.assertEqual(data.get("total_chunks"), 3)
            self.assertEqual(data.get("failed_chunk_index"), 2)
            self.assertEqual(data.get("executed_chunks"), [1, 2])
            self.assertEqual(data.get("not_executed_chunks"), [3])
            chunks = data.get("chunks", [])
            self.assertEqual(len(chunks), 3)
            self.assertEqual(chunks[0]["status"], "COMPLETED")
            self.assertEqual(chunks[1]["status"], "AUDIT_PENDING")
            self.assertEqual(chunks[2]["status"], "NOT_EXECUTED")

            report_text = (repo / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("3 組のうち 2 組目で失敗（3 組目は実行していない）", report_text)

    def test_single_chunk_failure_records_scope_size_and_chunk(self) -> None:
        """1組で収まる差分で失敗した場合も、範囲・ファイル数・バイト数・total_chunks=1 が残ること。"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as script_tmp:
            repo = Path(tmp)
            baseline_sha, _target_sha, _max_bytes = _init_repo_with_three_files(repo)

            auditor_script = Path(script_tmp) / "fail_single.py"
            auditor_script.write_text(
                "import json\n"
                "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'max_tokens', "
                "'provider_request_id': 'msg_fail_single', 'usage': {'input_tokens': 100, 'output_tokens': 8192}, 'findings': []}))\n",
                encoding="utf-8",
            )
            runner = IndependentAuditRunner(
                auditor_cmd_template=f"{sys.executable} {auditor_script} {{prompt_file}}",
                model_name="claude-opus-5-5",
                maker_model="gemini-4-hydrogen-medium",
                audit_paths=["src/"],
                audit_max_diff_bytes=200000,
            )
            orch = NightlyOrchestrator(
                repo_dir=repo,
                enable_audit_stage=True,
                auditor_runner=runner,
                audit_baseline_commit=baseline_sha,
                audit_paths=["src/"],
                audit_max_diff_bytes=200000,
            )
            rc = orch.run()
            self.assertEqual(rc, 0)

            data = _read_audit_stage_json(repo)
            self.assertEqual(data["status"], "AUDIT_PENDING")
            self.assertEqual(data.get("audit_paths"), ["src/"])
            self.assertEqual(data.get("diff_files_count"), 3)
            self.assertGreater(int(data.get("diff_bytes") or 0), 0)
            self.assertEqual(data.get("total_chunks"), 1)
            self.assertEqual(data.get("failed_chunk_index"), 1)
            self.assertEqual(data.get("executed_chunks"), [1])
            self.assertEqual(data.get("not_executed_chunks"), [])
            self.assertEqual(len(data.get("chunks", [])), 1)

            report_text = (repo / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("1 組のうち 1 組目で失敗", report_text)

    def test_chunk_mismatch_before_execution_records_all_unexecuted_chunks(self) -> None:
        """組の差分の一致検査で実行前に失敗したとき、全組が未実行として記録・報告されること。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            baseline_sha, _target_sha, max_bytes = _init_repo_with_three_files(repo)

            runner = IndependentAuditRunner(
                auditor_cmd_template="echo '[]'",
                model_name="claude-opus-5-5",
                maker_model="gemini-4-hydrogen-medium",
                audit_paths=["src/"],
                audit_max_diff_bytes=max_bytes,
            )
            orch = NightlyOrchestrator(
                repo_dir=repo,
                enable_audit_stage=True,
                auditor_runner=runner,
                audit_baseline_commit=baseline_sha,
                audit_paths=["src/"],
                audit_max_diff_bytes=max_bytes,
            )
            with patch("auto_developer.audit.verify_audit_chunks_match_full_diff", return_value=["src/a.py"]):
                rc = orch.run()
            self.assertEqual(rc, 0)

            data = _read_audit_stage_json(repo)
            self.assertEqual(data["status"], "AUDIT_PENDING")
            self.assertEqual(data.get("audit_paths"), ["src/"])
            self.assertEqual(data.get("diff_files_count"), 3)
            self.assertEqual(data.get("total_chunks"), 3)
            self.assertEqual(data.get("executed_chunks"), [])
            self.assertEqual(data.get("not_executed_chunks"), [1, 2, 3])
            chunks = data.get("chunks")
            assert isinstance(chunks, list)
            self.assertEqual(len(chunks), 3)
            self.assertTrue(all(c["status"] == "NOT_EXECUTED" for c in chunks))

            report_text = (repo / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("3 組のうち実行前に失敗（1・2・3 組目は実行していない）", report_text)


if __name__ == "__main__":
    unittest.main()
