"""GATE-9: verify-step が一時インデックスで Tree SHA を求め、本物のインデックスを変更しないことを検証する。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main
from auto_developer.guard import GitGuard


class TestGate9TempIndex(unittest.TestCase):
    """GATE-9: verify-step 前後で git status --porcelain が変わらず .env がステージされないこと。"""

    def _init_repo(self, repo: Path) -> str:
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (repo / "PLAN.md").write_text("- [ ] 1. dummy task\n", encoding="utf-8")
        (repo / "scripts").mkdir(parents=True, exist_ok=True)
        verify_sh = repo / "scripts" / "verify_local.sh"
        verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        verify_sh.chmod(0o755)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_verify_step_does_not_stage_untracked_files_in_real_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = self._init_repo(repo)
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

            status_before = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout

            rc = cli_main(["--work-dir", str(repo), "verify-step", "--checkpoint", checkpoint, "--task-id", "1"])
            self.assertEqual(rc, 0)

            status_after = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            non_plan_before = [l for l in status_before.splitlines() if not l.endswith(" PLAN.md")]
            non_plan_after = [l for l in status_after.splitlines() if not l.endswith(" PLAN.md")]
            self.assertEqual(non_plan_after, non_plan_before)
            self.assertIn("?? app.py", status_after)
            self.assertNotIn("A  app.py", status_after)

    def test_verify_step_does_not_stage_untracked_env_in_real_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = self._init_repo(repo)
            (repo / ".env").write_text("DUMMY=1\n", encoding="utf-8")

            status_before = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout

            rc = cli_main(["--work-dir", str(repo), "verify-step", "--checkpoint", checkpoint, "--task-id", "1"])
            self.assertEqual(rc, 1)

            status_after = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=repo,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(status_after, status_before)
            self.assertIn("?? .env", status_after)
            self.assertNotIn("A  .env", status_after)

    def test_get_working_tree_sha_uses_temp_index_without_modifying_real_index(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            (repo / ".env").write_text("DUMMY=1\n", encoding="utf-8")
            guard = GitGuard(repo_dir=repo)

            status_before = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
            ).stdout
            tree_sha = guard.get_working_tree_sha()
            self.assertEqual(len(tree_sha), 40)
            status_after = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True
            ).stdout
            self.assertEqual(status_after, status_before)


if __name__ == "__main__":
    unittest.main()
