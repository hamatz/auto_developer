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

from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import RunnerAdapter


class RogueMakerRunnerAdapter(RunnerAdapter):
    """領域外（src/core/app.py）を不正に変更する Maker アダプター。"""

    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir

    def run_maker(self, task: Any, context: dict[str, Any]) -> tuple[int, str]:
        # 正当な UI 変更
        ui_file = self.repo_dir / "src" / "ui" / "view.py"
        ui_file.write_text("# Updated UI\n", encoding="utf-8")
        # 領空侵犯 (Core の書き換え)
        core_file = self.repo_dir / "src" / "core" / "app.py"
        core_file.write_text("# HACKED CORE\n", encoding="utf-8")
        return 0, "Modified view.py and hacked core app.py"

    def run_checker(self, task: Any, diff: str, spec_content: str) -> tuple[bool, str]:
        return True, "Looks good"


class TestOrchestratorBoundary(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # テスト用Gitリポジトリ初期化
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # ディレクトリ構成
        (self.repo_dir / ".gitignore").write_text(".dev/\nNIGHTLY_REPORT.md\nlogs/\n", encoding="utf-8")
        (self.repo_dir / "src" / "core").mkdir(parents=True)
        (self.repo_dir / "src" / "ui").mkdir(parents=True)
        (self.repo_dir / "src" / "core" / "app.py").write_text("# Core Base\n", encoding="utf-8")
        (self.repo_dir / "src" / "ui" / "view.py").write_text("# UI Base\n", encoding="utf-8")
        (self.repo_dir / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# PLAN\n- [ ] 1. Test task\n", encoding="utf-8")

        verify_script = self.repo_dir / "verify.sh"
        verify_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        verify_script.chmod(0o755)

        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_dir, check=True)

        self.verify_script = verify_script

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_orchestrator_triggers_quarantine_on_boundary_violation(self) -> None:
        runner = RogueMakerRunnerAdapter(repo_dir=self.repo_dir)
        orchestrator = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            verify_script=self.verify_script,
            runner_adapter=runner,
            team_id="team-ui",
            owns_patterns=["src/ui/**"],
        )

        exit_code = orchestrator.run()
        # 領空侵犯によりパイプラインが Exit 1 で安全停止すること
        self.assertEqual(exit_code, 1)

        # 本線作業ツリーが初期状態にクリーン復元されていること
        core_text = (self.repo_dir / "src" / "core" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(core_text, "# Core Base\n")

        status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(status.stdout.strip(), "")

        # チーム専用の隔離ブランチ（quarantine/team-ui-...）が作成されていること
        branch_res = subprocess.run(
            ["git", "branch", "--list", "quarantine/team-ui-*"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(len(branch_res.stdout.strip()) > 0)


if __name__ == "__main__":
    unittest.main()
