from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.bundler import EvidenceBundler
from auto_developer.multi_team import (
    MultiTeamConfig,
    MultiTeamOrchestrator,
    TeamExecutionResult,
    TeamWorkerSpec,
    integrate_teams_job,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _clean_subproc_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing_pp}" if existing_pp else src_dir
    return env


def _run_cli(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo_dir), *args],
        cwd=repo_dir,
        env=_clean_subproc_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_repo(
    repo_dir: Path,
    *,
    core_plan_text: str,
    ui_plan_text: str,
) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(
        ".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n*.py[cod]\n",
        encoding="utf-8",
    )
    (repo_dir / "SPEC.md").write_text("# SPEC\n- TEAM-27\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Root PLAN\n\n## ステップ\n- [ ] 1. Root\n", encoding="utf-8")
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    verify_sh = repo_dir / "scripts" / "verify_local.sh"
    verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    verify_sh.chmod(0o755)

    (repo_dir / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo_dir / "src" / "ui" / "view.py").write_text("VIEW = 1\n", encoding="utf-8")

    (repo_dir / "plans").mkdir(parents=True, exist_ok=True)
    (repo_dir / "plans" / "core.md").write_text(core_plan_text, encoding="utf-8")
    (repo_dir / "plans" / "ui.md").write_text(ui_plan_text, encoding="utf-8")

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
        raise AssertionError(f"verify-step failed in {wt_dir}: {vs.stderr}\n{vs.stdout}")

    from review_test_helpers import record_test_review

    rr = record_test_review(wt_dir, task_id, reason="Verified")
    if rr.returncode != 0:
        raise AssertionError(f"record-review failed in {wt_dir}: {rr.stderr}\n{rr.stdout}")

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
        "Verified",
    )
    if cs.returncode != 0:
        raise AssertionError(f"commit-step failed in {wt_dir}: {cs.stderr}\n{cs.stdout}")


class TestTeam27NoChanges(unittest.TestCase):
    """TEAM-27: 基点からのコミットが0件のチームを NO_CHANGES として扱う。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_tmp = Path(self.tmp_dir.name)
        self.repo_dir = self.base_tmp / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.base_tmp / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_integrate_skips_no_changes_team_and_integrates_changed_team(self) -> None:
        base_commit = _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n<!-- まだタスクなし -->\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task one\n",
        )
        job_id = "job-partial-nc"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 42\n")

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")
        self.assertIn("NO_CHANGES", res.stdout)
        self.assertIn("core", res.stdout)

        rc_tag = f"rc/{job_id}"
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), rc_tag)
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), base_commit)
        self.assertEqual(_git(self.repo_dir, "show", f"{rc_tag}:src/ui/view.py").stdout, "VIEW = 42\n")

        # core は Bundle 生成・保全の対象外、ui は保全・検証済み
        core_zip = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "core.zip"
        ui_zip = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip"
        self.assertFalse(core_zip.exists())
        self.assertTrue(ui_zip.is_file())
        self.assertTrue(EvidenceBundler(self.repo_dir).verify_bundle(ui_zip).is_valid)

        # レポートと brief に NO_CHANGES が表示される
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("NO_CHANGES", report_text)
        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("NO_CHANGES", brief.stdout)

        # JSON 再実行の冪等性確認
        res_json = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id, "--json")
        self.assertEqual(res_json.returncode, 0, res_json.stderr)
        payload = json.loads(res_json.stdout)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["rc_tag"], rc_tag)
        self.assertEqual(payload["no_changes_teams"], ["core"])

        # force なしで cleanup が成功し、次の prepare も通る
        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, f"stdout={cln.stdout}\nstderr={cln.stderr}")
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), rc_tag)
        prep2 = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-after-nc")
        self.assertEqual(prep2.returncode, 0, prep2.stderr)

    def test_integrate_all_no_changes_skips_rc_tag_and_exits_zero(self) -> None:
        base_commit = _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n- [x] 1. Already done\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n",
        )
        job_id = "job-all-nc"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")
        self.assertIn("NO_CHANGES", res.stdout)

        # RC タグも統合ブランチも作成されず、main も不変
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertEqual(_git(self.repo_dir, "branch", "--list", f"integration/{job_id}").stdout.strip(), "")
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), base_commit)

        # JSON 出力でも NO_CHANGES と rc_tag=None を返す
        res_json = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id, "--json")
        self.assertEqual(res_json.returncode, 0, res_json.stderr)
        payload = json.loads(res_json.stdout)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["status"], "NO_CHANGES")
        self.assertIsNone(payload["rc_tag"])
        self.assertEqual(payload["no_changes_teams"], ["core", "ui"])

        # レポートと brief に NO_CHANGES が表示され、--force 不要であること
        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("NO_CHANGES", report_text)
        brief = _run_cli(self.repo_dir, "brief")
        self.assertEqual(brief.returncode, 0, brief.stderr)
        self.assertIn("NO_CHANGES", brief.stdout)

        # force なしで cleanup が成功し、次の prepare が通る
        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, f"stdout={cln.stdout}\nstderr={cln.stderr}")
        prep2 = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-all-nc-next")
        self.assertEqual(prep2.returncode, 0, prep2.stderr)

    def test_teams_run_partial_no_changes_ignores_zero_task_integrity_only_bundle(self) -> None:
        _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n- [x] 1. Done earlier\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task one\n",
        )
        maker_script = self.scripts_dir / "maker_ui_only.py"
        maker_script.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'ui':\n"
            "    (cwd / 'src' / 'ui' / 'view.py').write_text('VIEW = 88\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )

        job_id = "job-run-partial-nc"
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
        self.assertIn("NO_CHANGES", proc.stdout)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), f"rc/{job_id}")
        self.assertEqual(_git(self.repo_dir, "show", f"rc/{job_id}:src/ui/view.py").stdout, "VIEW = 88\n")
        self.assertFalse((self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "core.zip").exists())
        self.assertTrue((self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip").is_file())

        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("NO_CHANGES", report_text)
        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, f"stdout={cln.stdout}\nstderr={cln.stderr}")

    def test_teams_run_all_no_changes_exits_zero_without_rc_tag(self) -> None:
        base_commit = _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n",
        )
        job_id = "job-run-all-nc"
        proc = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            job_id,
            "--mock",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")
        self.assertIn("NO_CHANGES", proc.stdout)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), base_commit)

        report_text = (self.repo_dir / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
        self.assertIn("NO_CHANGES", report_text)
        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, f"stdout={cln.stdout}\nstderr={cln.stderr}")

    def test_integrate_rejects_no_changes_team_with_incomplete_or_failed_plan(self) -> None:
        _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n- [ ] 1. Unfinished core task\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task one\n",
        )
        job_id = "job-nc-incomplete"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 7\n")

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        self.assertIn("core", res.stdout + "\n" + res.stderr)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "core").is_dir())
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_rejects_no_changes_team_with_failed_marker_even_with_x(self) -> None:
        _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n- [ ] 1. Failed core task **[FAILED]**\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n",
        )
        job_id = "job-nc-failed-marker"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        self.assertIn("core", res.stdout + "\n" + res.stderr)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertTrue((self.repo_dir / ".dev" / "worktrees" / "core").is_dir())

    def test_integrate_rejects_no_changes_team_with_dirty_worktree(self) -> None:
        _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task one\n",
        )
        job_id = "job-nc-dirty"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)
        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 9\n")

        (core_wt / "src" / "core" / "dirty.py").write_text("X = 1\n", encoding="utf-8")

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        self.assertIn("core", res.stdout + "\n" + res.stderr)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertTrue(core_wt.is_dir())
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_still_rejects_committed_team_without_evidence(self) -> None:
        _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task one\n",
        )
        job_id = "job-nc-plus-bad-commit"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        (ui_wt / "plans" / "ui.md").write_text(
            "# PLAN (ui)\n\n## ステップ\n- [x] 1. UI task one\n",
            encoding="utf-8",
        )
        (ui_wt / "src" / "ui" / "view.py").write_text("VIEW = 999\n", encoding="utf-8")
        _git(ui_wt, "add", "-A")
        _git(ui_wt, "commit", "-m", "ui without evidence")

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        self.assertIn("ui", res.stdout + "\n" + res.stderr)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_fail_closed_when_git_rev_list_fails(self) -> None:
        _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n- [x] 1. Done\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n- [x] 1. Done\n",
        )
        job_id = "job-rev-list-fail"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)

        from auto_developer import multi_team

        orig_run_git = multi_team._run_git_cmd

        def fake_run_git(repo_dir: Path, args: list[str]) -> subprocess.CompletedProcess[str]:
            if args and args[0] == "rev-list":
                return subprocess.CompletedProcess(["git", *args], returncode=128, stdout="", stderr="rev-list failed")
            return orig_run_git(repo_dir, args)

        with mock.patch.object(multi_team, "_run_git_cmd", side_effect=fake_run_git):
            payload, code, err = integrate_teams_job(self.repo_dir, job_id=job_id)

        self.assertIsNone(payload)
        self.assertEqual(code, 1)
        self.assertIn("rev-list", err)

    def test_integrate_team_results_all_no_changes_skips_integration_gate(self) -> None:
        _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n",
        )
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-lib-nc",
            teams=[
                TeamWorkerSpec(team_id="core", base_branch="main"),
                TeamWorkerSpec(team_id="ui", base_branch="main"),
            ],
            base_branch="main",
        )
        results = {
            "core": TeamExecutionResult(team_id="core", success=True, status="NO_CHANGES", branch="team/core"),
            "ui": TeamExecutionResult(team_id="ui", success=True, status="NO_CHANGES", branch="team/ui"),
        }
        with mock.patch.object(orchestrator.integration_gate, "execute_integration") as mock_gate:
            multi_res = orchestrator.integrate_team_results(config, results)

        mock_gate.assert_not_called()
        self.assertTrue(multi_res.success)
        self.assertEqual(multi_res.status, "NO_CHANGES")
        self.assertIsNone(multi_res.release_candidate_tag)
        self.assertEqual(multi_res.details.get("no_changes_teams"), ["core", "ui"])

    def test_cleanup_refuses_no_changes_team_with_post_integrate_commit(self) -> None:
        _init_repo(
            self.repo_dir,
            core_plan_text="# PLAN (core)\n\n## ステップ\n",
            ui_plan_text="# PLAN (ui)\n\n## ステップ\n",
        )
        job_id = "job-nc-post-commit"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)
        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        (core_wt / "src" / "core" / "app.py").write_text("VALUE = 123\n", encoding="utf-8")
        _git(core_wt, "add", "src/core/app.py")
        _git(core_wt, "commit", "-m", "post-integrate unmerged commit")

        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(cln.returncode, 0)
        self.assertTrue(core_wt.is_dir())


if __name__ == "__main__":
    unittest.main()
