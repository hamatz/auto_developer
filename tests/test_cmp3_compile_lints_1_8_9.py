"""CMP-3 (Step 5): auto-dev compile --check の点検 L-1, L-8, L-9 と INV-12 のテスト。"""

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


def _create_valid_workspace(root: Path) -> dict[str, Any]:
    """Create a minimal valid workspace and IR passing F-1, source checks, and L-1/L-8/L-9."""
    doc_rel = "docs/designs/sample.md"
    doc_path = root / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: sample design content\n" for i in range(1, 21)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)

    return {
        "schema": "auto-dev-ir/1",
        "source": [
            {
                "path": doc_rel,
                "sha256": _sha256_bytes(doc_bytes),
            }
        ],
        "requirements": [
            {
                "id": "CORE-1",
                "statement": "Run deterministic checks.",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": {
                    "path": doc_rel,
                    "lines": [1, 5],
                    "sha256": _line_range_sha256(doc_bytes, 1, 5),
                },
            }
        ],
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A valid IR file",
                "when": "compile --check runs",
                "then": "It exits with 0",
                "observable": ["exit_code == 0"],
                "source": {
                    "path": doc_rel,
                    "lines": [6, 10],
                    "sha256": _line_range_sha256(doc_bytes, 6, 10),
                },
            }
        ],
        "steps": [
            {
                "n": 1,
                "title": "Implement lints",
                "requirements": ["CORE-1"],
                "files": ["src/auto_developer/compiler.py"],
            }
        ],
        "open_questions": [],
    }


class TestCMP3CompileLints189(unittest.TestCase):
    """CMP-3 Step 5: L-1 (acceptance/observable), L-8 (orphan reqs/steps), L-9 (open questions/markers)."""

    def _write_ir(self, root: Path, payload: dict[str, Any], name: str = "ir.json") -> Path:
        path = root / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def _run_cli(self, argv: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "auto_developer.cli", *argv],
            cwd=cwd or _REPO_ROOT,
            env=_clean_env(),
            capture_output=True,
            text=True,
            check=False,
        )

    def test_l1_empty_requirement_acceptance_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["requirements"][0]["acceptance"] = []
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            l1_errors = [e for e in data["errors"] if e["rule"] == "L-1"]
            self.assertEqual(len(l1_errors), 1)
            self.assertEqual(l1_errors[0]["location"], "requirements[0].acceptance")

    def test_l1_referenced_acceptance_without_observable_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["acceptance"][0]["observable"] = []
            # Second requirement also references AC-1; should not duplicate error for acceptance[0].observable.
            req2 = dict(ir["requirements"][0])
            req2["id"] = "CORE-2"
            ir["requirements"].append(req2)
            ir["steps"][0]["requirements"].append("CORE-2")
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            l1_errors = [e for e in data["errors"] if e["rule"] == "L-1"]
            self.assertEqual(len(l1_errors), 1)
            self.assertEqual(l1_errors[0]["location"], "acceptance[0].observable")

    def test_l1_whitespace_only_observable_is_error_and_unreferenced_ac_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["acceptance"][0]["observable"] = ["   "]
            ac2 = dict(ir["acceptance"][0])
            ac2["id"] = "AC-2"
            ac2["observable"] = []
            ir["acceptance"].append(ac2)
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            l1_errors = [e for e in data["errors"] if e["rule"] == "L-1"]
            self.assertEqual([e["location"] for e in l1_errors], ["acceptance[0].observable"])

    def test_l8_unlinked_requirement_and_step_are_warnings_with_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            req2 = dict(ir["requirements"][0])
            req2["id"] = "CORE-2"
            ir["requirements"].append(req2)
            ir["steps"].append(
                {
                    "n": 2,
                    "title": "Orphan step",
                    "requirements": [],
                    "files": ["src/auto_developer/cli.py"],
                }
            )
            ir_path = self._write_ir(root, ir)

            proc_json = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc_json.returncode, 0, proc_json.stdout + proc_json.stderr)
            data = json.loads(proc_json.stdout)
            self.assertEqual(data["errors"], [])
            l8_warnings = [w for w in data["warnings"] if w["rule"] == "L-8"]
            locs = {w["location"] for w in l8_warnings}
            self.assertEqual(locs, {"requirements[1]", "steps[1].requirements"})

            proc_text = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path)])
            self.assertEqual(proc_text.returncode, 0)
            self.assertIn("WARN L-8 ", proc_text.stdout)

    def test_l8_missing_steps_warns_for_all_requirements(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            del ir["steps"]
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["errors"], [])
            self.assertEqual(len(data["warnings"]), 1)
            self.assertEqual(data["warnings"][0]["rule"], "L-8")
            self.assertEqual(data["warnings"][0]["location"], "requirements[0]")

    def test_l9_non_empty_open_questions_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["open_questions"] = ["Which exit code should be used?"]
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            l9_errors = [e for e in data["errors"] if e["rule"] == "L-9"]
            self.assertEqual([e["location"] for e in l9_errors], ["open_questions"])

    def test_l9_unresolved_markers_across_req_ac_and_step(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["requirements"][0]["statement"] = "Run checks (TBDです)."
            ir["acceptance"][0]["given"] = "TODO: prepare input"
            ir["acceptance"][0]["when"] = "要検討のコマンドを実行"
            ir["acceptance"][0]["then"] = "出力形式は未定"
            ir["acceptance"][0]["observable"] = ["exit_code is tbd"]
            ir["steps"][0]["title"] = "TODO implement lint"
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            l9_locs = {e["location"] for e in data["errors"] if e["rule"] == "L-9"}
            self.assertEqual(
                l9_locs,
                {
                    "requirements[0].statement",
                    "acceptance[0].given",
                    "acceptance[0].when",
                    "acceptance[0].then",
                    "acceptance[0].observable[0]",
                    "steps[0].title",
                },
            )

    def test_l9_does_not_false_positive_on_ascii_substrings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["requirements"][0]["statement"] = "Check photodocumentation output."
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_inv12_compile_lints_write_no_files(self) -> None:
        def _snapshot(root: Path) -> dict[str, str]:
            snap: dict[str, str] = {}
            for p in sorted(root.rglob("*")):
                rel = p.relative_to(root).as_posix()
                snap[rel] = "<DIR>" if p.is_dir() else _sha256_bytes(p.read_bytes())
            return snap

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["requirements"][0]["acceptance"] = []
            ir["open_questions"] = ["Unresolved"]
            ir_path = self._write_ir(root, ir)
            before = _snapshot(root)

            rc = main(["--work-dir", str(root), "compile", "--check", str(ir_path)])
            self.assertEqual(rc, 1)
            self.assertEqual(before, _snapshot(root))


if __name__ == "__main__":
    unittest.main()
