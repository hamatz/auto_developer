import tempfile
import unittest
import subprocess
import sys
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.models import JobState
from auto_developer.guard import GitGuard
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import CommandRunnerAdapter


class TestRecoveryBaseBranch(unittest.TestCase):
    def test_job_state_base_branch_persistence(self):
        """機能V: JobState に base_branch が記録され、保存・復元できること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "job_state.json"
            state = JobState(
                job_id="test-job-1",
                base_branch="feature/experiment",
            )
            state.save(state_file)

            loaded = JobState.load(state_file)
            self.assertEqual(loaded.base_branch, "feature/experiment")

    def test_orchestrator_initializes_base_branch(self):
        """機能V: Orchestrator が起動時に現在の Git ブランチを base_branch として保存すること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "custom-main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\nlogs/\n", encoding="utf-8")
            (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. タスク1\n", encoding="utf-8")
            verify_sh = repo / "verify.sh"
            verify_sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify_sh.chmod(0o755)

            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            class DummyRunner(CommandRunnerAdapter):
                def run_maker(self, task, context):
                    (repo / "file.txt").write_text("done\n", encoding="utf-8")
                    return 0, "ok"

                def run_checker(self, task, diff, spec_content):
                    return True, "APPROVE"

            state_path = repo / ".dev" / "job_state.json"
            orchestrator = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=repo / "NIGHTLY_REPORT.md",
                state_path=state_path,
                runner_adapter=DummyRunner(),
                dry_run=False,
            )
            exit_code = orchestrator.run()
            self.assertEqual(exit_code, 0)

            loaded_state = JobState.load(state_path)
            self.assertEqual(loaded_state.base_branch, "custom-main")


if __name__ == "__main__":
    unittest.main()
