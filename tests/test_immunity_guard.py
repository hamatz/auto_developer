from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.guard import GitGuard


class TestAsymmetricImmunityGuard(unittest.TestCase):
    """機能ZZ.1: 非対称免疫ガードの単体テスト (Red-First)。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # 初期コミット作成: 既存の免疫テストを含む
        self.immunity_dir = self.repo_dir / "tests" / "immunity"
        self.immunity_dir.mkdir(parents=True, exist_ok=True)
        self.existing_immunity = self.immunity_dir / "test_existing.py"
        self.existing_immunity.write_text("# existing immunity test\n", encoding="utf-8")

        self.spec_file = self.repo_dir / "SPEC.md"
        self.spec_file.write_text("# Spec\n", encoding="utf-8")

        self.app_file = self.repo_dir / "src" / "app.py"
        self.app_file.parent.mkdir(parents=True, exist_ok=True)
        self.app_file.write_text("print('hello')\n", encoding="utf-8")

        subprocess.run(["git", "add", "-A"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit with immunity test"], cwd=self.repo_dir, check=True)

        self.guard = GitGuard(repo_dir=self.repo_dir)
        self.checkpoint = self.guard.get_current_checkpoint()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_default_mode_rejects_immunity_additions(self) -> None:
        """既定 (allow_immunity_additions=False) では、新規免疫テスト追加も拒絶されること。"""
        new_test = self.immunity_dir / "test_new.py"
        new_test.write_text("# new immunity test\n", encoding="utf-8")

        # 未追跡 (untracked)
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint))
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=False))

        # ステージ済み (staged)
        subprocess.run(["git", "add", str(new_test)], cwd=self.repo_dir, check=True)
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint))
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=False))

    def test_asymmetric_mode_allows_new_immunity_file_addition(self) -> None:
        """allow_immunity_additions=True の場合、新規免疫テスト追加 (untracked / staged A) は許可されること。"""
        new_test = self.immunity_dir / "test_repro.py"
        new_test.write_text("# reproduced immunity test\n", encoding="utf-8")

        # 未追跡の新規追加
        self.assertTrue(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

        # ステージ済みの新規追加
        subprocess.run(["git", "add", str(new_test)], cwd=self.repo_dir, check=True)
        self.assertTrue(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

    def test_asymmetric_mode_strictly_rejects_modifying_existing_immunity_file(self) -> None:
        """allow_immunity_additions=True であっても、既存の免疫テストの変更は厳格に拒絶されること。"""
        self.existing_immunity.write_text("# tampered immunity test\n", encoding="utf-8")

        # 変更 (unstaged)
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

        # 変更 (staged)
        subprocess.run(["git", "add", str(self.existing_immunity)], cwd=self.repo_dir, check=True)
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

    def test_asymmetric_mode_strictly_rejects_deleting_existing_immunity_file(self) -> None:
        """allow_immunity_additions=True であっても、既存の免疫テストの削除は厳格に拒絶されること。"""
        self.existing_immunity.unlink()

        # 削除 (unstaged)
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

        # 削除 (staged)
        subprocess.run(["git", "add", "-A"], cwd=self.repo_dir, check=True)
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

    def test_asymmetric_mode_strictly_rejects_other_protected_files(self) -> None:
        """allow_immunity_additions=True であっても、SPEC.md や .github の変更は拒絶されること。"""
        self.spec_file.write_text("# modified spec\n", encoding="utf-8")
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

    def test_asymmetric_mode_rejects_non_test_files(self) -> None:
        """機能AAA.1: allow_immunity_additions=True であっても、非Pythonファイルや命名規則外の追加は拒絶されること。"""
        # not_a_test.txt (非Python / 非テスト)
        not_test = self.immunity_dir / "not_a_test.txt"
        not_test.write_text("random text\n", encoding="utf-8")
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Non-test file 'not_a_test.txt' must be rejected even when allow_immunity_additions=True",
        )
        not_test.unlink()

        # helper.py (test_ で始まらない Python ファイル)
        helper = self.immunity_dir / "helper.py"
        helper.write_text("x = 1\n", encoding="utf-8")
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Non-test python file 'helper.py' must be rejected even when allow_immunity_additions=True",
        )

    def test_asymmetric_mode_rejects_symlinks(self) -> None:
        """機能AAA.1: allow_immunity_additions=True であっても、シンボリックリンクの追加は拒絶されること。"""
        symlink_test = self.immunity_dir / "test_link.py"
        try:
            symlink_test.symlink_to("/etc/hosts")
        except OSError:
            symlink_test.symlink_to(self.spec_file)

        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Symlink inside tests/immunity/ must be strictly rejected!",
        )

    def test_asymmetric_mode_rejects_top_level_monkeypatching(self) -> None:
        """機能CCC.1: allow_immunity_additions=True であっても、トップレベルで関数やモジュールを動的に書き換えるモンキーパッチコードを含む新規免疫テストは AST 検査で拒絶されること。"""
        tampered_test = self.immunity_dir / "test_zz_patch.py"
        tampered_test.write_text(
            """import unittest
import app

# 悪意ある動的差し替え（モンキーパッチ）
app.add = lambda a, b: a + b

class DummyTest(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)
""",
            encoding="utf-8",
        )
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Top-level monkeypatching or executable statements in immunity test must be strictly rejected!",
        )

    def test_asymmetric_mode_rejects_ast_if_wrapper_monkeypatching(self) -> None:
        """機能CCC.1: if True などのラッパー文の中に隠蔽されたモンキーパッチも AST 検査で厳格に拒絶されること。"""
        tampered_test = self.immunity_dir / "test_zz_if_patch.py"
        tampered_test.write_text(
            """import unittest
import app

if True:
    app.add = lambda a, b: a + b

class DummyTest(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)
""",
            encoding="utf-8",
        )
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Monkeypatch hidden in if statement must be strictly rejected by AST validator!",
        )

    def test_rejects_main_guard_with_arbitrary_call(self) -> None:
        """機能CCC.1: if __name__ == '__main__': 内であっても unittest.main 以外の任意関数呼び出しは拒絶されること。"""
        arbitrary_call_test = self.immunity_dir / "test_main_tamper.py"
        arbitrary_call_test.write_text(
            """import unittest

def do_malicious():
    pass

class SafeTest(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)

if __name__ == '__main__':
    do_malicious()
""",
            encoding="utf-8",
        )
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Arbitrary function call inside __main__ guard must be rejected!",
        )

    def test_asymmetric_mode_rejects_main_guard_with_else_clause(self) -> None:
        """機能CCC.1: if __name__ == '__main__': ガードに else: 節が含まれている場合、import時の迂回実行を防ぐため拒絶されること。"""
        else_tamper_test = self.immunity_dir / "test_else_tamper.py"
        else_tamper_test.write_text(
            """import unittest
import app

class SafeTest(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)

if __name__ == '__main__':
    unittest.main()
else:
    app.tamper_target = "malicious_override"
""",
            encoding="utf-8",
        )
        self.assertFalse(
            self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True),
            "Main guard with else clause must be rejected by AST validator!",
        )

    def test_verify_mutual_non_interference_detects_interference(self) -> None:
        """機能CCC.1: 新規テストが既存テストの実行結果に副作用（成否改変）を及ぼす場合、verify_mutual_non_interference で False と判定されること。"""
        # 1. 既存の失敗テスト (バグ未修正で本来落ちるテスト)
        existing_test = self.immunity_dir / "test_existing_failing.py"
        existing_test.write_text(
            """import unittest
import app

class ExistingBugTest(unittest.TestCase):
    def test_app_behavior(self):
        # app.py は "print('hello')\n" なので add は存在しないか不正
        self.assertTrue(hasattr(app, "fixed_by_monkeypatch"), "Bug remains unfixed!")
""",
            encoding="utf-8",
        )
        # 2. 候補テストがインポート時に既存モジュールを書き換えて既存テストを通過させてしまうケース
        candidate_test = self.immunity_dir / "test_candidate_side_effect.py"
        candidate_test.write_text(
            """import unittest
import app
app.fixed_by_monkeypatch = True  # 副作用

class CandidateTest(unittest.TestCase):
    def test_dummy(self):
        self.assertEqual(1, 2)  # 自身は Red
""",
            encoding="utf-8",
        )
        self.assertFalse(
            self.guard.verify_mutual_non_interference("tests/immunity/test_candidate_side_effect.py"),
            "Interference that alters existing test results must be detected and rejected!",
        )

    def test_verify_mutual_non_interference_passes_for_clean_red_test(self) -> None:
        """機能CCC.1: 副作用のない純粋な Red テスト候補は、既存テストに干渉しないため True と判定されること。"""
        # 既存のテスト
        existing_test = self.immunity_dir / "test_existing_normal.py"
        existing_test.write_text(
            """import unittest
class NormalExistingTest(unittest.TestCase):
    def test_pass(self):
        self.assertTrue(True)
""",
            encoding="utf-8",
        )
        # 副作用のない純粋な再現テスト候補
        clean_candidate = self.immunity_dir / "test_clean_repro.py"
        clean_candidate.write_text(
            """import unittest
class CleanReproTest(unittest.TestCase):
    def test_pure_repro(self):
        self.assertEqual(10, 20)  # 純粋なアサーション失敗 (Red)
""",
            encoding="utf-8",
        )
        self.assertTrue(
            self.guard.verify_mutual_non_interference("tests/immunity/test_clean_repro.py"),
            "Clean candidate test without side effects must pass mutual non-interference check!",
        )


if __name__ == "__main__":
    unittest.main()
