"""Tests for CORE-10: 計画ファイルの読み取り範囲・進捗表示・状態書き込み規則."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.cli import DEFAULT_PLAN_MD, main
from auto_developer.dispatcher import TaskDispatcher
from auto_developer.metrics import MetricsLog
from auto_developer.models import Task
from auto_developer.multi_team import _check_team_plan_completed, get_teams_status
from auto_developer.reporter import MorningReporter


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


class TestPlanParsingScope(unittest.TestCase):
    """CORE-10: HTMLコメント除外と「## ステップ」節スコープの一貫性."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_dir = Path(self._tmp.name)
        self.dispatcher = TaskDispatcher()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_parse_plan_ignores_html_comments_in_default_template(self) -> None:
        plan_file = self.tmp_dir / "PLAN.md"
        plan_file.write_text(DEFAULT_PLAN_MD, encoding="utf-8")

        tasks = self.dispatcher.parse_plan(plan_file)
        self.assertEqual([(t.task_id, t.title) for t in tasks], [
            ("1", "初期モジュールの実装"),
            ("2", "単体テストの追加と受入検証"),
        ])

    def test_parse_plan_ignores_single_and_multiline_html_comments_and_commented_heading(self) -> None:
        plan_file = self.tmp_dir / "PLAN.md"
        plan_file.write_text(
            "# PLAN.md\n\n"
            "<!-- - [ ] 90. single line comment task -->\n"
            "<!--\n"
            "## ステップ\n"
            "- [ ] 91. commented heading task\n"
            "- [x] 92. commented done task\n"
            "- [ ] 93. commented failed task **[FAILED]**\n"
            "-->\n\n"
            "## 現在の目標\n"
            "- [ ] 80. Goal section task should be ignored when steps heading exists\n\n"
            "## ステップ\n"
            "<!-- - [ ] 94. inline comment inside steps -->\n"
            "### サブ見出し\n"
            "- [ ] 1. Real step one\n"
            "<!--\n"
            "- [ ] 95. multiline comment inside steps\n"
            "## 別の見出し\n"
            "-->\n"
            "- [ ] 2. Real step two\n\n"
            "## 人間が行うこと\n"
            "- [ ] 99. Human task outside steps\n",
            encoding="utf-8",
        )

        tasks = self.dispatcher.parse_plan(plan_file)
        self.assertEqual([(t.task_id, t.title) for t in tasks], [
            ("1", "Real step one"),
            ("2", "Real step two"),
        ])

    def test_parse_plan_reads_whole_file_when_no_steps_heading(self) -> None:
        plan_file = self.tmp_dir / "PLAN.md"
        plan_file.write_text(
            "# Custom Plan\n\n"
            "<!--\n"
            "## ステップ\n"
            "- [ ] 99. Commented heading must not create scope\n"
            "-->\n"
            "## セクションA\n"
            "- [ ] 1. First task\n"
            "## セクションB\n"
            "- [x] 2. Second task\n",
            encoding="utf-8",
        )

        tasks = self.dispatcher.parse_plan(plan_file)
        self.assertEqual([(t.task_id, t.title, t.is_completed) for t in tasks], [
            ("1", "First task", False),
            ("2", "Second task", True),
        ])

    def test_parse_plan_returns_empty_when_steps_heading_present_but_empty(self) -> None:
        plan_file = self.tmp_dir / "PLAN.md"
        plan_file.write_text(
            "# PLAN.md\n\n"
            "## 現在の目標\n"
            "- [ ] 1. Goal task\n\n"
            "## ステップ\n\n"
            "## 止まっていること\n"
            "- [ ] 2. After steps task\n",
            encoding="utf-8",
        )

        tasks = self.dispatcher.parse_plan(plan_file)
        self.assertEqual(tasks, [])

    def test_mark_task_status_updates_only_target_line_preserving_comments_and_other_sections(self) -> None:
        plan_file = self.tmp_dir / "PLAN.md"
        original = (
            "# PLAN.md\n\n"
            "<!--\n"
            "- [ ] 1. 初期モジュールの実装\n"
            "- [ ] 2. 単体テストの追加と検証\n"
            "-->\n\n"
            "## 現在の目標\n"
            "- [ ] 1. 初期モジュールの実装\n\n"
            "## ステップ\n"
            "- [ ] 1. 初期モジュールの実装\n"
            "- [ ] 2. 単体テストの追加と検証\n\n"
            "## 人間が行うこと\n"
            "- [ ] 1. 初期モジュールの実装\n"
        )
        plan_file.write_text(original, encoding="utf-8")

        tasks = self.dispatcher.parse_plan(plan_file)
        self.assertEqual(len(tasks), 2)

        # Even if caller passes a stale/wrong line_number pointing into HTML comment (line 3),
        # mark_task_status must update the scoped task in ## ステップ and leave comment/other sections untouched.
        stale_task1 = Task("1", "初期モジュールの実装", line_number=3, is_completed=False, is_failed=False)
        self.dispatcher.mark_task_status(plan_file, stale_task1, status="completed")

        # Mark task 2 as failed using parsed task
        tasks_after = self.dispatcher.parse_plan(plan_file)
        self.dispatcher.mark_task_status(plan_file, tasks_after[1], status="failed")

        expected = (
            "# PLAN.md\n\n"
            "<!--\n"
            "- [ ] 1. 初期モジュールの実装\n"
            "- [ ] 2. 単体テストの追加と検証\n"
            "-->\n\n"
            "## 現在の目標\n"
            "- [ ] 1. 初期モジュールの実装\n\n"
            "## ステップ\n"
            "- [x] 1. 初期モジュールの実装\n"
            "- [ ] 2. 単体テストの追加と検証 **[FAILED]**\n\n"
            "## 人間が行うこと\n"
            "- [ ] 1. 初期モジュールの実装\n"
        )
        self.assertEqual(plan_file.read_text(encoding="utf-8"), expected)

    def test_mark_task_status_rejects_task_only_in_comment_or_other_section(self) -> None:
        plan_file = self.tmp_dir / "PLAN.md"
        original = (
            "# PLAN.md\n\n"
            "<!--\n"
            "- [ ] 99. Comment task\n"
            "-->\n\n"
            "## ステップ\n"
            "- [ ] 1. Real task\n\n"
            "## 人間が行うこと\n"
            "- [ ] 88. Human task\n"
        )
        plan_file.write_text(original, encoding="utf-8")

        with self.assertRaises(ValueError):
            self.dispatcher.mark_task_status(
                plan_file,
                Task("99", "Comment task", line_number=3, is_completed=False, is_failed=False),
                status="completed",
            )
        with self.assertRaises(ValueError):
            self.dispatcher.mark_task_status(
                plan_file,
                Task("88", "Human task", line_number=10, is_completed=False, is_failed=False),
                status="failed",
            )
        self.assertEqual(plan_file.read_text(encoding="utf-8"), original)

    def test_teams_status_and_integrate_use_same_plan_scope(self) -> None:
        repo_dir = self.tmp_dir / "repo"
        repo_dir.mkdir()
        _git(repo_dir, "init")
        _git(repo_dir, "config", "user.name", "Tester")
        _git(repo_dir, "config", "user.email", "tester@example.com")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--work-dir", str(repo_dir), "init"]), 0)
            self.assertEqual(main(["--work-dir", str(repo_dir), "teams", "init", "--teams", "core,ui"]), 0)

        # Add an uncompleted checkbox in another section outside ## ステップ to core plan
        core_plan = repo_dir / "plans" / "core.md"
        core_plan.write_text(
            core_plan.read_text(encoding="utf-8")
            + "\n## 参照メモ\n- [ ] 99. Not a step task\n",
            encoding="utf-8",
        )

        _git(repo_dir, "add", ".")
        _git(repo_dir, "commit", "-m", "init templates")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--work-dir", str(repo_dir), "teams", "prepare", "--job-id", "job-scope"]), 0)

        status_data, err_code, err_msg = get_teams_status(repo_dir, job_id="job-scope")
        self.assertEqual(err_code, 0, err_msg)
        assert status_data is not None
        by_id = {t["team_id"]: t for t in status_data["teams"]}
        # Template has 3 example tasks in HTML comment + 2 real tasks in ## ステップ
        self.assertEqual(by_id["core"]["total_tasks"], 2)
        self.assertEqual(by_id["core"]["completed_tasks"], 0)
        self.assertEqual(by_id["core"]["failed_tasks"], 0)
        self.assertEqual(by_id["ui"]["total_tasks"], 2)
        self.assertEqual(by_id["ui"]["completed_tasks"], 0)
        self.assertEqual(by_id["ui"]["failed_tasks"], 0)

        # Mark both real tasks in core worktree's plan as completed; leave comment and ## 参照メモ untouched
        core_wt_plan = repo_dir / ".dev" / "worktrees" / "core" / "plans" / "core.md"
        for task in self.dispatcher.parse_plan(core_wt_plan):
            self.dispatcher.mark_task_status(core_wt_plan, task, status="completed")

        ok, issues = _check_team_plan_completed(core_wt_plan)
        self.assertTrue(ok, f"Expected core plan completed, got issues: {issues}")

        status_after, err_code2, err_msg2 = get_teams_status(repo_dir, job_id="job-scope")
        self.assertEqual(err_code2, 0, err_msg2)
        assert status_after is not None
        by_id_after = {t["team_id"]: t for t in status_after["teams"]}
        self.assertEqual(by_id_after["core"]["completed_tasks"], 2)
        self.assertEqual(by_id_after["core"]["total_tasks"], 2)

    def test_reporter_and_metrics_follow_same_plan_scope(self) -> None:
        repo_dir = self.tmp_dir / "repo_rpt"
        repo_dir.mkdir()
        _git(repo_dir, "init")
        _git(repo_dir, "config", "user.name", "Tester")
        _git(repo_dir, "config", "user.email", "tester@example.com")

        plan_file = repo_dir / "PLAN.md"
        plan_file.write_text(
            "# PLAN.md\n\n"
            "<!--\n"
            "## ステップ\n"
            "- [x] 90. Commented done task\n"
            "- [ ] 91. Commented pending task\n"
            "-->\n\n"
            "## 現在の目標\n"
            "- Phase 1: test\n\n"
            "## ステップ\n"
            "- [x] 1. Real done\n"
            "<!-- - [x] 92. Comment inside steps -->\n"
            "- [ ] 2. Real pending\n\n"
            "## 別の節\n"
            "- [x] 93. Done outside steps\n",
            encoding="utf-8",
        )
        _git(repo_dir, "add", "PLAN.md")
        _git(repo_dir, "commit", "-m", "init")

        reporter = MorningReporter(repo_dir=repo_dir)
        report_path = repo_dir / "NIGHTLY_REPORT.md"
        reporter.generate_standalone_report(report_path=report_path, repo_dir=repo_dir, plan_path=plan_file)
        report_text = report_path.read_text(encoding="utf-8")
        self.assertIn("**1 / 2** 完了", report_text)

        # Also test standalone report when no ## ステップ heading exists
        no_heading_plan = repo_dir / "PLAN_NO_HEADING.md"
        no_heading_plan.write_text(
            "# PLAN\n\n"
            "<!--\n"
            "- [x] 90. Commented done\n"
            "-->\n"
            "- [x] 1. Done without heading\n"
            "- [ ] 2. Pending without heading\n",
            encoding="utf-8",
        )
        report_no_heading = repo_dir / "REPORT2.md"
        reporter.generate_standalone_report(
            report_path=report_no_heading,
            repo_dir=repo_dir,
            plan_path=no_heading_plan,
        )
        self.assertIn("**1 / 2** 完了", report_no_heading.read_text(encoding="utf-8"))

        metrics = MetricsLog(repo_dir=repo_dir, plan_path=plan_file)
        num_steps, aud_steps = metrics._extract_steps_from_plan(plan_file.read_text(encoding="utf-8"))
        self.assertEqual(num_steps, {"1", "2"})
        self.assertEqual(aud_steps, set())

        num_no_heading, _ = metrics._extract_steps_from_plan(no_heading_plan.read_text(encoding="utf-8"))
        self.assertEqual(num_no_heading, {"1", "2"})

    def test_cli_pre_flight_and_rollback_respect_scope(self) -> None:
        repo_dir = self.tmp_dir / "repo_cli"
        repo_dir.mkdir()
        _git(repo_dir, "init")
        _git(repo_dir, "config", "user.name", "Tester")
        _git(repo_dir, "config", "user.email", "tester@example.com")

        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(["--work-dir", str(repo_dir), "init"]), 0)

        _git(repo_dir, "add", ".")
        _git(repo_dir, "commit", "-m", "init")

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = main(["--work-dir", str(repo_dir), "pre-flight"])
        self.assertEqual(code, 0)
        payload = json.loads(out.getvalue())
        self.assertEqual(payload["next_task"]["id"], "1")
        self.assertEqual(payload["next_task"]["title"], "初期モジュールの実装")

        # verify-step marks [x] in ## ステップ without altering HTML comment
        checkpoint = payload["checkpoint"]
        (repo_dir / "work.txt").write_text("ok\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            v_code = main([
                "--work-dir",
                str(repo_dir),
                "verify-step",
                "--checkpoint",
                checkpoint,
                "--task-id",
                "1",
            ])
        self.assertEqual(v_code, 0)
        plan_after_verify = (repo_dir / "PLAN.md").read_text(encoding="utf-8")
        self.assertIn("- [ ] 1. タスク名  (未実行)", plan_after_verify)
        self.assertIn("- [x] 1. 初期モジュールの実装", plan_after_verify)

        # rollback-step resets to checkpoint and marks **[FAILED]** only on task 1 in ## ステップ
        with contextlib.redirect_stdout(io.StringIO()):
            r_code = main([
                "--work-dir",
                str(repo_dir),
                "rollback-step",
                "--checkpoint",
                checkpoint,
                "--task-id",
                "1",
                "--reason",
                "simulated fail",
            ])
        self.assertEqual(r_code, 0)
        q_branches = [
            b.strip().lstrip("* ").strip()
            for b in _git(repo_dir, "branch", "--list", "quarantine/*").stdout.splitlines()
            if b.strip()
        ]
        self.assertEqual(len(q_branches), 1)
        q_plan = _git(repo_dir, "show", f"{q_branches[0]}:PLAN.md").stdout
        self.assertIn("- [ ] 1. タスク名  (未実行)", q_plan)
        self.assertIn("- [ ] 1. 初期モジュールの実装 **[FAILED]**", q_plan)

    def test_audit_dispatcher_ignores_commented_headings_and_tasks(self) -> None:
        from auto_developer.audit import AuditFinding
        from auto_developer.audit_store import AuditStore
        from auto_developer.dispatcher import AuditDispatcher

        repo_dir = self.tmp_dir / "repo_audit"
        repo_dir.mkdir()
        plan_file = repo_dir / "PLAN.md"
        plan_file.write_text(
            "# PLAN.md\n\n"
            "<!--\n"
            "## ステップ\n"
            "- [ ] AUD-F-001. Fix: [F-001] commented example\n"
            "-->\n\n"
            "## ステップ\n"
            "- [x] 1. Done\n\n"
            "## 止まっていること・判断待ち\n"
            "- なし。\n",
            encoding="utf-8",
        )
        dispatcher = AuditDispatcher(
            repo_dir=repo_dir,
            audit_store=AuditStore(audit_dir=repo_dir / ".dev" / "audit"),
        )
        finding = AuditFinding(
            finding_id="F-001",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/app.py:1",
            description="real bug",
            reproduction_test_code="assert False\n",
        )
        dispatcher._register_step(finding, plan_file)
        updated = plan_file.read_text(encoding="utf-8")
        # Registered under the real ## ステップ before ## 止まっていること・判断待ち
        steps_pos = updated.rfind("## ステップ")
        stop_pos = updated.find("## 止まっていること・判断待ち")
        task_pos = updated.find("- [ ] AUD-F-001. Fix: [F-001] real bug")
        self.assertGreater(task_pos, steps_pos)
        self.assertLess(task_pos, stop_pos)
        self.assertIn("- [ ] AUD-F-001. Fix: [F-001] commented example", updated)


if __name__ == "__main__":
    unittest.main()
