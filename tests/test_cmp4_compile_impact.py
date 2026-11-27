"""CMP-4 (Phase 77 Step 2): compile --impact <IR> (I-1 fingerprint search & I-4 entry points)."""

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


def _snapshot_tree(root: Path) -> dict[str, bytes]:
    snap: dict[str, bytes] = {}
    for p in sorted(root.rglob("*")):
        if ".git" in p.parts:
            continue
        if p.is_file() and not p.is_symlink():
            snap[p.relative_to(root).as_posix()] = p.read_bytes()
    return snap


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test User"],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _git_add_all(root: Path) -> None:
    subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)


def _make_valid_ir(
    root: Path,
    requirements: list[dict[str, Any]],
) -> Path:
    doc_rel = "docs/designs/feature.md"
    doc_path = root / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: design spec\n" for i in range(1, 25)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    for idx, req in enumerate(requirements):
        if "source" not in req:
            s = min(1 + idx * 2, 20)
            req["source"] = {"path": doc_rel, "lines": [s, s + 1], "sha256": _range_sha(s, s + 1)}
        req.setdefault("kind", "modify")
        req.setdefault("acceptance", ["AC-1"])

    ir = {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": hashlib.sha256(doc_bytes).hexdigest()}],
        "purpose": {
            "statement": "Test impact analysis.",
            "source": {"path": doc_rel, "lines": [1, 2], "sha256": _range_sha(1, 2)},
        },
        "requirements": requirements,
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A repo",
                "when": "compile --impact runs",
                "then": "Candidates are listed",
                "observable": ["exit 0"],
                "source": {"path": doc_rel, "lines": [3, 4], "sha256": _range_sha(3, 4)},
            }
        ],
        "steps": [
            {
                "n": 1,
                "title": "Do work",
                "requirements": [requirements[0]["id"]],
                "files": ["src/app.py"],
            }
        ],
        "verification": {
            "commands": ["python3 -m unittest"],
            "source": {"path": doc_rel, "lines": [5, 6], "sha256": _range_sha(5, 6)},
        },
    }
    ir_path = root / "feature.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ir_path


class TestCmp4CompileImpact(unittest.TestCase):
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

    def test_i1_python_ast_distinguishes_def_ref_string_and_separates_tests(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            (root / "src").mkdir()
            (root / "src" / "app.py").write_text(
                "# compute_score comment only\n"
                "def compute_score(x):\n"
                "    return x + 1\n"
                "\n"
                "class ScoreHelper:\n"
                "    pass\n"
                "\n"
                "def caller(obj):\n"
                "    msg = 'calling compute_score now'\n"
                "    return compute_score(1) + obj.compute_score()\n",
                encoding="utf-8",
            )
            (root / "src" / "config.toml").write_text(
                "[tool]\nentry = 'compute_score'\n",
                encoding="utf-8",
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_app.py").write_text(
                "import unittest\n"
                "from src.app import compute_score\n"
                "\n"
                "class TestApp(unittest.TestCase):\n"
                "    def test_it(self):\n"
                "        self.assertEqual(compute_score(1), 2)\n",
                encoding="utf-8",
            )
            (root / "src" / "helper_test.py").write_text(
                "def test_extra():\n"
                "    return 'compute_score'\n",
                encoding="utf-8",
            )
            # Untracked file must be ignored
            (root / "src" / "untracked.py").write_text(
                "def compute_score():\n    pass\n",
                encoding="utf-8",
            )
            # .dev/ tracked file must be ignored
            (root / ".dev").mkdir()
            (root / ".dev" / "scratch.py").write_text(
                "def compute_score():\n    pass\n",
                encoding="utf-8",
            )
            _git_add_all(root)
            # Remove untracked.py from index so it stays untracked
            subprocess.run(
                ["git", "reset", "HEAD", "--", "src/untracked.py"],
                cwd=root,
                check=True,
                capture_output=True,
            )

            ir_path = _make_valid_ir(
                root,
                [
                    {
                        "id": "REQ-2",
                        "statement": "Update compute_score.",
                        "fingerprints": ["compute_score", "ScoreHelper"],
                    },
                    {
                        "id": "REQ-1",
                        "statement": "No fingerprints here.",
                    },
                ],
            )
            before = _snapshot_tree(root)

            proc = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(ir_path), "--json"]
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertEqual(before, _snapshot_tree(root))

            payload = json.loads(proc.stdout)
            self.assertEqual(payload["schema"], "auto-dev-impact/1")
            self.assertEqual(payload["record_tests"], [])

            reqs = payload["requirements"]
            # Sorted by requirement ID: REQ-1 then REQ-2
            self.assertEqual([r["id"] for r in reqs], ["REQ-1", "REQ-2"])

            req1, req2 = reqs[0], reqs[1]
            self.assertFalse(req1["searched"])
            self.assertIn("指紋がないので調べていない", req1["note"])
            self.assertEqual(req1["code_matches"], [])
            self.assertEqual(req1["test_matches"], [])
            self.assertEqual(req1["entry_points"], [])
            self.assertEqual(req1["mock_tests"], [])

            self.assertTrue(req2["searched"])
            self.assertEqual(req2["mock_tests"], [])
            code_matches = req2["code_matches"]
            test_matches = req2["test_matches"]

            # Verify untracked and .dev/ are excluded
            all_paths = {m["path"] for m in code_matches + test_matches}
            self.assertNotIn("src/untracked.py", all_paths)
            self.assertNotIn(".dev/scratch.py", all_paths)

            # Verify comment on line 1 of src/app.py is NOT matched
            self.assertFalse(
                any(m["path"] == "src/app.py" and m["line"] == 1 for m in code_matches)
            )

            # Verify AST kinds in src/app.py
            expected_app = {
                (2, "definition", "compute_score"),
                (5, "definition", "ScoreHelper"),
                (9, "string", "compute_score"),
                (10, "reference", "compute_score"),
            }
            actual_app = {
                (m["line"], m["kind"], m["fingerprint"])
                for m in code_matches
                if m["path"] == "src/app.py"
            }
            self.assertEqual(actual_app, expected_app)

            # Verify non-Python text match in src/config.toml
            self.assertIn(
                {"path": "src/config.toml", "line": 2, "kind": "text", "fingerprint": "compute_score"},
                code_matches,
            )

            # Verify test files separated into test_matches
            test_paths = {m["path"] for m in test_matches}
            self.assertEqual(test_paths, {"tests/test_app.py", "src/helper_test.py"})
            # ImportFrom line 2 is not matched as reference (only Name/Attribute per SPEC)
            self.assertFalse(
                any(
                    m["path"] == "tests/test_app.py" and m["line"] == 2
                    for m in test_matches
                )
            )
            self.assertTrue(
                any(
                    m["path"] == "tests/test_app.py" and m["line"] == 6 and m["kind"] == "reference"
                    for m in test_matches
                )
            )
            self.assertTrue(
                any(
                    m["path"] == "src/helper_test.py" and m["line"] == 2 and m["kind"] == "string"
                    for m in test_matches
                )
            )

    def test_i4_entry_points_and_plain_text_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            (root / "README.md").write_text("Run `my_entry` here.\n", encoding="utf-8")
            (root / "README.en.md").write_text("Nothing here.\n", encoding="utf-8")
            (root / "skills" / "demo").mkdir(parents=True)
            (root / "skills" / "demo" / "SKILL.md").write_text(
                "Use `my_entry` carefully.\n", encoding="utf-8"
            )
            (root / "docs" / "nested").mkdir(parents=True)
            (root / "docs" / "cli.md").write_text("Option `my_entry`.\n", encoding="utf-8")
            (root / "docs" / "nested" / "deep.md").write_text("Deep `my_entry`.\n", encoding="utf-8")
            (root / "scripts").mkdir()
            (root / "scripts" / "run_it.py").write_text(
                "def my_entry():\n"
                "    pass\n"
                "\n"
                "if __name__ == '__main__':\n"
                "    my_entry()\n",
                encoding="utf-8",
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_script.py").write_text(
                "import unittest\n"
                "def helper():\n"
                "    return 'my_entry'\n"
                "if __name__ == '__main__':\n"
                "    unittest.main()\n",
                encoding="utf-8",
            )
            _git_add_all(root)

            ir_path = _make_valid_ir(
                root,
                [
                    {
                        "id": "ENT-1",
                        "statement": "Check entry points.",
                        "fingerprints": ["my_entry"],
                    },
                    {
                        "id": "ENT-2",
                        "statement": "No fingerprints.",
                        "fingerprints": [],
                    },
                ],
            )
            proc = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(ir_path), "--json"]
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)
            ent1 = payload["requirements"][0]
            ep_paths = [e["path"] for e in ent1["entry_points"]]
            self.assertEqual(
                ep_paths,
                [
                    "README.md",
                    "docs/cli.md",
                    "scripts/run_it.py",
                    "skills/demo/SKILL.md",
                    "tests/test_script.py",
                ],
            )
            # docs/nested/deep.md must NOT be entry_points; tests/test_script.py IS included per SPEC
            self.assertNotIn("docs/nested/deep.md", ep_paths)
            test_ep = next(e for e in ent1["entry_points"] if e["path"] == "tests/test_script.py")
            self.assertEqual(test_ep["category"], "script")

            # Plain text output
            proc_txt = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(ir_path)]
            )
            self.assertEqual(proc_txt.returncode, 0, proc_txt.stdout + proc_txt.stderr)
            self.assertIn("ENT-1", proc_txt.stdout)
            self.assertIn("README.md", proc_txt.stdout)
            self.assertIn("ENT-2", proc_txt.stdout)
            self.assertIn("指紋がないので調べていない", proc_txt.stdout)

    def test_exit_codes_and_resilience(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            # Broken Python syntax & binary file should not crash
            (root / "src").mkdir()
            (root / "src" / "broken.py").write_text("def broken(:\n    target_sym\n", encoding="utf-8")
            (root / "src" / "data.bin").write_bytes(b"\x00\x01\x02target_sym\xff\xfe")
            _git_add_all(root)

            valid_ir = _make_valid_ir(
                root,
                [{"id": "RES-1", "statement": "Resilience.", "fingerprints": ["target_sym"]}],
            )
            proc_ok = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(valid_ir), "--json"]
            )
            self.assertEqual(proc_ok.returncode, 0, proc_ok.stdout + proc_ok.stderr)
            res_payload = json.loads(proc_ok.stdout)
            # broken.py falls back to text match; data.bin is skipped
            paths = {m["path"] for m in res_payload["requirements"][0]["code_matches"]}
            self.assertIn("src/broken.py", paths)
            self.assertNotIn("src/data.bin", paths)

            # Missing IR -> exit 2
            self.assertEqual(
                main(["--work-dir", str(root), "compile", "--impact", "missing.ir.json"]),
                2,
            )

            # Invalid JSON -> exit 2
            bad_json = root / "bad.ir.json"
            bad_json.write_text("{not json", encoding="utf-8")
            self.assertEqual(
                main(["--work-dir", str(root), "compile", "--impact", str(bad_json)]),
                2,
            )

            # F-1 schema error -> exit 1
            bad_schema = root / "f1_err.ir.json"
            bad_schema.write_text(
                json.dumps({"schema": "auto-dev-ir/1", "requirements": []}),
                encoding="utf-8",
            )
            self.assertEqual(
                main(["--work-dir", str(root), "compile", "--impact", str(bad_schema)]),
                1,
            )

            # Wrong IR schema version -> exit 2 (matches compile --check)
            wrong_schema_ver = root / "wrong_ver.ir.json"
            wrong_schema_ver.write_text(
                json.dumps({"schema": "auto-dev-ir/999", "requirements": []}),
                encoding="utf-8",
            )
            self.assertEqual(
                main(["--work-dir", str(root), "compile", "--check", str(wrong_schema_ver)]),
                2,
            )
            self.assertEqual(
                main(["--work-dir", str(root), "compile", "--impact", str(wrong_schema_ver)]),
                2,
            )

            # Mode conflict -> exit 2
            self.assertEqual(
                main([
                    "--work-dir",
                    str(root),
                    "compile",
                    "--impact",
                    str(valid_ir),
                    "--check",
                    str(valid_ir),
                ]),
                2,
            )


if __name__ == "__main__":
    unittest.main()
