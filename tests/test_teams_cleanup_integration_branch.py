from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.integration_gate import IntegrationJobState


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
    return [line.lstrip("+* ").strip() for line in out.splitlines() if line.strip()]


def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.pop("AUTO_DEV_JOB_ID", None)
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


def _init_two_team_repo(repo_dir: Path) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    init_proc = _run_cli(repo_dir, "init")
    if init_proc.returncode != 0:
        raise AssertionError(f"init failed: {init_proc.stdout}\n{init_proc.stderr}")

    teams_init_proc = _run_cli(repo_dir, "teams", "init", "--teams", "core,ui")
    if teams_init_proc.returncode != 0:
        raise AssertionError(
            f"teams init failed: {teams_init_proc.stdout}\n{teams_init_proc.stderr}"
        )

    (repo_dir / "scripts" / "verify_local.sh").write_text(
        "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
    )
    (repo_dir / "scripts" / "verify_local.sh").chmod(0o755)

    for tid in ("core", "ui"):
        (repo_dir / "plans" / f"{tid}.md").write_text(
            f"# PLAN ({tid})\n\n## ステップ\n- [ ] 1. {tid} task\n",
            encoding="utf-8",
        )

    (repo_dir / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core" / "app.py").write_text("CORE = 1\n", encoding="utf-8")
    (repo_dir / "src" / "ui" / "view.py").write_text("UI = 1\n", encoding="utf-8")

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "initial two-team setup")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def _write_valid_zip_stub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bundle_manifest.json", "{}")


class TestTeamsCleanupIntegrationBranch(unittest.TestCase):
    """TEAM-26: 統合に成功したジョブの teams cleanup で integration/<job_id> を削除し、RC タグは残す。"""

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

    def _run_successful_teams_job(self, job_id: str) -> None:
        maker_script = self.scripts_dir / "mock_maker.py"
        maker_script.write_text(
            "from pathlib import Path\n"
            "cwd = Path.cwd()\n"
            "if cwd.name == 'core':\n"
            "    (cwd / 'src' / 'core' / 'feature.py').write_text('CORE_FEAT = 1\\n', encoding='utf-8')\n"
            "elif cwd.name == 'ui':\n"
            "    (cwd / 'src' / 'ui' / 'feature.py').write_text('UI_FEAT = 1\\n', encoding='utf-8')\n",
            encoding="utf-8",
        )
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

    def _prepare_completed_job_manually(self, job_id: str) -> str:
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        (core_wt / "src" / "core" / "app.py").write_text("CORE = 2\n", encoding="utf-8")
        _git(core_wt, "add", "-A")
        _git(core_wt, "commit", "-m", "core work")
        core_sha = _git(core_wt, "rev-parse", "HEAD").stdout.strip()

        (ui_wt / "src" / "ui" / "view.py").write_text("UI = 2\n", encoding="utf-8")
        _git(ui_wt, "add", "-A")
        _git(ui_wt, "commit", "-m", "ui work")
        ui_sha = _git(ui_wt, "rev-parse", "HEAD").stdout.strip()

        _git(self.repo_dir, "checkout", "-b", f"integration/{job_id}", "main")
        _git(self.repo_dir, "merge", "--no-ff", "team/core", "-m", "merge core")
        _git(self.repo_dir, "merge", "--no-ff", "team/ui", "-m", "merge ui")
        rc_sha = _git(self.repo_dir, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo_dir, "tag", f"rc/{job_id}", rc_sha)
        _git(self.repo_dir, "checkout", "main")

        for tid in ("core", "ui"):
            _write_valid_zip_stub(
                self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / f"{tid}.zip"
            )

        IntegrationJobState(
            job_id=job_id,
            state="COMPLETED",
            base_commit="main",
            integration_branch=f"integration/{job_id}",
            verified_commits={"core": core_sha, "ui": ui_sha},
            merged_teams=["core", "ui"],
            rc_commit=rc_sha,
            rc_tag=f"rc/{job_id}",
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        return rc_sha

    def test_e2e_cleanup_completed_job_deletes_integration_branch_and_keeps_rc_tag(self) -> None:
        job_id = "job-t26-ok"
        self._run_successful_teams_job(job_id)

        int_branch = f"integration/{job_id}"
        rc_tag = f"rc/{job_id}"
        self.assertEqual(_list_branches(self.repo_dir, int_branch), [int_branch])
        rc_sha_before = _git(self.repo_dir, "rev-parse", f"refs/tags/{rc_tag}").stdout.strip()

        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, f"stdout={cln.stdout}\nstderr={cln.stderr}")

        # 統合ブランチ・チームブランチ・worktree は削除される
        self.assertEqual(_list_branches(self.repo_dir, int_branch), [])
        self.assertEqual(_list_branches(self.repo_dir, "team/*"), [])
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())
        self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "ui").exists())

        # RC タグと隔離ブランチ状態は維持される
        self.assertEqual(_git(self.repo_dir, "tag", "--list", rc_tag).stdout.strip(), rc_tag)
        self.assertEqual(_git(self.repo_dir, "rev-parse", f"refs/tags/{rc_tag}").stdout.strip(), rc_sha_before)
        self.assertEqual(_list_branches(self.repo_dir, "quarantine/*"), [])

        # 次のジョブをそのまま開始できる
        prep2 = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-t26-next")
        self.assertEqual(prep2.returncode, 0, prep2.stderr)

    def test_cleanup_completed_job_handles_leftover_clean_and_dirty_integration_worktree(self) -> None:
        # 1. クリーンな統合 worktree が残っている COMPLETED ジョブ
        job_clean = "job-t26-intwt-clean"
        rc_sha = self._prepare_completed_job_manually(job_clean)
        int_wt = self.repo_dir / ".dev" / "worktrees" / f"int-{job_clean}"
        _git(self.repo_dir, "worktree", "add", str(int_wt), f"integration/{job_clean}")

        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_clean)
        self.assertEqual(cln.returncode, 0, f"stdout={cln.stdout}\nstderr={cln.stderr}")
        self.assertFalse(int_wt.exists())
        self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_clean}"), [])
        self.assertEqual(_git(self.repo_dir, "rev-parse", f"refs/tags/rc/{job_clean}").stdout.strip(), rc_sha)

        # 2. 未コミット変更のある統合 worktree が残っている場合は --force なしで拒否し、--force で退避後に削除する
        job_dirty = "job-t26-intwt-dirty"
        rc_sha_dirty = self._prepare_completed_job_manually(job_dirty)
        int_wt_dirty = self.repo_dir / ".dev" / "worktrees" / f"int-{job_dirty}"
        _git(self.repo_dir, "worktree", "add", str(int_wt_dirty), f"integration/{job_dirty}")
        (int_wt_dirty / "uncommitted_int_note.txt").write_text("keep-me-in-quarantine\n", encoding="utf-8")

        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_dirty)
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(int_wt_dirty.exists())
        self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_dirty}"), [f"integration/{job_dirty}"])

        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_dirty, "--force")
        self.assertEqual(forced.returncode, 0, f"stdout={forced.stdout}\nstderr={forced.stderr}")
        self.assertFalse(int_wt_dirty.exists())
        self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_dirty}"), [])
        self.assertEqual(_git(self.repo_dir, "rev-parse", f"refs/tags/rc/{job_dirty}").stdout.strip(), rc_sha_dirty)
        snapshots = list((self.repo_dir / ".dev" / "quarantines").glob(f"snapshot_worktree_int-{job_dirty}_*.tar.gz"))
        self.assertTrue(snapshots, "Expected quarantine snapshot for dirty integration worktree")

    def test_cleanup_completed_job_protects_unmerged_commits_on_integration_branch(self) -> None:
        job_id = "job-t26-extra"
        rc_sha = self._prepare_completed_job_manually(job_id)

        # RC タグ後に手動で integration/<job_id> へ追加コミットが入った場合
        _git(self.repo_dir, "checkout", f"integration/{job_id}")
        (self.repo_dir / "post_rc_manual.txt").write_text("manual post-rc work\n", encoding="utf-8")
        _git(self.repo_dir, "add", "post_rc_manual.txt")
        _git(self.repo_dir, "commit", "-m", "post-rc manual commit")
        extra_sha = _git(self.repo_dir, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo_dir, "checkout", "main")

        # --force なしでは保護のため拒否される
        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_id}"), [f"integration/{job_id}"])

        # --force 時は INV-3 準拠で退避してから統合ブランチを削除し、RC タグは残す
        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(forced.returncode, 0, f"stdout={forced.stdout}\nstderr={forced.stderr}")
        self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_id}"), [])
        self.assertEqual(_git(self.repo_dir, "rev-parse", f"refs/tags/rc/{job_id}").stdout.strip(), rc_sha)
        q_branches = _list_branches(self.repo_dir, f"quarantine/{job_id}/integration/*")
        self.assertTrue(q_branches, "Expected quarantine branch for post-RC commits on integration branch")
        self.assertEqual(_git(self.repo_dir, "rev-parse", q_branches[-1]).stdout.strip(), extra_sha)

    def test_cleanup_failed_job_preserves_integration_branch_even_with_force(self) -> None:
        job_id = "job-t26-failed"
        prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
        self.assertEqual(prep.returncode, 0, prep.stderr)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        (core_wt / "src" / "core" / "app.py").write_text("CORE = 9\n", encoding="utf-8")
        _git(core_wt, "add", "-A")
        _git(core_wt, "commit", "-m", "core failed attempt")

        _git(self.repo_dir, "checkout", "-b", f"integration/{job_id}", "main")
        _git(self.repo_dir, "merge", "--no-ff", "team/core", "-m", "partial integration")
        failed_int_sha = _git(self.repo_dir, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo_dir, "checkout", "main")

        IntegrationJobState(
            job_id=job_id,
            state="FAILED",
            base_commit="main",
            integration_branch=f"integration/{job_id}",
        ).save(self.repo_dir / ".dev" / "integration_jobs")

        # --force なしは拒否
        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_id}"), [f"integration/{job_id}"])

        # --force でも失敗ジョブの integration/<job_id> は削除しない
        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(forced.returncode, 0, f"stdout={forced.stdout}\nstderr={forced.stderr}")
        self.assertFalse(core_wt.exists())
        self.assertEqual(_list_branches(self.repo_dir, "team/*"), [])
        self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_id}"), [f"integration/{job_id}"])
        self.assertEqual(
            _git(self.repo_dir, "rev-parse", f"refs/heads/integration/{job_id}").stdout.strip(),
            failed_int_sha,
        )

        # 失敗ジョブの後片付け後も次の prepare は通る
        prep2 = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-t26-after-fail")
        self.assertEqual(prep2.returncode, 0, prep2.stderr)

    def test_cleanup_uncompleted_job_preserves_integration_branch_even_with_force(self) -> None:
        for state_name in ("NO_STATE", "MERGING"):
            with self.subTest(state=state_name):
                job_id = f"job-t26-uncomp-{state_name.lower().replace('_', '-')}"
                prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
                self.assertEqual(prep.returncode, 0, prep.stderr)

                _git(self.repo_dir, "checkout", "-b", f"integration/{job_id}", "main")
                (self.repo_dir / "wip_int.txt").write_text("wip\n", encoding="utf-8")
                _git(self.repo_dir, "add", "wip_int.txt")
                _git(self.repo_dir, "commit", "-m", "wip integration")
                int_sha = _git(self.repo_dir, "rev-parse", "HEAD").stdout.strip()
                _git(self.repo_dir, "checkout", "main")

                if state_name != "NO_STATE":
                    IntegrationJobState(
                        job_id=job_id,
                        state=state_name,
                        base_commit="main",
                        integration_branch=f"integration/{job_id}",
                    ).save(self.repo_dir / ".dev" / "integration_jobs")

                refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
                self.assertNotEqual(refused.returncode, 0)
                self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_id}"), [f"integration/{job_id}"])

                forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
                self.assertEqual(forced.returncode, 0, f"stdout={forced.stdout}\nstderr={forced.stderr}")
                self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_id}"), [f"integration/{job_id}"])
                self.assertEqual(
                    _git(self.repo_dir, "rev-parse", f"refs/heads/integration/{job_id}").stdout.strip(),
                    int_sha,
                )

    def test_cleanup_does_not_delete_other_jobs_integration_branch_and_is_idempotent(self) -> None:
        job_id = "job-t26-scoped"
        rc_sha = self._prepare_completed_job_manually(job_id)
        _git(self.repo_dir, "branch", "integration/job-other", "main")

        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, f"stdout={cln.stdout}\nstderr={cln.stderr}")
        self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_id}"), [])
        self.assertEqual(_list_branches(self.repo_dir, "integration/job-other"), ["integration/job-other"])
        self.assertEqual(_git(self.repo_dir, "rev-parse", f"refs/tags/rc/{job_id}").stdout.strip(), rc_sha)

        # すでに integration/<job_id> がない状態での再実行も成功する
        cln_again = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln_again.returncode, 0, f"stdout={cln_again.stdout}\nstderr={cln_again.stderr}")
        self.assertEqual(_list_branches(self.repo_dir, "integration/job-other"), ["integration/job-other"])

    def test_cleanup_failed_or_uncompleted_job_preserves_integration_worktree_and_branch_even_with_force(self) -> None:
        for state_name in ("FAILED", "MERGING"):
            with self.subTest(state=state_name):
                job_id = f"job-t26-keep-intwt-{state_name.lower()}"
                prep = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", job_id)
                self.assertEqual(prep.returncode, 0, prep.stderr)

                int_wt = self.repo_dir / ".dev" / "worktrees" / f"int-{job_id}"
                _git(self.repo_dir, "worktree", "add", "-b", f"integration/{job_id}", str(int_wt), "main")
                (int_wt / "wip_int_file.txt").write_text("uncommitted integration work\n", encoding="utf-8")

                IntegrationJobState(
                    job_id=job_id,
                    state=state_name,
                    base_commit="main",
                    integration_branch=f"integration/{job_id}",
                    worktree_path=str(int_wt),
                ).save(self.repo_dir / ".dev" / "integration_jobs")

                forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
                self.assertEqual(forced.returncode, 0, f"stdout={forced.stdout}\nstderr={forced.stderr}")

                # チーム worktree とチームブランチは片付けられる一方、統合 worktree と統合ブランチは残る
                self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "core").exists())
                self.assertFalse((self.repo_dir / ".dev" / "worktrees" / "ui").exists())
                self.assertEqual(_list_branches(self.repo_dir, "team/*"), [])
                self.assertTrue(int_wt.exists())
                self.assertEqual((int_wt / "wip_int_file.txt").read_text(encoding="utf-8"), "uncommitted integration work\n")
                self.assertEqual(_list_branches(self.repo_dir, f"integration/{job_id}"), [f"integration/{job_id}"])

                # 次の subTest に備え、手動で統合 worktree/ブランチを片付ける
                _git(self.repo_dir, "worktree", "remove", "--force", str(int_wt))
                _git(self.repo_dir, "branch", "-D", f"integration/{job_id}")


if __name__ == "__main__":
    unittest.main()
