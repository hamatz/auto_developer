from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import (
    AuditFinding,
    AuditResolutionManager,
)
from auto_developer.audit_store import AuditStore


class TestAuditLessonRecording(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        self.src_dir = self.repo_dir / "src"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.immunity_dir = self.repo_dir / "tests" / "immunity"
        self.immunity_dir.mkdir(parents=True, exist_ok=True)

        self.lessons_path = self.repo_dir / "LESSONS.md"
        self.lessons_path.write_text(
            "# Repository Lessons Learned\n\n"
            "## 恒久教訓インデックス\n\n"
            "### [2026-11-01] Previous Lesson\n"
            "- Context: previous\n",
            encoding="utf-8",
        )

        self.audit_store = AuditStore(repo_dir=self.repo_dir)
        self.manager = AuditResolutionManager(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_record_lesson_from_finding(self) -> None:
        """機能EEE.4: 解決されたAUD指摘から LESSONS.md に構造化された教訓が自動追記されること。"""
        finding = AuditFinding(
            finding_id="BUG-REC-001",
            severity="P1",
            spec_reference="SPEC.md 2.2",
            location="src/module.py:15",
            description="Buffer overflow edge case",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase): pass",
            status="RESOLVED",
        )
        proof = {
            "finding_id": "BUG-REC-001",
            "before_status": "VERIFIED_RED",
            "after_status": "GREEN",
            "immunity_test_path": "tests/immunity/test_immunity_BUG_REC_001.py",
        }

        ok = self.manager.record_lesson(finding, proof, lessons_path=self.lessons_path)
        self.assertTrue(ok)

        content = self.lessons_path.read_text(encoding="utf-8")
        self.assertIn("[BUG-REC-001]", content)
        self.assertIn("Buffer overflow edge case", content)
        self.assertIn("tests/immunity/test_immunity_BUG_REC_001.py", content)
        self.assertIn("Verified Before Red (VERIFIED_RED) and After Green (GREEN)", content)

        # 二重追記防止 (Idempotency)
        ok_again = self.manager.record_lesson(finding, proof, lessons_path=self.lessons_path)
        self.assertTrue(ok_again)
        self.assertEqual(content.count("[BUG-REC-001]"), 1)

    def test_full_self_improvement_e2e_loop(self) -> None:
        """機能EEE.4: Level 7 完全自己改善ループの実証（指摘→Red検証→PLAN登録→Maker修正→After Green検証→免疫昇格→教訓記録）。"""
        from auto_developer.dispatcher import AuditDispatcher, TaskDispatcher

        # 1. バグのあるモジュール作成 (アサーション失敗を起こすバグ)
        calc_py = self.src_dir / "calc.py"
        calc_py.write_text(
            "def calc_div(a: int, b: int) -> int:\n"
            "    if b == 0:\n"
            "        return -999\n"
            "    return a // b\n",
            encoding="utf-8",
        )

        # 2. 監査指摘の作成
        repro_test = (
            "import unittest\n"
            "from calc import calc_div\n\n"
            "class TestCalc(unittest.TestCase):\n"
            "    def test_zero_div(self):\n"
            "        self.assertEqual(calc_div(10, 0), 0)\n"
        )
        finding = AuditFinding(
            finding_id="BUG-CALC-ZERO",
            severity="P0",
            spec_reference="SPEC.md 2.5",
            location="src/calc.py:2",
            description="calc_div returns incorrect code on 0 divisor",
            reproduction_test_code=repro_test,
        )

        # 3. 監査ディスパッチ（Red検証 & PLAN.md 登録 & AuditStore 永続化）
        plan_path = self.repo_dir / "PLAN.md"
        plan_path.write_text("# PLAN.md\n\n## ステップ\n", encoding="utf-8")

        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, audit_store=self.audit_store)
        dispatched = dispatcher.dispatch_finding(finding, plan_path=plan_path)
        self.assertEqual(dispatched.status, "DISPATCHED")

        # 4. TaskDispatcher が次のタスクとして取得
        task_disp = TaskDispatcher()
        tasks = task_disp.parse_plan(plan_path)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0].task_id, "AUD-BUG-CALC-ZERO")

        # 5. Maker による修正
        calc_py.write_text(
            "def calc_div(a: int, b: int) -> int:\n"
            "    if b == 0:\n"
            "        return 0\n"
            "    return a // b\n",
            encoding="utf-8",
        )

        # 6. AuditResolutionManager による解決（After Green検証 + 免疫テスト昇格 + 教訓記録）
        res = self.manager.resolve_task(tasks[0].task_id)
        self.assertTrue(res["success"])

        # 7. 教訓が LESSONS.md に記録されたこと
        self.manager.record_lesson(
            self.audit_store.get_finding("BUG-CALC-ZERO"),  # type: ignore[arg-type]
            res["proof"],
            lessons_path=self.lessons_path,
        )
        lessons_text = self.lessons_path.read_text(encoding="utf-8")
        self.assertIn("[BUG-CALC-ZERO]", lessons_text)
        self.assertIn("tests/immunity/test_immunity_BUG_CALC_ZERO.py", lessons_text)


if __name__ == "__main__":
    unittest.main()
