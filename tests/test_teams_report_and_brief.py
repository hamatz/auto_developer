from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.models import JobState


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


def _init_multi_team_repo(repo_dir: Path) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\n- TEAM-17\n", encoding="utf-8")
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
        raise AssertionError(f"verify-step failed in {wt_dir}: {vs.stderr}")

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
        raise AssertionError(f"commit-step failed in {wt_dir}: {cs.stderr}")


class TestTeamsReportAndBrief(unittest.TestCase):
    """TEAM-17: NIGHTLY_REPORT.md (report) と auto-dev brief のマルチチーム表示。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_tmp = Path(self.tmp_dir.name)
        self.repo_dir = self.base_tmp / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.base_commit = _init_multi_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_report_and_brief_after_successful_teams_integration(self) -> None:
        job_id = "job-rpt-ok"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 10\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 20\n")

        integ = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(integ.returncode, 0, integ.stderr)

        rpt = _run_cli(self.repo_dir, "report")
        self.assertEqual(rpt.returncode, 0, rpt.stderr)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")

        self.assertIn("core", report_text)
        self.assertIn("ui", report_text)
        self.assertIn(f"rc/{job_id}", report_text)
        self.assertIn("git merge", report_text)
        self.assertIn("teams cleanup", report_text)

        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("core", brief.stdout)
        self.assertIn("ui", brief.stdout)
        self.assertIn(f"rc/{job_id}", brief.stdout)
        self.assertIn("git merge", brief.stdout)

    def test_report_and_brief_after_failed_team_shows_quarantine_and_pending_decisions(self) -> None:
        job_id = "job-rpt-fail"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 10\n")

        (ui_wt / "plans" / "ui.md").write_text(
            "# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task one **[FAILED]**\n",
            encoding="utf-8",
        )
        _git(ui_wt, "add", "plans/ui.md")
        _git(ui_wt, "commit", "-m", "mark ui failed")
        _git(ui_wt, "branch", "quarantine/ui-1-1700000009")
        JobState(
            job_id=f"{job_id}-ui",
            status="FAILED",
            checkpoint_hash=self.base_commit,
            quarantined_task={"task_id": "1", "quarantined_branch": "quarantine/ui-1-1700000009"},
        ).save(ui_wt / ".dev" / "job_state.json")

        rpt = _run_cli(self.repo_dir, "report")
        self.assertEqual(rpt.returncode, 0, rpt.stderr)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")

        self.assertIn("core", report_text)
        self.assertIn("ui", report_text)
        self.assertIn("quarantine/ui-1-1700000009", report_text)
        self.assertIn("FAILED", report_text)
        self.assertIn(f"teams cleanup --job-id {job_id} --force", report_text)

        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("core", brief.stdout)
        self.assertIn("ui", brief.stdout)
        self.assertIn("quarantine/ui-1-1700000009", brief.stdout)
        self.assertIn(f"teams cleanup --job-id {job_id} --force", brief.stdout)

    def test_brief_still_shows_multi_team_summary_when_root_job_state_exists(self) -> None:
        job_id = "job-rpt-with-state"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 31\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 32\n")
        integ = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(integ.returncode, 0, integ.stderr)

        JobState(
            job_id="legacy-single-job",
            status="COMPLETED",
            checkpoint_hash=self.base_commit,
        ).save(self.repo_dir / ".dev" / "job_state.json")

        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("core", brief.stdout)
        self.assertIn("ui", brief.stdout)
        self.assertIn(f"rc/{job_id}", brief.stdout)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn(f"rc/{job_id}", report_text)


if __name__ == "__main__":
    unittest.main()
