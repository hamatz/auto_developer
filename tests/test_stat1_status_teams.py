"""STAT-1: auto-dev status [--json] のマルチチーム部分を検証する。"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main
from auto_developer.evidence import EvidenceStore

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "two-teams"


class TestStat1StatusTeams(unittest.TestCase):
    """STAT-1: teams.json がある場合の auto-dev status --json / テキスト出力を検証する。"""

    def test_status_json_includes_teams_worktree_evidence_and_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams"
            shutil.copytree(_EXAMPLE_DIR, trial)
            subprocess.run(["git", "init", "-b", "main"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=trial, check=True, capture_output=True)

            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)

            # Worktree に Evidence を1件作成して読めることを確かめる
            wt_core = trial / ".dev" / "worktrees" / "core"
            head_core = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=wt_core, check=True, capture_output=True, text=True
            ).stdout.strip()
            EvidenceStore(repo_dir=wt_core).issue_evidence(
                task_id="1",
                checkpoint=head_core,
                tree_sha="a" * 40,
                spec_sha="b" * 64,
                details={"verification": {"exit_code": 0}},
            )
            # Quarantine ブランチも1本作成 (quarantine/<job_id>/<team_id>/...)
            pre_out = io.StringIO()
            with contextlib.redirect_stdout(pre_out):
                cli_main(["--work-dir", str(trial), "status", "--json"])
            job_id = json.loads(pre_out.getvalue())["teams"]["job_id"]
            q_branch = f"quarantine/{job_id}/cli/task-1-999"
            subprocess.run(["git", "branch", q_branch], cwd=trial, check=True, capture_output=True)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(trial), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            teams_block = data.get("teams")
            self.assertIsNotNone(teams_block, "teams block must be populated when teams.json exists")
            assert teams_block is not None
            self.assertTrue(teams_block.get("job_id"))
            self.assertIn("integration", teams_block)
            self.assertFalse(teams_block["integration"]["integration_branch_exists"])
            self.assertFalse(teams_block["integration"]["rc_tag_exists"])

            by_id = {t["team_id"]: t for t in teams_block["teams"]}
            self.assertIn("core", by_id)
            self.assertIn("cli", by_id)
            self.assertEqual(len(by_id["core"]["evidence_records"]), 1)
            self.assertIn("no_changes", by_id["core"])
            self.assertIn(q_branch, by_id["cli"]["quarantine_branches"])

            # Text output includes Teams section
            text_out = io.StringIO()
            with contextlib.redirect_stdout(text_out):
                rc2 = cli_main(["--work-dir", str(trial), "status"])
            self.assertEqual(rc2, 0)
            self.assertIn("Teams", text_out.getvalue())
            self.assertIn("core", text_out.getvalue())


if __name__ == "__main__":
    unittest.main()
