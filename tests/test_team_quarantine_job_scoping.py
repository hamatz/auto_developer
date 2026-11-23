from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.guard import GitGuard


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_cli(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.pop("AUTO_DEV_JOB_ID", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(work_dir), *args],
        cwd=work_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _list_branches(repo_dir: Path, pattern: str) -> list[str]:
    out = _git(repo_dir, "branch", "--list", pattern).stdout
    return [line.lstrip("* ").strip() for line in out.splitlines() if line.strip()]


def _init_two_team_repo(repo_dir: Path) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    init_proc = _run_cli(repo_dir, "init")
    if init_proc.returncode != 0:
        raise AssertionError(f"init failed: {init_proc.stdout}\n{init_proc.stderr}")

    teams_init_proc = _run_cli(repo_dir, "teams", "init", "--teams", "core,ui")
    if teams_init_proc.returncode != 0:
        raise AssertionError(
            f"teams init failed: {teams_init_proc.stdout}\n{teams_init_proc.stderr}"
        )

    (repo_dir / "scripts" / "verify_local.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    (repo_dir / "scripts" / "verify_local.sh").chmod(0o755)

    for tid in ("core", "ui"):
        (repo_dir / "plans" / f"{tid}.md").write_text(
            f"# PLAN ({tid})\n\n## ステップ\n- [ ] 1. {tid} task\n",
            encoding="utf-8",
        )

    (repo_dir / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core" / "app.py").write_text("CORE = 1\n", encoding="utf-8")
    (repo_dir / "src" / "ui" / "view.py").write_text("UI = 1\n", encoding="utf-8")

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "initial two-team setup")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


class TestTeamQuarantineJobScoping(unittest.TestCase):
    """TEAM-23: 隔離ブランチへのジョブ ID 付与と status / brief / NIGHTLY_REPORT.md での対象ジョブ限定."""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_tmp = Path(self.tmp_dir.name)
        self.repo_dir = self.base_tmp / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.base_tmp / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.base_commit = _init_two_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_guard_quarantine_and_restore_formats_team_branch_with_job_id(self) -> None:
        guard = GitGuard(repo_dir=self.repo_dir)
        (self.repo_dir / "src" / "core" / "app.py").write_text("CORE = 2\n", encoding="utf-8")

        q_branch = guard.quarantine_and_restore(
            checkpoint=self.base_commit,
            task_id="1",
            job_id="job-unit-01",
            team_id="core",
        )
        self.assertTrue(
            q_branch.startswith("quarantine/job-unit-01/core/1-"),
            f"Expected quarantine/job-unit-01/core/1-..., got: {q_branch}",
        )

        # Single-repo mode (no team_id) keeps quarantine/<task_id>-...
        (self.repo_dir / "src" / "core" / "app.py").write_text("CORE = 3\n", encoding="utf-8")
        single_branch = guard.quarantine_and_restore(
            checkpoint=self.base_commit,
            task_id="1",
            job_id="job-single-01",
            team_id="",
        )
        self.assertTrue(
            single_branch.startswith("quarantine/1-"),
            f"Expected quarantine/1-..., got: {single_branch}",
        )

    def test_teams_run_creates_job_scoped_quarantine_branch(self) -> None:
        maker_script = self.scripts_dir / "maker_ui_violation.py"
        maker_script.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / 'app.py').write_text('CORE = 2\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    (cwd / 'src' / 'core' / 'intrusion.py').write_text('BAD = 1\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        job_id = "job-q-run-1"
        proc = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            job_id,
            "--maker-cmd",
            f"{sys.executable} {maker_script}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(proc.returncode, 1, f"stdout={proc.stdout}\nstderr={proc.stderr}")

        q_branches = _list_branches(self.repo_dir, f"quarantine/{job_id}/ui/*")
        self.assertTrue(q_branches, f"Expected quarantine/{job_id}/ui/* branch, all={_list_branches(self.repo_dir, 'quarantine/*')}")
        self.assertEqual(_list_branches(self.repo_dir, "quarantine/ui-*"), [])

    def test_prepare_and_lowlevel_rollback_step_creates_job_scoped_quarantine_branch(self) -> None:
        job_id = "job-q-prep-rb"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        wt_chk = _git(core_wt, "rev-parse", "HEAD").stdout.strip()
        (core_wt / "src" / "ui" / "view.py").write_text("HACK = 1\n", encoding="utf-8")

        rb = _run_cli(
            core_wt,
            "rollback-step",
            "--checkpoint",
            wt_chk,
            "--task-id",
            "1",
            "--reason",
            "Boundary violation",
        )
        self.assertEqual(rb.returncode, 0, rb.stderr)
        q_branches = _list_branches(self.repo_dir, f"quarantine/{job_id}/core/*")
        self.assertTrue(q_branches, f"Expected quarantine/{job_id}/core/*, got stdout={rb.stdout}")
        self.assertIn(q_branches[0], rb.stdout)

    def test_prepare_and_worktree_run_creates_job_scoped_quarantine_branch(self) -> None:
        job_id = "job-q-wt-run"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        maker_script = self.scripts_dir / "maker_rogue.py"
        maker_script.write_text(
            "from pathlib import Path\n"
            "Path('src/core/app.py').write_text('ROGUE = 1\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        run_proc = _run_cli(
            ui_wt,
            "run",
            "--maker-cmd",
            f"{sys.executable} {maker_script}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(run_proc.returncode, 1, run_proc.stderr)
        q_branches = _list_branches(self.repo_dir, f"quarantine/{job_id}/ui/*")
        self.assertTrue(q_branches, f"Expected quarantine/{job_id}/ui/*, all={_list_branches(self.repo_dir, 'quarantine/*')}")

    def test_cleanup_force_creates_job_scoped_quarantine_branch(self) -> None:
        job_id = "job-q-clean-force"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        (core_wt / "src" / "core" / "app.py").write_text("CORE = 99\n", encoding="utf-8")

        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(forced.returncode, 0, f"stdout={forced.stdout}\nstderr={forced.stderr}")
        q_branches = _list_branches(self.repo_dir, f"quarantine/{job_id}/core/*")
        self.assertTrue(
            q_branches,
            f"Expected quarantine/{job_id}/core/*, all={_list_branches(self.repo_dir, 'quarantine/*')}",
        )
        self.assertIn(q_branches[0], forced.stdout)
        self.assertEqual(_list_branches(self.repo_dir, "quarantine/core-*"), [])

    def test_status_brief_and_report_show_only_target_job_quarantines(self) -> None:
        # 1. Job 1 fails on ui (creates quarantine/job-old-fail/ui/...) and is force-cleaned up
        maker_fail = self.scripts_dir / "maker_job1_fail.py"
        maker_fail.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / 'app.py').write_text('CORE = 10\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    (cwd / 'src' / 'core' / 'leak.py').write_text('LEAK = 1\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        r1 = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            "job-old-fail",
            "--maker-cmd",
            f"{sys.executable} {maker_fail}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(r1.returncode, 1, r1.stderr)
        old_ui_q = _list_branches(self.repo_dir, "quarantine/job-old-fail/ui/*")
        self.assertTrue(old_ui_q)

        cln1 = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", "job-old-fail", "--force")
        self.assertEqual(cln1.returncode, 0, cln1.stderr)

        # 2. Job 2 succeeds cleanly
        maker_ok = self.scripts_dir / "maker_job2_ok.py"
        maker_ok.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / 'app.py').write_text('CORE = 20\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    (cwd / 'src' / 'ui' / 'view.py').write_text('UI = 20\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        r2 = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            "job-new-ok",
            "--maker-cmd",
            f"{sys.executable} {maker_ok}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(r2.returncode, 0, f"stdout={r2.stdout}\nstderr={r2.stderr}")

        # status (latest = job-new-ok) must have NO quarantine branches from job-old-fail
        st_new = _run_cli(self.repo_dir, "teams", "status", "--json")
        self.assertEqual(st_new.returncode, 0, st_new.stderr)
        st_new_data = json.loads(st_new.stdout)
        self.assertEqual(st_new_data["job_id"], "job-new-ok")
        for t in st_new_data["teams"]:
            self.assertEqual(t["quarantine_branches"], [], f"Unexpected quarantine in {t}")

        # status --job-id job-old-fail must show ONLY job-old-fail's quarantine branches
        st_old = _run_cli(self.repo_dir, "teams", "status", "--job-id", "job-old-fail", "--json")
        self.assertEqual(st_old.returncode, 0, st_old.stderr)
        st_old_data = json.loads(st_old.stdout)
        old_by_team = {t["team_id"]: t for t in st_old_data["teams"]}
        self.assertTrue(
            all(b.startswith("quarantine/job-old-fail/ui/") for b in old_by_team["ui"]["quarantine_branches"])
        )
        self.assertTrue(old_by_team["ui"]["quarantine_branches"])

        # report & NIGHTLY_REPORT.md must NOT aggregate or display job-old-fail's quarantine branches
        rpt = _run_cli(self.repo_dir, "report")
        self.assertEqual(rpt.returncode, 0, rpt.stderr)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("job-new-ok", report_text)
        self.assertIn("| **要確認・隔離** | **0** 件 |", report_text)
        self.assertNotIn("quarantine/job-old-fail/", report_text)

        # brief must NOT show job-old-fail's quarantine branches
        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("job-new-ok", brief.stdout)
        self.assertNotIn("quarantine/job-old-fail/", brief.stdout)

    def test_status_brief_and_report_count_only_current_failed_job_quarantines(self) -> None:
        # Simulate an older job's leftover quarantine branch in git
        _git(self.repo_dir, "branch", "quarantine/job-prev-1/core/1-1700000001", self.base_commit)

        maker_fail = self.scripts_dir / "maker_job2_fail.py"
        maker_fail.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / 'app.py').write_text('CORE = 30\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    (cwd / 'src' / 'core' / 'leak2.py').write_text('LEAK = 2\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        r2 = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            "job-curr-2",
            "--maker-cmd",
            f"{sys.executable} {maker_fail}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(r2.returncode, 1, r2.stderr)

        st = _run_cli(self.repo_dir, "teams", "status", "--json")
        self.assertEqual(st.returncode, 0, st.stderr)
        st_data = json.loads(st.stdout)
        self.assertEqual(st_data["job_id"], "job-curr-2")
        by_team = {t["team_id"]: t for t in st_data["teams"]}
        self.assertEqual(by_team["core"]["quarantine_branches"], [])
        self.assertEqual(len(by_team["ui"]["quarantine_branches"]), 1)
        self.assertTrue(by_team["ui"]["quarantine_branches"][0].startswith("quarantine/job-curr-2/ui/"))

        rpt = _run_cli(self.repo_dir, "report")
        self.assertEqual(rpt.returncode, 0, rpt.stderr)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("job-curr-2", report_text)
        self.assertIn("| **要確認・隔離** | **1** 件 |", report_text)
        self.assertIn("quarantine/job-curr-2/ui/", report_text)
        self.assertNotIn("quarantine/job-prev-1/", report_text)

        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("quarantine/job-curr-2/ui/", brief.stdout)
        self.assertNotIn("quarantine/job-prev-1/", brief.stdout)


if __name__ == "__main__":
    unittest.main()
