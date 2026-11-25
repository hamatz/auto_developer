"""CMP-2 (Step 4): auto-dev compile --outline <設計書> のテストと INV-12 検証。"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _line_range_sha256(doc_bytes: bytes, start: int, end: int) -> str:
    lines = doc_bytes.splitlines(keepends=True)
    return _sha256_bytes(b"".join(lines[start - 1 : end]))


class TestCMP2CompileOutline(unittest.TestCase):
    def _run_cli(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "auto_developer.cli", *args],
            cwd=cwd or _REPO_ROOT,
            env=_clean_env(),
            check=False,
            capture_output=True,
            text=True,
        )

    def test_outline_maps_headings_ranges_sha256_and_excludes_code_blocks(self) -> None:
        doc_text = (
            "# Design Title\n"
            "\n"
            "## Overview\n"
            "Purpose summary.\n"
            "\n"
            "### 用語定義 (Glossary)\n"
            "- Term A: desc\n"
            "\n"
            "## 1. The Problem (Background)\n"
            "Problem statement.\n"
            "\n"
            "## 2. goals\n"
            "- Goal 1\n"
            "\n"
            "## Non-Goals\n"
            "- Out of scope\n"
            "\n"
            "## Architectural Decisions (Resolved Questions)\n"
            "- D-1: use X\n"
            "\n"
            "## Critical User Journeys (CUJs)\n"
            "### CUJ 1: Happy path\n"
            "Given/When/Then\n"
            "\n"
            "## Technical Plan (Architecture)\n"
            "```markdown\n"
            "# Fake H1 inside backticks\n"
            "## 1. Summary\n"
            "~~~\n"
            "## Still inside backticks\n"
            "```\n"
            "\n"
            "~~~markdown\n"
            "# Fake H1 inside tildes\n"
            "## Goals\n"
            "~~~\n"
            "\n"
            "## Alternatives Considered\n"
            "- Alt 1\n"
            "\n"
            "## Detailed Implementation Plan\n"
            "- Step 1\n"
            "\n"
            "### Verification Plan\n"
            "- Run tests\n"
        )
        doc_bytes = doc_text.encode("utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_rel = "docs/designs/sample.md"
            doc_path = root / doc_rel
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_path.write_bytes(doc_bytes)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--outline", doc_rel, "--json"])
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)

            data = json.loads(proc.stdout)
            self.assertEqual(data["schema"], "auto-dev-outline/1")
            self.assertEqual(data["source"]["path"], doc_rel)
            self.assertEqual(data["source"]["sha256"], _sha256_bytes(doc_bytes))
            self.assertEqual(data["missing"], [])

            sections = data["sections"]
            titles = [s["title"] for s in sections]
            self.assertNotIn("Fake H1 inside backticks", titles)
            self.assertNotIn("1. Summary", titles)
            self.assertNotIn("Still inside backticks", titles)
            self.assertNotIn("Fake H1 inside tildes", titles)

            by_title = {s["title"]: s for s in sections}
            self.assertIsNone(by_title["Design Title"]["ir_element"])
            self.assertEqual(by_title["Design Title"]["level"], 1)
            self.assertEqual(by_title["Design Title"]["lines"], [1, 45])
            self.assertEqual(by_title["Design Title"]["sha256"], _line_range_sha256(doc_bytes, 1, 45))

            self.assertEqual(by_title["Overview"]["ir_element"], "purpose")
            self.assertEqual(by_title["Overview"]["level"], 2)
            self.assertEqual(by_title["Overview"]["lines"], [3, 8])
            self.assertEqual(by_title["Overview"]["sha256"], _line_range_sha256(doc_bytes, 3, 8))

            self.assertEqual(by_title["用語定義 (Glossary)"]["ir_element"], "terms")
            self.assertEqual(by_title["用語定義 (Glossary)"]["level"], 3)
            self.assertEqual(by_title["用語定義 (Glossary)"]["lines"], [6, 8])

            self.assertEqual(by_title["1. The Problem (Background)"]["ir_element"], "purpose")
            self.assertEqual(by_title["2. goals"]["ir_element"], "requirements")
            self.assertEqual(by_title["Non-Goals"]["ir_element"], "out_of_scope")
            self.assertEqual(by_title["Architectural Decisions (Resolved Questions)"]["ir_element"], "decisions")
            self.assertEqual(by_title["Critical User Journeys (CUJs)"]["ir_element"], "acceptance")
            self.assertEqual(by_title["CUJ 1: Happy path"]["ir_element"], "acceptance")
            self.assertEqual(by_title["Technical Plan (Architecture)"]["ir_element"], "steps")
            self.assertEqual(by_title["Alternatives Considered"]["ir_element"], "alternatives")
            self.assertEqual(by_title["Detailed Implementation Plan"]["ir_element"], "steps")
            self.assertEqual(by_title["Verification Plan"]["ir_element"], "verification")
            self.assertEqual(by_title["Verification Plan"]["lines"], [44, 45])
            self.assertEqual(
                by_title["Verification Plan"]["sha256"],
                _line_range_sha256(doc_bytes, 44, 45),
            )

    def test_outline_text_output_and_either_plan_section_satisfies_steps(self) -> None:
        doc_text = (
            "## Goals\n"
            "- Req 1\n"
            "## CUJ 1: Flow\n"
            "- AC 1\n"
            "## Detailed Implementation Plan\n"
            "- Step 1\n"
            "## Verification Plan\n"
            "- Check\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_path = root / "design.md"
            doc_path.write_text(doc_text, encoding="utf-8")

            proc = self._run_cli(["--work-dir", str(root), "compile", "--outline", "design.md"])
            self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
            self.assertIn("requirements", proc.stdout)
            self.assertIn("acceptance", proc.stdout)
            self.assertIn("steps", proc.stdout)
            self.assertIn("verification", proc.stdout)

    def test_outline_missing_required_sections_returns_one(self) -> None:
        doc_text = "## Overview\nOnly overview here.\n"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_path = root / "incomplete.md"
            doc_path.write_text(doc_text, encoding="utf-8")

            proc = self._run_cli(["--work-dir", str(root), "compile", "--outline", "incomplete.md", "--json"])
            self.assertEqual(proc.returncode, 1, msg=proc.stderr + proc.stdout)
            data = json.loads(proc.stdout)
            self.assertEqual(data["schema"], "auto-dev-outline/1")
            self.assertIn("Goals", data["missing"])
            self.assertIn("Critical User Journeys", data["missing"])
            self.assertIn("Verification Plan", data["missing"])
            self.assertTrue(
                any("Technical Plan" in item or "Detailed Implementation Plan" in item for item in data["missing"]),
                msg=f"Expected Technical/Detailed Plan in missing: {data['missing']}",
            )

            proc_text = self._run_cli(["--work-dir", str(root), "compile", "--outline", "incomplete.md"])
            self.assertEqual(proc_text.returncode, 1)
            self.assertIn("Goals", proc_text.stdout)

    def test_outline_unreadable_and_conflicting_flags_return_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.md"
            proc_missing = self._run_cli(["compile", "--outline", str(missing)])
            self.assertEqual(proc_missing.returncode, 2)

            bad_utf8 = root / "binary.md"
            bad_utf8.write_bytes(b"\xff\xfe\xfd")
            proc_bad = self._run_cli(["compile", "--outline", str(bad_utf8)])
            self.assertEqual(proc_bad.returncode, 2)

            ir_file = root / "ir.json"
            ir_file.write_text("{}", encoding="utf-8")
            doc_file = root / "doc.md"
            doc_file.write_text("# Hi\n", encoding="utf-8")
            proc_both = self._run_cli(["compile", "--check", str(ir_file), "--outline", str(doc_file)])
            self.assertEqual(proc_both.returncode, 2)

    def test_outline_inv12_writes_no_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True, capture_output=True)

            doc = repo / "design.md"
            doc.write_text("## Goals\n## Critical User Journeys\n## Technical Plan\n## Verification Plan\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            before_entries = sorted(p.name for p in repo.iterdir())
            rc = main(["--work-dir", str(repo), "compile", "--outline", "design.md"])
            self.assertEqual(rc, 0)
            after_entries = sorted(p.name for p in repo.iterdir())
            self.assertEqual(before_entries, after_entries)
            status = subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True, text=True)
            self.assertEqual(status.stdout.strip(), "")

    def test_outline_on_real_design_doc_ignores_nightly_report_headings(self) -> None:
        proc = self._run_cli(
            ["compile", "--outline", "docs/designs/autonomous_pipeline_orchestrator.md", "--json"]
        )
        self.assertEqual(proc.returncode, 0, msg=proc.stderr + proc.stdout)
        data = json.loads(proc.stdout)
        self.assertEqual(data["missing"], [])
        titles = [s["title"] for s in data["sections"]]
        self.assertNotIn("Nightly Execution Report: YYYY-MM-DD HH:MM:SS", titles)
        self.assertNotIn("1. Summary", titles)
        self.assertNotIn("2. Completed Tasks", titles)


if __name__ == "__main__":
    unittest.main()
