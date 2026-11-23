"""Tests for TEAM-21: handling boundary violations immediately before commit."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.evidence import EvidenceStore
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import RunnerAdapter


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return env


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(cwd), *args],
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_team_worktree_repo(repo_dir: Path) -> tuple[Path, str]:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(
        ".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n*.py[cod]\n",
        encoding="utf-8",
    )
    (repo_dir / "SPEC.md").write_text("# SPEC\n- TEAM-21\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Root PLAN\n\n## ステップ\n- [ ] 1. Root task\n", encoding="utf-8")

    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    verify_sh = repo_dir / "scripts" / "verify_local.sh"
    verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    verify_sh.chmod(0o755)

    (repo_dir / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo_dir / "src" / "ui" / "view.py").write_text("VIEW = 1\n", encoding="utf-8")

    (repo_dir / "plans").mkdir(parents=True, exist_ok=True)
    (repo_dir / "plans" / "core.md").write_text(
        "# PLAN (core)\n\n## ステップ\n- [ ] 1. Update core module\n",
        encoding="utf-8",
    )
    (repo_dir / "plans" / "ui.md").write_text(
        "# PLAN (ui)\n\n## ステップ\n- [ ] 1. Update ui module\n",
        encoding="utf-8",
    )

    teams_payload = {
        "version": 1,
        "project": "demo",
        "base": {"ref": "main", "require_clean": True},
        "teams": [
            {"id": "core", "plan": "plans/core.md", "owns": ["src/core/**"], "depends_on": []},
            {"id": "ui", "plan": "plans/ui.md", "owns": ["src/ui/**"], "depends_on": []},
        ],
        "integration": {
            "strategy": "merge-sequential",
            "require_team_evidence": True,
            "reverify_after_merge": True,
            "update_main": False,
        },
    }
    (repo_dir / "teams.json").write_text(json.dumps(teams_payload, indent=2) + "\n", encoding="utf-8")

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "initial commit")

    wt_dir = repo_dir / ".dev" / "worktrees" / "core"
    _git(repo_dir, "worktree", "add", "-b", "team/core", str(wt_dir), "main")
    wt_checkpoint = _git(wt_dir, "rev-parse", "HEAD").stdout.strip()
    return wt_dir, wt_checkpoint


class TestCommitGateBoundaryOrchestrator(unittest.TestCase):
    """TEAM-21: run のコミット直前担当範囲検査違反時の隔離・復元・Evidence破棄・FAILED記録."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_quarantines_discards_evidence_and_restores_clean_on_commit_gate_violation(self) -> None:
        wt_dir, wt_checkpoint = _init_team_worktree_repo(self.repo_dir)

        class RogueCheckerAdapter(RunnerAdapter):
            def __init__(self, work_dir: Path) -> None:
                self.work_dir = work_dir

            def run_maker(self, task: Any, context: dict[str, Any]) -> tuple[int, str]:
                (self.work_dir / "src" / "core" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
                return 0, "maker valid change"

            def run_checker(self, task: Any, diff: str, spec_content: str) -> tuple[bool, str]:
                # Checker unexpectedly writes outside owns right before commit gate
                (self.work_dir / "src" / "ui" / "checker_leak.py").write_text("LEAK = 1\n", encoding="utf-8")
                return True, "APPROVE: with side effect"

        orch = NightlyOrchestrator(repo_dir=wt_dir, runner_adapter=RogueCheckerAdapter(wt_dir))
        exit_code = orch.run()

        self.assertEqual(exit_code, 1, "Commit-gate boundary violation in run must stop with exit code 1")

        # Evidence must be discarded
        evidence_file = EvidenceStore(repo_dir=wt_dir).evidence_dir / "task_1.json"
        self.assertFalse(evidence_file.exists(), "Evidence must be discarded on commit-gate boundary violation")

        # Main worktree must be cleanly restored with NO staged changes left behind
        self.assertEqual(_git(wt_dir, "rev-parse", "HEAD").stdout.strip(), wt_checkpoint)
        self.assertEqual(
            _git(wt_dir, "status", "--porcelain").stdout.strip(),
            "",
            "Working tree and index (staged changes) must be completely clean",
        )
        self.assertFalse((wt_dir / "src" / "ui" / "checker_leak.py").exists())
        self.assertEqual((wt_dir / "src" / "core" / "app.py").read_text(encoding="utf-8"), "VALUE = 1\n")
        self.assertIn("- [ ] 1. Update core module", (wt_dir / "plans" / "core.md").read_text(encoding="utf-8"))

        # Quarantine branch created with **[FAILED]** recorded
        q_branches = [
            b.lstrip("* ").strip()
            for b in _git(wt_dir, "branch", "--list", "quarantine/*/core/1-*").stdout.splitlines()
            if b.strip()
        ]
        self.assertTrue(q_branches, "Expected quarantine branch for core task 1")
        q_plan = _git(wt_dir, "show", f"{q_branches[0]}:plans/core.md").stdout
        self.assertIn("**[FAILED]**", q_plan)

        # Failure log saved with violation details
        failure_log = wt_dir / "logs" / "nightly" / "task_1_failure.log"
        self.assertTrue(failure_log.is_file())
        self.assertIn("checker_leak.py", failure_log.read_text(encoding="utf-8"))

        # JobState and NIGHTLY_REPORT.md recorded FAILED
        state_data = json.loads((wt_dir / ".dev" / "job_state.json").read_text(encoding="utf-8"))
        self.assertEqual(state_data["status"], "FAILED")
        self.assertIsNotNone(state_data.get("quarantined_task"))
        report_text = (wt_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn(q_branches[0], report_text)

    def test_cli_run_quarantines_and_cleans_staged_changes_on_commit_gate_violation(self) -> None:
        wt_dir, wt_checkpoint = _init_team_worktree_repo(self.repo_dir)
        maker_cmd = "python3 -c \"from pathlib import Path; Path('src/core/app.py').write_text('VALUE = 10\\n')\""
        checker_cmd = (
            "python3 -c \"from pathlib import Path; "
            "Path('src/ui/checker_outside.py').write_text('OUTSIDE = 1\\n'); "
            "print('DECISION: APPROVE')\""
        )

        proc = _run_cli(
            wt_dir,
            "run",
            "--maker-cmd",
            maker_cmd,
            "--checker-cmd",
            checker_cmd,
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5-5-medium",
        )
        self.assertEqual(proc.returncode, 1, f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertEqual(_git(wt_dir, "rev-parse", "HEAD").stdout.strip(), wt_checkpoint)
        self.assertEqual(_git(wt_dir, "status", "--porcelain").stdout.strip(), "")
        self.assertFalse((wt_dir / "src" / "ui" / "checker_outside.py").exists())
        self.assertFalse((EvidenceStore(repo_dir=wt_dir).evidence_dir / "task_1.json").exists())

        q_branches = [
            b.lstrip("* ").strip()
            for b in _git(wt_dir, "branch", "--list", "quarantine/*/core/1-*").stdout.splitlines()
            if b.strip()
        ]
        self.assertTrue(q_branches)
        q_plan = _git(wt_dir, "show", f"{q_branches[0]}:plans/core.md").stdout
        self.assertIn("**[FAILED]**", q_plan)
        failure_log = wt_dir / "logs" / "nightly" / "task_1_failure.log"
        self.assertTrue(failure_log.is_file())
        self.assertIn("checker_outside.py", failure_log.read_text(encoding="utf-8"))


class TestCommitStepBoundaryCli(unittest.TestCase):
    """TEAM-21: commit-step がコミット直前の担当範囲違反で Evidence を無効にし、終了コード 3 と rollback-step 案内を返す."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_commit_step_invalidates_evidence_guides_rollback_and_keeps_head_and_plan(self) -> None:
        wt_dir, wt_checkpoint = _init_team_worktree_repo(self.repo_dir)

        (wt_dir / "src" / "core" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        v_ok = _run_cli(wt_dir, "verify-step", "--checkpoint", wt_checkpoint, "--task-id", "1")
        self.assertEqual(v_ok.returncode, 0, f"stdout={v_ok.stdout}\nstderr={v_ok.stderr}")

        from review_test_helpers import record_test_review

        rr = record_test_review(wt_dir, "1", reason="looks good")
        self.assertEqual(rr.returncode, 0, rr.stderr)

        evidence_file = EvidenceStore(repo_dir=wt_dir).evidence_dir / "task_1.json"
        self.assertTrue(evidence_file.exists())
        plan_before_commit = (wt_dir / "plans" / "core.md").read_text(encoding="utf-8")

        # Tamper outside owns after verify-step
        (wt_dir / "src" / "ui" / "rogue.py").write_text("ROGUE = 1\n", encoding="utf-8")

        c_bad = _run_cli(
            wt_dir,
            "commit-step",
            "--task-id",
            "1",
            "--checkpoint",
            wt_checkpoint,
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5-5-medium",
            "--checker-decision",
            "APPROVE",
            "--checker-reason",
            "looks good",
        )
        self.assertEqual(c_bad.returncode, 3, f"stdout={c_bad.stdout}\nstderr={c_bad.stderr}")
        self.assertIn("BOUNDARY", c_bad.stderr.upper())
        self.assertIn("rollback-step", c_bad.stderr)
        self.assertIn("src/ui/rogue.py", c_bad.stderr)

        # Evidence must be invalidated
        self.assertFalse(evidence_file.exists(), "Evidence must be invalidated when commit-step rejects boundary violation")

        # HEAD and plan file must NOT be modified by commit-step
        self.assertEqual(_git(wt_dir, "rev-parse", "HEAD").stdout.strip(), wt_checkpoint)
        self.assertEqual((wt_dir / "plans" / "core.md").read_text(encoding="utf-8"), plan_before_commit)

        # Even if the rogue file is removed, commit-step must NOT succeed with the invalidated Evidence
        (wt_dir / "src" / "ui" / "rogue.py").unlink()
        c_replay = _run_cli(
            wt_dir,
            "commit-step",
            "--task-id",
            "1",
            "--checkpoint",
            wt_checkpoint,
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5-5-medium",
            "--checker-decision",
            "APPROVE",
            "--checker-reason",
            "looks good",
        )
        self.assertEqual(c_replay.returncode, 3, "Invalidated Evidence must not be usable for commit-step")
        self.assertEqual(_git(wt_dir, "rev-parse", "HEAD").stdout.strip(), wt_checkpoint)

        # Guided rollback-step must quarantine and restore cleanly
        rb = _run_cli(
            wt_dir,
            "rollback-step",
            "--checkpoint",
            wt_checkpoint,
            "--task-id",
            "1",
            "--reason",
            "Commit-gate boundary violation",
        )
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertEqual(_git(wt_dir, "status", "--porcelain").stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
