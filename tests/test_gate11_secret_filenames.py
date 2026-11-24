"""GATE-11: 非ASCII機密ファイル名・diff省略表示・ファイル種類変更(T)の検査を検証する。"""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main
from auto_developer.evidence import compute_canonical_diff


class TestGate11SecretFilenames(unittest.TestCase):
    """GATE-11: 日本語ファイル名・省略表示・T(種類変更)に対する機密検査。"""

    def _init_repo(self, repo: Path) -> str:
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (repo / "PLAN.md").write_text("- [ ] 1. dummy task\n", encoding="utf-8")
        (repo / "scripts").mkdir(parents=True, exist_ok=True)
        verify_sh = repo / "scripts" / "verify_local.sh"
        verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        verify_sh.chmod(0o755)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_verify_step_rejects_non_ascii_pem_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = self._init_repo(repo)
            (repo / "鍵.pem").write_text("dummy-key\n", encoding="utf-8")

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cli_main(["--work-dir", str(repo), "verify-step", "--checkpoint", checkpoint, "--task-id", "1"])

            self.assertEqual(rc, 1)
            self.assertIn("SECRET_FILE_IN_COMMIT:", err.getvalue())
            self.assertIn("鍵.pem", err.getvalue())

    def test_canonical_diff_redacts_non_ascii_secret_filename_without_octal_escapes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = self._init_repo(repo)
            (repo / "鍵.pem").write_text("dummy-key\n", encoding="utf-8")

            diff_text = compute_canonical_diff(repo, checkpoint).decode("utf-8")
            self.assertIn("鍵.pem", diff_text)
            self.assertNotIn("\\351\\215\\265", diff_text)

    def test_verify_step_rejects_symlink_to_regular_file_type_change_of_env(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            (repo / ".env.example").write_text("FOO=bar\n", encoding="utf-8")
            (repo / ".env").symlink_to(".env.example")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "track symlink"], cwd=repo, check=True, capture_output=True)
            checkpoint = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()

            # Replace symlink with regular file (type change T in git)
            (repo / ".env").unlink()
            (repo / ".env").write_text("SECRET=123\n", encoding="utf-8")

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cli_main(["--work-dir", str(repo), "verify-step", "--checkpoint", checkpoint, "--task-id", "1"])

            self.assertEqual(rc, 1)
            self.assertIn("SECRET_FILE_IN_COMMIT:", err.getvalue())
            self.assertIn(".env", err.getvalue())


if __name__ == "__main__":
    unittest.main()
