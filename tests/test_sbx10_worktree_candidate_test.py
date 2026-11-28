from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.verifier import RedTestVerifier, verify_red_first_tests


class TestSbx10WorktreeCandidateTest(unittest.TestCase):
    """SBX-10: git worktree (.git がファイル) の中でもチェックポイント指定の候補テスト実行と Red-First 確認ができること。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.base_dir = Path(self.tmp_dir.name)
        self.main_repo = self.base_dir / "main_repo"
        self.main_repo.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.main_repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.main_repo, check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=self.main_repo,
            check=True,
            capture_output=True,
        )

        # commit 1: バグのある実装
        (self.main_repo / "src").mkdir(parents=True, exist_ok=True)
        (self.main_repo / "src" / "calc.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a - b\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "src/calc.py"], cwd=self.main_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "buggy add"], cwd=self.main_repo, check=True, capture_output=True)
        self.c1 = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.main_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # commit 2: 修正された実装
        (self.main_repo / "src" / "calc.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "src/calc.py"], cwd=self.main_repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix add"], cwd=self.main_repo, check=True, capture_output=True)
        self.c2 = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=self.main_repo,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        # worktree を作成 (.git はファイル)
        self.worktree_dir = self.base_dir / "team_worktree"
        subprocess.run(
            ["git", "worktree", "add", "-b", "team-feat", str(self.worktree_dir)],
            cwd=self.main_repo,
            check=True,
            capture_output=True,
        )
        self.assertTrue((self.worktree_dir / ".git").is_file())
        self.assertFalse((self.worktree_dir / ".git").is_dir())

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_verify_candidate_test_with_commit_in_git_worktree(self) -> None:
        """worktree 内で commit を指定した verify_candidate_test が COMMIT_CHECKOUT_FAILED にならずに動作すること。"""
        verifier = RedTestVerifier(repo_dir=self.worktree_dir)
        test_code = """import unittest
import calc

class CalcTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(1, 2), 3)
"""
        res_c1 = verifier.verify_candidate_test(test_code, commit=self.c1)
        self.assertTrue(res_c1.is_reproduced, f"Expected failure on c1, got {res_c1.reason}: {res_c1.output}")
        self.assertEqual(res_c1.reason, "GENUINE_RED")

        res_c2 = verifier.verify_candidate_test(test_code, commit=self.c2)
        self.assertFalse(res_c2.is_reproduced, f"Expected pass on c2, got {res_c2.reason}: {res_c2.output}")
        self.assertEqual(res_c2.reason, "ALREADY_GREEN")

    def test_verify_red_first_tests_in_git_worktree(self) -> None:
        """worktree 内での verify_red_first_tests (Red-First 確認) が COMMIT_CHECKOUT_FAILED にならず CONFIRMED になること。"""
        tests_dir = self.worktree_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_calc_add.py").write_text(
            """import unittest
import calc

class CalcAddTest(unittest.TestCase):
    def test_add(self):
        self.assertEqual(calc.add(1, 2), 3)
""",
            encoding="utf-8",
        )
        res = verify_red_first_tests(
            repo_dir=self.worktree_dir,
            checkpoint=self.c1,
            test_files=["tests/test_calc_add.py"],
        )
        self.assertEqual(res["result"], "CONFIRMED", f"Expected CONFIRMED, got {res}")
        self.assertEqual(len(res["file_results"]), 1)
        self.assertEqual(res["file_results"][0]["result"], "CONFIRMED")

    def test_capture_repo_snapshot_in_git_worktree(self) -> None:
        """worktree 内でも _capture_repo_snapshot が Git リポジトリとして HEAD を取得できること。"""
        verifier = RedTestVerifier(repo_dir=self.worktree_dir)
        snap = verifier._capture_repo_snapshot()
        self.assertEqual(snap.get("type"), "git")
        self.assertEqual(snap.get("head"), self.c2)

    def test_non_git_directory_with_commit_still_fails(self) -> None:
        """非 Git ディレクトリおよび Git リポジトリ内のサブディレクトリでは commit 指定時に COMMIT_CHECKOUT_FAILED になること。"""
        plain_dir = self.base_dir / "plain_dir"
        plain_dir.mkdir(parents=True, exist_ok=True)
        (plain_dir / "a.py").write_text("x = 1\n", encoding="utf-8")

        subdir = self.worktree_dir / "subdir"
        subdir.mkdir(parents=True, exist_ok=True)
        (subdir / "b.py").write_text("y = 2\n", encoding="utf-8")

        test_code = """import unittest

class DummyTest(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
"""
        for target_dir in (plain_dir, subdir):
            verifier = RedTestVerifier(repo_dir=target_dir)
            res = verifier.verify_candidate_test(test_code, commit="HEAD")
            self.assertFalse(res.is_reproduced)
            self.assertEqual(res.reason, "COMMIT_CHECKOUT_FAILED")
            self.assertIn("Repository is not a git repository", res.output)


if __name__ == "__main__":
    unittest.main()
