from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
tests_dir = str(Path(__file__).resolve().parent)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

from sandbox_test_helpers import require_platform_sandbox

from auto_developer.verifier import RedTestVerifier, RedVerificationResult


class TestLinuxBwrapIsolation(unittest.TestCase):
    """SBX-5: Linux の bwrap サンドボックス内で機密・非許可パス・ホーム・元リポジトリが読めないことを実実行で検証する。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name) / "parent_repo"
        self.repo_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.name", "TestUser"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        (self.repo_dir / ".gitignore").write_text(".env*\n", encoding="utf-8")
        src_path = self.repo_dir / "src"
        src_path.mkdir(parents=True, exist_ok=True)
        (src_path / "sample_module.py").write_text(
            "def compute_val(x: int) -> int:\n    return x * 2\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "init"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        self.verifier = RedTestVerifier(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _require_linux_bwrap(self) -> None:
        is_linux = sys.platform.startswith("linux")
        backend_available = is_linux and self.verifier._get_sandbox_backend() == "bwrap"
        unavailable_reason = (
            self.verifier._get_sandbox_unavailable_detail()
            if is_linux
            else "Linux bwrap sandbox is unavailable"
        )
        require_platform_sandbox(
            self,
            expected_platform="linux",
            backend_available=backend_available,
            platform_skip_reason="Linux bwrap sandbox isolation tests require Linux",
            unavailable_reason=unavailable_reason,
        )

    def _assert_read_blocked(
        self,
        result: RedVerificationResult,
        forbidden_secrets: list[str] | None = None,
    ) -> None:
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "UNHANDLED_EXCEPTION")
        self.assertTrue(
            "PermissionError" in result.output or "FileNotFoundError" in result.output,
            f"Expected PermissionError or FileNotFoundError in output: {result.output}",
        )
        for secret in forbidden_secrets or []:
            self.assertNotIn(secret, result.output)

    def test_bwrap_executes_genuine_red_in_isolated_clone(self) -> None:
        """SBX-5: Linux bwrap 内で通常の製品コードの失敗テストが GENUINE_RED として実行・受理されること。"""
        self._require_linux_bwrap()

        code = """import unittest
from sample_module import compute_val

class GenuineRedBwrapTest(unittest.TestCase):
    def test_repro(self):
        self.assertEqual(compute_val(2), 5)
"""
        result = self.verifier.verify_candidate_test(code)
        self.assertTrue(result.is_reproduced)
        self.assertEqual(result.reason, "GENUINE_RED")

    def test_bwrap_blocks_reading_host_sensitive_etc_files(self) -> None:
        """SBX-5: Linux bwrap 内で /etc/shadow 等のホスト機密ファイルが読めず、Red として受理されないこと。"""
        self._require_linux_bwrap()

        checked_any = False
        shadow_path = Path("/etc/shadow")
        if shadow_path.exists():
            checked_any = True
            code_shadow = """import unittest
from pathlib import Path

class ReadShadowTest(unittest.TestCase):
    def test_read_shadow(self):
        content = Path("/etc/shadow").read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG")
"""
            res_shadow = self.verifier.verify_candidate_test(code_shadow)
            self._assert_read_blocked(res_shadow)

        passwd_path = Path("/etc/passwd")
        if passwd_path.exists():
            checked_any = True
            lines = passwd_path.read_text(encoding="utf-8", errors="ignore").splitlines()
            first_line = lines[0] if lines else ""
            code_passwd = """import unittest
from pathlib import Path

class ReadPasswdTest(unittest.TestCase):
    def test_read_passwd(self):
        content = Path("/etc/passwd").read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG")
"""
            res_passwd = self.verifier.verify_candidate_test(code_passwd)
            self._assert_read_blocked(
                res_passwd,
                forbidden_secrets=[first_line] if first_line else [],
            )

        if not checked_any:
            self.skipTest("Neither /etc/shadow nor /etc/passwd exists on this Linux host")

    def test_bwrap_blocks_reading_unallowed_host_paths(self) -> None:
        """SBX-5: Linux bwrap 内で許可していないホストのパス (/opt や一時ディレクトリ) が読めないこと。"""
        self._require_linux_bwrap()

        with tempfile.TemporaryDirectory() as other_tmp:
            unallowed_secret = "BWRAP_UNALLOWED_HOST_PATH_SECRET_456"
            unallowed_file = Path(other_tmp) / "host_only_secret.txt"
            unallowed_file.write_text(unallowed_secret, encoding="utf-8")

            code_tmp = f"""import unittest
from pathlib import Path

class ReadUnallowedTmpPathTest(unittest.TestCase):
    def test_read_unallowed_tmp(self):
        content = Path(r"{unallowed_file.resolve()}").read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG")
"""
            res_tmp = self.verifier.verify_candidate_test(code_tmp)
            self._assert_read_blocked(res_tmp, forbidden_secrets=[unallowed_secret])

        opt_path = Path("/opt")
        if opt_path.exists():
            code_opt = """import os, unittest

class ReadUnallowedOptTest(unittest.TestCase):
    def test_read_opt(self):
        entries = os.listdir("/opt")
        self.assertEqual(len(entries), 0)
"""
            res_opt = self.verifier.verify_candidate_test(code_opt)
            self._assert_read_blocked(res_opt)

    def test_bwrap_blocks_reading_home_directory_and_ssh(self) -> None:
        """SBX-5: Linux bwrap 内でホームディレクトリおよび ~/.ssh が読めず、機密値が漏洩しないこと。"""
        self._require_linux_bwrap()

        real_home = Path.home().resolve()
        probe_secret = real_home / f".test_bwrap_home_secret_{os.getpid()}.txt"
        home_secret = "BWRAP_REAL_HOME_SECRET_ABC123"
        created_real_probe = False
        try:
            try:
                probe_secret.write_text(home_secret, encoding="utf-8")
                created_real_probe = True
            except OSError:
                created_real_probe = False

            if created_real_probe:
                code_home = f"""import unittest
from pathlib import Path

class ReadHomeSecretTest(unittest.TestCase):
    def test_read_home_secret(self):
        content = Path(r"{probe_secret}").read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG_TOKEN")
"""
                res_home = self.verifier.verify_candidate_test(code_home)
                self._assert_read_blocked(res_home, forbidden_secrets=[home_secret])

            real_ssh = real_home / ".ssh"
            if real_ssh.exists():
                code_real_ssh = f"""import os, unittest

class ListRealSshTest(unittest.TestCase):
    def test_list_real_ssh(self):
        entries = os.listdir(r"{real_ssh}")
        self.assertEqual(len(entries), 0)
"""
                res_real_ssh = self.verifier.verify_candidate_test(code_real_ssh)
                self._assert_read_blocked(res_real_ssh)
        finally:
            if created_real_probe and probe_secret.exists():
                probe_secret.unlink()

        with tempfile.TemporaryDirectory() as fake_home_tmp:
            fake_home = Path(fake_home_tmp) / "home_user"
            fake_ssh = fake_home / ".ssh"
            fake_ssh.mkdir(parents=True, exist_ok=True)
            ssh_secret = "BWRAP_SSH_PRIVATE_KEY_SECRET_999"
            (fake_ssh / "id_ed25519").write_text(ssh_secret, encoding="utf-8")

            with patch.dict(os.environ, {"HOME": str(fake_home)}):
                code_fake_ssh = """import unittest
from pathlib import Path

class ReadSshKeyTest(unittest.TestCase):
    def test_read_ssh_key(self):
        key_path = Path.home() / ".ssh" / "id_ed25519"
        content = key_path.read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG_KEY")
"""
                res_fake_ssh = self.verifier.verify_candidate_test(code_fake_ssh)
                self._assert_read_blocked(res_fake_ssh, forbidden_secrets=[ssh_secret])

    def test_bwrap_blocks_reading_parent_repo_files(self) -> None:
        """SBX-5: Linux bwrap 内で元のリポジトリの機密ファイルや .git が読めないこと。"""
        self._require_linux_bwrap()

        parent_secret_path = self.repo_dir / ".env.confidential"
        parent_secret = "BWRAP_PARENT_REPO_SECRET_XYZ987"
        parent_secret_path.write_text(parent_secret, encoding="utf-8")

        code_env = f"""import unittest
from pathlib import Path

class ReadParentRepoSecretTest(unittest.TestCase):
    def test_read_parent_repo_secret(self):
        content = Path(r"{parent_secret_path.resolve()}").read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG_PASSWORD")
"""
        res_env = self.verifier.verify_candidate_test(code_env)
        self._assert_read_blocked(res_env, forbidden_secrets=[parent_secret])

        parent_git_head = self.repo_dir / ".git" / "HEAD"
        code_git = f"""import unittest
from pathlib import Path

class ReadParentRepoGitTest(unittest.TestCase):
    def test_read_parent_repo_git_head(self):
        content = Path(r"{parent_git_head.resolve()}").read_text(encoding="utf-8")
        self.assertEqual(content, "WRONG_HEAD")
"""
        res_git = self.verifier.verify_candidate_test(code_git)
        self._assert_read_blocked(res_git)


if __name__ == "__main__":
    unittest.main()
