"""STAT-10: Tests for dashboard gap marker tooltips, quarantine scope distinction, and counter explanation."""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.dashboard import _build_replay_payload, render_dashboard_html
from auto_developer.metrics import MetricsLog
from auto_developer.status import collect_status_snapshot


def _run_git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo_with_quarantines(root: Path) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _run_git(repo, "init", "-b", "main")
    _run_git(repo, "config", "user.email", "test@example.com")
    _run_git(repo, "config", "user.name", "Test User")

    (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (repo / "PLAN.md").write_text(
        "# PLAN.md\n\n"
        "## 現在の目標\n"
        "- Phase 74: test\n\n"
        "## ステップ\n"
        "- [ ] 1. First task\n"
        "- [ ] 2. **[FAILED]** Second task\n\n"
        "## 止まっていること・判断待ち\n"
        "- なし。\n",
        encoding="utf-8",
    )
    _run_git(repo, "add", "SPEC.md", "PLAN.md")
    _run_git(repo, "commit", "-m", "init")
    head_sha = _run_git(repo, "rev-parse", "HEAD")

    # Create a prior quarantine branch (not in current timeline) and a current-timeline quarantine branch
    _run_git(repo, "branch", "quarantine/old-task-99-000", head_sha)
    _run_git(repo, "branch", "quarantine/2-111", head_sha)

    metrics = MetricsLog(repo_dir=repo)
    metrics.record_event("task_start", task_id="2", attempt=1)
    metrics.record_event(
        "rollback_step",
        task_id="2",
        checkpoint=head_sha,
        quarantine_branch="quarantine/2-111",
        reason="failed twice",
    )
    return repo


class TestStat10DashboardDetails(unittest.TestCase):
    def test_quarantine_attention_distinguishes_current_timeline_from_previous(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo_with_quarantines(Path(tmp))
            snapshot = collect_status_snapshot(repo)

            # Timeline rollback_step event must expose quarantine_branch
            rb_events = [e for e in snapshot["timeline"] if e.get("event") == "rollback_step"]
            self.assertEqual(len(rb_events), 1)
            self.assertEqual(rb_events[0].get("quarantine_branch"), "quarantine/2-111")

            q_items = [a for a in snapshot["attention"] if a.get("kind") == "QUARANTINE_BRANCH"]
            self.assertEqual(len(q_items), 2)
            by_msg = {item["message"]: item for item in q_items}
            cur_items = [item for item in q_items if item.get("scope") == "current"]
            prev_items = [item for item in q_items if item.get("scope") == "previous"]
            self.assertEqual(len(cur_items), 1, f"Expected 1 current quarantine item, got {by_msg}")
            self.assertEqual(len(prev_items), 1, f"Expected 1 previous quarantine item, got {by_msg}")
            self.assertIn("quarantine/2-111", cur_items[0]["message"])
            self.assertIn("quarantine/old-task-99-000", prev_items[0]["message"])

            payload = _build_replay_payload(snapshot)
            p_cur = [a for a in payload["attention"] if a.get("scope") == "current"]
            p_prev = [a for a in payload["attention"] if a.get("scope") == "previous"]
            self.assertEqual(len(p_cur), 1)
            self.assertEqual(len(p_prev), 1)

            html_text = render_dashboard_html(snapshot)
            self.assertIn('data-scope="current"', html_text)
            self.assertIn('data-scope="previous"', html_text)

    def test_top_quarantined_counter_has_timeline_scope_tooltip(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = _init_repo_with_quarantines(Path(tmp))
            snapshot = collect_status_snapshot(repo)
            html_text = render_dashboard_html(snapshot)

            match = re.search(r'<span\b[^>]*id="c-q-wrap"[^>]*>', html_text)
            self.assertIsNotNone(match)
            assert match is not None
            tag = match.group(0)
            title_match = re.search(r'title="([^"]+)"', tag)
            self.assertIsNotNone(title_match, f"Expected title tooltip on #c-q-wrap: {tag}")
            assert title_match is not None
            self.assertIn("timeline", title_match.group(1).lower())

    def test_gap_markers_include_hover_title_and_overlap_guard(self) -> None:
        snapshot = {
            "schema": "auto-dev-status/2",
            "repo": {"head": "abc1234", "branch": "main"},
            "plan": {"path": "PLAN.md", "tasks": []},
            "teams": None,
            "attention": [],
            "backlog": {"plan": []},
            "timeline": [
                {"ts": 1000.0, "event": "task_start", "task_id": "1"},
                {"ts": 1900.0, "event": "verify_step", "task_id": "1", "result": "PASSED"},
                {"ts": 2620.0, "event": "commit_step", "task_id": "1", "commit": "1111111"},
            ],
        }
        payload = _build_replay_payload(snapshot)
        self.assertEqual([g["label"] for g in payload["gaps"]], ["+15m gap", "+12m gap"])

        html_text = render_dashboard_html(snapshot)
        # Embedded snapshot payload contains gap labels
        m = re.search(r'<script type="application/json" id="snapshot">\s*(.*?)\s*</script>', html_text, re.DOTALL)
        self.assertIsNotNone(m)
        assert m is not None
        embedded = json.loads(m.group(1))
        self.assertEqual([g["label"] for g in embedded["gaps"]], ["+15m gap", "+12m gap"])

        # Gap marker rendering provides <title> tooltip on the marker group
        self.assertIn('class="g-gap-mark"', html_text)
        self.assertIn("<title>${esc(label)}</title>", html_text)


if __name__ == "__main__":
    unittest.main()
