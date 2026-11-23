from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from auto_developer.bundler import EvidenceBundler

ORCH = Path(__file__).resolve().parent.parent / "scripts" / "nightly_orchestrator.py"
SPEC_TEXT = (
    "# SPEC\n\n"
    "## 2. 要件\n"
    "- [ ] **APP-1**: `ok()` は True を返す。\n"
)
MAKER = "gemini-4-hydrogen-medium"
CHECKER = "claude-sonnet-5-5"


class TestGate12ReviewHistory(unittest.TestCase):
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
        (self.repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
        self.verify = self.repo / "verify.sh"
        self.verify.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.verify.chmod(0o755)
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

    def test_record_review_preserves_history_without_overwriting_and_keeps_latest_and_bundle_unchanged(self) -> None:
        # 1st attempt: REJECT
        (self.repo / "app.py").write_text("def ok():\n    return False\n", encoding="utf-8")
        self._verify_ok()
        io1 = self.io / "attempt1"
        io1.mkdir()
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(io1)).returncode, 0)
        diff1 = io1 / "diff.patch"
        spec1 = io1 / "spec.md"
        spec1.write_text("- [ ] **APP-1**: `ok()` は True を返す。\n", encoding="utf-8")
        out1 = io1 / "review.txt"
        out1.write_text("Returns False instead of True.\nDECISION: REJECT\n", encoding="utf-8")

        res1 = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(out1),
            "--diff-file",
            str(diff1),
            "--spec-file",
            str(spec1),
            "--maker-model",
            MAKER,
            "--checker-model",
            CHECKER,
            "--reason",
            "first attempt rejected",
        )
        self.assertEqual(res1.returncode, 0, res1.stderr)

        # 2nd attempt: APPROVE after fixing app.py
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()
        io2 = self.io / "attempt2"
        io2.mkdir()
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(io2)).returncode, 0)
        diff2 = io2 / "diff.patch"
        spec2 = io2 / "spec.md"
        spec2.write_text(SPEC_TEXT, encoding="utf-8")
        out2 = io2 / "review.txt"
        out2.write_text("Looks good.\nDECISION: APPROVE\n", encoding="utf-8")

        res2 = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(out2),
            "--diff-file",
            str(diff2),
            "--spec-file",
            str(spec2),
            "--maker-model",
            MAKER,
            "--checker-model",
            CHECKER,
            "--reason",
            "second attempt approved",
        )
        self.assertEqual(res2.returncode, 0, res2.stderr)

        reviews_dir = self.repo / ".git" / "nightly_evidence" / "reviews"
        task_dir = reviews_dir / "task_1"
        hist1 = task_dir / "history" / "1"
        hist2 = task_dir / "history" / "2"

        # History 1 (REJECT) must remain intact and not overwritten
        self.assertTrue(hist1.is_dir(), f"missing {hist1}")
        self.assertEqual((hist1 / "checker_output.txt").read_bytes(), out1.read_bytes())
        self.assertEqual((hist1 / "diff.patch").read_bytes(), diff1.read_bytes())
        self.assertEqual((hist1 / "spec_excerpt.md").read_bytes(), spec1.read_bytes())
        rec1 = json.loads((hist1 / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(rec1["decision"], "REJECT")
        self.assertEqual(rec1["maker_model"], MAKER)
        self.assertEqual(rec1["checker_model"], CHECKER)
        self.assertIsInstance(rec1["recorded_at"], float)
        self.assertEqual(rec1["checker_output_sha256"], hashlib.sha256(out1.read_bytes()).hexdigest())
        self.assertEqual(rec1["diff_sha256"], hashlib.sha256(diff1.read_bytes()).hexdigest())
        self.assertEqual(rec1["spec_sha256"], hashlib.sha256(spec1.read_bytes()).hexdigest())

        # History 2 (APPROVE) must also be recorded
        self.assertTrue(hist2.is_dir(), f"missing {hist2}")
        self.assertEqual((hist2 / "checker_output.txt").read_bytes(), out2.read_bytes())
        self.assertEqual((hist2 / "diff.patch").read_bytes(), diff2.read_bytes())
        self.assertEqual((hist2 / "spec_excerpt.md").read_bytes(), spec2.read_bytes())
        rec2 = json.loads((hist2 / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(rec2["decision"], "APPROVE")
        self.assertEqual(rec2["checker_output_sha256"], hashlib.sha256(out2.read_bytes()).hexdigest())
        self.assertEqual(rec2["diff_sha256"], hashlib.sha256(diff2.read_bytes()).hexdigest())
        self.assertEqual(rec2["spec_sha256"], hashlib.sha256(spec2.read_bytes()).hexdigest())

        # Latest record & copies (GATE-6) must match 2nd attempt
        latest_rec = json.loads((reviews_dir / "task_1.json").read_text(encoding="utf-8"))
        self.assertEqual(latest_rec["decision"], "APPROVE")
        self.assertEqual((task_dir / "checker_output.txt").read_bytes(), out2.read_bytes())
        self.assertEqual((task_dir / "diff.patch").read_bytes(), diff2.read_bytes())
        self.assertEqual((task_dir / "spec_excerpt.md").read_bytes(), spec2.read_bytes())

        # commit-step (GATE-6) must succeed using the latest record
        commit_res = self._orch(
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
            "second attempt approved",
        )
        self.assertEqual(commit_res.returncode, 0, commit_res.stderr)

        # Bundle (BDL-4) must include only the latest 3 files, NOT history/
        bundler = EvidenceBundler(repo_dir=self.repo)
        bundle_zip = bundler.create_bundle(dest_path=self.repo.parent / "bundle.zip")
        with zipfile.ZipFile(bundle_zip, "r") as zf:
            names = zf.namelist()
            self.assertIn("evidence/reviews/task_1/checker_output.txt", names)
            self.assertIn("evidence/reviews/task_1/diff.patch", names)
            self.assertIn("evidence/reviews/task_1/spec_excerpt.md", names)
            self.assertFalse(
                any("history" in n for n in names),
                f"history directory must not be included in bundle: {names}",
            )
            self.assertEqual(
                zf.read("evidence/reviews/task_1/checker_output.txt"),
                out2.read_bytes(),
            )
        verify_res = bundler.verify_bundle(bundle_zip)
        self.assertEqual(verify_res.status, "VERIFIED", verify_res)


if __name__ == "__main__":
    unittest.main()
