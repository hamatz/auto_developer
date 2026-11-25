"""CMP-1 (Step 3): auto-dev compile --check <IR> の元の場所（source）検査と INV-12 のテスト。"""

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


def _create_sample_workspace(root: Path) -> tuple[Path, dict[str, Any]]:
    """Create a sample design doc under root and a matching valid IR dict."""
    doc_rel = "docs/designs/sample.md"
    doc_path = root / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    content = "".join(f"Line {i}: sample design content\n" for i in range(1, 31))
    doc_bytes = content.encode("utf-8")
    doc_path.write_bytes(doc_bytes)

    ir: dict[str, Any] = {
        "schema": "auto-dev-ir/1",
        "source": [
            {
                "path": doc_rel,
                "sha256": _sha256_bytes(doc_bytes),
            }
        ],
        "purpose": {
            "text": "Sample purpose",
            "source": {
                "path": doc_rel,
                "lines": [1, 5],
                "sha256": _line_range_sha256(doc_bytes, 1, 5),
            },
        },
        "terms": [
            {
                "term": "IR",
                "definition": "Intermediate Representation",
                "source": {
                    "path": doc_rel,
                    "lines": [6, 10],
                    "sha256": _line_range_sha256(doc_bytes, 6, 10),
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
                    "path": doc_rel,
                    "lines": [11, 15],
                    "sha256": _line_range_sha256(doc_bytes, 11, 15),
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
                    "lines": [16, 20],
                    "sha256": _line_range_sha256(doc_bytes, 16, 20),
                },
            }
        ],
        "out_of_scope": [
            {
                "text": "GUI",
                "source": {
                    "path": doc_rel,
                    "lines": [21, 22],
                    "sha256": _line_range_sha256(doc_bytes, 21, 22),
                },
            }
        ],
        "decisions": [
            {
                "id": "D-1",
                "statement": "Use JSON for IR.",
                "forbids": ["YAML"],
                "source": {
                    "path": doc_rel,
                    "lines": [23, 25],
                    "sha256": _line_range_sha256(doc_bytes, 23, 25),
                },
            }
        ],
        "steps": [
            {
                "n": 1,
                "title": "Implement source check",
                "requirements": ["CORE-1"],
                "files": ["src/auto_developer/compiler.py"],
            }
        ],
        "verification": {
            "commands": ["python3 -m unittest"],
            "source": {
                "path": doc_rel,
                "lines": [26, 30],
                "sha256": _line_range_sha256(doc_bytes, 26, 30),
            },
        },
        "alternatives": [
            {
                "text": "TOML",
                "source": {
                    "path": doc_rel,
                    "lines": [28, 30],
                    "sha256": _line_range_sha256(doc_bytes, 28, 30),
                },
            }
        ],
        "open_questions": [],
    }
    return doc_path, ir


class TestCMP1CompileSource(unittest.TestCase):
    """CMP-1 Step 3: source existence, line bounds, range SHA-256 (ERROR), file SHA-256 (WARN)."""

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

    def test_valid_sources_pass_with_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, ir = _create_sample_workspace(root)
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["errors"], [])
            self.assertEqual(data["warnings"], [])

    def test_missing_source_file_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_path, ir = _create_sample_workspace(root)
            doc_path.unlink()
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("source[0].path", locs)
            self.assertIn("requirements[0].source.path", locs)
            self.assertTrue(all(e["rule"] == "F-1" for e in data["errors"]))

    def test_directory_as_source_path_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, ir = _create_sample_workspace(root)
            ir["source"][0]["path"] = "docs/designs"
            ir["requirements"][0]["source"]["path"] = "docs/designs"
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("source[0].path", locs)
            self.assertIn("requirements[0].source.path", locs)

    def test_unreadable_source_file_is_error(self) -> None:
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            self.skipTest("chmod 000 is bypassed as root")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_path, ir = _create_sample_workspace(root)
            doc_path.chmod(0o000)
            try:
                ir_path = self._write_ir(root, ir)
                proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
                self.assertEqual(proc.returncode, 1)
                data = json.loads(proc.stdout)
                locs = {e["location"] for e in data["errors"]}
                self.assertIn("source[0].path", locs)
                self.assertIn("requirements[0].source.path", locs)
            finally:
                doc_path.chmod(0o644)

    def test_path_traversal_outside_workspace_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as outer_tmp:
            outer = Path(outer_tmp)
            outside_file = outer / "secret.md"
            outside_bytes = b"outside\n"
            outside_file.write_bytes(outside_bytes)

            root = outer / "repo"
            root.mkdir()
            _, ir = _create_sample_workspace(root)
            ir["source"][0] = {"path": "../secret.md", "sha256": _sha256_bytes(outside_bytes)}
            ir["requirements"][0]["source"] = {
                "path": "../secret.md",
                "lines": [1, 1],
                "sha256": _sha256_bytes(outside_bytes),
            }
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("source[0].path", locs)
            self.assertIn("requirements[0].source.path", locs)

    def test_existing_absolute_path_and_empty_path_are_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_path, ir = _create_sample_workspace(root)
            abs_doc = str(doc_path.resolve())
            ir["source"][0]["path"] = abs_doc
            ir["requirements"][0]["source"]["path"] = abs_doc
            ir["acceptance"][0]["source"]["path"] = "   "
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("source[0].path", locs)
            self.assertIn("requirements[0].source.path", locs)
            self.assertIn("acceptance[0].source.path", locs)
            self.assertTrue(all(e["rule"] == "F-1" for e in data["errors"]))

    def test_symlink_escaping_workspace_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as outer_tmp:
            outer = Path(outer_tmp)
            outside_file = outer / "outside.md"
            outside_bytes = b"outside\n"
            outside_file.write_bytes(outside_bytes)

            root = outer / "repo"
            root.mkdir()
            _, ir = _create_sample_workspace(root)
            link_path = root / "docs" / "designs" / "escape.md"
            link_path.symlink_to(outside_file)

            ir["requirements"][0]["source"] = {
                "path": "docs/designs/escape.md",
                "lines": [1, 1],
                "sha256": _sha256_bytes(outside_bytes),
            }
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("requirements[0].source.path", locs)

    def test_line_range_out_of_bounds_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, ir = _create_sample_workspace(root)
            # sample.md has 30 lines
            ir["requirements"][0]["source"]["lines"] = [28, 31]
            ir["purpose"]["source"]["lines"] = [31, 35]
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("requirements[0].source.lines", locs)
            self.assertIn("purpose.source.lines", locs)

    def test_empty_file_line_range_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, ir = _create_sample_workspace(root)
            empty_doc = root / "docs" / "designs" / "empty.md"
            empty_doc.write_bytes(b"")
            ir["requirements"][0]["source"] = {
                "path": "docs/designs/empty.md",
                "lines": [1, 1],
                "sha256": _sha256_bytes(b""),
            }
            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertIn("requirements[0].source.lines", locs)

    def test_range_sha256_mismatch_is_error_across_all_source_holders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, ir = _create_sample_workspace(root)
            ir["purpose"]["source"]["sha256"] = "0" * 64
            ir["terms"][0]["source"]["sha256"] = "0" * 64
            ir["requirements"][0]["source"]["sha256"] = "0" * 64
            ir["acceptance"][0]["source"]["sha256"] = "0" * 64
            ir["out_of_scope"][0]["source"]["sha256"] = "0" * 64
            ir["decisions"][0]["source"]["sha256"] = "0" * 64
            ir["verification"]["source"]["sha256"] = "0" * 64
            ir["alternatives"][0]["source"]["sha256"] = "0" * 64

            ir_path = self._write_ir(root, ir)
            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1)
            data = json.loads(proc.stdout)
            locs = {e["location"] for e in data["errors"]}
            self.assertEqual(
                locs,
                {
                    "purpose.source.sha256",
                    "terms[0].source.sha256",
                    "requirements[0].source.sha256",
                    "acceptance[0].source.sha256",
                    "out_of_scope[0].source.sha256",
                    "decisions[0].source.sha256",
                    "verification.source.sha256",
                    "alternatives[0].source.sha256",
                },
            )

    def test_top_level_file_sha256_mismatch_only_is_warn_with_exit_zero(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_path, ir = _create_sample_workspace(root)
            # Append a line at the end so lines 1..30 keep identical bytes/SHA-256,
            # while the whole file SHA-256 changes.
            doc_path.write_bytes(doc_path.read_bytes() + b"Line 31: unrelated addition\n")

            ir_path = self._write_ir(root, ir)
            proc_json = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc_json.returncode, 0, proc_json.stdout + proc_json.stderr)
            data = json.loads(proc_json.stdout)
            self.assertEqual(data["errors"], [])
            self.assertEqual(len(data["warnings"]), 1)
            self.assertEqual(data["warnings"][0]["rule"], "F-1")
            self.assertEqual(data["warnings"][0]["location"], "source[0].sha256")

            proc_text = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path)])
            self.assertEqual(proc_text.returncode, 0, proc_text.stdout + proc_text.stderr)
            self.assertRegex(proc_text.stdout, r"^WARN F-1 source\[0\]\.sha256: ")

    def test_inv12_compile_with_source_checks_writes_nothing(self) -> None:
        def _snapshot(root: Path) -> dict[str, str]:
            snap: dict[str, str] = {}
            for p in sorted(root.rglob("*")):
                rel = p.relative_to(root).as_posix()
                snap[rel] = "<DIR>" if p.is_dir() else _sha256_bytes(p.read_bytes())
            return snap

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            doc_path, ir = _create_sample_workspace(root)
            doc_path.write_bytes(doc_path.read_bytes() + b"extra line\n")
            ir_path = self._write_ir(root, ir)
            before = _snapshot(root)

            rc = main(["--work-dir", str(root), "compile", "--check", str(ir_path)])
            self.assertEqual(rc, 0)
            self.assertEqual(before, _snapshot(root))


if __name__ == "__main__":
    unittest.main()
