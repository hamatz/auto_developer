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
from auto_developer.evidence import EvidenceStore


class TestAuditResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        self.src_dir = self.repo_dir / "src"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.immunity_dir = self.repo_dir / "tests" / "immunity"
        self.immunity_dir.mkdir(parents=True, exist_ok=True)

        # 初期アプリコード（バグあり）
        self.app_py = self.src_dir / "app.py"
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n    return a // b\n",
            encoding="utf-8",
        )

        self.audit_store = AuditStore(repo_dir=self.repo_dir)
        self.manager = AuditResolutionManager(repo_dir=self.repo_dir)

        # 初期 LESSONS.md
        self.lessons_md = self.repo_dir / "LESSONS.md"
        self.lessons_md.write_text(
            "# Repository Lessons Learned\n\n## 恒久教訓インデックス\n",
            encoding="utf-8",
        )

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_audit_resolution_resolves_and_promotes_immunity(self) -> None:
        """機能EEE.3: 修正完了したAUDタスクがBefore Red/After Greenを実証し、免疫テストへ自動昇格されること。"""
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestDiv(unittest.TestCase):\n"
            "    def test_zero(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        finding = AuditFinding(
            finding_id="BUG-ZERO-DIV",
            severity="P0",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Divide by zero in safe_div",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
        )
        self.audit_store.save_finding(finding)

        # 1. 修正前: アプリコードはバグがあるため After Green は失敗するはず
        res_before = self.manager.resolve_task("AUD-BUG-ZERO-DIV")
        self.assertFalse(res_before["success"])
        self.assertIn("AFTER_GREEN_FAILED", res_before["reason"])

        # 2. 修正実施 (Maker によるバグ修正)
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    if b == 0:\n"
            "        return 0\n"
            "    return a // b\n",
            encoding="utf-8",
        )

        # 3. 修正後: resolve_task が成功し、After Green が確認されること
        res_after = self.manager.resolve_task("AUD-BUG-ZERO-DIV")
        self.assertTrue(res_after["success"])
        proof = res_after["proof"]
        self.assertEqual(proof["finding_id"], "BUG-ZERO-DIV")
        self.assertEqual(proof["before_status"], "VERIFIED_RED")
        self.assertEqual(proof["after_status"], "GREEN")

        # 4. 免疫テストとして昇格配置されたことを検証
        promoted_path = self.repo_dir / proof["immunity_test_path"]
        self.assertTrue(promoted_path.exists())
        self.assertIn("class TestDiv", promoted_path.read_text(encoding="utf-8"))

        # 5. Finding のステータスが RESOLVED に更新されていること
        updated_finding = self.audit_store.get_finding("BUG-ZERO-DIV")
        self.assertIsNotNone(updated_finding)
        self.assertEqual(updated_finding.status, "RESOLVED")

    def test_evidence_records_red_green_proof(self) -> None:
        """機能EEE.3: Before Red / After Green 証跡が Evidence Record に確実に拘束・保存されること。"""
        ev_store = EvidenceStore(repo_dir=self.repo_dir)
        proof = {
            "finding_id": "BUG-ZERO-DIV",
            "before_status": "VERIFIED_RED",
            "after_status": "GREEN",
            "immunity_test_path": "tests/immunity/test_immunity_BUG_ZERO_DIV.py",
        }
        ev = ev_store.issue_evidence(
            task_id="AUD-BUG-ZERO-DIV",
            checkpoint="abc1234",
            tree_sha="tree123",
            spec_sha="spec123",
            details={
                "verification": {
                    "exit_code": 0,
                    "red_green_proof": proof,
                }
            },
        )
        self.assertEqual(ev["details"]["verification"]["red_green_proof"]["after_status"], "GREEN")

        consumed = ev_store.verify_and_consume("AUD-BUG-ZERO-DIV", checkpoint="abc1234")
        self.assertTrue(consumed)

        archived = ev_store.archive_record(
            task_id="AUD-BUG-ZERO-DIV",
            verification_info={"exit_code": 0, "red_green_proof": proof},
        )
        self.assertIsNotNone(archived)
        self.assertEqual(archived["verification"]["red_green_proof"]["finding_id"], "BUG-ZERO-DIV")

    def test_resolve_task_rejects_overwriting_different_existing_immunity_test(self) -> None:
        """機能FFF.1: 異なる内容の既存免疫テストが存在する場合、IMMUNITY_COLLISION で拒絶され元ファイルが保護されること。"""
        # アプリ修正（Green状態）
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    return 0 if b == 0 else a // b\n",
            encoding="utf-8",
        )
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestNew(unittest.TestCase):\n"
            "    def test_new(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        finding = AuditFinding(
            finding_id="BUG-COLLIDE",
            severity="P0",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Collide test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
        )
        self.audit_store.save_finding(finding)

        # 既存の免疫テスト（異なる内容）を事前配置
        target_immunity_file = self.repo_dir / "tests" / "immunity" / "test_immunity_BUG_COLLIDE.py"
        original_content = "# ORIGINAL IMMUNITY TEST CONTENT\n"
        target_immunity_file.write_text(original_content, encoding="utf-8")

        res = self.manager.resolve_task("AUD-BUG-COLLIDE")
        self.assertFalse(res["success"])
        self.assertIn("IMMUNITY_COLLISION", res["reason"])

        # 元ファイルが一切上書き・削除されていないこと
        self.assertEqual(target_immunity_file.read_text(encoding="utf-8"), original_content)

    def test_resolve_task_idempotent_when_existing_immunity_test_has_same_hash(self) -> None:
        """機能FFF.1: 同一ハッシュの既存免疫テストが存在する場合、上書きせず冪等に成功すること。"""
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    return 0 if b == 0 else a // b\n",
            encoding="utf-8",
        )
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestSame(unittest.TestCase):\n"
            "    def test_same(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        finding = AuditFinding(
            finding_id="BUG-IDEMPOTENT",
            severity="P1",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Idempotent test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
        )
        self.audit_store.save_finding(finding)

        # 同一内容のファイルを事前に配置
        target_immunity_file = self.repo_dir / "tests" / "immunity" / "test_immunity_BUG_IDEMPOTENT.py"
        target_immunity_file.write_text(repro_code, encoding="utf-8")

        res = self.manager.resolve_task("AUD-BUG-IDEMPOTENT")
        self.assertTrue(res["success"])
        self.assertEqual(target_immunity_file.read_text(encoding="utf-8"), repro_code)

    def test_resolve_task_rejects_unverified_before_red_status_reported(self) -> None:
        """機能FFF.2: status='REPORTED' のまま実証されていないFindingは UNVERIFIED_BEFORE_RED で拒絶されること。"""
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    return 0 if b == 0 else a // b\n",
            encoding="utf-8",
        )
        finding = AuditFinding(
            finding_id="BUG-UNVERIFIED",
            severity="P1",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Unverified red finding",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n",
            status="REPORTED",
        )
        self.audit_store.save_finding(finding)

        res = self.manager.resolve_task("AUD-BUG-UNVERIFIED")
        self.assertFalse(res["success"])
        self.assertIn("UNVERIFIED_BEFORE_RED", res["reason"])

    def test_resolve_task_rejects_missing_genuine_red_proof(self) -> None:
        """機能FFF.2: statusがVERIFIED_REDでも GENUINE_RED 証跡を持たないFindingは拒絶されること。"""
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    return 0 if b == 0 else a // b\n",
            encoding="utf-8",
        )
        finding = AuditFinding(
            finding_id="BUG-NO-PROOF",
            severity="P1",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Finding lacking GENUINE_RED proof",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n",
            status="VERIFIED_RED",
            red_verification_reason=None,
        )
        self.audit_store.save_finding(finding)

        res = self.manager.resolve_task("AUD-BUG-NO-PROOF")
        self.assertFalse(res["success"])
        self.assertIn("UNVERIFIED_BEFORE_RED", res["reason"])

    def test_resolve_task_binds_full_cryptographic_proof(self) -> None:
        """機能FFF.2: 解決時に before_commit, after_commit, repro_test_sha256 が完全拘束されること。"""
        import hashlib

        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    return 0 if b == 0 else a // b\n",
            encoding="utf-8",
        )
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestFull(unittest.TestCase):\n"
            "    def test_full(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        code_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
        finding = AuditFinding(
            finding_id="BUG-CRYPTO-PROOF",
            severity="P0",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Full cryptographic proof test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha="1111222233334444555566667777888899990000",
            code_hash=code_hash,
        )
        self.audit_store.save_finding(finding)

        res = self.manager.resolve_task("AUD-BUG-CRYPTO-PROOF")
        self.assertTrue(res["success"])
        proof = res["proof"]
        self.assertEqual(proof["before_commit"], "1111222233334444555566667777888899990000")
        self.assertEqual(proof["repro_test_sha256"], code_hash)
        self.assertEqual(proof["before_red_reason"], "GENUINE_RED")
        self.assertEqual(proof["before_status"], "VERIFIED_RED")
        self.assertEqual(proof["after_status"], "GREEN")

    def test_resolve_task_fails_when_record_lesson_fails(self) -> None:
        """機能FFF.3: LESSONS.md への記録に失敗した場合、RESOLVEDにせず LEARNING_RECORD_FAILED で停止すること。"""
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    return 0 if b == 0 else a // b\n",
            encoding="utf-8",
        )
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestLessonFail(unittest.TestCase):\n"
            "    def test_l(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        finding = AuditFinding(
            finding_id="BUG-LESSON-FAIL",
            severity="P0",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Lesson fail test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
        )
        self.audit_store.save_finding(finding)

        # LESSONS.md が存在しない状態（setUp で作成していない）
        lessons_path = self.repo_dir / "LESSONS.md"
        if lessons_path.exists():
            lessons_path.unlink()

        res = self.manager.resolve_task("AUD-BUG-LESSON-FAIL")
        self.assertFalse(res["success"])
        self.assertIn("LEARNING_RECORD_FAILED", res["reason"])

        # Finding のステータスが LEARNING_RECORD_FAILED に更新され、RESOLVED になっていないこと
        updated_finding = self.audit_store.get_finding("BUG-LESSON-FAIL")
        self.assertIsNotNone(updated_finding)
        self.assertEqual(updated_finding.status, "LEARNING_RECORD_FAILED")


if __name__ == "__main__":
    unittest.main()
