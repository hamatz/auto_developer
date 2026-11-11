from __future__ import annotations

import subprocess
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
    LessonEntry,
    LessonInjector,
)
from auto_developer.audit_store import AuditStore
from auto_developer.dispatcher import AuditDispatcher, TaskDispatcher
from auto_developer.verifier import RedTestVerifier


class TestLessonInjection(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        self.src_dir = self.repo_dir / "src"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.immunity_dir = self.repo_dir / "tests" / "immunity"
        self.immunity_dir.mkdir(parents=True, exist_ok=True)

        self.lessons_md = self.repo_dir / "LESSONS.md"
        self.lessons_md.write_text(
            """# Repository Lessons Learned

## 恒久教訓インデックス

### [2026-11-09] [BUG-ZERO-DIV] Divide by zero in safe_div
- Context: `src/app.py` (safe_div)
- Trap: `safe_div` crashed with ZeroDivisionError when denominator is 0
- Rule: Always check `b == 0` before integer division
- Rung: 作り & 検査
- Immunity Test: `tests/immunity/test_immunity_BUG_ZERO_DIV.py`
- Proof: Before Red verified, After Green verified

### [2026-11-08] [SECURITY-CHMOD] OS permission bypass via chmod
- Context: `src/auto_developer/verifier.py`
- Trap: Test processes could regain write permission via os.chmod
- Rule: Use physical clone isolation instead of in-place chmod
- Rung: 作り
- Immunity Test: `tests/test_red_verifier.py`
- Proof: Verified isolation in temporary clones
""",
            encoding="utf-8",
        )

        self.injector = LessonInjector(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_parse_lessons(self) -> None:
        """機能FFF.4: LESSONS.md から教訓エントリを構造化パースできること。"""
        entries = self.injector.parse_lessons()
        self.assertEqual(len(entries), 2)
        self.assertIsInstance(entries[0], LessonEntry)
        self.assertIn("BUG-ZERO-DIV", entries[0].title)
        self.assertIn("safe_div", entries[0].context)
        self.assertIn("ZeroDivisionError", entries[0].trap)
        self.assertIn("Always check", entries[0].rule)
        self.assertIn("SECURITY-CHMOD", entries[1].title)

    def test_find_relevant_lessons_by_keyword(self) -> None:
        """機能FFF.4: タスク名やキーワードから関連教訓を優先度付きで抽出できること。"""
        lessons = self.injector.find_relevant_lessons("Fix: [BUG-ZERO-DIV] safe_div crash on zero")
        self.assertTrue(len(lessons) >= 1)
        self.assertIn("BUG-ZERO-DIV", lessons[0].title)

        chmod_lessons = self.injector.find_relevant_lessons("verifier clone isolation security chmod")
        self.assertTrue(len(chmod_lessons) >= 1)
        self.assertIn("SECURITY-CHMOD", chmod_lessons[0].title)

    def test_format_injection(self) -> None:
        """機能FFF.4: 抽出された教訓が Maker 向け Markdown ブロックに整形されること。"""
        lessons = self.injector.find_relevant_lessons("safe_div")
        formatted = self.injector.format_injection(lessons)
        self.assertIn("## 関連する教訓・Traps (from LESSONS.md / Rule 10)", formatted)
        self.assertIn("ZeroDivisionError", formatted)
        self.assertIn("Always check", formatted)

    def test_e2e_closed_self_improvement_loop(self) -> None:
        """機能FFF.4: 外部監査 → Red実証 → PLAN登録 → Maker修正 → After Green → 免疫昇格 → LESSONS追記 → 次回Maker教訓注入の完全閉ループ。"""
        # Git リポジトリ初期化 (HHH.1)
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AutoDev Test"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "autodev@example.com"], cwd=self.repo_dir, check=True, capture_output=True)

        app_file = self.src_dir / "calculator.py"
        app_file.write_text(
            "def calculate_ratio(a: int, b: int) -> float:\n"
            "    return a / b\n",
            encoding="utf-8",
        )

        plan_file = self.repo_dir / "PLAN.md"
        plan_file.write_text("# PLAN.md\n\n## ステップ\n", encoding="utf-8")

        # Initial commit with seed bug
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit with seed bug"], cwd=self.repo_dir, check=True, capture_output=True)

        audit_store = AuditStore(repo_dir=self.repo_dir)
        verifier = RedTestVerifier(repo_dir=self.repo_dir)
        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, verifier=verifier, audit_store=audit_store)

        # 1. 外部監査役による指摘報告 (バグ再現コード付き: 真正なアサーション失敗を発生させる)
        repro_code = (
            "import unittest\n"
            "from calculator import calculate_ratio\n\n"
            "class TestRatio(unittest.TestCase):\n"
            "    def test_zero_ratio(self):\n"
            "        try:\n"
            "            res = calculate_ratio(5, 0)\n"
            "        except ZeroDivisionError:\n"
            "            res = -1.0\n"
            "        self.assertEqual(res, 0.0)\n"
        )
        finding = AuditFinding(
            finding_id="BUG-RATIO-ZERO",
            severity="P0",
            spec_reference="SPEC.md 2.2",
            location="src/calculator.py:2",
            description="calculate_ratio crashes on zero denominator",
            reproduction_test_code=repro_code,
            status="REPORTED",
        )

        # 2. AuditDispatcher による Red 検証と PLAN.md への自動タスク登録
        dispatched = dispatcher.dispatch_finding(finding, plan_path=plan_file)
        self.assertEqual(dispatched.status, "DISPATCHED")
        self.assertEqual(dispatched.red_verification_reason, "GENUINE_RED")
        self.assertIsNotNone(dispatched.code_hash)

        # 3. TaskDispatcher が登録されたタスクを取得
        task_disp = TaskDispatcher()
        tasks = task_disp.parse_plan(plan_file)
        pending_task = task_disp.get_next_pending_task(tasks)
        self.assertIsNotNone(pending_task)
        self.assertEqual(pending_task.task_id, "AUD-BUG-RATIO-ZERO")

        # 4. 次回 Maker へ既存教訓を注入 (LessonInjector)
        injector = LessonInjector(repo_dir=self.repo_dir)
        pre_lessons = injector.find_relevant_lessons(pending_task.title)
        injection_text = injector.format_injection(pre_lessons)
        self.assertTrue(len(pre_lessons) >= 1)
        self.assertIn("## 関連する教訓", injection_text)

        # 5. Maker によるバグ修正
        app_file.write_text(
            "def calculate_ratio(a: int, b: int) -> float:\n"
            "    return 0.0 if b == 0 else a / b\n",
            encoding="utf-8",
        )
        # Maker commit
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix: handle zero denominator"], cwd=self.repo_dir, check=True, capture_output=True)

        # 6. AuditResolutionManager による解決確定（After Green、免疫テスト自動昇格、LESSONS.md自動追記）
        resolution_manager = AuditResolutionManager(repo_dir=self.repo_dir)
        resolve_res = resolution_manager.resolve_task(pending_task.task_id)
        self.assertTrue(resolve_res["success"], resolve_res.get("reason"))

        # 7. 免疫テストが非破壊的に配置されたこと
        promoted_immunity = self.repo_dir / "tests" / "immunity" / "test_immunity_BUG_RATIO_ZERO.py"
        self.assertTrue(promoted_immunity.exists())

        # 8. 新たな教訓が LESSONS.md に自動追記されたこと
        lessons_text = self.lessons_md.read_text(encoding="utf-8")
        self.assertIn("[BUG-RATIO-ZERO]", lessons_text)

        # 9. ループ閉塞検証: 次の別タスクで calculate_ratio に関するタスクが来た際、新教訓が自動抽出されること！
        post_lessons = injector.find_relevant_lessons("Refactor calculate_ratio performance")
        found_titles = [l.title for l in post_lessons]
        self.assertTrue(any("BUG-RATIO-ZERO" in t for t in found_titles))


if __name__ == "__main__":
    unittest.main()
