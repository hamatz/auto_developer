from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import (
    AuditConfig,
    AuditResolutionManager,
    IndependentAuditRunner,
    LessonInjector,
)
from auto_developer.audit_store import AuditStore
from auto_developer.dispatcher import AuditDispatcher, TaskDispatcher
from auto_developer.models import Task
from auto_developer.runner import CommandRunnerAdapter


class TestLevel7RealLoopE2E(unittest.TestCase):
    """機能GGG.4: モックを排除した実サブプロセス（CommandRunnerAdapter）による

    Level 7 完全自己改善ループ（独立監査 → Before Red 隔離検証 → PLAN登録 →
    実Makerへの教訓注入・修正 → After Green 検証 → 免疫昇格 → 教訓記録 → 次回Maker教訓自動注入）
    の一気通貫受入試験。
    """

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)

        # 1. Git リポジトリ初期化
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AutoDev Test"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "autodev@example.com"], cwd=self.repo_dir, check=True)

        # 2. ディレクトリ構成
        self.src_dir = self.repo_dir / "src"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.immunity_dir = self.repo_dir / "tests" / "immunity"
        self.immunity_dir.mkdir(parents=True, exist_ok=True)

        # 3. シードバグを持つモジュール作成 (ゼロ除算で -999 を返すバグ)
        self.calc_py = self.src_dir / "calc.py"
        self.calc_py.write_text(
            "def calc_div(a: int, b: int) -> int:\n"
            "    if b == 0:\n"
            "        return -999\n"
            "    return a // b\n",
            encoding="utf-8",
        )

        # 4. 初期 LESSONS.md
        self.lessons_path = self.repo_dir / "LESSONS.md"
        self.lessons_path.write_text(
            "# Repository Lessons Learned\n\n"
            "## 恒久教訓インデックス\n\n"
            "### [2026-11-01] Arithmetic Boundary Handling\n"
            "- Context: math functions\n"
            "- Trap: Unhandled edge conditions in arithmetic operations\n",
            encoding="utf-8",
        )

        # 5. 初期 PLAN.md
        self.plan_path = self.repo_dir / "PLAN.md"
        self.plan_path.write_text(
            "# PLAN.md\n\n"
            "## ステップ\n",
            encoding="utf-8",
        )

        # 初期コミット
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit with seed bug"], cwd=self.repo_dir, check=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_complete_level7_closed_loop_e2e(self) -> None:
        """機能GGG.4: シードバグに対する Level 7 クローズドループ本番通し試験。"""
        # --- a. 独立監査ランナーによる Finding 検出 ---
        repro_code = (
            "import unittest\n"
            "from calc import calc_div\n\n"
            "class TestCalcZeroDivision(unittest.TestCase):\n"
            "    def test_zero_div(self):\n"
            "        self.assertEqual(calc_div(10, 0), 0)\n"
        )
        audit_payload = [
            {
                "finding_id": "BUG-REAL-001",
                "severity": "P0",
                "spec_reference": "SPEC.md 3.1",
                "location": "src/calc.py:2",
                "description": "Zero division handling returns -999 instead of 0",
                "reproduction_test_code": repro_code,
                "status": "REPORTED",
            }
        ]

        # 異系統LLM構成 (Gemini Maker vs Claude Opus Auditor)
        audit_cfg = AuditConfig(maker_model="gemini-3.8-flash", auditor_model="claude-opus-5.5")
        auditor_cmd = (
            f"{sys.executable} -c 'import json; print(json.dumps({json.dumps(audit_payload)}))'"
        )
        audit_runner = IndependentAuditRunner(
            auditor_cmd_template=auditor_cmd,
            model_name="claude-opus-5.5",
            config=audit_cfg,
        )
        findings = audit_runner.audit(spec_content="# SPEC\n", git_diff="diff --git a/src/calc.py")
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.finding_id, "BUG-REAL-001")

        # --- b. 隔離環境での Before Red 実証 & AuditStore 不変登録 & PLAN登録 ---
        audit_store = AuditStore(repo_dir=self.repo_dir)
        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, audit_store=audit_store)
        dispatched = dispatcher.dispatch_finding(finding, plan_path=self.plan_path)
        self.assertEqual(dispatched.status, "DISPATCHED")

        # AuditStore に code_hash が確定保存されていること (GGG.2)
        stored_finding = audit_store.get_finding("BUG-REAL-001")
        self.assertIsNotNone(stored_finding)
        assert stored_finding is not None
        self.assertIsNotNone(stored_finding.code_hash)
        self.assertEqual(stored_finding.red_verification_reason, "GENUINE_RED")

        # --- c. TaskDispatcher による PLAN.md からの AUD タスク取得 ---
        task_disp = TaskDispatcher()
        tasks = task_disp.parse_plan(self.plan_path)
        self.assertEqual(len(tasks), 1)
        task = tasks[0]
        self.assertEqual(task.task_id, "AUD-BUG-REAL-001")

        # --- d. LessonInjector による既存教訓検索とフォーマット ---
        injector = LessonInjector(repo_dir=self.repo_dir)
        relevant_lessons = injector.find_relevant_lessons(task.title)
        formatted_lessons = injector.format_injection(relevant_lessons)

        # --- e. CommandRunnerAdapter による実 Maker プロセスへの教訓注入とバグ修正 (GGG.1) ---
        # 実 Maker スクリプト: 標準入力を受領・検証し、src/calc.py を修正する
        maker_script = self.repo_dir / "maker_fix.py"
        maker_script.write_text(
            "import os, sys, json\n"
            "stdin_text = sys.stdin.read()\n"
            "env_lessons = os.environ.get('AUTO_DEV_LESSONS', '')\n"
            "ctx_file = os.environ.get('AUTO_DEV_CONTEXT_FILE', '')\n"
            "# 証跡を記録\n"
            "with open('maker_proof.json', 'w') as f:\n"
            "    json.dump({\n"
            "        'stdin_len': len(stdin_text),\n"
            "        'has_env': bool(env_lessons),\n"
            "        'ctx_file_exists': os.path.exists(ctx_file) if ctx_file else False,\n"
            "    }, f)\n"
            "# src/calc.py のバグ修正\n"
            "with open('src/calc.py', 'w') as f:\n"
            "    f.write('def calc_div(a: int, b: int) -> int:\\n    if b == 0:\\n        return 0\\n    return a // b\\n')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )

        runner = CommandRunnerAdapter(
            maker_cmd_template=f"cd {self.repo_dir} && {sys.executable} maker_fix.py",
        )
        code, _out = runner.run_maker(
            task,
            context={
                "attempt": 1,
                "lessons": formatted_lessons,
                "lesson_entries": [e.title for e in relevant_lessons],
            },
        )
        self.assertEqual(code, 0)

        # 実プロセスが教訓入力を受け取っていたことの検証
        proof_file = self.repo_dir / "maker_proof.json"
        self.assertTrue(proof_file.exists())
        proof_data = json.loads(proof_file.read_text(encoding="utf-8"))
        self.assertTrue(proof_data["has_env"])

        # --- f. AuditResolutionManager による After Green 検証と免疫テスト自動昇格 ---
        res_manager = AuditResolutionManager(repo_dir=self.repo_dir)
        resolve_res = res_manager.resolve_task(task.task_id)
        self.assertTrue(resolve_res["success"], f"Resolve failed: {resolve_res.get('reason')}")

        # 免疫テストが非破壊的に昇格配置されたこと
        promoted_immunity_file = self.repo_dir / "tests" / "immunity" / "test_immunity_BUG_REAL_001.py"
        self.assertTrue(promoted_immunity_file.exists())
        self.assertIn("class TestCalcZeroDivision", promoted_immunity_file.read_text(encoding="utf-8"))

        # --- g. 教訓の LESSONS.md への自動追記 ---
        record_ok = res_manager.record_lesson(
            audit_store.get_finding("BUG-REAL-001"),  # type: ignore[arg-type]
            resolve_res["proof"],
            lessons_path=self.lessons_path,
        )
        self.assertTrue(record_ok)

        lessons_text = self.lessons_path.read_text(encoding="utf-8")
        self.assertIn("[BUG-REAL-001]", lessons_text)
        self.assertIn("Zero division handling returns -999 instead of 0", lessons_text)
        self.assertIn("test_immunity_BUG_REAL_001.py", lessons_text)

        # --- h. 次回タスクにおける昇格教訓の自動検知と実 Maker への注入実証 ---
        next_task = Task(
            task_id="TASK-NEXT-MATH",
            title="Fix Zero division in statistics module",
            line_number=2,
            is_completed=False,
            is_failed=False,
        )
        next_injector = LessonInjector(repo_dir=self.repo_dir)
        next_matching_lessons = next_injector.find_relevant_lessons(next_task.title)
        self.assertTrue(
            any("BUG-REAL-001" in item.title or "Zero division" in item.title for item in next_matching_lessons)
        )

        next_injection = next_injector.format_injection(next_matching_lessons)
        self.assertIn("BUG-REAL-001", next_injection)
        self.assertIn("test_immunity_BUG_REAL_001.py", next_injection)

        # 後続の実 Maker スクリプト
        next_maker_script = self.repo_dir / "next_maker.py"
        next_maker_script.write_text(
            "import os, sys\n"
            "stdin_text = sys.stdin.read()\n"
            "with open('next_maker_received.txt', 'w') as f:\n"
            "    f.write(stdin_text)\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )

        next_runner = CommandRunnerAdapter(
            maker_cmd_template=f"cd {self.repo_dir} && {sys.executable} next_maker.py",
        )
        code2, _out2 = next_runner.run_maker(
            next_task,
            context={
                "attempt": 1,
                "lessons": next_injection,
                "lesson_entries": [e.title for e in next_matching_lessons],
            },
        )
        self.assertEqual(code2, 0)

        # 後続 Maker の標準入力に、直前タスクで昇格・記録された教訓が実際に到達したことを実証
        next_received = (self.repo_dir / "next_maker_received.txt").read_text(encoding="utf-8")
        self.assertIn("BUG-REAL-001", next_received)
        self.assertIn("test_immunity_BUG_REAL_001.py", next_received)


if __name__ == "__main__":
    unittest.main()
