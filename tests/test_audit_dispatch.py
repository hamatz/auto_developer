from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import AuditFinding
from auto_developer.dispatcher import AuditDispatcher, TaskDispatcher
from auto_developer.verifier import RedTestVerifier


class TestAuditDispatcher(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        # MMM.3: プロダクトコード (src/) を初期化
        src_path = self.repo_dir / "src"
        src_path.mkdir(parents=True, exist_ok=True)
        (src_path / "target_pkg.py").write_text(
            "def target_fn() -> bool:\n    return False\n", encoding="utf-8"
        )
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
from target_pkg import target_fn

class T(unittest.TestCase):
    def test_repro(self):
        self.assertTrue(target_fn())
""",
        )

        result_finding = self.dispatcher.dispatch_finding(finding, plan_path=self.plan_path)
        self.assertEqual(result_finding.status, "DISPATCHED")

        content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("- [ ] AUD-FINDING-001. Fix: [FINDING-001] AST check missing else branch validation", content)
        self.assertIn("Severity: P1", content)
        self.assertIn("Loc: src/auto_developer/guard.py:45", content)

    def test_dispatch_rejects_non_genuine_red_finding(self) -> None:
        """機能CCC.3.1: 偽Red（成功するテストや構文エラー）はREJECTEDとなり、PLAN.mdは改変されないこと。"""
        passing_finding = AuditFinding(
            finding_id="FINDING-PASS",
            severity="P1",
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
from target_pkg import target_fn

class T(unittest.TestCase):
    def test_repro(self):
        self.assertTrue(target_fn())
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
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertTrue(target_fn())",
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

    def test_dispatched_task_is_recognized_by_task_dispatcher(self) -> None:
        """機能DDD.2: AuditDispatcherで登録されたタスクがTaskDispatcherで認識され、
        次の未完了タスクとして取り出せ、mark_task_statusで更新できること。
        """
        finding = AuditFinding(
            finding_id="F-001",
            severity="P1",
            spec_reference="SPEC.md DDD.2",
            location="guard.py:10",
            description="Interoperability test",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_f(self):\n        self.assertTrue(target_fn())",
        )

        res = self.dispatcher.dispatch_finding(finding, plan_path=self.plan_path)
        self.assertEqual(res.status, "DISPATCHED")

        # TaskDispatcher での読み込みとタスク取得
        task_dispatcher = TaskDispatcher()
        tasks = task_dispatcher.parse_plan(self.plan_path)
        next_task = task_dispatcher.get_next_pending_task(tasks)

        self.assertIsNotNone(next_task, "Dispatched task must be recognized by TaskDispatcher!")
        assert next_task is not None
        self.assertEqual(next_task.task_id, "AUD-F-001")
        self.assertIn("Fix: [F-001] Interoperability test", next_task.title)

        # ステータス更新 (completed) の検証
        task_dispatcher.mark_task_status(self.plan_path, next_task, "completed")
        updated_content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("- [x] AUD-F-001. Fix: [F-001] Interoperability test", updated_content)

    def test_multi_llm_malformed_and_hostile_findings_rejected_and_persisted(self) -> None:
        """機能LLL.3: 外部LLMから構文エラー・偽陽性Red・機密読み取り・リポジトリ改ざん等の
        不正・敵対的 Finding が投入された場合でも、安全に REJECTED として AuditStore に保存され、
        PLAN.md を汚染しないこと（Sol氏 指摘）。
        """
        findings = [
            # 1. 構文不正 (Broken syntax)
            AuditFinding(
                finding_id="HOSTILE-SYNTAX-001",
                severity="P1",
                spec_reference="SPEC.md",
                location="app.py:1",
                description="Broken syntax finding",
                reproduction_test_code="def broken_syntax(:",
            ),
            # 2. 偽陽性 (Already green / pass)
            AuditFinding(
                finding_id="HOSTILE-PASS-002",
                severity="P1",
                spec_reference="SPEC.md",
                location="app.py:5",
                description="Passing test",
                reproduction_test_code="import unittest\nclass P(unittest.TestCase):\n    def test_p(self):\n        self.assertEqual(1, 1)",
            ),
            # 3. 機密ファイル読み取り試行 (Confidential read)
            AuditFinding(
                finding_id="HOSTILE-READ-003",
                severity="P0",
                spec_reference="SECURITY",
                location="app.py:10",
                description="Secret read probe",
                reproduction_test_code=(
                    "import unittest\n"
                    "from pathlib import Path\n"
                    "class R(unittest.TestCase):\n"
                    "    def test_r(self):\n"
                    "        _ = Path('/Library/Keychains/System.keychain').read_bytes()\n"
                    "        self.assertEqual(1, 2)\n"
                ),
            ),
            # 4. 真正な Red 指摘 (Genuine bug finding)
            AuditFinding(
                finding_id="GENUINE-BUG-004",
                severity="P1",
                spec_reference="SPEC.md",
                location="app.py:15",
                description="Legitimate bug",
                reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass G(unittest.TestCase):\n    def test_g(self):\n        self.assertTrue(target_fn())",
            ),
        ]

        dispatched_list = self.dispatcher.process_findings(findings, plan_path=self.plan_path)

        # 真正な指摘のみが 1 件 DISPATCHED されること
        self.assertEqual(len(dispatched_list), 1)
        self.assertEqual(dispatched_list[0].finding_id, "GENUINE-BUG-004")

        # PLAN.md には真正な指摘のみが登録され、不正・敵対的指摘は一切登録されないこと
        content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("GENUINE-BUG-004", content)
        self.assertNotIn("HOSTILE-SYNTAX-001", content)
        self.assertNotIn("HOSTILE-PASS-002", content)
        self.assertNotIn("HOSTILE-READ-003", content)

        # AuditStore にすべての指摘が保存され、拒絶されたものは REJECTED で記録されていること
        store = self.dispatcher.audit_store
        f1 = store.get_finding("HOSTILE-SYNTAX-001")
        self.assertIsNotNone(f1, "Rejected finding must be persisted in AuditStore")
        assert f1 is not None
        self.assertEqual(f1.status, "REJECTED")
        self.assertIn(f1.red_verification_reason, ("SYNTAX_ERROR", "BROKEN_SYNTAX"))

        f2 = store.get_finding("HOSTILE-PASS-002")
        self.assertIsNotNone(f2)
        assert f2 is not None
        self.assertEqual(f2.status, "REJECTED")
        self.assertEqual(f2.red_verification_reason, "ALREADY_GREEN")

        f3 = store.get_finding("HOSTILE-READ-003")
        self.assertIsNotNone(f3)
        assert f3 is not None
        self.assertEqual(f3.status, "REJECTED")
        self.assertIn(f3.red_verification_reason, ("UNHANDLED_EXCEPTION", "EXECUTION_ERROR"))

        f4 = store.get_finding("GENUINE-BUG-004")
        self.assertIsNotNone(f4)
        assert f4 is not None
        self.assertEqual(f4.status, "DISPATCHED")

    def test_dispatch_rejects_trivial_red_finding_without_adding_to_plan(self) -> None:
        """機能MMM.3: self.assertEqual(1, 2) や self.fail() 等の偽陽性 Red (TRIVIAL_RED) は REJECTED 隔離され PLAN.md に追加されないこと。"""
        trivial_finding = AuditFinding(
            finding_id="FINDING-TRIVIAL",
            severity="P0",
            spec_reference="SPEC.md MMM.3",
            location="src/auto_developer/verifier.py:10",
            description="Trivial red test with constant assertion",
            reproduction_test_code="""import unittest

class FakeTest(unittest.TestCase):
    def test_fake(self):
        self.assertEqual(1, 2)
""",
        )

        res = self.dispatcher.dispatch_finding(trivial_finding, plan_path=self.plan_path)
        self.assertEqual(res.status, "REJECTED")
        self.assertEqual(res.red_verification_reason, "TRIVIAL_RED")

        content = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("FINDING-TRIVIAL", content)

    def test_dispatch_new_finding_with_same_id_as_completed_task_is_not_dropped(self) -> None:
        """AUD-003 (Sol Phase 62 / Cycle 34): 過去の監査サイクルで完了済み (- [x]) のタスクと同じ finding_id を持つ新たな真正指摘が届いた場合、重複誤認で破棄されず正しく PLAN.md に追加・保存されること。"""
        # 事前に完了済みタスクとして AUD-002 を PLAN.md に記録しておく
        self.plan_path.write_text(
            "# PLAN.md\n\n## ステップ\n- [x] AUD-AUD-002. Fix: [AUD-002] Old fixed defect (Severity: P1, Loc: a.py:1)\n",
            encoding="utf-8",
        )

        new_finding = AuditFinding(
            finding_id="AUD-002",
            severity="P1",
            spec_reference="SPEC.md MMM.3",
            location="src/target_pkg.py:1",
            description="Brand new defect in target_pkg",
            reproduction_test_code="""import unittest
from target_pkg import target_fn

class NewDefectTest(unittest.TestCase):
    def test_repro(self):
        self.assertTrue(target_fn())
""",
        )

        res = self.dispatcher.dispatch_finding(new_finding, plan_path=self.plan_path)
        self.assertEqual(res.status, "DISPATCHED")

        content = self.plan_path.read_text(encoding="utf-8")
        # 新しい指摘のタスク行が追加されていること（未着手 - [ ]）
        self.assertIn("Brand new defect in target_pkg", content)
        self.assertTrue(any(line.startswith("- [ ]") and "Brand new defect in target_pkg" in line for line in content.splitlines()))

    def test_distinct_finding_with_substring_description_is_registered(self) -> None:
        """AUD-001 (Sol Phase 62 / Cycle 35): description が既存の未着手タスク行の部分文字列であっても、異なる finding_id の真正指摘が重複扱いされず登録されること。"""
        self.plan_path.write_text(
            "# PLAN.md\n\n## ステップ\n- [ ] AUD-AUD-001. Fix: [AUD-001] Parser drops real bug in audit module (Severity: P1, Loc: a.py:1)\n",
            encoding="utf-8",
        )

        finding = AuditFinding(
            finding_id="AUD-099",
            severity="P0",
            spec_reference="SPEC.md VVV.3",
            location="src/target_pkg.py:1",
            description="real bug",
            reproduction_test_code="""import unittest
from target_pkg import target_fn

class T(unittest.TestCase):
    def test_x(self):
        self.assertTrue(target_fn())
""",
            status="REPORTED",
        )

        res = self.dispatcher.dispatch_finding(finding, plan_path=self.plan_path)
        content = self.plan_path.read_text(encoding="utf-8")
        self.assertEqual(res.status, "DISPATCHED")
        self.assertIn("[AUD-099]", content, "genuine finding was marked DISPATCHED but never written to PLAN.md")

    def test_feature_www_0_prompt_includes_severity_definitions(self) -> None:
        """機能WWW.0: IndependentAuditRunner.DEFAULT_PROMPT_TEMPLATE に重要度定義が原文のまま埋め込まれていること。"""
        from auto_developer.audit import IndependentAuditRunner

        prompt = IndependentAuditRunner.DEFAULT_PROMPT_TEMPLATE
        self.assertIn("P0: データ消失、機密の漏洩、証跡（Evidence / Bundle）の偽装・改ざんの見逃し、保護ファイル防壁のバイパス。", prompt)
        self.assertIn("P1: 安全ゲート（検証・監査・Red 判定・Checker 判定）が Fail-Open になる経路、通常利用の主経路での仕様違反・誤った結果。", prompt)
        self.assertIn("P2: 限定的な条件・構文上のエッジケースでのみ起きる誤判定（偽陽性・偽陰性）、ヒューリスティクスの精度不足。", prompt)
        self.assertIn("INFO: 改善提案・理論的懸念・可読性。", prompt)
        self.assertIn("判定に迷う場合は低い方を付ける（P1 か P2 か迷うなら P2）。", prompt)

    def test_feature_www_1_valid_statuses_extended(self) -> None:
        """機能WWW.1: VALID_STATUSES に BACKLOGGED, NOTED, ESCALATED が含まれること。"""
        from auto_developer.audit import VALID_STATUSES

        self.assertIn("BACKLOGGED", VALID_STATUSES)
        self.assertIn("NOTED", VALID_STATUSES)
        self.assertIn("ESCALATED", VALID_STATUSES)

    def test_feature_www_1_p2_finding_routed_to_backlog(self) -> None:
        """機能WWW.1: P2 指摘は Red 検証を行わず BACKLOG.md に追記され、status が BACKLOGGED となること。"""
        backlog_path = self.repo_dir / "BACKLOG.md"
        if backlog_path.exists():
            backlog_path.unlink()

        # 敢えて実行不可な（構文不正な）テストコードを指定し、Red検証がスキップされることを確認する
        p2_finding = AuditFinding(
            finding_id="AUD-P2-001",
            severity="P2",
            spec_reference="SPEC.md WWW.1",
            location="src/foo.py:42",
            description="Minor edge case misbehavior",
            reproduction_test_code="def broken_syntax(:",
            target_commit="c0ffee1234",
        )

        res = self.dispatcher.dispatch_finding(p2_finding, plan_path=self.plan_path)
        self.assertEqual(res.status, "BACKLOGGED")

        # PLAN.md には書かれないこと
        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("AUD-P2-001", plan_content)

        # BACKLOG.md に追記されていること
        self.assertTrue(backlog_path.exists())
        backlog_content = backlog_path.read_text(encoding="utf-8")
        self.assertTrue(backlog_content.startswith("# BACKLOG.md"))
        expected_line = "- [AUD-P2-001] Minor edge case misbehavior (Severity: P2, Loc: src/foo.py:42, Cycle: c0ffee1234)"
        self.assertIn(expected_line, backlog_content)
        # チェックボックスを持たないこと
        self.assertNotIn("- [ ]", backlog_content)

        # 冪等性: 同一 finding_id を再度 dispatch しても二重追記されないこと
        self.dispatcher.dispatch_finding(p2_finding, plan_path=self.plan_path)
        lines = [line for line in backlog_path.read_text(encoding="utf-8").splitlines() if "[AUD-P2-001]" in line]
        self.assertEqual(len(lines), 1)

    def test_feature_www_1_info_finding_routed_to_audit_store_only(self) -> None:
        """機能WWW.1: INFO 指摘は PLAN.md も BACKLOG.md も書き換えず AuditStore にのみ保存され、status が NOTED となること。"""
        backlog_path = self.repo_dir / "BACKLOG.md"
        if backlog_path.exists():
            backlog_path.unlink()

        info_finding = AuditFinding(
            finding_id="AUD-INFO-001",
            severity="INFO",
            spec_reference="SPEC.md WWW.1",
            location="src/bar.py:10",
            description="Code clarity suggestion",
            reproduction_test_code="def broken(:",
        )

        res = self.dispatcher.dispatch_finding(info_finding, plan_path=self.plan_path)
        self.assertEqual(res.status, "NOTED")

        # PLAN.md と BACKLOG.md には一切書かれないこと
        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("AUD-INFO-001", plan_content)
        if backlog_path.exists():
            self.assertNotIn("AUD-INFO-001", backlog_path.read_text(encoding="utf-8"))

        # AuditStore に保存されていること
        saved = self.dispatcher.audit_store.get_finding("AUD-INFO-001")
        self.assertIsNotNone(saved)
        assert saved is not None
        self.assertEqual(saved.status, "NOTED")

    def test_feature_www_2_module_key_extraction(self) -> None:
        """機能WWW.2: location からモジュールキーが正しく抽出されること。"""
        extract = self.dispatcher._extract_module_key
        self.assertEqual(extract("src/auto_developer/verifier.py:166"), "src/auto_developer/verifier.py")
        self.assertEqual(extract("src/calc.py:5"), "src/calc.py")
        self.assertEqual(extract("src/foo.py (line 12)"), "src/foo.py")
        self.assertEqual(extract("  src/bar.py : 20"), "src/bar.py")
        self.assertEqual(extract(""), "UNKNOWN")
        self.assertEqual(extract(":123"), "UNKNOWN")

    def test_feature_www_2_budget_ledger_and_streak_increment_and_idempotency(self) -> None:
        """機能WWW.2: 修正予算台帳 (.dev/audit/budget.json) に streak が記録され、同一 cycle_id は二重加算されないこと。"""
        import json

        f1 = AuditFinding(
            finding_id="AUD-BUG-001",
            severity="P1",
            spec_reference="SPEC.md WWW.2",
            location="src/target_pkg.py:10",
            description="Genuine bug",
            reproduction_test_code="""import unittest
from target_pkg import target_fn
class T(unittest.TestCase):
    def test_x(self):
        self.assertTrue(target_fn())
""",
        )

        # サイクル1: 初回登録 -> streak 1
        self.dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="cycle-001")
        budget_path = self.repo_dir / ".dev" / "audit" / "budget.json"
        self.assertTrue(budget_path.exists())
        budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
        self.assertEqual(budget_data["src/target_pkg.py"]["streak"], 1)
        self.assertEqual(budget_data["src/target_pkg.py"]["last_cycle_id"], "cycle-001")

        # 冪等性: 同じ cycle-001 を再度処理しても streak は増えない
        self.dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="cycle-001")
        budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
        self.assertEqual(budget_data["src/target_pkg.py"]["streak"], 1)

        # サイクル2: 異なる cycle-002 で再登録 -> streak 2
        f2 = AuditFinding(
            finding_id="AUD-BUG-002",
            severity="P1",
            spec_reference="SPEC.md WWW.2",
            location="src/target_pkg.py:20",
            description="Another bug in same module",
            reproduction_test_code="""import unittest
from target_pkg import target_fn
class T(unittest.TestCase):
    def test_y(self):
        self.assertTrue(target_fn())
""",
        )
        self.dispatcher.process_findings([f2], plan_path=self.plan_path, cycle_id="cycle-002")
        budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
        self.assertEqual(budget_data["src/target_pkg.py"]["streak"], 2)
        self.assertEqual(budget_data["src/target_pkg.py"]["last_cycle_id"], "cycle-002")

    def test_feature_www_2_escalation_when_streak_exceeds_max(self) -> None:
        """機能WWW.2: streak >= max_consecutive_cycles のとき ESCALATED となり PLAN.md の「止まっていること・判断待ち」節に記録されること。"""
        dispatcher = AuditDispatcher(
            repo_dir=self.repo_dir,
            verifier=self.verifier,
            audit_store=self.dispatcher.audit_store,
            max_consecutive_cycles=2,
        )

        f1 = AuditFinding(
            finding_id="AUD-S1",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Bug 1",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_1(self):\n        self.assertTrue(target_fn())",
        )
        f2 = AuditFinding(
            finding_id="AUD-S2",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Bug 2",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_2(self):\n        self.assertTrue(target_fn())",
        )
        f3 = AuditFinding(
            finding_id="AUD-S3",
            severity="P0",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Bug 3 (exceeding budget)",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_3(self):\n        self.assertTrue(target_fn())",
        )

        # cycle 1: streak 1
        dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="c1")
        # cycle 2: streak 2
        dispatcher.process_findings([f2], plan_path=self.plan_path, cycle_id="c2")

        # cycle 3: streak was 2 (>= max 2) -> ESCALATED
        res_list = dispatcher.process_findings([f3], plan_path=self.plan_path, cycle_id="c3")
        self.assertEqual(len(res_list), 0, "Escalated finding must not be in dispatched return list")
        self.assertEqual(f3.status, "ESCALATED")

        plan_content = self.plan_path.read_text(encoding="utf-8")
        # ## ステップ には登録されないこと
        steps_part = plan_content.split("## 止まっていること・判断待ち")[0] if "## 止まっていること・判断待ち" in plan_content else plan_content
        self.assertNotIn("AUD-S3", steps_part)

        # ## 止まっていること・判断待ち 節に追記されていること
        self.assertIn("## 止まっていること・判断待ち", plan_content)
        expected_line = "- ESCALATED [AUD-S3] Bug 3 (exceeding budget) (Severity: P0, Loc: src/target_pkg.py:1, Streak: 2)"
        self.assertIn(expected_line, plan_content)
        self.assertNotIn("- [ ] AUD-S3", plan_content)

    def test_feature_www_2_streak_resets_when_no_p0_p1(self) -> None:
        """機能WWW.2: P0/P1 の登録がなかったサイクルで該当モジュールの streak が 0 に戻ること。"""
        import json

        f1 = AuditFinding(
            finding_id="AUD-R1",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Bug 1",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_1(self):\n        self.assertTrue(target_fn())",
        )
        self.dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="c1")
        budget_path = self.repo_dir / ".dev" / "audit" / "budget.json"
        budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
        self.assertEqual(budget_data["src/target_pkg.py"]["streak"], 1)

        # 次のサイクル c2 では P2 のみ（P0/P1 なし）
        f_p2 = AuditFinding(
            finding_id="AUD-P2-99",
            severity="P2",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="P2 bug",
            reproduction_test_code="def broken(:",
        )
        self.dispatcher.process_findings([f_p2], plan_path=self.plan_path, cycle_id="c2")
        budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
        self.assertEqual(budget_data["src/target_pkg.py"]["streak"], 0)
        self.assertEqual(budget_data["src/target_pkg.py"]["last_cycle_id"], "c2")

    def test_feature_www_2_idempotent_reprocessing_does_not_flip_to_escalated(self) -> None:
        """機能WWW.2: 同じ cycle_id を再処理した際、DISPATCHED 判定が ESCALATED に反転しないこと。"""
        dispatcher = AuditDispatcher(
            repo_dir=self.repo_dir,
            verifier=self.verifier,
            audit_store=self.dispatcher.audit_store,
            max_consecutive_cycles=2,
        )

        f1 = AuditFinding(
            finding_id="AUD-C1",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Bug 1",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_1(self):\n        self.assertTrue(target_fn())",
        )
        f2 = AuditFinding(
            finding_id="AUD-C2",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Bug 2",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_2(self):\n        self.assertTrue(target_fn())",
        )

        dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="c1")
        res2 = dispatcher.process_findings([f2], plan_path=self.plan_path, cycle_id="c2")
        self.assertEqual(len(res2), 1)
        self.assertEqual(f2.status, "DISPATCHED")

        # 同じ c2 を再投入
        res2_retry = dispatcher.process_findings([f2], plan_path=self.plan_path, cycle_id="c2")
        self.assertEqual(f2.status, "DISPATCHED", "Re-running the same cycle must remain DISPATCHED, not flip to ESCALATED")
        self.assertEqual(len(res2_retry), 1)

        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertNotIn("- ESCALATED", plan_content, "Re-running cycle 2 must not escalate")

    def test_feature_www_2_empty_findings_resets_streak(self) -> None:
        """機能WWW.2: 指摘が0件のサイクルでも、P0/P1の登録がなかったモジュールの streak は 0 に戻ること。"""
        import json

        f1 = AuditFinding(
            finding_id="AUD-R1",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Bug 1",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_1(self):\n        self.assertTrue(target_fn())",
        )
        self.dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="c1")
        budget_path = self.repo_dir / ".dev" / "audit" / "budget.json"
        budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
        self.assertEqual(budget_data["src/target_pkg.py"]["streak"], 1)

        # 空の findings リストで新サイクル c2 を実行
        self.dispatcher.process_findings([], plan_path=self.plan_path, cycle_id="c2")
        budget_data = json.loads(budget_path.read_text(encoding="utf-8"))
        self.assertEqual(budget_data["src/target_pkg.py"]["streak"], 0)
        self.assertEqual(budget_data["src/target_pkg.py"]["last_cycle_id"], "c2")

    def test_feature_www_6_reused_id_same_hash_open_plan_suppressed(self) -> None:
        """機能WWW.6: 未着手タスク(- [ ])と同一ID・同一ハッシュの指摘は重複登録を抑止し除外されること。"""
        repro = "import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_1(self):\n        self.assertTrue(target_fn())"
        f1 = AuditFinding(
            finding_id="AUD-001",
            severity="P1",
            spec_reference="SPEC.md WWW.6",
            location="src/target_pkg.py:1",
            description="Bug 1",
            reproduction_test_code=repro,
        )
        res1 = self.dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="c1")
        self.assertEqual(len(res1), 1)
        plan_content_1 = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("[AUD-001]", plan_content_1)

        # 全く同じ内容（AUD-001, same hash）を次サイクルで再報告
        f2 = AuditFinding(
            finding_id="AUD-001",
            severity="P1",
            spec_reference="SPEC.md WWW.6",
            location="src/target_pkg.py:1",
            description="Bug 1",
            reproduction_test_code=repro,
        )
        res2 = self.dispatcher.process_findings([f2], plan_path=self.plan_path, cycle_id="c2")
        # 重複抑止され、新規ディスパッチされない（空リスト）
        self.assertEqual(len(res2), 0)
        plan_content_2 = self.plan_path.read_text(encoding="utf-8")
        # タスク行が増えていないこと
        self.assertEqual(plan_content_1, plan_content_2)

    def test_feature_www_6_reused_id_same_hash_backlog_suppressed(self) -> None:
        """機能WWW.6: BACKLOG行と同一ID・同一ハッシュの指摘は重複登録を抑止し除外されること。"""
        repro = ""
        f1 = AuditFinding(
            finding_id="AUD-005",
            severity="P2",
            spec_reference="SPEC.md WWW.6",
            location="src/target_pkg.py:1",
            description="P2 issue",
            reproduction_test_code=repro,
        )
        self.dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="c1")
        backlog_path = self.repo_dir / "BACKLOG.md"
        self.assertTrue(backlog_path.exists())
        backlog_content_1 = backlog_path.read_text(encoding="utf-8")
        self.assertIn("[AUD-005]", backlog_content_1)

        # 同一内容を次サイクルで再報告
        f2 = AuditFinding(
            finding_id="AUD-005",
            severity="P2",
            spec_reference="SPEC.md WWW.6",
            location="src/target_pkg.py:1",
            description="P2 issue",
            reproduction_test_code=repro,
        )
        res2 = self.dispatcher.process_findings([f2], plan_path=self.plan_path, cycle_id="c2")
        self.assertEqual(len(res2), 0)
        backlog_content_2 = backlog_path.read_text(encoding="utf-8")
        self.assertEqual(backlog_content_1, backlog_content_2)

    def test_feature_www_6_reused_id_same_hash_completed_plan_reopens_as_regression(self) -> None:
        """機能WWW.6: 完了済みタスク(- [x])と同一ID・同一ハッシュの指摘は回帰として採番し直され再オープンされること。"""
        repro = "import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_1(self):\n        self.assertTrue(target_fn())"
        f1 = AuditFinding(
            finding_id="AUD-001",
            severity="P1",
            spec_reference="SPEC.md WWW.6",
            location="src/target_pkg.py:1",
            description="Regression Bug",
            reproduction_test_code=repro,
        )
        self.dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="c1")
        plan_content = self.plan_path.read_text(encoding="utf-8")
        # タスクを完了状態 (- [x]) に変更
        plan_content = plan_content.replace("- [ ] AUD-AUD-001", "- [x] AUD-AUD-001")
        self.plan_path.write_text(plan_content, encoding="utf-8")

        # 同一内容が再報告された
        f_reopened = AuditFinding(
            finding_id="AUD-001",
            severity="P1",
            spec_reference="SPEC.md WWW.6",
            location="src/target_pkg.py:1",
            description="Regression Bug",
            reproduction_test_code=repro,
        )
        res = self.dispatcher.process_findings([f_reopened], plan_path=self.plan_path, cycle_id="c2")
        self.assertEqual(len(res), 1)
        # AUD-001 は完了済みなので、回帰タスクとして AUD-002 で採番
        self.assertEqual(res[0].finding_id, "AUD-002")
        new_plan = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("[AUD-002]", new_plan)

    def test_feature_www_6_reused_id_different_hash_renumbered(self) -> None:
        """機能WWW.6: 同一IDだが内容(code_hash)の異なる指摘はAUD-018で採番し直されCODE_HASH_TAMPEREDを起こさないこと。"""
        repro1 = "import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_1(self):\n        self.assertTrue(target_fn())"
        f1 = AuditFinding(
            finding_id="AUD-001",
            severity="P1",
            spec_reference="SPEC.md WWW.6",
            location="src/target_pkg.py:1",
            description="Bug A",
            reproduction_test_code=repro1,
        )
        self.dispatcher.process_findings([f1], plan_path=self.plan_path, cycle_id="c1")

        # 別サイクルで外部LLMが AUD-001 を別のバグBに再利用
        repro2 = "import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_2(self):\n        self.assertTrue(target_fn())"
        f2 = AuditFinding(
            finding_id="AUD-001",
            severity="P1",
            spec_reference="SPEC.md WWW.6",
            location="src/target_pkg.py:1",
            description="Bug B (different)",
            reproduction_test_code=repro2,
        )
        # CODE_HASH_TAMPERED 例外が発生せず正常終了すること
        res = self.dispatcher.process_findings([f2], plan_path=self.plan_path, cycle_id="c2")
        self.assertEqual(len(res), 1)
        self.assertEqual(res[0].finding_id, "AUD-002")

    def test_feature_www_6_all_severity_paths_id_collision(self) -> None:
        """機能WWW.6: P0, P1, P2, INFO の全経路で同一IDが安全に採番・処理されAUDIT_FAILEDにならないこと。"""
        # 初回に AUD-001 を登録
        f0 = AuditFinding(
            finding_id="AUD-001",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Base bug",
            reproduction_test_code="import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_0(self):\n        self.assertTrue(target_fn())",
        )
        self.dispatcher.process_findings([f0], plan_path=self.plan_path, cycle_id="c0")

        # 衝突する AUD-001 を各重要度で投入
        f_info = AuditFinding(
            finding_id="AUD-001",
            severity="INFO",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Info note",
            reproduction_test_code="code_info",
        )
        f_p2 = AuditFinding(
            finding_id="AUD-001",
            severity="P2",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="P2 note",
            reproduction_test_code="code_p2",
        )
        # 一括投入して全経路で採番し直され、例外なく完了すること
        self.dispatcher.process_findings([f_info, f_p2], plan_path=self.plan_path, cycle_id="c1")
        self.assertEqual(f_info.status, "NOTED")
        self.assertEqual(f_p2.status, "BACKLOGGED")
        self.assertNotEqual(f_info.finding_id, "AUD-001")
        self.assertNotEqual(f_p2.finding_id, "AUD-001")

    def test_feature_www_6_backlog_item_reported_as_p1_is_suppressed(self) -> None:
        """機能WWW.6: BACKLOG行に存在する同一ハッシュの指摘が別サイクルでP1として再報告されても重複登録されないこと。"""
        repro = "import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_backlog(self):\n        self.assertTrue(target_fn())"
        f_p2 = AuditFinding(
            finding_id="AUD-010",
            severity="P2",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Backlog bug",
            reproduction_test_code=repro,
        )
        self.dispatcher.process_findings([f_p2], plan_path=self.plan_path, cycle_id="c1")
        plan_before = self.plan_path.read_text(encoding="utf-8")

        # 別サイクルで同一ハッシュがP1として報告された
        f_p1 = AuditFinding(
            finding_id="AUD-010",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Backlog bug promoted to P1",
            reproduction_test_code=repro,
        )
        res = self.dispatcher.process_findings([f_p1], plan_path=self.plan_path, cycle_id="c2")
        self.assertEqual(len(res), 0)
        plan_after = self.plan_path.read_text(encoding="utf-8")
        # PLAN.md に重複して登録されないこと
        self.assertEqual(plan_before, plan_after)

    def test_feature_www_6_open_plan_reported_as_p2_is_suppressed(self) -> None:
        """機能WWW.6: 未完了PLANに存在する同一ハッシュの指摘が別サイクルでP2として再報告されてもBACKLOGに重複登録されないこと。"""
        repro = "import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_plan(self):\n        self.assertTrue(target_fn())"
        f_p1 = AuditFinding(
            finding_id="AUD-020",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Plan bug",
            reproduction_test_code=repro,
        )
        self.dispatcher.process_findings([f_p1], plan_path=self.plan_path, cycle_id="c1")
        backlog_path = self.repo_dir / "BACKLOG.md"
        backlog_before = backlog_path.read_text(encoding="utf-8") if backlog_path.exists() else ""

        # 別サイクルで同一ハッシュがP2として報告された
        f_p2 = AuditFinding(
            finding_id="AUD-020",
            severity="P2",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Plan bug demoted to P2",
            reproduction_test_code=repro,
        )
        res = self.dispatcher.process_findings([f_p2], plan_path=self.plan_path, cycle_id="c2")
        self.assertEqual(len(res), 0)
        backlog_after = backlog_path.read_text(encoding="utf-8") if backlog_path.exists() else ""
        # BACKLOG.md に重複して登録されないこと
        self.assertEqual(backlog_before, backlog_after)

    def test_feature_www_6_duplicate_findings_in_same_batch_suppressed(self) -> None:
        """機能WWW.6: 同一バッチ内に同一ハッシュの指摘が複数含まれる場合、重複登録が抑止されること。"""
        repro = "import unittest\nfrom target_pkg import target_fn\nclass T(unittest.TestCase):\n    def test_batch(self):\n        self.assertTrue(target_fn())"
        f1 = AuditFinding(
            finding_id="AUD-030",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Batch bug 1",
            reproduction_test_code=repro,
        )
        f2 = AuditFinding(
            finding_id="AUD-030",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/target_pkg.py:1",
            description="Batch bug 2 duplicate",
            reproduction_test_code=repro,
        )
        res = self.dispatcher.process_findings([f1, f2], plan_path=self.plan_path, cycle_id="c1")
        # 1件目のみディスパッチされ、2件目は除外
        self.assertEqual(len(res), 1)
        plan_content = self.plan_path.read_text(encoding="utf-8")
        # PLAN.md に1回だけ記載されていること
        self.assertEqual(plan_content.count("[AUD-030]"), 1)


if __name__ == "__main__":
    unittest.main()
