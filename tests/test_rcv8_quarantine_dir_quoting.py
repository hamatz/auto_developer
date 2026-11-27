"""RCV-8 (Phase 79 Step 4): mkdir -p and shell quoting in git cat-file -p guidance."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.guard import format_snapshot_only_files_lines

_FAKE_KEY_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
_FAKE_KEY_END = "-----END " + "PRIVATE KEY-----"

REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    src_dir = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else src_dir
    return env


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo: Path) -> str:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("# Test\n", encoding="utf-8")
    (repo / "PLAN.md").write_text("# PLAN.md\n\n## ステップ\n- [ ] 1. Sample task\n", encoding="utf-8")
    (repo / ".gitignore").write_text(".dev/\nlogs/\n__pycache__/\n*.pyc\n.DS_Store\n", encoding="utf-8")
    _git(repo, "add", "README.md", "PLAN.md", ".gitignore")
    _git(repo, "commit", "-m", "chore: initial")
    return _git(repo, "rev-parse", "HEAD")


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", *args],
        cwd=repo,
        env=_clean_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


class TestRcv8QuarantineDirQuoting(unittest.TestCase):
    def test_format_lines_includes_mkdir_p_only_for_nested_paths_and_quotes_safely(self) -> None:
        entries = [
            {"path": ".env", "sha": "a" * 40},
            {"path": "nested/sub/secret.py", "sha": "b" * 40},
            {"path": "dir with space/it's $100/key.pem", "sha": "c" * 40},
        ]
        lines = format_snapshot_only_files_lines(
            snapshot_path=None,
            snapshot_only_files=[],
            git_object_files=entries,
        )
        text = "\n".join(lines)

        # Top-level file has no mkdir -p
        self.assertIn(f"  - .env: git cat-file -p {'a' * 40} > .env", text)
        self.assertNotIn("mkdir -p . &&", text)

        # Nested directory includes mkdir -p
        self.assertIn(
            f"  - nested/sub/secret.py: mkdir -p nested/sub && git cat-file -p {'b' * 40} > nested/sub/secret.py",
            text,
        )

        # Special characters in directory and file name are shell-quoted
        special_dir = shlex.quote("dir with space/it's $100")
        special_path = shlex.quote("dir with space/it's $100/key.pem")
        self.assertIn(
            f"mkdir -p {special_dir} && git cat-file -p {'c' * 40} > {special_path}",
            text,
        )

    def test_rollback_step_guided_command_restores_nested_and_special_dir_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)

            nested_rel = "new_dir/sub_dir/secret_fixture.py"
            special_rel = "dir with space/it's $100/key_file.py"
            top_rel = "top_secret.env"

            nested_content = f"K = '{_FAKE_KEY_BEGIN}\\nABC\\n{_FAKE_KEY_END}'\n"
            special_content = f"S = '{_FAKE_KEY_BEGIN}\\nXYZ\\n{_FAKE_KEY_END}'\n"
            top_content = "API_TOKEN=top_secret_123\n"

            for rel, content in [
                (nested_rel, nested_content),
                (special_rel, special_content),
                (top_rel, top_content),
            ]:
                target = repo / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

            proc = _run_cli(
                repo,
                "rollback-step",
                "--checkpoint",
                checkpoint,
                "--task-id",
                "1",
                "--reason",
                "RCV-8 test",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

            # Directories and files were removed by git clean -fd
            self.assertFalse((repo / "new_dir").exists())
            self.assertFalse((repo / "dir with space").exists())
            self.assertFalse((repo / top_rel).exists())

            log_text = (repo / "logs" / "nightly" / "task_1_failure.log").read_text(encoding="utf-8")

            # Extract guided command for each path from stdout and execute it in sh -c
            prefix_map: dict[str, str] = {}
            for line in proc.stdout.splitlines():
                stripped = line.strip()
                if stripped.startswith("- ") and ": " in stripped:
                    path_part, cmd_part = stripped[2:].split(": ", 1)
                    if path_part in {nested_rel, special_rel, top_rel}:
                        prefix_map[path_part] = cmd_part

            self.assertEqual(set(prefix_map.keys()), {nested_rel, special_rel, top_rel}, proc.stdout)
            self.assertTrue(prefix_map[nested_rel].startswith("mkdir -p "), prefix_map[nested_rel])
            self.assertTrue(prefix_map[special_rel].startswith("mkdir -p "), prefix_map[special_rel])
            self.assertTrue(prefix_map[top_rel].startswith("git cat-file -p "), prefix_map[top_rel])

            for rel, expected_content in [
                (nested_rel, nested_content),
                (special_rel, special_content),
                (top_rel, top_content),
            ]:
                cmd = prefix_map[rel]
                self.assertIn(cmd, log_text)
                restore = subprocess.run(
                    ["sh", "-c", cmd],
                    cwd=repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(restore.returncode, 0, f"{cmd}: {restore.stderr}")
                self.assertEqual((repo / rel).read_text(encoding="utf-8"), expected_content)


if __name__ == "__main__":
    unittest.main()
