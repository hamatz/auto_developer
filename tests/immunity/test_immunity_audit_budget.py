"""免疫テスト草案: 監査サイクルの終了保証（SPEC.md 機能WWW / LESSONS.md 2026-11-16）。

Phase 63 のステップ完了後、人間が tests/immunity/ に配置する。
監査役が指摘を返し続けても、開発ループが終わること（P2 は実行タスクにならない、
同一モジュールへの P0/P1 自動登録は上限で打ち切られる）を機械的に保証する。
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from dataclasses import dataclass
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[2] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import AuditFinding
from auto_developer.audit_store import AuditStore
from auto_developer.dispatcher import AuditDispatcher, TaskDispatcher
from auto_developer.runner import CommandRunnerAdapter

PLAN_TEMPLATE = """# PLAN.md

## ステップ
- [x] 1. 既存の完了タスク

## 止まっていること・判断待ち
- なし。
"""

REPRO = "import unittest\n\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail('x')\n"


def find_release_blockers(plan_text: str) -> list[str]:
    # 遅延 import: 未実装でも他のテストが個別に Red/Green を示せるようにする
    from auto_developer.bundler import find_release_blockers as impl

    return impl(plan_text)


@dataclass
class _FakeResult:
    is_reproduced: bool = True
    exit_code: int = 1
    reason: str = "GENUINE_RED"
    output: str = "fake"


class _CountingVerifier:
    """常に GENUINE_RED を返し、呼び出し回数を数える偽 RedTestVerifier。"""

    def __init__(self) -> None:
        self.calls = 0

    def verify_candidate_test(self, code: str) -> _FakeResult:
        self.calls += 1
        return _FakeResult()


class TestImmunityAuditLoopTermination(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.plan = self.repo / "PLAN.md"
        self.plan.write_text(PLAN_TEMPLATE, encoding="utf-8")
        self.verifier = _CountingVerifier()
        self._seq = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _dispatcher(self, max_cycles: int) -> AuditDispatcher:
        return AuditDispatcher(
            repo_dir=self.repo,
            verifier=self.verifier,  # type: ignore[arg-type]
            audit_store=AuditStore(audit_dir=self.repo / ".dev" / "audit"),
            max_consecutive_cycles=max_cycles,
        )

    def _finding(self, severity: str, location: str = "src/auto_developer/verifier.py:166") -> AuditFinding:
        self._seq += 1
        return AuditFinding(
            finding_id=f"AUD-{9000 + self._seq}",
            severity=severity,
            spec_reference="SPEC.md 機能WWW",
            location=location,
            description=f"edge case #{self._seq}",
            reproduction_test_code=REPRO + f"# {self._seq}\n",
        )

    def _pending_tasks(self) -> list[str]:
        tasks = TaskDispatcher().parse_plan(self.plan)
        return [t.task_id for t in tasks if not t.is_completed and not t.is_failed]

    def test_endless_p2_stream_never_becomes_plan_task(self) -> None:
        dispatcher = self._dispatcher(max_cycles=5)
        for cycle in range(20):
            findings = [self._finding("P2") for _ in range(3)]
            dispatcher.process_findings(findings, plan_path=self.plan, cycle_id=f"c{cycle}")
            for f in findings:
                self.assertEqual(f.status, "BACKLOGGED")

        self.assertEqual(self._pending_tasks(), [], "P2 must never become a Maker task")
        self.assertEqual(self.verifier.calls, 0, "P2 must not spend Red verification")
        backlog = (self.repo / "BACKLOG.md").read_text(encoding="utf-8")
        self.assertEqual(backlog.count("(Severity: P2"), 60)
        self.assertNotIn("- [ ]", backlog)
        self.assertEqual(find_release_blockers(self.plan.read_text(encoding="utf-8")), [])

    def test_p1_stream_on_same_module_escalates_after_budget(self) -> None:
        dispatcher = self._dispatcher(max_cycles=3)
        statuses = []
        for cycle in range(6):
            f = self._finding("P1")
            dispatcher.process_findings([f], plan_path=self.plan, cycle_id=f"c{cycle}")
            statuses.append(f.status)

        self.assertEqual(statuses, ["DISPATCHED"] * 3 + ["ESCALATED"] * 3)
        self.assertEqual(len(self._pending_tasks()), 3, "auto-registration must stop at the budget")
        plan_text = self.plan.read_text(encoding="utf-8")
        self.assertEqual(plan_text.count("- ESCALATED ["), 3)
        self.assertTrue(find_release_blockers(plan_text), "escalated P1 must block release")

    def test_streak_resets_after_cycle_without_findings_for_module(self) -> None:
        dispatcher = self._dispatcher(max_cycles=2)
        a, b = self._finding("P1"), self._finding("P1")
        dispatcher.process_findings([a], plan_path=self.plan, cycle_id="c1")
        dispatcher.process_findings([b], plan_path=self.plan, cycle_id="c2")
        other = self._finding("P1", location="src/auto_developer/bundler.py:10")
        dispatcher.process_findings([other], plan_path=self.plan, cycle_id="c3")
        c = self._finding("P1")
        dispatcher.process_findings([c], plan_path=self.plan, cycle_id="c4")
        self.assertEqual([a.status, b.status, other.status, c.status], ["DISPATCHED"] * 4)

    def test_reprocessing_same_cycle_does_not_double_count(self) -> None:
        dispatcher = self._dispatcher(max_cycles=2)
        first = self._finding("P1")
        dispatcher.process_findings([first], plan_path=self.plan, cycle_id="c1")
        dispatcher.process_findings([first], plan_path=self.plan, cycle_id="c1")
        second = self._finding("P1")
        dispatcher.process_findings([second], plan_path=self.plan, cycle_id="c2")
        third = self._finding("P1")
        dispatcher.process_findings([third], plan_path=self.plan, cycle_id="c3")
        self.assertEqual(second.status, "DISPATCHED")
        self.assertEqual(third.status, "ESCALATED")

    # --- 監査役が毎サイクル ID を振り直す（AUD-001 から再採番する）現実の挙動 (WWW.6) ---
    def _reused(self, severity: str, n: int, finding_id: str = "AUD-001") -> AuditFinding:
        return AuditFinding(
            finding_id=finding_id,
            severity=severity,
            spec_reference="SPEC.md 機能WWW",
            location="src/auto_developer/verifier.py:166",
            description=f"reused-id issue #{n}",
            reproduction_test_code=REPRO + f"# reused {n}\n",
        )

    def test_reused_finding_id_across_cycles_is_backlogged_not_crashed(self) -> None:
        dispatcher = self._dispatcher(max_cycles=5)
        findings = []
        for cycle in range(3):
            f = self._reused("P2", cycle)
            dispatcher.process_findings([f], plan_path=self.plan, cycle_id=f"c{cycle}")
            findings.append(f)

        self.assertEqual([f.status for f in findings], ["BACKLOGGED"] * 3)
        self.assertEqual(len({f.finding_id for f in findings}), 3, "colliding IDs must be re-numbered")
        backlog = (self.repo / "BACKLOG.md").read_text(encoding="utf-8")
        for n in range(3):
            self.assertIn(f"reused-id issue #{n}", backlog, "a different finding must not be dropped")

    def test_identical_rereport_is_not_duplicated_in_backlog(self) -> None:
        dispatcher = self._dispatcher(max_cycles=5)
        for cycle in range(3):
            dispatcher.process_findings([self._reused("P2", 0)], plan_path=self.plan, cycle_id=f"c{cycle}")
        backlog = (self.repo / "BACKLOG.md").read_text(encoding="utf-8")
        self.assertEqual(backlog.count("reused-id issue #0"), 1)

    def test_reused_finding_id_still_escalates_after_budget(self) -> None:
        dispatcher = self._dispatcher(max_cycles=1)
        findings = []
        for cycle in range(3):
            f = self._reused("P1", cycle)
            dispatcher.process_findings([f], plan_path=self.plan, cycle_id=f"c{cycle}")
            findings.append(f)

        self.assertEqual([f.status for f in findings], ["DISPATCHED", "ESCALATED", "ESCALATED"])
        self.assertEqual(len({f.finding_id for f in findings}), 3)
        plan_text = self.plan.read_text(encoding="utf-8")
        self.assertEqual(plan_text.count("- ESCALATED ["), 2)
        self.assertEqual(len(self._pending_tasks()), 1)

    def test_release_blockers_ignore_p2_and_catch_p0_p1(self) -> None:
        plan_text = (
            "## ステップ\n"
            "- [ ] AUD-1. Fix: [AUD-1] a (Severity: P2, Loc: x.py)\n"
            "- [x] AUD-2. Fix: [AUD-2] b (Severity: P1, Loc: x.py)\n"
        )
        self.assertEqual(find_release_blockers(plan_text), [])
        plan_text += "- [ ] AUD-3. Fix: [AUD-3] c (Severity: P1, Loc: x.py)\n"
        plan_text += "- [ ] AUD-4. Fix: [AUD-4] d (Severity: P0, Loc: x.py)\n"
        self.assertEqual(len(find_release_blockers(plan_text)), 2)


class TestImmunityCheckerTextFallbackFailClosed(unittest.TestCase):
    def _decide(self, text: str) -> bool:
        adapter = CommandRunnerAdapter(checker_cmd_template=f"printf '%b\\n' {text!r}", timeout_sec=30)
        approved, _ = adapter.run_checker(task=_task(), diff="", spec_content="")
        return approved

    def test_pass_substring_is_not_approval(self) -> None:
        self.assertFalse(self._decide("tests did not pass"))
        self.assertFalse(self._decide("BYPASS detected"))

    def test_conflicting_or_missing_decision_is_rejected(self) -> None:
        self.assertFalse(self._decide("APPROVE\nREJECT"))
        self.assertFalse(self._decide("looks fine to me"))

    def test_single_explicit_decision_line_is_honored(self) -> None:
        self.assertTrue(self._decide("review notes...\nDECISION: APPROVE"))
        self.assertFalse(self._decide("review notes...\nREJECT"))


def _task():  # type: ignore[no-untyped-def]
    from auto_developer.models import Task

    return Task(task_id="1", title="t", line_number=0, is_completed=False, is_failed=False)


if __name__ == "__main__":
    unittest.main()
