from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import AuditFinding
from auto_developer.dispatcher import AuditDispatcher
from auto_developer.verifier import RedTestVerifier


class TestAuditDispatcher(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        self.plan_path = self.repo_dir / "PLAN.md"
        self.plan_path.write_text(
            """# PLAN.md

## ステップ
- [x] 1. Initial task
""",
            encoding="utf-8",
        )
        self.verifier = RedTestVerifier(repo_dir=self.repo_dir)
        self.dispatcher = AuditDispatcher(repo_dir=self.repo_dir, verifier=self.verifier)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_dispatch_verified_red_finding_adds_task_to_plan(self) -> None:
        """機能CCC.3.1 & CCC.3.2: 真正なRedを持つFindingが検証され、PLAN.mdに修正タスクとして登録されること。"""
        finding = AuditFinding(
            finding_id="FINDING-001",
            severity="P1",
            spec_reference="SPEC.md CCC.1",
            location="src/auto_developer/guard.py:45",
            description="AST check missing else branch validation",
            reproduction_test_code="""import unittest
class T(unittest.TestCase):
    def test_repro(self):
        self.assertEqual(1, 2)
""",
        )

        result_finding = self.dispatcher.dispatch_finding(finding, plan_path=self.plan_path)
        self.assertEqual(result_finding.status, "DISPATCHED")

        content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("- [ ] Fix: [FINDING-001] AST check missing else branch validation", content)
        self.assertIn("Severity: P1", content)
        self.assertIn("Loc: src/auto_developer/guard.py:45", content)

    def test_dispatch_rejects_non_genuine_red_finding(self) -> None:
        """機能CCC.3.1: 偽Red（成功するテストや構文エラー）はREJECTEDとなり、PLAN.mdは改変されないこと。"""
        passing_finding = AuditFinding(
            finding_id="FINDING-PASS",
            severity="P2",
            spec_reference="SPEC.md",
            location="app.py",
            description="Already passing dummy test",
            reproduction_test_code="""import unittest
class T(unittest.TestCase):
    def test_ok(self):
        self.assertTrue(True)
""",
        )

        result_finding = self.dispatcher.dispatch_finding(passing_finding, plan_path=self.plan_path)
        self.assertEqual(result_finding.status, "REJECTED")

        content = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("FINDING-PASS", content)

    def test_dispatch_idempotent_duplicate_finding_not_added(self) -> None:
        """機能CCC.3.2: 既にPLAN.mdに登録済みの指摘IDは二重登録されないこと。"""
        finding = AuditFinding(
            finding_id="FINDING-DUP",
            severity="P0",
            spec_reference="SPEC.md",
            location="verifier.py",
            description="Duplicate test",
            reproduction_test_code="""import unittest
class T(unittest.TestCase):
    def test_repro(self):
        self.assertEqual(1, 2)
""",
        )

        self.dispatcher.dispatch_finding(finding, plan_path=self.plan_path)
        first_content = self.plan_path.read_text(encoding="utf-8")

        # 2回目のディスパッチ
        self.dispatcher.dispatch_finding(finding, plan_path=self.plan_path)
        second_content = self.plan_path.read_text(encoding="utf-8")

        self.assertEqual(first_content, second_content)
        self.assertEqual(second_content.count("[FINDING-DUP]"), 1)

    def test_pipeline_process_findings_batch(self) -> None:
        """機能CCC.3.2: 複数Findingのバッチ処理でRedのみが登録されること。"""
        f_red = AuditFinding(
            finding_id="BATCH-001",
            severity="P1",
            spec_reference="SPEC.md",
            location="a.py",
            description="Genuine red finding",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertEqual(1, 2)",
        )
        f_pass = AuditFinding(
            finding_id="BATCH-002",
            severity="INFO",
            spec_reference="SPEC.md",
            location="b.py",
            description="Passing finding",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test_p(self):\n        self.assertTrue(True)",
        )

        dispatched = self.dispatcher.process_findings([f_red, f_pass], plan_path=self.plan_path)
        self.assertEqual(len(dispatched), 1)
        self.assertEqual(dispatched[0].finding_id, "BATCH-001")

        content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("BATCH-001", content)
        self.assertNotIn("BATCH-002", content)


if __name__ == "__main__":
    unittest.main()
