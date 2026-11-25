from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parent.parent / "scripts" / "nightly_orchestrator.py"
SPEC_TEXT = (
    "# SPEC\n\n"
    "## 2. 要件\n"
    "- [ ] **APP-1**: `ok()` は True を返す。\n"
)
MAKER = "gemini-4-hydrogen-medium"
CHECKER = "claude-sonnet-5-5"


class TestGate13ReviewTreeMatch(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        self.io = base / "checker_io"
        self.io.mkdir()
        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "config", "user.name", "Tester"],
            ["git", "config", "user.email", "tester@example.com"],
        ):
            subprocess.run(cmd, cwd=self.repo, check=True, capture_output=True)
        self.plan = self.repo / "PLAN.md"
        self.plan.write_text("# PLAN\n\n## ステップ\n- [ ] 1. テストタスク\n", encoding="utf-8")
        (self.repo / "SPEC.md").write_text(SPEC_TEXT, encoding="utf-8")
        (self.repo / ".gitignore").write_text(".dev/\n*.ignored\n", encoding="utf-8")
        self.verify = self.repo / "verify.sh"
        self.verify.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.verify.chmod(0o755)
        (self.repo / "app.py").write_text("def ok():\n    return False\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)
        self.checkpoint = self._head()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _orch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ORCH), "--plan", str(self.plan), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def _verify_ok(self) -> None:
        res = self._orch(
            "--verify-script",
            str(self.verify),
            "verify-step",
            "--checkpoint",
            self.checkpoint,
            "--task-id",
            "1",
        )
        self.assertEqual(res.returncode, 0, res.stderr)

    def test_review_input_rejects_modified_tracked_file_after_verify(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()

        # Modify tracked file after verify-step
        (self.repo / "app.py").write_text("def ok():\n    return True  # edited\n", encoding="utf-8")
        out_dir = self.io / "after_modify"
        res = self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir))
        self.assertEqual(res.returncode, 3, f"stdout={res.stdout!r} stderr={res.stderr!r}")
        self.assertIn("verify-step", res.stderr)
        self.assertFalse((out_dir / "diff.patch").exists(), "diff.patch must not be written when tree changed")

    def test_review_input_rejects_untracked_file_added_after_verify(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()

        # Add untracked non-ignored file after verify-step
        (self.repo / "extra.py").write_text("x = 1\n", encoding="utf-8")
        out_dir = self.io / "after_untracked"
        res = self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir))
        self.assertEqual(res.returncode, 3, f"stdout={res.stdout!r} stderr={res.stderr!r}")
        self.assertIn("verify-step", res.stderr)
        self.assertFalse((out_dir / "diff.patch").exists())

    def test_review_input_allows_ignored_file_added_after_verify(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()

        (self.repo / "tmp.ignored").write_text("ignored content\n", encoding="utf-8")
        out_dir = self.io / "after_ignored"
        res = self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir))
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertTrue((out_dir / "diff.patch").is_file())

    def test_record_review_rejects_tree_change_after_verify_even_with_matching_diff(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()

        out_dir = self.io / "valid"
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir)).returncode, 0)
        spec_file = out_dir / "spec.md"
        spec_file.write_text(SPEC_TEXT, encoding="utf-8")
        checker_out = out_dir / "review.txt"
        checker_out.write_text("OK\nDECISION: APPROVE\n", encoding="utf-8")

        # Modify working tree after verify-step & review-input
        (self.repo / "app.py").write_text("def ok():\n    return True  # stealth edit\n", encoding="utf-8")
        res = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(checker_out),
            "--diff-file",
            str(out_dir / "diff.patch"),
            "--spec-file",
            str(spec_file),
            "--maker-model",
            MAKER,
            "--checker-model",
            CHECKER,
        )
        self.assertEqual(res.returncode, 3, f"stdout={res.stdout!r} stderr={res.stderr!r}")
        self.assertIn("verify-step", res.stderr)
        self.assertFalse((self.repo / ".git" / "nightly_evidence" / "reviews" / "task_1.json").exists())

    def test_review_input_and_record_review_reject_missing_tree_sha_in_evidence(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()

        # Simulate evidence without tree_sha
        ev_path = self.repo / ".git" / "nightly_evidence" / "task_1.json"
        ev_data = json.loads(ev_path.read_text(encoding="utf-8"))
        ev_data["tree_sha"] = ""
        ev_path.write_text(json.dumps(ev_data), encoding="utf-8")

        out_dir = self.io / "no_tree_sha"
        res_in = self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir))
        self.assertEqual(res_in.returncode, 3, f"stdout={res_in.stdout!r} stderr={res_in.stderr!r}")

        out_dir.mkdir(parents=True, exist_ok=True)
        diff_file = out_dir / "diff.patch"
        diff_file.write_text("", encoding="utf-8")
        spec_file = out_dir / "spec.md"
        spec_file.write_text(SPEC_TEXT, encoding="utf-8")
        checker_out = out_dir / "review.txt"
        checker_out.write_text("OK\nDECISION: APPROVE\n", encoding="utf-8")

        res_rec = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(checker_out),
            "--diff-file",
            str(diff_file),
            "--spec-file",
            str(spec_file),
            "--maker-model",
            MAKER,
            "--checker-model",
            CHECKER,
        )
        self.assertEqual(res_rec.returncode, 3, f"stdout={res_rec.stdout!r} stderr={res_rec.stderr!r}")

    def test_reverify_after_edit_allows_review_input_record_review_and_commit(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()

        (self.repo / "app.py").write_text("def ok():\n    return 1 == 1\n", encoding="utf-8")
        # Fails before re-verify
        res_fail = self._orch("review-input", "--task-id", "1", "--out-dir", str(self.io / "v1"))
        self.assertEqual(res_fail.returncode, 3)

        # Re-verify -> succeeds
        self._verify_ok()
        io2 = self.io / "v2"
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(io2)).returncode, 0)
        spec_file = io2 / "spec.md"
        spec_file.write_text(SPEC_TEXT, encoding="utf-8")
        checker_out = io2 / "review.txt"
        checker_out.write_text("OK\nDECISION: APPROVE\n", encoding="utf-8")
        res_rec = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(checker_out),
            "--diff-file",
            str(io2 / "diff.patch"),
            "--spec-file",
            str(spec_file),
            "--maker-model",
            MAKER,
            "--checker-model",
            CHECKER,
        )
        self.assertEqual(res_rec.returncode, 0, res_rec.stderr)
        res_commit = self._orch(
            "commit-step",
            "--task-id",
            "1",
            "--checkpoint",
            self.checkpoint,
            "--maker-model",
            MAKER,
            "--checker-model",
            CHECKER,
            "--checker-reason",
            "approved after re-verify",
        )
        self.assertEqual(res_commit.returncode, 0, res_commit.stderr)


if __name__ == "__main__":
    unittest.main()
