"""SBX-7: macOS で sys.executable がシンボリックリンクでも候補テストを起動できることを検証する。"""

from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.verifier import RedTestVerifier
from tests.sandbox_test_helpers import require_platform_sandbox


class TestSBX7MacOSSymlinkPython(unittest.TestCase):
    """SBX-7: macOS sandbox-exec でシンボリックリンク経由の Python 実行を検証する。"""

    def setUp(self) -> None:
        require_platform_sandbox(
            self,
            expected_platform="darwin",
            backend_available=bool(shutil.which("sandbox-exec")),
            platform_skip_reason="macOS sandbox-exec is only available on macOS",
            unavailable_reason="macOS sandbox-exec is unavailable on macOS",
        )
        self._repo_tmp = tempfile.TemporaryDirectory(prefix="sbx7_repo_")
        self.repo_dir = Path(self._repo_tmp.name)
        (self.repo_dir / "src").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "src" / "__init__.py").write_text("", encoding="utf-8")
        (self.repo_dir / "src" / "app.py").write_text("def value():\n    return 1\n", encoding="utf-8")

        import subprocess

        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, check=True, capture_output=True)

        self.verifier = RedTestVerifier(self.repo_dir)

    def tearDown(self) -> None:
        self._repo_tmp.cleanup()

    def test_symlinked_python_in_home_runs_candidate_test(self) -> None:
        """ホームディレクトリ配下の多段シンボリックリンク経由の sys.executable で GENUINE_RED を受理できること。"""
        real_python = Path(sys.executable)
        with tempfile.TemporaryDirectory(prefix=".tmp_sbx7_home_", dir=str(Path.home())) as home_tmp:
            home_tmp_path = Path(home_tmp)
            secret_file = home_tmp_path / "secret.txt"
            secret_file.write_text("TOP_SECRET", encoding="utf-8")

            link_dir = home_tmp_path / "hostedtoolcache" / "Python" / "3.12" / "bin"
            link_dir.mkdir(parents=True, exist_ok=True)
            mid_link = home_tmp_path / "mid_python"
            mid_link.symlink_to(real_python)
            outer_link = link_dir / "python3"
            outer_link.symlink_to(mid_link)

            failing_test = """import unittest
import app
class GenuineRedTest(unittest.TestCase):
    def test_fail(self):
        self.assertEqual(app.value(), 2)
"""
            with patch.object(sys, "executable", str(outer_link)):
                result = self.verifier.verify_candidate_test(failing_test)

            self.assertTrue(
                result.is_reproduced,
                f"Expected GENUINE_RED, got reason={result.reason}, output={result.output}",
            )
            self.assertEqual(result.reason, "GENUINE_RED")

            # ホーム内の秘密ファイルやディレクトリ一覧は引き続き読めないこと
            leak_test = f"""import unittest
from pathlib import Path
class LeakHomeTest(unittest.TestCase):
    def test_read_secret(self):
        data = Path({str(secret_file)!r}).read_text(encoding="utf-8")
        self.assertEqual(data, "")
"""
            with patch.object(sys, "executable", str(outer_link)):
                leak_result = self.verifier.verify_candidate_test(leak_test)

            self.assertFalse(leak_result.is_reproduced)
            self.assertEqual(leak_result.reason, "UNHANDLED_EXCEPTION")
            self.assertIn("PermissionError", leak_result.output)


if __name__ == "__main__":
    unittest.main()
