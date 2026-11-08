from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from auto_developer.verifier import RedTestVerifier


class TestRedTestVerifier(unittest.TestCase):
    """機能ZZ.2: 再現テスト検証ハーネス (RedTestVerifier) の単体テスト (Red-First)。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        self.verifier = RedTestVerifier(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_accepts_genuine_red_assertion_failure(self) -> None:
        """アサーション失敗等による真正な Red テストを正しく True (GENUINE_RED) と判定すること。"""
        code = """import unittest

class BugReproTest(unittest.TestCase):
    def test_repro_failure(self):
        self.assertEqual(1 + 1, 3, "Intentional reproduction failure")
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertTrue(result.is_reproduced)
        self.assertEqual(result.reason, "GENUINE_RED")
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn("AssertionError", result.output)

    def test_rejects_already_green_test(self) -> None:
        """現行コードですでに Green (通過) してしまうテストは False (ALREADY_GREEN) と判定すること。"""
        code = """import unittest

class AlreadyPassedTest(unittest.TestCase):
    def test_passing(self):
        self.assertEqual(1 + 1, 2)
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "ALREADY_GREEN")
        self.assertEqual(result.exit_code, 0)

    def test_rejects_syntax_error(self) -> None:
        """構文エラーを含む無効なテストコードは False (SYNTAX_ERROR) と判定すること。"""
        code = """import unittest
class BrokenSyntaxTest(unittest.TestCase
    def test_broken(self):
        pass
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "SYNTAX_ERROR")
        self.assertIn("SyntaxError", result.reason_detail)

    def test_rejects_import_error(self) -> None:
        """存在しないモジュールをインポートする無効なテストは False (IMPORT_ERROR) と判定すること。"""
        code = """import unittest
import completely_non_existent_module_xyz_123

class BadImportTest(unittest.TestCase):
    def test_dummy(self):
        self.assertTrue(True)
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "IMPORT_ERROR")
        self.assertIn("ModuleNotFoundError", result.output)

    def test_rejects_empty_or_no_test_methods(self) -> None:
        """テストメソッドが存在しないコードは False (NO_TESTS_FOUND) と判定すること。"""
        code = """# Just comments, no tests
x = 10
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "NO_TESTS_FOUND")

    def test_rejects_class_without_test_methods_ran_zero(self) -> None:
        """機能AAA.2: TestCase を継承していてもテストメソッドが無く実行数0件の場合は False (NO_TESTS_RAN) と判定すること。"""
        code = """import unittest
class TestX(unittest.TestCase):
    pass
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "NO_TESTS_RAN")

    def test_rejects_setup_environment_oserror(self) -> None:
        """機能AAA.2: setUp() での OSError 等の環境例外による失敗は False (SETUP_ERROR) と判定すること。"""
        code = """import unittest
class TestEnvError(unittest.TestCase):
    def setUp(self):
        raise OSError("Harness environment failure: device or file not available")
    def test_repro(self):
        self.assertEqual(1, 2)
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "SETUP_ERROR")

    def test_rejects_test_body_unhandled_oserror_without_assertion(self) -> None:
        """機能AAA.2: テストメソッド内でアサーション失敗ではなく OSError 等の例外が発生した場合は False (UNHANDLED_EXCEPTION) と判定すること。"""
        code = """import unittest
class TestBodyError(unittest.TestCase):
    def test_unhandled(self):
        raise OSError("Unexpected disk failure during test execution")
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "UNHANDLED_EXCEPTION")

    def test_verify_candidate_from_file_path(self) -> None:
        """ファイルパス指定からの検証も正しく動作すること。"""
        test_file = self.repo_dir / "test_file_repro.py"
        test_file.write_text(
            """import unittest
class FileReproTest(unittest.TestCase):
    def test_file_failure(self):
        self.fail("Explicit repro failure")
""",
            encoding="utf-8",
        )
        result = self.verifier.verify_candidate_test_file(test_file)
        self.assertTrue(result.is_reproduced)
        self.assertEqual(result.reason, "GENUINE_RED")


if __name__ == "__main__":
    unittest.main()
