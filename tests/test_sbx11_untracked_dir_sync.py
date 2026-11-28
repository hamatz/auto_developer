"""SBX-11: 一時クローンに新しいディレクトリ配下の未追跡ファイルもファイルごとに同期するテスト."""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.verifier import RedTestVerifier, verify_red_first_tests


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
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")

    (repo / ".gitignore").write_text("ignored_dir/\n.env\n", encoding="utf-8")
    (repo / "src" / "app").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "app" / "calc.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "test_existing.py").write_text(
        "import unittest\n"
        "from app.calc import add\n\n"
        "class TestExisting(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: initial commit")
    return _git(repo, "rev-parse", "HEAD")


class TestSbx11UntrackedDirSync(unittest.TestCase):
    """SBX-11: 新しいディレクトリ配下の未追跡ファイルが一時クローンに写されること。"""

    def test_red_first_confirms_with_untracked_module_in_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)

            # 新しいディレクトリ src/newpkg/ 配下に未追跡のモジュールを追加
            (repo / "src" / "newpkg").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "newpkg" / "__init__.py").write_text("", encoding="utf-8")
            (repo / "src" / "newpkg" / "mod.py").write_text(
                "def greet() -> str:\n    return 'hi'\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_newpkg.py").write_text(
                "import unittest\n"
                "from newpkg.mod import greet\n\n"
                "class TestNewPkg(unittest.TestCase):\n"
                "    def test_greet(self):\n"
                "        self.assertEqual(greet(), 'hi')\n",
                encoding="utf-8",
            )

            res = verify_red_first_tests(repo, checkpoint, ["tests/test_newpkg.py"])
            self.assertEqual(res["result"], "CONFIRMED", f"Expected CONFIRMED, got {res}")
            entry = res["file_results"][0]
            self.assertEqual(entry["result"], "CONFIRMED")
            self.assertEqual(entry["reason"], "IMPORT_ERROR")
            self.assertEqual(entry["current_result"], "PASSED")
            self.assertEqual(entry["current_reason"], "ALREADY_GREEN")

    def test_candidate_test_sees_untracked_file_in_new_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)

            (repo / "src" / "newpkg").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "newpkg" / "__init__.py").write_text("", encoding="utf-8")
            (repo / "src" / "newpkg" / "mod.py").write_text(
                "def greet() -> str:\n    return 'hi'\n",
                encoding="utf-8",
            )

            verifier = RedTestVerifier(repo_dir=repo)
            red_code = (
                "import unittest\n"
                "from newpkg.mod import greet\n\n"
                "class TestCandidate(unittest.TestCase):\n"
                "    def test_greet(self):\n"
                "        self.assertEqual(greet(), 'bye')\n"
            )
            res_red = verifier.verify_candidate_test(red_code)
            self.assertTrue(res_red.is_reproduced, f"Expected GENUINE_RED, got {res_red.reason}: {res_red.output}")
            self.assertEqual(res_red.reason, "GENUINE_RED")

            green_code = (
                "import unittest\n"
                "from newpkg.mod import greet\n\n"
                "class TestCandidateGreen(unittest.TestCase):\n"
                "    def test_greet(self):\n"
                "        self.assertEqual(greet(), 'hi')\n"
            )
            res_green = verifier.verify_candidate_test(green_code)
            self.assertFalse(res_green.is_reproduced)
            self.assertEqual(res_green.reason, "ALREADY_GREEN")

    def test_ignored_untracked_directory_is_not_copied(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)

            (repo / "src" / "ignored_dir").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "ignored_dir" / "__init__.py").write_text("", encoding="utf-8")
            (repo / "src" / "ignored_dir" / "secret.py").write_text("SECRET = 'x'\n", encoding="utf-8")

            verifier = RedTestVerifier(repo_dir=repo)
            test_code = (
                "import unittest\n"
                "from ignored_dir.secret import SECRET\n\n"
                "class TestIgnored(unittest.TestCase):\n"
                "    def test_secret(self):\n"
                "        self.assertEqual(SECRET, 'x')\n"
            )
            res = verifier.verify_candidate_test(test_code)
            self.assertEqual(res.reason, "IMPORT_ERROR")


if __name__ == "__main__":
    unittest.main()
