from __future__ import annotations

import io
import json
import subprocess
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

from auto_developer.multi_team import _read_team_plan_from_bundle, get_multi_team_report_summary
from auto_developer.reporter import MorningReporter


class TestRpt8TeamReport(unittest.TestCase):
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
        (self.repo / "PLAN.md").write_text("# PLAN\n- [x] 1. Done\n", encoding="utf-8")
        (self.repo / "plans").mkdir(parents=True, exist_ok=True)
        (self.repo / "plans" / "core.md").write_text("# PLAN\n- [x] 1. Core task\n", encoding="utf-8")
        (self.repo / "teams.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "project": "demo",
                    "base": {"ref": "main"},
                    "teams": [{"id": "core", "owns": ["src/core/**"], "plan": "plans/core.md"}],
                }
            ),
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_missing_base_commit_shows_unknown_not_unchanged(self) -> None:
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        summary = {
            "status": "COMPLETED",
            "total_tasks": 1,
            "succeeded_count": 1,
            "quarantined_count": 0,
            "completed_tasks": [],
            "multi_team": {
                "job_id": "job-rpt8-base",
                "job_state": "INTEGRATED",
                "base_ref": "main",
                "base_commit": "",
                "base_head": "",
                "base_unchanged": None,
                "rc_tag": "rc/job-rpt8-base",
                "rc_commit": "2222222bbbbbbb",
                "all_no_changes": False,
                "teams": [],
                "integration_errors": [],
                "pending_decisions": [],
            },
        }
        reporter.write_report(report_path, summary)
        content = report_path.read_text(encoding="utf-8")
        self.assertIn("不明", content)
        self.assertNotIn("未変更", content)

        brief = reporter.format_console_brief(summary)
        self.assertIn("不明", brief)
        self.assertNotIn("未変更", brief)

    def test_get_multi_team_report_summary_does_not_backfill_models_and_sets_base_unknown(self) -> None:
        from auto_developer.multi_team import prepare_teams_job

        job_id = "job-rpt8-models"
        prepare_teams_job(self.repo, job_id=job_id)

        # Clear recorded base_commit in job metadata and integration state to test missing base_commit
        for p in (
            self.repo / ".dev" / "team_jobs" / f"{job_id}.json",
            self.repo / ".dev" / "integration_jobs" / job_id / "job.json",
            self.repo / ".dev" / "integration_jobs" / job_id / "state.json",
        ):
            if p.is_file():
                d = json.loads(p.read_text(encoding="utf-8"))
                d["base_commit"] = ""
                d["maker_model"] = "gemini-3.8-flash"
                d["checker_model"] = "claude-opus-5-5-medium"
                p.write_text(json.dumps(d), encoding="utf-8")

        # Put an event in events.jsonl with models to verify it is NOT used for backfill
        ev_dir = self.repo / ".git" / "nightly_evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "events.jsonl").write_text(
            json.dumps(
                {
                    "event": "commit_step",
                    "task_id": "1",
                    "checker_model": "claude-opus-5-5-medium",
                    "maker_model": "gemini-3.8-flash",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        wt_dir = self.repo / ".dev" / "worktrees" / "core"
        (wt_dir / ".dev" / "evidence").mkdir(parents=True, exist_ok=True)
        (wt_dir / ".dev" / "evidence" / "task_1.json").write_text(
            json.dumps({"task_id": "1", "status": "COMPLETED", "commit": "abc1234", "checker": {"decision": "APPROVE"}}),
            encoding="utf-8",
        )
        summary = get_multi_team_report_summary(self.repo, job_id=job_id)
        self.assertIsNotNone(summary)
        assert summary is not None
        self.assertIsNone(summary["base_unchanged"])
        core_team = next(t for t in summary["teams"] if t["team_id"] == "core")
        task1 = core_team["tasks"][0]
        self.assertEqual(task1["checker_model"], "記録なし")
        self.assertEqual(task1["maker_model"], "記録なし")

    def test_read_team_plan_from_bundle_strips_only_dot_slash_prefix(self) -> None:
        bundle_path = self.repo / "dot_prefix.zip"
        tar_buf = io.BytesIO()
        content = b"# PLAN\n- [x] 1. Hidden dir plan\n"
        with tarfile.open(fileobj=tar_buf, mode="w") as tf:
            info = tarfile.TarInfo(".hidden/plan.md")
            info.size = len(content)
            tf.addfile(info, io.BytesIO(content))
        with zipfile.ZipFile(bundle_path, "w") as zf:
            zf.writestr("source_snapshot.tar", tar_buf.getvalue())

        loaded = _read_team_plan_from_bundle(bundle_path, "./.hidden/plan.md")
        self.assertEqual(loaded, content.decode("utf-8"))


if __name__ == "__main__":
    unittest.main()
