"""CMP-5 (Step 1): auto-dev compile --emit <IR> の骨格・事前検査・INV-12・決定性・共通ヘッダのテスト。"""

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
from auto_developer.compiler import render_draft_header

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _make_valid_ir(work_dir: Path) -> dict[str, Any]:
    doc_rel = "docs/designs/sample.md"
    doc_path = work_dir / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: sample design\n" for i in range(1, 31)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    file_sha = hashlib.sha256(doc_bytes).hexdigest()
    return {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": file_sha}],
        "purpose": {
            "statement": "Emit draft artifacts from validated IR.",
            "source": {"path": doc_rel, "lines": [1, 5], "sha256": _range_sha(1, 5)},
        },
        "requirements": [
            {
                "id": "CMP-5",
                "statement": "Add compile --emit skeleton.",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": {"path": doc_rel, "lines": [6, 10], "sha256": _range_sha(6, 10)},
            }
        ],
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A valid IR file",
                "when": "compile --emit runs",
                "then": "Draft files are emitted under .dev/compile/",
                "observable": ["ir.json and check.json exist"],
                "source": {"path": doc_rel, "lines": [11, 15], "sha256": _range_sha(11, 15)},
            }
        ],
        "decisions": [
            {
                "id": "D-1",
                "statement": "Write drafts only under .dev/compile/.",
                "forbids": ["SPEC.md mutation"],
                "source": {"path": doc_rel, "lines": [16, 20], "sha256": _range_sha(16, 20)},
            }
        ],
        "steps": [
            {
                "n": 1,
                "title": "Implement compile --emit skeleton",
                "requirements": ["CMP-5"],
                "files": ["src/auto_developer/compiler.py", "src/auto_developer/cli.py"],
            }
        ],
        "verification": {
            "commands": ["pytest tests/test_cmp5_compile_emit.py"],
            "source": {"path": doc_rel, "lines": [21, 25], "sha256": _range_sha(21, 25)},
        },
    }


def _snapshot_outside_compile(root: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if rel == ".dev" or rel.startswith(".dev/compile"):
            continue
        if p.is_file() and not p.is_symlink():
            snap[rel] = p.read_bytes()
    return snap


class TestCmp5CompileEmit(unittest.TestCase):
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

    def _write_ir(self, directory: Path, payload: dict[str, Any], name: str = "sample_ir.json") -> Path:
        path = directory / name
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return path

    def test_emit_default_out_dir_writes_ir_and_check_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root), name="my_feature.json")
            ir_bytes = ir_path.read_bytes()
            (root / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            (root / "PLAN.md").write_text("# PLAN\n", encoding="utf-8")
            before_outside = _snapshot_outside_compile(root)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            out_dir = root / ".dev" / "compile" / "my_feature"
            self.assertTrue(out_dir.is_dir())
            emitted_ir = out_dir / "ir.json"
            emitted_check = out_dir / "check.json"
            self.assertTrue(emitted_ir.is_file())
            self.assertTrue(emitted_check.is_file())
            self.assertEqual(emitted_ir.read_bytes(), ir_bytes)

            check_proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(check_proc.returncode, 0)
            self.assertEqual(emitted_check.read_text(encoding="utf-8"), check_proc.stdout)

            self.assertIn("ir.json", proc.stdout)
            self.assertIn("check.json", proc.stdout)
            self.assertEqual(before_outside, _snapshot_outside_compile(root))

    def test_emit_precheck_error_exits_one_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _make_valid_ir(root)
            ir["open_questions"] = ["Unresolved question"]
            ir_path = self._write_ir(root, ir, name="broken.json")

            proc = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(proc.returncode, 1)
            self.assertIn("ERROR", proc.stdout)
            self.assertIn("L-9", proc.stdout)
            self.assertFalse((root / ".dev" / "compile").exists())

    def test_emit_unreadable_or_invalid_ir_exits_two_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.json"
            proc_missing = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(missing)])
            self.assertEqual(proc_missing.returncode, 2)
            self.assertFalse((root / ".dev" / "compile").exists())

            bad_json = root / "bad.json"
            bad_json.write_text("{not json", encoding="utf-8")
            proc_bad = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(bad_json)])
            self.assertEqual(proc_bad.returncode, 2)
            self.assertFalse((root / ".dev" / "compile").exists())

    def test_emit_spec_handling_matches_check(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root))
            missing_spec = root / "missing_spec.md"
            proc_missing = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--spec", str(missing_spec)]
            )
            self.assertEqual(proc_missing.returncode, 2)
            self.assertFalse((root / ".dev" / "compile").exists())

            spec_path = root / "custom_spec.md"
            spec_path.write_text("## 4. Existing\n- [x] **CMP-5**: already in spec\n", encoding="utf-8")
            proc_warn = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--spec", str(spec_path)]
            )
            self.assertEqual(proc_warn.returncode, 0, proc_warn.stdout + proc_warn.stderr)
            check_data = json.loads((root / ".dev" / "compile" / "sample_ir" / "check.json").read_text(encoding="utf-8"))
            self.assertTrue(any(w["rule"] == "L-6" for w in check_data["warnings"]))

    def test_emit_out_dir_restriction_and_symlink_escape_return_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root))

            outside_candidates = [
                root / "outside",
                root / ".dev" / "other",
                root / ".dev" / "compile",
                root / ".dev" / "compile" / ".." / "escape",
            ]
            for cand in outside_candidates:
                existed_before = cand.exists()
                proc = self._run_cli(
                    ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(cand)]
                )
                self.assertEqual(proc.returncode, 2, f"expected 2 for {cand}: {proc.stdout + proc.stderr}")
                if not existed_before:
                    self.assertFalse(cand.exists())

            compile_dir = root / ".dev" / "compile"
            compile_dir.mkdir(parents=True, exist_ok=True)
            escape_target = root / "escape_target"
            escape_target.mkdir()
            symlink_out = compile_dir / "linked_out"
            symlink_out.symlink_to(escape_target)

            proc_sym = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(symlink_out)]
            )
            self.assertEqual(proc_sym.returncode, 2)
            self.assertEqual(list(escape_target.iterdir()), [])

    def test_emit_existing_files_require_force_and_preserve_unrelated_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root))
            out_dir = root / ".dev" / "compile" / "custom"
            out_dir.mkdir(parents=True, exist_ok=True)
            unrelated = out_dir / "human_notes.md"
            unrelated.write_text("keep me\n", encoding="utf-8")
            old_ir = out_dir / "ir.json"
            old_ir.write_text("old\n", encoding="utf-8")

            proc_no_force = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_dir)]
            )
            self.assertEqual(proc_no_force.returncode, 2)
            self.assertEqual(old_ir.read_text(encoding="utf-8"), "old\n")
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")

            proc_force = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_dir), "--force"]
            )
            self.assertEqual(proc_force.returncode, 0, proc_force.stdout + proc_force.stderr)
            self.assertEqual(old_ir.read_bytes(), ir_path.read_bytes())
            self.assertEqual(unrelated.read_text(encoding="utf-8"), "keep me\n")

    def test_emit_deterministic_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root))
            out_a = root / ".dev" / "compile" / "run_a"
            out_b = root / ".dev" / "compile" / "run_b"

            rc_a = main(["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_a)])
            rc_b = main(["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_b)])
            self.assertEqual(rc_a, 0)
            self.assertEqual(rc_b, 0)

            for fname in ("ir.json", "check.json"):
                self.assertEqual((out_a / fname).read_bytes(), (out_b / fname).read_bytes())

    def test_common_draft_header_helper_and_lang_option(self) -> None:
        sha = "a" * 64
        ja_header = render_draft_header("feature.ir.json", sha, lang="ja")
        en_header = render_draft_header("feature.ir.json", sha, lang="en")

        for header in (ja_header, en_header):
            self.assertNotIn("<!--", header)
            self.assertIn("INV-12", header)
            self.assertIn("feature.ir.json", header)
            self.assertIn(sha, header)
            self.assertIn("SPEC.md", header)
            self.assertIn("PLAN.md", header)

        self.assertIn("Spec Compiler", ja_header)
        self.assertIn("下書き", ja_header)
        self.assertIn("Spec Compiler", en_header)
        self.assertIn("draft", en_header.lower())
        self.assertEqual(ja_header, render_draft_header("feature.ir.json", sha, lang="ja"))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root))
            proc_en = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--lang", "en"]
            )
            self.assertEqual(proc_en.returncode, 0, proc_en.stdout + proc_en.stderr)

            proc_bad_lang = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--lang", "fr"]
            )
            self.assertEqual(proc_bad_lang.returncode, 2)

    def test_emit_conflicting_mode_flags_return_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root))
            doc_path = root / "design.md"
            doc_path.write_text("# Design\n", encoding="utf-8")

            proc_check = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--check", str(ir_path)]
            )
            self.assertEqual(proc_check.returncode, 2)

            proc_outline = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--outline", str(doc_path)]
            )
            self.assertEqual(proc_outline.returncode, 2)

    def test_emit_existing_dir_without_force_and_ir_error_exits_one_and_preserves_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _make_valid_ir(root)
            ir["open_questions"] = ["Unresolved question"]
            ir_path = self._write_ir(root, ir, name="broken.json")
            out_dir = root / ".dev" / "compile" / "existing"
            out_dir.mkdir(parents=True, exist_ok=True)
            existing_ir = out_dir / "ir.json"
            existing_ir.write_text("keep existing\n", encoding="utf-8")

            proc = self._run_cli(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(out_dir)]
            )
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            self.assertIn("ERROR", proc.stdout)
            self.assertEqual(existing_ir.read_text(encoding="utf-8"), "keep existing\n")
            self.assertFalse((out_dir / "check.json").exists())

    def test_emit_force_rejects_symlink_target_and_writes_nothing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root))
            spec_file = root / "SPEC.md"
            spec_file.write_text("# Protected SPEC\n", encoding="utf-8")

            out_dir = root / ".dev" / "compile" / "sym_target"
            out_dir.mkdir(parents=True, exist_ok=True)
            (out_dir / "ir.json").write_text("before\n", encoding="utf-8")
            (out_dir / "check.json").symlink_to(spec_file)

            proc = self._run_cli(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--emit",
                    str(ir_path),
                    "--out-dir",
                    str(out_dir),
                    "--force",
                ]
            )
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertEqual(spec_file.read_text(encoding="utf-8"), "# Protected SPEC\n")
            self.assertEqual((out_dir / "ir.json").read_text(encoding="utf-8"), "before\n")

    def test_emit_force_rejects_directory_target_cleanly_without_traceback(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_valid_ir(root))
            out_dir = root / ".dev" / "compile" / "dir_target"
            (out_dir / "ir.json").mkdir(parents=True, exist_ok=True)

            proc = self._run_cli(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--emit",
                    str(ir_path),
                    "--out-dir",
                    str(out_dir),
                    "--force",
                ]
            )
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertFalse((out_dir / "check.json").exists())


if __name__ == "__main__":
    unittest.main()
