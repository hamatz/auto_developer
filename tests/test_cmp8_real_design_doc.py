"""CMP-8 (Step 7): 実物の設計書 docs/designs/autonomous_pipeline_orchestrator.md での確認テスト。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DESIGN_DOC_REL = "docs/designs/autonomous_pipeline_orchestrator.md"
_CONFLICT_IR_REL = "tests/fixtures/compile/autonomous_pipeline_orchestrator_conflict.json"
_RESOLVED_IR_REL = "tests/fixtures/compile/autonomous_pipeline_orchestrator_resolved.json"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _line_range_sha256(doc_bytes: bytes, start: int, end: int) -> str:
    lines = doc_bytes.splitlines(keepends=True)
    return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()


class TestCMP8RealDesignDoc(unittest.TestCase):
    def _run_cli(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "auto_developer.cli", *args],
            cwd=_REPO_ROOT,
            env=_clean_env(),
            check=False,
            capture_output=True,
            text=True,
        )

    def test_outline_on_real_design_doc_maps_sections_and_skips_report_template(self) -> None:
        proc = self._run_cli(["compile", "--outline", _DESIGN_DOC_REL, "--json"])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)

        data = json.loads(proc.stdout)
        self.assertEqual(data["schema"], "auto-dev-outline/1")
        self.assertEqual(data["missing"], [])

        doc_bytes = (_REPO_ROOT / _DESIGN_DOC_REL).read_bytes()
        self.assertEqual(data["source"]["path"], _DESIGN_DOC_REL)
        self.assertEqual(data["source"]["sha256"], hashlib.sha256(doc_bytes).hexdigest())

        sections = data["sections"]
        by_title = {sec["title"]: sec for sec in sections}

        expected_mappings = {
            "Overview": "purpose",
            "用語定義 (Glossary)": "terms",
            "The Problem": "purpose",
            "Goals": "requirements",
            "Non-Goals": "out_of_scope",
            "Architectural Decisions (Resolved Questions)": "decisions",
            "Critical User Journeys (CUJs)": "acceptance",
            "Technical Plan (Architecture)": "steps",
            "Alternatives Considered": "alternatives",
            "Detailed Implementation Plan": "steps",
            "Verification Plan": "verification",
        }
        for title, expected_ir in expected_mappings.items():
            self.assertIn(title, by_title, msg=f"Missing heading {title!r}")
            sec = by_title[title]
            self.assertEqual(sec["ir_element"], expected_ir)
            start, end = sec["lines"]
            self.assertEqual(sec["sha256"], _line_range_sha256(doc_bytes, start, end))

        # NIGHTLY_REPORT.md template headings inside fenced code block must not be extracted.
        titles = [sec["title"] for sec in sections]
        self.assertNotIn("Nightly Execution Report: YYYY-MM-DD HH:MM:SS", titles)
        self.assertNotIn("1. Summary", titles)
        self.assertNotIn("2. Completed Tasks", titles)
        self.assertNotIn("3. Quarantined Task (If Failed)", titles)

    def test_check_conflict_ir_detects_l5_decision4_forbids_error(self) -> None:
        proc = self._run_cli(["compile", "--check", _CONFLICT_IR_REL, "--spec", "SPEC.md", "--json"])
        self.assertEqual(proc.returncode, 1, msg=proc.stderr + proc.stdout)

        data = json.loads(proc.stdout)
        self.assertEqual(data["schema"], "auto-dev-compile-check/1")
        self.assertEqual(data["warnings"], [])

        errors = data["errors"]
        self.assertTrue(all(err["rule"] == "L-5" for err in errors), msg=f"Unexpected non-L5 errors: {errors}")

        locations = {err["location"] for err in errors}
        self.assertIn("requirements[0].statement", locations)
        self.assertIn("acceptance[0].then", locations)

        messages = "\n".join(err["message"] for err in errors)
        self.assertIn("D-4", messages)
        self.assertIn("git reset --hard", messages)
        self.assertIn("git clean -fd", messages)

    def test_check_resolved_ir_passes_and_preserves_repo_inv12(self) -> None:
        status_before = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        root_entries_before = sorted(p.name for p in _REPO_ROOT.iterdir())

        proc = self._run_cli(["compile", "--check", _RESOLVED_IR_REL, "--spec", "SPEC.md", "--json"])
        self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        self.assertEqual(data["schema"], "auto-dev-compile-check/1")
        self.assertEqual(data["errors"], [])
        self.assertEqual(data["warnings"], [])

        proc_outline = self._run_cli(["compile", "--outline", _DESIGN_DOC_REL])
        self.assertEqual(proc_outline.returncode, 0, msg=proc_outline.stderr + proc_outline.stdout)

        status_after = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=_REPO_ROOT,
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        root_entries_after = sorted(p.name for p in _REPO_ROOT.iterdir())
        self.assertEqual(status_after, status_before)
        self.assertEqual(root_entries_after, root_entries_before)


if __name__ == "__main__":
    unittest.main()
