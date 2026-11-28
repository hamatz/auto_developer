import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer import cli
from auto_developer.evidence import compute_canonical_diff
from tests.review_test_helpers import record_test_review


def _init_git_repo(repo_dir: Path) -> str:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / ".gitignore").write_text(".dev/\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Plan\n\n## ステップ\n- [ ] 1. Test task\n", encoding="utf-8")
    (repo_dir / "app.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True
    ).stdout.strip()


class TestGate8CanonicalDiffRedaction(unittest.TestCase):
    def test_secret_content_lines_are_redacted_while_other_lines_preserved(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_git_repo(repo)
            fake_key = "sk-" + "aB12" * 6
            (repo / "app.py").write_text(
                f"x = 1\ny = 2\nAPI_KEY = '{fake_key}'\nz = 3\n",
                encoding="utf-8",
            )
            diff_text = compute_canonical_diff(repo, chk).decode("utf-8")
            self.assertNotIn(fake_key, diff_text)
            self.assertIn("[auto-dev: INV-5 の規則により省略]", diff_text)
            self.assertIn("+y = 2", diff_text)
            self.assertIn("+z = 3", diff_text)

    def test_secret_filename_patterns_omit_content_without_hash(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_git_repo(repo)
            secret_files = {
                ".env": "DB_PASSWORD=super_secret_val_12345\n",
                ".env.local": "LOCAL_VAL=secret_local_999\n",
                "server.key": "RAW_KEY_BODY_LINE\n",
                "cert.pem": "RAW_PEM_BODY_LINE\n",
                "store.pkcs12": "RAW_PKCS12_LINE\n",
                "store.pfx": "RAW_PFX_LINE\n",
                "keys/id_rsa": "RAW_ID_RSA_LINE\n",
                "keys/id_ed25519": "RAW_ID_ED25519_LINE\n",
                "keys/id_ecdsa": "RAW_ID_ECDSA_LINE\n",
            }
            for rel, content in secret_files.items():
                p = repo / rel
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(content, encoding="utf-8")

            diff_text = compute_canonical_diff(repo, chk).decode("utf-8")
            for rel, content in secret_files.items():
                self.assertIn(rel, diff_text)
                self.assertNotIn(content.strip(), diff_text)

    def test_secret_token_credential_in_filename_not_omitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_git_repo(repo)
            for fname in ("token_helper.py", "secret_config.py", "credential_loader.py"):
                (repo / fname).write_text("def load():\n    return 'ok'\n", encoding="utf-8")

            diff_text = compute_canonical_diff(repo, chk).decode("utf-8")
            self.assertIn("+def load():", diff_text)
            self.assertIn("+    return 'ok'", diff_text)
            self.assertNotIn("[auto-dev: INV-5 の規則により省略]", diff_text)

    def test_review_input_record_review_and_commit_step_with_redacted_diff(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_git_repo(repo)
            (repo / "scripts").mkdir(exist_ok=True)
            verify_sh = repo / "scripts" / "verify_local.sh"
            verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            verify_sh.chmod(0o755)
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add verify_local"], cwd=repo, check=True, capture_output=True)
            chk = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()

            fake_key = "sk-" + "xY90" * 6
            (repo / "app.py").write_text(f"x = 1\nKEY = '{fake_key}'\n", encoding="utf-8")

            with patch.object(sys, "argv", ["auto-dev", "--work-dir", str(repo), "verify-step", "--checkpoint", chk, "--task-id", "1"]):
                self.assertEqual(cli.main(), 0)

            out_dir = repo / ".dev" / "checker" / "t1"
            with patch.object(sys, "argv", ["auto-dev", "--work-dir", str(repo), "review-input", "--task-id", "1", "--out-dir", str(out_dir)]):
                self.assertEqual(cli.main(), 0)
            patch_text = (out_dir / "diff.patch").read_text(encoding="utf-8")
            self.assertNotIn(fake_key, patch_text)
            self.assertIn("[auto-dev: INV-5 の規則により省略]", patch_text)

            res = record_test_review(repo, "1")
            self.assertEqual(res.returncode, 0, res.stderr)

            with patch.object(
                sys,
                "argv",
                [
                    "auto-dev",
                    "--work-dir",
                    str(repo),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "gemini-3.8-flash",
                    "--checker-model",
                    "claude-opus-5-5-medium",
                ],
            ):
                self.assertEqual(cli.main(), 0)
