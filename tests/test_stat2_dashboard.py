"""STAT-2 / STAT-6: auto-dev dashboard [--out FILE] [--watch] の再生可能 HTML 生成を検証する。"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.cli import main as cli_main
from auto_developer.dashboard import render_dashboard_html
from auto_developer.metrics import MetricsLog
from auto_developer.multi_team import get_teams_status
from auto_developer.status import STATUS_SCHEMA, collect_status_snapshot

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "two-teams"
_SNAPSHOT_RE = re.compile(
    r'<script[^>]*id="snapshot"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_MAIN_SCRIPT_RE = re.compile(
    r'<script>\s*(\(\(\)\s*=>\s*\{.*?\}\)\(\);)\s*</script>',
    re.DOTALL,
)


def _extract_embedded_snapshot(html_text: str) -> dict[str, object]:
    m = _SNAPSHOT_RE.search(html_text)
    if m is None:
        raise AssertionError("Embedded <script id='snapshot'> not found")
    return json.loads(m.group(1))


def _run_dashboard_js_in_node(html_text: str, harness_js: str) -> dict[str, object]:
    node_bin = shutil.which("node")
    if not node_bin:
        raise unittest.SkipTest("node is not available")
    snap_m = _SNAPSHOT_RE.search(html_text)
    script_m = _MAIN_SCRIPT_RE.search(html_text)
    if snap_m is None or script_m is None:
        raise AssertionError("Embedded snapshot or main script not found")

    runner = f"""
const SNAPSHOT_TEXT = {json.dumps(snap_m.group(1))};
const elements = new Map();
function makeEl(id) {{
  return {{
    id,
    innerHTML: '',
    textContent: '',
    className: '',
    value: 0,
    style: {{}},
    children: [],
    attrs: {{}},
    classList: {{ toggle() {{}} }},
    setAttribute(k, v) {{ this.attrs[k] = v; }},
    appendChild(c) {{ this.children.push(c); }},
    querySelectorAll() {{ return []; }},
    getBoundingClientRect() {{ return {{ left: 0, width: 972 }}; }},
    onclick: null,
    oninput: null,
  }};
}}
function getEl(id) {{
  if (!elements.has(id)) elements.set(id, makeEl(id));
  if (id === 'snapshot') elements.get(id).textContent = SNAPSHOT_TEXT;
  return elements.get(id);
}}
global.document = {{
  getElementById: getEl,
  createElement: () => makeEl('dyn'),
  querySelectorAll: () => [],
}};
global.location = {{ search: '' }};
global.performance = {{ now: () => 0 }};
global.requestAnimationFrame = () => {{}};
global.URLSearchParams = class {{ has() {{ return false; }} get() {{ return null; }} }};
{script_m.group(1)}
{harness_js}
"""
    proc = subprocess.run(
        [node_bin, "-e", runner],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        raise AssertionError(f"Node JS execution failed ({proc.returncode}):\n{proc.stderr}\n{proc.stdout}")
    return json.loads(proc.stdout.strip())


class TestStat2Dashboard(unittest.TestCase):
    """STAT-2/STAT-6: 単一HTML出力、attention先頭、XSSエスケープ、オフライン完結、schema検証、再生UIを確かめる。"""

    def _init_repo(self, repo: Path, branch: str = "main") -> None:
        subprocess.run(["git", "init", "-b", branch], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (repo / "PLAN.md").write_text(
            "# Plan\n\n"
            "- [ ] 1. **[FAILED]** <script>alert('xss')</script> task\n"
            "- [ ] 2. Normal task </script><script>alert(2)</script>\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

    def test_dashboard_creates_self_contained_escaped_html_with_attention_first(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)

            rc = cli_main(["--work-dir", str(repo), "dashboard"])
            self.assertEqual(rc, 0)

            out_file = repo / ".dev" / "dashboard.html"
            self.assertTrue(out_file.is_file())
            html_text = out_file.read_text(encoding="utf-8")

            # Offline self-contained: no external http/https/script src/link
            self.assertNotIn("http://", html_text)
            self.assertNotIn("https://", html_text)
            self.assertNotIn("<script src", html_text.lower())

            # XSS escaped in HTML and embedded JSON does not break on </script>
            self.assertNotIn("<script>alert('xss')</script>", html_text)
            self.assertIn("&lt;script&gt;alert(&#x27;xss&#x27;)&lt;/script&gt;", html_text)
            embedded = _extract_embedded_snapshot(html_text)
            self.assertEqual(embedded.get("schema"), STATUS_SCHEMA)

            # Attention bar appears before main workspace / Release candidate
            att_idx = html_text.find('id="attention"')
            main_idx = html_text.find('class="main"')
            self.assertNotEqual(att_idx, -1)
            self.assertNotEqual(main_idx, -1)
            self.assertLess(att_idx, main_idx)

    def test_dashboard_custom_out_and_schema_mismatch(self) -> None:
        bad_html = render_dashboard_html({"schema": "unexpected/999", "attention": [], "plan": {"tasks": []}})
        self.assertIn("unexpected/999", bad_html)
        self.assertIn("Schema", bad_html)

        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams"
            shutil.copytree(_EXAMPLE_DIR, trial)
            subprocess.run(["git", "init", "-b", "main"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=trial, check=True, capture_output=True)
            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)

            custom_out = Path(tmp) / "custom_dash.html"
            self.assertEqual(cli_main(["--work-dir", str(trial), "dashboard", "--out", str(custom_out)]), 0)
            self.assertTrue(custom_out.is_file())
            team_html = custom_out.read_text(encoding="utf-8")

            # Teams and main-not-modified integration gate
            self.assertIn("core", team_html)
            self.assertIn("cli", team_html)
            self.assertIn("main", team_html)
            self.assertIn("INTEGRATION GATE", team_html)
            self.assertIn("is never modified", team_html)

            # Prototype UI sections: summary counters, stage, gantt, player, tower, log
            for token in (
                'id="c-tasks"',
                'id="c-reviews"',
                'id="c-retries"',
                'id="c-q"',
                'id="stage"',
                'id="nodes"',
                'id="gantt"',
                'id="gantt-svg"',
                'id="btn-play"',
                'data-speed="1"',
                'data-speed="4"',
                'data-speed="60"',
                'id="scrub"',
                'id="tower"',
                'id="loc"',
                'id="log"',
            ):
                self.assertIn(token, team_html)

    def test_dashboard_single_team_node_and_empty_timeline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo, branch="feat/replay")

            rc = cli_main(["--work-dir", str(repo), "dashboard"])
            self.assertEqual(rc, 0)
            html_text = (repo / ".dev" / "dashboard.html").read_text(encoding="utf-8")
            embedded = _extract_embedded_snapshot(html_text)

            self.assertEqual(embedded.get("mode"), "single")
            teams = embedded.get("teams")
            self.assertIsInstance(teams, list)
            assert isinstance(teams, list)
            self.assertEqual(len(teams), 1)
            self.assertEqual(teams[0]["branch"], "feat/replay")
            self.assertEqual(len(teams[0]["tasks"]), 2)
            self.assertEqual(embedded.get("events"), [])
            self.assertTrue(embedded.get("attention"))

            # Attention bar groups by kind with <details> and count badge
            self.assertIn('<details class="att-group" data-kind="FAILED_TASK">', html_text)
            self.assertIn('<span class="att-cnt">1</span>', html_text)

            # Dead event branches not emitted by Python should not remain
            for dead_kind in ("case 'job_start':", "case 'review_start':", "case 'team_done':", "case 'quarantine':"):
                self.assertNotIn(dead_kind, html_text)

            # Verify in Node that single-team layout does not collide L.plan / L['team:plan'] / L.gate
            res = _run_dashboard_js_in_node(
                html_text,
                """
const nodesHtml = elements.get('nodes').innerHTML;
const lefts = [...nodesHtml.matchAll(/class="node[^"]*" style="left:(\\d+(?:\\.\\d+)?)px/g)].map(m => Number(m[1]));
console.log(JSON.stringify({ lefts, attHtml: elements.get('attention').innerHTML }));
""",
            )
            lefts = res["lefts"]
            self.assertEqual(len(lefts), 3)
            self.assertEqual(len(set(lefts)), 3)
            self.assertIn('data-kind="FAILED_TASK"', str(res["attHtml"]))

    def test_dashboard_timeline_events_and_ten_minute_gap_compression(self) -> None:
        snapshot = {
            "schema": STATUS_SCHEMA,
            "generated_at": "2026-11-24T00:35:00+00:00",
            "repo": {"head": "abc1234", "branch": "main", "reason": None},
            "plan": {"path": "PLAN.md", "tasks": [], "blocked_items": [], "reason": None},
            "quarantine_branches": [],
            "audit": {"present": False, "status": None, "reason": "not found"},
            "teams": None,
            "attention": [],
            "backlog": {
                "plan": [
                    {"task_id": "1", "title": "First task", "status": "completed"},
                    {"task_id": "2", "title": "Second task", "status": "completed"},
                ]
            },
            "timeline": [
                {
                    "ts": 1000.0,
                    "timestamp": "2026-11-24T00:00:00+00:00",
                    "event": "task_start",
                    "team": None,
                    "task_id": "1",
                    "attempt": 1,
                    "maker_model": "gemini-4-hydrogen-medium",
                },
                {
                    "ts": 1060.0,
                    "timestamp": "2026-11-24T00:01:00+00:00",
                    "event": "commit_step",
                    "team": None,
                    "task_id": "1",
                    "commit": "1111111",
                    "additions": 12,
                    "deletions": 2,
                },
                {
                    "ts": 2860.0,
                    "timestamp": "2026-11-24T00:31:00+00:00",
                    "event": "task_start",
                    "team": None,
                    "task_id": "2",
                    "attempt": 1,
                    "maker_model": "gemini-4-hydrogen-medium",
                },
            ],
            "timeline_start": "2026-11-24T00:00:00+00:00",
            "timeline_end": "2026-11-24T00:31:00+00:00",
        }
        html_text = render_dashboard_html(snapshot)
        embedded = _extract_embedded_snapshot(html_text)

        # 30-minute (1800s) gap should be compressed; normal 60s gap preserved
        duration = embedded.get("duration")
        self.assertIsInstance(duration, (int, float))
        assert isinstance(duration, (int, float))
        self.assertGreater(duration, 60.0)
        self.assertLess(duration, 120.0)

        gaps = embedded.get("gaps")
        self.assertIsInstance(gaps, list)
        assert isinstance(gaps, list)
        self.assertEqual(len(gaps), 1)
        self.assertGreaterEqual(gaps[0]["real_gap"], 1800.0)
        self.assertEqual(gaps[0]["label"], "+30m gap")

    def test_gap_boundary_600_vs_601_labels_and_missing_ts(self) -> None:
        base_snap = {
            "schema": STATUS_SCHEMA,
            "repo": {"head": "abc1234", "branch": "main"},
            "plan": {"path": "PLAN.md", "tasks": []},
            "teams": None,
            "attention": [],
            "backlog": {"plan": [{"task_id": "1", "title": "T1", "status": "completed"}]},
        }
        # Exactly 600s -> NOT compressed
        snap_600 = {
            **base_snap,
            "timeline": [
                {"ts": 1000.0, "event": "task_start", "task_id": "1"},
                {"ts": 1600.0, "event": "verify_step", "task_id": "1", "result": "PASSED"},
            ],
        }
        p600 = _extract_embedded_snapshot(render_dashboard_html(snap_600))
        self.assertEqual(p600["gaps"], [])
        self.assertEqual(p600["duration"], 600.0)

        # 601s -> compressed to 3s with "+10m gap"
        snap_601 = {
            **base_snap,
            "timeline": [
                {"ts": 1000.0, "event": "task_start", "task_id": "1"},
                {"ts": 1601.0, "event": "verify_step", "task_id": "1", "result": "PASSED"},
            ],
        }
        p601 = _extract_embedded_snapshot(render_dashboard_html(snap_601))
        self.assertEqual(p601["duration"], 3.0)
        self.assertEqual(
            p601["gaps"],
            [{"t0": 0.0, "t1": 3.0, "real_gap": 601.0, "label": "+10m gap"}],
        )

        # Hour-scale gap label and missing ts (ISO timestamp fallback + completely missing ts)
        snap_mixed_ts = {
            **base_snap,
            "timeline": [
                {"ts": None, "timestamp": "2026-11-24T00:00:00+00:00", "event": "task_start", "task_id": "1"},
                {"ts": None, "timestamp": None, "event": "verify_step", "task_id": "1", "result": "FAILED"},
                {"ts": None, "timestamp": "2026-11-24T00:01:00+00:00", "event": "verify_step", "task_id": "1", "result": "PASSED"},
                {"ts": None, "timestamp": "2026-11-24T01:06:00+00:00", "event": "commit_step", "task_id": "1", "commit": "1111111"},
            ],
        }
        pmix = _extract_embedded_snapshot(render_dashboard_html(snap_mixed_ts))
        self.assertEqual(pmix["duration"], 64.0)
        gaps_mix = pmix["gaps"]
        assert isinstance(gaps_mix, list)
        self.assertEqual(len(gaps_mix), 1)
        self.assertEqual(gaps_mix[0]["label"], "+1h05m gap")

    def test_e2e_single_team_events_jsonl_and_old_phase_attention_filtering(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text(
                "# PLAN.md\n\n"
                "## 現在の目標\n"
                "- Phase 73\n\n"
                "## ステップ\n"
                "- [x] 1. Add alpha\n"
                "- [ ] 2. Pending beta\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
            base_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

            (repo / "alpha.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "feat: alpha [refs: 1]"], cwd=repo, check=True, capture_output=True)
            commit_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()

            metrics = MetricsLog(repo_dir=repo, plan_path=repo / "PLAN.md")
            metrics.events_path.parent.mkdir(parents=True, exist_ok=True)
            records = [
                # Old phase failures for task_id=2 must NOT leak into current phase attention[]
                {"schema": 1, "ts": 500, "event": "verify_step", "phase": "Phase 72", "head": base_sha, "task_id": "2", "result": "FAILED"},
                {"schema": 1, "ts": 510, "event": "checker_review", "phase": "Phase 72", "head": base_sha, "task_id": "2", "decision": "REJECT", "model": "old-checker"},
                # Current phase events
                {"schema": 1, "ts": 1000, "event": "verify_step", "phase": "Phase 73", "head": base_sha, "task_id": "1", "result": "FAILED", "exit_code": 1},
                {"schema": 1, "ts": 1030, "event": "verify_step", "phase": "Phase 73", "head": base_sha, "task_id": "1", "result": "PASSED", "exit_code": 0},
                {"schema": 1, "ts": 1040, "event": "checker_review", "phase": "Phase 73", "head": base_sha, "task_id": "1", "decision": "APPROVE", "model": "claude-opus-4-6"},
                {"schema": 1, "ts": 1050, "event": "commit_step", "phase": "Phase 73", "head": commit_sha, "task_id": "1", "commit": commit_sha},
                {"schema": 1, "ts": 2000, "event": "task_start", "phase": "Phase 73", "head": commit_sha, "task_id": "2", "attempt": 1, "maker_model": "gemini-4"},
            ]
            metrics.events_path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")

            snap = collect_status_snapshot(repo)
            att_kinds = [a["kind"] for a in snap["attention"]]
            self.assertNotIn("VERIFY_FAILURE", att_kinds)
            self.assertNotIn("CHECKER_REJECT", att_kinds)

            html_text = render_dashboard_html(snap)
            embedded = _extract_embedded_snapshot(html_text)
            self.assertEqual(embedded["mode"], "single")
            events = embedded["events"]
            assert isinstance(events, list)
            self.assertEqual([e["kind"] for e in events], ["verify", "verify", "review", "commit", "task_start"])
            self.assertTrue(all(e["team"] == "plan" for e in events))
            commit_ev = events[3]
            self.assertEqual(commit_ev["add"], 2)
            self.assertEqual(commit_ev["del"], 0)
            self.assertEqual(commit_ev["title"], "Add alpha")
            self.assertEqual(len(embedded["gaps"]), 1)
            self.assertEqual(embedded["duration"], 53.0)

    def test_e2e_multi_team_events_jsonl_routing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams"
            shutil.copytree(_EXAMPLE_DIR, trial)
            subprocess.run(["git", "init", "-b", "main"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=trial, check=True, capture_output=True)
            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)

            teams_payload, rc_status, _ = get_teams_status(trial)
            self.assertEqual(rc_status, 0)
            assert isinstance(teams_payload, dict)
            job_id = str(teams_payload["job_id"])
            core_wt = trial / ".dev" / "worktrees" / "core"
            (core_wt / "core_mod.py").write_text("x = 1\ny = 2\nz = 3\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=core_wt, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "core: step 1 [refs: 1]"], cwd=core_wt, check=True, capture_output=True)
            core_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=core_wt, check=True, capture_output=True, text=True).stdout.strip()

            core_events = MetricsLog(repo_dir=core_wt, team_id="core").events_path
            core_events.parent.mkdir(parents=True, exist_ok=True)
            core_events.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 100, "event": "verify_step", "task_id": "1", "result": "PASSED"}),
                    json.dumps({"schema": 1, "ts": 110, "event": "commit_step", "task_id": "1", "commit": core_sha}),
                ]) + "\n",
                encoding="utf-8",
            )
            main_events = MetricsLog(repo_dir=trial).events_path
            main_events.parent.mkdir(parents=True, exist_ok=True)
            main_events.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 105, "event": "task_start", "job_id": job_id, "team": "cli", "task_id": "1", "attempt": 1}),
                    json.dumps({"schema": 1, "ts": 120, "event": "integration_start", "job_id": job_id}),
                ]) + "\n",
                encoding="utf-8",
            )

            snap = collect_status_snapshot(trial)
            embedded = _extract_embedded_snapshot(render_dashboard_html(snap))
            self.assertEqual(embedded["mode"], "teams")
            events = embedded["events"]
            assert isinstance(events, list)
            self.assertEqual([(e["team"], e["kind"]) for e in events], [("core", "verify"), ("cli", "task_start"), ("core", "commit"), (None, "integration_start")])
            self.assertEqual(events[2]["add"], 3)
            self.assertEqual(events[2]["del"], 0)
            self.assertTrue(events[2]["title"])

    def test_js_replay_backlog_retries_attention_grouping_and_gantt_gap_labels(self) -> None:
        snapshot = {
            "schema": STATUS_SCHEMA,
            "repo": {"head": "abc1234", "branch": "main"},
            "plan": {"path": "PLAN.md", "tasks": []},
            "teams": None,
            "attention": [
                {"kind": "VERIFY_FAILURE", "severity": "HIGH", "message": "t1 failed"},
                {"kind": "VERIFY_FAILURE", "severity": "HIGH", "message": "t2 failed"},
                {"kind": "QUARANTINE_BRANCH", "severity": "HIGH", "message": "Quarantine branch present: quarantine/task-2"},
            ],
            "backlog": {
                "plan": [
                    {"task_id": "1", "title": "Task One", "status": "completed"},
                    {"task_id": "2", "title": "Task Two", "status": "pending"},
                    {"task_id": "3", "title": "Task Three", "status": "pending"},
                ]
            },
            "timeline": [
                # Task 1: no task_start, 3 verify_step events -> should leave backlog at t=0 and count 2 retries
                {"ts": 1000.0, "event": "verify_step", "task_id": "1", "result": "FAILED", "exit_code": 1},
                {"ts": 1010.0, "event": "verify_step", "task_id": "1", "result": "FAILED", "exit_code": 1},
                {"ts": 1020.0, "event": "verify_step", "task_id": "1", "result": "PASSED", "exit_code": 0},
                # Close gap #1 (+15m)
                {"ts": 1920.0, "event": "commit_step", "task_id": "1", "commit": "1111111", "additions": 5, "deletions": 1},
                # Close gap #2 (+12m) right next to gap #1 -> second gap label should be suppressed
                {"ts": 2640.0, "event": "task_start", "task_id": "2", "attempt": 2},
                # Task 2: 2 verify_step events -> +1 retry (total 3), then rollback (deduped against QUARANTINE_BRANCH)
                {"ts": 2645.0, "event": "verify_step", "task_id": "2", "result": "FAILED", "exit_code": 1},
                {"ts": 2650.0, "event": "verify_step", "task_id": "2", "result": "FAILED", "exit_code": 1},
                {"ts": 2655.0, "event": "rollback_step", "task_id": "2", "reason": "failed"},
                # Far gap #3 (+20m) after 300s of normal work -> third gap label should render
                {"ts": 2955.0, "event": "task_start", "task_id": "3", "attempt": 1},
                {"ts": 4155.0, "event": "commit_step", "task_id": "3", "commit": "3333333", "additions": 1, "deletions": 0},
            ],
        }
        html_text = render_dashboard_html(snapshot)
        res = _run_dashboard_js_in_node(
            html_text,
            """
const at0 = {
  retries: elements.get('c-retries').textContent,
  nodes: elements.get('nodes').innerHTML,
  att: elements.get('attention').innerHTML,
};
document.getElementById('scrub').value = 1000;
document.getElementById('scrub').oninput();
const atEnd = {
  retries: elements.get('c-retries').textContent,
  tasks: elements.get('c-tasks').textContent,
  q: elements.get('c-q').textContent,
  nodes: elements.get('nodes').innerHTML,
  att: elements.get('attention').innerHTML,
  gantt: elements.get('gantt-svg').innerHTML,
};
document.getElementById('gantt').onclick({ clientX: 70 });
const afterClickClock = elements.get('clock').textContent;
console.log(JSON.stringify({ at0, atEnd, afterClickClock }));
""",
        )
        at0 = res["at0"]
        at_end = res["atEnd"]
        assert isinstance(at0, dict) and isinstance(at_end, dict)

        # At t=0, task 1 already had verify_step so backlog has 2 / 3 LEFT (tasks 2 and 3)
        self.assertIn("2 / 3 LEFT", str(at0["nodes"]))
        self.assertEqual(str(at0["retries"]), "0")

        # At end: task 1 had 3 verifies (2 retries), task 2 had 2 verifies (1 retry) => 3 retries
        self.assertEqual(str(at_end["retries"]), "3")
        self.assertEqual(str(at_end["q"]), "1")
        self.assertIn("0 / 3 LEFT", str(at_end["nodes"]))

        # Attention grouped by kind with counts, and rollback did NOT duplicate QUARANTINE_BRANCH
        att_html = str(at_end["att"])
        self.assertIn('data-kind="VERIFY_FAILURE"', att_html)
        self.assertIn('<span class="att-cnt">2</span>', att_html)
        self.assertIn('data-kind="QUARANTINE_BRANCH"', att_html)
        self.assertEqual(att_html.count("Quarantine branch present"), 1)

        # Gantt has 3 gap rects, but only 2 non-overlapping gap labels (+15m gap and +20m gap)
        gantt_svg = str(at_end["gantt"])
        self.assertEqual(gantt_svg.count('fill="var(--review-bg)"'), 3)
        gap_labels = re.findall(r'<text class="g-gap"[^>]*>([^<]+)</text>', gantt_svg)
        self.assertEqual(gap_labels, ["+15m gap", "+20m gap"])

        # Gantt click at x=70 (G.left) seeks back to 00:00
        self.assertTrue(str(res["afterClickClock"]).startswith("00:00 / "))

    def test_stat9_minimum_event_spacing_preserves_order_and_gap_compression(self) -> None:
        """STAT-9: 同秒・1秒未満間隔のイベントが最低1秒ずつ空いて記録順が保たれ、601s空白圧縮と両立すること。"""
        base_snap = {
            "schema": STATUS_SCHEMA,
            "repo": {"head": "abc1234", "branch": "main"},
            "plan": {"path": "PLAN.md", "tasks": []},
            "teams": None,
            "attention": [],
            "backlog": {"plan": [{"task_id": "1", "title": "T1", "status": "completed"}]},
        }

        # 1. 同秒の3件のイベント: 1秒ずつ空いて記録順が保たれること
        snap_same_sec = {
            **base_snap,
            "timeline": [
                {"ts": 1000.0, "event": "task_start", "task_id": "1", "attempt": 1},
                {"ts": 1000.0, "event": "verify_step", "task_id": "1", "result": "PASSED"},
                {"ts": 1000.0, "event": "commit_step", "task_id": "1", "commit": "1111111"},
            ],
        }
        p_same = _extract_embedded_snapshot(render_dashboard_html(snap_same_sec))
        ev_same = p_same["events"]
        assert isinstance(ev_same, list)
        self.assertEqual([e["kind"] for e in ev_same], ["task_start", "verify", "commit"])
        self.assertEqual([e["t"] for e in ev_same], [0.0, 1.0, 2.0])
        self.assertEqual(p_same["duration"], 2.0)

        # 2. 1秒未満の間隔のイベント: 最低1秒ずつ空くこと
        snap_subsec = {
            **base_snap,
            "timeline": [
                {"ts": 1000.0, "event": "task_start", "task_id": "1", "attempt": 1},
                {"ts": 1000.2, "event": "verify_step", "task_id": "1", "result": "FAILED"},
                {"ts": 1000.7, "event": "verify_step", "task_id": "1", "result": "PASSED"},
            ],
        }
        p_sub = _extract_embedded_snapshot(render_dashboard_html(snap_subsec))
        ev_sub = p_sub["events"]
        assert isinstance(ev_sub, list)
        self.assertEqual([e["t"] for e in ev_sub], [0.0, 1.0, 2.0])
        self.assertEqual(p_sub["duration"], 2.0)

        # 3. 同秒イベントと10分超の空白圧縮の組み合わせ
        snap_combo = {
            **base_snap,
            "timeline": [
                {"ts": 1000.0, "event": "task_start", "task_id": "1", "attempt": 1},
                {"ts": 1000.0, "event": "verify_step", "task_id": "1", "result": "PASSED"},
                {"ts": 1000.0, "event": "checker_review", "task_id": "1", "decision": "APPROVE"},
                {"ts": 1601.0, "event": "commit_step", "task_id": "1", "commit": "1111111"},
                {"ts": 1601.0, "event": "task_start", "task_id": "2", "attempt": 1},
            ],
        }
        p_combo = _extract_embedded_snapshot(render_dashboard_html(snap_combo))
        ev_combo = p_combo["events"]
        assert isinstance(ev_combo, list)
        self.assertEqual([e["t"] for e in ev_combo], [0.0, 1.0, 2.0, 5.0, 6.0])
        self.assertEqual(p_combo["duration"], 6.0)
        self.assertEqual(
            p_combo["gaps"],
            [{"t0": 2.0, "t1": 5.0, "real_gap": 601.0, "label": "+10m gap"}],
        )

    def test_stat9_multi_team_same_second_preserves_file_order_and_spaces_replay(self) -> None:
        """STAT-9: チームの worktree とリポジトリの記録で同じ時刻のイベントがあってもファイル順を保ち1秒ずつ空くこと。"""
        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams"
            shutil.copytree(_EXAMPLE_DIR, trial)
            subprocess.run(["git", "init", "-b", "main"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=trial, check=True, capture_output=True)
            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)

            teams_payload, rc_status, _ = get_teams_status(trial)
            self.assertEqual(rc_status, 0)
            assert isinstance(teams_payload, dict)
            job_id = str(teams_payload["job_id"])

            core_wt = trial / ".dev" / "worktrees" / "core"
            core_events = MetricsLog(repo_dir=core_wt, team_id="core").events_path
            core_events.parent.mkdir(parents=True, exist_ok=True)
            core_events.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 100, "event": "task_start", "task_id": "1", "attempt": 1}),
                    json.dumps({"schema": 1, "ts": 100, "event": "verify_step", "task_id": "1", "result": "PASSED"}),
                ]) + "\n",
                encoding="utf-8",
            )
            main_events = MetricsLog(repo_dir=trial).events_path
            main_events.parent.mkdir(parents=True, exist_ok=True)
            main_events.write_text(
                "\n".join([
                    json.dumps({"schema": 1, "ts": 100, "event": "integration_start", "job_id": job_id}),
                    json.dumps({"schema": 1, "ts": 100, "event": "rc_tag", "job_id": job_id, "tag": "rc/test"}),
                ]) + "\n",
                encoding="utf-8",
            )

            snap = collect_status_snapshot(trial)
            embedded = _extract_embedded_snapshot(render_dashboard_html(snap))
            events = embedded["events"]
            assert isinstance(events, list)
            self.assertEqual(
                [(e["team"], e["kind"]) for e in events],
                [(None, "integration_start"), (None, "rc_tag"), ("core", "task_start"), ("core", "verify")],
            )
            self.assertEqual([e["t"] for e in events], [0.0, 1.0, 2.0, 3.0])
            self.assertEqual(embedded["duration"], 3.0)

    def test_dashboard_watch_regenerates_html_with_meta_refresh_and_stops_on_ctrl_c(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            sleep_calls: list[float] = []

            def fake_sleep(sec: float) -> None:
                sleep_calls.append(sec)
                if len(sleep_calls) >= 2:
                    raise KeyboardInterrupt

            with patch("time.sleep", side_effect=fake_sleep):
                rc = cli_main(["--work-dir", str(repo), "dashboard", "--watch", "3"])

            self.assertEqual(rc, 0)
            self.assertEqual(sleep_calls, [3, 3])
            out_file = repo / ".dev" / "dashboard.html"
            self.assertTrue(out_file.is_file())
            html_text = out_file.read_text(encoding="utf-8")
            self.assertIn('<meta http-equiv="refresh" content="3">', html_text)


if __name__ == "__main__":
    unittest.main()
