from __future__ import annotations

import hashlib
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


class TestGate13SingleReviewPerVerify(unittest.TestCase):
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

    def _prepare_review_input(self, subdir: str) -> tuple[Path, Path]:
        out_dir = self.io / subdir
        out_dir.mkdir(parents=True, exist_ok=True)
        res = self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir))
        self.assertEqual(res.returncode, 0, res.stderr)
        spec_file = out_dir / "spec.md"
        spec_file.write_text("- [ ] **APP-1**: `ok()` は True を返す。\n", encoding="utf-8")
        return out_dir / "diff.patch", spec_file

    def _record_review(
        self,
        checker_out: Path,
        diff_file: Path,
        spec_file: Path,
        reason: str = "review",
    ) -> subprocess.CompletedProcess[str]:
        return self._orch(
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
            "--reason",
            reason,
        )

    def test_record_review_rejects_second_decided_review_for_same_evidence(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()
        diff1, spec1 = self._prepare_review_input("v1")

        out_rej = self.io / "v1" / "reject.txt"
        out_rej.write_text("Needs more checks.\nDECISION: REJECT\n", encoding="utf-8")
        res1 = self._record_review(out_rej, diff1, spec1, reason="rejected first")
        self.assertEqual(res1.returncode, 0, res1.stderr)

        # Second decided review on the SAME verification evidence must be rejected (exit 4)
        out_app = self.io / "v1" / "approve.txt"
        out_app.write_text("Actually looks fine.\nDECISION: APPROVE\n", encoding="utf-8")
        res2 = self._record_review(out_app, diff1, spec1, reason="second review same verify")
        self.assertEqual(res2.returncode, 4, f"stdout={res2.stdout!r} stderr={res2.stderr!r}")
        self.assertIn("CHECKER_POLICY_VIOLATION:", res2.stderr)

        reviews_dir = self.repo / ".git" / "nightly_evidence" / "reviews"
        latest = json.loads((reviews_dir / "task_1.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["decision"], "REJECT")

        hist_dir = reviews_dir / "task_1" / "history"
        self.assertTrue((hist_dir / "1").is_dir())
        self.assertFalse((hist_dir / "2").exists())

        events_path = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        review_events = [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip() and json.loads(line).get("event") == "checker_review"
        ]
        self.assertEqual(len(review_events), 1)
        self.assertEqual(review_events[0]["decision"], "REJECT")

    def test_record_review_allows_decided_review_after_reverify(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()
        diff1, spec1 = self._prepare_review_input("v1")
        out_rej = self.io / "v1" / "reject.txt"
        out_rej.write_text("Reject first.\nDECISION: REJECT\n", encoding="utf-8")
        self.assertEqual(self._record_review(out_rej, diff1, spec1).returncode, 0)

        # Re-verify issues a new evidence token
        self._verify_ok()
        diff2, spec2 = self._prepare_review_input("v2")
        out_app = self.io / "v2" / "approve.txt"
        out_app.write_text("Approved after reverify.\nDECISION: APPROVE\n", encoding="utf-8")
        res2 = self._record_review(out_app, diff2, spec2)
        self.assertEqual(res2.returncode, 0, res2.stderr)

        reviews_dir = self.repo / ".git" / "nightly_evidence" / "reviews"
        latest = json.loads((reviews_dir / "task_1.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["decision"], "APPROVE")
        self.assertTrue((reviews_dir / "task_1" / "history" / "1").is_dir())
        self.assertTrue((reviews_dir / "task_1" / "history" / "2").is_dir())

    def test_record_review_records_undecided_output_in_history_and_allows_subsequent_decided_review(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()
        diff1, spec1 = self._prepare_review_input("v1")

        out_trunc = self.io / "v1" / "truncated.txt"
        out_trunc.write_text("Analyzing diff...\n(output cut off before decision)", encoding="utf-8")
        res_trunc = self._record_review(out_trunc, diff1, spec1, reason="truncated")
        self.assertEqual(res_trunc.returncode, 4, f"stdout={res_trunc.stdout!r} stderr={res_trunc.stderr!r}")
        self.assertIn("CHECKER_POLICY_VIOLATION:", res_trunc.stderr)

        reviews_dir = self.repo / ".git" / "nightly_evidence" / "reviews"
        self.assertFalse((reviews_dir / "task_1.json").exists(), "latest record must not be created for undecided output")

        hist1 = reviews_dir / "task_1" / "history" / "1"
        self.assertTrue(hist1.is_dir(), "undecided output must be preserved in history/1")
        self.assertEqual((hist1 / "checker_output.txt").read_bytes(), out_trunc.read_bytes())
        self.assertEqual((hist1 / "diff.patch").read_bytes(), diff1.read_bytes())
        self.assertEqual((hist1 / "spec_excerpt.md").read_bytes(), spec1.read_bytes())
        rec1 = json.loads((hist1 / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(rec1["decision"], "")
        self.assertEqual(rec1["checker_output_sha256"], hashlib.sha256(out_trunc.read_bytes()).hexdigest())

        # Subsequent decided review on the SAME verification evidence must succeed
        out_app = self.io / "v1" / "approve.txt"
        out_app.write_text("Looks good.\nDECISION: APPROVE\n", encoding="utf-8")
        res_app = self._record_review(out_app, diff1, spec1, reason="approved on retry")
        self.assertEqual(res_app.returncode, 0, res_app.stderr)

        latest = json.loads((reviews_dir / "task_1.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["decision"], "APPROVE")
        hist2 = reviews_dir / "task_1" / "history" / "2"
        self.assertTrue(hist2.is_dir())
        rec2 = json.loads((hist2 / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(rec2["decision"], "APPROVE")

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
            "approved on retry",
        )
        self.assertEqual(commit_res.returncode, 0, commit_res.stderr)

    def test_record_review_undecided_output_preserves_previous_latest_record(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return False\n", encoding="utf-8")
        self._verify_ok()
        diff1, spec1 = self._prepare_review_input("v1")
        out_rej = self.io / "v1" / "reject.txt"
        out_rej.write_text("Still False.\nDECISION: REJECT\n", encoding="utf-8")
        self.assertEqual(self._record_review(out_rej, diff1, spec1).returncode, 0)

        # Fix and re-verify
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()
        diff2, spec2 = self._prepare_review_input("v2")

        # Undecided output on v2 must not overwrite latest record (which is v1 REJECT)
        out_trunc = self.io / "v2" / "truncated.txt"
        out_trunc.write_text("Partial output without decision\n", encoding="utf-8")
        res_trunc = self._record_review(out_trunc, diff2, spec2)
        self.assertEqual(res_trunc.returncode, 4)

        reviews_dir = self.repo / ".git" / "nightly_evidence" / "reviews"
        latest = json.loads((reviews_dir / "task_1.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["decision"], "REJECT")
        self.assertEqual((reviews_dir / "task_1" / "checker_output.txt").read_bytes(), out_rej.read_bytes())

        hist2 = reviews_dir / "task_1" / "history" / "2"
        self.assertTrue(hist2.is_dir())
        rec2 = json.loads((hist2 / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(rec2["decision"], "")

        # Decided review on v2 succeeds
        out_app = self.io / "v2" / "approve.txt"
        out_app.write_text("Now True.\nDECISION: APPROVE\n", encoding="utf-8")
        res_app = self._record_review(out_app, diff2, spec2)
        self.assertEqual(res_app.returncode, 0, res_app.stderr)
        latest_after = json.loads((reviews_dir / "task_1.json").read_text(encoding="utf-8"))
        self.assertEqual(latest_after["decision"], "APPROVE")
        self.assertTrue((reviews_dir / "task_1" / "history" / "3").is_dir())


if __name__ == "__main__":
    unittest.main()
