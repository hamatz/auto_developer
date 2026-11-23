from __future__ import annotations

import json
import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _list_branches(repo_dir: Path, pattern: str) -> list[str]:
    out = _git(repo_dir, "branch", "--list", pattern).stdout
    return [line.lstrip("* ").strip() for line in out.splitlines() if line.strip()]


def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.pop("AUTO_DEV_JOB_ID", None)
    env["PYTHON"] = sys.executable
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return env


def _run_cli(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(work_dir), *args],
        cwd=work_dir,
        env=_clean_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_shipped_template_two_team_repo(repo_dir: Path) -> str:
    """`auto-dev init` と `auto-dev teams init --teams core,ui` の雛形をそのまま使って初期化する。"""
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Template E2E Runner")
    _git(repo_dir, "config", "user.email", "template-e2e@example.com")

    init_proc = _run_cli(repo_dir, "init")
    if init_proc.returncode != 0:
        raise AssertionError(f"init failed: {init_proc.stdout}\n{init_proc.stderr}")

    teams_init_proc = _run_cli(repo_dir, "teams", "init", "--teams", "core,ui")
    if teams_init_proc.returncode != 0:
        raise AssertionError(
            f"teams init failed: {teams_init_proc.stdout}\n{teams_init_proc.stderr}"
        )

    # 雛形の計画ファイルは HTML コメントを削らずそのまま使う（既に ## ステップ にタスク行がある）
    for plan_rel in ("PLAN.md", "plans/core.md", "plans/ui.md"):
        plan_text = (repo_dir / plan_rel).read_text(encoding="utf-8")
        if "<!--" not in plan_text or "- [ ] 1. タスク名  (未実行)" not in plan_text:
            raise AssertionError(f"Expected shipped HTML comment examples in {plan_rel}")

    # 雛形に Python のテスト 1 件だけを追加する（実行時にモジュール import で .pyc が生成される）
    (repo_dir / "tests" / "test_app_import.py").write_text(
        "import unittest\n\n"
        "import tests.test_smoke\n\n\n"
        "class AppImportTest(unittest.TestCase):\n"
        "    def test_module_imports(self):\n"
        "        self.assertTrue(hasattr(tests.test_smoke, 'SmokeTest'))\n",
        encoding="utf-8",
    )

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "chore: initial shipped template two-team project")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


class TestTeamsTemplateE2E(unittest.TestCase):
    """TEAM-24: init / teams init の雛形をそのまま使う受入テスト。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_tmp = Path(self.tmp_dir.name)
        self.repo_dir = self.base_tmp / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.base_tmp / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.base_commit = _init_shipped_template_two_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_template_e2e_run_cleanup_and_next_prepare(self) -> None:
        """(a)(b) 雛形のまま teams run → 進捗確認 → teams cleanup（--force なし）→ 次の teams prepare。"""
        maker_script = self.scripts_dir / "mock_maker_ok.py"
        maker_script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "task_id = os.environ.get('AUTO_DEV_TASK_ID', '1')\n"
            "if cwd.name == 'core':\n"
            "    target = cwd / 'src' / 'core' / ('engine.py' if task_id == '1' else 'service.py')\n"
            "    target.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target.write_text(f'CORE_TASK_{task_id} = True\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    target = cwd / 'src' / 'ui' / ('widget.py' if task_id == '1' else 'screen.py')\n"
            "    target.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target.write_text(f'UI_TASK_{task_id} = True\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

        job_id = "tpl-job-01"
        run_proc = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            job_id,
            "--maker-cmd",
            f"{sys.executable} {maker_script}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(
            run_proc.returncode,
            0,
            f"teams run failed:\nstdout={run_proc.stdout}\nstderr={run_proc.stderr}",
        )

        rc_tag = f"rc/{job_id}"
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), rc_tag)
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), self.base_commit)

        clean_env = _clean_subprocess_env()
        self.assertIsNone(sys.pycache_prefix, "Verification Python must not set sys.pycache_prefix")
        verify_py = clean_env["PYTHON"]
        builtin_prefix = subprocess.run(
            [verify_py, "-c", "import sys; print(sys.pycache_prefix or '')"],
            env=clean_env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(builtin_prefix, "", "Pinned PYTHON must not set sys.pycache_prefix")

        for tid, rel_files in (
            ("core", ("src/core/engine.py", "src/core/service.py")),
            ("ui", ("src/ui/widget.py", "src/ui/screen.py")),
        ):
            plan_text = _git(self.repo_dir, "show", f"{rc_tag}:plans/{tid}.md").stdout
            self.assertIn("<!--", plan_text)
            self.assertIn("- [ ] 1. タスク名  (未実行)", plan_text)
            self.assertIn("- [x] 1. 初期モジュールの実装", plan_text)
            self.assertIn("- [x] 2. 単体テストの追加と検証", plan_text)
            steps_body = plan_text.split("## ステップ", 1)[1]
            self.assertNotIn("- [ ]", steps_body)
            for rel_file in rel_files:
                self.assertTrue(_git(self.repo_dir, "show", f"{rc_tag}:{rel_file}").stdout)

            # バイトコード (.pyc) が worktree 内の __pycache__/*.pyc に生成されたことを条件なしで検証する (TEAM-28)
            wt_dir = self.repo_dir / ".dev" / "worktrees" / tid
            self.assertTrue(wt_dir.is_dir())
            pyc_files = list(wt_dir.rglob("__pycache__/*.pyc"))
            self.assertTrue(pyc_files, f"Expected __pycache__/*.pyc files under {wt_dir}")

        # (a) 進捗がコメント内の記法例（3行）を数えず 2/2 として表示されること
        status_json_proc = _run_cli(self.repo_dir, "teams", "status", "--job-id", job_id, "--json")
        self.assertEqual(status_json_proc.returncode, 0, status_json_proc.stderr)
        status_data = json.loads(status_json_proc.stdout)
        self.assertEqual(status_data["rc_tag"], rc_tag)
        teams_by_id = {t["team_id"]: t for t in status_data["teams"]}
        for tid in ("core", "ui"):
            self.assertEqual(teams_by_id[tid]["completed_tasks"], 2)
            self.assertEqual(teams_by_id[tid]["total_tasks"], 2)
            self.assertEqual(teams_by_id[tid]["failed_tasks"], 0)
            self.assertEqual(teams_by_id[tid]["quarantine_branches"], [])

        status_text_proc = _run_cli(self.repo_dir, "teams", "status", "--job-id", job_id)
        self.assertEqual(status_text_proc.returncode, 0, status_text_proc.stderr)
        self.assertIn("tasks=2/2", status_text_proc.stdout)
        self.assertNotIn("/5", status_text_proc.stdout)

        brief_proc = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief_proc.returncode, 0, brief_proc.stderr)
        self.assertIn(job_id, brief_proc.stdout)
        self.assertIn(rc_tag, brief_proc.stdout)
        self.assertIn("2 / 2", brief_proc.stdout)
        self.assertIn("4 / 4", brief_proc.stdout)
        self.assertNotIn("/ 5", brief_proc.stdout)
        self.assertNotIn("/ 10", brief_proc.stdout)

        report_proc = _run_cli(self.repo_dir, "report")
        self.assertEqual(report_proc.returncode, 0, report_proc.stderr)
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn(job_id, report_text)
        self.assertIn(rc_tag, report_text)
        self.assertIn("2 / 2", report_text)
        self.assertNotIn("/ 5", report_text)

        self.assertEqual(_list_branches(self.repo_dir, "quarantine/*"), [])

        # (b) --force なしの teams cleanup が成功し、隔離ブランチを作らない
        cleanup_proc = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(
            cleanup_proc.returncode,
            0,
            f"teams cleanup failed:\nstdout={cleanup_proc.stdout}\nstderr={cleanup_proc.stderr}",
        )
        self.assertEqual(_list_branches(self.repo_dir, "quarantine/*"), [])
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "ui").exists())
        self.assertEqual(_list_branches(self.repo_dir, "team/*"), [])
        for tid in ("core", "ui"):
            bundle_zip = (
                self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / f"{tid}.zip"
            )
            self.assertTrue(bundle_zip.is_file(), f"Missing preserved bundle: {bundle_zip}")

        # (b) 続けて新しいジョブ ID で teams prepare が成功する
        next_job_id = "tpl-job-02"
        prep2_proc = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", next_job_id, "--json")
        self.assertEqual(
            prep2_proc.returncode,
            0,
            f"next teams prepare failed:\nstdout={prep2_proc.stdout}\nstderr={prep2_proc.stderr}",
        )
        prep2_data = json.loads(prep2_proc.stdout)
        self.assertEqual(prep2_data["job_id"], next_job_id)
        for team_entry in prep2_data["teams"]:
            wt_path = Path(team_entry["worktree_path"])
            self.assertTrue(wt_path.is_dir())
            self.assertEqual(_git(wt_path, "status", "--porcelain").stdout.strip(), "")

    def test_template_e2e_out_of_owns_quarantined_and_not_integrated(self) -> None:
        """(c) 担当範囲外に書いたチームはジョブ別隔離ブランチへ退避され、worktree はクリーンに戻り統合されない。"""
        maker_script = self.scripts_dir / "mock_maker_out_of_owns.py"
        maker_script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "task_id = os.environ.get('AUTO_DEV_TASK_ID', '1')\n"
            "if cwd.name == 'core':\n"
            "    target = cwd / 'src' / 'core' / f'mod_{task_id}.py'\n"
            "    target.parent.mkdir(parents=True, exist_ok=True)\n"
            "    target.write_text(f'MOD = {task_id}\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    intrusion = cwd / 'src' / 'core' / 'intrusion.py'\n"
            "    intrusion.parent.mkdir(parents=True, exist_ok=True)\n"
            "    intrusion.write_text('INTRUSION = True\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

        job_id = "tpl-job-q01"
        run_proc = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            job_id,
            "--maker-cmd",
            f"{sys.executable} {maker_script}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(
            run_proc.returncode,
            1,
            f"Expected teams run to fail:\nstdout={run_proc.stdout}\nstderr={run_proc.stderr}",
        )

        rc_tag = f"rc/{job_id}"
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), "")
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), self.base_commit)
        self.assertFalse((self.repo_dir / "src" / "core" / "intrusion.py").exists())

        q_branches = _list_branches(self.repo_dir, f"quarantine/{job_id}/ui/*")
        self.assertTrue(
            q_branches,
            f"Expected quarantine/{job_id}/ui/* branch, got: {_list_branches(self.repo_dir, 'quarantine/*')}",
        )
        q_plan = _git(self.repo_dir, "show", f"{q_branches[0]}:plans/ui.md").stdout
        self.assertIn("<!--", q_plan)
        self.assertIn("**[FAILED]**", q_plan)

        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        self.assertTrue(ui_wt.is_dir())
        snapshots = sorted((ui_wt / ".dev" / "quarantines").glob("snapshot_task_*.tar.gz"))
        self.assertTrue(
            snapshots, "Expected quarantine snapshot archive for untracked out-of-owns file"
        )
        with tarfile.open(snapshots[-1], "r:gz") as tar:
            self.assertIn("src/core/intrusion.py", tar.getnames())

        self.assertEqual(_git(ui_wt, "status", "--porcelain").stdout.strip(), "")
        self.assertFalse((ui_wt / "src" / "core" / "intrusion.py").exists())

        status_proc = _run_cli(self.repo_dir, "teams", "status", "--job-id", job_id, "--json")
        self.assertEqual(status_proc.returncode, 0, status_proc.stderr)
        status_data = json.loads(status_proc.stdout)
        teams_by_id = {t["team_id"]: t for t in status_data["teams"]}
        self.assertEqual(teams_by_id["ui"]["total_tasks"], 2)
        self.assertEqual(teams_by_id["ui"]["completed_tasks"], 0)
        self.assertEqual(teams_by_id["ui"]["worker_status"], "FAILED")
        self.assertEqual(teams_by_id["ui"]["quarantine_branches"], q_branches)
        self.assertEqual(teams_by_id["core"]["total_tasks"], 2)
        self.assertEqual(teams_by_id["core"]["completed_tasks"], 2)
        self.assertEqual(teams_by_id["core"]["quarantine_branches"], [])

        brief_proc = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief_proc.returncode, 0, brief_proc.stderr)
        self.assertIn(q_branches[0], brief_proc.stdout)
        self.assertNotIn("/ 5", brief_proc.stdout)

        int_proc = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(int_proc.returncode, 1)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), "")


if __name__ == "__main__":
    unittest.main()
