"""REL-5: 追跡しているファイルに開発者のローカル絶対パスが含まれないことを検証する。"""

from __future__ import annotations

import re
import subprocess
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_LOCAL_PATH_RE = re.compile(r"/(?:Users|home)/([A-Za-z0-9_.-]+)")
_ALLOWED_EXAMPLE_USERS = frozenset({
    "runner",
    "user",
    "alice",
    "bob",
    "example",
    "dev",
    "ubuntu",
    "vscode",
    "codespace",
    "admin",
    "test",
    "name",
})


class TestREL5NoLocalPaths(unittest.TestCase):
    """REL-5: git ls-files で追跡中のファイルに開発者固有の /Users/... /home/... パスがないこと。"""

    def test_tracked_files_contain_no_developer_local_paths(self) -> None:
        proc = subprocess.run(
            ["git", "ls-files"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        violations: list[str] = []
        for rel_path in proc.stdout.splitlines():
            if not rel_path.strip():
                continue
            full_path = _REPO_ROOT / rel_path
            if not full_path.is_file():
                continue
            try:
                text = full_path.read_text(encoding="utf-8")
            except UnicodeDecodeError:
                continue
            for lineno, line in enumerate(text.splitlines(), start=1):
                for match in _LOCAL_PATH_RE.finditer(line):
                    username = match.group(1)
                    if set(username) == {"."} or username.lower() in _ALLOWED_EXAMPLE_USERS:
                        continue
                    violations.append(f"{rel_path}:{lineno}: {match.group(0)}")

        self.assertEqual(
            violations,
            [],
            "Found developer-specific local paths in tracked files:\n" + "\n".join(violations),
        )


if __name__ == "__main__":
    unittest.main()
