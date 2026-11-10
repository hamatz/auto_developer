from __future__ import annotations

import sys
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


if __name__ == "__main__":
    unittest.main()
