"""CMP-15 (Phase 79 Step 4): docs/ exclusion from tests and always-present mock_tests."""

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

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=root, check=True, capture_output=True)


def _make_valid_ir(root: Path, requirements: list[dict[str, Any]]) -> Path:
    doc_rel = "docs/designs/feature.md"
    doc_path = root / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: design spec\n" for i in range(1, 20)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    for idx, req in enumerate(requirements):
        if "source" not in req:
            s = min(1 + idx * 2, 16)
            req["source"] = {"path": doc_rel, "lines": [s, s + 1], "sha256": _range_sha(s, s + 1)}
        req.setdefault("kind", "modify")
        req.setdefault("acceptance", ["AC-1"])

    ir = {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": hashlib.sha256(doc_bytes).hexdigest()}],
        "purpose": {
            "statement": "Test CMP-15 impact details.",
            "source": {"path": doc_rel, "lines": [1, 2], "sha256": _range_sha(1, 2)},
        },
        "requirements": requirements,
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A repo",
                "when": "compile --impact runs",
                "then": "docs/ files are not tests and mock_tests is always present",
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


class TestCmp15ImpactDetails(unittest.TestCase):
    def _run_cli(self, args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
        env = _clean_env()
        src_path = str(_REPO_ROOT / "src")
        env["PYTHONPATH"] = f"{src_path}{os.pathsep}{env['PYTHONPATH']}" if "PYTHONPATH" in env else src_path
        return subprocess.run(
            [sys.executable, "-m", "auto_developer.cli", *args],
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_docs_test_files_are_treated_as_docs_and_mock_tests_always_present(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)

            (root / "docs" / "contracts" / "phase70" / "immunity").mkdir(parents=True)
            (root / "docs" / "contracts" / "phase70" / "immunity" / "test_immunity.py").write_text(
                "from unittest.mock import patch\n"
                "def test_doc_copy(tmp_path):\n"
                "    (tmp_path / 'events.jsonl').write_text('{}')\n"
                "    with patch('src.app.compute_score'):\n"
                "        return compute_score(1)\n",
                encoding="utf-8",
            )
            (root / "docs" / "test_example.py").write_text(
                "def test_example():\n"
                "    return compute_score(2)\n",
                encoding="utf-8",
            )
            (root / "tests").mkdir()
            (root / "tests" / "test_real.py").write_text(
                "def test_real():\n"
                "    return compute_score(3)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=root, check=True, capture_output=True)

            ir_path = _make_valid_ir(
                root,
                [
                    {
                        "id": "REQ-1",
                        "statement": "No fingerprints.",
                    },
                    {
                        "id": "REQ-2",
                        "statement": "Update compute_score.",
                        "fingerprints": ["compute_score"],
                    },
                ],
            )
            proc = self._run_cli(
                ["--work-dir", str(root), "compile", "--impact", str(ir_path), "--json"],
                cwd=root,
            )
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            payload = json.loads(proc.stdout)

            # record_tests must NOT include docs/ files
            self.assertEqual(payload["record_tests"], [])

            reqs = {r["id"]: r for r in payload["requirements"]}

            # Both requirements must always have mock_tests as a list (even when empty)
            self.assertIn("mock_tests", reqs["REQ-1"])
            self.assertEqual(reqs["REQ-1"]["mock_tests"], [])
            self.assertIn("mock_tests", reqs["REQ-2"])
            self.assertEqual(reqs["REQ-2"]["mock_tests"], [])

            # docs/**/test_*.py must appear in code_matches, NOT test_matches
            code_paths = {m["path"] for m in reqs["REQ-2"]["code_matches"]}
            test_paths = {m["path"] for m in reqs["REQ-2"]["test_matches"]}
            self.assertIn("docs/contracts/phase70/immunity/test_immunity.py", code_paths)
            self.assertIn("docs/test_example.py", code_paths)
            self.assertEqual(test_paths, {"tests/test_real.py"})


if __name__ == "__main__":
    unittest.main()
