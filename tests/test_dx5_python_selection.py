from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

from auto_developer.cli import DEFAULT_VERIFY_SH, main
from auto_developer.multi_team import MultiTeamConfig, MultiTeamOrchestrator, TeamWorkerSpec

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo_with_cli(repo_dir: Path) -> None:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "DX5 Test")
    _git(repo_dir, "config", "user.email", "dx5@example.com")
    rc = main(["--work-dir", str(repo_dir), "init"])
    if rc != 0:
        raise AssertionError(f"auto-dev init failed with {rc}")


class TestDX5PythonSelectionAndSrcPath(unittest.TestCase):
    """DX-5: 雛形検証スクリプトの Python 選択・src/ PYTHONPATH・ワーカー起動の検証。"""

    def test_default_verify_sh_prefers_path_python3_over_usr_bin(self) -> None:
        """PYTHON 未設定時は /usr/bin/python3 を優先せず PATH の python3 を使うこと。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            repo = base / "repo"
            repo.mkdir()
            _init_repo_with_cli(repo)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "init")

            marker = base / "used_path_python3.txt"
            fake_bin = base / "bin"
            fake_bin.mkdir()
            fake_py = fake_bin / "python3"
            fake_py.write_text(
                f"#!/usr/bin/env bash\necho ok > '{marker}'\nexec '{sys.executable}' \"$@\"\n",
                encoding="utf-8",
            )
            fake_py.chmod(0o755)

            env = os.environ.copy()
            env.pop("PYTHON", None)
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

            res = subprocess.run(
                ["bash", str(repo / "scripts" / "verify_local.sh")],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")
            self.assertTrue(
                marker.exists(),
                "verify_local.sh did not use PATH's python3",
            )

    def test_default_verify_sh_uses_python_env_var_when_set(self) -> None:
        """環境変数 PYTHON が設定されていれば PATH の python3 より優先すること。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            repo = base / "repo"
            repo.mkdir()
            _init_repo_with_cli(repo)
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "init")

            path_marker = base / "used_path_python3.txt"
            fake_bin = base / "bin"
            fake_bin.mkdir()
            fake_py = fake_bin / "python3"
            fake_py.write_text(
                f"#!/usr/bin/env bash\necho path > '{path_marker}'\nexec '{sys.executable}' \"$@\"\n",
                encoding="utf-8",
            )
            fake_py.chmod(0o755)

            env_marker = base / "used_env_python.txt"
            custom_py = base / "custom_python"
            custom_py.write_text(
                f"#!/usr/bin/env bash\necho env > '{env_marker}'\nexec '{sys.executable}' \"$@\"\n",
                encoding="utf-8",
            )
            custom_py.chmod(0o755)

            env = os.environ.copy()
            env["PYTHON"] = str(custom_py)
            env["PATH"] = f"{fake_bin}:{env.get('PATH', '')}"

            res = subprocess.run(
                ["bash", str(repo / "scripts" / "verify_local.sh")],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")
            self.assertTrue(env_marker.exists(), "verify_local.sh did not use $PYTHON")
            self.assertFalse(path_marker.exists(), "verify_local.sh should not fall back to PATH python3 when $PYTHON is set")

    def test_default_verify_sh_adds_src_to_pythonpath_preserving_existing(self) -> None:
        """src/ ディレクトリがあれば既存の PYTHONPATH を壊さずに先頭へ追加すること。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            repo = base / "repo"
            repo.mkdir()
            _init_repo_with_cli(repo)

            pkg_dir = repo / "src" / "sample_pkg"
            pkg_dir.mkdir(parents=True)
            (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
            (pkg_dir / "calc.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")

            extra_dir = base / "extra_lib"
            extra_dir.mkdir()
            (extra_dir / "extra_mod.py").write_text("VALUE = 42\n", encoding="utf-8")

            (repo / "tests" / "test_src_import.py").write_text(
                "import unittest\n"
                "import extra_mod\n"
                "from sample_pkg.calc import add\n\n"
                "class SrcImportTest(unittest.TestCase):\n"
                "    def test_imports(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n"
                "        self.assertEqual(extra_mod.VALUE, 42)\n",
                encoding="utf-8",
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "add src package and test")

            env = os.environ.copy()
            env["PYTHON"] = sys.executable
            env["PYTHONPATH"] = str(extra_dir)

            res = subprocess.run(
                ["bash", str(repo / "scripts" / "verify_local.sh")],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")

    def test_default_verify_sh_adds_src_when_pythonpath_unset(self) -> None:
        """PYTHONPATH 未設定でも src/ があれば import できること。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            base = Path(tmp_dir)
            repo = base / "repo"
            repo.mkdir()
            _init_repo_with_cli(repo)

            pkg_dir = repo / "src" / "sample_pkg"
            pkg_dir.mkdir(parents=True)
            (pkg_dir / "__init__.py").write_text("", encoding="utf-8")
            (pkg_dir / "calc.py").write_text("def mul(a: int, b: int) -> int:\n    return a * b\n", encoding="utf-8")

            (repo / "tests" / "test_src_only.py").write_text(
                "import unittest\n"
                "from sample_pkg.calc import mul\n\n"
                "class SrcOnlyTest(unittest.TestCase):\n"
                "    def test_mul(self):\n"
                "        self.assertEqual(mul(3, 4), 12)\n",
                encoding="utf-8",
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "add src package")

            env = os.environ.copy()
            env["PYTHON"] = sys.executable
            env.pop("PYTHONPATH", None)

            res = subprocess.run(
                ["bash", str(repo / "scripts" / "verify_local.sh")],
                cwd=repo,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")

    def test_default_verify_sh_preserves_required_invariants(self) -> None:
        """雛形の区切り文字列・|| true 禁止・teams.json 保護を維持すること。"""
        self.assertIn("Running Project Tests", DEFAULT_VERIFY_SH)
        test_section = DEFAULT_VERIFY_SH.split("Running Project Tests", 1)[1]
        self.assertNotIn("|| true", test_section)
        self.assertIn("teams.json", DEFAULT_VERIFY_SH)

    def test_multi_team_worker_default_cmd_uses_sys_executable(self) -> None:
        """MultiTeamOrchestrator の既定ワーカーコマンドが sys.executable を使うこと。"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            repo = Path(tmp_dir)
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.name", "DX5 Test")
            _git(repo, "config", "user.email", "dx5@example.com")
            (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "init")

            orchestrator = MultiTeamOrchestrator(repo_dir=repo)
            config = MultiTeamConfig(
                job_id="job-dx5-sys-exe",
                teams=[TeamWorkerSpec(team_id="team-a", extra_args=["--mock"])],
                base_branch="main",
            )
            orchestrator.prepare_workers(config)

            captured_cmds: list[list[str]] = []

            def fake_run(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
                captured_cmds.append(list(cmd))
                if cmd == ["git", "rev-parse", "HEAD"]:
                    return subprocess.CompletedProcess(cmd, returncode=0, stdout="deadbeef\n", stderr="")
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="ok\n", stderr="")

            with unittest.mock.patch("subprocess.run", side_effect=fake_run):
                results = orchestrator.run_workers(config)

            self.assertTrue(results["team-a"].success)
            cli_cmds = [c for c in captured_cmds if "auto_developer.cli" in c]
            self.assertEqual(len(cli_cmds), 1)
            self.assertEqual(cli_cmds[0][0], sys.executable)
            self.assertEqual(cli_cmds[0][1:3], ["-m", "auto_developer.cli"])
            orchestrator.cleanup_workers(config)
