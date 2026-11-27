from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.verifier import RedTestVerifier


class TestSbx9SandboxGitEnv(unittest.TestCase):
    """SBX-9: 候補テストの隔離環境でホストの GIT_* を除き、HOME/XDG_CONFIG_HOME を一時空ディレクトリにして git が動くことを検証する。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name) / "parent_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True, capture_output=True)
        (self.repo_dir / "README.md").write_text("parent\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        self.verifier = RedTestVerifier(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_git_init_and_commit_succeed_with_host_git_config_and_git_dir(self) -> None:
        """SBX-9 受入: ホストに GIT_CONFIG_COUNT/KEY_0/VALUE_0 と GIT_DIR があってもサンドボックス内の git init/commit が成功し元リポジトリが不変なこと。"""
        before_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        candidate_code = """import subprocess
import tempfile
import unittest
from pathlib import Path

class SandboxGitTest(unittest.TestCase):
    def test_git_init_and_commit(self):
        with tempfile.TemporaryDirectory() as td:
            repo = Path(td) / "sub_repo"
            repo.mkdir()
            init_res = subprocess.run(
                ["git", "init", "-b", "main"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(init_res.returncode, 0, f"git init failed: {init_res.stderr}")
            (repo / "sample.txt").write_text("hello\\n", encoding="utf-8")
            add_res = subprocess.run(["git", "add", "sample.txt"], cwd=repo, capture_output=True, text=True)
            self.assertEqual(add_res.returncode, 0, f"git add failed: {add_res.stderr}")
            commit_res = subprocess.run(
                ["git", "-c", "user.name=SandboxUser", "-c", "user.email=sandbox@example.com", "commit", "-m", "test"],
                cwd=repo,
                capture_output=True,
                text=True,
            )
            self.assertEqual(commit_res.returncode, 0, f"git commit failed: {commit_res.stderr}")
"""
        host_env_overrides = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "safe.directory",
            "GIT_CONFIG_VALUE_0": "*",
            "GIT_CONFIG_PARAMETERS": "'user.name=Host'",
            "GIT_DIR": str((self.repo_dir / ".git").resolve()),
            "GIT_WORK_TREE": str(self.repo_dir.resolve()),
            "GIT_INDEX_FILE": str((self.repo_dir / ".git" / "index").resolve()),
            "GIT_CONFIG_NOSYSTEM": "0",
        }
        with patch.dict(os.environ, host_env_overrides):
            result = self.verifier.verify_candidate_test(candidate_code)

        self.assertEqual(result.reason, "ALREADY_GREEN", f"Expected ALREADY_GREEN, got {result.reason}: {result.output}")
        self.assertEqual(result.exit_code, 0)

        after_head = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        after_status = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        self.assertEqual(before_head, after_head)
        self.assertEqual(after_status, "")

    def test_sanitized_env_isolates_home_xdg_and_strips_git_vars(self) -> None:
        """SBX-9: 候補テストの環境で GIT_* が除かれ、HOME/XDG_CONFIG_HOME が一時領域の空ディレクトリになり、GIT_CONFIG_NOSYSTEM=1 が渡ること。"""
        host_home = str(Path.home().resolve())
        candidate_code = f"""import os
import unittest
from pathlib import Path

class EnvIsolationTest(unittest.TestCase):
    def test_env(self):
        for k in os.environ:
            if k.startswith("GIT_"):
                self.assertEqual(k, "GIT_CONFIG_NOSYSTEM", f"Unexpected GIT_* env var: {{k}}")
        self.assertEqual(os.environ.get("GIT_CONFIG_NOSYSTEM"), "1")
        home = Path(os.environ["HOME"])
        xdg = Path(os.environ["XDG_CONFIG_HOME"])
        self.assertNotEqual(str(home.resolve()), {host_home!r})
        self.assertTrue(home.is_dir())
        self.assertTrue(xdg.is_dir())
        self.assertEqual(list(home.iterdir()), [])
        self.assertEqual(list(xdg.iterdir()), [])
"""
        host_env_overrides = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "user.name",
            "GIT_CONFIG_VALUE_0": "Host",
            "GIT_DIR": "/tmp/fake_git_dir",
            "XDG_CONFIG_HOME": "/tmp/host_xdg_config",
        }
        with patch.dict(os.environ, host_env_overrides):
            result = self.verifier.verify_candidate_test(candidate_code)

        self.assertEqual(result.reason, "ALREADY_GREEN", f"Expected ALREADY_GREEN, got {result.reason}: {result.output}")
        self.assertEqual(result.exit_code, 0)

    def test_bwrap_command_mounts_sandbox_home_and_passes_sanitized_env(self) -> None:
        """SBX-9: Linux bwrap コマンド構築でも HOME/XDG_CONFIG_HOME の一時ディレクトリがマウントされ、GIT_CONFIG_NOSYSTEM=1 等が渡ること。"""
        captured_cmd: list[str] = []
        captured_env: dict[str, str] = {}
        orig_popen = subprocess.Popen

        def capturing_popen(*args: Any, **kwargs: Any) -> Any:
            nonlocal captured_cmd, captured_env
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and "bwrap" in str(cmd[0]):
                captured_cmd = list(cmd)
                captured_env = dict(kwargs.get("env") or {})
                mock_p = MagicMock()
                mock_p.pid = 99999
                mock_p.returncode = 0
                mock_p.communicate.return_value = (
                    '===AUTO_DEV_RUNNER_STARTED===\n===AUTO_DEV_TEST_RESULT===\n{"tests_run": 1, "failures": [], "errors": [], "executed_product_code": true}\n',
                    "",
                )
                return mock_p
            return orig_popen(*args, **kwargs)

        code = """import unittest
class PassTest(unittest.TestCase):
    def test_ok(self):
        self.assertEqual(1, 1)
"""
        host_env_overrides = {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "user.name",
            "GIT_CONFIG_VALUE_0": "Host",
            "GIT_DIR": str((self.repo_dir / ".git").resolve()),
        }
        with patch.dict(os.environ, host_env_overrides), \
             patch.object(self.verifier, "_get_sandbox_backend", return_value="bwrap"), \
             patch("subprocess.Popen", side_effect=capturing_popen):
            result = self.verifier.verify_candidate_test(code)

        self.assertEqual(result.reason, "ALREADY_GREEN")
        self.assertTrue(captured_cmd)
        self.assertEqual(captured_env.get("GIT_CONFIG_NOSYSTEM"), "1")
        git_keys = [k for k in captured_env if k.startswith("GIT_")]
        self.assertEqual(git_keys, ["GIT_CONFIG_NOSYSTEM"])

        home_dir = captured_env.get("HOME", "")
        xdg_dir = captured_env.get("XDG_CONFIG_HOME", "")
        self.assertTrue(home_dir)
        self.assertTrue(xdg_dir)
        self.assertNotEqual(home_dir, str(Path.home().resolve()))

        # --tmpfs /tmp の後に HOME / XDG_CONFIG_HOME（またはその親）が --dir / --bind されていること
        tmpfs_idx = captured_cmd.index("--tmpfs")
        bind_targets: list[str] = []
        for i, token in enumerate(captured_cmd):
            if token == "--bind" and i + 1 < len(captured_cmd) and i > tmpfs_idx:
                bind_targets.append(captured_cmd[i + 1])

        self.assertTrue(
            any(home_dir == t or Path(home_dir).is_relative_to(Path(t)) for t in bind_targets),
            f"HOME {home_dir} must be bound in bwrap after --tmpfs /tmp. bind_targets={bind_targets}",
        )
        self.assertTrue(
            any(xdg_dir == t or Path(xdg_dir).is_relative_to(Path(t)) for t in bind_targets),
            f"XDG_CONFIG_HOME {xdg_dir} must be bound in bwrap after --tmpfs /tmp. bind_targets={bind_targets}",
        )


if __name__ == "__main__":
    unittest.main()
