"""CMP-14 (Step 4): compile --emit への影響分析（impact.json・contract.md・plan.md）の反映テスト。"""

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


def _make_repo_and_ir(root: Path) -> Path:
    doc_rel = "docs/designs/sample.md"
    doc_path = root / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: sample design\n" for i in range(1, 35)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    # Tracked files for I-1, I-2, I-3, I-4
    (root / "README.md").write_text("# Sample Repo\nUses compute_score.\n", encoding="utf-8")
    (root / "src").mkdir(parents=True, exist_ok=True)
    (root / "src" / "engine.py").write_text(
        "def compute_score(x: int) -> int:\n    return x + 1\n",
        encoding="utf-8",
    )
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "test_engine.py").write_text(
        "from unittest.mock import patch\n"
        "from src.engine import compute_score\n\n"
        "def test_compute_score_mocked():\n"
        "    with patch('src.engine.compute_score') as m:\n"
        "        m.return_value = 2\n"
        "        assert compute_score(1) == 2\n"
        "        m.assert_called_once()\n",
        encoding="utf-8",
    )
    (root / "tests" / "test_records.py").write_text(
        "def test_synthetic_events(tmp_path):\n"
        "    p = tmp_path / 'events.jsonl'\n"
        "    p.write_text('{}\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )

    ir: dict[str, Any] = {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": hashlib.sha256(doc_bytes).hexdigest()}],
        "purpose": {
            "statement": "Reflect impact analysis into emit drafts.",
            "source": {"path": doc_rel, "lines": [1, 5], "sha256": _range_sha(1, 5)},
        },
        "requirements": [
            {
                "id": "CMP-14",
                "statement": "Update compute_score behavior.",
                "kind": "modify",
                "acceptance": ["AC-1"],
                "fingerprints": ["compute_score"],
                "source": {"path": doc_rel, "lines": [6, 10], "sha256": _range_sha(6, 10)},
            },
            {
                "id": "CMP-15",
                "statement": "Add unrelated feature without fingerprints.",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": {"path": doc_rel, "lines": [11, 15], "sha256": _range_sha(11, 15)},
            },
        ],
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A tracked repository",
                "when": "compile --emit runs",
                "then": "impact.json, contract.md, and plan.md include impact candidates",
                "observable": ["impact.json matches compile --impact --json"],
                "source": {"path": doc_rel, "lines": [16, 20], "sha256": _range_sha(16, 20)},
            }
        ],
        "steps": [
            {
                "n": 1,
                "title": "Update compute_score",
                "requirements": ["CMP-14"],
                "files": ["src/engine.py"],
            },
            {
                "n": 2,
                "title": "Add unrelated feature",
                "requirements": ["CMP-15"],
                "files": ["src/other.py"],
            },
        ],
        "verification": {
            "commands": ["python3 -m unittest tests/test_engine.py"],
            "source": {"path": doc_rel, "lines": [21, 25], "sha256": _range_sha(21, 25)},
        },
    }
    ir_path = root / "feature.ir.json"
    ir_path.write_text(json.dumps(ir, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return ir_path


class TestCmp14EmitImpact(unittest.TestCase):
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

    def test_emit_writes_impact_json_and_updates_contract_and_plan_ja(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)
            ir_path = _make_repo_and_ir(root)
            _git_add_all(root)

            proc = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("impact.json", proc.stdout)

            out_dir = root / ".dev" / "compile" / "feature"
            impact_file = out_dir / "impact.json"
            contract_file = out_dir / "contract.md"
            plan_file = out_dir / "plan.md"
            self.assertTrue(impact_file.is_file())

            # impact.json matches compile --impact --json byte-for-byte
            impact_cli = self._run_cli(["--work-dir", str(root), "compile", "--impact", str(ir_path), "--json"])
            self.assertEqual(impact_cli.returncode, 0, impact_cli.stdout + impact_cli.stderr)
            self.assertEqual(impact_file.read_text(encoding="utf-8"), impact_cli.stdout)

            contract_text = contract_file.read_text(encoding="utf-8")
            self.assertIn("## 影響分析", contract_text)
            self.assertIn("影響分析の候補を確かめ、既存テストを変えるかどうかを決める", contract_text)
            self.assertNotIn("Phase 77 で自動化するまで人間が書く", contract_text)
            self.assertIn("CMP-14", contract_text)
            self.assertIn("src/engine.py", contract_text)
            self.assertIn("tests/test_engine.py", contract_text)
            self.assertIn("test_compute_score_mocked", contract_text)
            self.assertIn("README.md", contract_text)
            self.assertIn("tests/test_records.py", contract_text)
            self.assertIn("指紋がないので調べていない", contract_text)

            plan_text = plan_file.read_text(encoding="utf-8")
            self.assertIn("## 既存テスト変更の理由", plan_text)
            self.assertIn("影響の候補: `tests/test_engine.py`", plan_text)
            self.assertIn("影響の候補なし", plan_text)

    def test_emit_impact_en_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)
            ir_path = _make_repo_and_ir(root)
            _git_add_all(root)

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

            for fname in ("impact.json", "contract.md", "plan.md"):
                self.assertEqual((out_a / fname).read_bytes(), (out_b / fname).read_bytes())

            contract_en = (out_a / "contract.md").read_text(encoding="utf-8")
            self.assertIn("## Impact Analysis", contract_en)
            self.assertIn("Review the impact analysis candidates and decide whether to modify existing tests.", contract_en)
            self.assertNotIn("until automated in Phase 77", contract_en)
            self.assertIn("tests/test_engine.py", contract_en)
            self.assertIn("tests/test_records.py", contract_en)

            plan_en = (out_a / "plan.md").read_text(encoding="utf-8")
            self.assertIn("Impact candidates: `tests/test_engine.py`", plan_en)
            self.assertIn("No impact candidates", plan_en)

    def test_emit_impact_symlink_and_dir_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _init_git_repo(root)
            ir_path = _make_repo_and_ir(root)
            _git_add_all(root)

            protected = root / "SPEC.md"
            protected.write_text("# Protected SPEC\n", encoding="utf-8")

            sym_dir = root / ".dev" / "compile" / "sym_impact"
            sym_dir.mkdir(parents=True, exist_ok=True)
            (sym_dir / "impact.json").symlink_to(protected)
            rc_sym = main(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(sym_dir), "--force"]
            )
            self.assertEqual(rc_sym, 2)
            self.assertEqual(protected.read_text(encoding="utf-8"), "# Protected SPEC\n")
            self.assertFalse((sym_dir / "ir.json").exists())

            dir_target = root / ".dev" / "compile" / "dir_impact"
            (dir_target / "impact.json").mkdir(parents=True, exist_ok=True)
            rc_dir = main(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(dir_target), "--force"]
            )
            self.assertEqual(rc_dir, 2)
            self.assertFalse((dir_target / "ir.json").exists())

    def test_emit_in_non_git_workdir_succeeds_with_empty_impact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = _make_repo_and_ir(root)
            rc = main(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(rc, 0)

            out_dir = root / ".dev" / "compile" / "feature"
            impact_data = json.loads((out_dir / "impact.json").read_text(encoding="utf-8"))
            self.assertEqual(impact_data["schema"], "auto-dev-impact/1")
            self.assertEqual(impact_data["record_tests"], [])
            self.assertIn("影響の候補なし", (out_dir / "plan.md").read_text(encoding="utf-8"))
            self.assertIn("## 影響分析", (out_dir / "contract.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
