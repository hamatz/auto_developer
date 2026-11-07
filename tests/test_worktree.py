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

    def test_remove_worktree_preserves_unmerged_branch(self) -> None:
        """未マージコミットがある場合、delete_branch=True でもブランチが破棄されず保全されること。"""
        wt_path = self.manager.create_worktree(team_id="team-a")
        (wt_path / "feature.txt").write_text("unmerged feature commit\n", encoding="utf-8")
        subprocess.run(["git", "add", "feature.txt"], cwd=wt_path, check=True)
        subprocess.run(["git", "commit", "-m", "feat: unmerged work"], cwd=wt_path, check=True)

        # Worktree 削除（delete_branch=True を指定）
        deleted_branch = self.manager.remove_worktree(team_id="team-a", force=False, delete_branch=True)
        self.assertFalse(deleted_branch, "Unmerged branch must NOT be deleted!")
        self.assertFalse(wt_path.exists())

        # ブランチがまだ存在していること（成果保全）
        res = subprocess.run(
            ["git", "branch", "--list", "feature/team-a"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.stdout.strip(), "feature/team-a")

    def test_remove_worktree_force_quarantines_uncommitted_work(self) -> None:
        """force=True で未コミット変更がある場合、直接破壊せず Quarantine スナップショットを作成して保全すること。"""
        import tarfile

        wt_path = self.manager.create_worktree(team_id="team-a")
        (wt_path / "dirty.txt").write_text("critical uncommitted thoughts\n", encoding="utf-8")

        # force=True で削除
        self.manager.remove_worktree(team_id="team-a", force=True)
        self.assertFalse(wt_path.exists())

        # .dev/quarantines にスナップショットが生成されていること
        quarantines_dir = self.repo_dir / ".dev" / "quarantines"
        self.assertTrue(quarantines_dir.exists())
        snapshots = list(quarantines_dir.glob("snapshot_worktree_team-a_*.tar.gz"))
        self.assertTrue(len(snapshots) > 0, "Quarantine snapshot must be created before forced removal!")

        # スナップショット内に未コミットファイルが存在することを確認
        with tarfile.open(snapshots[0], "r:gz") as tar:
            names = tar.getnames()
            self.assertTrue(any("dirty.txt" in n for n in names))

    def test_remove_worktree_force_requires_purge_unmerged_to_delete_unmerged_branch(self) -> None:
        """force=True, delete_branch=True でも purge_unmerged=False なら未マージブランチを保全すること。"""
        wt_path = self.manager.create_worktree(team_id="team-a")
        (wt_path / "work.txt").write_text("unmerged work\n", encoding="utf-8")
        subprocess.run(["git", "add", "work.txt"], cwd=wt_path, check=True)
        subprocess.run(["git", "commit", "-m", "feat: unmerged commit"], cwd=wt_path, check=True)

        # 1. force=True, delete_branch=True (purge_unmerged=False)
        deleted = self.manager.remove_worktree(
            team_id="team-a", force=True, delete_branch=True, purge_unmerged=False
        )
        self.assertFalse(deleted, "Unmerged branch must not be deleted even with force=True unless purge_unmerged=True")
        res = subprocess.run(
            ["git", "branch", "--list", "feature/team-a"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res.stdout.strip(), "feature/team-a")

        # 2. purge_unmerged=True を明示した場合は削除を許可
        deleted_purged = self.manager.remove_worktree(
            team_id="team-a", force=True, delete_branch=True, purge_unmerged=True
        )
        self.assertTrue(deleted_purged)
        res_after = subprocess.run(
            ["git", "branch", "--list", "feature/team-a"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_after.stdout.strip(), "")

    def test_cleanup_all(self) -> None:
        self.manager.create_worktree(team_id="team-a")
        self.manager.create_worktree(team_id="team-b")
        self.assertEqual(len(self.manager.list_worktrees()), 2)

        # デフォルト引数（force=False, delete_branches=False）での安全なクリーンアップ
        self.manager.cleanup_all()
        self.assertEqual(len(self.manager.list_worktrees()), 0)

        # ブランチは削除されずに保全されていること
        res_a = subprocess.run(["git", "branch", "--list", "feature/team-a"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        res_b = subprocess.run(["git", "branch", "--list", "feature/team-b"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        self.assertEqual(res_a.stdout.strip(), "feature/team-a")
        self.assertEqual(res_b.stdout.strip(), "feature/team-b")


if __name__ == "__main__":
    unittest.main()
