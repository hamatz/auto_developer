"""免疫テスト: commit-step の Checker 方針検査（SPEC.md 機能XXX.1 / AGENTS.md Rule 6）。

LESSONS.md 2026-11-12「Checker を Maker と同じ系統で起動していた」は手順書（道具）止まりで、
Phase 63 ステップ 1 で再発した（Checker = `pro` = Gemini 2.5 Pro）。
また commit-step は Checker が REJECT でもコミットできた。これらを関門として機械的に塞ぐ。
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[2] / "scripts" / "nightly_orchestrator.py"


class TestImmunityCheckerPolicyAtCommitStep(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.name", "Tester"],
            ["git", "config", "user.email", "tester@example.com"],
        ):
            subprocess.run(cmd, cwd=self.repo, check=True, capture_output=True)
        self.plan = self.repo / "PLAN.md"
        self.plan.write_text("# PLAN\n\n## ステップ\n- [ ] 1. テストタスク\n", encoding="utf-8")
        self.verify = self.repo / "verify.sh"
        self.verify.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.verify.chmod(0o755)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)
        self.checkpoint = self._head()

        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        res = self._orch(
            "--verify-script", str(self.verify),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1",
        )
        self.assertEqual(res.returncode, 0, f"verify-step failed: {res.stderr}")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _orch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ORCH), "--plan", str(self.plan), *args],
            cwd=self.repo, capture_output=True, text=True, check=False, timeout=120,
        )

    def _commit(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self._orch(
            "commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
            "--checker-reason", "immunity test", *extra,
        )

    def _assert_rejected_without_side_effects(self, res: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(res.returncode, 4, f"stdout={res.stdout!r} stderr={res.stderr!r}")
        self.assertIn("CHECKER_POLICY_VIOLATION", res.stderr)
        self.assertNotIn("COMMITTED", res.stdout)
        self.assertEqual(self._head(), self.checkpoint, "HEAD must not move on policy violation")
        # Evidence は消費されていない: 正しい引数で再実行すればコミットできる
        ok = self._commit(
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "APPROVE",
        )
        self.assertEqual(ok.returncode, 0, f"evidence must remain usable: {ok.stderr}")
        self.assertIn("COMMITTED", ok.stdout)

    def test_same_family_checker_is_rejected(self) -> None:
        res = self._commit(
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "gemini-2.5-pro",
            "--checker-decision", "APPROVE",
        )
        self._assert_rejected_without_side_effects(res)

    def test_unknown_family_checker_is_rejected(self) -> None:
        # Phase 63 ステップ 1 の実例: Model: "pro"
        res = self._commit(
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "pro",
            "--checker-decision", "APPROVE",
        )
        self._assert_rejected_without_side_effects(res)

    def test_missing_models_do_not_fall_back_to_defaults(self) -> None:
        res = self._commit("--checker-decision", "APPROVE")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("COMMITTED", res.stdout)
        self.assertEqual(self._head(), self.checkpoint)

    def test_rejected_review_cannot_be_committed(self) -> None:
        res = self._commit(
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "REJECT",
        )
        self._assert_rejected_without_side_effects(res)

    def test_valid_cross_family_approval_is_committed_with_structured_record(self) -> None:
        res = self._commit(
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "approve",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("COMMITTED", res.stdout)
        self.assertNotEqual(self._head(), self.checkpoint)

        record = json.loads(
            (self.repo / ".git" / "nightly_evidence" / "records" / "task_1.json").read_text(encoding="utf-8")
        )
        checker = record["checker"]
        self.assertEqual(checker["model"], "claude-opus-5-5-medium")
        self.assertEqual(checker["family"], "claude")
        self.assertEqual(checker["maker_model"], "gemini-3.8-flash")
        self.assertEqual(checker["maker_family"], "gemini")
        self.assertEqual(str(checker["decision"]).upper(), "APPROVE")


if __name__ == "__main__":
    unittest.main()
