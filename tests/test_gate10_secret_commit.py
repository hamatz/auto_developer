"""GATE-10: init の .gitignore 追記と verify-step / commit-step での機密ファイル拒否を検証する。"""

from __future__ import annotations

import contextlib
import io
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main
from auto_developer.guard import GitGuard


class TestGate10SecretCommit(unittest.TestCase):
    """GATE-10: .env / 鍵ファイル / SSH鍵のコミット拒否と init .gitignore を検証する。"""

    def _init_repo(self, repo: Path) -> str:
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
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

    def test_init_adds_env_patterns_to_gitignore(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            rc = cli_main(["--work-dir", str(repo), "init"])
            self.assertEqual(rc, 0)
            lines = (repo / ".gitignore").read_text(encoding="utf-8").splitlines()
            self.assertIn(".env", lines)
            self.assertIn(".env.*", lines)
            self.assertIn("!.env.example", lines)

    def test_verify_step_rejects_unignored_env_and_key_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = self._init_repo(repo)
            (repo / ".env").write_text("API_KEY=dummy\n", encoding="utf-8")

            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cli_main(["--work-dir", str(repo), "verify-step", "--checkpoint", checkpoint, "--task-id", "1"])

            self.assertEqual(rc, 1)
            self.assertIn("SECRET_FILE_IN_COMMIT:", err.getvalue())
            self.assertIn(".env", err.getvalue())
            self.assertIn(".gitignore", err.getvalue())

    def test_commit_step_rejects_secret_file_with_exit_4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as rev_tmp:
            repo = Path(tmp)
            checkpoint = self._init_repo(repo)
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")
            self.assertEqual(
                cli_main(["--work-dir", str(repo), "verify-step", "--checkpoint", checkpoint, "--task-id", "1"]),
                0,
            )

            # record-review with valid cross-family models
            review_dir = Path(rev_tmp)
            cli_main(["--work-dir", str(repo), "review-input", "--task-id", "1", "--out-dir", str(review_dir)])
            checker_out = review_dir / "checker_out.txt"
            checker_out.write_text('{"decision": "APPROVE", "reason": "ok"}\n', encoding="utf-8")
            spec_ex = review_dir / "spec.md"
            spec_ex.write_text("# Spec\n", encoding="utf-8")
            self.assertEqual(
                cli_main([
                    "--work-dir", str(repo), "record-review",
                    "--task-id", "1",
                    "--checker-output", str(checker_out),
                    "--diff-file", str(review_dir / "diff.patch"),
                    "--spec-file", str(spec_ex),
                    "--maker-model", "gemini-4-hydrogen-medium",
                    "--checker-model", "claude-sonnet-5-5",
                    "--reason", "ok",
                ]),
                0,
            )

            # Inject .env before commit-step
            (repo / ".env").write_text("SECRET=123\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cli_main([
                    "--work-dir", str(repo), "commit-step",
                    "--task-id", "1",
                    "--checkpoint", checkpoint,
                    "--maker-model", "gemini-4-hydrogen-medium",
                    "--checker-model", "claude-sonnet-5-5",
                    "--checker-reason", "ok",
                ])

            self.assertEqual(rc, 4)
            self.assertIn("SECRET_FILE_IN_COMMIT:", err.getvalue())
            head_after = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
            ).stdout.strip()
            self.assertEqual(head_after, checkpoint)

    def test_allowed_env_examples_and_token_names_are_not_rejected(self) -> None:
        for name in [".env.example", ".env.sample", ".env.template", "token_counter.py", "secret_config.json"]:
            self.assertFalse(GitGuard.is_forbidden_commit_secret_path(name), f"{name} should be allowed")
        for name in [".env", ".env.local", "server.key", "cert.pem", "store.pkcs12", "id.pfx", "id_rsa", "id_ed25519", "id_ecdsa"]:
            self.assertTrue(GitGuard.is_forbidden_commit_secret_path(name), f"{name} should be rejected")


if __name__ == "__main__":
    unittest.main()
