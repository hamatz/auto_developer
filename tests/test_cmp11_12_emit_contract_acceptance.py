"""CMP-11 & CMP-12 (Step 4): contract.md と acceptance_tests.md の下書き出力のテスト。"""

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


def _make_sample_ir(
    work_dir: Path,
    *,
    with_teams: bool = False,
    with_warnings: bool = False,
    with_decisions: bool = True,
    with_alternatives: bool = True,
) -> dict[str, Any]:
    doc_rel = "docs/designs/sample.md"
    doc_path = work_dir / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: sample design content\n" for i in range(1, 45)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    reqs: list[dict[str, Any]] = [
        {
            "id": "CMP-11",
            "statement": "Emit contract.md draft from IR.",
            "kind": "new",
            "acceptance": ["AC-1", "AC-2"],
            "source": {"path": doc_rel, "lines": [6, 10], "sha256": _range_sha(6, 10)},
        },
        {
            "id": "CMP-12",
            "statement": "Emit acceptance_tests.md draft from IR.",
            "kind": "modify",
            "acceptance": ["AC-2"],
            "source": {"path": doc_rel, "lines": [11, 15], "sha256": _range_sha(11, 15)},
        },
    ]

    steps: list[dict[str, Any]] = [
        {
            "n": 1,
            "title": "Render contract and acceptance drafts",
            "requirements": ["CMP-11"] if with_warnings else ["CMP-11", "CMP-12"],
            "files": ["src/alpha/a.py", "src/beta/b.py"] if with_teams else ["src/auto_developer/compiler.py"],
        }
    ]

    ir: dict[str, Any] = {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": hashlib.sha256(doc_bytes).hexdigest()}],
        "purpose": {
            "statement": "Generate contract and acceptance drafts for human review.",
            "source": {"path": doc_rel, "lines": [1, 5], "sha256": _range_sha(1, 5)},
        },
        "requirements": reqs,
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A validated IR with decisions and alternatives",
                "when": "auto-dev compile --emit runs",
                "then": "contract.md is written with all required sections",
                "observable": ["contract.md exists", "sections appear in order"],
                "source": {"path": doc_rel, "lines": [16, 20], "sha256": _range_sha(16, 20)},
            },
            {
                "id": "AC-2",
                "given": "Acceptance criteria in IR",
                "when": "auto-dev compile --emit runs",
                "then": "acceptance_tests.md lists each AC without test code",
                "observable": ["acceptance_tests.md exists"],
                "source": {"path": doc_rel, "lines": [21, 25], "sha256": _range_sha(21, 25)},
            },
        ],
        "steps": steps,
        "verification": {
            "commands": ["python3 -m unittest tests/test_cmp11_12_emit_contract_acceptance.py"],
            "source": {"path": doc_rel, "lines": [26, 30], "sha256": _range_sha(26, 30)},
        },
    }
    if with_decisions:
        ir["decisions"] = [
            {
                "id": "D-1",
                "statement": "Keep all drafts under .dev/compile/ without modifying SPEC.md.",
                "forbids": ["SPEC.md mutation", "git reset --hard"],
                "source": {"path": doc_rel, "lines": [31, 35], "sha256": _range_sha(31, 35)},
            }
        ]
    if with_alternatives:
        ir["alternatives"] = [
            {
                "statement": "Write drafts directly to docs/contracts/",
                "source": {"path": doc_rel, "lines": [36, 40], "sha256": _range_sha(36, 40)},
            }
        ]
    if with_teams:
        ir["teams"] = [
            {"id": "alpha", "owns": ["src/alpha/**"]},
            {"id": "beta", "owns": ["src/beta/**"]},
        ]
    return ir


class TestCmp11And12EmitContractAcceptance(unittest.TestCase):
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

    def _write_ir(self, directory: Path, payload: dict[str, Any], name: str = "phase76.json") -> Path:
        path = directory / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_emit_contract_and_acceptance_ja_without_warnings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _make_sample_ir(root)
            ir_path = self._write_ir(root, ir, name="clean.json")
            ir_sha = hashlib.sha256(ir_path.read_bytes()).hexdigest()

            proc = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("contract.md", proc.stdout)
            self.assertIn("acceptance_tests.md", proc.stdout)

            out_dir = root / ".dev" / "compile" / "clean"
            contract_path = out_dir / "contract.md"
            acceptance_path = out_dir / "acceptance_tests.md"
            self.assertTrue(contract_path.is_file())
            self.assertTrue(acceptance_path.is_file())

            contract_text = contract_path.read_text(encoding="utf-8")
            self.assertTrue(contract_text.startswith("> "))
            self.assertIn("INV-12", contract_text)
            self.assertIn("clean.json", contract_text)
            self.assertIn(ir_sha, contract_text)

            # Section order: 目的 -> 人間が判断すること -> 要件の一覧 -> 判断 -> 検討した代案 -> 元の文書
            headings = [
                "## 目的",
                "## 人間が判断すること",
                "## 要件の一覧",
                "## 判断",
                "## 検討した代案",
                "## 元の文書",
            ]
            positions = [contract_text.index(h) for h in headings]
            self.assertEqual(positions, sorted(positions))

            # Purpose
            self.assertIn("Generate contract and acceptance drafts for human review.", contract_text)
            self.assertIn("docs/designs/sample.md:L1-L5", contract_text)

            # Human decisions always present even with 0 warnings
            self.assertIn("Phase 77", contract_text)

            # Requirements table
            self.assertIn("CMP-11", contract_text)
            self.assertIn("新規", contract_text)
            self.assertIn("CMP-12", contract_text)
            self.assertIn("修正", contract_text)
            self.assertIn("AC-1, AC-2", contract_text)
            self.assertIn("docs/designs/sample.md:L6-L10", contract_text)

            # Decisions
            self.assertIn("D-1", contract_text)
            self.assertIn("Keep all drafts under .dev/compile/ without modifying SPEC.md.", contract_text)
            self.assertIn("SPEC.md mutation", contract_text)
            self.assertIn("git reset --hard", contract_text)
            self.assertIn("docs/designs/sample.md:L31-L35", contract_text)

            # Alternatives
            self.assertIn("Write drafts directly to docs/contracts/", contract_text)

            # Source documents
            self.assertIn(ir["source"][0]["sha256"], contract_text)

            # acceptance_tests.md
            acc_text = acceptance_path.read_text(encoding="utf-8")
            self.assertTrue(acc_text.startswith("> "))
            self.assertIn("INV-12", acc_text)
            self.assertIn("## AC-1", acc_text)
            self.assertIn("## AC-2", acc_text)
            self.assertLess(acc_text.index("## AC-1"), acc_text.index("## AC-2"))
            self.assertIn("Given: A validated IR with decisions and alternatives", acc_text)
            self.assertIn("When: auto-dev compile --emit runs", acc_text)
            self.assertIn("Then: contract.md is written with all required sections", acc_text)
            self.assertIn("contract.md exists", acc_text)
            self.assertIn("sections appear in order", acc_text)
            self.assertIn("CMP-11", acc_text)
            self.assertIn("CMP-12", acc_text)
            self.assertIn("docs/designs/sample.md:L16-L20", acc_text)
            self.assertNotIn("def test_", acc_text)
            self.assertNotIn("```", acc_text)

    def test_emit_contract_includes_warnings_and_cross_team_steps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _make_sample_ir(root, with_teams=True, with_warnings=True)
            ir_path = self._write_ir(root, ir, name="warn_teams.json")

            rc = main(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(rc, 0)

            contract_text = (root / ".dev" / "compile" / "warn_teams" / "contract.md").read_text(encoding="utf-8")
            # L-8 warning for unlinked CMP-12 should appear under 人間が判断すること
            self.assertIn("L-8", contract_text)
            self.assertIn("CMP-12", contract_text)
            # Cross-team step 1 should appear under 人間が判断すること
            self.assertIn("Render contract and acceptance drafts", contract_text)
            self.assertIn("src/alpha/a.py", contract_text)
            self.assertIn("src/beta/b.py", contract_text)

    def test_emit_contract_and_acceptance_en_deterministic_and_force(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _make_sample_ir(root, with_decisions=False, with_alternatives=False)
            ir_path = self._write_ir(root, ir, name="en_sample.json")
            out_a = root / ".dev" / "compile" / "run_a"
            out_b = root / ".dev" / "compile" / "run_b"

            rc_a = main(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_a), "--lang", "en"]
            )
            rc_b = main(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_b), "--lang", "en"]
            )
            self.assertEqual(rc_a, 0)
            self.assertEqual(rc_b, 0)

            self.assertEqual((out_a / "contract.md").read_bytes(), (out_b / "contract.md").read_bytes())
            self.assertEqual(
                (out_a / "acceptance_tests.md").read_bytes(), (out_b / "acceptance_tests.md").read_bytes()
            )

            contract_en = (out_a / "contract.md").read_text(encoding="utf-8")
            headings_en = [
                "## Purpose",
                "## Human Decisions Required",
                "## Requirements",
                "## Decisions",
                "## Alternatives Considered",
                "## Source Documents",
            ]
            positions = [contract_en.index(h) for h in headings_en]
            self.assertEqual(positions, sorted(positions))
            self.assertIn("new", contract_en)
            self.assertIn("modify", contract_en)
            self.assertIn("Phase 77", contract_en)

            acc_en = (out_a / "acceptance_tests.md").read_text(encoding="utf-8")
            self.assertIn("Observable", acc_en)
            self.assertIn("Referenced by", acc_en)

            # --force preserves unrelated files and overwrites contract.md & acceptance_tests.md
            unrelated = out_a / "human_notes.txt"
            unrelated.write_text("keep me\n", encoding="utf-8")
            (out_a / "contract.md").write_text("stale\n", encoding="utf-8")
            (out_a / "acceptance_tests.md").write_text("stale\n", encoding="utf-8")

            rc_force = main(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--emit",
                    str(ir_path),
                    "--out-dir",
                    str(out_a),
                    "--lang",
                    "en",
                    "--force",
                ]
            )
            self.assertEqual(rc_force, 0)
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")
            self.assertIn("## Purpose", (out_a / "contract.md").read_text(encoding="utf-8"))
            self.assertIn("## AC-1", (out_a / "acceptance_tests.md").read_text(encoding="utf-8"))

    def test_emit_contract_and_acceptance_symlink_and_dir_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_sample_ir(root))
            protected = root / "SPEC.md"
            protected.write_text("# Protected SPEC\n", encoding="utf-8")

            for filename in ("contract.md", "acceptance_tests.md"):
                sym_dir = root / ".dev" / "compile" / f"sym_{filename}"
                sym_dir.mkdir(parents=True, exist_ok=True)
                (sym_dir / filename).symlink_to(protected)
                rc = main(
                    ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(sym_dir), "--force"]
                )
                self.assertEqual(rc, 2)
                self.assertEqual(protected.read_text(encoding="utf-8"), "# Protected SPEC\n")
                self.assertFalse((sym_dir / "ir.json").exists())

                dir_target = root / ".dev" / "compile" / f"dir_{filename}"
                (dir_target / filename).mkdir(parents=True, exist_ok=True)
                rc_dir = main(
                    ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(dir_target), "--force"]
                )
                self.assertEqual(rc_dir, 2)
                self.assertFalse((dir_target / "ir.json").exists())


if __name__ == "__main__":
    unittest.main()
