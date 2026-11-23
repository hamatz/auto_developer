from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
import zipfile
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.integration_gate import IntegrationJobState
from auto_developer.worktree import WorktreeError, WorktreeManager


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
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.pop("AUTO_DEV_JOB_ID", None)
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


def _create_mock_maker_script(script_path: Path) -> None:
    script_path.write_text(
        "from pathlib import Path\n"
        "cwd = Path.cwd()\n"
        "if cwd.name == 'core':\n"
        "    (cwd / 'src' / 'core' / 'feature.py').write_text('CORE_FEAT = 1\\n', encoding='utf-8')\n"
        "elif cwd.name == 'ui':\n"
        "    (cwd / 'src' / 'ui' / 'feature.py').write_text('UI_FEAT = 1\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )


def _populate_ignored_artifacts_and_caches(wt_dir: Path) -> None:
    artifacts = [
        wt_dir / "NIGHTLY_REPORT.md",
        wt_dir / ".dev" / "job_state.json",
        wt_dir / ".dev" / "bundles" / "bundle_stub.zip",
        wt_dir / "logs" / "nightly" / "task_1_failure.log",
        wt_dir / "src" / "core" / "__pycache__" / "app.cpython-311.pyc",
        wt_dir / "src" / "core" / "cached.pyc",
        wt_dir / "src" / "core" / "cached.pyo",
        wt_dir / "src" / "core" / "cached.pyd",
        wt_dir / ".pytest_cache" / "v" / "cache" / "nodeids",
        wt_dir / ".mypy_cache" / "3.11" / "app.meta.json",
        wt_dir / ".ruff_cache" / "0.1.0" / "cache",
    ]
    for path in artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("generated\n", encoding="utf-8")


def _write_valid_zip_stub(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("bundle_manifest.json", "{}")


class TestTeamsCleanupArtifacts(unittest.TestCase):
    """TEAM-22: 成功したジョブの teams cleanup で Auto Developer 生成物とキャッシュ類を保全対象外にし、Bundle 存在を検証する。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_tmp = Path(self.tmp_dir.name)
        self.repo_dir = self.base_tmp / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        self.scripts_dir = self.base_tmp / "scripts"
        self.scripts_dir.mkdir(parents=True, exist_ok=True)
        self.base_commit = _init_two_team_repo(self.repo_dir)
        self.maker_script = self.scripts_dir / "mock_maker.py"
        _create_mock_maker_script(self.maker_script)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _run_successful_teams_job(self, job_id: str) -> None:
        proc = _run_cli(
            self.repo_dir,
            "teams",
            "run",
            "--job-id",
            job_id,
            "--maker-cmd",
            f"{sys.executable} {self.maker_script}",
            "--checker-cmd",
            "printf 'DECISION: APPROVE\\n'",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5.5",
        )
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")

    def _prepare_completed_job_with_bundles(self, job_id: str) -> tuple[Path, Path]:
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

        return core_wt, ui_wt

    def test_cleanup_without_force_succeeds_with_autodev_artifacts_and_caches(self) -> None:
        job_id = "job-cleanup-ok"
        self._run_successful_teams_job(job_id)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _populate_ignored_artifacts_and_caches(core_wt)
        _populate_ignored_artifacts_and_caches(ui_wt)

        cln = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertEqual(cln.returncode, 0, f"stdout={cln.stdout}\nstderr={cln.stderr}")

        self.assertFalse(core_wt.exists())
        self.assertFalse(ui_wt.exists())
        self.assertEqual(_git(self.repo_dir, "branch", "--list", "team/*").stdout.strip(), "")
        self.assertEqual(_git(self.repo_dir, "branch", "--list", "quarantine/*").stdout.strip(), "")

        # Preserved bundles and RC tag must remain
        self.assertEqual(_git(self.repo_dir, "tag", "--list", f"rc/{job_id}").stdout.strip(), f"rc/{job_id}")
        self.assertTrue(
            (self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "core.zip").is_file()
        )
        self.assertTrue(
            (self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip").is_file()
        )

        # Next prepare should succeed cleanly
        prep2 = _run_cli(self.repo_dir, "teams", "prepare", "--job-id", "job-cleanup-next")
        self.assertEqual(prep2.returncode, 0, prep2.stderr)

    def test_cleanup_without_force_refuses_when_team_bundle_missing_or_invalid(self) -> None:
        job_id = "job-missing-bundle"
        self._run_successful_teams_job(job_id)

        ui_bundle = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip"
        ui_bundle.unlink()

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"

        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--force", refused.stderr)
        self.assertTrue(core_wt.exists())
        self.assertTrue(ui_wt.exists())
        self.assertIn("team/core", _git(self.repo_dir, "branch", "--list", "team/core").stdout)
        self.assertIn("team/ui", _git(self.repo_dir, "branch", "--list", "team/ui").stdout)

        # Invalid/empty zip must also be refused without --force
        ui_bundle.write_bytes(b"not-a-zip")
        refused_corrupt = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused_corrupt.returncode, 0)
        self.assertTrue(ui_wt.exists())

    def test_cleanup_without_force_refuses_missing_bundle_even_with_clean_worktrees(self) -> None:
        job_id = "job-clean-missing-bundle"
        core_wt, ui_wt = self._prepare_completed_job_with_bundles(job_id)
        ui_bundle = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "ui.zip"
        ui_bundle.unlink()

        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--force", refused.stderr)
        self.assertTrue(core_wt.exists())
        self.assertTrue(ui_wt.exists())
        self.assertIn("team/core", _git(self.repo_dir, "branch", "--list", "team/core").stdout)
        self.assertIn("team/ui", _git(self.repo_dir, "branch", "--list", "team/ui").stdout)

    def test_cleanup_without_force_refuses_tracked_modified_artifact_names(self) -> None:
        # Track logs/tracked.log and NIGHTLY_REPORT.md in main before job runs
        (self.repo_dir / "logs").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "logs" / "tracked.log").write_text("v1\n", encoding="utf-8")
        (self.repo_dir / "NIGHTLY_REPORT.md").write_text("v1\n", encoding="utf-8")
        _git(self.repo_dir, "add", "-f", "logs/tracked.log", "NIGHTLY_REPORT.md")
        _git(self.repo_dir, "commit", "-m", "track log and report")

        job_id = "job-tracked-artifact"
        core_wt, _ = self._prepare_completed_job_with_bundles(job_id)

        (core_wt / "logs" / "tracked.log").write_text("modified-uncommitted\n", encoding="utf-8")
        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(core_wt.exists())
        self.assertIn("team/core", _git(self.repo_dir, "branch", "--list", "team/core").stdout)

        # Restore logs/tracked.log and modify tracked NIGHTLY_REPORT.md instead
        _git(core_wt, "checkout", "--", "logs/tracked.log")
        (core_wt / "NIGHTLY_REPORT.md").write_text("modified-report\n", encoding="utf-8")
        refused2 = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused2.returncode, 0)
        self.assertTrue(core_wt.exists())

    def test_cleanup_without_force_refuses_non_ignored_untracked_artifact_names(self) -> None:
        # Remove NIGHTLY_REPORT.md and *.py[cod] from .gitignore
        (self.repo_dir / ".gitignore").write_text(".dev/\nlogs/\n", encoding="utf-8")
        _git(self.repo_dir, "add", ".gitignore")
        _git(self.repo_dir, "commit", "-m", "narrow gitignore")

        job_id = "job-unignored-artifact"
        core_wt, _ = self._prepare_completed_job_with_bundles(job_id)
        (core_wt / "NIGHTLY_REPORT.md").write_text("unignored report\n", encoding="utf-8")

        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(core_wt.exists())

    def test_cleanup_without_force_refuses_other_ignored_files_and_force_quarantines(self) -> None:
        with open(self.repo_dir / ".gitignore", "a", encoding="utf-8") as f:
            f.write("ignored_user_notes.txt\n")
        _git(self.repo_dir, "add", ".gitignore")
        _git(self.repo_dir, "commit", "-m", "ignore user notes")

        job_id = "job-other-ignored"
        self._run_successful_teams_job(job_id)

        core_wt = self.repo_dir / ".dev" / "worktrees" / "core"
        ui_wt = self.repo_dir / ".dev" / "worktrees" / "ui"
        _populate_ignored_artifacts_and_caches(core_wt)
        (ui_wt / "ignored_user_notes.txt").write_text("important user ignored note\n", encoding="utf-8")

        # Must refuse without --force and must not partially delete core either
        refused = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id)
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(core_wt.exists())
        self.assertTrue(ui_wt.exists())

        # With --force, quarantines user ignored file into .dev/quarantines snapshot before removing
        forced = _run_cli(self.repo_dir, "teams", "cleanup", "--job-id", job_id, "--force")
        self.assertEqual(forced.returncode, 0, f"stdout={forced.stdout}\nstderr={forced.stderr}")
        self.assertFalse(core_wt.exists())
        self.assertFalse(ui_wt.exists())

        snapshots = sorted(
            (self.repo_dir / ".dev" / "quarantines").glob("snapshot_worktree_ui_*.tar.gz")
        )
        self.assertTrue(snapshots, "Expected worktree quarantine snapshot for ui ignored file under --force")
        with tarfile.open(snapshots[-1], "r:gz") as tar:
            names = tar.getnames()
            self.assertTrue(any("ignored_user_notes.txt" in n for n in names))

    def test_worktree_manager_default_remove_still_rejects_ignored_artifacts(self) -> None:
        wt_manager = WorktreeManager(repo_dir=self.repo_dir)
        wt_path = wt_manager.create_worktree(team_id="core", base_branch="main", branch_name="team/core")
        (wt_path / ".dev").mkdir(parents=True, exist_ok=True)
        (wt_path / ".dev" / "job_state.json").write_text("{}\n", encoding="utf-8")

        with self.assertRaises(WorktreeError):
            wt_manager.remove_worktree("core", force=False)
        self.assertTrue(wt_path.exists())


if __name__ == "__main__":
    unittest.main()
