from __future__ import annotations

import contextlib
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

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
        try:
            p_abs = Path(r"{canary_abs_path}")
            if p_abs.exists():
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

    def test_commit_verification_fails_closed_on_invalid_commit(self) -> None:
        """機能III.1: 存在しないコミットを指定した場合、現行ツリーへフォールバックせず COMMIT_CHECKOUT_FAILED で拒絶すること。"""
        import subprocess

        # Git リポジトリ初期化
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)
        dummy_file = self.repo_dir / "app.py"
        dummy_file.write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, check=True)

        # 現行コードに対して通過する（ALREADY_GREEN になるはずの）テストコード
        green_test = """import unittest
class PassingTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(1, 1)
"""
        # 存在しないコミットを指定して検証を実行
        bad_commit = "0" * 40
        result = self.verifier.verify_candidate_test(green_test, commit=bad_commit)

        # 現行コードへフォールバックして ALREADY_GREEN (exit 0) になってはならない (Fail-Closed)
        self.assertFalse(result.is_reproduced)
        self.assertNotEqual(result.exit_code, 0)
        self.assertEqual(result.reason, "COMMIT_CHECKOUT_FAILED")
        self.assertIn("COMMIT_CHECKOUT_FAILED", result.output)

    def test_resource_limits_prevent_excessive_file_write(self) -> None:
        """機能III.3: 候補テストが巨大ファイル（10MB超）を作成しようとした場合、OSリソース制限（RLIMIT_FSIZE）により遮断されること。"""
        code = """import unittest
class MassiveFileTest(unittest.TestCase):
    def test_write_massive_file(self):
        # 15MB のファイル書き込みを試みる
        with open("massive_file.bin", "wb") as f:
            f.write(b"0" * (15 * 1024 * 1024))
        self.assertEqual(1, 2)
"""
        result = self.verifier.verify_candidate_test(code)
        # 巨大ファイル書き込み例外等により GENUINE_RED ではなく UNHANDLED_EXCEPTION / EXECUTION_ERROR (is_reproduced=False) となること
        self.assertFalse(result.is_reproduced)
        self.assertIn(result.reason, ("UNHANDLED_EXCEPTION", "EXECUTION_ERROR"))
        self.assertIn("File too large", result.output)

    def test_sanitizes_dangerous_and_secret_env_variables(self) -> None:
        """機能III.3: 動的ローダー・Python設定・シークレット等の危険環境変数が候補テスト実行時に遮断されること。"""
        import os
        code = """import os
import unittest
class EnvCheckTest(unittest.TestCase):
    def test_check_env(self):
        self.assertNotIn("DYLD_TEST_INJECTION", os.environ)
        self.assertNotIn("LD_TEST_PRELOAD", os.environ)
        self.assertNotIn("PYTHONSTARTUP", os.environ)
        self.assertNotIn("MY_SECRET_TOKEN", os.environ)
"""
        os.environ["DYLD_TEST_INJECTION"] = "1"
        os.environ["LD_TEST_PRELOAD"] = "1"
        os.environ["PYTHONSTARTUP"] = "/evil.py"
        os.environ["MY_SECRET_TOKEN"] = "secret123"
        try:
            result = self.verifier.verify_candidate_test(code)
            # ALREADY_GREEN (すべての危険環境変数が除外され、アサーション通過)
            self.assertEqual(result.reason, "ALREADY_GREEN")
            self.assertEqual(result.exit_code, 0)
        finally:
            os.environ.pop("DYLD_TEST_INJECTION", None)
            os.environ.pop("LD_TEST_PRELOAD", None)
            os.environ.pop("PYTHONSTARTUP", None)
            os.environ.pop("MY_SECRET_TOKEN", None)

    def test_sandbox_prevents_subprocess_tampering_of_parent_repo(self) -> None:
        """機能JJJ.1: 候補テストがサブプロセス経由で元リポジトリの canary を書き換えようとしても、OSサンドボックスにより物理遮断され ORIGINAL が保たれること。"""
        canary = self.repo_dir / "canary_jjj1.txt"
        canary.write_text("ORIGINAL", encoding="utf-8")
        canary_abs = str(canary.resolve())

        code = f"""import subprocess
import unittest

class HostileSubprocessTest(unittest.TestCase):
    def test_tamper(self):
        # サブプロセスを起動して chmod +w し、canary の書き換えを試みる (Sol氏 Phase 49 再現手順)
        subprocess.run(["sh", "-c", "chmod 777 '{canary_abs}' && echo TAMPERED > '{canary_abs}'"], capture_output=True)
        self.assertEqual(1, 2)
"""
        result = self.verifier.verify_candidate_test(code)
        # canary は絶対に改変されていないこと (物理的不変性)
        self.assertEqual(canary.read_text(encoding="utf-8"), "ORIGINAL")
        self.assertTrue(result.is_reproduced)

    def test_sandbox_blocks_network_access(self) -> None:
        """機能JJJ.1: 候補テストからの外部ネットワーク接続が OS サンドボックスにより拒絶されること。"""
        code = """import unittest
import urllib.request

class NetAccessTest(unittest.TestCase):
    def test_connect(self):
        urllib.request.urlopen("https://example.com", timeout=2)
"""
        result = self.verifier.verify_candidate_test(code)
        # ネットワーク接続失敗（URLError / OSError）により ALREADY_GREEN にならず失敗すること
        self.assertFalse(result.is_reproduced)
        self.assertIn("URLError", result.output)

    def test_fails_closed_when_sandbox_unavailable(self) -> None:
        """機能JJJ.1: 利用可能なOSサンドボックス機構がない場合、非サンドボックスへフォールバックせず SANDBOX_UNAVAILABLE で拒絶すること。"""
        from unittest.mock import patch
        code = """import unittest
class SimpleTest(unittest.TestCase):
    def test_repro(self):
        self.assertEqual(1, 2)
"""
        with patch.object(self.verifier, "_get_sandbox_backend", return_value=None):
            result = self.verifier.verify_candidate_test(code)
            self.assertFalse(result.is_reproduced)
            self.assertEqual(result.reason, "SANDBOX_UNAVAILABLE")
            self.assertNotEqual(result.exit_code, 0)

    def test_repo_tamper_detected_when_parent_repo_modified(self) -> None:
        """機能JJJ.2: テスト実行中に元リポジトリが改変された場合、アサーション成否に関わらず REPO_TAMPER_DETECTED で即座に拒絶されること。"""
        from unittest.mock import MagicMock, patch

        mock_proc = MagicMock()
        mock_proc.pid = 99999
        mock_proc.returncode = 1

        def hostile_communicate(timeout: int | None = None) -> tuple[str, str]:
            # テスト実行中にフラグ・パーミッションを強制復元して元リポジトリへファイルを不正作成 (改ざん注入)
            subprocess.run(["chflags", "nouchg", str(self.repo_dir)], check=False)
            os.chmod(self.repo_dir, 0o755)
            tamper_file = self.repo_dir / "injected_tamper.txt"
            tamper_file.write_text("TAMPERED_IN_FLIGHT", encoding="utf-8")
            # 正常な Red (AssertionError) の結果を偽装して返す
            return (
                '===AUTO_DEV_TEST_RESULT===\n{"tests_run": 1, "failures": [{"err": "hostile"}], "errors": []}',
                "",
            )

        mock_proc.communicate.side_effect = hostile_communicate

        orig_popen = subprocess.Popen

        def popen_router(*args: Any, **kwargs: Any) -> Any:
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and any("sandbox" in str(c) or "bwrap" in str(c) or "-c" in str(c) for c in cmd):
                return mock_proc
            return orig_popen(*args, **kwargs)

        code = """import unittest
class TamperingTest(unittest.TestCase):
    def test_repro(self):
        self.assertEqual(1, 2)
"""
        with patch("subprocess.Popen", side_effect=popen_router):
            result = self.verifier.verify_candidate_test(code)
            # アサーション失敗があっても GENUINE_RED にならず REPO_TAMPER_DETECTED で拒絶されること
            self.assertFalse(result.is_reproduced)
            self.assertEqual(result.reason, "REPO_TAMPER_DETECTED")
            self.assertNotEqual(result.exit_code, 0)
            self.assertIn("injected_tamper.txt", result.reason_detail)

    def test_repo_tamper_detected_when_untracked_content_tampered_in_flight(self) -> None:
        """機能KKK.2: テスト実行中に未追跡ファイルの内容のみが改変された場合でも、REPO_TAMPER_DETECTED で拒絶されること。"""
        from unittest.mock import MagicMock, patch

        # Git リポジトリ初期化
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)
        dummy_init = self.repo_dir / "init.txt"
        dummy_init.write_text("INIT", encoding="utf-8")
        subprocess.run(["git", "add", "init.txt"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, check=True)

        # 未追跡ファイルを事前に配置
        probe_untracked = self.repo_dir / "existing_untracked.txt"
        probe_untracked.write_text("PRISTINE_UNTRACKED", encoding="utf-8")

        mock_proc = MagicMock()
        mock_proc.pid = 99998
        mock_proc.returncode = 1

        def hostile_communicate(timeout: int | None = None) -> tuple[str, str]:
            # テスト実行中にフラグを戻して未追跡ファイルの内容のみを書き換える
            subprocess.run(["chflags", "-R", "nouchg", str(self.repo_dir)], check=False)
            os.chmod(self.repo_dir, 0o755)
            os.chmod(probe_untracked, 0o644)
            probe_untracked.write_text("TAMPERED_UNTRACKED_IN_FLIGHT", encoding="utf-8")
            return (
                '===AUTO_DEV_TEST_RESULT===\n{"tests_run": 1, "failures": [{"err": "hostile"}], "errors": []}',
                "",
            )

        mock_proc.communicate.side_effect = hostile_communicate
        orig_popen = subprocess.Popen

        def popen_router(*args: Any, **kwargs: Any) -> Any:
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and any("sandbox" in str(c) or "bwrap" in str(c) or "-c" in str(c) for c in cmd):
                return mock_proc
            return orig_popen(*args, **kwargs)

        code = """import unittest
class TamperingUntrackedContentTest(unittest.TestCase):
    def test_repro(self):
        self.assertEqual(1, 2)
"""
        with patch("subprocess.Popen", side_effect=popen_router):
            result = self.verifier.verify_candidate_test(code)
            self.assertFalse(result.is_reproduced)
            self.assertEqual(result.reason, "REPO_TAMPER_DETECTED")
            self.assertIn("existing_untracked.txt", result.reason_detail)

    def test_reaps_multiple_descendant_processes_on_completion(self) -> None:
        """機能JJJ.3: 候補テストが複数のバックグラウンドプロセスを起動しても、完了後にすべて一括回収されること。"""
        marker = "auto_dev_test_multi_zombie_marker_completion_1122"
        code = f"""import subprocess
import sys
import unittest

class MultiZombieProcessTest(unittest.TestCase):
    def test_spawn_multiple_and_exit(self):
        # 3つのバックグラウンドプロセスを起動
        for i in range(3):
            cmd = [sys.executable, "-c", "import time; # {marker}\\ntime.sleep(60)"]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.assertEqual(1, 1)
"""
        try:
            result = self.verifier.verify_candidate_test(code)
            self.assertEqual(result.reason, "ALREADY_GREEN")

            # 完了後、マーカーを持つプロセスが1つも残存していないこと
            time.sleep(0.2)
            p_check = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True, check=False)
            surviving = [p for p in p_check.stdout.strip().splitlines() if p and int(p) != os.getpid()]
            self.assertEqual(surviving, [], f"Zombie processes survived after completion: {surviving}")
        finally:
            p_clean = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True, check=False)
            for p in p_clean.stdout.strip().splitlines():
                if p and int(p) != os.getpid():
                    with contextlib.suppress(Exception):
                        os.kill(int(p), 9)

    def test_reaps_multiple_descendant_processes_on_timeout(self) -> None:
        """機能JJJ.3: 候補テストがタイムアウトした場合でも、起動した子孫プロセス全体が一括回収されること。"""
        marker = "auto_dev_test_multi_zombie_marker_timeout_3344"
        code = f"""import subprocess
import sys
import time
import unittest

class TimeoutZombieProcessTest(unittest.TestCase):
    def test_spawn_and_hang(self):
        # 3つのバックグラウンドプロセスを起動
        for i in range(3):
            cmd = [sys.executable, "-c", "import time; # {marker}\\ntime.sleep(60)"]
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # テスト自身が無限ループでハング
        while True:
            time.sleep(0.1)
"""
        try:
            result = self.verifier.verify_candidate_test(code, timeout_sec=2)
            self.assertEqual(result.reason, "TIMEOUT")

            # タイムアウト後、マーカーを持つプロセスが1つも残存していないこと
            time.sleep(0.2)
            p_check = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True, check=False)
            surviving = [p for p in p_check.stdout.strip().splitlines() if p and int(p) != os.getpid()]
            self.assertEqual(surviving, [], f"Zombie processes survived after timeout: {surviving}")
        finally:
            p_clean = subprocess.run(["pgrep", "-f", marker], capture_output=True, text=True, check=False)
            for p in p_clean.stdout.strip().splitlines():
                if p and int(p) != os.getpid():
                    with contextlib.suppress(Exception):
                        os.kill(int(p), 9)

    def test_nproc_limit_computation_and_status_recording(self) -> None:
        """機能JJJ.3: RLIMIT_NPROC の動的計算と RedVerificationResult への設定ステータス記録を検証。"""
        limits, status = self.verifier._compute_nproc_limit(margin=30)
        if os.name == "posix":
            self.assertIn("applied: limit=", status)
            self.assertIsNotNone(limits)
            assert limits is not None
            self.assertGreaterEqual(limits[0], 30)

        # 実際のテスト実行結果に process_limit_status が記録されること
        code = """import unittest
class SimpleTest(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(1, 1)
"""
        result = self.verifier.verify_candidate_test(code)
        if os.name == "posix":
            self.assertIn("applied: limit=", result.process_limit_status)
        else:
            self.assertEqual(result.process_limit_status, "unsupported")

    def test_sandbox_blocks_reading_host_home_secrets(self) -> None:
        """機能KKK.1: サンドボックス内で実行された候補テストがホストのホームディレクトリ内の機密ファイルを読み取れないこと。"""
        home = Path.home().resolve()
        probe_secret = home / f".test_secret_probe_{os.getpid()}.txt"
        secret_content = "SUPER_SECRET_HOST_TOKEN_ABC123"
        probe_secret.write_text(secret_content, encoding="utf-8")
        try:
            code = f"""import unittest
from pathlib import Path

class ReadSecretTest(unittest.TestCase):
    def test_leak_secret(self):
        target = Path("{probe_secret}")
        # 秘密ファイルを読み取って例外メッセージまたはアサーションで漏洩させようとする
        content = target.read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG_TOKEN")
"""
            result = self.verifier.verify_candidate_test(code)
            # サンドボックスにより読み取り例外 (PermissionError / FileNotFoundError) が発生し、
            # 真正な Red ではなく UNHANDLED_EXCEPTION になること
            self.assertFalse(result.is_reproduced)
            self.assertEqual(result.reason, "UNHANDLED_EXCEPTION")
            # 秘密内容が結果の出力に漏洩していないこと
            self.assertNotIn(secret_content, result.output)
        finally:
            if probe_secret.exists():
                probe_secret.unlink()

    def test_sandbox_blocks_reading_parent_repo_confidential_files(self) -> None:
        """機能KKK.1: サンドボックス内で実行された候補テストが元リポジトリの機密ファイル（.env 等）を読み取れないこと。"""
        parent_secret = self.repo_dir / ".env.confidential"
        secret_content = "CONFIDENTIAL_DB_PASSWORD_XYZ987"
        parent_secret.write_text(secret_content, encoding="utf-8")

        code = f"""import unittest
from pathlib import Path

class ReadParentRepoTest(unittest.TestCase):
    def test_leak_repo_secret(self):
        target = Path("{parent_secret.resolve()}")
        content = target.read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG_PASSWORD")
"""
        result = self.verifier.verify_candidate_test(code)
        # サンドボックスにより読み取り例外が発生し、UNHANDLED_EXCEPTION になること
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "UNHANDLED_EXCEPTION")
        # 秘密内容が結果の出力に漏洩していないこと
        self.assertNotIn(secret_content, result.output)

    def test_repo_tamper_detects_untracked_and_ignored_content_modifications(self) -> None:
        """機能KKK.2: 未追跡ファイルおよび無視ファイルの内容のみが変更された場合でも、
        スナップショット比較で確実に改ざんが検知されること (Sol氏 Phase 50 再現手順)。
        """
        # Git リポジトリ初期化
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # .gitignore をコミット
        gitignore = self.repo_dir / ".gitignore"
        gitignore.write_text("*.ignored\n", encoding="utf-8")
        subprocess.run(["git", "add", ".gitignore"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, check=True)

        # 1. 未追跡ファイルと無視ファイルを作成
        untracked = self.repo_dir / "probe_untracked.txt"
        untracked.write_text("ORIGINAL_UNTRACKED", encoding="utf-8")
        ignored = self.repo_dir / "probe.ignored"
        ignored.write_text("ORIGINAL_IGNORED", encoding="utf-8")

        before_snapshot = self.verifier._capture_repo_snapshot()

        # 2. ファイル名・パスはそのまま、内容のみ改ざん
        untracked.write_text("TAMPERED_UNTRACKED_CONTENT", encoding="utf-8")
        ignored.write_text("TAMPERED_IGNORED_CONTENT", encoding="utf-8")

        after_snapshot = self.verifier._capture_repo_snapshot()

        # 3. 比較により、両方のパスが検知されること
        diffs = self.verifier._compare_repo_snapshots(before_snapshot, after_snapshot)
        self.assertIn("probe_untracked.txt", diffs)
        self.assertIn("probe.ignored", diffs)

    def test_lockdown_active_before_subprocess_popen_spawn(self) -> None:
        """機能KKK.3: subprocess.Popen が呼び出された時点で、
        すでに元リポジトリが _lockdown_repo により書き込み禁止になっていること（起動順序レース解消）。
        """
        import stat
        from unittest.mock import patch

        is_locked_at_spawn = False
        orig_popen = subprocess.Popen

        def inspecting_popen(*args: Any, **kwargs: Any) -> Any:
            nonlocal is_locked_at_spawn
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and any("sandbox" in str(c) or "bwrap" in str(c) or "-c" in str(c) for c in cmd):
                st = os.stat(self.repo_dir)
                is_locked_at_spawn = not bool(st.st_mode & stat.S_IWUSR)
            return orig_popen(*args, **kwargs)

        code = """import unittest
class SimplePassTest(unittest.TestCase):
    def test_pass(self):
        self.assertEqual(1, 1)
"""
        with patch("subprocess.Popen", side_effect=inspecting_popen):
            result = self.verifier.verify_candidate_test(code)
            self.assertEqual(result.reason, "ALREADY_GREEN")
            self.assertTrue(
                is_locked_at_spawn,
                "Repository must already be locked down (read-only) at the moment Popen is invoked!",
            )

    def test_bwrap_command_excludes_entire_etc_and_minimizes_mounts(self) -> None:
        """機能LLL.1: Linux bwrap において /etc 全体のマウントが廃止され、
        最小限のファイルのみが個別にバインドされること（Sol氏 Phase 51 P1指摘）。
        """
        from unittest.mock import MagicMock, patch

        bwrap_cmd_captured = []
        orig_popen = subprocess.Popen

        def capturing_popen(*args: Any, **kwargs: Any) -> Any:
            nonlocal bwrap_cmd_captured
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and "bwrap" in str(cmd[0]):
                bwrap_cmd_captured = list(cmd)
                mock_p = MagicMock()
                mock_p.pid = 12345
                mock_p.returncode = 0
                mock_p.communicate.return_value = (
                    '===AUTO_DEV_TEST_RESULT===\n{"tests_run": 1, "failures": [], "errors": []}',
                    "",
                )
                return mock_p
            return orig_popen(*args, **kwargs)

        code = """import unittest
class PassTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(1, 1)
"""
        with patch.object(self.verifier, "_get_sandbox_backend", return_value="bwrap"), \
             patch("subprocess.Popen", side_effect=capturing_popen):
            result = self.verifier.verify_candidate_test(code)
            self.assertEqual(result.reason, "ALREADY_GREEN")

        # 1. bwrap コマンドが実行されたこと
        self.assertTrue(bwrap_cmd_captured, "bwrap command must be invoked")

        # 2. --ro-bind のペアを抽出
        ro_bind_targets = []
        for i, token in enumerate(bwrap_cmd_captured):
            if token == "--ro-bind" and i + 1 < len(bwrap_cmd_captured):
                ro_bind_targets.append(bwrap_cmd_captured[i + 1])

        # 3. /etc 全体のマウントが排除されていること
        self.assertNotIn("/etc", ro_bind_targets, "/etc entire directory must NOT be ro-bound into sandbox!")

        # 4. ホストホームディレクトリ全体がマウントされていないこと
        home_str = str(Path.home().resolve())
        self.assertNotIn(home_str, ro_bind_targets, "Host home directory must NOT be ro-bound!")

    def test_bwrap_virtualenv_in_home_mounts_only_prefix_not_home(self) -> None:
        """機能LLL.1: 仮想環境がホーム直下の場合、ホーム全体ではなく subfolder のみがマウントされること。"""
        from unittest.mock import MagicMock, patch

        bwrap_cmd_captured = []
        orig_popen = subprocess.Popen

        def capturing_popen(*args: Any, **kwargs: Any) -> Any:
            nonlocal bwrap_cmd_captured
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and "bwrap" in str(cmd[0]):
                bwrap_cmd_captured = list(cmd)
                mock_p = MagicMock()
                mock_p.pid = 12345
                mock_p.returncode = 0
                mock_p.communicate.return_value = (
                    '===AUTO_DEV_TEST_RESULT===\n{"tests_run": 1, "failures": [], "errors": []}',
                    "",
                )
                return mock_p
            return orig_popen(*args, **kwargs)

        code = """import unittest
class PassTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(1, 1)
"""
        home_path = str(Path.home().resolve())
        with patch.object(self.verifier, "_get_sandbox_backend", return_value="bwrap"), \
             patch("sys.prefix", home_path), \
             patch("sys.base_prefix", home_path), \
             patch("subprocess.Popen", side_effect=capturing_popen):
            result = self.verifier.verify_candidate_test(code)
            self.assertEqual(result.reason, "ALREADY_GREEN")

        ro_bind_targets = [
            bwrap_cmd_captured[i + 1]
            for i, token in enumerate(bwrap_cmd_captured)
            if token == "--ro-bind" and i + 1 < len(bwrap_cmd_captured)
        ]
        self.assertNotIn(home_path, ro_bind_targets, "Home path itself must not be ro-bound even if sys.prefix == home")


if __name__ == "__main__":
    unittest.main()
