"""STAT-1: auto-dev status [--json] の単一チーム部分を検証する。"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main
from auto_developer.evidence import resolve_evidence_records_dir
from auto_developer.metrics import MetricsLog


class TestStat1StatusSingle(unittest.TestCase):
    """STAT-1: 単一チームでの auto-dev status --json / テキスト出力と attention[] を検証する。"""

    def _init_repo(self, repo: Path) -> str:
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (repo / "PLAN.md").write_text(
            "# Plan\n\n"
            "- [x] 1. Done without evidence\n"
            "- [ ] 2. **[FAILED]** Broken task\n"
            "- [ ] 3. Pending task\n\n"
            "## 止まっていること・判断待ち\n"
            "- Need human decision on API\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_status_json_reports_plan_events_discrepancies_quarantine_audit_and_attention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            head_sha = self._init_repo(repo)
            subprocess.run(["git", "branch", "quarantine/task-2-123"], cwd=repo, check=True, capture_output=True)

            metrics = MetricsLog(repo_dir=repo, plan_path=repo / "PLAN.md")
            metrics.record_event("verify_step", task_id="2", checkpoint=head_sha, result="SANDBOX_UNAVAILABLE", exit_code=1)
            metrics.record_event("checker_review", task_id="3", checkpoint=head_sha, decision="REJECT", model="claude-sonnet-5-5")

            records_dir = resolve_evidence_records_dir(repo)
            records_dir.mkdir(parents=True, exist_ok=True)
            (records_dir / "audit_stage.json").write_text(
                json.dumps({
                    "status": "SUCCESS",
                    "mode": "LIVE",
                    "target_commit": "0" * 40,
                    "findings": [{"severity": "P1", "title": "bug"}],
                }),
                encoding="utf-8",
            )

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            self.assertEqual(data["schema"], "auto-dev-status/1")
            self.assertEqual(data["repo"]["head"], head_sha)
            self.assertEqual(data["repo"]["branch"], "main")
            self.assertEqual(len(data["plan"]["tasks"]), 3)
            self.assertEqual(data["plan"]["blocked_items"], ["Need human decision on API"])
            self.assertIn("quarantine/task-2-123", data["quarantine_branches"])
            self.assertIsNotNone(data["audit"])

            kinds = {item["kind"] for item in data["attention"]}
            self.assertIn("PLAN_EVIDENCE_MISMATCH", kinds)
            self.assertIn("FAILED_TASK", kinds)
            self.assertIn("BLOCKED_ITEM", kinds)
            self.assertIn("QUARANTINE_BRANCH", kinds)
            self.assertIn("VERIFY_FAILURE", kinds)
            self.assertIn("CHECKER_REJECT", kinds)
            self.assertIn("AUDIT_OUTDATED", kinds)
            self.assertIn("AUDIT_P0_P1", kinds)

            # Human-readable output
            text_out = io.StringIO()
            with contextlib.redirect_stdout(text_out):
                rc2 = cli_main(["--work-dir", str(repo), "status"])
            self.assertEqual(rc2, 0)
            self.assertIn("Attention", text_out.getvalue())
            self.assertIn("quarantine/task-2-123", text_out.getvalue())


if __name__ == "__main__":
    unittest.main()
