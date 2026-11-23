"""STAT-2: auto-dev dashboard [--out FILE] の HTML 生成を検証する。"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "two-teams"


class TestStat2Dashboard(unittest.TestCase):
    """STAT-2: 単一HTML出力、attention先頭、XSSエスケープ、オフライン完結、schema検証を確かめる。"""

    def _init_repo(self, repo: Path) -> None:
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (repo / "PLAN.md").write_text(
            "# Plan\n\n"
            "- [ ] 1. **[FAILED]** <script>alert('xss')</script> task\n"
            "- [ ] 2. Normal task\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    def test_dashboard_creates_self_contained_escaped_html_with_attention_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)

            rc = cli_main(["--work-dir", str(repo), "dashboard"])
            self.assertEqual(rc, 0)

            out_file = repo / ".dev" / "dashboard.html"
            self.assertTrue(out_file.is_file())
            html_text = out_file.read_text(encoding="utf-8")

            # Offline self-contained: no external http/https/script src/link
            self.assertNotIn("http://", html_text)
            self.assertNotIn("https://", html_text)
            self.assertNotIn("<script src", html_text.lower())

            # XSS escaped
            self.assertNotIn("<script>alert('xss')</script>", html_text)
            self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html_text)

            # Attention appears before Tasks section
            att_idx = html_text.find("Attention")
            tasks_idx = html_text.find("Tasks")
            self.assertNotEqual(att_idx, -1)
            self.assertNotEqual(tasks_idx, -1)
            self.assertLess(att_idx, tasks_idx)

    def test_dashboard_custom_out_and_schema_mismatch(self) -> None:
        from auto_developer.dashboard import render_dashboard_html

        bad_html = render_dashboard_html({"schema": "unexpected/999", "attention": [], "plan": {"tasks": []}})
        self.assertIn("unexpected/999", bad_html)
        self.assertIn("Schema", bad_html)

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams"
            shutil.copytree(_EXAMPLE_DIR, trial)
            subprocess.run(["git", "init", "-b", "main"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=trial, check=True, capture_output=True)
            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)

            custom_out = Path(tmp) / "custom_dash.html"
            self.assertEqual(cli_main(["--work-dir", str(trial), "dashboard", "--out", str(custom_out)]), 0)
            self.assertTrue(custom_out.is_file())
            team_html = custom_out.read_text(encoding="utf-8")
            self.assertIn("core", team_html)
            self.assertIn("cli", team_html)
            self.assertIn("main", team_html)
            self.assertIn("rc/", team_html)


if __name__ == "__main__":
    unittest.main()
