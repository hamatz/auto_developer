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
from auto_developer.dispatcher import AuditDispatcher
from auto_developer.verifier import RedTestVerifier


class TestAuditPersistence(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        self.plan_path = self.repo_dir / "PLAN.md"
        self.plan_path.write_text("# PLAN.md\n\n## ステップ\n", encoding="utf-8")
        self.audit_dir = self.repo_dir / ".dev" / "audit"
        self.store = AuditStore(audit_dir=self.audit_dir)
        self.verifier = RedTestVerifier(repo_dir=self.repo_dir)
        self.dispatcher = AuditDispatcher(
            repo_dir=self.repo_dir, verifier=self.verifier, audit_store=self.store
        )

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_persist_finding_saves_json_and_repro_test(self) -> None:
        """機能DDD.3: AuditStore が指摘メタデータを JSON に、再現コードを .py に保存できること。"""
        finding = AuditFinding(
            finding_id="FINDING-PERSIST-001",
            severity="P0",
            spec_reference="SPEC.md DDD.3",
            location="verifier.py:100",
            description="Persistence test finding",
            reproduction_test_code="""import unittest
class Repro(unittest.TestCase):
    def test_repro(self):
        self.assertEqual(1, 2)
""",
        )

        json_path, py_path = self.store.save_finding(finding)
        self.assertTrue(json_path.exists())
        self.assertTrue(py_path.exists())
        self.assertEqual(json_path.name, "FINDING-PERSIST-001.json")
        self.assertEqual(py_path.name, "FINDING-PERSIST-001.py")
        self.assertEqual(py_path.read_text(encoding="utf-8"), finding.reproduction_test_code)

        # JSON 内にハッシュ等のメタデータが含まれていること
        import json

        data = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual(data["finding_id"], "FINDING-PERSIST-001")
        self.assertIn("code_hash", data)
        self.assertIn("reproduction_test_file", data)

    def test_load_persisted_finding(self) -> None:
        """機能DDD.3: 保存された指摘を finding_id から正確に復元できること。"""
        finding = AuditFinding(
            finding_id="FINDING-LOAD-002",
            severity="P1",
            spec_reference="SPEC.md DDD.3",
            location="guard.py:20",
            description="Load finding test",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    pass",
        )
        self.store.save_finding(finding)

        loaded = self.store.load_finding("FINDING-LOAD-002")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.finding_id, "FINDING-LOAD-002")
        self.assertEqual(loaded.severity, "P1")
        self.assertEqual(loaded.reproduction_test_code, finding.reproduction_test_code)

    def test_dispatch_automatically_persists_verified_finding(self) -> None:
        """機能DDD.3: AuditDispatcher が VERIFIED_RED 指摘を登録する際、自動的に永続化すること。"""
        finding = AuditFinding(
            finding_id="AUTO-PERSIST-003",
            severity="P2",
            spec_reference="SPEC.md DDD.3",
            location="calc.py:5",
            description="Auto persist during dispatch",
            reproduction_test_code="""import unittest
class Repro(unittest.TestCase):
    def test_fail(self):
        self.assertEqual(10, 20)
""",
        )

        res = self.dispatcher.dispatch_finding(finding, plan_path=self.plan_path)
        self.assertEqual(res.status, "DISPATCHED")

        # 自動保存の確認
        loaded = self.store.load_finding("AUTO-PERSIST-003")
        self.assertIsNotNone(loaded)
        assert loaded is not None
        self.assertEqual(loaded.status, "DISPATCHED")

        # PLAN.md に保存先情報またはタスクが正しく登録されていること
        plan_content = self.plan_path.read_text(encoding="utf-8")
        self.assertIn("AUD-AUTO-PERSIST-003", plan_content)


if __name__ == "__main__":
    unittest.main()
