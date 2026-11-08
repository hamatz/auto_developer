from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

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
# No TestCase class or test_* functions
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

    def test_rejects_exception_spoofing_oserror_with_assertion_message(self) -> None:
        """機能BBB.1: 例外メッセージ内に 'AssertionError' を含ませた OSError 等の例外偽装は GENUINE_RED ではなく False (UNHANDLED_EXCEPTION) と判定すること。"""
        code = """import unittest
class TestFake(unittest.TestCase):
    def test_error(self):
        raise OSError("AssertionError: pretend failure")
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "UNHANDLED_EXCEPTION")

    def test_candidate_test_execution_cannot_modify_parent_repository(self) -> None:
        """機能AAA.3: 候補テストが親リポジトリ内のファイルを改変・破壊しようとしても隔離され、本体が汚染されないこと。"""
        canary_file = self.repo_dir / "canary.txt"
        canary_file.write_text("PRISTINE_ORIGINAL_CONTENT", encoding="utf-8")

        hostile_code = """import unittest
from pathlib import Path

class HostileTest(unittest.TestCase):
    def test_tamper_and_fail(self):
        canary = Path("canary.txt")
        if canary.exists():
            canary.write_text("MALICIOUSLY_MODIFIED", encoding="utf-8")
        self.assertEqual(1, 2)
"""
        self.verifier.verify_candidate_test(hostile_code)
        self.assertEqual(
            canary_file.read_text(encoding="utf-8"),
            "PRISTINE_ORIGINAL_CONTENT",
            "Parent repository's canary.txt must not be modified by candidate test execution!",
        )

    def test_candidate_test_cannot_modify_parent_repository_by_absolute_path(self) -> None:
        """機能BBB.2: 候補テストが本体リポジトリの絶対パスを指定してファイルを書き換えようとしても、OS権限レベルの読取専用ロックダウンにより PermissionError で阻止され本体が改変されないこと。"""
        canary_file = self.repo_dir / "canary.txt"
        canary_file.write_text("PRISTINE_ORIGINAL_CONTENT", encoding="utf-8")

        abs_canary_str = str(canary_file.resolve())
        hostile_code = f"""import unittest
from pathlib import Path

class AbsolutePathTamperTest(unittest.TestCase):
    def test_tamper_abs_path(self):
        target = Path(r"{abs_canary_str}")
        target.write_text("MALICIOUSLY_MODIFIED_VIA_ABSOLUTE_PATH", encoding="utf-8")
        self.assertEqual(1, 2)
"""
        result = self.verifier.verify_candidate_test(hostile_code)
        self.assertEqual(
            canary_file.read_text(encoding="utf-8"),
            "PRISTINE_ORIGINAL_CONTENT",
            "Parent repository's canary.txt must not be modified even via absolute path!",
        )
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "UNHANDLED_EXCEPTION")

    def test_candidate_test_cannot_create_or_delete_file_in_parent_repository(self) -> None:
        """機能BBB.2: 候補テストが本体リポジトリ内に新規ファイルを作成または既存ファイルを削除しようとしても PermissionError で物理遮断されること。"""
        canary_to_delete = self.repo_dir / "canary_delete.txt"
        canary_to_delete.write_text("KEEP_ME", encoding="utf-8")
        new_file_target = self.repo_dir / "unwanted_created.txt"

        abs_del = str(canary_to_delete.resolve())
        abs_create = str(new_file_target.resolve())

        hostile_code = f"""import os
import unittest
from pathlib import Path

class CreateDeleteTamperTest(unittest.TestCase):
    def test_tamper(self):
        try:
            os.unlink(r"{abs_del}")
        except Exception:
            pass
        try:
            Path(r"{abs_create}").write_text("SPURIOUS", encoding="utf-8")
        except Exception:
            pass
        self.assertEqual(1, 2)
"""
        result = self.verifier.verify_candidate_test(hostile_code)
        self.assertTrue(canary_to_delete.exists(), "canary_delete.txt must not be deleted!")
        self.assertFalse(new_file_target.exists(), "unwanted_created.txt must not be created!")
        self.assertTrue(result.is_reproduced)  # try-exceptで握りつぶした場合は assertEqual(1, 2) が failure となる
        self.assertEqual(result.reason, "GENUINE_RED")

    def test_candidate_test_does_not_inherit_sensitive_environment_secrets(self) -> None:
        """機能AAA.3: 候補テストに親プロセスのシークレットやAPIキーが露出・継承されないこと。"""
        import os
        os.environ["SECRET_TEST_TOKEN_XYZ"] = "super_secret_value_123"
        try:
            leaking_code = """import os
import unittest

class SecretInspectionTest(unittest.TestCase):
    def test_check_env(self):
        found = [k for k in os.environ if "SECRET_TEST_TOKEN" in k]
        self.assertEqual(found, [], f"Sensitive environment variables leaked: {found}")
"""
            result = self.verifier.verify_candidate_test(leaking_code)
            self.assertEqual(result.reason, "ALREADY_GREEN")
        finally:
            os.environ.pop("SECRET_TEST_TOKEN_XYZ", None)

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

    def test_rejects_root_execution_fail_closed(self) -> None:
        """機能CCC.1: root権限 (UID 0) 環境ではOSパーミッションロックダウンが無効化されるため、候補テストの実行を ROOT_EXECUTION_FORBIDDEN で拒絶すること。"""
        from unittest.mock import patch

        with patch("os.geteuid", return_value=0):
            code = """import unittest
class ReproTest(unittest.TestCase):
    def test_fail(self):
        self.assertEqual(1, 2)
"""
            result = self.verifier.verify_candidate_test(code)
            self.assertFalse(result.is_reproduced)
            self.assertEqual(result.reason, "ROOT_EXECUTION_FORBIDDEN")

    def test_rejects_top_level_executable_statements_ast(self) -> None:
        """機能CCC.1: 候補テストコードのトップレベルにモンキーパッチ等の実行文が含まれている場合、実行前に False (TOP_LEVEL_EXEC_FORBIDDEN) で拒絶されること。"""
        code = """import unittest
calc_value = 100
import sys

class ReproTest(unittest.TestCase):
    def test_fail(self):
        self.assertEqual(1, 2)
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "TOP_LEVEL_EXEC_FORBIDDEN")

    def test_rejects_main_guard_with_else_clause_ast(self) -> None:
        """機能CCC.1: 候補テストコードの if __name__ == '__main__': ガードに else: 節が含まれている場合、TOP_LEVEL_EXEC_FORBIDDEN で拒絶されること。"""
        code = """import unittest
class ReproTest(unittest.TestCase):
    def test_fail(self):
        self.assertEqual(1, 2)

if __name__ == '__main__':
    unittest.main()
else:
    malicious_override = True
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "TOP_LEVEL_EXEC_FORBIDDEN")

    def test_candidate_executes_in_isolated_clone_and_preserves_parent(self) -> None:
        """機能DDD.1: 候補テストは元リポジトリとは独立した一時クローン内で実行され、
        テストメソッド内で os.chmod を用いてファイルを書き換えても、
        元リポジトリの canary.txt は物理的に無傷のまま保護されること。
        """
        import subprocess

        # 1. 元リポジトリを初期化して canary.txt をコミット
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)
        canary = self.repo_dir / "canary.txt"
        canary.write_text("ORIGINAL_CANARY", encoding="utf-8")
        subprocess.run(["git", "add", "canary.txt"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init canary"], cwd=self.repo_dir, check=True)

        canary_abs_path = str(canary.resolve())

        # 2. 候補テスト: 相対パスおよび絶対パスで canary.txt の chmod 権限復元 & 書き換えを試みる
        code = f"""import os
import unittest
from pathlib import Path

class HostileChmodTest(unittest.TestCase):
    def test_tamper(self):
        # 実行時ディレクトリの canary を改変試行
        p_local = Path("canary.txt")
        if p_local.exists():
            os.chmod(p_local, 0o644)
            p_local.write_text("TAMPERED_LOCAL")
        # 元リポジトリの絶対パスへも chmod して改変試行
        p_abs = Path(r"{canary_abs_path}")
        if p_abs.exists():
            try:
                os.chmod(p_abs, 0o644)
                p_abs.write_text("TAMPERED_ABSOLUTE")
            except Exception:
                pass
        self.assertEqual(1, 2)
"""
        result = self.verifier.verify_candidate_test(code)

        # 3. アサーション失敗により Red と判定される
        self.assertTrue(result.is_reproduced)
        self.assertEqual(result.reason, "GENUINE_RED")

        # 4. 元リポジトリの canary.txt は絶対に改変されていないこと (物理的不変性)
        self.assertEqual(
            canary.read_text(encoding="utf-8"),
            "ORIGINAL_CANARY",
            "Parent repository's canary.txt must remain pristine!",
        )


if __name__ == "__main__":
    unittest.main()
