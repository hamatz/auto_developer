"""Tests for TEAM-20: post-verification boundary inspection before Evidence issuance."""

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


def _init_team_worktree_repo(repo_dir: Path, verify_body: str) -> tuple[Path, str]:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\n- TEAM-20\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Root PLAN\n\n## ステップ\n- [ ] 1. Root task\n", encoding="utf-8")

    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    verify_sh = repo_dir / "scripts" / "verify_local.sh"
    verify_sh.write_text(verify_body, encoding="utf-8")
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


class TestPostVerifyBoundaryCli(unittest.TestCase):
    """TEAM-20: verify-step が検証スクリプト実行後の担当範囲違反を検知して Evidence を発行しない."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_verify_step_rejects_post_verify_boundary_violation_with_exit_1_and_no_evidence(self) -> None:
        verify_body = (
            "#!/usr/bin/env bash\n"
            "mkdir -p build\n"
            "echo 'generated during verify' > build/generated.txt\n"
            "echo 'TAMPERED_BY_VERIFY = True' > src/ui/view.py\n"
            "exit 0\n"
        )
        wt_dir, wt_checkpoint = _init_team_worktree_repo(self.repo_dir, verify_body)

        # Maker changes only within owns
        (wt_dir / "src" / "core" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

        res = _run_cli(wt_dir, "verify-step", "--checkpoint", wt_checkpoint, "--task-id", "1")
        self.assertEqual(res.returncode, 1, f"stdout={res.stdout}\nstderr={res.stderr}")

        # Must NOT issue evidence
        evidence_file = EvidenceStore(repo_dir=wt_dir).evidence_dir / "task_1.json"
        self.assertFalse(evidence_file.exists(), "Evidence must NOT be issued when post-verify boundary fails")

        # Must revert plan file back to pending [ ]
        self.assertIn(
            "- [ ] 1. Update core module",
            (wt_dir / "plans" / "core.md").read_text(encoding="utf-8"),
        )

        # stderr must list violated files and mention .gitignore guidance
        self.assertIn("build/generated.txt", res.stderr)
        self.assertIn("src/ui/view.py", res.stderr)
        self.assertIn(".gitignore", res.stderr)

        # metrics event must record BOUNDARY_VIOLATION with exit_code 1
        events_path = wt_dir / ".dev" / "metrics" / "events.jsonl"
        self.assertTrue(events_path.is_file())
        events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "verify_step")
        self.assertEqual(events[0]["result"], "BOUNDARY_VIOLATION")
        self.assertEqual(events[0]["exit_code"], 1)

    def test_verify_step_non_team_allows_untracked_file_from_verify(self) -> None:
        _git(self.repo_dir, "init", "-b", "main")
        _git(self.repo_dir, "config", "user.name", "Test Runner")
        _git(self.repo_dir, "config", "user.email", "test@example.com")
        (self.repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
        (self.repo_dir / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# PLAN\n\n## ステップ\n- [ ] 1. Single task\n", encoding="utf-8")
        (self.repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
        vsh = self.repo_dir / "scripts" / "verify_local.sh"
        vsh.write_text("#!/usr/bin/env bash\necho 'ok' > verify_artifact.txt\nexit 0\n", encoding="utf-8")
        vsh.chmod(0o755)
        (self.repo_dir / "app.py").write_text("X = 1\n", encoding="utf-8")
        _git(self.repo_dir, "add", "-A")
        _git(self.repo_dir, "commit", "-m", "init")
        chk = _git(self.repo_dir, "rev-parse", "HEAD").stdout.strip()

        (self.repo_dir / "app.py").write_text("X = 2\n", encoding="utf-8")
        res = _run_cli(self.repo_dir, "verify-step", "--checkpoint", chk, "--task-id", "1")
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")
        self.assertTrue((EvidenceStore(repo_dir=self.repo_dir).evidence_dir / "task_1.json").exists())

    def test_verify_step_allows_gitignored_files_generated_during_verify(self) -> None:
        verify_body = (
            "#!/usr/bin/env bash\n"
            "mkdir -p src/ui/__pycache__\n"
            "echo 'bytecode' > src/ui/__pycache__/view.cpython-312.pyc\n"
            "exit 0\n"
        )
        wt_dir, wt_checkpoint = _init_team_worktree_repo(self.repo_dir, verify_body)
        # Add __pycache__/ to .gitignore in committed repo
        gitignore = wt_dir / ".gitignore"
        gitignore.write_text(gitignore.read_text(encoding="utf-8") + "__pycache__/\n", encoding="utf-8")
        _git(wt_dir, "add", ".gitignore")
        _git(wt_dir, "commit", "-m", "ignore pycache")
        wt_checkpoint = _git(wt_dir, "rev-parse", "HEAD").stdout.strip()

        (wt_dir / "src" / "core" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")

        res = _run_cli(wt_dir, "verify-step", "--checkpoint", wt_checkpoint, "--task-id", "1")
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")
        self.assertIn("VERIFICATION PASSED", res.stdout)
        evidence_file = EvidenceStore(repo_dir=wt_dir).evidence_dir / "task_1.json"
        self.assertTrue(evidence_file.exists())


class TestPostVerifyBoundaryOrchestrator(unittest.TestCase):
    """TEAM-20: run が検証後の担当範囲違反を検証失敗として扱い、再試行・隔離に従う."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_run_retries_when_post_verify_boundary_fails_once_then_succeeds(self) -> None:
        # verify_local.sh checks app.py: if VALUE = 2, it leaks a file outside owns and exits 0.
        # On retry (VALUE = 3), it succeeds cleanly without leaking.
        verify_body = (
            "#!/usr/bin/env bash\n"
            "if grep -q 'VALUE = 2' src/core/app.py; then\n"
            "  echo 'leak from verify' > outside_leak.txt\n"
            "fi\n"
            "exit 0\n"
        )
        wt_dir, _ = _init_team_worktree_repo(self.repo_dir, verify_body)

        class FixOnRetryAdapter(RunnerAdapter):
            def __init__(self, work_dir: Path) -> None:
                self.work_dir = work_dir
                self.contexts: list[dict[str, Any]] = []
                self.checker_called = 0

            def run_maker(self, task: Any, context: dict[str, Any]) -> tuple[int, str]:
                self.contexts.append(context)
                if context.get("attempt") == 1:
                    (self.work_dir / "src" / "core" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
                    return 0, "maker attempt 1"
                leak = self.work_dir / "outside_leak.txt"
                if leak.exists():
                    leak.unlink()
                (self.work_dir / "src" / "core" / "app.py").write_text("VALUE = 3\n", encoding="utf-8")
                return 0, "maker attempt 2 fixed leak"

            def run_checker(self, task: Any, diff: str, spec_content: str) -> tuple[bool, str]:
                self.checker_called += 1
                return True, "APPROVE: clean\nDECISION: APPROVE"

        adapter = FixOnRetryAdapter(wt_dir)
        orch = NightlyOrchestrator(repo_dir=wt_dir, runner_adapter=adapter)
        exit_code = orch.run()

        self.assertEqual(exit_code, 0)
        self.assertEqual(len(adapter.contexts), 2, "Expected 1 retry after post-verify boundary failure")
        self.assertEqual(adapter.contexts[1].get("attempt"), 2)
        self.assertIn("outside_leak.txt", str(adapter.contexts[1].get("prev_error", "")))
        self.assertEqual(adapter.checker_called, 1, "Checker must only run after clean verification")
        self.assertIn("- [x] 1. Update core module", (wt_dir / "plans" / "core.md").read_text(encoding="utf-8"))

        record = EvidenceStore(repo_dir=wt_dir).get_record("1")
        self.assertIsNotNone(record)
        assert record is not None
        self.assertEqual(len(record["attempts"]), 2)
        self.assertNotEqual(record["attempts"][0]["exit_code"], 0)
        self.assertEqual(record["attempts"][1]["exit_code"], 0)

    def test_run_quarantines_and_fails_when_post_verify_boundary_fails_twice(self) -> None:
        # Both attempts clean up before verify (or verify creates new leak each time) and exit 0
        verify_body = (
            "#!/usr/bin/env bash\n"
            "echo 'persistent leak' > outside_leak.txt\n"
            "exit 0\n"
        )
        wt_dir, wt_checkpoint = _init_team_worktree_repo(self.repo_dir, verify_body)

        class PersistentLeakAdapter(RunnerAdapter):
            def __init__(self, work_dir: Path) -> None:
                self.work_dir = work_dir
                self.maker_calls = 0
                self.checker_calls = 0

            def run_maker(self, task: Any, context: dict[str, Any]) -> tuple[int, str]:
                self.maker_calls += 1
                leak = self.work_dir / "outside_leak.txt"
                if leak.exists():
                    leak.unlink()
                (self.work_dir / "src" / "core" / "app.py").write_text(
                    f"VALUE = {self.maker_calls + 10}\n", encoding="utf-8"
                )
                return 0, f"maker attempt {self.maker_calls}"

            def run_checker(self, task: Any, diff: str, spec_content: str) -> tuple[bool, str]:
                self.checker_calls += 1
                return True, "APPROVE"

        adapter = PersistentLeakAdapter(wt_dir)
        orch = NightlyOrchestrator(repo_dir=wt_dir, runner_adapter=adapter)
        exit_code = orch.run()

        self.assertEqual(exit_code, 1)
        self.assertEqual(adapter.maker_calls, 2, "Must retry once (CORE-4) before quarantining")
        self.assertEqual(adapter.checker_calls, 0, "Checker must NOT run when verification boundary fails")

        # Evidence must NOT exist
        evidence_file = EvidenceStore(repo_dir=wt_dir).evidence_dir / "task_1.json"
        self.assertFalse(evidence_file.exists(), "Evidence must NOT exist after failure")

        # Main worktree must be cleanly restored to checkpoint
        self.assertEqual(_git(wt_dir, "rev-parse", "HEAD").stdout.strip(), wt_checkpoint)
        self.assertEqual(_git(wt_dir, "status", "--porcelain").stdout.strip(), "")
        self.assertFalse((wt_dir / "outside_leak.txt").exists())
        self.assertEqual((wt_dir / "src" / "core" / "app.py").read_text(encoding="utf-8"), "VALUE = 1\n")

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
        log_text = failure_log.read_text(encoding="utf-8")
        self.assertIn("outside_leak.txt", log_text)

    def test_cli_run_quarantines_post_verify_boundary_violation(self) -> None:
        verify_body = (
            "#!/usr/bin/env bash\n"
            "echo 'verify leak' > verify_leak.txt\n"
            "exit 0\n"
        )
        wt_dir, wt_checkpoint = _init_team_worktree_repo(self.repo_dir, verify_body)
        maker_cmd = "python3 -c \"from pathlib import Path; Path('src/core/app.py').write_text('VALUE = 50\\n')\""
        checker_cmd = "echo 'DECISION: APPROVE'"

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
        self.assertFalse((wt_dir / "verify_leak.txt").exists())
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
        self.assertIn("verify_leak.txt", failure_log.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
