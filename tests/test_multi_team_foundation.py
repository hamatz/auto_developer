from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.boundary import BoundaryGuard, BoundaryViolationError
from auto_developer.manifest import TeamManifest
from auto_developer.worktree import WorktreeManager


class TestMultiTeamFoundation(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # テスト用リポジトリ初期化
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # ディレクトリ構成
        (self.repo_dir / ".gitignore").write_text(".dev/\n", encoding="utf-8")
        (self.repo_dir / "src" / "core").mkdir(parents=True)
        (self.repo_dir / "src" / "ui").mkdir(parents=True)
        (self.repo_dir / "src" / "core" / "app.py").write_text("# Core Base\n", encoding="utf-8")
        (self.repo_dir / "src" / "ui" / "view.py").write_text("# UI Base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_dir, check=True)

        self.wt_manager = WorktreeManager(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        try:
            self.wt_manager.cleanup_all(force=True)
        except (subprocess.SubprocessError, OSError):
            pass
        self.temp_dir.cleanup()

    def test_multi_team_coexistence(self) -> None:
        """2つの独立Worktreeが競合なく共存し、メインブランチを汚染しないことを実証。"""
        # 1. Team Core と Team UI の Worktree 作成
        core_wt = self.wt_manager.create_worktree(team_id="team-core")
        ui_wt = self.wt_manager.create_worktree(team_id="team-ui")

        self.assertTrue(core_wt.exists())
        self.assertTrue(ui_wt.exists())

        # 2. Team Core が自領域を変更してコミット
        (core_wt / "src" / "core" / "app.py").write_text("# Core Updated by Team Core\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=core_wt, check=True)
        subprocess.run(["git", "commit", "-m", "feat(core): update core logic"], cwd=core_wt, check=True)

        # 3. Team UI が自領域を変更してコミット
        (ui_wt / "src" / "ui" / "view.py").write_text("# UI Updated by Team UI\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=ui_wt, check=True)
        subprocess.run(["git", "commit", "-m", "feat(ui): update ui view"], cwd=ui_wt, check=True)

        # 4. メインブランチがクリーンで未変更のままであることの検証
        main_core_text = (self.repo_dir / "src" / "core" / "app.py").read_text(encoding="utf-8")
        main_ui_text = (self.repo_dir / "src" / "ui" / "view.py").read_text(encoding="utf-8")
        self.assertEqual(main_core_text, "# Core Base\n")
        self.assertEqual(main_ui_text, "# UI Base\n")

        status_main = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(status_main.stdout.strip(), "")

        # 5. 各Worktreeのブランチが独立していることの検証
        core_branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=core_wt, check=True, capture_output=True, text=True
        ).stdout.strip()
        ui_branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=ui_wt, check=True, capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(core_branch, "feature/team-core")
        self.assertEqual(ui_branch, "feature/team-ui")

    def test_multi_team_boundary_enforcement_and_rollback(self) -> None:
        """BoundaryGuardによる領空侵犯検知と、安全なロールバック復元を実証。"""
        manifest_data = {
            "version": 1,
            "project": "multi-team-app",
            "teams": [
                {"id": "team-ui", "owns": ["src/ui/**"]},
            ],
        }
        manifest = TeamManifest.from_dict(manifest_data)
        ui_team = manifest.teams[0]

        ui_wt = self.wt_manager.create_worktree(team_id="team-ui")
        guard = BoundaryGuard(repo_dir=ui_wt, owns_patterns=ui_team.owns)

        # 正当なUI変更と領空侵犯（core領域の改変およびルート侵入）
        (ui_wt / "src" / "ui" / "view.py").write_text("# Legit UI\n", encoding="utf-8")
        (ui_wt / "src" / "core" / "app.py").write_text("# Rogue Core Hack\n", encoding="utf-8")
        (ui_wt / "malicious.sh").write_text("#!/bin/sh\n", encoding="utf-8")

        # 領空侵犯の検知
        res = guard.inspect_changes()
        self.assertFalse(res.allowed)
        self.assertIn("src/core/app.py", res.violations)
        self.assertIn("malicious.sh", res.violations)
        self.assertNotIn("src/ui/view.py", res.violations)

        with self.assertRaises(BoundaryViolationError):
            guard.enforce()

        # ロールバックの実行（Fail-Fast隔離復元シミュレーション）
        subprocess.run(["git", "reset", "--hard", "HEAD"], cwd=ui_wt, check=True, capture_output=True)
        subprocess.run(["git", "clean", "-fd"], cwd=ui_wt, check=True, capture_output=True)

        # 完全に侵犯前の状態に復元されていることの検証
        restored_core = (ui_wt / "src" / "core" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(restored_core, "# Core Base\n")
        self.assertFalse((ui_wt / "malicious.sh").exists())
        self.assertEqual(guard.inspect_changes().modified_files, [])


if __name__ == "__main__":
    unittest.main()
