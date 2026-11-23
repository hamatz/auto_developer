"""DX-3: examples/two-teams/ を一時ディレクトリに写して teams prepare まで通ることを検証する。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "two-teams"


class TestDX3TwoTeamsExample(unittest.TestCase):
    """DX-3: examples/two-teams/ が存在し、コピー先で verify_local.sh と teams prepare が通ること。"""

    def test_two_teams_example_prepares_cleanly(self) -> None:
        self.assertTrue(_EXAMPLE_DIR.is_dir(), "examples/two-teams must exist")
        self.assertTrue((_EXAMPLE_DIR / "teams.json").is_file(), "teams.json must exist")
        self.assertTrue((_EXAMPLE_DIR / "SPEC.md").is_file(), "SPEC.md must exist")
        self.assertTrue((_EXAMPLE_DIR / "plans").is_dir(), "plans/ must exist")
        self.assertTrue((_EXAMPLE_DIR / "scripts" / "verify_local.sh").is_file(), "scripts/verify_local.sh must exist")

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams-trial"
            shutil.copytree(_EXAMPLE_DIR, trial)
            subprocess.run(["git", "init", "-b", "main"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=trial, check=True, capture_output=True)

            # verify_local.sh passes
            vproc = subprocess.run(
                ["bash", str(trial / "scripts" / "verify_local.sh")],
                cwd=trial,
                env={**__import__("os").environ, "PYTHON": sys.executable},
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(vproc.returncode, 0, f"verify_local.sh failed:\n{vproc.stdout}\n{vproc.stderr}")

            # teams prepare & teams status
            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)
            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "status"]), 0)


if __name__ == "__main__":
    unittest.main()
