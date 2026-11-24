"""STAT-5: status --json の timeline[]・backlog・時系列の始まりと終わり・schema v2 を検証する。"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main
from auto_developer.metrics import MetricsLog
from auto_developer.multi_team import get_teams_status


def _run_git(cwd: Path, args: list[str]) -> str:
    proc = subprocess.run(
        ["git"] + args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def _init_git_repo(repo: Path) -> str:
    _run_git(repo, ["init", "-b", "main"])
    _run_git(repo, ["config", "user.name", "Test"])
    _run_git(repo, ["config", "user.email", "test@example.com"])
    (repo / "README.md").write_text("# Repo\n", encoding="utf-8")
    _run_git(repo, ["add", "-A"])
    _run_git(repo, ["commit", "-m", "init"])
    return _run_git(repo, ["rev-parse", "HEAD"])


def _append_event(events_path: Path, record: dict[str, object]) -> None:
    events_path.parent.mkdir(parents=True, exist_ok=True)
    with open(events_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


class TestStat5TimelineBacklog(unittest.TestCase):
    """STAT-5: timeline[]・backlog・timeline_start/end・auto-dev-status/2 を検証する。"""

    def test_single_team_timeline_dedup_numstat_scoping_and_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            base_sha = _init_git_repo(repo)

            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text(
                "# PLAN.md\n\n"
                "## 現在の目標\n"
                "- Phase 73\n\n"
                "## ステップ\n"
                "- [x] 1. Add alpha module\n"
                "- [ ] 2. **[FAILED]** Broken beta\n"
                "- [ ] 3. Pending gamma\n",
                encoding="utf-8",
            )
            _run_git(repo, ["add", "-A"])
            _run_git(repo, ["commit", "-m", "plan"])

            # Task 1 commit: add 3 lines, delete 1 line in README.md
            (repo / "alpha.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
            (repo / "README.md").write_text("# Updated Repo\n", encoding="utf-8")
            _run_git(repo, ["add", "-A"])
            _run_git(repo, ["commit", "-m", "feat: add alpha [refs: 1]"])
            commit_sha = _run_git(repo, ["rev-parse", "HEAD"])

            metrics = MetricsLog(repo_dir=repo, plan_path=repo / "PLAN.md")
            events_path = metrics.events_path

            # Old phase / non-plan event that must be excluded
            _append_event(events_path, {
                "schema": 1, "ts": 500, "event": "task_start", "phase": "Phase 72",
                "head": base_sha, "task_id": "1", "attempt": 1,
            })
            # Non-STAT-4 event that must be excluded
            _append_event(events_path, {
                "schema": 1, "ts": 1000, "event": "audit_dispatched", "phase": "Phase 73",
                "head": base_sha, "task_id": "1",
            })
            # Task 1: consecutive task_start events (attempt 1 -> attempt 2) are all preserved
            _append_event(events_path, {
                "schema": 1, "ts": 1001, "event": "task_start", "phase": "Phase 73",
                "head": base_sha, "task_id": "1", "attempt": 1,
            })
            _append_event(events_path, {
                "schema": 1, "ts": 1005, "event": "task_start", "phase": "Phase 73",
                "head": base_sha, "task_id": "1", "attempt": 2,
            })
            _append_event(events_path, {
                "schema": 1, "ts": 1010, "event": "verify_step", "phase": "Phase 73",
                "head": base_sha, "task_id": "1", "attempt": 2, "result": "FAILED", "exit_code": 1,
            })
            # Retry task_start after verify_step -> must be kept!
            _append_event(events_path, {
                "schema": 1, "ts": 1015, "event": "task_start", "phase": "Phase 73",
                "head": base_sha, "task_id": "1", "attempt": 2,
            })
            _append_event(events_path, {
                "schema": 1, "ts": 1020, "event": "verify_step", "phase": "Phase 73",
                "head": base_sha, "task_id": "1", "attempt": 2, "result": "PASSED", "exit_code": 0,
            })
            _append_event(events_path, {
                "schema": 1, "ts": 1025, "event": "checker_review", "phase": "Phase 73",
                "head": base_sha, "task_id": "1", "decision": "APPROVE",
            })
            _append_event(events_path, {
                "schema": 1, "ts": 1030, "event": "commit_step", "phase": "Phase 73",
                "head": commit_sha, "task_id": "1", "result": "COMMITTED", "commit": commit_sha,
            })
            # Task 99 not in current PLAN.md -> must be excluded
            _append_event(events_path, {
                "schema": 1, "ts": 1040, "event": "task_start", "phase": "Phase 73",
                "head": commit_sha, "task_id": "99", "attempt": 1,
            })

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            self.assertEqual(data["schema"], "auto-dev-status/2")
            self.assertIn("timeline", data)
            self.assertIn("timeline_start", data)
            self.assertIn("timeline_end", data)
            self.assertIn("backlog", data)

            tl = data["timeline"]
            self.assertEqual([e["ts"] for e in tl], [1001, 1005, 1010, 1015, 1020, 1025, 1030])
            self.assertEqual(
                [e["event"] for e in tl],
                [
                    "task_start", "task_start", "verify_step", "task_start",
                    "verify_step", "checker_review", "commit_step",
                ],
            )
            commit_ev = tl[-1]
            self.assertEqual(commit_ev["commit"], commit_sha)
            # alpha.py (+2, -0) + README.md (+1, -1) => +3, -1
            self.assertEqual(commit_ev["additions"], 3)
            self.assertEqual(commit_ev["deletions"], 1)

            self.assertIsNotNone(data["timeline_start"])
            self.assertIsNotNone(data["timeline_end"])
            self.assertLessEqual(data["timeline_start"], data["timeline_end"])

            backlog = data["backlog"]
            self.assertIn("plan", backlog)
            self.assertEqual(
                backlog["plan"],
                [
                    {"task_id": "1", "title": "Add alpha module", "status": "completed"},
                    {"task_id": "2", "title": "**[FAILED]** Broken beta", "status": "failed"},
                    {"task_id": "3", "title": "Pending gamma", "status": "pending"},
                ],
            )

    def test_single_team_empty_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "PLAN.md").write_text("# Plan\n\n- [ ] 1. First task\n", encoding="utf-8")
            _run_git(repo, ["add", "-A"])
            _run_git(repo, ["commit", "-m", "plan"])

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            self.assertEqual(data["schema"], "auto-dev-status/2")
            self.assertEqual(data["timeline"], [])
            self.assertIsNone(data["timeline_start"])
            self.assertIsNone(data["timeline_end"])
            self.assertEqual(
                data["backlog"],
                {"plan": [{"task_id": "1", "title": "First task", "status": "pending"}]},
            )

    def test_multi_team_timeline_merge_numstat_latest_job_and_backlog(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            base_sha = _init_git_repo(repo)

            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "plans").mkdir()
            (repo / "plans" / "core.md").write_text(
                "# Core Plan\n\n- [ ] 1. Core task\n",
                encoding="utf-8",
            )
            (repo / "plans" / "cli.md").write_text(
                "# CLI Plan\n\n- [ ] 1. CLI task\n- [ ] 2. CLI second\n",
                encoding="utf-8",
            )
            (repo / "teams.json").write_text(
                json.dumps({
                    "version": 1,
                    "project": "test-proj",
                    "teams": [
                        {"id": "core", "plan": "plans/core.md", "owns": ["src/core/**"]},
                        {"id": "cli", "plan": "plans/cli.md", "owns": ["src/cli/**"]},
                    ],
                }),
                encoding="utf-8",
            )
            _run_git(repo, ["add", "-A"])
            _run_git(repo, ["commit", "-m", "setup teams"])

            prep_rc = cli_main(["--work-dir", str(repo), "teams", "prepare"])
            self.assertEqual(prep_rc, 0)
            st_payload, st_rc, st_err = get_teams_status(repo)
            self.assertEqual(st_rc, 0, st_err)
            assert st_payload is not None
            job_id = str(st_payload["job_id"])
            wt_by_id = {str(t["team_id"]): Path(str(t["worktree_path"])) for t in st_payload["teams"]}

            # Commit in core worktree
            core_wt = wt_by_id["core"]
            (core_wt / "src").mkdir(exist_ok=True)
            (core_wt / "src" / "core.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
            (core_wt / "plans" / "core.md").write_text(
                "# Core Plan\n\n- [x] 1. Core task\n",
                encoding="utf-8",
            )
            _run_git(core_wt, ["add", "-A"])
            _run_git(core_wt, ["commit", "-m", "feat(core): task 1 [refs: 1]"])
            core_commit = _run_git(core_wt, ["rev-parse", "HEAD"])

            # Create a real merge commit on an integration branch in main repo
            _run_git(repo, ["checkout", "-b", f"integration/{job_id}"])
            _run_git(repo, ["merge", "--no-ff", "team/core", "-m", "merge core"])
            merge_commit = _run_git(repo, ["rev-parse", "HEAD"])
            _run_git(repo, ["checkout", "main"])

            # Write worker events in worktrees
            core_metrics = MetricsLog(repo_dir=core_wt, plan_path=core_wt / "plans" / "core.md", team_id="core")
            _append_event(core_metrics.events_path, {
                "schema": 1, "ts": 2001, "event": "task_start", "team": "core",
                "task_id": "1", "attempt": 1, "head": base_sha,
            })
            _append_event(core_metrics.events_path, {
                "schema": 1, "ts": 2005, "event": "commit_step", "team": "core",
                "task_id": "1", "result": "COMMITTED", "commit": core_commit, "head": core_commit,
            })

            cli_wt = wt_by_id["cli"]
            cli_metrics = MetricsLog(repo_dir=cli_wt, plan_path=cli_wt / "plans" / "cli.md", team_id="cli")
            _append_event(cli_metrics.events_path, {
                "schema": 1, "ts": 2002, "event": "task_start", "team": "cli",
                "task_id": "1", "attempt": 1, "head": base_sha,
            })

            # Write an older job's integration & team events AND current job's events in main repo
            main_metrics = MetricsLog(repo_dir=repo)
            _append_event(main_metrics.events_path, {
                "schema": 1, "ts": 1000, "event": "integration_start", "job_id": "job-old-000",
            })
            _append_event(main_metrics.events_path, {
                "schema": 1, "ts": 1001, "event": "commit_step", "job_id": "job-old-000",
                "team": "core", "task_id": "1", "result": "COMMITTED", "commit": core_commit,
            })
            _append_event(main_metrics.events_path, {
                "schema": 1, "ts": 1002, "event": "task_start",
                "team": "core", "task_id": "1", "attempt": 1,
            })
            # Duplicate of core's task_start with latest job_id in main repo -> must be deduplicated
            _append_event(main_metrics.events_path, {
                "schema": 1, "ts": 2001, "event": "task_start", "job_id": job_id,
                "team": "core", "task_id": "1", "attempt": 1, "head": base_sha,
            })
            _append_event(main_metrics.events_path, {
                "schema": 1, "ts": 2010, "event": "integration_start", "job_id": job_id,
            })
            _append_event(main_metrics.events_path, {
                "schema": 1, "ts": 2012, "event": "integration_merge", "job_id": job_id,
                "team": "core", "commit": merge_commit,
            })
            _append_event(main_metrics.events_path, {
                "schema": 1, "ts": 2015, "event": "rc_tag", "job_id": job_id,
                "tag": f"rc/{job_id}", "commit": merge_commit,
            })

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            self.assertEqual(data["schema"], "auto-dev-status/2")
            tl = data["timeline"]
            # Older job event (ts=1000) excluded; sorted chronologically
            self.assertEqual([e["ts"] for e in tl], [2001, 2002, 2005, 2010, 2012, 2015])
            self.assertEqual(
                [e["event"] for e in tl],
                ["task_start", "task_start", "commit_step", "integration_start", "integration_merge", "rc_tag"],
            )

            # commit_step has line counts; merge commit does not
            core_commit_ev = tl[2]
            self.assertEqual(core_commit_ev["team"], "core")
            self.assertEqual(core_commit_ev["additions"], 4)  # 3 in core.py + 1 in plans/core.md
            self.assertEqual(core_commit_ev["deletions"], 1)  # 1 in plans/core.md

            merge_ev = tl[4]
            self.assertIsNone(merge_ev["additions"])
            self.assertIsNone(merge_ev["deletions"])
            rc_tag_ev = tl[5]
            self.assertIsNone(rc_tag_ev["additions"])
            self.assertIsNone(rc_tag_ev["deletions"])

            # Backlog per team
            self.assertEqual(
                data["backlog"],
                {
                    "core": [{"task_id": "1", "title": "Core task", "status": "completed"}],
                    "cli": [
                        {"task_id": "1", "title": "CLI task", "status": "pending"},
                        {"task_id": "2", "title": "CLI second", "status": "pending"},
                    ],
                },
            )

    def test_missing_timestamps_binary_commit_missing_commit_and_git_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            base_sha = _init_git_repo(repo)
            (repo / "PLAN.md").write_text("# Plan\n\n- [x] 1. Binary & edge cases\n", encoding="utf-8")
            _run_git(repo, ["add", "-A"])
            _run_git(repo, ["commit", "-m", "plan"])

            # Binary-only commit
            (repo / "img.bin").write_bytes(b"\x00\x01\x02\xff\xfe\xfd")
            _run_git(repo, ["add", "-A"])
            _run_git(repo, ["commit", "-m", "add binary [refs: 1]"])
            bin_sha = _run_git(repo, ["rev-parse", "HEAD"])

            metrics = MetricsLog(repo_dir=repo, plan_path=repo / "PLAN.md")
            # Missing both ts and timestamp
            _append_event(metrics.events_path, {
                "schema": 1, "event": "task_start", "task_id": "1", "attempt": 1, "head": base_sha,
            })
            # Binary commit -> additions/deletions should be None
            _append_event(metrics.events_path, {
                "schema": 1, "ts": 3000, "event": "commit_step", "task_id": "1",
                "result": "COMMITTED", "commit": bin_sha,
            })
            # Non-existent commit SHA -> additions/deletions should be None
            _append_event(metrics.events_path, {
                "schema": 1, "ts": 3010, "event": "commit_step", "task_id": "1",
                "result": "COMMITTED", "commit": "deadbeef" * 5,
            })

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            tl = data["timeline"]
            self.assertEqual(len(tl), 3)
            self.assertIsNone(tl[0]["timestamp"])
            self.assertIsNone(tl[1]["additions"])
            self.assertIsNone(tl[1]["deletions"])
            self.assertIsNone(tl[2]["additions"])
            self.assertIsNone(tl[2]["deletions"])
            # timeline_start / timeline_end must ignore missing-timestamp event and use 3000..3010
            self.assertEqual(data["timeline_start"], tl[1]["timestamp"])
            self.assertEqual(data["timeline_end"], tl[2]["timestamp"])

    def test_backlog_when_worktree_deleted_and_plan_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "plans").mkdir()
            (repo / "plans" / "core.md").write_text("# Core\n\n- [ ] 1. Core task\n", encoding="utf-8")
            (repo / "plans" / "cli.md").write_text("# CLI\n\n- [ ] 1. CLI task\n", encoding="utf-8")
            (repo / "teams.json").write_text(
                json.dumps({
                    "version": 1,
                    "project": "test-proj",
                    "teams": [
                        {"id": "core", "plan": "plans/core.md", "owns": ["src/core/**"]},
                        {"id": "cli", "plan": "plans/cli.md", "owns": ["src/cli/**"]},
                    ],
                }),
                encoding="utf-8",
            )
            _run_git(repo, ["add", "-A"])
            _run_git(repo, ["commit", "-m", "setup teams"])

            self.assertEqual(cli_main(["--work-dir", str(repo), "teams", "prepare"]), 0)
            st_payload, st_rc, st_err = get_teams_status(repo)
            self.assertEqual(st_rc, 0, st_err)
            assert st_payload is not None
            wt_by_id = {str(t["team_id"]): Path(str(t["worktree_path"])) for t in st_payload["teams"]}

            # Remove core worktree, and delete cli plan from worktree, branch, and repo
            _run_git(repo, ["worktree", "remove", "--force", str(wt_by_id["core"])])
            _run_git(wt_by_id["cli"], ["rm", "plans/cli.md"])
            _run_git(wt_by_id["cli"], ["commit", "-m", "remove cli plan"])
            (repo / "plans" / "cli.md").unlink()

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            # core falls back to branch team/core; cli missing plan returns []
            self.assertEqual(
                data["backlog"]["core"],
                [{"task_id": "1", "title": "Core task", "status": "pending"}],
            )
            self.assertEqual(data["backlog"]["cli"], [])


if __name__ == "__main__":
    unittest.main()
