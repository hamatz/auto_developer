from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.cli import main
from auto_developer.manifest import load_teams_manifest


def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    env.pop("AUTO_DEV_JOB_ID", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return env


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _run_cli_subprocess(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(work_dir), *args],
        cwd=work_dir,
        env=_clean_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


class TestTeamsInitBaseRef(unittest.TestCase):
    """TEAM-25: teams init が base.ref に現在のブランチ名を書く受入テスト。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name) / "repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_teams_init_uses_current_committed_branch_and_prepare_succeeds(self) -> None:
        _git(self.repo_dir, "init", "-b", "develop")
        _git(self.repo_dir, "config", "user.name", "Tester")
        _git(self.repo_dir, "config", "user.email", "tester@example.com")
        (self.repo_dir / "README.md").write_text("# demo\n", encoding="utf-8")
        _git(self.repo_dir, "add", "README.md")
        _git(self.repo_dir, "commit", "-m", "initial commit")

        proc = _run_cli_subprocess(self.repo_dir, "teams", "init", "--teams", "core,ui")
        self.assertEqual(proc.returncode, 0, f"stdout={proc.stdout}\nstderr={proc.stderr}")

        raw = json.loads((self.repo_dir / "teams.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["base"]["ref"], "develop")

        manifest = load_teams_manifest(self.repo_dir)
        self.assertEqual(manifest.base_ref, "develop")

        _git(self.repo_dir, "add", "-A")
        _git(self.repo_dir, "commit", "-m", "add teams config")

        prep_proc = _run_cli_subprocess(self.repo_dir, "teams", "prepare", "--job-id", "job-develop")
        self.assertEqual(
            prep_proc.returncode,
            0,
            f"teams prepare failed on develop branch: stdout={prep_proc.stdout}\nstderr={prep_proc.stderr}",
        )

    def test_teams_init_uses_unborn_branch_when_no_commits_exist(self) -> None:
        for branch_name in ("master", "feature/init-test"):
            sub_repo = Path(self.temp_dir.name) / f"unborn-{branch_name.replace('/', '_')}"
            sub_repo.mkdir(parents=True, exist_ok=True)
            _git(sub_repo, "init", "-b", branch_name)

            with self.subTest(branch=branch_name):
                stdout_buf = io.StringIO()
                stderr_buf = io.StringIO()
                with (
                    contextlib.redirect_stdout(stdout_buf),
                    contextlib.redirect_stderr(stderr_buf),
                ):
                    code = main(["--work-dir", str(sub_repo), "teams", "init", "--teams", "core,ui"])
                self.assertEqual(code, 0)
                raw = json.loads((sub_repo / "teams.json").read_text(encoding="utf-8"))
                self.assertEqual(raw["base"]["ref"], branch_name)

    def test_teams_init_detached_head_falls_back_to_main_with_notice(self) -> None:
        _git(self.repo_dir, "init", "-b", "develop")
        _git(self.repo_dir, "config", "user.name", "Tester")
        _git(self.repo_dir, "config", "user.email", "tester@example.com")
        (self.repo_dir / "README.md").write_text("# demo\n", encoding="utf-8")
        _git(self.repo_dir, "add", "README.md")
        _git(self.repo_dir, "commit", "-m", "initial commit")
        _git(self.repo_dir, "checkout", "--detach", "HEAD")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            code = main(["--work-dir", str(self.repo_dir), "teams", "init"])

        self.assertEqual(code, 0)
        raw = json.loads((self.repo_dir / "teams.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["base"]["ref"], "main")
        combined = stdout_buf.getvalue() + "\n" + stderr_buf.getvalue()
        self.assertIn("main", combined)
        self.assertRegex(combined.lower(), r"branch|detached|fallback|default")

    def test_teams_init_non_git_dir_falls_back_to_main_with_notice(self) -> None:
        non_git_dir = Path(self.temp_dir.name) / "non_git"
        non_git_dir.mkdir(parents=True, exist_ok=True)

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            code = main(["--work-dir", str(non_git_dir), "teams", "init"])

        self.assertEqual(code, 0)
        raw = json.loads((non_git_dir / "teams.json").read_text(encoding="utf-8"))
        self.assertEqual(raw["base"]["ref"], "main")
        combined = stdout_buf.getvalue() + "\n" + stderr_buf.getvalue()
        self.assertIn("main", combined)
        self.assertRegex(combined.lower(), r"branch|fallback|default")

    def test_teams_init_does_not_overwrite_existing_teams_json_base_ref(self) -> None:
        _git(self.repo_dir, "init", "-b", "develop")
        existing_payload = {
            "version": 1,
            "project": "custom",
            "base": {"ref": "custom-base", "require_clean": True},
            "teams": [
                {"id": "core", "owns": ["src/core/**"]},
                {"id": "ui", "owns": ["src/ui/**"]},
            ],
        }
        existing_text = json.dumps(existing_payload, indent=2)
        (self.repo_dir / "teams.json").write_text(existing_text, encoding="utf-8")

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            code = main(["--work-dir", str(self.repo_dir), "teams", "init"])

        self.assertEqual(code, 0)
        self.assertEqual((self.repo_dir / "teams.json").read_text(encoding="utf-8"), existing_text)
        self.assertEqual(stderr_buf.getvalue(), "")


if __name__ == "__main__":
    unittest.main()
