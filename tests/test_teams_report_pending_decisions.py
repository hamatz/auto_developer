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
from auto_developer.reporter import MorningReporter


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing_pp}" if existing_pp else src_dir
    return env


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_cli(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo_dir), *args],
        cwd=repo_dir,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_multi_team_repo(repo_dir: Path) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(
        ".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n*.py[cod]\n",
        encoding="utf-8",
    )
    (repo_dir / "SPEC.md").write_text("# SPEC\n- RPT-5\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text(
        "# Root PLAN\n\n## ステップ\n- [ ] 1. Root\n",
        encoding="utf-8",
    )
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


def _extract_pending_section(text: str) -> str:
    marker = "### 判断待ちの事項 (Pending Decisions)"
    idx = text.find(marker)
    if idx == -1:
        return ""
    rest = text[idx:]
    lines = rest.splitlines()
    out = [lines[0]]
    for line in lines[1:]:
        if line.startswith(("## ", "### ")):
            break
        out.append(line)
    return "\n".join(out)


class TestRpt5TeamPendingDecisionsUnit(unittest.TestCase):
    """RPT-5: MorningReporter のマルチチーム判断待ち表示の単体テスト。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp_dir.name)
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.name", "Test User")
        _git(self.repo, "config", "user.email", "test@example.com")
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        _git(self.repo, "add", ".")
        _git(self.repo, "commit", "-m", "init")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_write_report_with_team_decisions_omits_single_team_wording(self) -> None:
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 2,
                "succeeded_count": 2,
                "quarantined_count": 0,
                "checkpoint": "abc1234",
                "completed_tasks": [],
                "quarantined_task": {
                    "task_id": "99",
                    "title": "Stale single task",
                    "phase": "VERIFIER_ERROR",
                    "quarantined_branch": "quarantine/task-99-stale",
                    "log_path": "logs/nightly/task_99_failure.log",
                    "root_cause_summary": "stale error",
                },
                "multi_team": {
                    "job_id": "job-unit-1",
                    "job_state": "INTEGRATED",
                    "base_ref": "main",
                    "rc_tag": "rc/job-unit-1",
                    "teams": [],
                    "integration_errors": [],
                    "pending_decisions": ["RC タグ `rc/job-unit-1` を確認してください"],
                },
            },
        )
        content = report_path.read_text(encoding="utf-8")
        section = _extract_pending_section(content)
        self.assertIn("RC タグ `rc/job-unit-1` を確認してください", section)
        self.assertNotIn("単一チーム実行の隔離タスクはありません", content)
        self.assertNotIn("## 要確認・隔離事項", content)
        self.assertNotIn("## あなたに判断してほしいこと", content)
        self.assertNotIn("quarantine/task-99-stale", content)
        self.assertNotIn("すべての予定タスクが正常に検証され", content)

    def test_write_report_with_empty_team_decisions_shows_nashi(self) -> None:
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 2,
                "succeeded_count": 2,
                "quarantined_count": 0,
                "checkpoint": "abc1234",
                "completed_tasks": [],
                "multi_team": {
                    "job_id": "job-unit-empty",
                    "job_state": "CLEANED_UP",
                    "base_ref": "main",
                    "rc_tag": "rc/job-unit-empty",
                    "teams": [],
                    "integration_errors": [],
                    "pending_decisions": [],
                },
            },
        )
        content = report_path.read_text(encoding="utf-8")
        section = _extract_pending_section(content)
        self.assertTrue(section, f"Pending Decisions section missing in:\n{content}")
        self.assertIn("- なし", section)
        self.assertNotIn("単一チーム実行の隔離タスクはありません", content)
        self.assertNotIn("teams status --job-id", content)
        self.assertNotIn("## 要確認・隔離事項", content)
        self.assertNotIn("## あなたに判断してほしいこと", content)

    def test_format_console_brief_with_team_job_shows_nashi_and_no_single_team_wording(self) -> None:
        reporter = MorningReporter(repo_dir=self.repo)
        brief_text = reporter.format_console_brief(
            {
                "status": "COMPLETED",
                "total_tasks": 2,
                "succeeded_count": 2,
                "quarantined_count": 0,
                "checkpoint": "abc1234",
                "completed_tasks": [],
                "quarantined_task": {
                    "task_id": "99",
                    "title": "Stale single task",
                    "phase": "VERIFIER_ERROR",
                    "quarantined_branch": "quarantine/task-99-stale",
                    "log_path": "logs/nightly/task_99_failure.log",
                    "root_cause_summary": "stale error",
                },
                "multi_team": {
                    "job_id": "job-brief-empty",
                    "job_state": "CLEANED_UP",
                    "base_ref": "main",
                    "rc_tag": None,
                    "teams": [],
                    "integration_errors": [],
                    "pending_decisions": [],
                },
            }
        )
        self.assertIn("判断待ちの事項", brief_text)
        self.assertIn("なし", brief_text)
        self.assertNotIn("All tasks completed smoothly. Ready for review or push!", brief_text)
        self.assertNotIn("Action Required / Quarantined Task", brief_text)
        self.assertNotIn("Next Choices for Developer", brief_text)
        self.assertNotIn("quarantine/task-99-stale", brief_text)


class TestRpt5TeamPendingDecisionsAcceptance(unittest.TestCase):
    """RPT-5: チームジョブ時の auto-dev brief / NIGHTLY_REPORT.md の受入テスト。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.base_commit = _init_multi_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_integrated_job_excludes_single_team_and_past_job_items(self) -> None:
        # 1. 過去の失敗チームジョブ (job-old-fail) を作成し --force で片付ける
        prep_old = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-old-fail")
        self.assertEqual(prep_old.returncode, 0, prep_old.stderr)
        ui_wt_old = self.repo_dir / ".dev" / "worktrees" / "ui"
        _git(ui_wt_old, "branch", "quarantine/job-old-fail/ui/1-1700000001")
        cln_old = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", "job-old-fail", "--force")
        self.assertEqual(cln_old.returncode, 0, cln_old.stderr)

        # 2. 単一チーム由来の残骸 (PLAN.md の止まっていること、job_state.json の隔離タスク、audit_stage.json) を配置
        (self.repo_dir / "PLAN.md").write_text(
            "# Root PLAN\n\n## ステップ\n- [ ] 1. Root\n\n"
            "## 止まっていること・判断待ち\n- SINGLE_TEAM_PLAN_DECISION: stale escalation\n",
            encoding="utf-8",
        )
        _git(self.repo_dir, "add", "PLAN.md")
        _git(self.repo_dir, "commit", "-m", "update root plan with single-team pending")

        JobState(
            job_id="legacy-single-failed",
            status="FAILED",
            checkpoint_hash=self.base_commit,
            quarantined_task={
                "task_id": "99",
                "title": "Legacy single task",
                "phase": "VERIFIER_ERROR",
                "quarantined_branch": "quarantine/task-99-stale",
                "log_path": "logs/nightly/task_99_failure.log",
                "root_cause_summary": "stale failure",
            },
        ).save(self.repo_dir / ".dev" / "job_state.json")

        audit_dir = self.repo_dir / ".git" / "nightly_evidence"
        audit_dir.mkdir(parents=True, exist_ok=True)
        (audit_dir / "audit_stage.json").write_text(
            json.dumps(
                {
                    "status": "SUCCESS",
                    "dispatched": [],
                    "backlogged": [],
                    "escalated": [
                        {
                            "finding_id": "AUD-OLD-1",
                            "severity": "P1",
                            "location": "src/old.py:10",
                            "description": "Old single-team audit escalation",
                            "streak": 5,
                        }
                    ],
                    "rejected": [],
                    "duplicates": [],
                }
            ),
            encoding="utf-8",
        )

        # 3. 現在のチームジョブ (job-curr-ok) を実行・統合
        job_id = "job-curr-ok"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)
        _complete_task_via_lowlevel_cli(
            self.repo_dir / ".dev" / "worktrees" / "core", "1", "src/core/app.py", "VALUE = 10\n"
        )
        _complete_task_via_lowlevel_cli(
            self.repo_dir / ".dev" / "worktrees" / "ui", "1", "src/ui/view.py", "VIEW = 20\n"
        )
        integ = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(integ.returncode, 0, integ.stderr)

        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")

        for output_name, text in [("NIGHTLY_REPORT.md", report_text), ("brief stdout", brief.stdout)]:
            with self.subTest(output=output_name):
                self.assertIn("判断待ちの事項", text)
                self.assertIn(f"rc/{job_id}", text)
                self.assertIn(f"teams cleanup --job-id {job_id}", text)
                self.assertNotIn("単一チーム実行の隔離タスクはありません", text)
                self.assertNotIn("SINGLE_TEAM_PLAN_DECISION", text)
                self.assertNotIn("quarantine/task-99-stale", text)
                self.assertNotIn("AUD-OLD-1", text)
                self.assertNotIn("job-old-fail", text)
                self.assertNotIn("All tasks completed smoothly", text)
                self.assertNotIn("すべての予定タスクが正常に検証され", text)

    def test_merged_and_cleaned_team_job_shows_nashi_in_pending_decisions(self) -> None:
        job_id = "job-merged-clean"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)
        _complete_task_via_lowlevel_cli(
            self.repo_dir / ".dev" / "worktrees" / "core", "1", "src/core/app.py", "VALUE = 30\n"
        )
        _complete_task_via_lowlevel_cli(
            self.repo_dir / ".dev" / "worktrees" / "ui", "1", "src/ui/view.py", "VIEW = 40\n"
        )
        integ = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(integ.returncode, 0, integ.stderr)

        # RC タグを main に取り込み、worktree とチームブランチを片付ける -> 判断待ちがなくなる
        _git(self.repo_dir, "merge", "--no-ff", "-m", "merge rc", f"rc/{job_id}")
        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, cln.stderr)

        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")

        for output_name, text in [("NIGHTLY_REPORT.md", report_text), ("brief stdout", brief.stdout)]:
            with self.subTest(output=output_name):
                section = _extract_pending_section(text)
                self.assertTrue(section, f"Pending Decisions section missing in {output_name}:\n{text}")
                self.assertIn("- なし", section)
                self.assertNotIn("git merge", section)
                self.assertNotIn("teams cleanup", section)
                self.assertNotIn("単一チーム実行の隔離タスクはありません", text)
                self.assertNotIn("すべての予定タスクが正常に検証され", text)

    def test_force_cleaned_failed_job_with_dropped_quarantine_shows_nashi(self) -> None:
        job_id = "job-failed-cleaned"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        # maker failure via teams run or manual state in worktree
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        q_branch = f"quarantine/{job_id}/ui/1-1700000099"
        _git(ui_wt, "branch", q_branch)
        JobState(
            job_id=f"{job_id}-ui",
            status="FAILED",
            checkpoint_hash=self.base_commit,
            quarantined_task={"task_id": "1", "quarantined_branch": q_branch},
        ).save(ui_wt / ".dev" / "job_state.json")

        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(cln.returncode, 0, cln.stderr)
        _git(self.repo_dir, "branch", "-D", q_branch)

        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")

        for output_name, text in [("NIGHTLY_REPORT.md", report_text), ("brief stdout", brief.stdout)]:
            with self.subTest(output=output_name):
                section = _extract_pending_section(text)
                self.assertTrue(section, f"Pending Decisions section missing in {output_name}:\n{text}")
                self.assertIn("- なし", section)
                self.assertNotIn("teams status", section)
                self.assertNotIn("teams cleanup", section)
                self.assertNotIn("単一チーム実行の隔離タスクはありません", text)


if __name__ == "__main__":
    unittest.main()
