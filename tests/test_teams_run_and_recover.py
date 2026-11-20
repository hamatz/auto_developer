from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.cli import main
from auto_developer.integration_gate import IntegrationJobState
from auto_developer.multi_team import (
    MultiTeamOrchestrator,
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


def _init_multi_team_repo(
    repo_dir: Path,
    verify_script_body: str = "#!/usr/bin/env bash\nexit 0\n",
) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\n- TEAM-15\n- TEAM-16\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Root PLAN\n\n## ステップ\n- [ ] 1. Root\n", encoding="utf-8")
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    verify_sh = repo_dir / "scripts" / "verify_local.sh"
    verify_sh.write_text(verify_script_body, encoding="utf-8")
    verify_sh.chmod(0o755)

    (repo_dir / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo_dir / "src" / "ui" / "view.py").write_text("VIEW = 1\n", encoding="utf-8")

    (repo_dir / "plans").mkdir(parents=True, exist_ok=True)
    (repo_dir / "plans" / "core.md").write_text(
        "# PLAN (core)\n\n## ステップ\n- [ ] 1. Core task one\n",
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


def _complete_task_via_lowlevel_cli(
    wt_dir: Path,
    task_id: str,
    rel_file: str,
    new_content: str,
) -> None:
    pf = _run_cli(wt_dir, "pre-flight")
    if pf.returncode != 0:
        raise AssertionError(f"pre-flight failed in {wt_dir}: {pf.stderr}")
    checkpoint = json.loads(pf.stdout)["checkpoint"]

    target = wt_dir / rel_file
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(new_content, encoding="utf-8")

    vs = _run_cli(wt_dir, "verify-step", "--checkpoint", checkpoint, "--task-id", task_id)
    if vs.returncode != 0:
        raise AssertionError(f"verify-step failed in {wt_dir}: {vs.stderr}\n{vs.stdout}")

    cs = _run_cli(
        wt_dir,
        "commit-step",
        "--task-id",
        task_id,
        "--checkpoint",
        checkpoint,
        "--maker-model",
        "gemini-3.8-flash",
        "--checker-model",
        "claude-opus-5.5",
        "--checker-decision",
        "APPROVE",
        "--checker-reason",
        "Looks good",
    )
    if cs.returncode != 0:
        raise AssertionError(f"commit-step failed in {wt_dir}: {cs.stderr}\n{cs.stdout}")


def _is_process_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return True


class TestTeamsRunCli(unittest.TestCase):
    """TEAM-15: auto-dev teams run。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_tmp = Path(self.tmp_dir.name)
        self.repo_dir = self.base_tmp / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.base_tmp / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.base_commit = _init_multi_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_teams_run_end_to_end_prepares_runs_workers_and_integrates(self) -> None:
        maker_script = self.scripts_dir / "maker_ok.py"
        maker_script.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / 'app.py').write_text('VALUE = 42\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    (cwd / 'src' / 'ui' / 'view.py').write_text('VIEW = 99\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        checker_cmd = "printf 'DECISION: APPROVE\\n'"
        maker_cmd = f"{sys.executable} {maker_script}"

        proc = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            "job-run-ok",
            "--maker-cmd",
            maker_cmd,
            "--checker-cmd",
            checker_cmd,
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")

        rc_tag = "rc/job-run-ok"
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), rc_tag)
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), self.base_commit)
        self.assertEqual(_git(self.repo_dir, "show", f"{rc_tag}:src/core/app.py").stdout, "VALUE = 42\n")
        self.assertEqual(_git(self.repo_dir, "show", f"{rc_tag}:src/ui/view.py").stdout, "VIEW = 99\n")

        workers = WorkerRegistry(self.repo_dir, "job-run-ok").load()
        self.assertEqual(set(workers.keys()), {"core", "ui"})
        for tid in ("core", "ui"):
            self.assertEqual(workers[tid].status, "COMPLETED")
            self.assertGreater(workers[tid].pid, 0)
            self.assertGreater(workers[tid].pgid, 0)

        self.assertTrue((self.repo_dir / ".dev" / "integration_jobs" / "job-run-ok" / "evidence" / "core.zip").is_file())
        self.assertTrue((self.repo_dir / ".dev" / "integration_jobs" / "job-run-ok" / "evidence" / "ui.zip").is_file())
        self.assertIn(rc_tag, proc.stdout)

    def test_teams_run_forwards_worker_flags_and_default_timeout(self) -> None:
        captured_calls: list[dict[str, object]] = []

        def fake_run_workers(
            _self: MultiTeamOrchestrator,
            config: object,
            worker_cmd_factory: object = None,
            worker_fn: object = None,
            timeout_sec: float = 120.0,
        ) -> dict[str, object]:
            captured_calls.append(
                {
                    "timeout_sec": timeout_sec,
                    "cmds": {
                        t.team_id: worker_cmd_factory(t.team_id, self.repo_dir / ".dev" / "worktrees" / t.team_id)  # type: ignore[operator]
                        for t in config.teams  # type: ignore[attr-defined]
                    },
                }
            )
            return {}

        with mock.patch.object(MultiTeamOrchestrator, "run_workers", autospec=True, side_effect=fake_run_workers):
            ret = main(
                [
                    "--work-dir",
                    str(self.repo_dir),
                    "teams",
                    "run",
                    "--job-id",
                    "job-mock-fwd",
                    "--mock",
                    "--maker-model",
                    "gemini-3.8-flash",
                    "--checker-model",
                    "claude-opus-5.5",
                ]
            )
        # fake_run_workers returned empty dict so worker failure stops before integration
        self.assertEqual(ret, 1)
        self.assertEqual(len(captured_calls), 1)
        self.assertEqual(captured_calls[0]["timeout_sec"], 21600.0)
        cmds = captured_calls[0]["cmds"]
        assert isinstance(cmds, dict)
        for tid in ("core", "ui"):
            cmd = cmds[tid]
            assert isinstance(cmd, list)
            self.assertIn("--work-dir", cmd)
            self.assertIn("run", cmd)
            self.assertIn("--mock", cmd)
            self.assertIn("--maker-model", cmd)
            self.assertIn("gemini-3.8-flash", cmd)
            self.assertIn("--checker-model", cmd)
            self.assertIn("claude-opus-5.5", cmd)
            self.assertLess(cmd.index("--work-dir"), cmd.index("run"))

    def test_teams_run_fails_closed_without_integration_when_any_team_fails(self) -> None:
        maker_script = self.scripts_dir / "maker_partial_fail.py"
        maker_script.write_text(
            "import sys\n"
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / 'app.py').write_text('VALUE = 77\\n', encoding='utf-8')\n"
            "else:\n"
            "    sys.stderr.write('simulated ui maker failure\\n')\n"
            "    sys.exit(1)\n",
            encoding="utf-8",
        )
        proc = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            "job-partial-fail",
            "--maker-cmd",
            f"{sys.executable} {maker_script}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
        )
        self.assertEqual(proc.returncode, 1)
        combined = proc.stdout + "\n" + proc.stderr
        self.assertIn("ui", combined)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", "rc/job-partial-fail").stdout.strip(), "")
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "core").is_dir())
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "ui").is_dir())

    def test_teams_run_enforces_worker_timeout_and_skips_integration(self) -> None:
        pid_file = self.scripts_dir / "slow_worker_pids.txt"
        maker_script = self.scripts_dir / "maker_slow.py"
        maker_script.write_text(
            "import os, time\n"
            "from pathlib import Path\n"
            f"with open({str(pid_file)!r}, 'a', encoding='utf-8') as f:\n"
            "    f.write(f'{os.getpid()}\\n')\n"
            "    f.flush()\n"
            "time.sleep(30)\n",
            encoding="utf-8",
        )
        t0 = time.monotonic()
        proc = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            "job-timeout",
            "--worker-timeout",
            "1",
            "--maker-cmd",
            f"{sys.executable} {maker_script}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
        )
        elapsed = time.monotonic() - t0
        self.assertEqual(proc.returncode, 1)
        self.assertLess(elapsed, 20.0)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", "rc/job-timeout").stdout.strip(), "")
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "core").is_dir())
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "ui").is_dir())

        workers = WorkerRegistry(self.repo_dir, "job-timeout").load()
        self.assertTrue(any(w.status == "TIMEOUT" for w in workers.values()))
        if pid_file.exists():
            for line in pid_file.read_text(encoding="utf-8").splitlines():
                if line.strip().isdigit():
                    self.assertFalse(_is_process_alive(int(line.strip())))

    def test_teams_run_rejects_missing_cmds_or_same_model_family_before_prepare(self) -> None:
        no_cmd = _run_cli(self.repo_dir, "teams", "run", "--job-id", "job-no-cmd")
        self.assertEqual(no_cmd.returncode, 2)
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())

        same_fam = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            "job-same-fam",
            "--mock",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "gemini-4.0-pro",
        )
        self.assertEqual(same_fam.returncode, 2)
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())


class TestTeamsRecoverCli(unittest.TestCase):
    """TEAM-16: auto-dev teams recover [--job-id ID] [--json]。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        self.base_commit = _init_multi_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_teams_recover_reclaims_orphan_rescues_bundles_and_integrates(self) -> None:
        job_id = "job-rec-1"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 111\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 222\n")

        self.assertEqual(_run_cli(core_wt, "bundle", "--job-id", f"{job_id}-core").returncode, 0)
        self.assertEqual(_run_cli(ui_wt, "bundle", "--job-id", f"{job_id}-ui").returncode, 0)

        orphan = subprocess.Popen(["sleep", "60"], start_new_session=True)
        try:
            registry = WorkerRegistry(self.repo_dir, job_id)
            now = time.time()
            registry.save(
                {
                    "core": WorkerRegistration(
                        team_id="core",
                        pid=orphan.pid,
                        pgid=orphan.pid,
                        worktree_path=str(core_wt),
                        base_branch="main",
                        branch_name="team/core",
                        status="RUNNING",
                        start_time=now,
                        updated_at=now,
                        process_start_time=get_process_start_identifier(orphan.pid),
                    ),
                    "ui": WorkerRegistration(
                        team_id="ui",
                        pid=999991,
                        pgid=999991,
                        worktree_path=str(ui_wt),
                        base_branch="main",
                        branch_name="team/ui",
                        status="COMPLETED",
                        start_time=now,
                        updated_at=now,
                    ),
                }
            )

            rec = _run_cli(self.repo_dir, "teams", "recover", "--job-id", job_id, "--json")
            self.assertEqual(rec.returncode, 0, f"stdout={rec.stdout}\nstderr={rec.stderr}")
            data = json.loads(rec.stdout)
            self.assertTrue(data["success"])
            self.assertIn("core", data["reclaimed_orphans"])
            self.assertIn("core", data["rescued_bundles"])
            self.assertIn("ui", data["rescued_bundles"])
            self.assertEqual(data["rc_tag"], f"rc/{job_id}")
            self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), f"rc/{job_id}")
            try:
                orphan.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
            self.assertFalse(_is_process_alive(orphan.pid))
        finally:
            if _is_process_alive(orphan.pid):
                orphan.kill()
                orphan.wait(timeout=1.0)

    def test_teams_recover_handles_interrupted_integration_gate_and_allows_retry(self) -> None:
        job_id = "job-rec-gate"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 301\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 302\n")

        _git(self.repo_dir, "branch", f"integration/{job_id}", "main")
        IntegrationJobState(
            job_id=job_id,
            state="MERGING",
            base_commit=self.base_commit,
            integration_branch=f"integration/{job_id}",
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        # integrate refuses while interrupted
        blocked = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(blocked.returncode, 1)
        self.assertIn("teams recover", blocked.stderr)

        # recover resets/quarantines interrupted integration
        rec = _run_cli(self.repo_dir, "teams", "recover", "--job-id", job_id)
        self.assertEqual(rec.returncode, 0, f"stdout={rec.stdout}\nstderr={rec.stderr}")

        # integrate succeeds afterward
        ok = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(ok.returncode, 0, f"stdout={ok.stdout}\nstderr={ok.stderr}")
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), f"rc/{job_id}")

    def test_teams_recover_defaults_to_latest_job_and_fails_closed_on_unverified_process(self) -> None:
        job_id = "job-rec-unverified"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        unrelated = subprocess.Popen(["sleep", "60"], start_new_session=True)
        try:
            registry = WorkerRegistry(self.repo_dir, job_id)
            now = time.time()
            registry.save(
                {
                    "core": WorkerRegistration(
                        team_id="core",
                        pid=unrelated.pid,
                        pgid=unrelated.pid,
                        worktree_path=str(core_wt),
                        base_branch="main",
                        branch_name="team/core",
                        status="RUNNING",
                        start_time=now,
                        updated_at=now,
                        process_start_time="lstart:MISMATCH",
                    ),
                    "ui": WorkerRegistration(
                        team_id="ui",
                        pid=999992,
                        pgid=999992,
                        worktree_path=str(ui_wt),
                        base_branch="main",
                        branch_name="team/ui",
                        status="FAILED",
                        start_time=now,
                        updated_at=now,
                    ),
                }
            )

            rec = _run_cli(self.repo_dir, "teams", "recover", "--json")
            self.assertEqual(rec.returncode, 1)
            data = json.loads(rec.stdout)
            self.assertFalse(data["success"])
            self.assertIn("core", data["unverified_processes"])
            self.assertTrue(_is_process_alive(unrelated.pid))
            self.assertTrue(core_wt.is_dir())
            self.assertTrue(ui_wt.is_dir())
        finally:
            if _is_process_alive(unrelated.pid):
                unrelated.kill()
                unrelated.wait(timeout=1.0)

    def test_teams_recover_unknown_or_invalid_job_id(self) -> None:
        bad = _run_cli(self.repo_dir, "teams", "recover", "--job-id", "../bad")
        self.assertEqual(bad.returncode, 2)

        missing = _run_cli(self.repo_dir, "teams", "recover", "--job-id", "does-not-exist")
        self.assertEqual(missing.returncode, 1)


if __name__ == "__main__":
    unittest.main()
