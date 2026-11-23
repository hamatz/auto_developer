"""Tests for CORE-11: init / teams init の .gitignore 追記と重複防止."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.cli import main

EXPECTED_AUTO_DEV_IGNORES = (
    ".dev/",
    "logs/",
    "NIGHTLY_REPORT.md",
)

EXPECTED_PYTHON_IGNORES = (
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
)

ALL_EXPECTED_IGNORES = EXPECTED_AUTO_DEV_IGNORES + EXPECTED_PYTHON_IGNORES


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


class TestInitGitignore(unittest.TestCase):
    """CORE-11: init と teams init が .gitignore に生成物を重複なく追記すること."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_cli(self, *args: str) -> int:
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(["--work-dir", str(self.repo_dir), *args])

    def test_init_creates_gitignore_with_auto_dev_and_python_artifacts(self) -> None:
        _git(self.repo_dir, "init", "-b", "main")
        self.assertEqual(self._run_cli("init"), 0)

        gitignore = self.repo_dir / ".gitignore"
        self.assertTrue(gitignore.is_file())
        lines = [line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()]
        for entry in ALL_EXPECTED_IGNORES:
            self.assertEqual(lines.count(entry), 1, f"Expected '{entry}' once in .gitignore, got lines={lines}")

        # git check-ignore で実際の無視動作も確認
        for sample_path in (
            ".dev/job_state.json",
            "logs/nightly/run.log",
            "NIGHTLY_REPORT.md",
            "src/pkg/__pycache__/mod.cpython-312.pyc",
            "foo.pyc",
            "foo.pyo",
            "foo.pyd",
            ".pytest_cache/README.md",
            ".mypy_cache/3.12/state.json",
            ".ruff_cache/latest",
        ):
            chk = subprocess.run(
                ["git", "check-ignore", "-q", sample_path],
                cwd=self.repo_dir,
                check=False,
            )
            self.assertEqual(chk.returncode, 0, f"git check-ignore failed for {sample_path}")

    def test_teams_init_creates_gitignore_even_without_prior_init(self) -> None:
        _git(self.repo_dir, "init", "-b", "main")
        self.assertEqual(self._run_cli("teams", "init", "--teams", "core,ui"), 0)

        gitignore = self.repo_dir / ".gitignore"
        self.assertTrue(gitignore.is_file())
        lines = [line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()]
        for entry in ALL_EXPECTED_IGNORES:
            self.assertEqual(lines.count(entry), 1, f"Expected '{entry}' once in .gitignore, got lines={lines}")

    def test_init_preserves_existing_content_and_avoids_duplicates(self) -> None:
        gitignore = self.repo_dir / ".gitignore"
        existing = (
            "# User rules\n"
            "dist/\n"
            "  .dev/  \n"
            "__pycache__/\t\n"
            "logs/\n"
            "logs/\n"
            "# substring traps: NIGHTLY_REPORT.md.bak my_logs/ foo.dev/\n"
            "custom_no_trailing_newline"
        )
        gitignore.write_text(existing, encoding="utf-8")

        self.assertEqual(self._run_cli("init"), 0)

        updated = gitignore.read_text(encoding="utf-8")
        # 既存の内容がすべて保持されていること
        self.assertTrue(updated.startswith("# User rules\ndist/\n  .dev/  \n__pycache__/\t\n"))
        self.assertIn("# substring traps: NIGHTLY_REPORT.md.bak my_logs/ foo.dev/", updated)
        self.assertIn("custom_no_trailing_newline\n", updated)

        stripped_lines = [line.strip() for line in updated.splitlines()]
        # すでに存在していたエントリ（空白付き含む）は増やさない
        self.assertEqual(stripped_lines.count(".dev/"), 1)
        self.assertEqual(stripped_lines.count("__pycache__/"), 1)
        self.assertEqual(stripped_lines.count("logs/"), 2)  # 既存の重複はそのまま保持し追加しない
        # 不足していたエントリ（部分一致トラップ含む）は1件ずつ追加される
        for entry in (
            "NIGHTLY_REPORT.md",
            "*.py[cod]",
            ".pytest_cache/",
            ".mypy_cache/",
            ".ruff_cache/",
        ):
            self.assertEqual(
                stripped_lines.count(entry),
                1,
                f"Expected '{entry}' once in .gitignore, got lines={stripped_lines}",
            )

    def test_teams_init_preserves_existing_content_and_avoids_duplicates(self) -> None:
        gitignore = self.repo_dir / ".gitignore"
        existing = (
            "node_modules/\n"
            "  NIGHTLY_REPORT.md \n"
            "*.py[cod]\n"
            "# .pytest_cache/ comment only\n"
            "build"
        )
        gitignore.write_text(existing, encoding="utf-8")

        self.assertEqual(self._run_cli("teams", "init", "--teams", "core,ui"), 0)

        updated = gitignore.read_text(encoding="utf-8")
        self.assertTrue(updated.startswith("node_modules/\n  NIGHTLY_REPORT.md \n*.py[cod]\n"))
        self.assertIn("# .pytest_cache/ comment only\n", updated)
        self.assertIn("build\n", updated)

        stripped_lines = [line.strip() for line in updated.splitlines()]
        for entry in ALL_EXPECTED_IGNORES:
            self.assertEqual(
                stripped_lines.count(entry),
                1,
                f"Expected '{entry}' once in .gitignore, got lines={stripped_lines}",
            )

    def test_init_and_teams_init_are_idempotent_on_gitignore(self) -> None:
        self.assertEqual(self._run_cli("init"), 0)
        gitignore = self.repo_dir / ".gitignore"
        first = gitignore.read_text(encoding="utf-8")

        self.assertEqual(self._run_cli("init"), 0)
        self.assertEqual(gitignore.read_text(encoding="utf-8"), first)

        self.assertEqual(self._run_cli("teams", "init", "--teams", "core,ui"), 0)
        self.assertEqual(gitignore.read_text(encoding="utf-8"), first)

        self.assertEqual(self._run_cli("teams", "init", "--teams", "core,ui"), 0)
        self.assertEqual(gitignore.read_text(encoding="utf-8"), first)

    def test_teams_init_validation_failure_does_not_modify_gitignore(self) -> None:
        gitignore = self.repo_dir / ".gitignore"
        gitignore.write_text("keep_me/\n", encoding="utf-8")

        self.assertEqual(self._run_cli("teams", "init", "--teams", "InvalidUpper"), 2)
        self.assertEqual(gitignore.read_text(encoding="utf-8"), "keep_me/\n")

        (self.repo_dir / "teams.json").write_text(
            json.dumps({
                "version": 1,
                "project": "bad",
                "teams": [{"id": "core", "owns": ["src/core/**"], "plan": "../escape.md"}],
            }),
            encoding="utf-8",
        )
        self.assertEqual(self._run_cli("teams", "init"), 2)
        self.assertEqual(gitignore.read_text(encoding="utf-8"), "keep_me/\n")


if __name__ == "__main__":
    unittest.main()
