from __future__ import annotations

import io
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.audit import IndependentAuditRunner
from auto_developer.cli import main as cli_main
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter


def _init_git_repo(repo_dir: Path) -> str:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo_dir, check=True)
    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\n\n## 1. Rule\nMust work.\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text(
        "# PLAN.md\n\n## ステップ\n- [x] 1. First\n- [x] 2. Second\n- [x] 3. Third\n",
        encoding="utf-8",
    )
    (repo_dir / "src").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "app.py").write_text("def hello() -> str:\n    return 'v1'\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True
    ).stdout.strip()
    (repo_dir / "src" / "app.py").write_text("def hello() -> str:\n    return 'v2'\n", encoding="utf-8")
    subprocess.run(["git", "add", "src/app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "feat: update app"], cwd=repo_dir, check=True, capture_output=True)
    return base_sha


class TestRPT8AuditReport(unittest.TestCase):
    def test_audit_only_run_report_and_brief(self) -> None:
        """監査だけの実行で『0 / N 件のタスクが正常に完了』や『git log --oneline -n 0』を出さず、計画完了済みを表示すること。"""
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as script_tmp:
            repo = Path(tmp)
            baseline_sha = _init_git_repo(repo)
            auditor_script = Path(script_tmp) / "mock_auditor.py"
            auditor_script.write_text(
                "import json, sys\n"
                "print(json.dumps({"
                "'mode': 'LIVE', 'provider': 'anthropic', 'status': 'COMPLETED', "
                "'stop_reason': 'end_turn', 'provider_request_id': 'msg_rpt8_001', "
                "'usage': {'input_tokens': 10000, 'output_tokens': 300, "
                "'output_tokens_details': {'thinking_tokens': 250}}, "
                "'findings': []"
                "}))\n",
                encoding="utf-8",
            )
            runner = IndependentAuditRunner(
                auditor_cmd_template=f"python3 {auditor_script} {{prompt_file}}",
                model_name="claude-opus-5-5",
                maker_model="gemini-4-hydrogen-medium",
                audit_paths=["src/"],
            )
            orch = NightlyOrchestrator(
                repo_dir=repo,
                enable_audit_stage=True,
                auditor_runner=runner,
                audit_baseline_commit=baseline_sha,
            )
            rc = orch.run()
            self.assertEqual(rc, 0)

            report_text = (repo / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("監査だけの実行（計画のタスクは 3 件すべて完了済み）", report_text)
            self.assertNotIn("0 / 3 件のタスクが正常に完了", report_text)
            self.assertNotIn("すべての予定タスクがコミットされた", report_text)
            self.assertNotIn("すべての予定タスクが正常に検証され、本線にコミットされました", report_text)
            self.assertNotIn("git log --oneline -n 0", report_text)
            self.assertNotIn("git log -p -n 0", report_text)
            # 監査の深さ（範囲・組数・トークン数）も含まれること
            self.assertIn("src/", report_text)
            self.assertIn("10000", report_text)
            self.assertIn("300", report_text)
            self.assertIn("250", report_text)
            self.assertNotIn("監査役の考えた量が入力に比べて少ない（弱い保証）", report_text)

            # auto-dev brief でも同様に表示されること
            buf = io.StringIO()
            with patch("sys.stdout", buf):
                brief_rc = cli_main(["--work-dir", str(repo), "brief"])
            self.assertEqual(brief_rc, 0)
            brief_out = buf.getvalue()
            self.assertIn("監査だけの実行（計画のタスクは 3 件すべて完了済み）", brief_out)
            self.assertNotIn("0 / 3 件のタスクが正常に完了", brief_out)
            self.assertIn("src/", brief_out)
            self.assertIn("10000", brief_out)
            self.assertIn("300", brief_out)
            self.assertIn("250", brief_out)

    def test_audit_depth_multichunk_and_weak_assurance_warning(self) -> None:
        """組ごとの入力・出力（うち考えた部分）を示し、1万入力あたり100未満の組があれば弱い保証の注意を出す（判定は変えない）。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            reporter = MorningReporter(repo_dir=repo)
            report_path = repo / "NIGHTLY_REPORT.md"
            summary_data = {
                "status": "COMPLETED",
                "total_tasks": 2,
                "succeeded_count": 0,
                "quarantined_count": 0,
                "checkpoint": "abc1234",
                "completed_tasks": [],
                "audit_stage": {
                    "status": "SUCCESS",
                    "audit_paths": ["src/", "scripts/"],
                    "diff_files_count": 3,
                    "diff_bytes": 250000,
                    "empty_diff": False,
                    "total_chunks": 2,
                    "chunks": [
                        {
                            "chunk_index": 1,
                            "total_chunks": 2,
                            "files": ["src/a.py"],
                            "diff_bytes": 120000,
                            "input_tokens": 10000,
                            "output_tokens": 400,
                            "thinking_tokens": 100,
                            "usage": {
                                "input_tokens": 10000,
                                "output_tokens": 400,
                                "output_tokens_details": {"thinking_tokens": 100},
                            },
                        },
                        {
                            "chunk_index": 2,
                            "total_chunks": 2,
                            "files": ["src/b.py", "scripts/c.py"],
                            "diff_bytes": 130000,
                            "input_tokens": 20000,
                            "output_tokens": 250,
                            "thinking_tokens": 199,
                            "usage": {
                                "input_tokens": 20000,
                                "output_tokens": 250,
                                "output_tokens_details": {"thinking_tokens": 199},
                            },
                        },
                    ],
                    "dispatched": [],
                    "backlogged": [],
                    "escalated": [],
                    "duplicates": [],
                    "rejected": [],
                },
            }

            reporter.write_report(report_path, summary_data)
            report_text = report_path.read_text(encoding="utf-8")
            self.assertIn("`SUCCESS`", report_text)
            self.assertIn("`src/`, `scripts/`", report_text)
            self.assertIn("2 組", report_text)
            self.assertIn("10000", report_text)
            self.assertIn("400", report_text)
            self.assertIn("100", report_text)
            self.assertIn("20000", report_text)
            self.assertIn("250", report_text)
            self.assertIn("199", report_text)
            self.assertIn("監査役の考えた量が入力に比べて少ない（弱い保証）", report_text)

            brief_text = reporter.format_console_brief(summary_data)
            self.assertIn("SUCCESS", brief_text)
            self.assertIn("src/", brief_text)
            self.assertIn("20000", brief_text)
            self.assertIn("199", brief_text)
            self.assertIn("監査役の考えた量が入力に比べて少ない（弱い保証）", brief_text)

    def test_audit_depth_boundary_exactly_100_per_10k_no_warning(self) -> None:
        """入力 1 万トークンあたりちょうど 100 トークン考えた場合は弱い保証の注意を出さないこと。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            reporter = MorningReporter(repo_dir=repo)
            report_path = repo / "NIGHTLY_REPORT.md"
            summary_data = {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": "abc1234",
                "completed_tasks": [],
                "audit_stage": {
                    "status": "SUCCESS",
                    "audit_paths": ["src/"],
                    "diff_files_count": 1,
                    "diff_bytes": 5000,
                    "empty_diff": False,
                    "total_chunks": 1,
                    "chunks": [
                        {
                            "chunk_index": 1,
                            "total_chunks": 1,
                            "files": ["src/a.py"],
                            "diff_bytes": 5000,
                            "input_tokens": 10000,
                            "output_tokens": 150,
                            "thinking_tokens": 100,
                            "usage": {
                                "input_tokens": 10000,
                                "output_tokens": 150,
                                "output_tokens_details": {"thinking_tokens": 100},
                            },
                        }
                    ],
                    "dispatched": [],
                    "backlogged": [],
                    "escalated": [],
                    "duplicates": [],
                    "rejected": [],
                },
            }
            reporter.write_report(report_path, summary_data)
            report_text = report_path.read_text(encoding="utf-8")
            self.assertNotIn("監査役の考えた量が入力に比べて少ない（弱い保証）", report_text)
            brief_text = reporter.format_console_brief(summary_data)
            self.assertNotIn("監査役の考えた量が入力に比べて少ない（弱い保証）", brief_text)

    def test_audit_depth_legacy_single_metadata_without_chunks(self) -> None:
        """chunks[] がない従来形式の audit_metadata.usage からもトークン数と弱い保証の注意を出せること。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            reporter = MorningReporter(repo_dir=repo)
            report_path = repo / "NIGHTLY_REPORT.md"
            summary_data = {
                "status": "COMPLETED",
                "total_tasks": 3,
                "succeeded_count": 0,
                "quarantined_count": 0,
                "checkpoint": "53d55a9",
                "completed_tasks": [],
                "audit_stage": {
                    "status": "SUCCESS",
                    "audit_metadata": {
                        "mode": "LIVE",
                        "provider": "anthropic",
                        "auditor_model": "claude-opus-5-5",
                        "maker_model": "gemini-4-hydrogen-medium",
                        "execution_id": "msg_legacy_001",
                        "provider_request_id": "msg_legacy_001",
                        "usage": {
                            "input_tokens": 598693,
                            "output_tokens": 2183,
                            "output_tokens_details": {"thinking_tokens": 2174},
                        },
                    },
                    "dispatched": [],
                    "backlogged": [],
                    "escalated": [],
                    "duplicates": [],
                    "rejected": [],
                },
            }
            reporter.write_report(report_path, summary_data)
            report_text = report_path.read_text(encoding="utf-8")
            self.assertIn("598693", report_text)
            self.assertIn("2183", report_text)
            self.assertIn("2174", report_text)
            self.assertIn("監査役の考えた量が入力に比べて少ない（弱い保証）", report_text)
            self.assertIn("`SUCCESS`", report_text)


if __name__ == "__main__":
    unittest.main()
