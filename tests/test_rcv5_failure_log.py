from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.models import Task
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter
from auto_developer.runner import MockRunnerAdapter, RunnerAdapter


class _ModifySpecRunner(RunnerAdapter):
    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir

    def run_maker(self, task: Task, context: dict[str, object]) -> tuple[int, str]:
        (self.repo_dir / "SPEC.md").write_text("# Tampered SPEC\n", encoding="utf-8")
        return 0, "modified SPEC.md"

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        return True, "OK\nDECISION: APPROVE"


class _WriteAppRunner(MockRunnerAdapter):
    def __init__(self, repo_dir: Path, *, checker_approve: bool, checker_reason: str) -> None:
        super().__init__(checker_approve=checker_approve, checker_reason=checker_reason)
        self.repo_dir = repo_dir

    def run_maker(self, task: Task, context: dict[str, object]) -> tuple[int, str]:
        (self.repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
        return 0, "created app.py"


class TestRcv5FailureLog(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "config", "user.name", "Tester"],
            ["git", "config", "user.email", "tester@example.com"],
        ):
            subprocess.run(cmd, cwd=self.repo, check=True, capture_output=True)
        (self.repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n", encoding="utf-8")
        (self.repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
        (self.repo / "PLAN.md").write_text("# PLAN\n\n## ステップ\n- [ ] 1. Test task\n", encoding="utf-8")
        verify = self.repo / "verify.sh"
        verify.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        verify.chmod(0o755)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_write_failure_log_writes_phase_and_reason_as_first_two_lines(self) -> None:
        log_path = self.repo / "logs" / "nightly" / "task_1_failure.log"
        MorningReporter().write_failure_log(
            log_path,
            {
                "task_id": "1",
                "title": "Test",
                "checkpoint": "abc",
                "failure_phase": "VERIFY",
                "reason": "Test suite failed",
            },
        )
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "Failure Phase: VERIFY")
        self.assertEqual(lines[1], "Reason: Test suite failed")

    def test_protected_files_failure_log_records_phase_and_file_list(self) -> None:
        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            plan_path=self.repo / "PLAN.md",
            spec_path=self.repo / "SPEC.md",
            verify_script=self.repo / "verify.sh",
            runner_adapter=_ModifySpecRunner(self.repo),
        )
        rc = orch.run()
        self.assertNotEqual(rc, 0)
        log_path = self.repo / "logs" / "nightly" / "task_1_failure.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "Failure Phase: PROTECTED_FILES")
        self.assertTrue(lines[1].startswith("Reason: "), lines[1])
        self.assertIn("SPEC.md", lines[1])

    def test_checker_reject_failure_log_records_phase_and_reason(self) -> None:
        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            plan_path=self.repo / "PLAN.md",
            spec_path=self.repo / "SPEC.md",
            verify_script=self.repo / "verify.sh",
            runner_adapter=_WriteAppRunner(
                self.repo, checker_approve=False, checker_reason="Missing edge case\nDECISION: REJECT"
            ),
        )
        rc = orch.run()
        self.assertNotEqual(rc, 0)
        log_path = self.repo / "logs" / "nightly" / "task_1_failure.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "Failure Phase: CHECKER_REJECT")
        self.assertTrue(lines[1].startswith("Reason: "), lines[1])
        self.assertIn("Missing edge case", lines[1])

    def test_maker_no_diff_failure_log_records_phase_and_reason(self) -> None:
        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            plan_path=self.repo / "PLAN.md",
            spec_path=self.repo / "SPEC.md",
            verify_script=self.repo / "verify.sh",
            runner_adapter=MockRunnerAdapter(),
        )
        rc = orch.run()
        self.assertNotEqual(rc, 0)
        log_path = self.repo / "logs" / "nightly" / "task_1_failure.log"
        lines = log_path.read_text(encoding="utf-8").splitlines()
        self.assertEqual(lines[0], "Failure Phase: MAKER_NO_DIFF")
        self.assertTrue(lines[1].startswith("Reason: "), lines[1])


if __name__ == "__main__":
    unittest.main()
