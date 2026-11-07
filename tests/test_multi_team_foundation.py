from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.manifest import TeamManifest
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import RunnerAdapter
from auto_developer.worktree import WorktreeManager


class RogueTeamRunnerAdapter(RunnerAdapter):
    """領域外 (src/core/app.py) およびルートファイルを不正に変更・作成する Maker。"""

    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir

    def run_maker(self, task: Any, context: dict[str, Any]) -> tuple[int, str]:
        # 正当な UI 変更
        (self.repo_dir / "src" / "ui" / "view.py").write_text("# Legit UI\n", encoding="utf-8")
        # 領空侵犯 (Core の書き換え)
        (self.repo_dir / "src" / "core" / "app.py").write_text("# HACKED CORE\n", encoding="utf-8")
        # 未追跡の侵入ファイル
        (self.repo_dir / "rogue.sh").write_text("#!/bin/sh\necho rogue\n", encoding="utf-8")
        return 0, "Modified view.py and hacked core app.py"

    def run_checker(self, task: Any, diff: str, spec_content: str) -> tuple[bool, str]:
        return True, "Looks good"


class TestMultiTeamFoundation(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # テスト用リポジトリ初期化
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # ディレクトリ構成
        (self.repo_dir / ".gitignore").write_text(".dev/\nNIGHTLY_REPORT.md\nlogs/\n", encoding="utf-8")
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

    def test_multi_team_boundary_enforcement_and_automatic_quarantine(self) -> None:
        """BoundaryGuardと自動Quarantineの連携による領空侵犯遮断・成果保全・クリーン復元を実証。"""
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

        # チームWorktree内にタスク計画と検証スクリプトを用意
        (ui_wt / "PLAN.md").write_text("# PLAN\n- [ ] 1. Update UI components\n", encoding="utf-8")
        (ui_wt / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
        verify_script = ui_wt / "verify.sh"
        verify_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        verify_script.chmod(0o755)

        subprocess.run(["git", "add", "."], cwd=ui_wt, check=True)
        subprocess.run(["git", "commit", "-m", "init ui worktree setup"], cwd=ui_wt, check=True)

        # 不正侵入を行う Maker を用いてオーケストレーターをチームモードで起動
        runner = RogueTeamRunnerAdapter(repo_dir=ui_wt)
        orchestrator = NightlyOrchestrator(
            repo_dir=ui_wt,
            verify_script=verify_script,
            runner_adapter=runner,
            team_id=ui_team.id,
            owns_patterns=ui_team.owns,
        )

        exit_code = orchestrator.run()

        # 1. 領空侵犯によりパイプラインが Exit 1 で安全停止すること
        self.assertEqual(exit_code, 1)

        # 2. 作業ツリーが侵犯前のクリーンな状態に完全復元されていること（未追跡ファイル含めて完全消去）
        status_ui = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ui_wt,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(status_ui.stdout.strip(), "")

        # 3. 侵犯されたコアコードが元の状態に復元されていること
        restored_core = (ui_wt / "src" / "core" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(restored_core, "# Core Base\n")
        self.assertFalse((ui_wt / "rogue.sh").exists())

        # 4. チーム専用の隔離ブランチ（quarantine/team-ui-1-...）が作成されていること
        branch_res = subprocess.run(
            ["git", "branch", "--list", "quarantine/team-ui-1-*"],
            cwd=ui_wt,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(len(branch_res.stdout.strip()) > 0)

        # 5. 成果保全用スナップショットアーカイブが存在すること
        quarantines_dir = ui_wt / ".dev" / "quarantines"
        snapshots = list(quarantines_dir.glob("snapshot_task_team-ui-1_*.tar.gz"))
        self.assertTrue(len(snapshots) > 0)

        # 6. メインリポジトリのブランチおよびファイルが一切汚染されていないこと
        main_core = (self.repo_dir / "src" / "core" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(main_core, "# Core Base\n")
        status_main = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(status_main.stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
