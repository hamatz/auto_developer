from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.guard import GitGuard
from auto_developer.manifest import TeamConfig, TeamManifest
from auto_developer.models import JobState
from auto_developer.multi_team import (
    TeamExecutionResult,
    _format_worker_failure_message,
)
from auto_developer.orchestrator import NightlyOrchestrator

REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    src_dir = str(REPO_ROOT / "src")
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else src_dir
    return env


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo: Path) -> str:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")
    (repo / "README.md").write_text("# Test\n", encoding="utf-8")
    (repo / "PLAN.md").write_text(
        "# PLAN.md\n\n## ステップ\n- [ ] 1. Sample task\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(".dev/\nlogs/\n__pycache__/\n*.pyc\n.DS_Store\n", encoding="utf-8")
    _git(repo, "add", "README.md", "PLAN.md", ".gitignore")
    _git(repo, "commit", "-m", "chore: initial")
    return _git(repo, "rev-parse", "HEAD")


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", *args],
        cwd=repo,
        env=_clean_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


class TestRcv6QuarantineSnapshotListing(unittest.TestCase):
    def test_rollback_step_lists_snapshot_only_new_files_and_writes_failure_log(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)

            # Tracked modification + untracked new test file + dotfile + ignored + cache + secrets
            (repo / "README.md").write_text("# Modified\n", encoding="utf-8")
            (repo / "tests").mkdir(parents=True, exist_ok=True)
            (repo / "tests" / "test_new_feature.py").write_text(
                "def test_ok():\n    assert True\n",
                encoding="utf-8",
            )
            (repo / ".custom").mkdir(parents=True, exist_ok=True)
            (repo / ".custom" / "new_cfg.yml").write_text("enabled: true\n", encoding="utf-8")
            (repo / ".DS_Store").write_bytes(b"\x00\x01dsstore")
            (repo / "logs" / "nightly").mkdir(parents=True, exist_ok=True)
            (repo / "logs" / "nightly" / "old.log").write_text("old log\n", encoding="utf-8")
            (repo / "__pycache__").mkdir(parents=True, exist_ok=True)
            (repo / "__pycache__" / "mod.cpython-310.pyc").write_bytes(b"pycdata")
            (repo / ".env").write_text("API_KEY=super_secret_12345\n", encoding="utf-8")
            (repo / "server.pem").write_text("-----BEGIN " + "PRIVATE KEY-----\nsecret\n", encoding="utf-8")
            (repo / "credentials.json").write_text('{"token": "secret"}\n', encoding="utf-8")

            proc = _run_cli(
                repo,
                "rollback-step",
                "--checkpoint",
                checkpoint,
                "--task-id",
                "1",
                "--reason",
                "Checker rejected twice",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

            lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
            self.assertGreaterEqual(len(lines), 2, proc.stdout)
            # 1st line must remain unchanged
            self.assertRegex(
                lines[0],
                rf"^ROLLED BACK TO {checkpoint} \(Work quarantined to: quarantine/1-\d+\)$",
            )
            # Output lists the new untracked files (including dotfile), excludes tracked .gitignore, ignored, cache, and secrets
            self.assertIn("tests/test_new_feature.py", proc.stdout)
            self.assertIn(".custom/new_cfg.yml", proc.stdout)
            self.assertNotIn(".gitignore", proc.stdout)
            self.assertNotIn(".DS_Store", proc.stdout)
            self.assertNotIn("old.log", proc.stdout)
            self.assertNotIn("__pycache__", proc.stdout)
            # Secret file contents must never appear in stdout
            self.assertNotIn("super_secret_12345", proc.stdout)
            self.assertNotIn("BEGIN PRIVATE KEY", proc.stdout)
            self.assertNotIn('"token": "secret"', proc.stdout)

            # Must show tar xzf command that actually restores both non-secret files (including dotfile)
            tar_lines = [ln for ln in proc.stdout.splitlines() if "tar xzf " in ln]
            self.assertEqual(len(tar_lines), 1, proc.stdout)
            tar_cmd_str = tar_lines[0].split("tar xzf ", 1)[1]
            tar_args = shlex.split(f"tar xzf {tar_cmd_str}")
            self.assertIn("tests/test_new_feature.py", tar_args)
            self.assertIn(".custom/new_cfg.yml", tar_args)
            self.assertNotIn(".env", tar_args)
            self.assertNotIn("server.pem", tar_args)
            self.assertNotIn("credentials.json", tar_args)

            # New files were removed by rollback, then restored by running the tar command
            self.assertFalse((repo / "tests" / "test_new_feature.py").exists())
            self.assertFalse((repo / ".custom" / "new_cfg.yml").exists())
            subprocess.run(tar_args, cwd=repo, check=True)
            self.assertTrue((repo / "tests" / "test_new_feature.py").is_file())
            self.assertTrue((repo / ".custom" / "new_cfg.yml").is_file())
            self.assertEqual((repo / ".custom" / "new_cfg.yml").read_text(encoding="utf-8"), "enabled: true\n")

            # Failure log must keep first 2 lines and include snapshot-only info
            log_path = repo / "logs" / "nightly" / "task_1_failure.log"
            self.assertTrue(log_path.is_file())
            log_lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(log_lines[0], "Failure Phase: ROLLBACK")
            self.assertEqual(log_lines[1], "Reason: Checker rejected twice")
            log_text = "\n".join(log_lines)
            self.assertIn("tests/test_new_feature.py", log_text)
            self.assertIn("tar xzf ", log_text)

    def test_rollback_step_when_no_snapshot_only_files_shows_single_line_none(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)

            # Only tracked file modified (goes into quarantine branch, not snapshot-only)
            (repo / "README.md").write_text("# Tracked change only\n", encoding="utf-8")

            proc = _run_cli(
                repo,
                "rollback-step",
                "--checkpoint",
                checkpoint,
                "--task-id",
                "1",
                "--reason",
                "Tracked only failure",
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)
            lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
            self.assertRegex(
                lines[0],
                rf"^ROLLED BACK TO {checkpoint} \(Work quarantined to: quarantine/1-\d+\)$",
            )
            self.assertEqual(len(lines), 2, proc.stdout)
            self.assertIn("none", lines[1].lower())
            self.assertNotIn("tar xzf", proc.stdout)

            log_path = repo / "logs" / "nightly" / "task_1_failure.log"
            log_text = log_path.read_text(encoding="utf-8")
            self.assertTrue(log_text.splitlines()[0].startswith("Failure Phase: ROLLBACK"))
            self.assertTrue(log_text.splitlines()[1].startswith("Reason: Tracked only failure"))
            self.assertNotIn("tar xzf", log_text)
            self.assertIn("Snapshot-only", log_text)

    def test_orchestrator_handle_failure_prints_and_logs_snapshot_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)
            verify_sh = repo / "verify.sh"
            verify_sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            _git(repo, "add", "verify.sh")
            _git(repo, "commit", "-m", "chore: add verify")
            checkpoint = _git(repo, "rev-parse", "HEAD")

            # Simulate task leaving an untracked file
            (repo / "new_test.py").write_text("assert 1 == 1\n", encoding="utf-8")

            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                verify_script=verify_sh,
            )
            task = orch.dispatcher.parse_plan(repo / "PLAN.md")[0]
            job_state = JobState(job_id="job-rcv6", status="RUNNING", base_branch="main")

            import io
            from contextlib import redirect_stdout

            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = orch._handle_failure(
                    task=task,
                    checkpoint=checkpoint,
                    total_tasks=1,
                    succeeded_tasks=[],
                    job_state=job_state,
                    phase="VERIFY",
                    reason="Verification failed",
                )
            self.assertEqual(rc, 1)
            out = buf.getvalue()
            self.assertIn("new_test.py", out)
            self.assertIn("tar xzf ", out)

            log_path = repo / "logs" / "nightly" / "task_1_failure.log"
            log_text = log_path.read_text(encoding="utf-8")
            self.assertTrue(log_text.splitlines()[0].startswith("Failure Phase: VERIFY"))
            self.assertTrue(log_text.splitlines()[1].startswith("Reason: Verification failed"))
            self.assertIn("new_test.py", log_text)
            self.assertIn("tar xzf ", log_text)

    def test_teams_run_failure_message_includes_snapshot_only_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            checkpoint = _init_repo(wt)
            (wt / "team_new_file.py").write_text("x = 1\n", encoding="utf-8")

            guard = GitGuard(repo_dir=wt)
            q_branch = guard.quarantine_and_restore(
                checkpoint=checkpoint,
                task_id="1",
                job_id="job-team-rcv6",
                team_id="core",
            )
            self.assertTrue(q_branch)
            snap = guard.last_quarantine_snapshot
            self.assertIsNotNone(snap)
            self.assertEqual(guard.last_snapshot_only_files, ["team_new_file.py"])

            js = JobState(
                job_id="job-team-rcv6",
                status="FAILED",
                base_branch="team/core",
                quarantined_task={
                    "task_id": "1",
                    "phase": "VERIFY",
                    "quarantined_branch": q_branch,
                    "snapshot_path": str(snap),
                    "snapshot_only_files": list(guard.last_snapshot_only_files),
                },
            )
            js.save(wt / ".dev" / "job_state.json")

            manifest = TeamManifest(
                version=1,
                project="test",
                teams=[
                    TeamConfig(
                        id="core",
                        plan="PLAN.md",
                        owns=["src/**"],
                    )
                ],
            )
            results = {
                "core": TeamExecutionResult(
                    team_id="core",
                    branch="team/core",
                    success=False,
                    status="FAILED",
                    details={"error": "Task 1 failed"},
                )
            }
            msg = _format_worker_failure_message(
                "job-team-rcv6",
                manifest,
                results,
                ["core"],
                {"core": wt},
            )
            self.assertIn("team_new_file.py", msg)
            self.assertIn("tar xzf ", msg)

    def test_list_snapshot_only_files_preserves_dotfiles_and_excludes_secrets(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            (repo / ".custom").mkdir(parents=True, exist_ok=True)
            (repo / ".custom" / "new_cfg.yml").write_text("a: 1\n", encoding="utf-8")
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "new_mod.py").write_text("x = 1\n", encoding="utf-8")
            (repo / ".env").write_text("SECRET=1\n", encoding="utf-8")
            (repo / "certs").mkdir(parents=True, exist_ok=True)
            (repo / "certs" / "server.pem").write_text("pem\n", encoding="utf-8")
            (repo / "config").mkdir(parents=True, exist_ok=True)
            (repo / "config" / "credentials.json").write_text("{}\n", encoding="utf-8")
            (repo / ".DS_Store").write_bytes(b"\x00\x01")
            (repo / "logs" / "nightly").mkdir(parents=True, exist_ok=True)
            (repo / "logs" / "nightly" / "old.log").write_text("log\n", encoding="utf-8")
            (repo / "__pycache__").mkdir(parents=True, exist_ok=True)
            (repo / "__pycache__" / "x.pyc").write_bytes(b"pyc")

            guard = GitGuard(repo_dir=repo)
            only_files = guard.list_snapshot_only_files()
            self.assertEqual(only_files, [".custom/new_cfg.yml", "src/new_mod.py"])

    def test_quarantine_and_restore_recovers_branch_and_tree_when_listing_raises(self) -> None:
        from unittest.mock import patch

        from auto_developer.guard import format_snapshot_only_files_lines

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)
            (repo / "README.md").write_text("# Modified\n", encoding="utf-8")
            (repo / "new_file.py").write_text("x = 1\n", encoding="utf-8")

            guard = GitGuard(repo_dir=repo)
            with patch.object(guard, "list_snapshot_only_files", side_effect=RuntimeError("listing exploded")):
                q_branch = guard.quarantine_and_restore(checkpoint=checkpoint, task_id="1")

            self.assertTrue(q_branch)
            self.assertEqual(guard.get_current_branch(), "main")
            self.assertEqual(_git(repo, "status", "--porcelain"), "")
            self.assertEqual(guard.last_snapshot_only_files, [])
            self.assertIsNotNone(guard.last_snapshot_only_error)
            self.assertIn("listing exploded", guard.last_snapshot_only_error or "")
            lines = format_snapshot_only_files_lines(
                guard.last_quarantine_snapshot,
                guard.last_snapshot_only_files,
                repo_dir=repo,
                error_reason=guard.last_snapshot_only_error,
            )
            self.assertEqual(len(lines), 1)
            self.assertTrue(lines[0].startswith("Snapshot-only new files: unavailable ("))
            self.assertIn("listing exploded", lines[0])

    def test_orchestrator_handle_failure_aborts_without_cleaning_when_git_ls_files_fails(self) -> None:
        import io
        from contextlib import redirect_stdout
        from unittest.mock import patch

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            verify_sh = repo / "verify.sh"
            verify_sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            _git(repo, "add", "verify.sh")
            _git(repo, "commit", "-m", "chore: add verify")
            checkpoint = _git(repo, "rev-parse", "HEAD")

            (repo / "new_test.py").write_text("assert 1 == 1\n", encoding="utf-8")

            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                verify_script=verify_sh,
            )
            task = orch.dispatcher.parse_plan(repo / "PLAN.md")[0]
            job_state = JobState(job_id="job-rcv6-err", status="RUNNING", base_branch="main")

            orig_run_git = orch.git_guard._run_git

            def failing_ls_files(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
                if "ls-files" in args:
                    raise subprocess.CalledProcessError(128, ["git", *args], stderr="fatal: ls-files failed")
                return orig_run_git(args, env=env)

            buf = io.StringIO()
            with patch.object(orch.git_guard, "_run_git", side_effect=failing_ls_files), redirect_stdout(buf):
                rc = orch._handle_failure(
                    task=task,
                    checkpoint=checkpoint,
                    total_tasks=1,
                    succeeded_tasks=[],
                    job_state=job_state,
                    phase="VERIFY",
                    reason="Verification failed",
                )

            self.assertEqual(rc, 1)
            self.assertEqual(orch.git_guard.get_current_branch(), "main")
            self.assertTrue((repo / "new_test.py").exists())
            self.assertEqual(_git(repo, "branch", "--list", "quarantine/*"), "")
            out = buf.getvalue()
            self.assertIn("QUARANTINE ERROR:", out)
            self.assertIn("ls-files failed", out)

            log_path = repo / "logs" / "nightly" / "task_1_failure.log"
            log_lines = log_path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(log_lines[0], "Failure Phase: VERIFY")
            self.assertTrue(log_lines[1].startswith("Reason: Verification failed"))
            self.assertIn("ls-files failed", "\n".join(log_lines))

    def test_quarantine_and_restore_succeeds_when_existing_manifest_is_corrupt(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)
            q_dir = repo / ".dev" / "quarantines"
            q_dir.mkdir(parents=True, exist_ok=True)
            (q_dir / "manifest_task_bad_utf8.json").write_bytes(b"\xff\xfe\xfd")
            (q_dir / "manifest_task_bad_list.json").write_text("[1, 2, 3]", encoding="utf-8")

            (repo / "README.md").write_text("# Modified\n", encoding="utf-8")
            (repo / "fresh_file.py").write_text("y = 2\n", encoding="utf-8")

            guard = GitGuard(repo_dir=repo)
            q_branch = guard.quarantine_and_restore(checkpoint=checkpoint, task_id="1")
            self.assertTrue(q_branch)
            self.assertEqual(guard.get_current_branch(), "main")
            self.assertEqual(_git(repo, "status", "--porcelain"), "")
            self.assertIsNone(guard.last_snapshot_only_error)
            self.assertEqual(guard.last_snapshot_only_files, ["fresh_file.py"])


if __name__ == "__main__":
    unittest.main()
