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


if __name__ == "__main__":
    unittest.main()
