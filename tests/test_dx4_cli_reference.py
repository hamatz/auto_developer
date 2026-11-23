"""DX-4: scripts/gen_cli_reference.py と docs/cli.md の一致を検証する。"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent


class TestDX4CliReference(unittest.TestCase):
    """DX-4: docs/cli.md が argparse 定義から生成された最新の内容と一致すること。"""

    def test_docs_cli_md_is_in_sync_with_gen_cli_reference(self) -> None:
        script = _REPO_ROOT / "scripts" / "gen_cli_reference.py"
        doc = _REPO_ROOT / "docs" / "cli.md"
        self.assertTrue(script.is_file(), "scripts/gen_cli_reference.py must exist")
        self.assertTrue(doc.is_file(), "docs/cli.md must exist")

        proc = subprocess.run(
            [sys.executable, str(script), "--check"],
            cwd=_REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"docs/cli.md is out of sync with argparse definition:\n{proc.stdout}\n{proc.stderr}",
        )


if __name__ == "__main__":
    unittest.main()
