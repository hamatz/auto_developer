from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.worktree import WorktreeError, WorktreeManager


class TestWorktreeManager(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # テスト用Gitリポジトリ初期化
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # 初期コミット作成
        (self.repo_dir / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "README.md"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_dir, check=True)

        self.manager = WorktreeManager(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        try:
            self.manager.cleanup_all(force=True)
        except (subprocess.SubprocessError, OSError):
            pass
        self.temp_dir.cleanup()

    def test_create_worktree_success(self) -> None:
        wt_path = self.manager.create_worktree(team_id="team-core")
        self.assertTrue(wt_path.exists())
        self.assertTrue((wt_path / "README.md").exists())

        # ブランチ確認
        res = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=wt_path,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.stdout.strip(), "feature/team-core")

    def test_create_worktree_collision(self) -> None:
        self.manager.create_worktree(team_id="team-core")
        with self.assertRaises(WorktreeError):
            self.manager.create_worktree(team_id="team-core")

    def test_list_worktrees(self) -> None:
        self.assertEqual(len(self.manager.list_worktrees()), 0)

        self.manager.create_worktree(team_id="team-a")
        self.manager.create_worktree(team_id="team-b")

        worktrees = self.manager.list_worktrees()
        self.assertEqual(len(worktrees), 2)
        team_ids = {wt.team_id for wt in worktrees}
        self.assertEqual(team_ids, {"team-a", "team-b"})

    def test_remove_worktree(self) -> None:
        wt_path = self.manager.create_worktree(team_id="team-a")
        self.assertTrue(wt_path.exists())

        self.manager.remove_worktree(team_id="team-a", force=True, delete_branch=True)
        self.assertFalse(wt_path.exists())
        self.assertEqual(len(self.manager.list_worktrees()), 0)

        # ブランチが削除されていること
        res = subprocess.run(
            ["git", "branch", "--list", "feature/team-a"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.stdout.strip(), "")

    def test_create_worktree_invalid_team_id(self) -> None:
        invalid_ids = ["", "   ", "TeamA", "team/core", "../outside", "team@core", "-invalid", "1team"]
        for t_id in invalid_ids:
            with self.subTest(team_id=t_id), self.assertRaises(WorktreeError):
                self.manager.create_worktree(team_id=t_id)

    def test_remove_worktree_path_traversal(self) -> None:
        with self.assertRaises(WorktreeError):
            self.manager.remove_worktree(team_id="../outside")

    def test_remove_worktree_with_uncommitted_changes_rejected(self) -> None:
        wt_path = self.manager.create_worktree(team_id="team-a")
        # 未コミットの変更を作成
        (wt_path / "work.txt").write_text("uncommitted work", encoding="utf-8")
        subprocess.run(["git", "add", "work.txt"], cwd=wt_path, check=True)

        # force=False の場合、未コミット変更があれば削除拒否されるべき
        with self.assertRaises(WorktreeError):
            self.manager.remove_worktree(team_id="team-a", force=False)

        # ディレクトリと成果物が破壊されずに残っていること（成果保全）
        self.assertTrue(wt_path.exists())
        self.assertTrue((wt_path / "work.txt").exists())

    def test_cleanup_all(self) -> None:
        self.manager.create_worktree(team_id="team-a")
        self.manager.create_worktree(team_id="team-b")
        self.assertEqual(len(self.manager.list_worktrees()), 2)

        self.manager.cleanup_all(force=True)
        self.assertEqual(len(self.manager.list_worktrees()), 0)


if __name__ == "__main__":
    unittest.main()
