"""CMP-1 (Step 2): auto-dev compile --check <IR> の形式検査と INV-12 のテスト。"""

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


def _make_valid_ir() -> dict[str, Any]:
    return {
        "schema": "auto-dev-ir/1",
        "source": [
            {
                "path": "docs/designs/sample.md",
                "sha256": "a" * 64,
            }
        ],
        "purpose": {
            "text": "Sample purpose",
            "source": {
                "path": "docs/designs/sample.md",
                "lines": [1, 5],
                "sha256": "b" * 64,
            },
        },
        "terms": [
            {
                "term": "IR",
                "definition": "Intermediate Representation",
                "source": {
                    "path": "docs/designs/sample.md",
                    "lines": [6, 10],
                    "sha256": "c" * 64,
                },
            }
        ],
        "requirements": [
            {
                "id": "CORE-1",
                "statement": "Run deterministic checks.",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": {
                    "path": "docs/designs/sample.md",
                    "lines": [11, 15],
                    "sha256": "d" * 64,
                },
                "fingerprints": ["run_checks"],
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
                    "path": "docs/designs/sample.md",
                    "lines": [16, 20],
                    "sha256": "e" * 64,
                },
            }
        ],
        "out_of_scope": ["GUI"],
        "decisions": [
            {
                "id": "D-1",
                "statement": "Use JSON for IR.",
                "forbids": ["YAML"],
                "source": {
                    "path": "docs/designs/sample.md",
                    "lines": [21, 25],
                    "sha256": "f" * 64,
                },
            }
        ],
        "steps": [
            {
                "n": 1,
                "title": "Implement schema check",
                "requirements": ["CORE-1"],
                "files": ["src/auto_developer/compiler.py"],
            }
        ],
        "teams": [
            {
                "id": "core",
                "owns": ["src/auto_developer/**"],
            }
        ],
        "verification": {
            "commands": ["python3 -m unittest"],
            "source": {
                "path": "docs/designs/sample.md",
                "lines": [26, 30],
                "sha256": "1" * 64,
            },
        },
        "alternatives": ["TOML"],
        "open_questions": [],
    }


class TestCMP1CompileSchema(unittest.TestCase):
    """CMP-1 Step 2: IR schema validation, exit codes, --json, and INV-12."""

    def _write_ir(self, tmpdir: Path, payload: Any, name: str = "ir.json") -> Path:
        path = tmpdir / name
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

    def test_valid_ir_returns_zero_and_json_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = self._write_ir(Path(tmp), _make_valid_ir())
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["schema"], "auto-dev-compile-check/1")
            self.assertEqual(data["errors"], [])
            self.assertEqual(data["warnings"], [])

    def test_missing_file_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "does_not_exist.json"
            proc = self._run_cli(["compile", "--check", str(missing)])
            self.assertEqual(proc.returncode, 2)

            proc_json = self._run_cli(["compile", "--check", str(missing), "--json"])
            self.assertEqual(proc_json.returncode, 2)
            data = json.loads(proc_json.stdout)
            self.assertEqual(data["schema"], "auto-dev-compile-check/1")
            self.assertEqual(data["errors"][0]["rule"], "F-1")
            self.assertEqual(data["warnings"], [])

    def test_non_object_json_root_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            arr = self._write_ir(Path(tmp), [1, 2, 3], name="array.json")
            proc = self._run_cli(["compile", "--check", str(arr), "--json"])
            self.assertEqual(proc.returncode, 2)
            data = json.loads(proc.stdout)
            self.assertEqual(data["schema"], "auto-dev-compile-check/1")
            self.assertEqual(data["errors"][0]["rule"], "F-1")
            self.assertEqual(data["errors"][0]["location"], "<root>")

    def test_invalid_json_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.json"
            bad.write_text("{not valid json", encoding="utf-8")
            proc = self._run_cli(["compile", "--check", str(bad), "--json"])
            self.assertEqual(proc.returncode, 2)
            data = json.loads(proc.stdout)
            self.assertEqual(data["schema"], "auto-dev-compile-check/1")
            self.assertTrue(len(data["errors"]) >= 1)
            self.assertEqual(data["errors"][0]["rule"], "F-1")

    def test_wrong_schema_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ir["schema"] = "auto-dev-ir/999"
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 2)
            data = json.loads(proc.stdout)
            self.assertEqual(data["errors"][0]["rule"], "F-1")
            self.assertEqual(data["errors"][0]["location"], "schema")

    def test_missing_required_top_level_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            del ir["source"]
            ir["requirements"] = []
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("source", locs)
            self.assertIn("requirements", locs)
            self.assertTrue(all(e["rule"] == "F-1" for e in data["errors"]))

    def test_requirement_id_format_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            req2 = dict(ir["requirements"][0])
            req2["id"] = "bad_id"
            req3 = dict(ir["requirements"][0])
            ir["requirements"].extend([req2, req3])
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = [e["location"] for e in data["errors"]]
            self.assertIn("requirements[1].id", locs)
            self.assertIn("requirements[2].id", locs)

    def test_requirement_kind_and_statement(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ir["requirements"][0]["kind"] = "update"
            ir["requirements"][0]["statement"] = "   "
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("requirements[0].kind", locs)
            self.assertIn("requirements[0].statement", locs)

    def test_acceptance_and_decision_id_format_and_duplicates(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ac2 = dict(ir["acceptance"][0])
            ac2["id"] = "BAD-1"
            ac3 = dict(ir["acceptance"][0])
            ir["acceptance"].extend([ac2, ac3])

            d2 = dict(ir["decisions"][0])
            d2["id"] = "DEC-1"
            d3 = dict(ir["decisions"][0])
            ir["decisions"].extend([d2, d3])

            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("acceptance[1].id", locs)
            self.assertIn("acceptance[2].id", locs)
            self.assertIn("decisions[1].id", locs)
            self.assertIn("decisions[2].id", locs)

    def test_reference_integrity_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ir["requirements"][0]["acceptance"] = ["AC-999"]
            ir["steps"][0]["requirements"] = ["NOPE-1"]
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("requirements[0].acceptance[0]", locs)
            self.assertIn("steps[0].requirements[0]", locs)

    def test_steps_must_be_sequential_from_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            step2 = dict(ir["steps"][0])
            step2["n"] = 3
            ir["steps"].append(step2)
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("steps[1].n", locs)

    def test_source_lines_validation_rejects_bool_and_invalid_ranges(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ir["requirements"][0]["source"]["lines"] = [True, 5]
            ir["acceptance"][0]["source"]["lines"] = [10, 5]
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("requirements[0].source.lines", locs)
            self.assertIn("acceptance[0].source.lines", locs)

    def test_text_output_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ir["requirements"][0]["kind"] = "invalid"
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path)])
            self.assertEqual(proc.returncode, 1)
            self.assertRegex(proc.stdout, r"^ERROR F-1 requirements\[0\]\.kind: ")

    def test_teams_duplicate_id_and_verification_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ir["teams"].append({"id": "core", "owns": ["tests/**"]})
            ir["verification"] = {"commands": "pytest"}  # wrong type + missing source
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("teams[1].id", locs)
            self.assertIn("verification.commands", locs)
            self.assertIn("verification.source", locs)

    def test_ids_reject_trailing_newline(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ir["requirements"][0]["id"] = "CORE-1\n"
            ir["acceptance"][0]["id"] = "AC-1\n"
            ir["decisions"][0]["id"] = "D-1\n"
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("requirements[0].id", locs)
            self.assertIn("acceptance[0].id", locs)
            self.assertIn("decisions[0].id", locs)

    def test_acceptance_decisions_teams_and_source_field_types(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir = _make_valid_ir()
            ir["source"] = [{"path": "/abs/path.md", "sha256": ""}]
            ir["acceptance"][0]["given"] = 123
            ir["acceptance"][0]["when"] = None
            ir["acceptance"][0]["then"] = ["not", "str"]
            ir["decisions"][0]["forbids"] = "YAML"
            ir["teams"][0]["owns"] = "src/**"
            ir_path = self._write_ir(Path(tmp), ir)
            proc = self._run_cli(["compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("source[0].path", locs)
            self.assertIn("source[0].sha256", locs)
            self.assertIn("acceptance[0].given", locs)
            self.assertIn("acceptance[0].when", locs)
            self.assertIn("acceptance[0].then", locs)
            self.assertIn("decisions[0].forbids", locs)
            self.assertIn("teams[0].owns", locs)

    def test_inv12_compile_creates_or_modifies_no_files(self) -> None:
        def _snapshot(root: Path) -> dict[str, str]:
            snap: dict[str, str] = {}
            for p in sorted(root.rglob("*")):
                rel = p.relative_to(root).as_posix()
                if p.is_dir():
                    snap[rel] = "<DIR>"
                else:
                    snap[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
            return snap

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            sub = repo / "nested"
            sub.mkdir()
            ir_path = self._write_ir(sub, _make_valid_ir())
            before = _snapshot(repo)

            rc = main(["--work-dir", str(repo), "compile", "--check", str(ir_path)])
            self.assertEqual(rc, 0)
            self.assertEqual(before, _snapshot(repo))

            rc_json = main(["--work-dir", str(repo), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(rc_json, 0)
            self.assertEqual(before, _snapshot(repo))

    def test_compile_check_with_top_level_checker_option(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            ir_path = self._write_ir(Path(tmp), _make_valid_ir())
            proc = self._run_cli(["--checker-model", "test-model", "compile", "--check", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_compile_without_check_returns_two(self) -> None:
        proc = self._run_cli(["compile"])
        self.assertEqual(proc.returncode, 2)
        self.assertIn("--check", proc.stderr)

    def test_top_level_abbreviated_option_returns_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = self._run_cli(["--work", tmp, "status"])
            self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
