"""CMP-3 (Step 6): auto-dev compile --check の点検 L-5, L-6, L-7 と --spec, INV-12 のテスト。"""

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
                "statement": "Quarantine failed work before restoring.",
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
                "given": "A dirty worktree",
                "when": "rollback runs",
                "then": "Changes are saved to quarantine branch",
                "observable": ["exit_code == 0"],
                "source": {
                    "path": doc_rel,
                    "lines": [6, 10],
                    "sha256": _line_range_sha256(doc_bytes, 6, 10),
                },
            }
        ],
        "decisions": [
            {
                "id": "D-1",
                "statement": "Never run destructive reset without quarantine.",
                "forbids": ["git reset --hard", "git clean -fd"],
                "source": {
                    "path": doc_rel,
                    "lines": [11, 15],
                    "sha256": _line_range_sha256(doc_bytes, 11, 15),
                },
            }
        ],
        "steps": [
            {
                "n": 1,
                "title": "Implement quarantine flow",
                "requirements": ["CORE-1"],
                "files": ["src/auto_developer/guard.py"],
            }
        ],
        "open_questions": [],
    }


def _sample_spec_md() -> str:
    return (
        "# SPEC.md\n\n"
        "## 3. 不変条件\n\n"
        "- **INV-1**: test\n\n"
        "## 4. 機能要件（実装済み）\n\n"
        "### 4.1 CORE\n\n"
        "- [x] **CORE-1 タスク取得**: existing requirement\n"
        "- [x] **GATE-2 Evidence**: existing gate\n\n"
        "## 5. 次のリリースで追加する要件\n\n"
        "- [ ] **CMP-3 点検**: future requirement in section 5\n"
    )


class TestCMP3CompileLints567(unittest.TestCase):
    """CMP-3 Step 6: L-5 (forbids), L-6 (--spec section 4), L-7 (teams owns), INV-12."""

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

    def test_l5_forbids_phrase_in_requirement_and_acceptance_is_error(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["requirements"][0]["statement"] = "Rollback by running git reset --hard immediately."
            ir["acceptance"][0]["given"] = "A failed task"
            ir["acceptance"][0]["when"] = "Cleanup runs git clean -fd"
            ir["acceptance"][0]["then"] = "It runs git reset --hard too"
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            l5_errors = [e for e in data["errors"] if e["rule"] == "L-5"]
            locs = {e["location"] for e in l5_errors}
            self.assertEqual(
                locs,
                {
                    "requirements[0].statement",
                    "acceptance[0].when",
                    "acceptance[0].then",
                },
            )
            for err in l5_errors:
                self.assertIn("decisions[0]", err["message"])
                self.assertIn("D-1", err["message"])

    def test_l5_is_case_sensitive_and_ignores_blank_forbids(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["decisions"][0]["forbids"] = ["git reset --hard", "", "   "]
            ir["requirements"][0]["statement"] = "Avoid GIT RESET --HARD in production."
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["errors"], [])

    def test_l6_warns_on_section4_id_mismatch_with_explicit_spec(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec_path = root / "custom_spec.md"
            spec_path.write_text(_sample_spec_md(), encoding="utf-8")

            ir = _create_valid_workspace(root)
            # CORE-1 is in section 4, but kind is 'new' -> WARN L-6
            # NEW-99 is not in section 4, but kind is 'modify' -> WARN L-6
            # CMP-3 is in section 5 (not section 4), kind is 'new' -> no warning
            req2 = dict(ir["requirements"][0])
            req2["id"] = "NEW-99"
            req2["kind"] = "modify"
            req3 = dict(ir["requirements"][0])
            req3["id"] = "CMP-3"
            req3["kind"] = "new"
            ir["requirements"].extend([req2, req3])
            ir["steps"][0]["requirements"].extend(["NEW-99", "CMP-3"])
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--check",
                    str(ir_path),
                    "--spec",
                    str(spec_path),
                    "--json",
                ]
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            self.assertEqual(data["errors"], [])
            l6_warnings = [w for w in data["warnings"] if w["rule"] == "L-6"]
            self.assertEqual(
                {w["location"] for w in l6_warnings},
                {"requirements[0].kind", "requirements[1].kind"},
            )

    def test_l6_uses_default_spec_in_work_dir_and_skips_when_absent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir_path = self._write_ir(root, ir)

            # No SPEC.md in root -> L-6 skipped
            proc_no_spec = self._run_cli(
                ["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"]
            )
            self.assertEqual(proc_no_spec.returncode, 0)
            self.assertEqual(json.loads(proc_no_spec.stdout)["warnings"], [])

            # Add SPEC.md in root -> L-6 detects CORE-1 with kind='new'
            (root / "SPEC.md").write_text(_sample_spec_md(), encoding="utf-8")
            proc_with_spec = self._run_cli(
                ["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"]
            )
            self.assertEqual(proc_with_spec.returncode, 0)
            l6_warnings = [
                w for w in json.loads(proc_with_spec.stdout)["warnings"] if w["rule"] == "L-6"
            ]
            self.assertEqual([w["location"] for w in l6_warnings], ["requirements[0].kind"])

    def test_l6_missing_explicit_spec_exits_two(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--check",
                    str(ir_path),
                    "--spec",
                    "missing_spec.md",
                    "--json",
                ]
            )
            self.assertEqual(proc.returncode, 2, proc.stdout + proc.stderr)

    def test_l7_team_ownership_overlap_and_unowned_file_are_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["teams"] = [
                {"id": "core", "owns": ["src/auto_developer/**"]},
                {"id": "cli", "owns": ["src/auto_developer/cli.py"]},
            ]
            ir["steps"][0]["files"] = [
                "src/auto_developer/guard.py",  # core only -> OK
                "src/auto_developer/cli.py",  # core + cli -> overlap ERROR
                "docs/cli.md",  # neither -> unowned ERROR
            ]
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 1, proc.stdout + proc.stderr)
            data = json.loads(proc.stdout)
            l7_errors = [e for e in data["errors"] if e["rule"] == "L-7"]
            self.assertEqual(
                {e["location"] for e in l7_errors},
                {"steps[0].files[1]", "steps[0].files[2]"},
            )

    def test_l7_valid_disjoint_teams_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir = _create_valid_workspace(root)
            ir["teams"] = [
                {"id": "core", "owns": ["src/auto_developer/guard.py"]},
                {"id": "cli", "owns": ["src/auto_developer/cli.py"]},
            ]
            ir["steps"][0]["files"] = [
                "src/auto_developer/guard.py",
                "src/auto_developer/cli.py",
            ]
            ir_path = self._write_ir(root, ir)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--check", str(ir_path), "--json"])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(json.loads(proc.stdout)["errors"], [])

    def test_inv12_compile_with_spec_writes_no_files(self) -> None:
        def _snapshot(root: Path) -> dict[str, str]:
            snap: dict[str, str] = {}
            for p in sorted(root.rglob("*")):
                rel = p.relative_to(root).as_posix()
                snap[rel] = "<DIR>" if p.is_dir() else _sha256_bytes(p.read_bytes())
            return snap

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec_path = root / "SPEC.md"
            spec_path.write_text(_sample_spec_md(), encoding="utf-8")
            ir = _create_valid_workspace(root)
            ir["teams"] = [{"id": "core", "owns": ["src/other/**"]}]
            ir["requirements"][0]["statement"] = "Run git reset --hard"
            ir_path = self._write_ir(root, ir)
            before = _snapshot(root)

            rc = main(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--check",
                    str(ir_path),
                    "--spec",
                    str(spec_path),
                ]
            )
            self.assertEqual(rc, 1)
            self.assertEqual(before, _snapshot(root))


if __name__ == "__main__":
    unittest.main()
