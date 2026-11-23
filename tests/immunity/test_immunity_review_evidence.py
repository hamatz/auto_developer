"""免疫テスト: 査読の証跡（SPEC.md GATE-5 / GATE-6 / LESSONS.md 2026-11-22）。

Phase 67〜69 で、Checker の出力が REJECT なのに、エージェントが APPROVE を記録してコミットしたものが4件あった。
原因は、バックグラウンドの Checker の完了通知が2回届き、1回目（APPROVE）の通知だけを見て
`record-review --decision APPROVE` と `commit-step` を実行したこと。
`auto-dev` は判定を自己申告で受け取っていたため、これを止められなかった。

ここでは、判定を Checker の出力ファイルから読み取り、差分・SPEC・順序に拘束することで、
次の抜け道がすべて塞がれていることを保証する。

- 申告した判定と出力が食い違う
- 記録した後に出力が書き換わる（今回の競合）
- 査読の記録がない
- 検証より前の査読
- 差分・SPEC の要約や取り違え

Phase 70 ステップ 2（`review-input` と新しい `record-review`）の完了後に、人間が tests/immunity/ に配置する。
ステップ 3（`commit-step` の照合）の Red になる。
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ORCH = Path(__file__).resolve().parents[2] / "scripts" / "nightly_orchestrator.py"

SPEC_TEXT = "# SPEC\n\n## 2. 要件\n- [ ] **APP-1**: `ok()` は True を返す。\n"
MAKER = "gemini-3.8-flash"
CHECKER = "claude-opus-5-5-medium"


class TestImmunityReviewEvidence(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        base = Path(self._tmp.name)
        self.repo = base / "repo"
        self.repo.mkdir()
        # Checker の入出力はリポジトリの外に置く（差分に混ざらないように）
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
        self.verify = self.repo / "verify.sh"
        self.verify.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.verify.chmod(0o755)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)
        self.checkpoint = self._head()
        (self.repo / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        self._verify_ok()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    # ---- helpers -------------------------------------------------------
    def _head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _orch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ORCH), "--plan", str(self.plan), *args],
            cwd=self.repo, capture_output=True, text=True, check=False, timeout=120,
        )

    def _verify_ok(self) -> None:
        res = self._orch(
            "--verify-script", str(self.verify),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1",
        )
        self.assertEqual(res.returncode, 0, f"verify-step failed: {res.stderr}")

    def _write_inputs(self, diff_text: str | None = None, spec_text: str | None = None) -> tuple[Path, Path]:
        """Checker に渡す入力を用意する。差分は `auto-dev review-input` が決まった形式で書き出す。"""
        if diff_text is None:
            res = self._orch("review-input", "--task-id", "1", "--out-dir", str(self.io))
            self.assertEqual(res.returncode, 0, f"review-input failed: {res.stderr}")
            diff_file = self.io / "diff.patch"
            self.assertTrue(diff_file.is_file(), "review-input must write diff.patch")
        else:
            diff_file = self.io / "diff_custom.patch"
            diff_file.write_text(diff_text, encoding="utf-8")
        spec_file = self.io / "spec_excerpt.md"
        spec_file.write_text(SPEC_TEXT if spec_text is None else spec_text, encoding="utf-8")
        return diff_file, spec_file

    def _write_output(self, body: str, name: str = "review.txt") -> Path:
        out = self.io / name
        out.write_text(body, encoding="utf-8")
        return out

    def _record(self, output: Path, *extra: str, diff_text: str | None = None,
                spec_text: str | None = None) -> subprocess.CompletedProcess[str]:
        diff_file, spec_file = self._write_inputs(diff_text, spec_text)
        return self._orch(
            "record-review", "--task-id", "1",
            "--checker-output", str(output), "--diff-file", str(diff_file), "--spec-file", str(spec_file),
            "--maker-model", MAKER, "--checker-model", CHECKER, *extra,
        )

    def _commit(self, *extra: str) -> subprocess.CompletedProcess[str]:
        return self._orch(
            "commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
            "--maker-model", MAKER, "--checker-model", CHECKER, "--checker-reason", "immunity", *extra,
        )

    def _assert_not_committed(self, res: subprocess.CompletedProcess[str], code: int = 4) -> None:
        self.assertEqual(res.returncode, code, f"stdout={res.stdout!r} stderr={res.stderr!r}")
        self.assertNotIn("COMMITTED", res.stdout)
        self.assertEqual(self._head(), self.checkpoint, "HEAD must not move")

    # ---- tests ---------------------------------------------------------
    def test_valid_review_from_output_file_allows_commit(self) -> None:
        out = self._write_output("All requirements met.\nDECISION: APPROVE\n")
        rec = self._record(out)
        self.assertEqual(rec.returncode, 0, rec.stderr)
        res = self._commit()
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertIn("COMMITTED", res.stdout)

    def test_commit_without_any_review_is_rejected(self) -> None:
        res = self._commit("--checker-decision", "APPROVE")
        self._assert_not_committed(res)
        self.assertIn("CHECKER_POLICY_VIOLATION", res.stderr)

    def test_declared_approve_with_reject_output_is_rejected_at_record(self) -> None:
        out = self._write_output("Exception escapes on overflow.\nDECISION: REJECT\n")
        rec = self._record(out, "--decision", "APPROVE")
        self.assertEqual(rec.returncode, 4, rec.stderr)
        self.assertIn("CHECKER_POLICY_VIOLATION", rec.stderr)
        self._assert_not_committed(self._commit())

    def test_reject_output_cannot_be_committed_even_if_commit_declares_approve(self) -> None:
        out = self._write_output("Missing boundary tests.\nDECISION: REJECT\n")
        self.assertEqual(self._record(out).returncode, 0)
        self._assert_not_committed(self._commit("--checker-decision", "APPROVE"))

    def test_output_rewritten_after_record_blocks_commit(self) -> None:
        """今回の競合: APPROVE の途中出力で記録した後、Checker が最終的に REJECT を書いた。"""
        out = self._write_output("Looks fine so far.\nDECISION: APPROVE\n")
        self.assertEqual(self._record(out).returncode, 0)
        out.write_text("Looks fine so far.\nDECISION: APPROVE\n\nOn closer look...\nDECISION: REJECT\n",
                       encoding="utf-8")
        self._assert_not_committed(self._commit())

    def test_output_without_single_decision_line_is_not_accepted(self) -> None:
        for body in ("Still reviewing...\n", "DECISION: APPROVE\nDECISION: REJECT\n", "APPROVE: looks good\n"):
            with self.subTest(body=body):
                out = self._write_output(body, name="partial.txt")
                rec = self._record(out)
                self.assertEqual(rec.returncode, 4, f"body={body!r} stderr={rec.stderr}")
                self._assert_not_committed(self._commit())

    def test_review_before_latest_verify_is_stale(self) -> None:
        out = self._write_output("OK\nDECISION: APPROVE\n")
        self.assertEqual(self._record(out).returncode, 0)
        # 査読の後に作業ツリーを変えて検証をやり直した: 古い査読は新しい検証に使えない
        (self.repo / "app.py").write_text("def ok():\n    return 1 == 1\n", encoding="utf-8")
        self._verify_ok()
        self._assert_not_committed(self._commit())

    def test_diff_file_that_does_not_match_the_tree_is_rejected(self) -> None:
        out = self._write_output("OK\nDECISION: APPROVE\n")
        rec = self._record(out, diff_text="diff --git a/other.py b/other.py\n+x = 1\n")
        self.assertEqual(rec.returncode, 3, rec.stderr)
        self._assert_not_committed(self._commit())

    def test_summarized_spec_is_rejected(self) -> None:
        out = self._write_output("OK\nDECISION: APPROVE\n")
        rec = self._record(out, spec_text="APP-1: ok() should be truthy (summary)\n")
        self.assertEqual(rec.returncode, 4, rec.stderr)
        self._assert_not_committed(self._commit())

    def test_commit_models_must_match_the_review(self) -> None:
        out = self._write_output("OK\nDECISION: APPROVE\n")
        self.assertEqual(self._record(out).returncode, 0)
        res = self._orch(
            "commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
            "--maker-model", MAKER, "--checker-model", "claude-sonnet-5-5", "--checker-reason", "x",
        )
        self._assert_not_committed(res)


if __name__ == "__main__":
    unittest.main()
