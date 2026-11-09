from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import AuditFinding
from auto_developer.audit_store import AuditStore


class TestAuditStoreImmutability(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.audit_dir = Path(self.tmp_dir.name)
        self.store = AuditStore(audit_dir=self.audit_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_save_finding_establishes_immutable_code_hash(self) -> None:
        """機能GGG.2: 初回登録時に code_hash が計算・保存されること。"""
        repro_code = "def test_repro(): assert False"
        finding = AuditFinding(
            finding_id="BUG-IMM-001",
            severity="P0",
            spec_reference="SPEC.md",
            location="src/app.py:10",
            description="First finding",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
        )
        json_path, py_path = self.store.save_finding(finding)
        self.assertTrue(json_path.exists())
        self.assertTrue(py_path.exists())

        loaded = self.store.load_finding("BUG-IMM-001")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.reproduction_test_code, repro_code)
        self.assertIsNotNone(loaded.code_hash)
        assert loaded.code_hash is not None
        self.assertEqual(len(loaded.code_hash), 64)

    def test_save_finding_rejects_tampered_repro_code(self) -> None:
        """機能GGG.2: 既存の再現コードと異なるコードでの再保存、またはディスク上改ざん後の再保存を拒絶すること。"""
        repro_code = "def test_repro(): assert False"
        finding = AuditFinding(
            finding_id="BUG-IMM-002",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/app.py:20",
            description="Tampering test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
        )
        self.store.save_finding(finding)

        # 1. 外部からディスク上の .py ファイルが改ざんされたケース
        py_file = self.audit_dir / "repro_tests" / "BUG-IMM-002.py"
        py_file.write_text("def test_tampered(): pass", encoding="utf-8")

        # 状態更新しようとした場合、既存の真のコードハッシュとディスク上の改ざんが検知され拒絶されること
        finding.status = "RESOLVED"
        with self.assertRaises(ValueError) as ctx:
            self.store.save_finding(finding)
        self.assertIn("CODE_HASH_TAMPERED", str(ctx.exception))

        # 2. 異なる再現コードを持つ Finding オブジェクトで上書きしようとしたケース
        finding2 = AuditFinding(
            finding_id="BUG-IMM-002",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/app.py:20",
            description="Tampering test with different code",
            reproduction_test_code="def test_malicious(): os.system('echo pwned')",
            status="DISPATCHED",
        )
        with self.assertRaises(ValueError) as ctx2:
            self.store.save_finding(finding2)
        self.assertIn("CODE_HASH_TAMPERED", str(ctx2.exception))


if __name__ == "__main__":
    unittest.main()
