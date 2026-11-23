from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.cli import main
from auto_developer.integration_gate import IntegrationJobState
from auto_developer.models import JobState
from auto_developer.multi_team import (
    WorkerRegistration,
    WorkerRegistry,
    get_process_start_identifier,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_multi_team_repo(repo_dir: Path) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\n- TEAM-13\n- TEAM-16\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Root PLAN\n\n## ステップ\n- [ ] 1. Root\n", encoding="utf-8")
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
        "# PLAN (core)\n\n## ステップ\n- [ ] 1. Core task one\n- [ ] 2. Core task two\n",
        encoding="utf-8",
    )
    (repo_dir / "plans" / "ui.md").write_text(
        "# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task one\n",
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
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def _run_cli(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing_pp}" if existing_pp else src_dir
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo_dir), *args],
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class TestTeamsPrepareCli(unittest.TestCase):
    """TEAM-13: auto-dev teams prepare [--job-id ID] [--json]。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        _init_multi_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_prepare_rejects_dirty_working_tree(self) -> None:
        (self.repo_dir / "dirty.txt").write_text("uncommitted\n", encoding="utf-8")
        proc = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-dirty")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("dirty", proc.stderr.lower())
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "ui").exists())

    def test_prepare_rejects_invalid_teams_json(self) -> None:
        bad_teams = {
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/**"], "depends_on": []},
                {"id": "ui", "owns": ["src/ui/**"], "depends_on": []},
            ],
        }
        (self.repo_dir / "teams.json").write_text(json.dumps(bad_teams), encoding="utf-8")
        _git(self.repo_dir, "add", "teams.json")
        _git(self.repo_dir, "commit", "-m", "invalid overlapping owns")

        proc = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-bad-cfg")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("TEAMS_CONFIG_ERROR:", proc.stderr)
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())

    def test_prepare_rejects_invalid_job_id(self) -> None:
        proc = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "../evil")
        self.assertEqual(proc.returncode, 2)
        self.assertIn("job", proc.stderr.lower())
        self.assertNotIn("invalid choice", proc.stderr.lower())
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())

    def test_prepare_creates_worktrees_and_outputs_json_and_text(self) -> None:
        proc = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-alpha", "--json")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout)
        self.assertEqual(payload["job_id"], "job-alpha")
        self.assertIn("worktrees", payload)
        self.assertEqual(
            Path(payload["worktrees"]["core"]).resolve(),
            (self.repo_dir / ".dev" / "worktrees" / "core").resolve(),
        )
        self.assertEqual(
            Path(payload["worktrees"]["ui"]).resolve(),
            (self.repo_dir / ".dev" / "worktrees" / "ui").resolve(),
        )
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "core").is_dir())
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "ui").is_dir())
        self.assertEqual(
            _git(self.repo_dir / ".dev" / "worktrees" / "core", "branch", "--show-current").stdout.strip(),
            "team/core",
        )
        self.assertEqual(
            _git(self.repo_dir / ".dev" / "worktrees" / "ui", "branch", "--show-current").stdout.strip(),
            "team/ui",
        )

    def test_prepare_generates_job_id_and_text_output(self) -> None:
        proc = _run_cli(self.repo_dir, "teams", "prepare")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("core", proc.stdout)
        self.assertIn("ui", proc.stdout)
        self.assertIn(str((self.repo_dir / ".dev" / "worktrees" / "core").resolve()), proc.stdout)
        self.assertIn(str((self.repo_dir / ".dev" / "worktrees" / "ui").resolve()), proc.stdout)

    def test_prepare_rejects_leftover_worktrees_or_branches_and_guides_user(self) -> None:
        first = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-1", "--json")
        self.assertEqual(first.returncode, 0, first.stderr)

        # Put a marker file inside core worktree to verify prepare does not overwrite/recreate it
        marker = self.repo_dir / ".dev" / "worktrees" / "core" / "marker.txt"
        marker.write_text("keep-me\n", encoding="utf-8")

        second = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-2")
        self.assertNotEqual(second.returncode, 0)
        combined = second.stdout + "\n" + second.stderr
        self.assertIn("teams status", combined)
        self.assertIn("teams cleanup", combined)
        self.assertIn("teams recover", combined)
        self.assertTrue(marker.exists())

        # Also test when only branch team/core remains (worktrees removed manually)
        _git(self.repo_dir, "worktree", "remove", "--force", str(self.repo_dir / ".dev" / "worktrees" / "core"))
        _git(self.repo_dir, "worktree", "remove", "--force", str(self.repo_dir / ".dev" / "worktrees" / "ui"))
        third = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-3")
        self.assertNotEqual(third.returncode, 0)
        combined3 = third.stdout + "\n" + third.stderr
        self.assertIn("teams status", combined3)
        self.assertIn("teams cleanup", combined3)
        self.assertIn("teams recover", combined3)


class TestTeamsStatusAndCleanupCli(unittest.TestCase):
    """TEAM-16: auto-dev teams status / teams cleanup。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        self.base_commit = _init_multi_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_status_reports_worktree_branch_tasks_worker_and_quarantine(self) -> None:
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-stat-1", "--json")
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"

        # Mark 1 of 2 tasks completed in core worktree and commit
        (core_wt / "plans" / "core.md").write_text(
            "# PLAN (core)\n\n## ステップ\n- [x] 1. Core task one\n- [ ] 2. Core task two\n",
            encoding="utf-8",
        )
        (core_wt / "src" / "core" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        _git(core_wt, "add", "-A")
        _git(core_wt, "commit", "-m", "feat: core task 1")

        # Create a quarantine branch for core and a failed JobState in ui worktree
        _git(core_wt, "branch", "quarantine/job-stat-1/core/2-1700000001")
        JobState(
            job_id="ui-run-1",
            status="FAILED",
            checkpoint_hash=self.base_commit,
            quarantined_task={"task_id": "1", "quarantined_branch": "quarantine/job-stat-1/ui/1-1700000002"},
        ).save(ui_wt / ".dev" / "job_state.json")
        _git(ui_wt, "branch", "quarantine/job-stat-1/ui/1-1700000002")

        # Check JSON status (without --job-id, picks latest job)
        st_json = _run_cli(self.repo_dir, "teams", "status", "--json")
        self.assertEqual(st_json.returncode, 0, st_json.stderr)
        data = json.loads(st_json.stdout)
        self.assertEqual(data["job_id"], "job-stat-1")
        by_team = {t["team_id"]: t for t in data["teams"]}

        self.assertEqual(by_team["core"]["branch"], "team/core")
        self.assertTrue(by_team["core"]["worktree_exists"])
        self.assertEqual(by_team["core"]["completed_tasks"], 1)
        self.assertEqual(by_team["core"]["total_tasks"], 2)
        self.assertIn("quarantine/job-stat-1/core/2-1700000001", by_team["core"]["quarantine_branches"])
        self.assertNotIn("quarantine/job-stat-1/ui/1-1700000002", by_team["core"]["quarantine_branches"])

        self.assertEqual(by_team["ui"]["branch"], "team/ui")
        self.assertEqual(by_team["ui"]["completed_tasks"], 0)
        self.assertEqual(by_team["ui"]["total_tasks"], 1)
        self.assertEqual(by_team["ui"]["worker_status"], "FAILED")
        self.assertIn("quarantine/job-stat-1/ui/1-1700000002", by_team["ui"]["quarantine_branches"])

        # Check human-readable text output
        st_txt = _run_cli(self.repo_dir, "teams", "status", "--job-id", "job-stat-1")
        self.assertEqual(st_txt.returncode, 0, st_txt.stderr)
        self.assertIn("core", st_txt.stdout)
        self.assertIn("team/core", st_txt.stdout)
        self.assertIn("1/2", st_txt.stdout)
        self.assertIn("quarantine/job-stat-1/core/2-1700000001", st_txt.stdout)
        self.assertIn("FAILED", st_txt.stdout)

    def test_status_unknown_job_id_returns_error(self) -> None:
        proc = _run_cli(self.repo_dir, "teams", "status", "--job-id", "does-not-exist")
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("invalid choice", proc.stderr.lower())

    def test_cleanup_succeeds_for_completed_integration_and_allows_next_prepare(self) -> None:
        job_id = "job-ok-1"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        (core_wt / "src" / "core" / "app.py").write_text("VALUE = 10\n", encoding="utf-8")
        _git(core_wt, "add", "-A")
        _git(core_wt, "commit", "-m", "core done")
        core_sha = _git(core_wt, "rev-parse", "HEAD").stdout.strip()

        (ui_wt / "src" / "ui" / "view.py").write_text("VIEW = 10\n", encoding="utf-8")
        _git(ui_wt, "add", "-A")
        _git(ui_wt, "commit", "-m", "ui done")
        ui_sha = _git(ui_wt, "rev-parse", "HEAD").stdout.strip()

        # Simulate successful integration: integration/job-ok-1 merged both and tagged rc/job-ok-1
        _git(self.repo_dir, "checkout", "-b", f"integration/{job_id}", "main")
        _git(self.repo_dir, "merge", "--no-ff", "team/core", "-m", "merge core")
        _git(self.repo_dir, "merge", "--no-ff", "team/ui", "-m", "merge ui")
        rc_sha = _git(self.repo_dir, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo_dir, "tag", f"rc/{job_id}", rc_sha)
        _git(self.repo_dir, "checkout", "main")

        # Keep a quarantine branch & evidence files to verify cleanup does NOT delete or alter them
        _git(self.repo_dir, "branch", "quarantine/core-old-1", self.base_commit)
        ev_dir = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        ev_file = ev_dir / "core.zip"
        ui_ev_file = ev_dir / "ui.zip"
        for tid, path in (("core", ev_file), ("ui", ui_ev_file)):
            with zipfile.ZipFile(path, "w") as zf:
                zf.writestr("bundle_manifest.json", json.dumps({"team_id": tid}))
        core_ev_sha_before = hashlib.sha256(ev_file.read_bytes()).hexdigest()
        ui_ev_sha_before = hashlib.sha256(ui_ev_file.read_bytes()).hexdigest()

        IntegrationJobState(
            job_id=job_id,
            state="COMPLETED",
            base_commit="main",
            integration_branch=f"integration/{job_id}",
            verified_commits={"core": core_sha, "ui": ui_sha},
            merged_teams=["core", "ui"],
            rc_commit=rc_sha,
            rc_tag=f"rc/{job_id}",
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, cln.stderr)

        # Worktrees and team branches are removed
        self.assertFalse(core_wt.exists())
        self.assertFalse(ui_wt.exists())
        self.assertEqual(_git(self.repo_dir, "branch", "--list", "team/*").stdout.strip(), "")

        # RC tag, evidence, and quarantine branches remain
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), f"rc/{job_id}")
        self.assertTrue(ev_file.exists())
        self.assertTrue(ui_ev_file.exists())
        self.assertEqual(hashlib.sha256(ev_file.read_bytes()).hexdigest(), core_ev_sha_before)
        self.assertEqual(hashlib.sha256(ui_ev_file.read_bytes()).hexdigest(), ui_ev_sha_before)
        self.assertIn("quarantine/core-old-1", _git(self.repo_dir, "branch", "--list", "quarantine/*").stdout)

        # Next prepare for a new job should now succeed cleanly
        prep2 = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-ok-2")
        self.assertEqual(prep2.returncode, 0, prep2.stderr)

    def test_cleanup_refuses_failed_or_unintegrated_job_without_force(self) -> None:
        job_id = "job-fail-1"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"

        # 1. Before any integration state exists: refuse without --force
        refuse_unintegrated = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refuse_unintegrated.returncode, 0)
        self.assertIn("--force", refuse_unintegrated.stderr)
        self.assertTrue(core_wt.exists())
        self.assertTrue(ui_wt.exists())
        self.assertIn("team/core", _git(self.repo_dir, "branch", "--list", "team/core").stdout)

        # 2. Failed integration state: refuse without --force
        IntegrationJobState(
            job_id=job_id,
            state="FAILED",
            base_commit="main",
            integration_branch=f"integration/{job_id}",
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        refuse_failed = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refuse_failed.returncode, 0)
        self.assertIn("--force", refuse_failed.stderr)
        self.assertTrue(core_wt.exists())
        self.assertTrue(ui_wt.exists())

        # 3. With --force: removes worktrees and team branches
        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertFalse(core_wt.exists())
        self.assertFalse(ui_wt.exists())
        self.assertEqual(_git(self.repo_dir, "branch", "--list", "team/*").stdout.strip(), "")

    def test_cleanup_refuses_when_worker_process_is_still_running(self) -> None:
        job_id = "job-running-1"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            start_new_session=True,
        )
        try:
            pgid = os.getpgid(proc.pid)
            WorkerRegistry(self.repo_dir, job_id).register_worker(
                WorkerRegistration(
                    team_id="core",
                    pid=proc.pid,
                    pgid=pgid,
                    worktree_path=str(core_wt),
                    base_branch="main",
                    branch_name="team/core",
                    status="RUNNING",
                    start_time=1000.0,
                    updated_at=1000.0,
                    process_start_time=get_process_start_identifier(proc.pid),
                )
            )
            IntegrationJobState(
                job_id=job_id,
                state="COMPLETED",
                base_commit="main",
                integration_branch=f"integration/{job_id}",
            ).save(self.repo_dir / ".dev" / "integration_jobs")

            refuse = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
            self.assertNotEqual(refuse.returncode, 0)
            self.assertTrue(core_wt.exists())
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_main_direct_call_teams_status_and_cleanup_unknown(self) -> None:
        self.assertNotEqual(
            main(["--work-dir", str(self.repo_dir), "teams", "cleanup", "--job-id", "no-such-job"]),
            0,
        )

    def test_prepare_rejects_uncompleted_integration_branch(self) -> None:
        _git(self.repo_dir, "branch", "integration/crashed-job")
        proc = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-new")
        self.assertNotEqual(proc.returncode, 0)
        combined = proc.stdout + "\n" + proc.stderr
        self.assertIn("teams status", combined)
        self.assertIn("teams cleanup", combined)
        self.assertIn("teams recover", combined)

    def test_status_without_job_and_stale_worker_detection(self) -> None:
        empty = _run_cli(self.repo_dir, "teams", "status", "--json")
        self.assertEqual(empty.returncode, 0, empty.stderr)
        empty_data = json.loads(empty.stdout)
        self.assertIsNone(empty_data["job_id"])
        self.assertEqual(empty_data["job_state"], "NO_JOB")

        job_id = "job-stale-1"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)
        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"

        WorkerRegistry(self.repo_dir, job_id).register_worker(
            WorkerRegistration(
                team_id="core",
                pid=999999,
                pgid=999999,
                worktree_path=str(core_wt),
                base_branch="main",
                branch_name="team/core",
                status="RUNNING",
                start_time=1000.0,
                updated_at=1000.0,
                process_start_time="stale-start-time",
            )
        )
        st = _run_cli(self.repo_dir, "teams", "status", "--job-id", job_id, "--json")
        self.assertEqual(st.returncode, 0, st.stderr)
        by_team = {t["team_id"]: t for t in json.loads(st.stdout)["teams"]}
        self.assertEqual(by_team["core"]["worker_status"], "STALE")

    def test_cleanup_refuses_dirty_worktree_on_completed_job_without_force(self) -> None:
        job_id = "job-dirty-wt"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        (core_wt / "src" / "core" / "app.py").write_text("VALUE = 50\n", encoding="utf-8")

        _git(self.repo_dir, "tag", f"rc/{job_id}", self.base_commit)
        IntegrationJobState(
            job_id=job_id,
            state="COMPLETED",
            base_commit="main",
            integration_branch=f"integration/{job_id}",
            rc_commit=self.base_commit,
            rc_tag=f"rc/{job_id}",
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("uncommitted", refused.stderr.lower())
        self.assertTrue(core_wt.exists())

        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertFalse(core_wt.exists())
        q_branches = _git(self.repo_dir, "branch", "--list", f"quarantine/{job_id}/core/*").stdout.splitlines()
        self.assertTrue(len(q_branches) > 0)
        q_name = q_branches[-1].strip().lstrip("* ").strip()
        self.assertEqual(_git(self.repo_dir, "show", f"{q_name}:src/core/app.py").stdout, "VALUE = 50\n")

    def test_cleanup_force_quarantines_unmerged_commits_and_uncommitted_excluding_secrets(self) -> None:
        job_id = "job-quarantine-inv3-inv5"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        # Committed unmerged work on team/core
        (core_wt / "src" / "core" / "app.py").write_text("VALUE = 77\n", encoding="utf-8")
        _git(core_wt, "add", "src/core/app.py")
        _git(core_wt, "commit", "-m", "unmerged core work")

        # Additional uncommitted changes + new untracked file + secret files (INV-5)
        (core_wt / "src" / "core" / "app.py").write_text("VALUE = 88\n", encoding="utf-8")
        (core_wt / "src" / "core" / "wip.py").write_text("WIP = True\n", encoding="utf-8")
        (core_wt / ".env").write_text("API_SECRET=topsecret\n", encoding="utf-8")
        (core_wt / "server.key").write_text("PRIVATE KEY DATA\n", encoding="utf-8")
        (core_wt / "cert.pem").write_text("PEM DATA\n", encoding="utf-8")

        IntegrationJobState(
            job_id=job_id,
            state="FAILED",
            base_commit="main",
            integration_branch=f"integration/{job_id}",
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)
        self.assertFalse(core_wt.exists())
        self.assertEqual(_git(self.repo_dir, "branch", "--list", "team/*").stdout.strip(), "")

        q_branches = [
            b.strip().lstrip("* ").strip()
            for b in _git(self.repo_dir, "branch", "--list", f"quarantine/{job_id}/core/*").stdout.splitlines()
            if b.strip()
        ]
        self.assertTrue(len(q_branches) > 0, "Expected quarantine branch for core")
        q_branch = q_branches[-1]

        # Failed committed + uncommitted work is restorable from quarantine branch
        self.assertEqual(_git(self.repo_dir, "show", f"{q_branch}:src/core/app.py").stdout, "VALUE = 88\n")
        self.assertEqual(_git(self.repo_dir, "show", f"{q_branch}:src/core/wip.py").stdout, "WIP = True\n")

        # Secret files must NOT be in the quarantine branch (INV-5)
        q_files = set(_git(self.repo_dir, "ls-tree", "-r", "--name-only", q_branch).stdout.splitlines())
        self.assertNotIn(".env", q_files)
        self.assertNotIn("server.key", q_files)
        self.assertNotIn("cert.pem", q_files)

    def test_cleanup_force_aborts_if_quarantine_fails(self) -> None:
        job_id = "job-q-abort"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        (core_wt / "src" / "core" / "app.py").write_text("VALUE = 99\n", encoding="utf-8")
        _git(core_wt, "add", "src/core/app.py")
        _git(core_wt, "commit", "-m", "unmerged core commit")

        with mock.patch(
            "auto_developer.multi_team._quarantine_team_before_cleanup",
            side_effect=RuntimeError("simulated quarantine failure"),
        ):
            code = main(["--work-dir", str(self.repo_dir), "teams", "cleanup", "--job-id", job_id, "--force"])

        self.assertNotEqual(code, 0)
        self.assertTrue(core_wt.exists())
        self.assertIn("team/core", _git(self.repo_dir, "branch", "--list", "team/core").stdout)

    def test_cleanup_does_not_delete_worktrees_outside_target_job(self) -> None:
        job_id = "job-scoped-1"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        # Create an unrelated worktree not belonging to job_id
        extra_wt = self.repo_dir / ".dev" / "worktrees" / "extra"
        _git(self.repo_dir, "worktree", "add", "-b", "feature/extra", str(extra_wt), "main")

        IntegrationJobState(
            job_id=job_id,
            state="FAILED",
            base_commit="main",
            integration_branch=f"integration/{job_id}",
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(forced.returncode, 0, forced.stderr)

        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "ui").exists())
        self.assertTrue(extra_wt.exists())
        self.assertIn("feature/extra", _git(self.repo_dir, "branch", "--list", "feature/extra").stdout)

    def test_cleanup_refuses_completed_job_when_rc_ref_unresolvable(self) -> None:
        job_id = "job-no-rc"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        IntegrationJobState(
            job_id=job_id,
            state="COMPLETED",
            base_commit="main",
            integration_branch=f"integration/{job_id}",
            rc_commit=None,
            rc_tag=None,
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("rc", refused.stderr.lower())
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "core").exists())
        self.assertIn("team/core", _git(self.repo_dir, "branch", "--list", "team/core").stdout)


if __name__ == "__main__":
    unittest.main()
