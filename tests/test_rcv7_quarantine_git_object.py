from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.cli import main as cli_main
from auto_developer.guard import GitGuard, QuarantineObjectError
from auto_developer.manifest import TeamConfig, TeamManifest
from auto_developer.models import JobState
from auto_developer.multi_team import TeamExecutionResult, _format_worker_failure_message
from auto_developer.orchestrator import NightlyOrchestrator

# 偽の秘密鍵の目印は、実行時に組み立てる（ソースに続けて書くと、CI の gitleaks が本物の秘密鍵と誤検知する）。
_FAKE_KEY_BEGIN = "-----BEGIN " + "PRIVATE KEY-----"
_FAKE_KEY_END = "-----END " + "PRIVATE KEY-----"

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


class TestRcv7QuarantineGitObject(unittest.TestCase):
    def test_rollback_step_preserves_secret_content_and_env_as_git_objects(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)

            fake_key_content = (
                "# test fixture\n"
                f"FAKE_KEY = '{_FAKE_KEY_BEGIN}\\nMIIEvQIBADANBgkqhkiG9w0BAQEF\\n{_FAKE_KEY_END}'\n"
            )
            env_content = "SECRET_TOKEN=top_secret_value_999\n"

            (repo / "tests").mkdir(parents=True, exist_ok=True)
            (repo / "tests" / "test_key_fixture.py").write_text(fake_key_content, encoding="utf-8")
            (repo / ".env").write_text(env_content, encoding="utf-8")
            (repo / "normal_new.py").write_text("x = 1\n", encoding="utf-8")

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
            self.assertRegex(
                lines[0],
                rf"^ROLLED BACK TO {checkpoint} \(Work quarantined to: quarantine/1-\d+\)$",
            )

            # Working tree was cleaned
            self.assertFalse((repo / "tests" / "test_key_fixture.py").exists())
            self.assertFalse((repo / ".env").exists())
            self.assertFalse((repo / "normal_new.py").exists())

            # Snapshot-only listing and tar xzf must ONLY include normal_new.py
            tar_lines = [ln for ln in proc.stdout.splitlines() if "tar xzf " in ln]
            self.assertEqual(len(tar_lines), 1, proc.stdout)
            tar_args = shlex.split("tar xzf " + tar_lines[0].split("tar xzf ", 1)[1])
            self.assertIn("normal_new.py", tar_args)
            self.assertNotIn("tests/test_key_fixture.py", tar_args)
            self.assertNotIn(".env", tar_args)

            # Snapshot archive must NOT contain secret files
            snap_files = list((repo / ".dev" / "quarantines").glob("snapshot_task_*.tar.gz"))
            self.assertEqual(len(snap_files), 1)
            with tarfile.open(snap_files[0], "r:gz") as tf:
                names = {m.name for m in tf.getmembers()}
                self.assertIn("normal_new.py", names)
                self.assertNotIn("tests/test_key_fixture.py", names)
                self.assertNotIn(".env", names)

            # Secret contents must NOT be leaked in stdout or failure log
            self.assertNotIn("MIIEvQIBADANBgkqhkiG9w0BAQEF", proc.stdout)
            self.assertNotIn("top_secret_value_999", proc.stdout)

            # Output must guide git cat-file -p <SHA> > <path> for both files
            cat_matches = re.findall(
                r"git cat-file -p ([0-9a-f]{40,64}) > ([^\s]+)",
                proc.stdout,
            )
            recovered_by_path = {path: sha for sha, path in cat_matches}
            self.assertIn("tests/test_key_fixture.py", recovered_by_path, proc.stdout)
            self.assertIn(".env", recovered_by_path, proc.stdout)

            # Verify git cat-file -p actually recovers exact original content
            self.assertEqual(
                _git(repo, "cat-file", "-p", recovered_by_path["tests/test_key_fixture.py"]) + "\n",
                fake_key_content,
            )
            self.assertEqual(
                _git(repo, "cat-file", "-p", recovered_by_path[".env"]) + "\n",
                env_content,
            )

            # Manifest records path and object SHA without content
            manifests = list((repo / ".dev" / "quarantines").glob("manifest_task_*.json"))
            self.assertEqual(len(manifests), 1)
            manifest_text = manifests[0].read_text(encoding="utf-8")
            self.assertNotIn("MIIEvQIBADANBgkqhkiG9w0BAQEF", manifest_text)
            self.assertNotIn("top_secret_value_999", manifest_text)
            manifest_data = json.loads(manifest_text)
            git_objs = {item["path"]: item["sha"] for item in manifest_data.get("git_object_files", [])}
            self.assertEqual(git_objs.get("tests/test_key_fixture.py"), recovered_by_path["tests/test_key_fixture.py"])
            self.assertEqual(git_objs.get(".env"), recovered_by_path[".env"])

            # Failure log keeps first 2 lines and includes git cat-file -p guidance
            log_path = repo / "logs" / "nightly" / "task_1_failure.log"
            log_text = log_path.read_text(encoding="utf-8")
            log_lines = log_text.splitlines()
            self.assertEqual(log_lines[0], "Failure Phase: ROLLBACK")
            self.assertEqual(log_lines[1], "Reason: Checker rejected twice")
            self.assertIn(f"git cat-file -p {recovered_by_path['tests/test_key_fixture.py']} > tests/test_key_fixture.py", log_text)
            self.assertIn(f"git cat-file -p {recovered_by_path['.env']} > .env", log_text)
            self.assertNotIn("MIIEvQIBADANBgkqhkiG9w0BAQEF", log_text)
            self.assertNotIn("top_secret_value_999", log_text)

    def test_quarantine_aborts_without_deleting_when_hash_object_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)
            (repo / "README.md").write_text("# Modified\n", encoding="utf-8")
            secret_file = repo / "tests" / "test_secret.py"
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.write_text(f"KEY = '{_FAKE_KEY_BEGIN}'\n", encoding="utf-8")

            guard = GitGuard(repo_dir=repo)
            orig_run_git = guard._run_git

            def failing_hash_object(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
                if "hash-object" in args:
                    raise subprocess.CalledProcessError(1, ["git", *args], stderr="fatal: object write denied")
                return orig_run_git(args, env=env)

            with (
                patch.object(guard, "_run_git", side_effect=failing_hash_object),
                self.assertRaises(QuarantineObjectError) as ctx,
            ):
                guard.quarantine_and_restore(checkpoint=checkpoint, task_id="1")

            self.assertIn("tests/test_secret.py", str(ctx.exception))
            self.assertIn("object write denied", str(ctx.exception))
            # Working tree must remain intact, no quarantine branch created
            self.assertTrue(secret_file.is_file())
            self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "# Modified\n")
            self.assertEqual(_git(repo, "branch", "--list", "quarantine/*"), "")

    def test_quarantine_aborts_without_deleting_when_ls_files_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)
            (repo / "README.md").write_text("# Modified\n", encoding="utf-8")
            new_file = repo / "untracked_secret.py"
            new_file.write_text(f"KEY = '{_FAKE_KEY_BEGIN}'\n", encoding="utf-8")

            guard = GitGuard(repo_dir=repo)
            orig_run_git = guard._run_git

            def failing_ls_files(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
                if "ls-files" in args:
                    raise subprocess.CalledProcessError(128, ["git", *args], stderr="fatal: ls-files failed")
                return orig_run_git(args, env=env)

            with (
                patch.object(guard, "_run_git", side_effect=failing_ls_files),
                self.assertRaises(QuarantineObjectError) as ctx,
            ):
                guard.quarantine_and_restore(checkpoint=checkpoint, task_id="1")

            self.assertIn("ls-files", str(ctx.exception))
            self.assertTrue(new_file.is_file())
            self.assertEqual((repo / "README.md").read_text(encoding="utf-8"), "# Modified\n")
            self.assertEqual(_git(repo, "branch", "--list", "quarantine/*"), "")

    def test_rollback_step_cli_exits_1_without_deleting_when_hash_object_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(repo)
            secret_file = repo / "tests" / "test_key_cli.py"
            secret_file.parent.mkdir(parents=True, exist_ok=True)
            secret_file.write_text(f"KEY = '{_FAKE_KEY_BEGIN}'\n", encoding="utf-8")

            orig_run_git = GitGuard._run_git

            def failing_hash_object(
                self: GitGuard,
                args: list[str],
                env: dict[str, str] | None = None,
            ) -> subprocess.CompletedProcess[str]:
                if "hash-object" in args:
                    raise subprocess.CalledProcessError(1, ["git", *args], stderr="permission denied")
                return orig_run_git(self, args, env=env)

            with patch.object(GitGuard, "_run_git", failing_hash_object):
                rc = cli_main([
                    "--work-dir",
                    str(repo),
                    "rollback-step",
                    "--checkpoint",
                    checkpoint,
                    "--task-id",
                    "1",
                    "--reason",
                    "Test abort",
                ])

            self.assertEqual(rc, 1)
            self.assertTrue(secret_file.is_file())
            self.assertEqual(_git(repo, "branch", "--list", "quarantine/*"), "")
            log_text = (repo / "logs" / "nightly" / "task_1_failure.log").read_text(encoding="utf-8")
            self.assertIn("permission denied", log_text)

    def test_orchestrator_handle_failure_exits_1_without_cleaning_when_hash_object_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            verify_sh = repo / "verify.sh"
            verify_sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            _git(repo, "add", "verify.sh")
            _git(repo, "commit", "-m", "chore: add verify")
            checkpoint = _git(repo, "rev-parse", "HEAD")

            secret_file = repo / "leak.py"
            secret_file.write_text(f"KEY = '{_FAKE_KEY_BEGIN}'\n", encoding="utf-8")

            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                verify_script=verify_sh,
            )
            task = orch.dispatcher.parse_plan(repo / "PLAN.md")[0]
            job_state = JobState(job_id="job-rcv7-fail", status="RUNNING", base_branch="main")

            orig_run_git = orch.git_guard._run_git

            def failing_hash_object(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
                if "hash-object" in args:
                    raise subprocess.CalledProcessError(1, ["git", *args], stderr="disk full")
                return orig_run_git(args, env=env)

            with patch.object(orch.git_guard, "_run_git", side_effect=failing_hash_object):
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
            self.assertTrue(secret_file.is_file())
            self.assertEqual(_git(repo, "branch", "--list", "quarantine/*"), "")
            log_text = (repo / "logs" / "nightly" / "task_1_failure.log").read_text(encoding="utf-8")
            self.assertIn("disk full", log_text)

    def test_teams_run_failure_message_includes_git_object_recovery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            checkpoint = _init_repo(wt)
            (wt / "team_secret.py").write_text(f"K = '{_FAKE_KEY_BEGIN}'\n", encoding="utf-8")

            guard = GitGuard(repo_dir=wt)
            q_branch = guard.quarantine_and_restore(
                checkpoint=checkpoint,
                task_id="1",
                job_id="job-team-rcv7",
                team_id="core",
            )
            self.assertTrue(q_branch)
            self.assertEqual(guard.last_snapshot_only_files, [])
            self.assertEqual(len(guard.last_git_object_files), 1)
            obj = guard.last_git_object_files[0]
            self.assertEqual(obj["path"], "team_secret.py")

            js = JobState(
                job_id="job-team-rcv7",
                status="FAILED",
                base_branch="team/core",
                quarantined_task={
                    "task_id": "1",
                    "phase": "VERIFY",
                    "quarantined_branch": q_branch,
                    "snapshot_path": str(guard.last_quarantine_snapshot),
                    "snapshot_only_files": [],
                    "git_object_files": list(guard.last_git_object_files),
                },
            )
            js.save(wt / ".dev" / "job_state.json")

            manifest = TeamManifest(
                version=1,
                project="test",
                teams=[TeamConfig(id="core", plan="PLAN.md", owns=["src/**"])],
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
                "job-team-rcv7",
                manifest,
                results,
                ["core"],
                {"core": wt},
            )
            self.assertIn(f"git cat-file -p {obj['sha']} > team_secret.py", msg)


if __name__ == "__main__":
    unittest.main()
