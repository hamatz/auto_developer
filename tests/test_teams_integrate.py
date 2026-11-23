from __future__ import annotations

import contextlib
import hashlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.bundler import EvidenceBundler
from auto_developer.cli import main
from auto_developer.evidence import EvidenceStore
from auto_developer.multi_team import (
    WorkerRegistration,
    WorkerRegistry,
    get_process_start_identifier,
)


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_cli(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}:{existing_pp}" if existing_pp else src_dir
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo_dir), *args],
        cwd=repo_dir,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _init_multi_team_repo(
    repo_dir: Path,
    verify_script_body: str = "#!/usr/bin/env bash\nexit 0\n",
) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\n- TEAM-14\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Root PLAN\n\n## ステップ\n- [ ] 1. Root\n", encoding="utf-8")
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    verify_sh = repo_dir / "scripts" / "verify_local.sh"
    verify_sh.write_text(verify_script_body, encoding="utf-8")
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


def _record_synthetic_approved_commit(
    wt_dir: Path,
    team_id: str,
    task_id: str,
    plan_rel: str,
    file_rel: str,
    content: str,
    base_commit: str,
) -> str:
    """境界検査を介さず直接コミット＋正規 EvidenceStore 記録を作成する（コンフリクトテスト用）。"""
    plan_file = wt_dir / plan_rel
    plan_file.write_text(
        f"# PLAN ({team_id})\n\n## ステップ\n- [x] {task_id}. Done\n",
        encoding="utf-8",
    )
    target = wt_dir / file_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    _git(wt_dir, "add", "-A")
    _git(wt_dir, "commit", "-m", f"team {team_id} task {task_id}")

    commit_sha = _git(wt_dir, "rev-parse", "HEAD").stdout.strip()
    tree_sha = _git(wt_dir, "rev-parse", "HEAD^{tree}").stdout.strip()
    spec_sha = hashlib.sha256((wt_dir / "SPEC.md").read_bytes()).hexdigest()

    ev = EvidenceStore(repo_dir=wt_dir)
    ev.issue_evidence(task_id=task_id, checkpoint=base_commit, tree_sha=tree_sha, spec_sha=spec_sha)
    ev.verify_and_consume(
        task_id=task_id,
        checkpoint=base_commit,
        expected_tree_sha=tree_sha,
        expected_spec_sha=spec_sha,
    )
    ev.archive_record(
        task_id=task_id,
        status="COMPLETED",
        commit_info={"sha": commit_sha, "tree_sha": tree_sha},
        checker_info={"decision": "APPROVE", "reason": "ok"},
        verification_info={"exit_code": 0},
    )
    return commit_sha


class TestTeamsIntegrateCli(unittest.TestCase):
    """TEAM-14: auto-dev teams integrate --job-id ID の受入・単体テスト。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        self.base_commit = _init_multi_team_repo(self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_integrate_succeeds_with_preexisting_bundles_and_prints_rc_and_merge_cmd(self) -> None:
        job_id = "job-ok-bundles"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"

        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 42\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 99\n")

        # 各 worktree で事前に bundle を生成しておく
        self.assertEqual(_run_cli(core_wt, "bundle", "--job-id", f"{job_id}-core").returncode, 0)
        self.assertEqual(_run_cli(ui_wt, "bundle", "--job-id", f"{job_id}-ui").returncode, 0)

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")

        # RC タグが作成され、main は更新されていないこと
        rc_tag = f"rc/{job_id}"
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), rc_tag)
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), self.base_commit)

        # RC タグに両チームの変更が含まれること
        self.assertEqual(_git(self.repo_dir, "show", f"{rc_tag}:src/core/app.py").stdout, "VALUE = 42\n")
        self.assertEqual(_git(self.repo_dir, "show", f"{rc_tag}:src/ui/view.py").stdout, "VIEW = 99\n")

        # 成果 Bundle が保全されていること
        core_zip = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "core.zip"
        ui_zip = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip"
        self.assertTrue(core_zip.is_file())
        self.assertTrue(ui_zip.is_file())
        self.assertTrue(EvidenceBundler(self.repo_dir).verify_bundle(core_zip).is_valid)
        self.assertTrue(EvidenceBundler(self.repo_dir).verify_bundle(ui_zip).is_valid)

        # RC タグと main への取り込みコマンドが出力されること
        self.assertIn(rc_tag, res.stdout)
        self.assertIn("git merge", res.stdout)
        self.assertIn("main", res.stdout)

    def test_integrate_generates_missing_bundles_before_preservation_and_gate(self) -> None:
        job_id = "job-auto-bundle"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"

        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 100\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 200\n")

        # 事前の Bundle は存在しない状態
        self.assertFalse((core_wt / ".dev" / "bundles").exists())
        self.assertFalse((ui_wt / ".dev" / "bundles").exists())

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id, "--json")
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")

        payload = json.loads(res.stdout)
        self.assertTrue(payload["success"])
        self.assertEqual(payload["rc_tag"], f"rc/{job_id}")
        self.assertTrue((self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "core.zip").is_file())
        self.assertTrue((self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip").is_file())
        self.assertEqual(_git(self.repo_dir, "show", f"rc/{job_id}:src/core/app.py").stdout, "VALUE = 100\n")
        self.assertEqual(_git(self.repo_dir, "show", f"rc/{job_id}:src/ui/view.py").stdout, "VIEW = 200\n")

    def test_integrate_rejects_incomplete_plan_and_keeps_worktrees(self) -> None:
        job_id = "job-incomplete"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 2\n")
        # ui は未完了のまま (- [ ])

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        combined = res.stdout + "\n" + res.stderr
        self.assertIn("ui", combined)
        self.assertIn("plans/ui.md", combined)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        # worktree を残す
        self.assertTrue(core_wt.is_dir())
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_rejects_failed_plan_task_and_keeps_worktrees(self) -> None:
        job_id = "job-failed-task"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 3\n")

        (ui_wt / "plans" / "ui.md").write_text(
            "# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task one **[FAILED]**\n",
            encoding="utf-8",
        )
        _git(ui_wt, "add", "plans/ui.md")
        _git(ui_wt, "commit", "-m", "mark ui failed")

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        combined = res.stdout + "\n" + res.stderr
        self.assertIn("ui", combined)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertTrue(core_wt.is_dir())
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_rejects_dirty_team_worktree_and_keeps_worktrees(self) -> None:
        job_id = "job-dirty-wt"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 4\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 4\n")

        # core worktree に未コミットファイルを残す
        (core_wt / "src" / "core" / "untracked.py").write_text("X = 1\n", encoding="utf-8")

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        combined = res.stdout + "\n" + res.stderr
        self.assertIn("core", combined)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertTrue(core_wt.is_dir())
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_rejects_dirty_main_repo_and_keeps_worktrees(self) -> None:
        job_id = "job-dirty-main"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 5\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 5\n")

        # メインリポジトリに未コミット変更を残す
        (self.repo_dir / "dirty.txt").write_text("dirty\n", encoding="utf-8")

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        combined = res.stdout + "\n" + res.stderr
        self.assertIn("clean", combined.lower())
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertTrue(core_wt.is_dir())
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_rejects_missing_evidence_audit_failure_and_keeps_worktrees(self) -> None:
        job_id = "job-audit-fail"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 6\n")

        # ui は Evidence なしで直接コミットし、[x] に書き換える
        (ui_wt / "plans" / "ui.md").write_text(
            "# PLAN (ui)\n\n## ステップ\n- [x] 1. UI task one\n",
            encoding="utf-8",
        )
        (ui_wt / "src" / "ui" / "view.py").write_text("VIEW = 6\n", encoding="utf-8")
        _git(ui_wt, "add", "-A")
        _git(ui_wt, "commit", "-m", "ui without evidence")

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        combined = res.stdout + "\n" + res.stderr
        self.assertIn("ui", combined)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertTrue(core_wt.is_dir())
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_rejects_merge_conflict_identifies_team_and_keeps_worktrees(self) -> None:
        job_id = "job-conflict"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"

        _record_synthetic_approved_commit(
            core_wt, "core", "1", "plans/core.md", "shared.txt", "from core\n", self.base_commit
        )
        _record_synthetic_approved_commit(
            ui_wt, "ui", "1", "plans/ui.md", "shared.txt", "from ui\n", self.base_commit
        )

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 1)
        combined = res.stdout + "\n" + res.stderr
        self.assertIn("ui", combined)
        self.assertIn("conflict", combined.lower())
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
        self.assertEqual(_git(self.repo_dir, "rev-parse", "main").stdout.strip(), self.base_commit)
        self.assertTrue(core_wt.is_dir())
        self.assertTrue(ui_wt.is_dir())

    def test_integrate_rejects_post_integration_verification_failure_and_keeps_worktrees(self) -> None:
        # 両チームの成果物が合わさったときだけ失敗する検証スクリプト
        verify_body = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if grep -q 'VALUE = 77' src/core/app.py && grep -q 'VIEW = 77' src/ui/view.py; then\n"
            "  echo 'INTEGRATION INVARIANT BROKEN'\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n"
        )
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            base_c = _init_multi_team_repo(repo, verify_script_body=verify_body)
            job_id = "job-post-fail"
            self.assertEqual(_run_cli(repo, "teams", "prepare", "--job-id", job_id).returncode, 0)

            core_wt = repo / ".dev" / "worktrees" / "core"
            ui_wt = repo / ".dev" / "worktrees" / "ui"
            _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 77\n")
            _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 77\n")

            res = _run_cli(repo, "teams", "integrate", "--job-id", job_id)
            self.assertEqual(res.returncode, 1)
            combined = res.stdout + "\n" + res.stderr
            self.assertIn("quarantine/", combined)
            self.assertEqual(_git(repo, "tag", "--list", f"rc/{job_id}").stdout.strip(), "")
            self.assertEqual(_git(repo, "rev-parse", "main").stdout.strip(), base_c)
            self.assertTrue(core_wt.is_dir())
            self.assertTrue(ui_wt.is_dir())

    def test_integrate_rejects_unknown_or_invalid_job_id_and_active_worker(self) -> None:
        bad_id = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", "../evil")
        self.assertEqual(bad_id.returncode, 2)

        missing = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", "no-such-job")
        self.assertEqual(missing.returncode, 1)

        job_id = "job-active"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)
        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 8\n")
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 8\n")

        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(10)"],
            start_new_session=True,
        )
        try:
            WorkerRegistry(self.repo_dir, job_id).register_worker(
                WorkerRegistration(
                    team_id="core",
                    pid=proc.pid,
                    pgid=os.getpgid(proc.pid),
                    worktree_path=str(core_wt),
                    base_branch="main",
                    branch_name="team/core",
                    status="RUNNING",
                    start_time=1000.0,
                    updated_at=1000.0,
                    process_start_time=get_process_start_identifier(proc.pid),
                )
            )
            res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
            self.assertEqual(res.returncode, 1)
            self.assertIn("core", res.stdout + "\n" + res.stderr)
            self.assertTrue(core_wt.is_dir())
        finally:
            proc.terminate()
            proc.wait(timeout=5)

    def test_integrate_succeeds_after_fixing_precondition_failure(self) -> None:
        job_id = "job-retry"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 11\n")

        first = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(first.returncode, 1)
        self.assertTrue(ui_wt.is_dir())

        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 11\n")
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            second = main(["--work-dir", str(self.repo_dir), "teams", "integrate", "--job-id", job_id])
        self.assertEqual(second, 0)
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), f"rc/{job_id}")
        self.assertIn(f"rc/{job_id}", buf.getvalue())

        # Re-running integrate on the already-completed unchanged job is idempotent
        third = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id, "--json")
        self.assertEqual(third.returncode, 0, third.stderr)
        self.assertEqual(json.loads(third.stdout)["status"], "ALREADY_INTEGRATED")

        # If a team branch diverges after completion, re-running integrate rejects it
        (ui_wt / "src" / "ui" / "extra.py").write_text("EXTRA = 1\n", encoding="utf-8")
        _git(ui_wt, "add", "src/ui/extra.py")
        _git(ui_wt, "commit", "-m", "post-integration commit")
        diverged = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(diverged.returncode, 1)

    def test_integrate_ignores_html_comment_examples_in_team_plan(self) -> None:
        job_id = "job-comments"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)
        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"

        for wt, tid, rel, content in (
            (core_wt, "core", "src/core/app.py", "VALUE = 55\n"),
            (ui_wt, "ui", "src/ui/view.py", "VIEW = 55\n"),
        ):
            _record_synthetic_approved_commit(wt, tid, "1", f"plans/{tid}.md", rel, content, self.base_commit)
            (wt / "plans" / f"{tid}.md").write_text(
                f"# PLAN ({tid})\n\n<!--\n- [ ] 1. Example pending\n-->\n\n## ステップ\n- [x] 1. {tid} real\n",
                encoding="utf-8",
            )
            _git(wt, "add", f"plans/{tid}.md")
            _git(wt, "commit", "-m", f"add comment in {tid} plan")
            commit_sha = _git(wt, "rev-parse", "HEAD").stdout.strip()
            tree_sha = _git(wt, "rev-parse", "HEAD^{tree}").stdout.strip()
            spec_sha = hashlib.sha256((wt / "SPEC.md").read_bytes()).hexdigest()
            ev = EvidenceStore(repo_dir=wt)
            ev.issue_evidence(task_id="1", checkpoint=self.base_commit, tree_sha=tree_sha, spec_sha=spec_sha)
            ev.verify_and_consume(
                task_id="1",
                checkpoint=self.base_commit,
                expected_tree_sha=tree_sha,
                expected_spec_sha=spec_sha,
            )
            ev.archive_record(
                task_id="1",
                status="COMPLETED",
                commit_info={"sha": commit_sha, "tree_sha": tree_sha},
                checker_info={"decision": "APPROVE", "reason": "ok"},
                verification_info={"exit_code": 0},
            )

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), f"rc/{job_id}")

    def test_integrate_regenerates_bundle_after_gate_failure_and_team_fix_commit(self) -> None:
        verify_body = (
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if grep -q 'VALUE = 77' src/core/app.py && grep -q 'VIEW = 77' src/ui/view.py; then\n"
            "  echo 'INTEGRATION INVARIANT BROKEN'\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n"
        )
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            _init_multi_team_repo(repo, verify_script_body=verify_body)
            job_id = "job-gate-retry"
            self.assertEqual(_run_cli(repo, "teams", "prepare", "--job-id", job_id).returncode, 0)

            core_wt = repo / ".dev" / "worktrees" / "core"
            ui_wt = repo / ".dev" / "worktrees" / "ui"
            _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 77\n")
            _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 77\n")
            u1_sha = _git(ui_wt, "rev-parse", "HEAD").stdout.strip()

            # 1回目: 統合後検証で失敗し、worktree が残る
            first = _run_cli(repo, "teams", "integrate", "--job-id", job_id)
            self.assertEqual(first.returncode, 1)
            self.assertTrue(ui_wt.is_dir())
            ui_zip = repo / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip"
            self.assertTrue(ui_zip.is_file())
            self.assertEqual(EvidenceBundler(repo).verify_bundle(ui_zip).head_commit, u1_sha)

            # ui 側で修正タスクを追加してコミット (新しい HEAD になる)
            (ui_wt / "plans" / "ui.md").write_text(
                "# PLAN (ui)\n\n## ステップ\n- [x] 1. UI task one\n- [ ] 2. Fix cross-team conflict\n",
                encoding="utf-8",
            )
            _git(ui_wt, "add", "plans/ui.md")
            _git(ui_wt, "commit", "-m", "add fix task to ui plan")
            _complete_task_via_lowlevel_cli(ui_wt, "2", "src/ui/view.py", "VIEW = 88\n")
            u2_sha = _git(ui_wt, "rev-parse", "HEAD").stdout.strip()
            self.assertNotEqual(u1_sha, u2_sha)

            # 2回目: 古い Bundle を再利用せず新しい HEAD 用の Bundle を生成・保全して統合成功
            second = _run_cli(repo, "teams", "integrate", "--job-id", job_id)
            self.assertEqual(second.returncode, 0, f"stdout={second.stdout}\nstderr={second.stderr}")
            self.assertEqual(_git(repo, "tag", "--list", f"rc/{job_id}").stdout.strip(), f"rc/{job_id}")
            self.assertEqual(_git(repo, "show", f"rc/{job_id}:src/ui/view.py").stdout, "VIEW = 88\n")
            verified_ui = EvidenceBundler(repo).verify_bundle(ui_zip)
            self.assertTrue(verified_ui.is_valid)
            self.assertEqual(verified_ui.head_commit, u2_sha)

    def test_integrate_does_not_reuse_stale_or_other_job_bundle_in_worktree(self) -> None:
        job_id = "job-fresh-check"
        self.assertEqual(_run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id).returncode, 0)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"

        # core: task 1 の時点で bundle を作り、その後 task 2 をコミットして HEAD を進める
        (core_wt / "plans" / "core.md").write_text(
            "# PLAN (core)\n\n## ステップ\n- [ ] 1. Core one\n- [ ] 2. Core two\n",
            encoding="utf-8",
        )
        _git(core_wt, "add", "plans/core.md")
        _git(core_wt, "commit", "-m", "core two tasks")
        _complete_task_via_lowlevel_cli(core_wt, "1", "src/core/app.py", "VALUE = 10\n")
        self.assertEqual(_run_cli(core_wt, "bundle", "--job-id", f"{job_id}-core").returncode, 0)
        _complete_task_via_lowlevel_cli(core_wt, "2", "src/core/app.py", "VALUE = 20\n")
        c2_sha = _git(core_wt, "rev-parse", "HEAD").stdout.strip()

        # ui: 現在の HEAD で別ジョブ ID の bundle が既にある状態にする
        _complete_task_via_lowlevel_cli(ui_wt, "1", "src/ui/view.py", "VIEW = 30\n")
        self.assertEqual(_run_cli(ui_wt, "bundle", "--job-id", "other-job-ui").returncode, 0)

        res = _run_cli(self.repo_dir, "teams", "integrate", "--job-id", job_id)
        self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")

        core_zip = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "core.zip"
        ui_zip = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip"
        self.assertEqual(EvidenceBundler(self.repo_dir).verify_bundle(core_zip).head_commit, c2_sha)
        with zipfile.ZipFile(ui_zip, "r") as zf:
            ui_manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        self.assertEqual(ui_manifest["job_id"], f"{job_id}-ui")


if __name__ == "__main__":
    unittest.main()
