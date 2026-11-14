from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
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
    AuditFinding,
    AuditResolutionManager,
    IndependentAuditRunner,
    LessonInjector,
)
from auto_developer.audit_store import AuditStore
from auto_developer.dispatcher import AuditDispatcher, TaskDispatcher
from auto_developer.models import Task
from auto_developer.runner import CommandRunnerAdapter
from auto_developer.verifier import RedTestVerifier


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
        """機能GGG.4 / HHH.3: シードバグに対する Level 7 クローズドループ本番通し試験。"""
        # Red検証前の HEAD コミットを保持 (HHH.3)
        initial_commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

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
        self.assertEqual(finding.auditor_model, "claude-opus-5.5")

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
        self.assertEqual(stored_finding.before_commit_sha, initial_commit_sha)

        # 機能III.1: 存在しないコミットを指定した場合の Fail-Closed 検証
        verifier = RedTestVerifier(repo_dir=self.repo_dir)
        invalid_commit_res = verifier.verify_candidate_test(finding.reproduction_test_code, commit="0" * 40)
        self.assertFalse(invalid_commit_res.is_reproduced)
        self.assertEqual(invalid_commit_res.reason, "COMMIT_CHECKOUT_FAILED")

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

        # --- e. CommandRunnerAdapter による実 Maker プロセスへの教訓注入とバグ修正 (GGG.1 / HHH.2) ---
        # 実 Maker スクリプト: 標準入力を受領・検証し、src/calc.py を修正する (未コミット状態)
        dev_dir = self.repo_dir / ".dev"
        dev_dir.mkdir(parents=True, exist_ok=True)
        maker_script = dev_dir / "maker_fix.py"
        maker_script.write_text(
            "import os, sys, json\n"
            "stdin_text = sys.stdin.read()\n"
            "env_lessons = os.environ.get('AUTO_DEV_LESSONS', '')\n"
            "ctx_file = os.environ.get('AUTO_DEV_CONTEXT_FILE', '')\n"
            "lessons_file = os.environ.get('AUTO_DEV_LESSONS_FILE', '')\n"
            "# 証跡を記録\n"
            "with open('.dev/maker_proof.json', 'w') as f:\n"
            "    json.dump({\n"
            "        'stdin_len': len(stdin_text),\n"
            "        'has_env': bool(env_lessons),\n"
            "        'ctx_file': ctx_file,\n"
            "        'ctx_file_exists': os.path.exists(ctx_file) if ctx_file else False,\n"
            "        'lessons_file': lessons_file,\n"
            "        'lessons_file_exists': os.path.exists(lessons_file) if lessons_file else False,\n"
            "    }, f)\n"
            "# src/calc.py のバグ修正\n"
            "with open('src/calc.py', 'w') as f:\n"
            "    f.write('def calc_div(a: int, b: int) -> int:\\n    if b == 0:\\n        return 0\\n    return a // b\\n')\n"
            "sys.exit(0)\n",
            encoding="utf-8",
        )

        runner = CommandRunnerAdapter(
            maker_cmd_template=f"cd {self.repo_dir} && {sys.executable} .dev/maker_fix.py",
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

        # 実プロセスが環境変数と教訓ファイルを受領していたことの検証 (HHH.2 / HHH.3)
        proof_file = self.repo_dir / ".dev" / "maker_proof.json"
        self.assertTrue(proof_file.exists())
        proof_data = json.loads(proof_file.read_text(encoding="utf-8"))
        self.assertTrue(proof_data["has_env"])
        self.assertTrue(proof_data["ctx_file_exists"])
        self.assertTrue(proof_data["lessons_file_exists"])

        # --- e2. 未コミット状態での resolve_task 拒絶検証 (HHH.3) ---
        res_manager = AuditResolutionManager(repo_dir=self.repo_dir)
        uncommitted_res = res_manager.resolve_task(task.task_id)
        self.assertFalse(uncommitted_res["success"])
        self.assertEqual(uncommitted_res.get("error"), "UNCOMMITTED_AFTER_STATE")
        immunity_candidate = self.repo_dir / "tests" / "immunity" / "test_immunity_BUG_REAL_001.py"
        self.assertFalse(immunity_candidate.exists())

        # --- e3. Maker による Git コミット確定 ---
        subprocess.run(["git", "add", "src/calc.py", "PLAN.md"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "fix: resolve zero division bug"], cwd=self.repo_dir, check=True)
        after_commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

        # --- f. AuditResolutionManager による After Green 検証と免疫テスト自動昇格 ---
        resolve_res = res_manager.resolve_task(task.task_id)
        self.assertTrue(resolve_res["success"], f"Resolve failed: {resolve_res.get('reason')}")

        proof = resolve_res["proof"]
        self.assertEqual(proof["before_commit"], initial_commit_sha)
        self.assertEqual(proof["after_commit"], after_commit_sha)
        self.assertNotEqual(proof["before_commit"], proof["after_commit"])
        self.assertEqual(proof["before_result"]["reason"], "GENUINE_RED")
        self.assertEqual(proof["after_result"]["reason"], "ALREADY_GREEN")

        # 機能III.2: proof の詳細フィールド検証 (tests_run, failures, errors, output_hash)
        self.assertIn("tests_run", proof["before_result"])
        self.assertIn("failures", proof["before_result"])
        self.assertIn("errors", proof["before_result"])
        self.assertIn("output_hash", proof["before_result"])
        self.assertGreaterEqual(proof["before_result"]["tests_run"], 1)

        self.assertIn("tests_run", proof["after_result"])
        self.assertIn("failures", proof["after_result"])
        self.assertIn("errors", proof["after_result"])
        self.assertIn("output_hash", proof["after_result"])
        self.assertGreaterEqual(proof["after_result"]["tests_run"], 1)

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
        # 機能III.2: LESSONS.md のコミット拘束および再現テストハッシュ参照
        self.assertIn(f"commit {initial_commit_sha}", lessons_text)
        self.assertIn(f"commit {after_commit_sha}", lessons_text)
        self.assertIn(f"test_sha256: {stored_finding.code_hash}", lessons_text)

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

    def test_hostile_reproduction_test_blocked_by_sandbox_and_not_promoted(self) -> None:
        """機能JJJ.4: サブプロセス経由で親リポジトリ改ざんを試みる悪意ある再現テストが
        OSサンドボックスで遮断され、PLAN登録・解決・免疫昇格・LESSONS記録がすべて物理遮断されることを実証。
        """
        canary_file = self.repo_dir / "parent_canary.txt"
        canary_file.write_text("PRISTINE_ORIGINAL_CONTENT", encoding="utf-8")
        subprocess.run(["git", "add", "parent_canary.txt"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add canary"], cwd=self.repo_dir, check=True)

        # 親リポジトリの canary をサブプロセス経由で改変しようとする悪意ある再現テストコード
        # （OS サンドボックスのカーネル強制 deny file-write* によってサブプロセスが拒絶される）
        hostile_repro_code = f"""import subprocess
import unittest

class TestHostileTamper(unittest.TestCase):
    def test_tamper_via_subprocess(self):
        cmd = ["sh", "-c", "echo HOSTILE_TAMPERED_CONTENT > '{canary_file.resolve()}'"]
        subprocess.run(cmd, check=True)
        self.assertEqual(1, 2)
"""
        audit_store = AuditStore(repo_dir=self.repo_dir)
        finding = AuditFinding(
            finding_id="BUG-HOSTILE-001",
            severity="P0",
            spec_reference="SECURITY",
            location="src/calc.py:1",
            description="Hostile test attempting to tamper with parent repository via subprocess",
            reproduction_test_code=hostile_repro_code,
            code_hash=hashlib.sha256(hostile_repro_code.encode("utf-8")).hexdigest(),
            auditor_model="attacker-sim",
        )

        # 1. AuditDispatcher による Red 検証と PLAN.md 登録試行
        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, audit_store=audit_store)
        dispatched = dispatcher.dispatch_finding(finding, plan_path=self.plan_path)

        # サンドボックス遮断により真正な Red と判定されず、ステータスが REJECTED になること
        self.assertEqual(dispatched.status, "REJECTED")

        # 2. PLAN.md に悪意あるタスクが一切登録されていないこと (PLAN登録の物理遮断)
        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("BUG-HOSTILE-001", plan_content)
        self.assertNotIn("AUD-BUG-HOSTILE-001", plan_content)

        # 3. 監査ストアに REJECTED 状態で保存
        audit_store.save_finding(dispatched)

        # 4. AuditResolutionManager での解決試行も Red 未実証 (INVALID_BEFORE_COMMIT / UNVERIFIED_BEFORE_RED) として物理遮断されること
        res_manager = AuditResolutionManager(repo_dir=self.repo_dir)
        resolve_res = res_manager.resolve_task(f"AUD-{finding.finding_id}")
        self.assertFalse(resolve_res["success"])
        self.assertIn(resolve_res.get("error"), ("INVALID_BEFORE_COMMIT", "UNVERIFIED_BEFORE_RED"))

        # 5. 親リポジトリの canary が一切改ざんされていないこと
        self.assertEqual(canary_file.read_text(encoding="utf-8"), "PRISTINE_ORIGINAL_CONTENT")

        # 6. 免疫テストに昇格されていないこと
        promoted_immunity_file = self.immunity_dir / "test_immunity_BUG_HOSTILE_001.py"
        self.assertFalse(promoted_immunity_file.exists())

        # 7. LESSONS.md に登録されていないこと
        lessons_text = self.lessons_path.read_text(encoding="utf-8") if self.lessons_path.exists() else ""
        self.assertNotIn("BUG-HOSTILE-001", lessons_text)

    def test_hostile_confidential_read_blocked_by_sandbox_and_not_promoted(self) -> None:
        """機能KKK.4: ホストの機密情報（~/.ssh等）や親リポジトリ非公開ファイルの読み取りを
        試みる悪意ある再現テストがOSサンドボックスで遮断され、PLAN/免疫/LESSONSに昇格しないことを実証。
        """
        # 親リポジトリ内に機密ファイルを作成
        secret_file = self.repo_dir / "secret_credentials.env"
        secret_file.write_text("SUPER_SECRET_TOKEN=xyz123", encoding="utf-8")
        subprocess.run(["git", "add", "secret_credentials.env"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "chore: add confidential file"], cwd=self.repo_dir, check=True)

        host_ssh_probe = Path.home() / ".ssh" / "id_rsa"

        # 機密読み取りを試みるテストコード
        hostile_read_code = f"""import os, sys, unittest
from pathlib import Path

class TestConfidentialRead(unittest.TestCase):
    def test_steal_credentials(self):
        # 1. ホストの ~/.ssh/id_rsa の読み取り試行
        ssh_path = Path('{host_ssh_probe}')
        try:
            if ssh_path.exists():
                _ = ssh_path.read_text()
        except (PermissionError, FileNotFoundError, OSError):
            pass

        # 2. 親リポジトリの機密ファイル読み取り試行
        parent_secret = Path('{secret_file.resolve()}')
        # カーネルレベルで deny されているため、exists() または read_text() で PermissionError
        _ = parent_secret.read_text()
        self.assertEqual(1, 2)
"""
        audit_store = AuditStore(repo_dir=self.repo_dir)
        finding = AuditFinding(
            finding_id="BUG-CONFIDENTIAL-001",
            severity="P0",
            spec_reference="SECURITY",
            location="src/calc.py:1",
            description="Hostile test attempting to read host secrets and parent confidential files",
            reproduction_test_code=hostile_read_code,
            code_hash=hashlib.sha256(hostile_read_code.encode("utf-8")).hexdigest(),
            auditor_model="attacker-sim",
        )

        # 1. AuditDispatcher による Red 検証
        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, audit_store=audit_store)
        dispatched = dispatcher.dispatch_finding(finding, plan_path=self.plan_path)

        # 読み取り例外により真正な Red と判定されず REJECTED
        self.assertEqual(dispatched.status, "REJECTED")

        # 2. PLAN.md に登録されていないこと
        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("BUG-CONFIDENTIAL-001", plan_content)

        # 3. 解決試行も遮断されること
        res_manager = AuditResolutionManager(repo_dir=self.repo_dir)
        resolve_res = res_manager.resolve_task(f"AUD-{finding.finding_id}")
        self.assertFalse(resolve_res["success"])

        # 4. 免疫テストに昇格されていないこと
        promoted_immunity_file = self.immunity_dir / "test_immunity_BUG_CONFIDENTIAL_001.py"
        self.assertFalse(promoted_immunity_file.exists())

        # 5. LESSONS.md に登録されていないこと
        lessons_text = self.lessons_path.read_text(encoding="utf-8") if self.lessons_path.exists() else ""
        self.assertNotIn("BUG-CONFIDENTIAL-001", lessons_text)

    def test_hostile_untracked_tamper_blocked_by_snapshot_and_not_promoted(self) -> None:
        """機能KKK.4: 未追跡・無視ファイルの改ざんがスナップショット比較で検知され、
        REPO_TAMPER_DETECTED により REJECTED となり PLAN/免疫/LESSONS に昇格しないことを実証。
        """
        from typing import Any
        from unittest.mock import patch

        # 親リポジトリに未追跡ファイルを作成
        untracked_file = self.repo_dir / "untracked_config.ini"
        untracked_file.write_text("DEBUG=false\n", encoding="utf-8")

        tamper_code = """import unittest

class TestUntrackedTamper(unittest.TestCase):
    def test_tamper(self):
        # 通常の失敗テスト
        self.assertEqual(1, 2)
"""
        audit_store = AuditStore(repo_dir=self.repo_dir)
        finding = AuditFinding(
            finding_id="BUG-UNTRACKED-TAMPER-001",
            severity="P0",
            spec_reference="SECURITY",
            location="src/calc.py:1",
            description="Hostile test attempting to tamper with untracked file content",
            reproduction_test_code=tamper_code,
            code_hash=hashlib.sha256(tamper_code.encode("utf-8")).hexdigest(),
            auditor_model="attacker-sim",
        )

        orig_popen = subprocess.Popen

        def tampering_popen(*args: Any, **kwargs: Any) -> Any:
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and any("sandbox" in str(c) or "bwrap" in str(c) or "-c" in str(c) for c in cmd):
                # テストプロセス起動中に未追跡ファイルの内容のみを書き換える
                os.system(f"chflags -R nouchg '{self.repo_dir}' 2>/dev/null || true")
                with contextlib.suppress(Exception):
                    os.chmod(untracked_file, stat.S_IRUSR | stat.S_IWUSR)
                untracked_file.write_text("DEBUG=true\nTAMPERED=yes\n", encoding="utf-8")
            return orig_popen(*args, **kwargs)

        # 1. AuditDispatcher による Red 検証（実行中に未追跡ファイルの改ざんが発生）
        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, audit_store=audit_store)
        with patch("subprocess.Popen", side_effect=tampering_popen):
            dispatched = dispatcher.dispatch_finding(finding, plan_path=self.plan_path)

        # REPO_TAMPER_DETECTED により REJECTED
        self.assertEqual(dispatched.status, "REJECTED")
        self.assertEqual(dispatched.red_verification_reason, "REPO_TAMPER_DETECTED")

        # 2. PLAN.md に登録されていないこと
        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("BUG-UNTRACKED-TAMPER-001", plan_content)

        # 3. 解決試行も遮断されること
        res_manager = AuditResolutionManager(repo_dir=self.repo_dir)
        resolve_res = res_manager.resolve_task(f"AUD-{finding.finding_id}")
        self.assertFalse(resolve_res["success"])

        # 4. 免疫テストに昇格されていないこと
        promoted_immunity_file = self.immunity_dir / "test_immunity_BUG_UNTRACKED_TAMPER_001.py"
        self.assertFalse(promoted_immunity_file.exists())

        # 5. LESSONS.md に登録されていないこと
        lessons_text = self.lessons_path.read_text(encoding="utf-8") if self.lessons_path.exists() else ""
        self.assertNotIn("BUG-UNTRACKED-TAMPER-001", lessons_text)

    def test_multi_llm_hostile_and_malformed_batch_isolated_from_level7_loop(self) -> None:
        """機能LLL.4: 外部LLMから構文エラー・偽陽性・機密読み取り・リポジトリ改ざん等の
        不正・敵対的 Finding 群が投入された場合でも、真正な指摘のみが選別されて
        Level 7 自律ループ（修正 → After Green → 免疫昇格 → 教訓記録）が安全に完走し、
        不正な指摘が PLAN / 免疫 / LESSONS に一切波及しないことを実証。
        """
        # 初期コミットの SHA
        initial_commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

        # 真正なゼロ除算バグ再現テスト
        repro_code = (
            "import unittest\n"
            "from calc import calc_div\n\n"
            "class TestCalcZeroDivision(unittest.TestCase):\n"
            "    def test_zero_div(self):\n"
            "        self.assertEqual(calc_div(10, 0), 0)\n"
        )

        batch_findings = [
            # 1. 構文不正
            AuditFinding(
                finding_id="BATCH-ERR-001",
                severity="P2",
                spec_reference="SPEC.md",
                location="src/calc.py:1",
                description="Malformed syntax finding",
                reproduction_test_code="def broken_syntax(:",
                auditor_model="claude-opus-5.5",
            ),
            # 2. 偽陽性 (最初からPassするテスト)
            AuditFinding(
                finding_id="BATCH-PASS-002",
                severity="P2",
                spec_reference="SPEC.md",
                location="src/calc.py:5",
                description="Already passing finding",
                reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test_p(self):\n        self.assertEqual(1, 1)",
                auditor_model="claude-opus-5.5",
            ),
            # 3. 機密ファイル読み取り試行
            AuditFinding(
                finding_id="BATCH-SECRET-003",
                severity="P0",
                spec_reference="SECURITY",
                location="src/calc.py:1",
                description="Hostile secret read probe",
                reproduction_test_code=(
                    "import unittest\n"
                    "from pathlib import Path\n"
                    "class R(unittest.TestCase):\n"
                    "    def test_leak(self):\n"
                    "        _ = Path('/Library/Keychains/System.keychain').read_bytes()\n"
                    "        self.assertEqual(1, 2)\n"
                ),
                auditor_model="claude-opus-5.5",
            ),
            # 4. 真正なバグ指摘
            AuditFinding(
                finding_id="BATCH-GENUINE-004",
                severity="P0",
                spec_reference="SPEC.md 3.1",
                location="src/calc.py:2",
                description="Zero division handling returns -999 instead of 0",
                reproduction_test_code=repro_code,
                auditor_model="claude-opus-5.5",
            ),
        ]

        audit_store = AuditStore(repo_dir=self.repo_dir)
        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, audit_store=audit_store)

        # 1. バッチ処理による選別
        dispatched_list = dispatcher.process_findings(batch_findings, plan_path=self.plan_path)

        # 真正な指摘のみが 1 件受領されること
        self.assertEqual(len(dispatched_list), 1)
        dispatched_task = dispatched_list[0]
        self.assertEqual(dispatched_task.finding_id, "BATCH-GENUINE-004")
        self.assertEqual(dispatched_task.status, "DISPATCHED")

        # 2. 不正・敵対的指摘がすべて REJECTED で AuditStore に記録されていること
        s1 = audit_store.get_finding("BATCH-ERR-001")
        self.assertIsNotNone(s1)
        assert s1 is not None
        self.assertEqual(s1.status, "REJECTED")

        s2 = audit_store.get_finding("BATCH-PASS-002")
        self.assertIsNotNone(s2)
        assert s2 is not None
        self.assertEqual(s2.status, "REJECTED")

        s3 = audit_store.get_finding("BATCH-SECRET-003")
        self.assertIsNotNone(s3)
        assert s3 is not None
        self.assertEqual(s3.status, "REJECTED")

        # 3. PLAN.md に真正な指摘のみが登録されていること
        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("BATCH-GENUINE-004", plan_content)
        self.assertNotIn("BATCH-ERR-001", plan_content)
        self.assertNotIn("BATCH-PASS-002", plan_content)
        self.assertNotIn("BATCH-SECRET-003", plan_content)

        # 4. 実 Maker プロセスによる修正実行 (CommandRunnerAdapter)
        dev_dir = self.repo_dir / ".dev"
        dev_dir.mkdir(parents=True, exist_ok=True)
        maker_script = dev_dir / "maker_fix.py"
        maker_script.write_text(
            "with open('src/calc.py', 'w') as f:\n"
            "    f.write('def calc_div(a: int, b: int) -> int:\\n    if b == 0:\\n        return 0\\n    return a // b\\n')\n",
            encoding="utf-8",
        )
        task_disp = TaskDispatcher()
        tasks = task_disp.parse_plan(self.plan_path)
        target_task = next(t for t in tasks if "BATCH-GENUINE-004" in t.title)

        runner = CommandRunnerAdapter(
            maker_cmd_template=f"cd {self.repo_dir} && {sys.executable} .dev/maker_fix.py",
        )
        code, _ = runner.run_maker(target_task, context={"attempt": 1})
        self.assertEqual(code, 0)

        # 5. コミット確定
        subprocess.run(["git", "add", "src/calc.py", "PLAN.md"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "fix: resolve zero division bug"], cwd=self.repo_dir, check=True)
        after_commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

        # 6. AuditResolutionManager による解決・免疫昇格・教訓記録
        res_manager = AuditResolutionManager(repo_dir=self.repo_dir)
        resolve_res = res_manager.resolve_task(target_task.task_id)
        self.assertTrue(resolve_res["success"])
        self.assertEqual(resolve_res["proof"]["before_commit"], initial_commit_sha)
        self.assertEqual(resolve_res["proof"]["after_commit"], after_commit_sha)

        # 免疫テストが真正なバグのみ昇格していること
        promoted = self.immunity_dir / "test_immunity_BATCH_GENUINE_004.py"
        self.assertTrue(promoted.exists())
        self.assertFalse((self.immunity_dir / "test_immunity_BATCH_ERR_001.py").exists())
        self.assertFalse((self.immunity_dir / "test_immunity_BATCH_PASS_002.py").exists())
        self.assertFalse((self.immunity_dir / "test_immunity_BATCH_SECRET_003.py").exists())

        # LESSONS.md の更新
        record_ok = res_manager.record_lesson(
            audit_store.get_finding("BATCH-GENUINE-004"),  # type: ignore[arg-type]
            resolve_res["proof"],
            lessons_path=self.lessons_path,
        )
        self.assertTrue(record_ok)
        lessons_text = self.lessons_path.read_text(encoding="utf-8")
        self.assertIn("BATCH-GENUINE-004", lessons_text)
        self.assertNotIn("BATCH-ERR-001", lessons_text)
        self.assertNotIn("BATCH-PASS-002", lessons_text)
        self.assertNotIn("BATCH-SECRET-003", lessons_text)


if __name__ == "__main__":
    unittest.main()
