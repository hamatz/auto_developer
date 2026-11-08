from __future__ import annotations

import sys
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import AuditFinding, AuditRunner


class TestAuditRunner(unittest.TestCase):
    def setUp(self) -> None:
        self.runner = AuditRunner()

    def test_audit_finding_model_fields(self) -> None:
        """機能CCC.2.1: AuditFinding が仕様の全フィールドを保持し、シリアライズ可能であること。"""
        finding = AuditFinding(
            finding_id="FINDING-001",
            severity="P1",
            spec_reference="SPEC.md CCC.1",
            location="src/auto_developer/guard.py:45",
            description="AST check missing else branch validation",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    pass",
            status="REPORTED",
        )
        self.assertEqual(finding.finding_id, "FINDING-001")
        self.assertEqual(finding.severity, "P1")
        self.assertEqual(finding.spec_reference, "SPEC.md CCC.1")
        self.assertEqual(finding.location, "src/auto_developer/guard.py:45")
        self.assertEqual(finding.description, "AST check missing else branch validation")
        self.assertEqual(finding.status, "REPORTED")

        # to_dict / from_dict の対称性
        d = finding.to_dict()
        self.assertEqual(d["finding_id"], "FINDING-001")
        restored = AuditFinding.from_dict(d)
        self.assertEqual(restored, finding)

    def test_parse_valid_findings_from_raw_records(self) -> None:
        """機能CCC.2.2: 構造化入力から有効な AuditFinding リストを正常に生成できること。"""
        raw_data = [
            {
                "finding_id": "BUG-101",
                "severity": "P0",
                "spec_reference": "SPEC.md CCC.1.2",
                "location": "src/auto_developer/verifier.py",
                "description": "Root UID execution bypass",
                "reproduction_test_code": "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.assertEqual(1, 2)",
                "status": "REPORTED",
            }
        ]
        findings = self.runner.parse_findings(raw_data)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0].finding_id, "BUG-101")
        self.assertEqual(findings[0].severity, "P0")
        self.assertEqual(findings[0].status, "REPORTED")

        # run_audit 経由での呼び出し確認
        audit_findings = self.runner.run_audit(raw_data)
        self.assertEqual(audit_findings, findings)

    def test_rejects_missing_required_fields(self) -> None:
        """機能CCC.2.2: 必須フィールドが欠落している指摘は除外または無効化されること。"""
        invalid_data = [
            {
                "finding_id": "BUG-102",
                # missing severity
                "spec_reference": "SPEC.md",
                "location": "app.py",
                "description": "Missing severity",
                "reproduction_test_code": "test code",
            }
        ]
        with self.assertRaises(ValueError):
            self.runner.parse_findings(invalid_data)

    def test_rejects_invalid_severity(self) -> None:
        """機能CCC.2.2: 深刻度が不正な値 (P0, P1, P2, INFO 以外) の場合は拒絶されること。"""
        invalid_data = [
            {
                "finding_id": "BUG-103",
                "severity": "CRITICAL",  # 不正な深刻度
                "spec_reference": "SPEC.md",
                "location": "app.py",
                "description": "Invalid severity test",
                "reproduction_test_code": "test code",
            }
        ]
        with self.assertRaises(ValueError):
            self.runner.parse_findings(invalid_data)

    def test_rejects_empty_reproduction_test_code(self) -> None:
        """機能CCC.2.2: 再現テストコードが空または空白のみの場合は拒絶されること。"""
        invalid_data = [
            {
                "finding_id": "BUG-104",
                "severity": "P1",
                "spec_reference": "SPEC.md",
                "location": "app.py",
                "description": "Empty reproduction test code",
                "reproduction_test_code": "   \n\t  ",
            }
        ]
        with self.assertRaises(ValueError):
            self.runner.parse_findings(invalid_data)


if __name__ == "__main__":
    unittest.main()
