from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.cli import main


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


class TestDx10DemoTeamsCommand(unittest.TestCase):
    """DX-10: `auto-dev demo --teams [--keep]` の単体・受入テスト。"""

    def test_demo_help_lists_teams_and_keep_options(self) -> None:
        """`auto-dev demo --help` に `--teams` と `--keep` オプションが表示されること。"""
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), self.assertRaises(SystemExit) as cm:
            main(["demo", "--help"])
        self.assertEqual(cm.exception.code, 0)
        help_text = stdout_buf.getvalue()
        self.assertIn("--teams", help_text)
        self.assertIn("--keep", help_text)

    def test_demo_teams_default_cleans_up_temp_dir_and_does_not_touch_cwd(self) -> None:
        """`auto-dev demo --teams`（--keep なし）は一時ディレクトリで完走し、終了時に削除して CWD を汚さないこと。"""
        with tempfile.TemporaryDirectory() as caller_tmp:
            caller_dir = Path(caller_tmp)
            marker_file = caller_dir / "untouched.txt"
            marker_file.write_text("keep me\n", encoding="utf-8")

            proc = _run_cli_subprocess(caller_dir, "demo", "--teams")
            self.assertEqual(
                proc.returncode,
                0,
                f"demo --teams failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )

            out = proc.stdout
            self.assertIn("rc/", out)
            self.assertIn("dashboard.html", out)

            m = re.search(r"Demo workspace(?:\s+path)?:\s*(.+)", out)
            self.assertIsNotNone(m, f"Demo workspace path not reported in output:\n{out}")
            assert m is not None
            demo_path = Path(m.group(1).strip())
            self.assertFalse(
                demo_path.exists(),
                f"Temporary demo directory was not deleted without --keep: {demo_path}",
            )

            for vendor_token in ("gemini-", "claude-", "gpt-", "deepseek-", "qwen", "llama", "mistral"):
                self.assertNotIn(vendor_token, out.lower())

            self.assertEqual(
                sorted(p.name for p in caller_dir.iterdir()),
                ["untouched.txt"],
            )

    def test_demo_teams_keep_verifies_two_teams_retry_rc_tag_and_dashboard(self) -> None:
        """`auto-dev demo --teams --keep` が2チーム並行実行・検証失敗→2回目成功・RCタグ・ダッシュボードを満たすこと。"""
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
                out = proc.stdout

                m_ws = re.search(r"Demo workspace kept at:\s*(.+)", out)
                self.assertIsNotNone(m_ws, f"Kept workspace path missing from output:\n{out}")
                assert m_ws is not None
                kept_dir = Path(m_ws.group(1).strip())
                self.assertTrue(kept_dir.is_dir(), f"Kept directory does not exist: {kept_dir}")

                # 1. teams.json に2チーム以上定義され、各チーム2タスク以上あること
                teams_cfg = json.loads((kept_dir / "teams.json").read_text(encoding="utf-8"))
                teams = teams_cfg.get("teams", [])
                self.assertGreaterEqual(len(teams), 2, f"Expected >= 2 teams, got: {teams}")

                # 2. RC タグが作成されていること（main は直接書き換えない）
                rc_tags = [
                    t.strip()
                    for t in _git(kept_dir, "tag", "--list", "rc/*").stdout.splitlines()
                    if t.strip()
                ]
                self.assertTrue(rc_tags, "Expected at least one rc/* tag to be created")
                rc_tag = rc_tags[-1]
                self.assertIn(rc_tag, out)

                for team in teams:
                    plan_rel = team["plan"]
                    rc_plan = _git(kept_dir, "show", f"{rc_tag}:{plan_rel}").stdout
                    done_tasks = re.findall(r"^- \[x\] ", rc_plan, flags=re.MULTILINE)
                    self.assertGreaterEqual(
                        len(done_tasks),
                        2,
                        f"Expected >= 2 completed tasks in {rc_tag}:{plan_rel}, got:\n{rc_plan}",
                    )

                # 3. 一方のチーム (core) のタスク 2 で、attempt=1 の検証が失敗し、attempt=2 で成功した記録があること
                core_events_path = (
                    kept_dir / ".dev" / "worktrees" / "core" / ".dev" / "metrics" / "events.jsonl"
                )
                cli_events_path = (
                    kept_dir / ".dev" / "worktrees" / "cli" / ".dev" / "metrics" / "events.jsonl"
                )
                self.assertTrue(core_events_path.is_file(), f"Expected {core_events_path} to exist")
                self.assertTrue(cli_events_path.is_file(), f"Expected {cli_events_path} to exist")

                core_events = [
                    json.loads(line)
                    for line in core_events_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                cli_events = [
                    json.loads(line)
                    for line in cli_events_path.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]

                core_task2_verifies = [
                    e
                    for e in core_events
                    if e.get("event") == "verify_step"
                    and e.get("team") == "core"
                    and str(e.get("task_id")) == "2"
                ]
                attempt1 = [e for e in core_task2_verifies if e.get("attempt") == 1]
                attempt2 = [e for e in core_task2_verifies if e.get("attempt") == 2]
                self.assertTrue(
                    attempt1 and attempt1[0].get("result") != "PASSED" and attempt1[0].get("exit_code", 0) != 0,
                    f"Expected core task 2 attempt 1 verify_step to fail, got: {core_task2_verifies}",
                )
                self.assertTrue(
                    attempt2 and attempt2[0].get("result") == "PASSED" and attempt2[0].get("exit_code", 1) == 0,
                    f"Expected core task 2 attempt 2 verify_step to pass, got: {core_task2_verifies}",
                )

                # 4. デモ用のモデル名・系統が両チームで使われていること
                commit_events = [
                    e for e in (core_events + cli_events) if e.get("event") == "commit_step"
                ]
                self.assertEqual(
                    {(e.get("team"), str(e.get("task_id"))) for e in commit_events},
                    {("core", "1"), ("core", "2"), ("cli", "1"), ("cli", "2")},
                )
                for ev in commit_events:
                    self.assertEqual(ev.get("maker_model"), "auto-dev-demo-maker")
                    self.assertEqual(ev.get("checker_model"), "auto-dev-demo-checker")

                # 5. ダッシュボード HTML が書かれ、その場所が出力に表示されていること
                m_dash = re.search(r"Dashboard:\s*(.+)", out)
                self.assertIsNotNone(m_dash, f"Dashboard path missing from output:\n{out}")
                assert m_dash is not None
                reported_dash = Path(m_dash.group(1).strip())
                expected_dash = kept_dir / ".dev" / "dashboard.html"
                self.assertEqual(reported_dash.resolve(), expected_dash.resolve())
                self.assertTrue(expected_dash.is_file(), f"Dashboard HTML not found: {expected_dash}")
                dash_html = expected_dash.read_text(encoding="utf-8")
                self.assertIn("auto-dev-status/2", dash_html)
                self.assertIn(rc_tag, dash_html)
                snap_m = re.search(
                    r'<script[^>]*id="snapshot"[^>]*>(.*?)</script>',
                    dash_html,
                    re.DOTALL,
                )
                self.assertIsNotNone(snap_m, "Embedded snapshot JSON missing in demo dashboard")
                assert snap_m is not None
                embedded = json.loads(snap_m.group(1))
                dash_events = embedded.get("events", [])
                self.assertGreaterEqual(len(dash_events), 2)
                self.assertGreaterEqual(
                    float(embedded.get("duration", 0.0)),
                    float(max(0, len(dash_events) - 1)),
                )
                for idx in range(1, len(dash_events)):
                    self.assertGreaterEqual(
                        float(dash_events[idx]["t"]) - float(dash_events[idx - 1]["t"]),
                        1.0,
                    )
            finally:
                if kept_dir is not None and kept_dir.exists():
                    shutil.rmtree(kept_dir, ignore_errors=True)

    def test_demo_teams_returns_nonzero_when_rc_tag_missing(self) -> None:
        """RC タグが作られなかった場合は _run_demo_teams_flow の検証で終了コード 1 になること。"""
        from auto_developer import demo as demo_mod

        orig_run_cli = demo_mod._run_demo_cli

        def _fake_cli(repo_dir: Path, *args: str) -> tuple[int, str, str]:
            if args and args[0] == "teams":
                return 0, json.dumps({"rc_tag": None}), ""
            return orig_run_cli(repo_dir, *args)

        stderr_buf = io.StringIO()
        with (
            patch.object(demo_mod, "_run_demo_cli", side_effect=_fake_cli),
            patch("sys.stderr", stderr_buf),
        ):
            rc = demo_mod.run_demo(keep=False, teams=True)
        self.assertEqual(rc, 1)
        self.assertIn("No RC tag was created", stderr_buf.getvalue())

    def test_demo_teams_returns_nonzero_when_dashboard_missing(self) -> None:
        """ダッシュボードが書かれなかった場合は _run_demo_teams_flow の検証で終了コード 1 になること。"""
        from auto_developer import demo as demo_mod

        orig_run_cli = demo_mod._run_demo_cli

        def _fake_cli(repo_dir: Path, *args: str) -> tuple[int, str, str]:
            if args and args[0] == "teams":
                _git(repo_dir, "tag", "rc/job-demo-test")
                return 0, json.dumps({"rc_tag": "rc/job-demo-test"}), ""
            if args and args[0] == "dashboard":
                return 0, "", ""
            return orig_run_cli(repo_dir, *args)

        stderr_buf = io.StringIO()
        with (
            patch.object(demo_mod, "_run_demo_cli", side_effect=_fake_cli),
            patch("sys.stderr", stderr_buf),
        ):
            rc = demo_mod.run_demo(keep=False, teams=True)
        self.assertEqual(rc, 1)
        self.assertIn("Dashboard HTML was not written", stderr_buf.getvalue())


if __name__ == "__main__":
    unittest.main()
