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
    "- [ ] **APP-2**: `val()` は 42 を返す。\n"
)


class TestGate5ReviewEvidence(unittest.TestCase):
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
        (self.repo / ".gitignore").write_text("*.ignored\n", encoding="utf-8")
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

    def test_review_input_requires_valid_evidence(self) -> None:
        res = self._orch("review-input", "--task-id", "1", "--out-dir", str(self.io))
        self.assertEqual(res.returncode, 3, f"stdout={res.stdout!r} stderr={res.stderr!r}")

    def test_review_input_writes_canonical_diff_including_untracked_without_mutating_index(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        (self.repo / "skip.ignored").write_text("ignored\n", encoding="utf-8")
        self._verify_ok()

        index_before = (self.repo / ".git" / "index").read_bytes()
        res = self._orch("review-input", "--task-id", "1", "--out-dir", str(self.io))
        self.assertEqual(res.returncode, 0, res.stderr)
        index_after = (self.repo / ".git" / "index").read_bytes()
        self.assertEqual(index_before, index_after, "git index must not be modified")

        diff_file = self.io / "diff.patch"
        self.assertTrue(diff_file.is_file())
        diff_text = diff_file.read_text(encoding="utf-8")
        self.assertIn("app.py", diff_text)
        self.assertNotIn("skip.ignored", diff_text)

    def test_legacy_record_review_without_checker_output_is_rejected_with_exit_2(self) -> None:
        res = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--decision",
            "APPROVE",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5-5-medium",
        )
        self.assertEqual(res.returncode, 2, f"stdout={res.stdout!r} stderr={res.stderr!r}")
        self.assertIn("--checker-output", res.stderr)

    def test_record_review_requires_valid_evidence(self) -> None:
        out = self.io / "review.txt"
        out.write_text("OK\nDECISION: APPROVE\n", encoding="utf-8")
        diff_file = self.io / "diff.patch"
        diff_file.write_text("", encoding="utf-8")
        spec_file = self.io / "spec.md"
        spec_file.write_text(SPEC_TEXT, encoding="utf-8")

        res = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(out),
            "--diff-file",
            str(diff_file),
            "--spec-file",
            str(spec_file),
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5-5-medium",
        )
        self.assertEqual(res.returncode, 3, res.stderr)

    def test_record_review_rejects_invalid_checker_output_and_decision_mismatch(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(self.io)).returncode, 0)
        diff_file = self.io / "diff.patch"
        spec_file = self.io / "spec.md"
        spec_file.write_text("- [ ] **APP-1**: `ok()` は True を返す。\n", encoding="utf-8")

        for body in ("No decision here\n", "DECISION: APPROVE\nDECISION: REJECT\n", "APPROVE: inline\n"):
            out = self.io / "bad_review.txt"
            out.write_text(body, encoding="utf-8")
            res = self._orch(
                "record-review",
                "--task-id",
                "1",
                "--checker-output",
                str(out),
                "--diff-file",
                str(diff_file),
                "--spec-file",
                str(spec_file),
                "--maker-model",
                "gemini-3.8-flash",
                "--checker-model",
                "claude-opus-5-5-medium",
            )
            self.assertEqual(res.returncode, 4, f"body={body!r} stderr={res.stderr}")
            self.assertIn("CHECKER_POLICY_VIOLATION:", res.stderr)

        # --decision mismatch
        out_rej = self.io / "rej.txt"
        out_rej.write_text("Bug found.\nDECISION: REJECT\n", encoding="utf-8")
        res_mis = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(out_rej),
            "--diff-file",
            str(diff_file),
            "--spec-file",
            str(spec_file),
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5-5-medium",
            "--decision",
            "APPROVE",
        )
        self.assertEqual(res_mis.returncode, 4, res_mis.stderr)
        self.assertIn("CHECKER_POLICY_VIOLATION:", res_mis.stderr)
        self.assertFalse((self.repo / ".git" / "nightly_evidence" / "reviews" / "task_1.json").exists())

    def test_record_review_rejects_diff_mismatch_and_spec_summary(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(self.io)).returncode, 0)
        diff_file = self.io / "diff.patch"
        spec_file = self.io / "spec.md"
        spec_file.write_text(SPEC_TEXT, encoding="utf-8")
        out = self.io / "ok.txt"
        out.write_text("OK\nDECISION: APPROVE\n", encoding="utf-8")

        # 1-byte diff mismatch -> exit 3
        bad_diff = self.io / "bad_diff.patch"
        bad_diff.write_bytes(diff_file.read_bytes() + b" ")
        res_diff = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(out),
            "--diff-file",
            str(bad_diff),
            "--spec-file",
            str(spec_file),
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-opus-5-5-medium",
        )
        self.assertEqual(res_diff.returncode, 3, res_diff.stderr)

        # empty or summarized spec -> exit 4
        for bad_spec_text in ("", "   \n", "APP-1 summary\n"):
            bad_spec = self.io / "bad_spec.md"
            bad_spec.write_text(bad_spec_text, encoding="utf-8")
            res_spec = self._orch(
                "record-review",
                "--task-id",
                "1",
                "--checker-output",
                str(out),
                "--diff-file",
                str(diff_file),
                "--spec-file",
                str(bad_spec),
                "--maker-model",
                "gemini-3.8-flash",
                "--checker-model",
                "claude-opus-5-5-medium",
            )
            self.assertEqual(res_spec.returncode, 4, res_spec.stderr)

    def test_record_review_records_hashes_copies_reject_and_same_family_without_consuming_evidence(self) -> None:
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(self.io)).returncode, 0)
        diff_file = self.io / "diff.patch"
        spec_file = self.io / "spec.md"
        spec_file.write_text("- [ ] **APP-1**: `ok()` は True を返す。\n", encoding="utf-8")
        out = self.io / "review.txt"
        out.write_text("Needs work.\nDECISION: REJECT\n", encoding="utf-8")

        # Same family is allowed in record-review (GATE-4 check happens in commit-step)
        res = self._orch(
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(out),
            "--diff-file",
            str(diff_file),
            "--spec-file",
            str(spec_file),
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "gemini-2.5-pro",
        )
        self.assertEqual(res.returncode, 0, res.stderr)

        ev_dir = self.repo / ".git" / "nightly_evidence"
        # Evidence is NOT consumed
        self.assertTrue((ev_dir / "task_1.json").is_file())
        ev_data = json.loads((ev_dir / "task_1.json").read_text(encoding="utf-8"))

        review_record = json.loads((ev_dir / "reviews" / "task_1.json").read_text(encoding="utf-8"))
        self.assertEqual(review_record["decision"], "REJECT")
        self.assertEqual(review_record["evidence_token"], ev_data["token"])
        self.assertEqual(
            review_record["checker_output_sha256"],
            hashlib.sha256(out.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            review_record["diff_sha256"],
            hashlib.sha256(diff_file.read_bytes()).hexdigest(),
        )
        self.assertEqual(
            review_record["spec_sha256"],
            hashlib.sha256(spec_file.read_bytes()).hexdigest(),
        )
        # Copies exist in Evidence dir
        copies = review_record["copies"]
        self.assertTrue(Path(copies["checker_output"]).is_file())
        self.assertTrue(Path(copies["diff"]).is_file())
        self.assertTrue(Path(copies["spec"]).is_file())


if __name__ == "__main__":
    unittest.main()
