"""STAT-3: Dashboard requirement badges, requirement counts, and DESIGN_DRIFT display tests."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from auto_developer.compiler import IR_SCHEMA_VERSION, anchor_source_range
from auto_developer.dashboard import _build_replay_payload, render_dashboard_html
from auto_developer.status import STATUS_SCHEMA, collect_status_snapshot

_SNAPSHOT_RE = re.compile(
    r'<script[^>]*id="snapshot"[^>]*>(.*?)</script>',
    re.DOTALL,
)
_MAIN_SCRIPT_RE = re.compile(
    r'<script>\s*(\(\(\)\s*=>\s*\{.*?\}\)\(\);)\s*</script>',
    re.DOTALL,
)


def _init_git_repo(repo: Path, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)


def _extract_embedded_snapshot(html_text: str) -> dict[str, Any]:
    m = _SNAPSHOT_RE.search(html_text)
    if m is None:
        raise AssertionError("Embedded <script id='snapshot'> not found")
    parsed = json.loads(m.group(1))
    assert isinstance(parsed, dict)
    return parsed


def _run_dashboard_js_in_node(html_text: str, harness_js: str) -> dict[str, Any]:
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
    parsed = json.loads(proc.stdout.strip())
    assert isinstance(parsed, dict)
    return parsed


def _build_valid_ir(repo: Path, doc_rel: str) -> dict[str, Any]:
    doc_path = repo / doc_rel
    doc_sha = hashlib.sha256(doc_path.read_bytes()).hexdigest()
    _, a1, _ = anchor_source_range(f"{doc_rel}:3-4", work_dir=repo)
    _, a2, _ = anchor_source_range(f"{doc_rel}:5-5", work_dir=repo)
    _, a_ac, _ = anchor_source_range(f"{doc_rel}:7-7", work_dir=repo)
    _, a_ver, _ = anchor_source_range(f"{doc_rel}:9-9", work_dir=repo)
    return {
        "schema": IR_SCHEMA_VERSION,
        "source": [{"path": doc_rel, "sha256": doc_sha}],
        "requirements": [
            {
                "id": "REQ-1",
                "statement": "Display requirement badge on ticket",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": a1,
            },
            {
                "id": "REQ-2",
                "statement": "Update requirement count during replay",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": a2,
            },
        ],
        "acceptance": [
            {
                "id": "AC-1",
                "given": "dashboard is rendered",
                "when": "user views backlog",
                "then": "badges and counts appear",
                "observable": ["dashboard.html"],
                "source": a_ac,
            }
        ],
        "decisions": [],
        "steps": [
            {"n": 1, "title": "Step 1 (REQ-1)", "requirements": ["REQ-1"], "files": ["src/a.py"]},
            {"n": 2, "title": "Step 2 (REQ-2)", "requirements": ["REQ-2"], "files": ["src/b.py"]},
        ],
        "verification": {"commands": ["python3 -m unittest"], "source": a_ver},
    }


class TestStat3DashboardRequirements(unittest.TestCase):
    """STAT-3: 要件の印・数・DESIGN_DRIFT の表示を検証する。"""

    def test_payload_and_html_include_requirements_source_and_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text(
                "# Plan\n\n"
                "## 現在の目標\n"
                "- Phase 77\n\n"
                "## ステップ\n"
                "- [x] 1. First task (REQ-1)\n"
                "- [ ] 2. Second task part A (REQ-2)\n"
                "- [ ] 3. Second task part B（REQ-2, PLAN-9）\n",
                encoding="utf-8",
            )
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True)
            (designs_dir / "feature.md").write_text(
                "# Feature\n\n"
                "REQ-1 line 3\n"
                "REQ-1 line 4\n"
                "REQ-2 line 5\n\n"
                "AC-1: Observable\n\n"
                "Verify: unittest\n",
                encoding="utf-8",
            )
            ir_data = _build_valid_ir(repo, "docs/designs/feature.md")
            (designs_dir / "feature.ir.json").write_text(json.dumps(ir_data, indent=2), encoding="utf-8")

            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            snapshot = collect_status_snapshot(repo)
            payload = _build_replay_payload(snapshot)

            self.assertIn("requirements", payload)
            reqs = {r["id"]: r for r in payload["requirements"]}
            self.assertEqual(set(reqs.keys()), {"REQ-1", "REQ-2", "PLAN-9"})
            self.assertEqual(reqs["REQ-1"]["statement"], "Display requirement badge on ticket")
            self.assertEqual(reqs["REQ-1"]["source"], "docs/designs/feature.md:L3-L4")
            self.assertEqual(reqs["REQ-2"]["statement"], "Update requirement count during replay")
            self.assertEqual(reqs["REQ-2"]["source"], "docs/designs/feature.md:L5-L5")
            self.assertIsNone(reqs["PLAN-9"]["statement"])
            self.assertIsNone(reqs["PLAN-9"]["source"])

            tasks = payload["teams"][0]["tasks"]
            self.assertEqual(tasks[0]["reqs"], ["REQ-1"])
            self.assertEqual(tasks[1]["reqs"], ["REQ-2"])
            self.assertEqual(tasks[2]["reqs"], ["REQ-2", "PLAN-9"])

            html_text = render_dashboard_html(snapshot)
            self.assertIn('id="c-reqs"', html_text)
            self.assertIn('id="c-reqs-wrap"', html_text)
            self.assertIn("0/3", html_text)
            self.assertIn("REQ-1", html_text)
            self.assertIn("Display requirement badge on ticket", html_text)
            self.assertIn("docs/designs/feature.md:L3-L4", html_text)
            self.assertIn("PLAN-9", html_text)
            self.assertIn("req-badge", html_text)

    def test_no_requirements_hides_counter_and_omits_badges(self) -> None:
        snapshot: dict[str, Any] = {
            "schema": STATUS_SCHEMA,
            "repo": {"head": "abc1234", "branch": "main"},
            "plan": {"path": "PLAN.md", "tasks": []},
            "teams": None,
            "attention": [],
            "backlog": {"plan": [{"task_id": "1", "title": "Plain task", "status": "pending"}]},
            "requirements": [],
        }
        payload = _build_replay_payload(snapshot)
        self.assertEqual(payload.get("requirements"), [])
        self.assertEqual(payload["teams"][0]["tasks"][0].get("reqs"), [])

        html_text = render_dashboard_html(snapshot)
        self.assertIn('id="c-reqs-wrap" style="display:none"', html_text)

    def test_design_drift_renders_in_attention_bar(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# Plan\n\n- [ ] 1. Task (REQ-1)\n", encoding="utf-8")
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True)
            doc_path = designs_dir / "feature.md"
            doc_path.write_text(
                "# Feature\n\n"
                "REQ-1 line 3\n"
                "REQ-1 line 4\n"
                "REQ-2 line 5\n\n"
                "AC-1: Observable\n\n"
                "Verify: unittest\n",
                encoding="utf-8",
            )
            ir_data = _build_valid_ir(repo, "docs/designs/feature.md")
            (designs_dir / "feature.ir.json").write_text(json.dumps(ir_data, indent=2), encoding="utf-8")

            # Mutate design doc after IR approval
            doc_path.write_text(
                "# Feature\n\n"
                "REQ-1 MODIFIED line 3\n"
                "REQ-1 line 4\n"
                "REQ-2 line 5\n\n"
                "AC-1: Observable\n\n"
                "Verify: unittest\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            snapshot = collect_status_snapshot(repo)
            payload = _build_replay_payload(snapshot)
            drift_att = [a for a in payload["attention"] if a.get("kind") == "DESIGN_DRIFT"]
            self.assertEqual(len(drift_att), 1)
            self.assertIn("feature.ir.json", drift_att[0]["text"])

            html_text = render_dashboard_html(snapshot)
            self.assertIn('data-kind="DESIGN_DRIFT"', html_text)
            att_idx = html_text.find('id="attention"')
            main_idx = html_text.find('class="main"')
            self.assertLess(att_idx, main_idx)

    def test_js_replay_increments_reqs_done_and_renders_badges_with_tooltips(self) -> None:
        snapshot = {
            "schema": STATUS_SCHEMA,
            "repo": {"head": "abc1234", "branch": "main"},
            "plan": {"path": "PLAN.md", "tasks": []},
            "teams": None,
            "attention": [],
            "backlog": {
                "plan": [
                    {"task_id": "1", "title": "Task one (REQ-1)", "status": "completed"},
                    {"task_id": "2", "title": "Task two A (REQ-2)", "status": "completed"},
                    {"task_id": "3", "title": "Task two B (REQ-2, PLAN-9)", "status": "completed"},
                ]
            },
            "requirements": [
                {
                    "id": "REQ-1",
                    "statement": "First <script>req</script>",
                    "source": {"path": "docs/designs/f.md", "lines": [3, 4], "sha256": "aaa"},
                    "tasks": [{"task_id": "1", "title": "Task one (REQ-1)", "status": "completed"}],
                    "status": "done",
                },
                {
                    "id": "REQ-2",
                    "statement": "Second req",
                    "source": {"path": "docs/designs/f.md", "lines": [5, 6], "sha256": "bbb"},
                    "tasks": [
                        {"task_id": "2", "title": "Task two A (REQ-2)", "status": "completed"},
                        {"task_id": "3", "title": "Task two B (REQ-2, PLAN-9)", "status": "completed"},
                    ],
                    "status": "done",
                },
                {
                    "id": "PLAN-9",
                    "statement": None,
                    "source": None,
                    "tasks": [{"task_id": "3", "title": "Task two B (REQ-2, PLAN-9)", "status": "completed"}],
                    "status": "done",
                },
            ],
            "timeline": [
                {"ts": 1000.0, "event": "task_start", "task_id": "1", "attempt": 1},
                {"ts": 1010.0, "event": "commit_step", "task_id": "1", "commit": "1111111"},
                {"ts": 1020.0, "event": "task_start", "task_id": "2", "attempt": 1},
                {"ts": 1030.0, "event": "commit_step", "task_id": "2", "commit": "2222222"},
                {"ts": 1040.0, "event": "task_start", "task_id": "3", "attempt": 1},
                {"ts": 1050.0, "event": "commit_step", "task_id": "3", "commit": "3333333"},
            ],
        }
        html_text = render_dashboard_html(snapshot)
        # XSS escaped in embedded JSON
        self.assertNotIn("<script>req</script>", html_text)

        res = _run_dashboard_js_in_node(
            html_text,
            """
const at0 = {
  reqs: elements.get('c-reqs').textContent,
  wrapDisplay: elements.get('c-reqs-wrap').style.display,
  nodes: elements.get('nodes').innerHTML,
};
document.getElementById('scrub').value = 200; // t = 10s (after task 1 commit)
document.getElementById('scrub').oninput();
const at1 = {
  reqs: elements.get('c-reqs').textContent,
  nodes: elements.get('nodes').innerHTML,
};
document.getElementById('scrub').value = 600; // t = 30s (after task 2 commit, before task 3)
document.getElementById('scrub').oninput();
const at2 = {
  reqs: elements.get('c-reqs').textContent,
};
document.getElementById('scrub').value = 1000; // t = 50s (all committed)
document.getElementById('scrub').oninput();
const atEnd = {
  reqs: elements.get('c-reqs').textContent,
  nodes: elements.get('nodes').innerHTML,
};
console.log(JSON.stringify({ at0, at1, at2, atEnd }));
""",
        )
        self.assertEqual(res["at0"]["reqs"], "0/3")
        self.assertNotEqual(res["at0"]["wrapDisplay"], "none")
        # At t=0: task 1 is active in team node (task_start at t=0), tasks 2 & 3 are in backlog
        self.assertIn("First &lt;script&gt;req&lt;/script&gt;", str(res["at0"]["nodes"]))
        self.assertIn("docs/designs/f.md:L3-L4", str(res["at0"]["nodes"]))
        self.assertIn('title="PLAN-9">PLAN-9</span>', str(res["at0"]["nodes"]))

        # After task 1 commits: REQ-1 is done => 1/3
        self.assertEqual(res["at1"]["reqs"], "1/3")
        # After task 2 commits: REQ-2 still needs task 3 => still 1/3
        self.assertEqual(res["at2"]["reqs"], "1/3")
        # After task 3 commits: REQ-1, REQ-2, PLAN-9 all done => 3/3
        self.assertEqual(res["atEnd"]["reqs"], "3/3")


if __name__ == "__main__":
    unittest.main()
