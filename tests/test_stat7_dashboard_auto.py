"""STAT-7: ダッシュボードの自動更新 (--enable/--disable, run/teams run --dashboard, worktree, LIVE, 非干渉, スキル) を検証する。"""

from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from auto_developer.cli import main as cli_main
from auto_developer.dashboard import render_dashboard_html
from auto_developer.metrics import MetricsLog
from auto_developer.status import collect_status_snapshot

_REPO_ROOT = Path(__file__).resolve().parent.parent
_NIGHTLY_RUN_SKILL = _REPO_ROOT / "skills" / "nightly-run" / "SKILL.md"
_NIGHTLY_SETUP_SKILL = _REPO_ROOT / "skills" / "nightly-setup" / "SKILL.md"


def _init_git_repo(repo: Path, branch: str = "main") -> None:
    subprocess.run(["git", "init", "-b", branch], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
    (repo / "SPEC.md").write_text("# Spec\n\n- [ ] **APP-1**: x is 1.\n", encoding="utf-8")
    (repo / "PLAN.md").write_text(
        "# Plan\n\n## 現在の目標\n- Phase 1: test\n\n## ステップ\n- [ ] 1. First task\n",
        encoding="utf-8",
    )
    ok_sh = repo / "scripts" / "verify_local.sh"
    ok_sh.parent.mkdir(parents=True, exist_ok=True)
    ok_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    ok_sh.chmod(0o755)
    (repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)


def _extract_payload(html_text: str) -> dict[str, object]:
    raw = html_text.split('<script type="application/json" id="snapshot">', 1)[1].split("</script>", 1)[0]
    return json.loads(raw)


class TestStat7DashboardAuto(unittest.TestCase):
    """STAT-7: 自動更新設定、イベント連動再生成、worktree 連携、LIVE 表示、非干渉、スキル。"""

    def test_enable_and_disable_writes_config_and_controls_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            custom_out = repo / ".dev" / "custom_dash.html"

            rc_en = cli_main(["--work-dir", str(repo), "dashboard", "--enable", "--out", str(custom_out)])
            self.assertEqual(rc_en, 0)

            cfg_path = repo / ".dev" / "dashboard.json"
            self.assertTrue(cfg_path.is_file(), ".dev/dashboard.json must be created on --enable")
            cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertTrue(cfg.get("enabled"))
            self.assertEqual(Path(cfg["out"]).resolve(), custom_out.resolve())
            self.assertTrue(custom_out.is_file(), "--enable must write initial dashboard HTML")
            self.assertIn("LIVE", custom_out.read_text(encoding="utf-8"))
            self.assertIn('<meta http-equiv="refresh"', custom_out.read_text(encoding="utf-8"))

            # Remove HTML, then record a STAT-4 event -> should regenerate
            custom_out.unlink()
            metrics = MetricsLog(repo_dir=repo)
            self.assertTrue(metrics.record_event("task_start", task_id="1", attempt=1))
            self.assertTrue(custom_out.is_file(), "STAT-4 event must regenerate dashboard when enabled")

            # Disable -> subsequent STAT-4 event must NOT regenerate
            rc_dis = cli_main(["--work-dir", str(repo), "dashboard", "--disable"])
            self.assertEqual(rc_dis, 0)
            cfg_after = json.loads(cfg_path.read_text(encoding="utf-8"))
            self.assertFalse(cfg_after.get("enabled"))

            custom_out.unlink()
            self.assertTrue(metrics.record_event("verify_step", task_id="1", attempt=1, result="PASSED"))
            self.assertFalse(custom_out.exists(), "STAT-4 event must NOT regenerate dashboard when disabled")

    def test_enable_disable_watch_error_handling(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)

            err_buf = io.StringIO()
            with redirect_stderr(err_buf):
                rc_both = cli_main(["--work-dir", str(repo), "dashboard", "--enable", "--disable"])
                rc_watch_en = cli_main(["--work-dir", str(repo), "dashboard", "--watch", "3", "--enable"])
                rc_watch_dis = cli_main(["--work-dir", str(repo), "dashboard", "--watch", "3", "--disable"])
            self.assertEqual(rc_both, 2)
            self.assertEqual(rc_watch_en, 2)
            self.assertEqual(rc_watch_dis, 2)

            # Initial write failure during --enable: config is saved, warning printed, exit 0 (INV-8)
            blocked_dir = repo / ".dev" / "blocked_init"
            blocked_dir.mkdir(parents=True)
            warn_buf = io.StringIO()
            with redirect_stderr(warn_buf):
                rc_fail_init = cli_main(["--work-dir", str(repo), "dashboard", "--enable", "--out", str(blocked_dir)])
            self.assertEqual(rc_fail_init, 0)
            self.assertTrue((repo / ".dev" / "dashboard.json").is_file())
            self.assertIn("DASHBOARD_WARNING", warn_buf.getvalue())

    def test_worktree_event_regenerates_main_repo_dashboard(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            wt_dir = Path(tmp) / "wt_alpha"
            subprocess.run(
                ["git", "worktree", "add", "-b", "team/alpha", str(wt_dir), "main"],
                cwd=repo,
                check=True,
                capture_output=True,
            )

            rc_en = cli_main(["--work-dir", str(repo), "dashboard", "--enable"])
            self.assertEqual(rc_en, 0)

            main_dash = repo / ".dev" / "dashboard.html"
            self.assertTrue(main_dash.is_file())
            main_dash.unlink()

            # Record STAT-4 event from inside the worktree
            wt_metrics = MetricsLog(repo_dir=wt_dir, team_id="alpha")
            self.assertTrue(wt_metrics.record_event("task_start", task_id="1", attempt=1))

            self.assertTrue(
                main_dash.is_file(),
                "STAT-4 event in a team worktree must regenerate the main repo's dashboard",
            )
            self.assertFalse(
                (wt_dir / ".dev" / "dashboard.html").exists(),
                "Dashboard must be written to main repo, not inside worktree",
            )

    def test_custom_plan_path_preserved_on_event_regeneration(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            custom_plan = repo / "CUSTOM_PLAN.md"
            custom_plan.write_text(
                "# Custom Plan\n\n## 現在の目標\n- Phase 2: custom\n\n## ステップ\n- [ ] 1. CustomUniqueTaskXYZ\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add custom plan"], cwd=repo, check=True, capture_output=True)

            self.assertEqual(cli_main(["--work-dir", str(repo), "dashboard", "--enable"]), 0)
            dash_file = repo / ".dev" / "dashboard.html"
            dash_file.unlink()

            metrics = MetricsLog(repo_dir=repo, plan_path=custom_plan)
            self.assertTrue(metrics.record_event("task_start", task_id="1", attempt=1))
            self.assertTrue(dash_file.is_file())
            self.assertIn("CustomUniqueTaskXYZ", dash_file.read_text(encoding="utf-8"))

    def test_run_with_dashboard_flag_updates_ephemerally_and_respects_configured_out(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            maker_sh = repo / "maker_ok.sh"
            maker_sh.write_text("#!/usr/bin/env bash\necho 'x=1' >> app.txt\n", encoding="utf-8")
            maker_sh.chmod(0o755)
            checker_sh = repo / "checker_ok.sh"
            checker_sh.write_text("#!/usr/bin/env bash\necho 'DECISION: APPROVE'\n", encoding="utf-8")
            checker_sh.chmod(0o755)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add scripts"], cwd=repo, check=True, capture_output=True)

            # Configure custom out path via --enable --out, then --disable (disabled persistently, but out is saved)
            custom_out = repo / ".dev" / "my_dash.html"
            self.assertEqual(
                cli_main(["--work-dir", str(repo), "dashboard", "--enable", "--out", str(custom_out)]),
                0,
            )
            self.assertEqual(cli_main(["--work-dir", str(repo), "dashboard", "--disable"]), 0)
            custom_out.unlink()

            prev_dash_env = os.environ.get("AUTO_DEV_DASHBOARD")
            rc = cli_main([
                "--work-dir", str(repo),
                "run",
                "--maker-cmd", str(maker_sh),
                "--checker-cmd", str(checker_sh),
                "--maker-model", "gemini-3.8-flash",
                "--checker-model", "claude-opus-5.5",
                "--dashboard",
            ])
            self.assertEqual(rc, 0)
            self.assertTrue(custom_out.is_file(), "run --dashboard must respect configured out path")
            payload = _extract_payload(custom_out.read_text(encoding="utf-8"))
            kinds = {ev.get("kind") for ev in payload.get("events", [])}  # type: ignore[union-attr]
            self.assertIn("task_start", kinds)
            self.assertIn("verify", kinds)
            self.assertIn("review", kinds)
            self.assertIn("commit", kinds)
            cfg = json.loads((repo / ".dev" / "dashboard.json").read_text(encoding="utf-8"))
            self.assertFalse(cfg.get("enabled"), "run --dashboard must not flip persistent enabled=true")
            self.assertEqual(os.environ.get("AUTO_DEV_DASHBOARD"), prev_dash_env)

    def test_teams_run_with_dashboard_flag_updates_ephemerally(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            rc_init = cli_main(["--work-dir", str(repo), "teams", "init", "--teams", "alpha,beta"])
            self.assertEqual(rc_init, 0)
            maker_sh = repo / "maker_ok.sh"
            maker_sh.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "branch=$(git rev-parse --abbrev-ref HEAD)\n"
                "team=${branch#team/}\n"
                "mkdir -p \"src/$team\"\n"
                "echo \"$team=1\" >> \"src/$team/out.txt\"\n",
                encoding="utf-8",
            )
            maker_sh.chmod(0o755)
            checker_sh = repo / "checker_ok.sh"
            checker_sh.write_text("#!/usr/bin/env bash\necho 'DECISION: APPROVE'\n", encoding="utf-8")
            checker_sh.chmod(0o755)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "teams init"], cwd=repo, check=True, capture_output=True)

            dash_file = repo / ".dev" / "dashboard.html"
            self.assertFalse(dash_file.exists())

            rc_run = cli_main([
                "--work-dir", str(repo),
                "teams", "run",
                "--maker-cmd", str(maker_sh),
                "--checker-cmd", str(checker_sh),
                "--maker-model", "gemini-3.8-flash",
                "--checker-model", "claude-opus-5.5",
                "--dashboard",
            ])
            self.assertEqual(rc_run, 0)
            self.assertTrue(dash_file.is_file(), "teams run --dashboard must generate dashboard HTML")
            html_text = dash_file.read_text(encoding="utf-8")
            self.assertIn("LIVE", html_text)
            payload = _extract_payload(html_text)
            events = payload.get("events", [])
            self.assertIsInstance(events, list)
            commit_teams = {
                ev.get("team")
                for ev in events  # type: ignore[union-attr]
                if isinstance(ev, dict) and ev.get("kind") == "commit"
            }
            self.assertEqual(
                commit_teams,
                {"alpha", "beta"},
                "Worker subprocesses must propagate AUTO_DEV_DASHBOARD and update main dashboard",
            )
            kinds = {ev.get("kind") for ev in events if isinstance(ev, dict)}  # type: ignore[union-attr]
            self.assertIn("rc_tag", kinds)
            self.assertFalse(
                (repo / ".dev" / "dashboard.json").exists(),
                "teams run --dashboard must not persist .dev/dashboard.json",
            )

    def test_live_html_shows_live_pill_and_seeks_to_end_of_timeline(self) -> None:
        node_bin = shutil.which("node")
        if not node_bin:
            self.skipTest("node is not available")

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            metrics = MetricsLog(repo_dir=repo)
            metrics.record_event("task_start", task_id="1", attempt=1)
            metrics.record_event("verify_step", task_id="1", attempt=1, result="PASSED")

            snap = collect_status_snapshot(repo)
            html_text = render_dashboard_html(snap, refresh_seconds=5)
            self.assertIn('<meta http-equiv="refresh" content="5">', html_text)
            self.assertIn('class="pill live" id="mode-pill">LIVE<', html_text)

            snap_m = html_text.split('<script type="application/json" id="snapshot">', 1)[1].split("</script>", 1)[0]
            script_m = html_text.split("<script>\n(() => {", 1)[1].split("})();\n</script>", 1)[0]
            script_full = "(() => {" + script_m + "})();"

            runner = f"""
const SNAPSHOT_TEXT = {json.dumps(snap_m)};
const elements = new Map();
function makeEl(id) {{
  return {{
    id, innerHTML: '', textContent: '', className: '', value: 0, style: {{}},
    children: [], attrs: {{}},
    classList: {{ toggle() {{}} }},
    setAttribute(k, v) {{ this.attrs[k] = v; }},
    appendChild(c) {{ this.children.push(c); }},
    querySelectorAll() {{ return []; }},
    getBoundingClientRect() {{ return {{ left: 0, width: 972 }}; }},
    onclick: null, oninput: null,
  }};
}}
function getEl(id) {{
  if (!elements.has(id)) elements.set(id, makeEl(id));
  if (id === 'snapshot') elements.get(id).textContent = SNAPSHOT_TEXT;
  return elements.get(id);
}}
global.document = {{ getElementById: getEl, createElement: () => makeEl('dyn'), querySelectorAll: () => [] }};
global.location = {{ search: '' }};
global.performance = {{ now: () => 0 }};
global.requestAnimationFrame = () => {{}};
global.URLSearchParams = class {{ has() {{ return false; }} get() {{ return null; }} }};
{script_full}
console.log(JSON.stringify({{ clock: getEl('clock').textContent, scrub: getEl('scrub').value }}));
"""
            proc = subprocess.run([node_bin, "-e", runner], capture_output=True, text=True, check=True)
            res = json.loads(proc.stdout.strip())
            clock_parts = str(res["clock"]).split("/")
            self.assertEqual(clock_parts[0].strip(), clock_parts[1].strip(), "LIVE mode must seek to end of timeline")

    def test_dashboard_failure_warns_once_and_does_not_affect_commit_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _init_git_repo(repo)
            checkpoint = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()

            blocked_out = repo / ".dev" / "blocked_dash"
            rc_en = cli_main(["--work-dir", str(repo), "dashboard", "--enable", "--out", str(blocked_out)])
            self.assertEqual(rc_en, 0)
            blocked_out.unlink()
            blocked_out.mkdir()

            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
            review_dir = Path(tmp) / "review_io"
            review_dir.mkdir()
            spec_excerpt = review_dir / "spec_excerpt.md"
            spec_excerpt.write_text("# Spec\n\n- [ ] **APP-1**: x is 1.\n", encoding="utf-8")
            checker_out = review_dir / "checker.txt"
            checker_out.write_text("ok\nDECISION: APPROVE\n", encoding="utf-8")

            err_buf = io.StringIO()
            with redirect_stderr(err_buf):
                rc_v = cli_main([
                    "--work-dir", str(repo), "verify-step",
                    "--checkpoint", checkpoint, "--task-id", "1",
                ])
                rc_ri = cli_main([
                    "--work-dir", str(repo), "review-input",
                    "--task-id", "1", "--out-dir", str(review_dir),
                ])
                rc_rr = cli_main([
                    "--work-dir", str(repo), "record-review",
                    "--task-id", "1",
                    "--checker-output", str(checker_out),
                    "--diff-file", str(review_dir / "diff.patch"),
                    "--spec-file", str(spec_excerpt),
                    "--maker-model", "gemini-3.8-flash",
                    "--checker-model", "claude-opus-5.5",
                ])
                rc_c = cli_main([
                    "--work-dir", str(repo), "commit-step",
                    "--task-id", "1", "--checkpoint", checkpoint,
                    "--maker-model", "gemini-3.8-flash",
                    "--checker-model", "claude-opus-5.5",
                    "--checker-reason", "ok",
                ])

            self.assertEqual(rc_v, 0, err_buf.getvalue())
            self.assertEqual(rc_ri, 0, err_buf.getvalue())
            self.assertEqual(rc_rr, 0, err_buf.getvalue())
            self.assertEqual(rc_c, 0, err_buf.getvalue())
            new_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertNotEqual(new_head, checkpoint)
            self.assertIn("- [x] 1.", (repo / "PLAN.md").read_text(encoding="utf-8"))
            self.assertEqual(
                err_buf.getvalue().count("DASHBOARD_WARNING"),
                1,
                f"Dashboard failure must warn exactly once across verify/commit, got: {err_buf.getvalue()}",
            )

    def test_run_with_dashboard_failure_warns_once_and_preserves_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            maker_sh = repo / "maker_ok.sh"
            maker_sh.write_text("#!/usr/bin/env bash\necho 'x=1' >> app.txt\n", encoding="utf-8")
            maker_sh.chmod(0o755)
            checker_sh = repo / "checker_ok.sh"
            checker_sh.write_text("#!/usr/bin/env bash\necho 'DECISION: APPROVE'\n", encoding="utf-8")
            checker_sh.chmod(0o755)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add scripts"], cwd=repo, check=True, capture_output=True)

            # Block default .dev/dashboard.html by making it a directory
            (repo / ".dev" / "dashboard.html").mkdir(parents=True)

            prev_dash_env = os.environ.get("AUTO_DEV_DASHBOARD")
            err_buf = io.StringIO()
            with redirect_stderr(err_buf):
                rc = cli_main([
                    "--work-dir", str(repo),
                    "run",
                    "--maker-cmd", str(maker_sh),
                    "--checker-cmd", str(checker_sh),
                    "--maker-model", "gemini-3.8-flash",
                    "--checker-model", "claude-opus-5.5",
                    "--dashboard",
                ])

            self.assertEqual(rc, 0, err_buf.getvalue())
            self.assertEqual(os.environ.get("AUTO_DEV_DASHBOARD"), prev_dash_env)
            self.assertEqual(
                err_buf.getvalue().count("DASHBOARD_WARNING"),
                1,
                f"run --dashboard failure must warn exactly once, got: {err_buf.getvalue()}",
            )

    def test_teams_run_with_dashboard_failure_warns_once_and_preserves_exit_code(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            rc_init = cli_main(["--work-dir", str(repo), "teams", "init", "--teams", "alpha,beta"])
            self.assertEqual(rc_init, 0)
            maker_sh = repo / "maker_ok.sh"
            maker_sh.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "branch=$(git rev-parse --abbrev-ref HEAD)\n"
                "team=${branch#team/}\n"
                "mkdir -p \"src/$team\"\n"
                "echo \"$team=1\" >> \"src/$team/out.txt\"\n",
                encoding="utf-8",
            )
            maker_sh.chmod(0o755)
            checker_sh = repo / "checker_ok.sh"
            checker_sh.write_text("#!/usr/bin/env bash\necho 'DECISION: APPROVE'\n", encoding="utf-8")
            checker_sh.chmod(0o755)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "teams init"], cwd=repo, check=True, capture_output=True)

            # Block default .dev/dashboard.html by making it a directory
            (repo / ".dev" / "dashboard.html").mkdir(parents=True)

            prev_dash_env = os.environ.get("AUTO_DEV_DASHBOARD")
            err_buf = io.StringIO()
            with redirect_stderr(err_buf):
                rc_run = cli_main([
                    "--work-dir", str(repo),
                    "teams", "run",
                    "--maker-cmd", str(maker_sh),
                    "--checker-cmd", str(checker_sh),
                    "--maker-model", "gemini-3.8-flash",
                    "--checker-model", "claude-opus-5.5",
                    "--dashboard",
                ])

            self.assertEqual(rc_run, 0, err_buf.getvalue())
            self.assertEqual(os.environ.get("AUTO_DEV_DASHBOARD"), prev_dash_env)
            self.assertEqual(
                err_buf.getvalue().count("DASHBOARD_WARNING"),
                1,
                f"teams run --dashboard failure must warn exactly once, got: {err_buf.getvalue()}",
            )
            worker_logs = list((repo / ".dev" / "integration_jobs").rglob("*.log"))
            for log_file in worker_logs:
                self.assertNotIn(
                    "DASHBOARD_WARNING",
                    log_file.read_text(encoding="utf-8", errors="replace"),
                    f"Worker log {log_file} must not emit DASHBOARD_WARNING after parent failure",
                )

    def test_skills_mention_dashboard_enable_option(self) -> None:
        run_text = _NIGHTLY_RUN_SKILL.read_text(encoding="utf-8")
        setup_text = _NIGHTLY_SETUP_SKILL.read_text(encoding="utf-8")
        self.assertIn("auto-dev dashboard --enable", run_text)
        self.assertIn(".dev/dashboard.html", run_text)
        self.assertIn("auto-dev dashboard --enable", setup_text)
        self.assertIn(".dev/dashboard.html", setup_text)


if __name__ == "__main__":
    unittest.main()
