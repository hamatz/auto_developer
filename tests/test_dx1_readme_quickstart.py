"""DX-1: README.md / README.en.md の readme-test: run ブロック実行と日英一致を検証する。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RUN_BLOCK_RE = re.compile(
    r"<!--\s*readme-test:\s*run\s*-->\s*\n```[^\n]*\n(.*?)\n```",
    re.DOTALL,
)


def _extract_run_blocks(readme_path: Path) -> list[str]:
    text = readme_path.read_text(encoding="utf-8")
    blocks: list[str] = []
    for match in _RUN_BLOCK_RE.finditer(text):
        lines = [
            line
            for line in match.group(1).splitlines()
            if not line.lstrip().startswith("pip install")
        ]
        blocks.append("\n".join(lines).strip())
    return blocks


class TestDX1ReadmeQuickstart(unittest.TestCase):
    """DX-1: README.md と README.en.md の run ブロックが一致し、すべて正常終了すること。"""

    def test_readme_ja_and_en_run_blocks_match(self) -> None:
        ja_blocks = _extract_run_blocks(_REPO_ROOT / "README.md")
        en_blocks = _extract_run_blocks(_REPO_ROOT / "README.en.md")
        self.assertGreater(len(ja_blocks), 0, "Expected at least one readme-test: run block")
        self.assertEqual(
            len(ja_blocks),
            len(en_blocks),
            f"Run block count mismatch: README.md={len(ja_blocks)} vs README.en.md={len(en_blocks)}",
        )
        for idx, (ja_cmd, en_cmd) in enumerate(zip(ja_blocks, en_blocks, strict=True), start=1):
            self.assertEqual(ja_cmd, en_cmd, f"Command mismatch in readme-test: run block #{idx}")

    def test_readme_run_blocks_execute_cleanly(self) -> None:
        blocks = _extract_run_blocks(_REPO_ROOT / "README.md")
        self.assertGreater(len(blocks), 0)

        status_before = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout

        py_bin_dir = str(Path(sys.executable).resolve().parent)
        for idx, script in enumerate(blocks, start=1):
            with (
                self.subTest(block=idx),
                tempfile.TemporaryDirectory(prefix=f"readme_test_{idx}_") as tmp_home,
            ):
                    env = dict(os.environ)
                    env["HOME"] = tmp_home
                    env["PATH"] = f"{py_bin_dir}:{env.get('PATH', '')}"
                    env["PYTHON"] = sys.executable
                    env["GIT_AUTHOR_NAME"] = "ReadmeTest"
                    env["GIT_AUTHOR_EMAIL"] = "readme-test@example.com"
                    env["GIT_COMMITTER_NAME"] = "ReadmeTest"
                    env["GIT_COMMITTER_EMAIL"] = "readme-test@example.com"

                    proc = subprocess.run(
                        ["bash", "-euo", "pipefail", "-c", script],
                        cwd=_REPO_ROOT,
                        env=env,
                        check=False,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        proc.returncode,
                        0,
                        f"README run block #{idx} failed (exit {proc.returncode}):\n"
                        f"--- script ---\n{script}\n"
                        f"--- stdout ---\n{proc.stdout}\n"
                        f"--- stderr ---\n{proc.stderr}",
                    )

        status_after = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        self.assertEqual(status_after, status_before, "README run blocks must not dirty the repository")


if __name__ == "__main__":
    unittest.main()
