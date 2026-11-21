from __future__ import annotations

import json
import os
import subprocess
import sys
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


def _run_cli(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(work_dir), *args],
        cwd=work_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _init_two_team_repo(repo_dir: Path) -> str:
    """`auto-dev init` と `auto-dev teams init --teams core,ui` で一時2チームリポジトリを初期化する。"""
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "E2E Runner")
    _git(repo_dir, "config", "user.email", "e2e@example.com")

    init_proc = _run_cli(repo_dir, "init")
    if init_proc.returncode != 0:
        raise AssertionError(f"init failed: {init_proc.stdout}\n{init_proc.stderr}")

    teams_init_proc = _run_cli(repo_dir, "teams", "init", "--teams", "core,ui")
    if teams_init_proc.returncode != 0:
        raise AssertionError(
            f"teams init failed: {teams_init_proc.stdout}\n{teams_init_proc.stderr}"
        )

    # 既存パーサーは HTML コメント内のタスク記法例も読み取るため、E2E 用の計画ファイルはコメント例なしで定義する
    (repo_dir / "PLAN.md").write_text(
        "# PLAN.md\n\n## 現在の目標\n- マルチチーム開発\n\n## ステップ\n- [ ] 1. ルート統合管理\n",
        encoding="utf-8",
    )
    for tid in ("core", "ui"):
        (repo_dir / "plans" / f"{tid}.md").write_text(
            f"# PLAN ({tid})\n\n## 現在の目標\n- チーム `{tid}` の実装と検証\n\n"
            "## ステップ\n- [ ] 1. 初期モジュールの実装\n- [ ] 2. 単体テストの追加と検証\n",
            encoding="utf-8",
        )

    (repo_dir / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core" / "__init__.py").write_text("CORE_VERSION = 1\n", encoding="utf-8")
    (repo_dir / "src" / "ui" / "__init__.py").write_text("UI_VERSION = 1\n", encoding="utf-8")

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "chore: initial two-team project")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


class TestTeamsCliE2E(unittest.TestCase):
    """TEAM-19: 2チーム構成の一時リポジトリに対する CLI E2E 受入テスト。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_tmp = Path(self.tmp_dir.name)
        self.repo_dir = self.base_tmp / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.base_tmp / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.base_commit = _init_two_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _assert_rc_contains_both_teams(
        self,
        rc_tag: str,
        job_id: str,
        expected_files: dict[str, str],
    ) -> None:
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), rc_tag)
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), self.base_commit)

        for team_id in ("core", "ui"):
            plan_text = _git(self.repo_dir, "show", f"{rc_tag}:plans/{team_id}.md").stdout
            self.assertIn("- [x] 1. 初期モジュールの実装", plan_text)
            self.assertIn("- [x] 2. 単体テストの追加と検証", plan_text)
            self.assertNotIn("- [ ]", plan_text)

        for rel_path, expected_content in expected_files.items():
            actual = _git(self.repo_dir, "show", f"{rc_tag}:{rel_path}").stdout
            self.assertEqual(actual, expected_content, f"Mismatch in {rc_tag}:{rel_path}")

        for team_id in ("core", "ui"):
            bundle_zip = (
                self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / f"{team_id}.zip"
            )
            self.assertTrue(bundle_zip.is_file(), f"Missing preserved bundle: {bundle_zip}")
            vb = _run_cli(self.repo_dir, "verify-bundle", str(bundle_zip))
            self.assertEqual(
                vb.returncode,
                0,
                f"verify-bundle failed for {team_id}: {vb.stdout}\n{vb.stderr}",
            )

    def test_e2e_teams_run_integrates_both_teams_and_creates_rc_tag(self) -> None:
        """(a) `teams run`（mock の Maker / Checker）で両チームの変更が統合され RC タグができる。"""
        maker_script = self.scripts_dir / "mock_maker_ok.py"
        maker_script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "task_id = os.environ.get('AUTO_DEV_TASK_ID', '1')\n"
            "if cwd.name == 'core':\n"
            "    if task_id == '1':\n"
            "        (cwd / 'src' / 'core' / 'engine.py').write_text('ENGINE = \"v1\"\\n', encoding='utf-8')\n"
            "    else:\n"
            "        (cwd / 'src' / 'core' / 'service.py').write_text('SERVICE = True\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    if task_id == '1':\n"
            "        (cwd / 'src' / 'ui' / 'widget.py').write_text('WIDGET = \"ready\"\\n', encoding='utf-8')\n"
            "    else:\n"
            "        (cwd / 'src' / 'ui' / 'screen.py').write_text('SCREEN = \"main\"\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        job_id = "e2e-run-01"
        proc = _run_cli(
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
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")

        rc_tag = f"rc/{job_id}"
        self.assertIn(rc_tag, proc.stdout)
        self._assert_rc_contains_both_teams(
            rc_tag,
            job_id,
            {
                "src/core/engine.py": 'ENGINE = "v1"\n',
                "src/core/service.py": "SERVICE = True\n",
                "src/ui/widget.py": 'WIDGET = "ready"\n',
                "src/ui/screen.py": 'SCREEN = "main"\n',
            },
        )

        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn(job_id, brief.stdout)
        self.assertIn(rc_tag, brief.stdout)

    def test_e2e_out_of_owns_team_is_quarantined_and_not_integrated(self) -> None:
        """(b) 担当範囲外（他チームの owns 等）を変更したチームは隔離され、統合されない。"""
        maker_script = self.scripts_dir / "mock_maker_boundary_violation.py"
        maker_script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "task_id = os.environ.get('AUTO_DEV_TASK_ID', '1')\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / f'mod_{task_id}.py').write_text(f'MOD = {task_id}\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    # 担当範囲外の追跡済みファイル変更と未追跡ファイル作成を行う\n"
            "    (cwd / 'src' / 'core' / '__init__.py').write_text('CORE_VERSION = 999\\n', encoding='utf-8')\n"
            "    (cwd / 'src' / 'core' / 'intrusion.py').write_text('INTRUSION = True\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
        job_id = "e2e-quarantine-01"
        proc = _run_cli(
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
        self.assertEqual(proc.returncode, 1, f"stdout={proc.stdout}\nstderr={proc.stderr}")

        rc_tag = f"rc/{job_id}"
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), "")
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), self.base_commit)
        self.assertEqual(
            (self.repo_dir / "src" / "core" / "__init__.py").read_text(encoding="utf-8"),
            "CORE_VERSION = 1\n",
        )
        self.assertFalse((self.repo_dir / "src" / "core" / "intrusion.py").exists())

        q_branches = [
            line.lstrip("* ").strip()
            for line in _git(self.repo_dir, "branch", "--list", "quarantine/e2e-quarantine-01/ui/*").stdout.splitlines()
            if line.strip()
        ]
        self.assertTrue(q_branches, "Expected a quarantine/e2e-quarantine-01/ui/* branch to be created")
        q_core_init = _git(self.repo_dir, "show", f"{q_branches[0]}:src/core/__init__.py").stdout
        self.assertEqual(q_core_init, "CORE_VERSION = 999\n")
        q_plan = _git(self.repo_dir, "show", f"{q_branches[0]}:plans/ui.md").stdout
        self.assertIn("**[FAILED]**", q_plan)

        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        self.assertTrue(ui_wt.is_dir())
        self.assertEqual(_git(ui_wt, "status", "--porcelain").stdout.strip(), "")
        self.assertEqual(
            (ui_wt / "src" / "core" / "__init__.py").read_text(encoding="utf-8"),
            "CORE_VERSION = 1\n",
        )
        self.assertFalse((ui_wt / "src" / "core" / "intrusion.py").exists())

        status_proc = _run_cli(self.repo_dir, "teams", "status", "--job-id", job_id, "--json")
        self.assertEqual(status_proc.returncode, 0, status_proc.stderr)
        status_data = json.loads(status_proc.stdout)
        teams_by_id = {t["team_id"]: t for t in status_data["teams"]}
        self.assertTrue(teams_by_id["ui"]["quarantine_branches"])
        self.assertEqual(teams_by_id["ui"]["total_tasks"], 2)
        self.assertEqual(teams_by_id["core"]["total_tasks"], 2)

        # 明示的に integrate を呼んでも統合されないこと
        int_proc = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(int_proc.returncode, 1)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), "")

    def test_e2e_config_errors_exit_with_code_2(self) -> None:
        """(c) depends_on や重なった owns などの設定エラーで TEAMS_CONFIG_ERROR / 終了コード 2 になる。"""
        base_teams = json.loads((self.repo_dir / "teams.json").read_text(encoding="utf-8"))

        invalid_cases = [
            (
                "non_empty_depends_on",
                [
                    {"id": "core", "plan": "plans/core.md", "owns": ["src/core/**"], "depends_on": []},
                    {
                        "id": "ui",
                        "plan": "plans/ui.md",
                        "owns": ["src/ui/**"],
                        "depends_on": ["core"],
                    },
                ],
            ),
            (
                "overlapping_owns",
                [
                    {"id": "core", "plan": "plans/core.md", "owns": ["src/**"], "depends_on": []},
                    {"id": "ui", "plan": "plans/ui.md", "owns": ["src/ui/**"], "depends_on": []},
                ],
            ),
            (
                "plan_inside_other_team_owns",
                [
                    {
                        "id": "core",
                        "plan": "plans/core.md",
                        "owns": ["src/core/**", "plans/**"],
                        "depends_on": [],
                    },
                    {"id": "ui", "plan": "plans/ui.md", "owns": ["src/ui/**"], "depends_on": []},
                ],
            ),
        ]

        for label, teams_list in invalid_cases:
            with self.subTest(case=label):
                payload = dict(base_teams)
                payload["teams"] = teams_list
                (self.repo_dir / "teams.json").write_text(
                    json.dumps(payload, indent=2) + "\n",
                    encoding="utf-8",
                )
                _git(self.repo_dir, "add", "teams.json")
                _git(self.repo_dir, "commit", "-m", f"test invalid config: {label}")

                prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", f"cfg-{label}")
                self.assertEqual(prep.returncode, 2, f"prepare stdout={prep.stdout}\nstderr={prep.stderr}")
                self.assertIn("TEAMS_CONFIG_ERROR:", prep.stderr)

                run = _run_cli(
                    self.repo_dir,
                    "teams",
                    "run",
                    "--job-id",
                    f"cfg-run-{label}",
                    "--maker-cmd",
                    "true",
                    "--checker-cmd",
                    "printf 'DECISION: APPROVE\\n'",
                )
                self.assertEqual(run.returncode, 2, f"run stdout={run.stdout}\nstderr={run.stderr}")
                self.assertIn("TEAMS_CONFIG_ERROR:", run.stderr)
                self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())
                self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "ui").exists())
                self.assertEqual(_git(self.repo_dir, "branch", "--list", "team/*").stdout.strip(), "")

    def test_e2e_lowlevel_prepare_worktree_subcommands_and_integrate(self) -> None:
        """(d) teams prepare → worktree 内の低レベルサブコマンド → teams integrate でも両チームが統合され RC タグができる。"""
        job_id = "e2e-lowlevel-01"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id, "--json")
        self.assertEqual(prep.returncode, 0, f"stdout={prep.stdout}\nstderr={prep.stderr}")
        prep_data = json.loads(prep.stdout)
        self.assertEqual(prep_data["job_id"], job_id)
        worktrees = {t["team_id"]: Path(t["worktree_path"]) for t in prep_data["teams"]}
        self.assertEqual(set(worktrees.keys()), {"core", "ui"})

        deliverables = {
            ("core", "1"): ("src/core/engine.py", 'ENGINE = "lowlevel"\n'),
            ("core", "2"): ("src/core/service.py", "SERVICE = 2\n"),
            ("ui", "1"): ("src/ui/widget.py", 'WIDGET = "lowlevel"\n'),
            ("ui", "2"): ("src/ui/screen.py", "SCREEN = 2\n"),
        }

        for team_id, wt_dir in worktrees.items():
            completed_ids: list[str] = []
            while True:
                pf = _run_cli(wt_dir, "pre-flight")
                self.assertEqual(pf.returncode, 0, f"pre-flight failed ({team_id}): {pf.stderr}")
                pf_data = json.loads(pf.stdout)
                self.assertTrue(pf_data["clean"])
                self.assertEqual(pf_data["team_id"], team_id)
                self.assertEqual(pf_data["plan"], f"plans/{team_id}.md")

                next_task = pf_data["next_task"]
                if next_task is None:
                    break
                task_id = str(next_task["id"])
                rel_file, content = deliverables[(team_id, task_id)]
                target = wt_dir / rel_file
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")

                checkpoint = str(pf_data["checkpoint"])
                vs = _run_cli(wt_dir, "verify-step", "--checkpoint", checkpoint, "--task-id", task_id)
                self.assertEqual(
                    vs.returncode,
                    0,
                    f"verify-step failed ({team_id} task {task_id}): {vs.stdout}\n{vs.stderr}",
                )

                rr = _run_cli(
                    wt_dir,
                    "record-review",
                    "--task-id",
                    task_id,
                    "--decision",
                    "APPROVE",
                    "--checker-model",
                    "claude-opus-5-5-medium",
                    "--maker-model",
                    "gemini-3.8-flash",
                    "--reason",
                    "Matches SPEC and stays within owns",
                )
                self.assertEqual(rr.returncode, 0, f"record-review failed: {rr.stderr}")

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
                    "claude-opus-5-5-medium",
                    "--checker-decision",
                    "APPROVE",
                    "--checker-reason",
                    "Matches SPEC and stays within owns",
                )
                self.assertEqual(
                    cs.returncode,
                    0,
                    f"commit-step failed ({team_id} task {task_id}): {cs.stdout}\n{cs.stderr}",
                )
                completed_ids.append(task_id)

            self.assertEqual(completed_ids, ["1", "2"])

        integrate = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(
            integrate.returncode,
            0,
            f"teams integrate failed: {integrate.stdout}\n{integrate.stderr}",
        )

        rc_tag = f"rc/{job_id}"
        self.assertIn(rc_tag, integrate.stdout)
        self.assertIn("git merge", integrate.stdout)
        self._assert_rc_contains_both_teams(
            rc_tag,
            job_id,
            {rel_path: content for rel_path, content in deliverables.values()},
        )


if __name__ == "__main__":
    unittest.main()
