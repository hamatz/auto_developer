"""GATE-22: Red-First の一時クローン実行で対象ファイルだけを実行するテスト (Red-First)."""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.verifier import verify_red_first_tests


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
    (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (repo / "PLAN.md").write_text("# PLAN\n", encoding="utf-8")
    (repo / "src" / "app").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "app" / "calc.py").write_text(
        "def get_val() -> int:\n    return 1\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: initial commit")
    return _git(repo, "rev-parse", "HEAD")


class TestGate22RedFirstSingleFile(unittest.TestCase):
    """GATE-22: 下位ディレクトリの同名ファイルを実行せず対象ファイルのみ実行する。"""

    def test_does_not_execute_same_named_file_in_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / "tests" / "test_a.py").write_text(
                "import unittest\n\n"
                "class TestA(unittest.TestCase):\n"
                "    def test_ok(self) -> None:\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            (repo / "tests" / "sub").mkdir(parents=True, exist_ok=True)
            (repo / "tests" / "sub" / "__init__.py").write_text("", encoding="utf-8")
            (repo / "tests" / "sub" / "test_a.py").write_text(
                "import unittest\n"
                "from app.calc import get_val\n\n"
                "class TestSubA(unittest.TestCase):\n"
                "    def test_sub(self) -> None:\n"
                "        self.assertEqual(get_val(), 2)\n",
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "chore: add tests")
            checkpoint = _git(repo, "rev-parse", "HEAD")

            # 製品コードと tests/sub/test_a.py の条件だけ通るようにし、
            # 対象の tests/test_a.py には中身のない変更だけを加える。
            (repo / "src" / "app" / "calc.py").write_text(
                "def get_val() -> int:\n    return 2\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_a.py").write_text(
                "# trivial comment change\n"
                "import unittest\n\n"
                "class TestA(unittest.TestCase):\n"
                "    def test_ok(self) -> None:\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )

            res = verify_red_first_tests(repo, checkpoint, ["tests/test_a.py"])
            self.assertEqual(res["result"], "NOT_RED")
            self.assertEqual(len(res["file_results"]), 1)
            self.assertEqual(res["file_results"][0]["reason"], "ALREADY_GREEN")
            self.assertEqual(res["file_results"][0]["current_result"], "SKIPPED")

    def test_executes_target_file_inside_package_without_running_subpackage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            pkg = repo / "tests" / "pkg"
            sub = pkg / "sub"
            sub.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "helper.py").write_text("EXPECTED = 42\n", encoding="utf-8")
            (pkg / "test_a.py").write_text(
                "import unittest\n"
                "from app.calc import get_val\n"
                "from .helper import EXPECTED\n\n"
                "class TestPkgA(unittest.TestCase):\n"
                "    def test_pkg(self) -> None:\n"
                "        self.assertEqual(get_val(), EXPECTED)\n",
                encoding="utf-8",
            )
            (sub / "__init__.py").write_text("", encoding="utf-8")
            (sub / "test_a.py").write_text(
                "import unittest\n\n"
                "class TestPkgSubA(unittest.TestCase):\n"
                "    def test_must_not_run(self) -> None:\n"
                "        self.fail('subpackage test_a.py must not be executed')\n",
                encoding="utf-8",
            )
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "chore: add package tests")
            checkpoint = _git(repo, "rev-parse", "HEAD")

            (repo / "src" / "app" / "calc.py").write_text(
                "def get_val() -> int:\n    return 42\n",
                encoding="utf-8",
            )

            res = verify_red_first_tests(repo, checkpoint, ["tests/pkg/test_a.py"])
            self.assertEqual(res["result"], "CONFIRMED")
            self.assertEqual(len(res["file_results"]), 1)
            self.assertEqual(res["file_results"][0]["reason"], "GENUINE_RED")
            self.assertEqual(res["file_results"][0]["current_result"], "PASSED")

    def test_import_error_in_package_target_file_reports_import_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)
            pkg = repo / "tests" / "pkg"
            pkg.mkdir(parents=True, exist_ok=True)
            (pkg / "__init__.py").write_text("", encoding="utf-8")
            (pkg / "test_imp.py").write_text(
                "import unittest\n"
                "from app.new_mod import fn\n\n"
                "class TestImp(unittest.TestCase):\n"
                "    def test_fn(self) -> None:\n"
                "        self.assertEqual(fn(), 1)\n",
                encoding="utf-8",
            )
            (repo / "src" / "app" / "new_mod.py").write_text(
                "def fn() -> int:\n    return 1\n",
                encoding="utf-8",
            )

            res = verify_red_first_tests(repo, checkpoint, ["tests/pkg/test_imp.py"])
            self.assertEqual(res["result"], "CONFIRMED")
            self.assertEqual(len(res["file_results"]), 1)
            self.assertEqual(res["file_results"][0]["reason"], "IMPORT_ERROR")
            self.assertEqual(res["file_results"][0]["current_result"], "PASSED")


if __name__ == "__main__":
    unittest.main()
