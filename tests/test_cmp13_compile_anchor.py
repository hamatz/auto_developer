"""CMP-13 (Phase 77 Step 1): compile --anchor, --emit project/out_dir without .ir, and skill updates."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from auto_developer.cli import main
from auto_developer.compiler import check_ir_file
from auto_developer.manifest import load_teams_manifest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL_FILE = _REPO_ROOT / "skills" / "nightly-compile" / "SKILL.md"


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink():
            snap[p.relative_to(root).as_posix()] = p.read_bytes()
    return snap


def _make_ir_with_teams(work_dir: Path) -> dict[str, Any]:
    doc_rel = "docs/designs/calc.md"
    doc_path = work_dir / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: calc design\n" for i in range(1, 25)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    return {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": hashlib.sha256(doc_bytes).hexdigest()}],
        "purpose": {
            "statement": "Calculator feature.",
            "source": {"path": doc_rel, "lines": [1, 4], "sha256": _range_sha(1, 4)},
        },
        "requirements": [
            {
                "id": "CALC-1",
                "statement": "Add two numbers.",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": {"path": doc_rel, "lines": [5, 8], "sha256": _range_sha(5, 8)},
                "fingerprints": ["add_numbers"],
            }
        ],
        "acceptance": [
            {
                "id": "AC-1",
                "given": "Two integers",
                "when": "add_numbers runs",
                "then": "Sum is returned",
                "observable": ["return value is a + b"],
                "source": {"path": doc_rel, "lines": [9, 12], "sha256": _range_sha(9, 12)},
            }
        ],
        "teams": [
            {"id": "core", "owns": ["src/calc/**"]},
        ],
        "steps": [
            {
                "n": 1,
                "title": "Implement add_numbers",
                "requirements": ["CALC-1"],
                "files": ["src/calc/ops.py"],
            }
        ],
        "verification": {
            "commands": ["python3 -m unittest"],
            "source": {"path": doc_rel, "lines": [13, 16], "sha256": _range_sha(13, 16)},
        },
    }


class TestCmp13CompileAnchor(unittest.TestCase):
    def _run_cli(self, args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        env = _clean_env()
        src_path = str(_REPO_ROOT / "src")
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}" if "PYTHONPATH" in env else src_path
        return subprocess.run(
            [sys.executable, "-m", "auto_developer.cli", *args],
            cwd=cwd or _REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_anchor_returns_cmp1_source_object_and_matches_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_rel = "docs/designs/sample.md"
            doc_path = root / doc_rel
            doc_path.parent.mkdir(parents=True, exist_ok=True)
            doc_bytes = (
                b"# Design\n"
                b"line 2\n"
                b"line 3\n"
                b"line 4\n"
                b"line 5\n"
            )
            doc_path.write_bytes(doc_bytes)
            byte_lines = doc_bytes.splitlines(keepends=True)
            expected_sha = hashlib.sha256(b"".join(byte_lines[1:4])).hexdigest()
            before = _snapshot_tree(root)

            proc = self._run_cli(
                ["--work-dir", str(root), "compile", "--anchor", f"./{doc_rel}:2-4", "--json"]
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            self.assertEqual(
                payload,
                {"path": doc_rel, "lines": [2, 4], "sha256": expected_sha},
            )
            self.assertEqual(before, _snapshot_tree(root))

            # Single-line range and plain text output
            proc_text = self._run_cli(
                ["--work-dir", str(root), "compile", "--anchor", f"{doc_rel}:3-3"]
            )
            self.assertEqual(proc_text.returncode, 0, proc_text.stdout + proc_text.stderr)
            single_sha = hashlib.sha256(byte_lines[2]).hexdigest()
            self.assertIn(doc_rel, proc_text.stdout)
            self.assertIn("L3-L3", proc_text.stdout)
            self.assertIn(single_sha, proc_text.stdout)

            # Confirm the anchor object passes CMP-1 source validation
            ir = _make_ir_with_teams(root)
            ir["requirements"][0]["source"] = payload
            ir_path = root / "verify.ir.json"
            ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2), encoding="utf-8")
            rc, check_payload, _ = check_ir_file(ir_path, work_dir=root)
            self.assertEqual(rc, 0, check_payload)

    def test_anchor_error_cases_return_two_and_write_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "doc.md"
            doc.write_text("a\nb\nc\n", encoding="utf-8")
            outside = root.parent / "outside_anchor_test.md"
            outside.write_text("x\ny\n", encoding="utf-8")
            try:
                before = _snapshot_tree(root)
                invalid_specs = [
                    "missing.md:1-2",
                    ".:1-1",
                    "doc.md:1-4",
                    "doc.md:0-2",
                    "doc.md:3-1",
                    "doc.md",
                    "doc.md:1",
                    "doc.md:abc-def",
                    ":1-2",
                    "doc.md:1-2-3",
                    f"../{outside.name}:1-2",
                    f"{outside}:1-2",
                ]
                for spec in invalid_specs:
                    rc = main(["--work-dir", str(root), "compile", "--anchor", spec])
                    self.assertEqual(rc, 2, f"expected exit 2 for {spec!r}")
                self.assertEqual(before, _snapshot_tree(root))
            finally:
                outside.unlink(missing_ok=True)

    def test_anchor_conflicts_with_other_compile_modes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc = root / "doc.md"
            doc.write_text("a\nb\n", encoding="utf-8")
            ir_path = root / "x.ir.json"
            ir_path.write_text("{}", encoding="utf-8")

            for extra in (["--check", str(ir_path)], ["--outline", str(doc)], ["--emit", str(ir_path)]):
                proc = self._run_cli(
                    ["--work-dir", str(root), "compile", "--anchor", "doc.md:1-2", *extra]
                )
                self.assertEqual(proc.returncode, 2, f"expected 2 for {extra}")

    def test_emit_strips_ir_suffix_from_out_dir_and_teams_project(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = root / "calc.ir.json"
            ir_path.write_text(
                json.dumps(_make_ir_with_teams(root), ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )

            proc = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            out_dir = root / ".dev" / "compile" / "calc"
            self.assertTrue(out_dir.is_dir())
            self.assertFalse((root / ".dev" / "compile" / "calc.ir").exists())

            manifest = load_teams_manifest(root, manifest_path=out_dir / "teams.json")
            self.assertEqual(manifest.project, "calc")

    def test_skill_updated_for_anchor_fingerprints_and_no_v010_draft(self) -> None:
        text = _SKILL_FILE.read_text(encoding="utf-8")
        self.assertNotIn("v0.10-draft", text)
        self.assertIn("compile --anchor", text)
        self.assertIn("fingerprints", text)
        for doc_name in ("goal.md", "architecture.md", "decision.md", "todo.md"):
            self.assertIn(doc_name, text)


if __name__ == "__main__":
    unittest.main()
