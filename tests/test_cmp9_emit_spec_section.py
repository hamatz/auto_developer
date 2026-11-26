"""CMP-9 (Step 2): spec_section.md の下書き出力のテスト。"""

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


def _make_ir_with_modify_and_oos(
    work_dir: Path,
    *,
    out_of_scope: list[Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    doc_rel = "docs/designs/sample.md"
    doc_path = work_dir / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: sample design\n" for i in range(1, 41)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    shas = {
        "req1": _range_sha(6, 10),
        "req2": _range_sha(11, 15),
        "ac1": _range_sha(16, 20),
        "ac2": _range_sha(21, 25),
        "oos": _range_sha(26, 28),
    }

    ir: dict[str, Any] = {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": hashlib.sha256(doc_bytes).hexdigest()}],
        "purpose": {
            "statement": "Emit spec section draft.",
            "source": {"path": doc_rel, "lines": [1, 5], "sha256": _range_sha(1, 5)},
        },
        "requirements": [
            {
                "id": "CMP-9",
                "statement": "Emit spec_section.md from IR.",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": {"path": doc_rel, "lines": [6, 10], "sha256": shas["req1"]},
            },
            {
                "id": "CMP-5",
                "statement": "Include spec_section.md in compile --emit output.",
                "kind": "modify",
                "acceptance": ["AC-2"],
                "source": {"path": doc_rel, "lines": [11, 15], "sha256": shas["req2"]},
            },
        ],
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A valid IR with requirements and acceptance criteria",
                "when": "auto-dev compile --emit runs",
                "then": "spec_section.md is written under .dev/compile/",
                "observable": ["spec_section.md exists", "source comment is present"],
                "source": {"path": doc_rel, "lines": [16, 20], "sha256": shas["ac1"]},
            },
            {
                "id": "AC-2",
                "given": "A requirement with kind == modify",
                "when": "spec_section.md is rendered",
                "then": "The requirement has the modify marker after its ID",
                "observable": ["CMP-5 has modify marker"],
                "source": {"path": doc_rel, "lines": [21, 25], "sha256": shas["ac2"]},
            },
        ],
        "steps": [
            {
                "n": 1,
                "title": "Render spec_section.md",
                "requirements": ["CMP-9", "CMP-5"],
                "files": ["src/auto_developer/compiler.py"],
            }
        ],
        "verification": {
            "commands": ["python3 -m unittest tests/test_cmp9_emit_spec_section.py"],
            "source": {"path": doc_rel, "lines": [29, 32], "sha256": _range_sha(29, 32)},
        },
    }
    if out_of_scope is not None:
        resolved_oos: list[Any] = []
        for item in out_of_scope:
            if isinstance(item, dict) and "source" in item:
                copied = dict(item)
                copied["source"] = {
                    "path": doc_rel,
                    "lines": [26, 28],
                    "sha256": shas["oos"],
                }
                resolved_oos.append(copied)
            else:
                resolved_oos.append(item)
        ir["out_of_scope"] = resolved_oos
    return ir, shas


class TestCmp9EmitSpecSection(unittest.TestCase):
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

    def _write_ir(self, directory: Path, payload: dict[str, Any], name: str = "feature.ir.json") -> Path:
        path = directory / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_emit_writes_spec_section_ja_with_requirements_ac_source_and_out_of_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir, shas = _make_ir_with_modify_and_oos(
                root,
                out_of_scope=[
                    "Impact analysis (CMP-4)",
                    {"text": "Dashboard overlay (STAT-3)", "source": {}},
                ],
            )
            ir_path = self._write_ir(root, ir, name="phase76.ir.json")
            ir_sha = hashlib.sha256(ir_path.read_bytes()).hexdigest()

            proc = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("spec_section.md", proc.stdout)

            spec_out = root / ".dev" / "compile" / "phase76" / "spec_section.md"
            self.assertTrue(spec_out.is_file())
            text = spec_out.read_text(encoding="utf-8")

            # Common header
            self.assertTrue(text.startswith("> "))
            self.assertIn("INV-12", text)
            self.assertIn("phase76.ir.json", text)
            self.assertIn(ir_sha, text)

            # Section heading
            self.assertIn("### 5.1 （フェーズの名前）", text)

            # Requirements in IR order + modify marker
            req1_line = "- [ ] **CMP-9**: Emit spec_section.md from IR."
            req2_line = "- [ ] **CMP-5**（修正）: Include spec_section.md in compile --emit output."
            self.assertIn(req1_line, text)
            self.assertIn(req2_line, text)
            self.assertLess(text.index(req1_line), text.index(req2_line))

            # Source comment immediately after each requirement
            c1 = f"<!-- source: docs/designs/sample.md:L6-L10 sha256:{shas['req1']} -->"
            c2 = f"<!-- source: docs/designs/sample.md:L11-L15 sha256:{shas['req2']} -->"
            self.assertIn(c1, text)
            self.assertIn(c2, text)
            self.assertLess(text.index(req1_line), text.index(c1))
            self.assertLess(text.index(c1), text.index("AC-1"))
            self.assertLess(text.index(req2_line), text.index(c2))
            self.assertLess(text.index(c2), text.index("AC-2"))

            # Indented acceptance criteria with Given/When/Then/observable
            self.assertIn("Given: A valid IR with requirements and acceptance criteria", text)
            self.assertIn("When: auto-dev compile --emit runs", text)
            self.assertIn("Then: spec_section.md is written under .dev/compile/", text)
            self.assertIn("観測できる結果", text)
            self.assertIn("spec_section.md exists", text)
            self.assertIn("source comment is present", text)

            # Out of scope
            self.assertIn("スコープ外（SPEC の 6 章に写す）", text)
            self.assertIn("Impact analysis (CMP-4)", text)
            self.assertIn("Dashboard overlay (STAT-3)", text)

    def test_emit_spec_section_en_and_omits_empty_out_of_scope(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir, _ = _make_ir_with_modify_and_oos(root, out_of_scope=[])
            ir_path = self._write_ir(root, ir, name="phase76_en.json")

            proc = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--lang", "en"]
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            spec_out = root / ".dev" / "compile" / "phase76_en" / "spec_section.md"
            text = spec_out.read_text(encoding="utf-8")

            self.assertIn("### 5.1 (Phase Name)", text)
            self.assertIn("- [ ] **CMP-5** (modify): Include spec_section.md in compile --emit output.", text)
            self.assertIn("Observable", text)
            self.assertNotIn("観測できる結果", text)
            self.assertNotIn("スコープ外", text)
            self.assertNotIn("Out of Scope", text)

    def test_emit_spec_section_deterministic_and_target_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir, _ = _make_ir_with_modify_and_oos(root, out_of_scope=["Non-goal A"])
            ir_path = self._write_ir(root, ir)
            out_a = root / ".dev" / "compile" / "run_a"
            out_b = root / ".dev" / "compile" / "run_b"

            self.assertEqual(
                main(["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_a)]),
                0,
            )
            self.assertEqual(
                main(["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_b)]),
                0,
            )
            self.assertEqual(
                (out_a / "spec_section.md").read_bytes(),
                (out_b / "spec_section.md").read_bytes(),
            )

            # Symlink target safety on spec_section.md
            protected = root / "SPEC.md"
            protected.write_text("# Protected\n", encoding="utf-8")
            sym_dir = root / ".dev" / "compile" / "sym_spec"
            sym_dir.mkdir(parents=True, exist_ok=True)
            (sym_dir / "spec_section.md").symlink_to(protected)
            rc_sym = main(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--emit",
                    str(ir_path),
                    "--out-dir",
                    str(sym_dir),
                    "--force",
                ]
            )
            self.assertEqual(rc_sym, 2)
            self.assertEqual(protected.read_text(encoding="utf-8"), "# Protected\n")
            self.assertFalse((sym_dir / "ir.json").exists())


if __name__ == "__main__":
    unittest.main()
