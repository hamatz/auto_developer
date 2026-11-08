from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.guard import GitGuard
from auto_developer.verifier import RedTestVerifier


class TestImmunityAndRedAcceptance(unittest.TestCase):
    """機能ZZ.3: 非対称免疫保護と再現テスト検証ハーネスの統合受入テスト。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # 初期コミット作成: 既存の免疫テストおよびアプリケーションコードを含む
        self.immunity_dir = self.repo_dir / "tests" / "immunity"
        self.immunity_dir.mkdir(parents=True, exist_ok=True)
        self.existing_immunity = self.immunity_dir / "test_baseline_contract.py"
        self.existing_immunity.write_text(
            """import unittest
class BaselineImmunityTest(unittest.TestCase):
    def test_baseline_always_true(self):
        self.assertTrue(True)
""",
            encoding="utf-8",
        )

        self.src_dir = self.repo_dir / "src"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.calc_file = self.src_dir / "calculator.py"
        self.calc_file.write_text(
            """def divide(a: int, b: int) -> float:
    # 不具合: ゼロ除算時の独自例外ではなく標準 ZeroDivisionError が送出される、あるいは負数の扱いに欠陥
    return a / b
""",
            encoding="utf-8",
        )

        self.spec_file = self.repo_dir / "SPEC.md"
        self.spec_file.write_text("# SPEC\n", encoding="utf-8")

        subprocess.run(["git", "add", "-A"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial baseline commit"], cwd=self.repo_dir, check=True)

        self.guard = GitGuard(repo_dir=self.repo_dir)
        self.checkpoint = self.guard.get_current_checkpoint()
        self.verifier = RedTestVerifier(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_end_to_end_audit_reproduction_and_immunity_addition(self) -> None:
        """受入シナリオ1: 監査役の指摘から生成された再現テストを検証し、真正なRedのみを免疫テストとして新規登録できること。"""
        # 監査役が提示した再現テストコード (未修正コードに対して確実に Red になるアサーション)
        candidate_bug_test = """import unittest
from calculator import divide

class DivideBugReproTest(unittest.TestCase):
    def test_divide_zero_expected_behavior(self):
        # 期待仕様: ゼロ除算時は -1.0 を返す
        try:
            res = divide(10, 0)
        except ZeroDivisionError:
            res = None
        self.assertEqual(res, -1.0, "Bug reproduced: currently raises ZeroDivisionError instead of -1.0")
"""
        # 1. RedTestVerifier による客観的再現判定
        red_result = self.verifier.verify_candidate_test(candidate_bug_test)
        self.assertTrue(red_result.is_reproduced)
        self.assertEqual(red_result.reason, "GENUINE_RED")
        self.assertNotEqual(red_result.exit_code, 0)

        # 2. 真正な Red であることが確認されたため、監査役フェーズで免疫テストへ新規追加
        new_immunity_file = self.immunity_dir / "test_divide_zero_immunity.py"
        new_immunity_file.write_text(candidate_bug_test, encoding="utf-8")

        # 3. GitGuard による不変条件検査:
        # allow_immunity_additions=False (通常Maker) の場合は厳格に遮断されること
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=False))

        # allow_immunity_additions=True (監査役フェーズ) の場合は新規追加が安全に許可されること
        self.assertTrue(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

    def test_end_to_end_malicious_tampering_of_immunity_blocked(self) -> None:
        """受入シナリオ2: 監査役フェーズであっても、既存の免疫テスト改変・削除や保護ファイル改変は絶対遮断されること。"""
        # 既存免疫テストの改変
        self.existing_immunity.write_text("# tampered\n", encoding="utf-8")
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Modifying existing immunity tests must be strictly blocked!",
        )

        # ロールバック
        subprocess.run(["git", "checkout", "HEAD", "--", str(self.existing_immunity)], cwd=self.repo_dir, check=True)

        # 既存免疫テストの削除
        self.existing_immunity.unlink()
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Deleting existing immunity tests must be strictly blocked!",
        )

        # ロールバック
        subprocess.run(["git", "checkout", "HEAD", "--", str(self.existing_immunity)], cwd=self.repo_dir, check=True)

        # SPEC.md や .github などの保護ファイルの改変
        self.spec_file.write_text("# Malicious spec edit\n", encoding="utf-8")
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Modifying SPEC.md must be strictly blocked even in auditor mode!",
        )

    def test_end_to_end_invalid_repro_rejected_before_immunity_addition(self) -> None:
        """受入シナリオ3: 既に Green なテストや無効なテストは Red 判定で弾かれ、免疫テスト昇格が阻止されること。"""
        # すでに Green になるコード
        already_green_code = """import unittest
from calculator import divide

class AlreadyGreenTest(unittest.TestCase):
    def test_divide_normal(self):
        self.assertEqual(divide(10, 2), 5.0)
"""
        result_green = self.verifier.verify_candidate_test(already_green_code)
        self.assertFalse(result_green.is_reproduced)
        self.assertEqual(result_green.reason, "ALREADY_GREEN")

        # 構文エラーのコード
        syntax_err_code = """import unittest
class Broken(unittest.TestCase
"""
        result_syntax = self.verifier.verify_candidate_test(syntax_err_code)
        self.assertFalse(result_syntax.is_reproduced)
        self.assertEqual(result_syntax.reason, "SYNTAX_ERROR")

    def test_end_to_end_hostile_candidate_test_isolated(self) -> None:
        """受入シナリオ4 (AAA.3/P0): 敵対的候補テストが本体リポジトリを改ざんしようとしても、物理隔離され本体が保護されること。"""
        canary = self.repo_dir / "canary.txt"
        canary.write_text("PRISTINE_CANARY", encoding="utf-8")

        hostile_code = """import unittest
from pathlib import Path

class MaliciousCandidateTest(unittest.TestCase):
    def test_tamper_repo(self):
        p = Path("canary.txt")
        if p.exists():
            p.write_text("HACKED_CANARY", encoding="utf-8")
        self.assertEqual(1, 2, "Intentional failure after tampering")
"""
        res = self.verifier.verify_candidate_test(hostile_code)
        self.assertTrue(res.is_reproduced)
        # 本体の canary.txt は絶対に改変されていないこと
        self.assertEqual(
            canary.read_text(encoding="utf-8"),
            "PRISTINE_CANARY",
            "Parent repository canary.txt must remain pristine!",
        )

    def test_end_to_end_exception_spoofing_rejected_as_genuine_red(self) -> None:
        """受入シナリオ5 (BBB.1): 例外メッセージ偽装 (OSError('AssertionError: ...')) が GENUINE_RED として誤判定されず確実に弾かれること。"""
        spoofed_code = """import unittest

class FakeAssertionTest(unittest.TestCase):
    def test_fake(self):
        raise OSError("AssertionError: pretend failure message to spoof verifier")
"""
        res = self.verifier.verify_candidate_test(spoofed_code)
        self.assertFalse(
            res.is_reproduced,
            "Spoofed exception message must not fool RedTestVerifier into genuine red!",
        )
        self.assertEqual(res.reason, "UNHANDLED_EXCEPTION")

    def test_end_to_end_absolute_path_tamper_blocked_by_os_lockdown(self) -> None:
        """受入シナリオ6 (BBB.2): 候補テストが絶対パス指定で本体リポジトリを改変しようとしても OS 権限ロックダウンにより PermissionError で阻止され本体が無傷であること。"""
        canary = self.repo_dir / "canary_abs.txt"
        canary.write_text("PRISTINE_ABS_CANARY", encoding="utf-8")
        abs_canary_str = str(canary.resolve())

        hostile_code = f"""import unittest
from pathlib import Path

class AbsoluteTamperTest(unittest.TestCase):
    def test_tamper(self):
        Path(r"{abs_canary_str}").write_text("HACKED_VIA_ABSOLUTE_PATH", encoding="utf-8")
        self.assertEqual(1, 2)
"""
        res = self.verifier.verify_candidate_test(hostile_code)
        self.assertEqual(
            canary.read_text(encoding="utf-8"),
            "PRISTINE_ABS_CANARY",
            "Parent repository file must remain pristine even against absolute path tampering!",
        )
        self.assertFalse(res.is_reproduced)
        self.assertEqual(res.reason, "UNHANDLED_EXCEPTION")


if __name__ == "__main__":
    unittest.main()
