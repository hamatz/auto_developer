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
    (repo_dir / "SPEC.md").write_text("# SPEC\n- RPT-7\n", encoding="utf-8")
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
    *,
    maker_model: str = "gemini-3.8-flash",
    checker_model: str = "claude-opus-5.5",
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

    io_dir = wt_dir / ".dev" / "checker_io"
    ri = _run_cli(wt_dir, "review-input", "--task-id", task_id, "--out-dir", str(io_dir))
    if ri.returncode != 0:
        raise AssertionError(f"review-input failed in {wt_dir}: {ri.stderr}")
    spec_file = io_dir / "spec.md"
    spec_file.write_text((wt_dir / "SPEC.md").read_text(encoding="utf-8"), encoding="utf-8")
    out_file = io_dir / "review.txt"
    out_file.write_text("Looks good\nDECISION: APPROVE\n", encoding="utf-8")

    rr = _run_cli(
        wt_dir,
        "record-review",
        "--task-id",
        task_id,
        "--checker-output",
        str(out_file),
        "--diff-file",
        str(io_dir / "diff.patch"),
        "--spec-file",
        str(spec_file),
        "--decision",
        "APPROVE",
        "--maker-model",
        maker_model,
        "--checker-model",
        checker_model,
        "--reason",
        "Looks good",
    )
    if rr.returncode != 0:
        raise AssertionError(f"record-review failed in {wt_dir}: {rr.stderr}")

    cs = _run_cli(
        wt_dir,
        "commit-step",
        "--task-id",
        task_id,
        "--checkpoint",
        checkpoint,
        "--maker-model",
        maker_model,
        "--checker-model",
        checker_model,
        "--checker-decision",
        "APPROVE",
        "--checker-reason",
        "Looks good",
    )
    if cs.returncode != 0:
        raise AssertionError(f"commit-step failed in {wt_dir}: {cs.stderr}")


class TestRpt7TeamReportUnit(unittest.TestCase):
    """RPT-7: MorningReporter のマルチチーム詳細表示と単一チーム非変更の単体テスト。"""

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

    def test_write_report_shows_per_team_tasks_checker_models_no_changes_and_rc_commit(self) -> None:
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": "1111111aaaaaaa",
                "initial_checkpoint": "1111111aaaaaaa",
                "final_commit": "1111111aaaaaaa",
                "completed_tasks": [],
                "multi_team": {
                    "job_id": "job-rpt7-u1",
                    "job_state": "INTEGRATED",
                    "base_ref": "main",
                    "base_commit": "1111111aaaaaaa",
                    "base_head": "1111111aaaaaaa",
                    "base_unchanged": True,
                    "rc_tag": "rc/job-rpt7-u1",
                    "rc_commit": "2222222bbbbbbb",
                    "all_no_changes": False,
                    "teams": [
                        {
                            "team_id": "core",
                            "status": "COMPLETED",
                            "completed_tasks": 1,
                            "total_tasks": 1,
                            "failed_tasks": 0,
                            "branch": "team/core",
                            "quarantine_branches": [],
                            "no_changes": False,
                            "tasks": [
                                {
                                    "task_id": "1",
                                    "title": "Core task one",
                                    "status": "COMPLETED",
                                    "commit": "3333333ccccccc",
                                    "checker_decision": "APPROVE",
                                    "checker_model": "claude-opus-5.5",
                                    "maker_model": "gemini-3.8-flash",
                                }
                            ],
                        },
                        {
                            "team_id": "ui",
                            "status": "NO_CHANGES",
                            "completed_tasks": 0,
                            "total_tasks": 1,
                            "failed_tasks": 0,
                            "branch": "team/ui",
                            "quarantine_branches": [],
                            "no_changes": True,
                            "tasks": [],
                        },
                    ],
                    "integration_errors": [],
                    "pending_decisions": [],
                },
            },
        )
        content = report_path.read_text(encoding="utf-8")
        self.assertIn("Core task one", content)
        self.assertIn("task-1", content)
        self.assertIn("APPROVE", content)
        self.assertIn("claude-opus-5.5", content)
        self.assertIn("gemini-3.8-flash", content)
        self.assertIn("NO_CHANGES", content)
        self.assertIn("2222222bbbbbbb", content)
        self.assertIn("rc/job-rpt7-u1", content)
        self.assertIn("main", content)
        self.assertIn("未変更", content)
        self.assertNotIn("最新コミット (HEAD)", content)
        self.assertNotIn("完了した新規タスクはありません", content)

    def test_write_report_handles_missing_models_and_all_no_changes(self) -> None:
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "NO_CHANGES",
                "total_tasks": 1,
                "succeeded_count": 0,
                "quarantined_count": 0,
                "checkpoint": "aaaa111",
                "initial_checkpoint": "aaaa111",
                "final_commit": "aaaa111",
                "completed_tasks": [],
                "multi_team": {
                    "job_id": "job-rpt7-u2",
                    "job_state": "NO_CHANGES",
                    "base_ref": "main",
                    "base_commit": "aaaa111",
                    "base_head": "aaaa111",
                    "base_unchanged": True,
                    "rc_tag": None,
                    "rc_commit": None,
                    "all_no_changes": True,
                    "teams": [
                        {
                            "team_id": "core",
                            "status": "NO_CHANGES",
                            "completed_tasks": 0,
                            "total_tasks": 1,
                            "failed_tasks": 0,
                            "branch": "team/core",
                            "quarantine_branches": [],
                            "no_changes": True,
                            "tasks": [
                                {
                                    "task_id": "1",
                                    "title": "Unchanged task",
                                    "status": "PENDING",
                                    "commit": "",
                                    "checker_decision": "N/A",
                                    "checker_model": "記録なし",
                                    "maker_model": "記録なし",
                                }
                            ],
                        }
                    ],
                    "integration_errors": [],
                    "pending_decisions": [],
                },
            },
        )
        content = report_path.read_text(encoding="utf-8")
        self.assertIn("NO_CHANGES", content)
        self.assertIn("記録なし", content)
        self.assertIn("未変更", content)
        self.assertNotIn("最新コミット (HEAD)", content)

    def test_single_team_report_keeps_latest_head_line(self) -> None:
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "initial_checkpoint": "003fd387092bab02",
                "final_commit": "84f864138968304a",
                "completed_tasks": [
                    {
                        "task_id": "1",
                        "title": "Single task",
                        "commit": "84f864138968304a",
                        "evidence_tree_sha": "tree123",
                        "checker_decision": "APPROVE",
                    }
                ],
            },
        )
        content = report_path.read_text(encoding="utf-8")
        self.assertIn("| **最新コミット (HEAD)** | `84f864138968304a` |", content)

    def test_format_console_brief_shows_tasks_models_no_changes_and_rc_commit(self) -> None:
        reporter = MorningReporter(repo_dir=self.repo)
        brief_text = reporter.format_console_brief(
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": "1111111aaaaaaa",
                "completed_tasks": [],
                "multi_team": {
                    "job_id": "job-rpt7-brief",
                    "job_state": "INTEGRATED",
                    "base_ref": "main",
                    "base_commit": "1111111aaaaaaa",
                    "base_unchanged": True,
                    "rc_tag": "rc/job-rpt7-brief",
                    "rc_commit": "2222222bbbbbbb",
                    "teams": [
                        {
                            "team_id": "core",
                            "status": "COMPLETED",
                            "completed_tasks": 1,
                            "total_tasks": 1,
                            "failed_tasks": 0,
                            "branch": "team/core",
                            "quarantine_branches": [],
                            "tasks": [
                                {
                                    "task_id": "1",
                                    "title": "Core task one",
                                    "status": "COMPLETED",
                                    "commit": "3333333ccccccc",
                                    "checker_decision": "APPROVE",
                                    "checker_model": "claude-opus-5.5",
                                    "maker_model": "gemini-3.8-flash",
                                }
                            ],
                        },
                        {
                            "team_id": "ui",
                            "status": "NO_CHANGES",
                            "completed_tasks": 0,
                            "total_tasks": 0,
                            "failed_tasks": 0,
                            "branch": "team/ui",
                            "quarantine_branches": [],
                            "tasks": [],
                        },
                    ],
                    "integration_errors": [],
                    "pending_decisions": [],
                },
            }
        )
        self.assertIn("Core task one", brief_text)
        self.assertIn("APPROVE", brief_text)
        self.assertIn("claude-opus-5.5", brief_text)
        self.assertIn("gemini-3.8-flash", brief_text)
        self.assertIn("NO_CHANGES", brief_text)
        self.assertIn("2222222bbbbbbb", brief_text)
        self.assertIn("未変更", brief_text)


class TestRpt7TeamReportAcceptance(unittest.TestCase):
    """RPT-7: チームジョブの NIGHTLY_REPORT.md / auto-dev brief の受入テスト。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.base_commit = _init_multi_team_repo(self.repo_dir)
        self.scripts_dir = Path(self.tmp_dir.name) / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_report_and_brief_with_partial_no_changes_and_after_cleanup(self) -> None:
        job_id = "job-rpt7-partial"
        # ui チームはコミット 0 件 (NO_CHANGES) の構成とする
        (self.repo_dir / "plans" / "ui.md").write_text("# PLAN (ui)\n\n## ステップ\n", encoding="utf-8")
        _git(self.repo_dir, "add", "plans/ui.md")
        _git(self.repo_dir, "commit", "-m", "empty ui plan")
        base2 = _git(self.repo_dir, "rev-parse", "HEAD").stdout.strip()
        repo2 = self.repo_dir

        prep2 = _run_cli(repo2, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep2.returncode, 0, prep2.stderr)
        _complete_task_via_lowlevel_cli(
            repo2 / ".dev" / "worktrees" / "core",
            "1",
            "src/core/app.py",
            "VALUE = 10\n",
            maker_model="gemini-3.8-flash",
            checker_model="claude-opus-5.5",
        )

        integ = _run_cli(repo2, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(integ.returncode, 0, f"stdout={integ.stdout}\nstderr={integ.stderr}")
        rc_commit = _git(repo2, "rev-parse", f"refs/tags/rc/{job_id}^{{commit}}").stdout.strip()
        self.assertEqual(_git(repo2, "rev-parse", "main").stdout.strip(), base2)

        for phase_label in ("before_cleanup", "after_cleanup"):
            if phase_label == "after_cleanup":
                cln = _run_cli(repo2, "teams", "cleanup", "--job-id", job_id)
                self.assertEqual(cln.returncode, 0, f"cleanup failed: {cln.stderr}")
                self.assertFalse((repo2 / ".dev" / "worktrees" / "core").exists())

            rpt = _run_cli(repo2, "report")
            self.assertEqual(rpt.returncode, 0, rpt.stderr)
            report_text = (repo2 / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")

            brief = _run_cli(repo2, "brief")
            self.assertEqual(brief.returncode, 0, brief.stderr)

            for out_name, text in [("NIGHTLY_REPORT.md", report_text), ("brief", brief.stdout)]:
                with self.subTest(phase=phase_label, output=out_name):
                    self.assertIn("Core task one", text)
                    self.assertIn("APPROVE", text)
                    self.assertIn("claude-opus-5.5", text)
                    self.assertIn("gemini-3.8-flash", text)
                    self.assertIn("NO_CHANGES", text)
                    self.assertIn(rc_commit, text)
                    self.assertIn(f"rc/{job_id}", text)
                    self.assertIn("main", text)
                    self.assertIn("未変更", text)
                    self.assertNotIn("最新コミット (HEAD)", text)
                    self.assertNotIn("完了した新規タスクはありません", text)

    def test_teams_run_records_models_and_handles_checker_reject(self) -> None:
        maker_ok = self.scripts_dir / "maker_ok.py"
        maker_ok.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / 'app.py').write_text('VALUE = 77\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    (cwd / 'src' / 'ui' / 'view.py').write_text('VIEW = 88\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        checker_conditional = self.scripts_dir / "checker_cond.py"
        checker_conditional.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'ui':\n"
            "    print('DECISION: REJECT')\n"
            "else:\n"
            "    print('DECISION: APPROVE')\n",
            encoding="utf-8",
        )

        # 1. 成功ケース: teams run -> report / brief にモデル名・APPROVE・RC コミットが出る
        job_ok = "job-rpt7-run-ok"
        res_ok = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            job_ok,
            "--maker-cmd",
            f"{sys.executable} {maker_ok}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(res_ok.returncode, 0, f"stdout={res_ok.stdout}\nstderr={res_ok.stderr}")
        rc_commit = _git(self.repo_dir, "rev-parse", f"refs/tags/rc/{job_ok}^{{commit}}").stdout.strip()

        brief_ok = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief_ok.returncode, 0, brief_ok.stderr)
        report_ok = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        for out_name, text in [("NIGHTLY_REPORT.md", report_ok), ("brief", brief_ok.stdout)]:
            with self.subTest(case="teams_run_ok", output=out_name):
                self.assertIn("Core task one", text)
                self.assertIn("UI task one", text)
                self.assertIn("APPROVE", text)
                self.assertIn("claude-opus-5.5", text)
                self.assertIn("gemini-3.8-flash", text)
                self.assertIn(rc_commit, text)
                self.assertIn("未変更", text)
                self.assertNotIn("最新コミット (HEAD)", text)

        # cleanup して次ジョブで ui の Checker REJECT を検証
        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_ok)
        self.assertEqual(cln.returncode, 0, cln.stderr)

        job_rej = "job-rpt7-run-rej"
        res_rej = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            job_rej,
            "--maker-cmd",
            f"{sys.executable} {maker_ok}",
            "--checker-cmd",
            f"{sys.executable} {checker_conditional}",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(res_rej.returncode, 1, f"stdout={res_rej.stdout}\nstderr={res_rej.stderr}")

        rpt_rej = _run_cli(self.repo_dir, "report")
        self.assertEqual(rpt_rej.returncode, 0, rpt_rej.stderr)
        brief_rej = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief_rej.returncode, 0, brief_rej.stderr)
        report_rej = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")

        for out_name, text in [("NIGHTLY_REPORT.md", report_rej), ("brief", brief_rej.stdout)]:
            with self.subTest(case="teams_run_reject", output=out_name):
                self.assertIn("Core task one", text)
                self.assertIn("UI task one", text)
                self.assertIn("APPROVE", text)
                self.assertIn("REJECT", text)
                self.assertIn("FAILED", text)
                self.assertIn("claude-opus-5.5", text)
                self.assertIn("gemini-3.8-flash", text)
                self.assertIn("未変更", text)
                self.assertNotIn("最新コミット (HEAD)", text)

    def test_all_no_changes_and_corrupt_bundle_resilience(self) -> None:
        # 1. 全チーム NO_CHANGES の場合
        (self.repo_dir / "plans" / "core.md").write_text("# PLAN (core)\n\n## ステップ\n", encoding="utf-8")
        (self.repo_dir / "plans" / "ui.md").write_text("# PLAN (ui)\n\n## ステップ\n", encoding="utf-8")
        _git(self.repo_dir, "add", "plans/core.md", "plans/ui.md")
        _git(self.repo_dir, "commit", "-m", "empty plans")

        job_nc = "job-rpt7-allnc"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_nc).returncode, 0)
        integ_nc = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_nc)
        self.assertEqual(integ_nc.returncode, 0, integ_nc.stderr)

        brief_nc = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief_nc.returncode, 0, brief_nc.stderr)
        report_nc = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        for out_name, text in [("NIGHTLY_REPORT.md", report_nc), ("brief", brief_nc.stdout)]:
            with self.subTest(case="all_no_changes", output=out_name):
                self.assertIn("NO_CHANGES", text)
                self.assertIn("main", text)
                self.assertIn("未変更", text)
                self.assertNotIn("最新コミット (HEAD)", text)

        self.assertEqual(_run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_nc).returncode, 0)

        # 2. cleanup 後に保全 Bundle が破損していても安全にフォールバックすること
        (self.repo_dir / "plans" / "core.md").write_text(
            "# PLAN (core)\n\n## ステップ\n- [ ] 1. Core task one\n", encoding="utf-8"
        )
        _git(self.repo_dir, "add", "plans/core.md")
        _git(self.repo_dir, "commit", "-m", "restore core task")

        job_corrupt = "job-rpt7-corrupt"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_corrupt).returncode, 0)
        _complete_task_via_lowlevel_cli(
            self.repo_dir / ".dev" / "worktrees" / "core",
            "1",
            "src/core/app.py",
            "VALUE = 99\n",
        )
        self.assertEqual(_run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_corrupt).returncode, 0)
        self.assertEqual(_run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_corrupt).returncode, 0)

        corrupt_zip = self.repo_dir / ".dev" / "integration_jobs" / job_corrupt / "evidence" / "core.zip"
        self.assertTrue(corrupt_zip.is_file())
        corrupt_zip.write_bytes(b"corrupted-zip-data")

        brief_c = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief_c.returncode, 0, brief_c.stderr)
        report_c = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("Core task one", report_c)
        self.assertIn("N/A", report_c)


if __name__ == "__main__":
    unittest.main()
