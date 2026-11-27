from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.guard import GitGuard


class TestRcv9QuarantineFilenameZ(unittest.TestCase):
    """RCV-9: sanitize_staged_changes は -z でステージ済みファイル名を読み、非ASCIIや空白を含むファイルも検査する。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        (self.repo_dir / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (self.repo_dir / "鍵.txt").write_text("safe initial content\n", encoding="utf-8")
        (self.repo_dir / "my key.txt").write_text("safe initial content\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial"], cwd=self.repo_dir, check=True, capture_output=True)
        self.checkpoint = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_tracked_non_ascii_file_with_secret_is_excluded_from_quarantine_branch(self) -> None:
        """追跡している `鍵.txt` に入れた秘密が隔離ブランチにコミットされないこと。"""
        secret_marker = "-----BEGIN " + "PRIVATE KEY-----"
        (self.repo_dir / "鍵.txt").write_text(
            f"{secret_marker}\nDUMMY_KEY_BODY\n",
            encoding="utf-8",
        )
        (self.repo_dir / "my key.txt").write_text(
            f"{secret_marker}\nDUMMY_KEY_BODY_2\n",
            encoding="utf-8",
        )

        guard = GitGuard(self.repo_dir)
        quarantine_branch = guard.quarantine_and_restore(
            task_id="task-rcv9",
            checkpoint=self.checkpoint,
        )

        # 隔離ブランチ上の 鍵.txt / my key.txt には秘密が入っていない（初期内容のまま）
        for filename in ("鍵.txt", "my key.txt"):
            show_res = subprocess.run(
                ["git", "show", f"{quarantine_branch}:{filename}"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=True,
            )
            self.assertNotIn(secret_marker, show_res.stdout)
            self.assertEqual(show_res.stdout, "safe initial content\n")

    def test_sanitize_staged_changes_skips_missing_files_without_error(self) -> None:
        """ステージ後に作業ツリーから消えたファイルがあっても例外にならず検査を続行すること。"""
        secret_marker = "-----BEGIN " + "PRIVATE KEY-----"
        (self.repo_dir / "鍵.txt").write_text(
            f"{secret_marker}\nDUMMY_KEY_BODY\n",
            encoding="utf-8",
        )
        (self.repo_dir / "my key.txt").write_text("modified safe\n", encoding="utf-8")
        subprocess.run(["git", "add", "-u"], cwd=self.repo_dir, check=True)
        # ステージ後に my key.txt を作業ツリーから削除
        (self.repo_dir / "my key.txt").unlink()

        guard = GitGuard(self.repo_dir)
        excluded = guard.sanitize_staged_changes()
        self.assertEqual(excluded, ["鍵.txt"])


if __name__ == "__main__":
    unittest.main()
