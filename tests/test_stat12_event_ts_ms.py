"""STAT-12: イベントの ts_ms 記録・時系列/再生の ts_ms ソート・demo --teams 統合順のテスト。"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from typing import Any

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.cli import main as cli_main
from auto_developer.dashboard import _build_replay_payload, _parse_ts_seconds, render_dashboard_html
from auto_developer.metrics import MetricsLog
from auto_developer.multi_team import get_teams_status
from auto_developer.status import _parse_event_ts, collect_status_snapshot


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else SRC_DIR
    return env


def _run_cli_subprocess(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", *args],
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=240,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo: Path) -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Stat12 Tester")
    _git(repo, "config", "user.email", "stat12@example.com")
    (repo / "README.md").write_text("# Stat12\n", encoding="utf-8")
    (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (repo / "PLAN.md").write_text(
        "# PLAN.md\n\n## 現在の目標\n- Phase 78\n\n## ステップ\n- [ ] 1. Task\n",
        encoding="utf-8",
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")


def _extract_embedded_snapshot(html_text: str) -> dict[str, Any]:
    m = re.search(
        r'<script[^>]+id=["\']snapshot["\'][^>]*>(.*?)</script>',
        html_text,
        flags=re.DOTALL,
    )
    assert m is not None, "Embedded <script id='snapshot'> not found"
    return json.loads(m.group(1))


class TestStat12EventTsMs(unittest.TestCase):
    """STAT-12: ts_ms 記録と時系列・再生の順序を検証する。"""

    def test_record_event_includes_integer_ts_ms(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            metrics = MetricsLog(repo_dir=repo, plan_path=repo / "PLAN.md")
            self.assertTrue(metrics.record_event("task_start", task_id="1", attempt=1))
            time.sleep(0.015)
            self.assertTrue(metrics.record_event("verify_step", task_id="1", attempt=1, result="PASSED", exit_code=0))

            events = metrics.read_all_events()
            self.assertEqual(len(events), 2)
            for ev in events:
                self.assertIn("ts", ev)
                self.assertIn("ts_ms", ev)
                self.assertIsInstance(ev["ts"], int)
                self.assertNotIsInstance(ev["ts"], bool)
                self.assertIsInstance(ev["ts_ms"], int)
                self.assertNotIsInstance(ev["ts_ms"], bool)
                self.assertEqual(ev["ts_ms"] // 1000, ev["ts"])
            self.assertGreater(events[1]["ts_ms"], events[0]["ts_ms"])

    def test_timeline_and_replay_sort_by_ts_ms_across_teams_and_integration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            self.assertEqual(cli_main(["--work-dir", str(repo), "init"]), 0)
            self.assertEqual(cli_main(["--work-dir", str(repo), "teams", "init", "--teams", "core,cli"]), 0)
            for tid in ("core", "cli"):
                (repo / "plans" / f"{tid}.md").write_text(
                    f"# PLAN ({tid})\n\n## 現在の目標\n- Phase 78\n\n## ステップ\n- [x] 1. {tid} task\n",
                    encoding="utf-8",
                )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "setup teams")
            self.assertEqual(cli_main(["--work-dir", str(repo), "teams", "prepare"]), 0)

            teams_payload, rc_status, _ = get_teams_status(repo)
            self.assertEqual(rc_status, 0)
            assert isinstance(teams_payload, dict)
            job_id = str(teams_payload["job_id"])

            # Main events are read first, but have later ts_ms within the same second (ts=1000).
            main_events = MetricsLog(repo_dir=repo).events_path
            main_events.parent.mkdir(parents=True, exist_ok=True)
            main_events.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000500, "event": "integration_start", "job_id": job_id}),
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000600, "event": "rc_tag", "job_id": job_id, "tag": "rc/test"}),
                ]) + "\n",
                encoding="utf-8",
            )

            core_wt = repo / ".dev" / "worktrees" / "core"
            core_events = MetricsLog(repo_dir=core_wt, team_id="core").events_path
            core_events.parent.mkdir(parents=True, exist_ok=True)
            core_events.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000100, "event": "task_start", "task_id": "1", "attempt": 1}),
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000300, "event": "commit_step", "task_id": "1", "result": "COMMITTED", "commit": "1111111"}),
                ]) + "\n",
                encoding="utf-8",
            )

            cli_wt = repo / ".dev" / "worktrees" / "cli"
            cli_events = MetricsLog(repo_dir=cli_wt, team_id="cli").events_path
            cli_events.parent.mkdir(parents=True, exist_ok=True)
            cli_events.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000150, "event": "task_start", "task_id": "1", "attempt": 1}),
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000400, "event": "commit_step", "task_id": "1", "result": "COMMITTED", "commit": "2222222"}),
                ]) + "\n",
                encoding="utf-8",
            )

            snap = collect_status_snapshot(repo)
            tl = snap["timeline"]
            self.assertEqual(
                [(e["team"], e["event"]) for e in tl],
                [
                    ("core", "task_start"),
                    ("cli", "task_start"),
                    ("core", "commit_step"),
                    ("cli", "commit_step"),
                    (None, "integration_start"),
                    (None, "rc_tag"),
                ],
            )
            self.assertEqual(
                [e.get("ts_ms") for e in tl],
                [1000100, 1000150, 1000300, 1000400, 1000500, 1000600],
            )

            payload = _build_replay_payload(snap)
            self.assertEqual(
                [(e["team"], e["kind"]) for e in payload["events"]],
                [
                    ("core", "task_start"),
                    ("cli", "task_start"),
                    ("core", "commit"),
                    ("cli", "commit"),
                    (None, "integration_start"),
                    (None, "rc_tag"),
                ],
            )

    def test_legacy_events_without_ts_ms_preserve_ts_and_input_order(self) -> None:
        snap = {
            "schema": "auto-dev-status/2",
            "repo": {"name": "legacy", "branch": "main", "head": "abcdef0"},
            "plan": {"path": "PLAN.md", "tasks": [{"task_id": "1", "title": "T1", "status": "done"}]},
            "backlog": {"plan": [{"task_id": "1", "title": "T1", "status": "done"}]},
            "timeline": [
                {"ts": 1000, "event": "verify_step", "task_id": "1", "result": "PASSED"},
                {"ts": 1000, "event": "task_start", "task_id": "1", "attempt": 1},
            ],
            "attention": [],
        }
        payload = _build_replay_payload(snap)
        self.assertEqual(
            [e["kind"] for e in payload["events"]],
            ["verify", "task_start"],
        )

    def test_demo_teams_integration_events_appear_after_team_commits(self) -> None:
        with tempfile.TemporaryDirectory() as caller_tmp:
            caller_dir = Path(caller_tmp)
            kept_dir: Path | None = None
            try:
                proc = _run_cli_subprocess(caller_dir, "demo", "--teams", "--keep")
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"demo --teams --keep failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
                )
                m_ws = re.search(r"Demo workspace kept at:\s*(.+)", proc.stdout)
                self.assertIsNotNone(m_ws, f"Kept workspace path missing:\n{proc.stdout}")
                assert m_ws is not None
                kept_dir = Path(m_ws.group(1).strip())

                snap = collect_status_snapshot(kept_dir)
                tl = snap["timeline"]
                self.assertTrue(tl, "Expected non-empty timeline from demo --teams")
                for ev in tl:
                    self.assertIsInstance(ev.get("ts_ms"), int, f"Missing integer ts_ms in {ev}")

                commit_indices = [i for i, e in enumerate(tl) if e.get("event") == "commit_step"]
                int_start_indices = [i for i, e in enumerate(tl) if e.get("event") == "integration_start"]
                self.assertTrue(commit_indices, f"No commit_step in timeline: {tl}")
                self.assertTrue(int_start_indices, f"No integration_start in timeline: {tl}")
                self.assertGreater(
                    min(int_start_indices),
                    max(commit_indices),
                    f"integration_start appeared before team commit_step in timeline: {[(e.get('team'), e.get('event'), e.get('ts_ms')) for e in tl]}",
                )

                dashboard_html = (kept_dir / ".dev" / "dashboard.html").read_text(encoding="utf-8")
                embedded = _extract_embedded_snapshot(dashboard_html)
                replay_events = embedded["events"]
                commit_t = [e["t"] for e in replay_events if e.get("kind") == "commit"]
                int_start_t = [e["t"] for e in replay_events if e.get("kind") == "integration_start"]
                self.assertTrue(commit_t and int_start_t)
                self.assertGreater(min(int_start_t), max(commit_t))

                # Verify integration lane bar/events start at or after integration_start.t
                int_lane_kinds = {"integration_start", "merge", "reverify", "rc_tag", "integration_failed"}
                int_lane_events = [
                    e for e in replay_events
                    if e.get("team") is None and e.get("kind") in int_lane_kinds
                ]
                self.assertTrue(int_lane_events)
                self.assertEqual(int_lane_events[0].get("kind"), "integration_start")
                int_start_val = int_start_t[0]
                for ev in int_lane_events:
                    self.assertGreaterEqual(ev["t"], int_start_val)
            finally:
                if kept_dir is not None and kept_dir.exists():
                    shutil.rmtree(kept_dir, ignore_errors=True)

    def test_invalid_ts_ms_falls_back_to_ts(self) -> None:
        for bad_ts_ms in (True, False, "9999999", None):
            ev: dict[str, Any] = {"ts": 1000, "event": "task_start", "task_id": "1"}
            if bad_ts_ms is not None:
                ev["ts_ms"] = bad_ts_ms
            sec, iso = _parse_event_ts(ev)
            self.assertEqual(sec, 1000.0)
            self.assertIsNotNone(iso)
            self.assertEqual(_parse_ts_seconds(ev), 1000.0)

        snap = {
            "schema": "auto-dev-status/2",
            "repo": {"name": "fallback", "branch": "main", "head": "abcdef0"},
            "plan": {"path": "PLAN.md", "tasks": [{"task_id": "1", "title": "T1", "status": "done"}]},
            "backlog": {"plan": [{"task_id": "1", "title": "T1", "status": "done"}]},
            "timeline": [
                {"ts": 2000, "ts_ms": True, "event": "verify_step", "task_id": "1", "result": "PASSED"},
                {"ts": 1000, "ts_ms": "9999999", "event": "task_start", "task_id": "1", "attempt": 1},
                {"ts": 1500, "ts_ms": False, "event": "checker_review", "task_id": "1", "decision": "APPROVE"},
            ],
            "attention": [],
        }
        payload = _build_replay_payload(snap)
        self.assertEqual(
            [e["kind"] for e in payload["events"]],
            ["task_start", "review", "verify"],
        )

    def test_mixed_ts_ms_and_legacy_ts_events_order_deterministically(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            events_path = MetricsLog(repo_dir=repo).events_path
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000500, "event": "checker_review", "task_id": "1", "decision": "APPROVE"}),
                    json.dumps({"schema": 1, "ts": 1000, "event": "task_start", "task_id": "1", "attempt": 1}),
                    json.dumps({"schema": 1, "ts": 1001, "event": "commit_step", "task_id": "1", "result": "COMMITTED", "commit": "1111111"}),
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000200, "event": "verify_step", "task_id": "1", "result": "PASSED"}),
                ]) + "\n",
                encoding="utf-8",
            )
            snap = collect_status_snapshot(repo)
            self.assertEqual(
                [e["event"] for e in snap["timeline"]],
                ["task_start", "verify_step", "checker_review", "commit_step"],
            )
            payload = _build_replay_payload(snap)
            self.assertEqual(
                [e["kind"] for e in payload["events"]],
                ["task_start", "verify", "review", "commit"],
            )

    def test_huge_ts_ms_does_not_crash_status_or_dashboard(self) -> None:
        for huge in (10**20, 10**400, float("inf")):
            sec, _iso = _parse_event_ts({"ts": 1000, "ts_ms": huge, "event": "task_start"})
            self.assertIsInstance(sec, float)
            _parse_ts_seconds({"ts": 1000, "ts_ms": huge, "event": "task_start"})

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(repo)
            events_path = MetricsLog(repo_dir=repo).events_path
            events_path.parent.mkdir(parents=True, exist_ok=True)
            events_path.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 1000100, "event": "task_start", "task_id": "1", "attempt": 1}),
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 10**20, "event": "verify_step", "task_id": "1", "result": "PASSED"}),
                    json.dumps({"schema": 1, "ts": 1000, "ts_ms": 10**400, "event": "checker_review", "task_id": "1", "decision": "APPROVE"}),
                ]) + "\n",
                encoding="utf-8",
            )
            snap = collect_status_snapshot(repo)
            self.assertEqual(len(snap["timeline"]), 3)
            html_out = render_dashboard_html(snap)
            embedded = _extract_embedded_snapshot(html_out)
            self.assertEqual(len(embedded["events"]), 3)


if __name__ == "__main__":
    unittest.main()
