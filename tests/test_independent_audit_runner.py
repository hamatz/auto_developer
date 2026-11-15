from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import IndependentAuditRunner


class TestIndependentAuditRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = IndependentAuditRunner(
            model_name="claude-opus-5.5",
        )

    def test_build_audit_prompt_contains_spec_and_diff(self) -> None:
        """機能EEE.2: 独立監査プロンプトにSPECとgit diffが含まれ、厳格な構造化フォーマットを指示すること。"""
        spec = "# System Spec\nMust not divide by zero."
        diff = "diff --git a/app.py\n+ return 1 / 0"
        prompt = self.runner.build_audit_prompt(spec, diff)

        self.assertIn("Must not divide by zero.", prompt)
        self.assertIn("return 1 / 0", prompt)
        self.assertIn("finding_id", prompt)
        self.assertIn("reproduction_test_code", prompt)
        self.assertIn("SPEC.md", prompt)

    def test_parse_response_markdown_json_block(self) -> None:
        """機能EEE.2: Markdownコードブロック内のJSONから AuditFinding を構造化抽出できること。"""
        response_text = """
Here is the audit finding:
```json
[
  {
    "finding_id": "BUG-DIV-ZERO",
    "severity": "P0",
    "spec_reference": "SPEC.md 2.1",
    "location": "app.py:10",
    "description": "Zero division vulnerability",
    "reproduction_test_code": "import unittest\\nclass T(unittest.TestCase):\\n    def test_fail(self):\\n        self.assertEqual(1, 2)",
    "status": "REPORTED"
  }
]
```
Please fix this immediately.
"""
        findings = self.runner.parse_response(response_text)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "BUG-DIV-ZERO")
        self.assertEqual(findings[0].severity, "P0")
        self.assertEqual(findings[0].location, "app.py:10")

    def test_parse_response_empty_findings(self) -> None:
        """機能EEE.2: 指摘なし（全件承認）の空リストを正常にパースできること。"""
        response_text = '{"findings": []}'
        findings = self.runner.parse_response(response_text)
        self.assertEqual(findings, [])

    def test_audit_execution_with_cmd_template(self) -> None:
        """機能EEE.2: 外部コマンドテンプレート経由で監査を実行し構造化Findingを取得できること。"""
        cmd_template = (
            'echo \'[{"finding_id": "CMD-001", "severity": "P1", '
            '"spec_reference": "SPEC.md", "location": "main.py:5", '
            '"description": "Cmd finding", "reproduction_test_code": "import unittest\\nclass T(unittest.TestCase):\\n    def test_x(self): self.fail()", '
            '"status": "REPORTED"}]\''
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd_template,
            model_name="claude-opus-5.5",
        )
        findings = runner.audit(spec_content="# SPEC", git_diff="diff --git a/main.py")
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "CMD-001")
        self.assertEqual(findings[0].auditor_model, "claude-opus-5.5")

    def test_audit_finding_auditor_model_persisted_in_audit_store(self) -> None:
        """機能HHH.2: IndependentAuditRunner が設定した auditor_model が AuditStore で保存・復元されること。"""
        import tempfile

        from auto_developer.audit_store import AuditStore

        cmd_template = (
            'echo \'[{"finding_id": "CMD-002", "severity": "P0", '
            '"spec_reference": "SPEC.md", "location": "calc.py:1", '
            '"description": "Test finding", "reproduction_test_code": "import unittest\\nclass T(unittest.TestCase):\\n    def test_fail(self): self.fail()", '
            '"status": "REPORTED"}]\''
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd_template,
            model_name="claude-opus-5.5",
        )
        findings = runner.audit(spec_content="# SPEC", git_diff="diff --git a/calc.py")
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        self.assertEqual(finding.auditor_model, "claude-opus-5.5")

        with tempfile.TemporaryDirectory() as tmpdir:
            store = AuditStore(repo_dir=Path(tmpdir))
            store.save_finding(finding)
            loaded = store.get_finding("CMD-002")
            self.assertIsNotNone(loaded)
            assert loaded is not None
            self.assertEqual(loaded.auditor_model, "claude-opus-5.5")


class TestIndependentAuditAcceptanceCriteriaHarness(unittest.TestCase):
    """機能NNN.3: 独立第三者監査の6大受入条件（実行隔離・偽陽性耐性・Evidence改ざん検知・
    複数ワーカー耐障害性・Multi-LLM境界・Level 7継続自己修復）を網羅する体系的監査テストスイート。
    """

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        import subprocess

        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Audit Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "audit@example.com"], cwd=self.repo_dir, check=True)

        src_dir = self.repo_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "sample_module.py").write_text(
            "def compute_val(x: int) -> int:\n    return x * 2\n", encoding="utf-8"
        )
        (self.repo_dir / "SPEC.md").write_text("# Spec\nMust compute correctly.\n", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# Plan\n- [x] Initial task\n", encoding="utf-8")
        (self.repo_dir / "NIGHTLY_REPORT.md").write_text("# Report\nInitial\n", encoding="utf-8")
        (self.repo_dir / ".gitignore").write_text(".dev/\nlogs/\n.env\n", encoding="utf-8")
        (self.repo_dir / ".env").write_text("CONFIDENTIAL_API_KEY=secret_12345\n", encoding="utf-8")

        subprocess.run(["git", "add", "src/sample_module.py", "SPEC.md", "PLAN.md", "NIGHTLY_REPORT.md", ".gitignore"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init baseline"], cwd=self.repo_dir, check=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_criterion_1_execution_isolation(self) -> None:
        """受入条件1 [実行隔離]: OSサンドボックスにより、親リポジトリの機密ファイル読み取りおよび改変が遮断されること。"""
        from auto_developer.verifier import RedTestVerifier

        verifier = RedTestVerifier(repo_dir=self.repo_dir)
        # 親リポジトリの .env を読み込もうとする候補テスト
        env_path = str((self.repo_dir / ".env").resolve())
        leaking_code = f"""import unittest
from pathlib import Path

class LeakSecretTest(unittest.TestCase):
    def test_read_env(self):
        text = Path(r"{env_path}").read_text(encoding="utf-8")
        self.assertIn("CONFIDENTIAL", text)
"""
        result = verifier.verify_candidate_test(leaking_code)
        # サンドボックスにより PermissionError が発生し、UNHANDLED_EXCEPTION になること
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "UNHANDLED_EXCEPTION")
        self.assertIn("PermissionError", result.output)

    def test_criterion_2_false_positive_resistance(self) -> None:
        """受入条件2 [偽陽性耐性]: 固定値比較・無条件fail・形骸的呼出を伴う偽RedがTRIVIAL_REDとして排除され、真正なRedのみ受理されること。"""
        from auto_developer.verifier import RedTestVerifier

        verifier = RedTestVerifier(repo_dir=self.repo_dir)

        # 1. 固定値比較のみ (1 == 2)
        r1 = verifier.verify_candidate_test("import unittest\nclass T(unittest.TestCase):\n def test_f(self):\n  self.assertEqual(1, 2)")
        self.assertFalse(r1.is_reproduced)
        self.assertEqual(r1.reason, "TRIVIAL_RED")

        # 2. 形骸的プロダクト呼出 + 固定値比較
        fake_code = """import unittest
from sample_module import compute_val
class T(unittest.TestCase):
    def test_f(self):
        compute_val(1)
        self.assertEqual(1, 2)
"""
        r2 = verifier.verify_candidate_test(fake_code)
        self.assertFalse(r2.is_reproduced)
        self.assertEqual(r2.reason, "TRIVIAL_RED")

        # 3. 真正な因果依存アサーション
        genuine_code = """import unittest
from sample_module import compute_val
class T(unittest.TestCase):
    def test_f(self):
        res = compute_val(2)
        self.assertEqual(res, 999)
"""
        r3 = verifier.verify_candidate_test(genuine_code)
        self.assertTrue(r3.is_reproduced)
        self.assertEqual(r3.reason, "GENUINE_RED")

    def test_criterion_3_evidence_tamper_detection_and_bundle_integrity(self) -> None:
        """受入条件3 [Evidence改ざん検知と暗号学的完全性]: 改ざんされたバンドルを第三者検証ツールが即座に INVALID と検知すること。"""
        import hashlib
        import json
        import subprocess

        from auto_developer.bundler import EvidenceBundler

        # 正常な Evidence Record を作成
        records_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
        records_dir.mkdir(parents=True, exist_ok=True)
        res_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True)
        head_commit = res_commit.stdout.strip()
        res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo_dir, capture_output=True, text=True, check=True)
        head_tree = res_tree.stdout.strip()
        spec_hash = hashlib.sha256((self.repo_dir / "SPEC.md").read_bytes()).hexdigest()

        record = {
            "task_id": "1",
            "checkpoint": "chk1",
            "commit": head_commit,
            "tree_sha": head_tree,
            "spec_sha": spec_hash,
            "checker": {"decision": "APPROVE", "reason": "Criteria satisfied"},
            "status": "COMPLETED",
            "evidence_token": "token-xyz-123",
            "timestamp": "2026-11-14T12:00:00Z",
        }
        (records_dir / "task_1.json").write_text(json.dumps(record), encoding="utf-8")

        bundler = EvidenceBundler(repo_dir=self.repo_dir)
        bundle_path = self.repo_dir / "valid_bundle.zip"
        bundler.create_bundle(dest_path=bundle_path, job_id="job-nnn-3")
        self.assertTrue(bundle_path.exists())

        # 正常なバンドルの検証
        verif_ok = bundler.verify_bundle(bundle_path)
        self.assertEqual(verif_ok.status, "VERIFIED")

        # バンドル内のファイルを1バイト改ざん
        import zipfile
        tampered_path = self.repo_dir / "tampered_bundle.zip"
        with zipfile.ZipFile(bundle_path, "r") as zin, zipfile.ZipFile(tampered_path, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "SPEC.md":
                    data = data + b"\n# TAMPERED LINE"
                zout.writestr(item, data)

        verif_bad = bundler.verify_bundle(tampered_path)
        self.assertEqual(verif_bad.status, "INVALID")
        self.assertTrue(any("Hash mismatch" in err or "TAMPERED" in err for err in verif_bad.errors))

    def test_criterion_4_multi_worker_resilience_and_isolation(self) -> None:
        """受入条件4 [複数ワーカー耐障害性]: 分散 Git worktree による並行ワーカーが物理分離され安全に後処理されること。"""
        from auto_developer.worktree import WorktreeManager

        wt_manager = WorktreeManager(repo_dir=self.repo_dir)
        wt1 = wt_manager.create_worktree(team_id="worker-alpha")
        wt2 = wt_manager.create_worktree(team_id="worker-beta")

        self.assertTrue(wt1.exists())
        self.assertTrue(wt2.exists())
        self.assertNotEqual(wt1.resolve(), wt2.resolve())

        # ワーカーalphaでの変更がワーカーbetaに漏洩しないこと
        (wt1 / "src" / "alpha_file.py").write_text("# alpha", encoding="utf-8")
        self.assertFalse((wt2 / "src" / "alpha_file.py").exists())

        wt_manager.cleanup_all(force=True)
        self.assertFalse(wt1.exists())
        self.assertFalse(wt2.exists())

    def test_criterion_5_multi_llm_audit_boundary(self) -> None:
        """受入条件5 [Multi-LLM 監査境界]: 同一モデルファミリーによる自己監査が遮断され、異系統モデル間のみ独立承認されること。"""
        from auto_developer.audit import verify_model_independence

        # 同一ファミリー違反
        ok_same, msg_same = verify_model_independence("gemini-3.8-flash", "gemini-2.5-pro")
        self.assertFalse(ok_same)
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", msg_same)
        self.assertIn("same family", msg_same)

        # 異系統モデル適合 (Gemini Maker vs Claude Auditor)
        ok_diff1, _ = verify_model_independence("gemini-3.8-flash", "claude-opus-5.5")
        self.assertTrue(ok_diff1)

        # 異系統モデル適合 (Gemini Maker vs OpenAI Auditor)
        ok_diff2, _ = verify_model_independence("gemini-3.8-flash", "gpt-5")
        self.assertTrue(ok_diff2)

    def test_criterion_6_level7_continuous_self_refinement_loop(self) -> None:
        """受入条件6 [Level 7継続自己修復]: 真正な監査指摘のみがPLAN.mdに自動登録され、偽Red指摘は隔離・拒絶されること。"""
        from auto_developer.audit import AuditFinding
        from auto_developer.audit_store import AuditStore
        from auto_developer.dispatcher import AuditDispatcher
        from auto_developer.verifier import RedTestVerifier

        store = AuditStore(repo_dir=self.repo_dir)
        verifier = RedTestVerifier(repo_dir=self.repo_dir)
        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, verifier=verifier, audit_store=store)

        plan_file = self.repo_dir / "PLAN.md"
        plan_before = plan_file.read_text(encoding="utf-8")

        # 1. 偽Red指摘のディスパッチ -> REJECTED, PLAN.md 不変
        fake_finding = AuditFinding(
            finding_id="FAKE-001",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/sample_module.py:1",
            description="Fake finding with constant mismatch",
            reproduction_test_code="""import unittest
from sample_module import compute_val
class FakeTest(unittest.TestCase):
    def test_fake(self):
        compute_val(1)
        self.assertEqual(1, 2)
""",
            status="REPORTED",
            auditor_model="claude-opus-5.5",
        )
        dispatched_fake = dispatcher.dispatch_finding(fake_finding, plan_path=plan_file)
        self.assertEqual(dispatched_fake.status, "REJECTED")
        self.assertEqual(dispatched_fake.red_verification_reason, "TRIVIAL_RED")
        self.assertEqual(plan_file.read_text(encoding="utf-8"), plan_before)

        # 2. 真正Red指摘のディスパッチ -> DISPATCHED, PLAN.md 更新
        genuine_finding = AuditFinding(
            finding_id="GENUINE-001",
            severity="P0",
            spec_reference="SPEC.md",
            location="src/sample_module.py:2",
            description="Genuine computation error",
            reproduction_test_code="""import unittest
from sample_module import compute_val
class GenuineTest(unittest.TestCase):
    def test_calc(self):
        res = compute_val(2)
        self.assertEqual(res, 999)
""",
            status="REPORTED",
            auditor_model="claude-opus-5.5",
        )
        dispatched_genuine = dispatcher.dispatch_finding(genuine_finding, plan_path=plan_file)
        self.assertEqual(dispatched_genuine.status, "DISPATCHED")
        self.assertEqual(dispatched_genuine.red_verification_reason, "GENUINE_RED")
        plan_after = plan_file.read_text(encoding="utf-8")
        self.assertIn("GENUINE-001", plan_after)
        self.assertIn("Genuine computation error", plan_after)

        # 3. 後続再代入を含む真正Red指摘のディスパッチ (機能OOO.3: Program Order の受入保証)
        reassign_finding = AuditFinding(
            finding_id="GENUINE-REASSIGN-001",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/sample_module.py:2",
            description="Reassignment after assertion bug",
            reproduction_test_code="""import unittest
from sample_module import compute_val
class ReassignTest(unittest.TestCase):
    def test_reassign(self):
        res = compute_val(2)
        self.assertEqual(res, 999)
        res = 12345
""",
            status="REPORTED",
            auditor_model="claude-opus-5.5",
        )
        dispatched_reassign = dispatcher.dispatch_finding(reassign_finding, plan_path=plan_file)
        self.assertEqual(dispatched_reassign.status, "DISPATCHED")
        self.assertEqual(dispatched_reassign.red_verification_reason, "GENUINE_RED")
        self.assertIn("GENUINE-REASSIGN-001", plan_file.read_text(encoding="utf-8"))

        # 4. 条件付き self.fail() を含む真正Red指摘のディスパッチ (機能OOO.3: Guarded fail の受入保証)
        guarded_fail_finding = AuditFinding(
            finding_id="GENUINE-GUARD-FAIL-001",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/sample_module.py:2",
            description="Guarded fail bug",
            reproduction_test_code="""import unittest
from sample_module import compute_val
class GuardedFailTest(unittest.TestCase):
    def test_guard(self):
        if compute_val(2) != 999:
            self.fail("Guarded condition failure detected")
""",
            status="REPORTED",
            auditor_model="claude-opus-5.5",
        )
        dispatched_guard = dispatcher.dispatch_finding(guarded_fail_finding, plan_path=plan_file)
        self.assertEqual(dispatched_guard.status, "DISPATCHED")
        self.assertEqual(dispatched_guard.red_verification_reason, "GENUINE_RED")
        self.assertIn("GENUINE-GUARD-FAIL-001", plan_file.read_text(encoding="utf-8"))

        # 5. 条件分岐後の変数合流を伴う真正Red指摘のディスパッチ (機能PPP.3: Merge & Uncertainty Protection の受入保証)
        branch_merge_finding = AuditFinding(
            finding_id="GENUINE-BRANCH-MERGE-001",
            severity="P1",
            spec_reference="SPEC.md PPP.1",
            location="src/sample_module.py:2",
            description="Branch merge variable evaluation bug",
            reproduction_test_code="""import unittest
from sample_module import compute_val
class BranchMergeTest(unittest.TestCase):
    def test_merge(self):
        x = 1
        if compute_val(2) == 999:
            x = 2
        self.assertEqual(x, 999)
""",
            status="REPORTED",
            auditor_model="claude-opus-5.5",
        )
        dispatched_merge = dispatcher.dispatch_finding(branch_merge_finding, plan_path=plan_file)
        self.assertEqual(dispatched_merge.status, "DISPATCHED")
        self.assertEqual(dispatched_merge.red_verification_reason, "GENUINE_RED")
        self.assertIn("GENUINE-BRANCH-MERGE-001", plan_file.read_text(encoding="utf-8"))


class TestLiveExternalAuditPipelineBaseline(unittest.TestCase):
    """機能QQQ.1: 実外部異系統モデル独立監査パイプラインの構成とベースライン固定。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        import subprocess

        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Audit Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "audit@example.com"], cwd=self.repo_dir, check=True)

        (self.repo_dir / "SPEC.md").write_text("# Spec QQQ\nMust compute square.\n", encoding="utf-8")
        src_dir = self.repo_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "math_mod.py").write_text("def square(x: int) -> int:\n    return x + 2\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "base commit"], cwd=self.repo_dir, check=True)
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        self.baseline_sha = res.stdout.strip()

        # 追加コミットを作成
        (src_dir / "math_mod.py").write_text("def square(x: int) -> int:\n    return x * 3\n", encoding="utf-8")
        subprocess.run(["git", "commit", "-am", "second commit"], cwd=self.repo_dir, check=True)
        res_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir, check=True, capture_output=True, text=True)
        self.head_sha = res_head.stdout.strip()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_audit_config_and_runner_baseline_binding(self) -> None:
        """QQQ.1: AuditConfig と IndependentAuditRunner が baseline_commit を保持・検証すること。"""
        from auto_developer.audit import AuditConfig

        config = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="claude-opus-5.5",
            baseline_commit=self.baseline_sha,
        )
        self.assertEqual(config.baseline_commit, self.baseline_sha)
        self.assertTrue(config.validate())

        runner = IndependentAuditRunner(
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.baseline_sha,
        )
        self.assertEqual(runner.baseline_commit, self.baseline_sha)

    def test_audit_repository_extracts_baseline_diff_and_executes(self) -> None:
        """QQQ.1: audit_repository が baseline_commit からの diff を抽出して独立プロンプトで実行すること。"""
        cmd_template = (
            'echo \'[{"finding_id": "FIND-QQQ-001", "severity": "P1", '
            '"spec_reference": "SPEC.md QQQ", "location": "src/math_mod.py:2", '
            '"description": "Square implementation is incorrect", '
            '"reproduction_test_code": "import unittest\\nfrom math_mod import square\\nclass T(unittest.TestCase):\\n    def test_sq(self): self.assertEqual(square(3), 9)", '
            '"status": "REPORTED"}]\''
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd_template,
            model_name="claude-opus-5.5",
            baseline_commit=self.baseline_sha,
        )
        findings = runner.audit_repository(
            repo_dir=self.repo_dir,
            spec_path=self.repo_dir / "SPEC.md",
            baseline_commit=self.baseline_sha,
            head_commit=self.head_sha,
        )
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "FIND-QQQ-001")
        self.assertEqual(findings[0].baseline_commit, self.baseline_sha)
        self.assertEqual(findings[0].auditor_model, "claude-opus-5.5")

    def test_audit_store_persists_baseline_commit(self) -> None:
        """QQQ.1: AuditFinding の baseline_commit が AuditStore で永続化・復元されること。"""
        from auto_developer.audit import AuditFinding
        from auto_developer.audit_store import AuditStore

        finding = AuditFinding(
            finding_id="STORE-BASELINE-001",
            severity="P0",
            spec_reference="SPEC.md QQQ",
            location="src/test.py:1",
            description="Baseline store test",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test_x(self): self.fail()",
            status="REPORTED",
            baseline_commit=self.baseline_sha,
            auditor_model="claude-opus-5.5",
        )
        store = AuditStore(repo_dir=self.repo_dir)
        store.save_finding(finding)
        loaded = store.get_finding("STORE-BASELINE-001")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.baseline_commit, self.baseline_sha)


class TestLiveExternalAuditDefenseScreening(unittest.TestCase):
    """機能QQQ.2: 外部監査役による未知欠陥探索と Auto Developer 防壁による自動選別実証。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        import subprocess

        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Audit Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "audit@example.com"], cwd=self.repo_dir, check=True)

        (self.repo_dir / "SPEC.md").write_text("# Spec QQQ.2\nMust compute add.\n", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# Plan\n- [x] Initial setup\n", encoding="utf-8")
        src_dir = self.repo_dir / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "calc.py").write_text("def add_vals(a: int, b: int) -> int:\n    return a + b + 10\n", encoding="utf-8")

        tests_dir = self.repo_dir / "tests"
        tests_dir.mkdir(parents=True, exist_ok=True)
        (tests_dir / "test_existing.py").write_text("import unittest\nclass T(unittest.TestCase):\n    def test_pass(self):\n        self.assertTrue(True)\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init calc"], cwd=self.repo_dir, check=True)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_screen_and_dispatch_findings_separates_genuine_from_adversarial(self) -> None:
        """QQQ.2: 外部監査役から投入された指摘バッチ（真正Red、Trivial Red、機密読取、改ざん試行）を多層防壁で自動選別すること。"""
        from auto_developer.audit import AuditFinding, IndependentAuditRunner
        from auto_developer.dispatcher import AuditDispatcher
        from auto_developer.verifier import RedTestVerifier

        # 1. 真正な不具合指摘 (GENUINE_RED: add_vals(2, 3) が 15 を返すため失敗)
        genuine_finding = AuditFinding(
            finding_id="AUD-GENUINE-001",
            severity="P1",
            spec_reference="SPEC.md QQQ.2",
            location="src/calc.py:2",
            description="add_vals returns incorrect sum",
            reproduction_test_code="""import unittest
from calc import add_vals
class TestAdd(unittest.TestCase):
    def test_correct_addition(self):
        self.assertEqual(add_vals(2, 3), 5)
""",
            status="REPORTED",
            auditor_model="claude-opus-5.5",
        )

        # 2. 巧妙な偽Red (TRIVIAL_RED: 関数は呼ぶが固定値比較で合成失敗)
        trivial_finding = AuditFinding(
            finding_id="AUD-TRIVIAL-001",
            severity="P1",
            spec_reference="SPEC.md QQQ.2",
            location="src/calc.py:2",
            description="Trivial fixed comparison fake red",
            reproduction_test_code="""import unittest
from calc import add_vals
class TestFake(unittest.TestCase):
    def test_fake(self):
        add_vals(1, 2)
        self.assertEqual(1, 2)
""",
            status="REPORTED",
            auditor_model="claude-opus-5.5",
        )

        # 3. リポジトリ改ざん試行 (REPO_TAMPER: 元リポジトリの改変を試みる)
        tamper_finding = AuditFinding(
            finding_id="AUD-TAMPER-001",
            severity="P0",
            spec_reference="SPEC.md QQQ.2",
            location="src/calc.py:1",
            description="Malicious repository tampering",
            reproduction_test_code=f"""import unittest
from pathlib import Path
class TestTamper(unittest.TestCase):
    def test_tamper(self):
        target = Path("{self.repo_dir}") / "tamper_marker.txt"
        target.write_text("HACKED", encoding="utf-8")
        self.fail("tamper executed")
""",
            status="REPORTED",
            auditor_model="claude-opus-5.5",
        )

        verifier = RedTestVerifier(repo_dir=self.repo_dir)
        dispatcher = AuditDispatcher(repo_dir=self.repo_dir, verifier=verifier)
        runner = IndependentAuditRunner(model_name="claude-opus-5.5")

        batch = [genuine_finding, trivial_finding, tamper_finding]
        plan_path = self.repo_dir / "PLAN.md"

        results = runner.screen_and_dispatch_findings(
            findings=batch,
            dispatcher=dispatcher,
            plan_path=plan_path,
        )

        # 真正指摘のみが DISPATCHED になり、PLAN.md に登録されていること
        self.assertEqual(len(results["dispatched"]), 1)
        self.assertEqual(results["dispatched"][0].finding_id, "AUD-GENUINE-001")
        self.assertEqual(results["dispatched"][0].status, "DISPATCHED")
        self.assertEqual(results["dispatched"][0].red_verification_reason, "GENUINE_RED")

        # 偽Red・改ざん指摘は REJECTED として隔離されていること
        self.assertEqual(len(results["rejected"]), 2)
        rejected_ids = [f.finding_id for f, _reason in results["rejected"]]
        self.assertIn("AUD-TRIVIAL-001", rejected_ids)
        self.assertIn("AUD-TAMPER-001", rejected_ids)

        plan_content = plan_path.read_text(encoding="utf-8")
        self.assertIn("AUD-GENUINE-001", plan_content)
        self.assertNotIn("AUD-TRIVIAL-001", plan_content)
        self.assertNotIn("AUD-TAMPER-001", plan_content)


if __name__ == "__main__":
    unittest.main()
