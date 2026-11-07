from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.boundary import BoundaryCheckResult, BoundaryGuard, BoundaryViolationError


class TestBoundaryGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # テスト用Gitリポジトリ初期化
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # 初期構成
        (self.repo_dir / "src" / "core").mkdir(parents=True)
        (self.repo_dir / "src" / "ui").mkdir(parents=True)
        (self.repo_dir / "src" / "core" / "app.py").write_text("# Core App\n", encoding="utf-8")
        (self.repo_dir / "src" / "ui" / "view.py").write_text("# UI View\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_dir, check=True)

        # ベースコミットSHA取得
        res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.base_commit = res.stdout.strip()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_within_boundary_allowed(self) -> None:
        guard = BoundaryGuard(
            repo_dir=self.repo_dir,
            owns_patterns=["src/ui/**"],
            base_checkpoint=self.base_commit,
        )

        # UIスコープ内のみの編集
        (self.repo_dir / "src" / "ui" / "view.py").write_text("# Updated UI\n", encoding="utf-8")
        (self.repo_dir / "src" / "ui" / "button.py").write_text("# New Button\n", encoding="utf-8")

        res: BoundaryCheckResult = guard.inspect_changes()
        self.assertTrue(res.allowed)
        self.assertEqual(len(res.violations), 0)
        self.assertEqual(set(res.modified_files), {"src/ui/view.py", "src/ui/button.py"})

        # enforce が例外を出さないこと
        guard.enforce()

    def test_outside_boundary_detected(self) -> None:
        guard = BoundaryGuard(
            repo_dir=self.repo_dir,
            owns_patterns=["src/ui/**"],
            base_checkpoint=self.base_commit,
        )

        # UIスコープ外（core）のファイルを不正変更
        (self.repo_dir / "src" / "core" / "app.py").write_text("# Infiltrated Core\n", encoding="utf-8")
        # 未追跡のルートファイル作成
        (self.repo_dir / "unauthorized.txt").write_text("rogue", encoding="utf-8")

        res: BoundaryCheckResult = guard.inspect_changes()
        self.assertFalse(res.allowed)
        self.assertIn("src/core/app.py", res.violations)
        self.assertIn("unauthorized.txt", res.violations)

        with self.assertRaises(BoundaryViolationError):
            guard.enforce()

    def test_exact_and_nested_glob_matching(self) -> None:
        guard = BoundaryGuard(
            repo_dir=self.repo_dir,
            owns_patterns=["src/core/*", "docs/*.md"],
            base_checkpoint=self.base_commit,
        )

        (self.repo_dir / "src" / "core" / "utils.py").write_text("# Utils\n", encoding="utf-8")
        (self.repo_dir / "docs").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "docs" / "guide.md").write_text("# Guide\n", encoding="utf-8")

        res = guard.inspect_changes()
        self.assertTrue(res.allowed)


if __name__ == "__main__":
    unittest.main()
