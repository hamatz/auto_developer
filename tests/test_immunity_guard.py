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


if __name__ == "__main__":
    unittest.main()
