"""免疫テスト草案: 計測の非干渉（SPEC.md 機能XXX.3 / 不変条件 5）。

Phase 64 ステップ 3 の完了後に人間が tests/immunity/ に配置する（見張り役。Red-First ではない）。
イベントログの書き込みに失敗しても、検証・コミットの結果が計測なしの場合と同一であることを保証する。

Phase 70（GATE-5 / GATE-6）で commit-step の前に record-review が必要になった。
査読の記録（ゲートの証跡）は計測とは別の場所に保存され、イベントログの失敗に影響されないことも確かめる。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[2] / "scripts" / "nightly_orchestrator.py"


class TestImmunityMetricsNonInterference(unittest.TestCase):
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
        self.plan.write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 99: test\n\n## ステップ\n- [ ] 1. テストタスク\n", encoding="utf-8"
        )
        self.ok_sh = self.repo / "ok.sh"
        self.ok_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.spec_text = "# SPEC\n\n- [ ] **APP-1**: x は 1 である。\n"
        (self.repo / "SPEC.md").write_text(self.spec_text, encoding="utf-8")
        self.ng_sh = self.repo / "ng.sh"
        self.ng_sh.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        for p in (self.ok_sh, self.ng_sh):
            p.chmod(0o755)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)
        self.checkpoint = self._head()
        (self.repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.io = Path(self._tmp.name) / "checker_io"
        self.io.mkdir()

        # イベントログの書き込み先をディレクトリで塞ぐ（書き込みが必ず失敗する）
        blocked = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        blocked.mkdir(parents=True)

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

    def _review(self, decision: str, checker_model: str) -> subprocess.CompletedProcess[str]:
        res = self._orch("review-input", "--task-id", "1", "--out-dir", str(self.io))
        self.assertEqual(res.returncode, 0, f"review-input failed: {res.stderr}")
        spec_file = self.io / "spec_excerpt.md"
        spec_file.write_text(self.spec_text, encoding="utf-8")
        out = self.io / "review.txt"
        out.write_text(f"immunity\nDECISION: {decision}\n", encoding="utf-8")
        return self._orch(
            "record-review", "--task-id", "1",
            "--checker-output", str(out), "--diff-file", str(self.io / "diff.patch"),
            "--spec-file", str(spec_file),
            "--maker-model", "gemini-3.8-flash", "--checker-model", checker_model,
        )

    def test_failed_verification_still_fails_when_metrics_cannot_be_written(self) -> None:
        res = self._orch("--verify-script", str(self.ng_sh), "verify-step",
                         "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertNotEqual(res.returncode, 0)
        self.assertNotIn("VERIFICATION PASSED", res.stdout)

    def test_pass_and_commit_are_unchanged_when_metrics_cannot_be_written(self) -> None:
        res = self._orch("--verify-script", str(self.ok_sh), "verify-step",
                         "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("METRICS_WARNING", res.stderr)

        rec = self._review("APPROVE", "claude-opus-5-5-medium")
        self.assertEqual(rec.returncode, 0, rec.stderr)  # 査読の記録はイベントログの失敗に影響されない

        res = self._orch("commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
                         "--maker-model", "gemini-3.8-flash", "--checker-model", "claude-opus-5-5-medium",
                         "--checker-decision", "APPROVE", "--checker-reason", "immunity")
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("COMMITTED", res.stdout)
        self.assertNotEqual(self._head(), self.checkpoint)

    def test_policy_violation_is_unchanged_when_metrics_cannot_be_written(self) -> None:
        self._orch("--verify-script", str(self.ok_sh), "verify-step",
                   "--checkpoint", self.checkpoint, "--task-id", "1")
        self._review("APPROVE", "gemini-2.5-pro")
        res = self._orch("commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
                         "--maker-model", "gemini-3.8-flash", "--checker-model", "gemini-2.5-pro",
                         "--checker-decision", "APPROVE", "--checker-reason", "immunity")
        self.assertEqual(res.returncode, 4)
        self.assertEqual(self._head(), self.checkpoint)


if __name__ == "__main__":
    unittest.main()
