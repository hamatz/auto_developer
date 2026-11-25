"""CMP-10 (Step 3): plan.md、teams.json、plans/<id>.md の下書き出力のテスト。"""

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
from auto_developer.dispatcher import TaskDispatcher
from auto_developer.manifest import load_teams_manifest

_REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _make_base_ir(
    work_dir: Path,
    *,
    steps: list[dict[str, Any]] | None = None,
    teams: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    doc_rel = "docs/designs/sample.md"
    doc_path = work_dir / doc_rel
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_bytes = "".join(f"Line {i}: sample design\n" for i in range(1, 35)).encode("utf-8")
    doc_path.write_bytes(doc_bytes)
    lines = doc_bytes.splitlines(keepends=True)

    def _range_sha(start: int, end: int) -> str:
        return hashlib.sha256(b"".join(lines[start - 1 : end])).hexdigest()

    ir: dict[str, Any] = {
        "schema": "auto-dev-ir/1",
        "source": [{"path": doc_rel, "sha256": hashlib.sha256(doc_bytes).hexdigest()}],
        "purpose": {
            "statement": "Emit plan and team manifests from IR.",
            "source": {"path": doc_rel, "lines": [1, 5], "sha256": _range_sha(1, 5)},
        },
        "requirements": [
            {
                "id": "CMP-10",
                "statement": "Emit plan.md, teams.json, and plans/<id>.md.",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": {"path": doc_rel, "lines": [6, 10], "sha256": _range_sha(6, 10)},
            },
            {
                "id": "CMP-5",
                "statement": "Include plan.md in compile --emit output.",
                "kind": "modify",
                "acceptance": ["AC-1"],
                "source": {"path": doc_rel, "lines": [11, 15], "sha256": _range_sha(11, 15)},
            },
        ],
        "acceptance": [
            {
                "id": "AC-1",
                "given": "A valid IR with steps",
                "when": "auto-dev compile --emit runs",
                "then": "plan.md and optional team artifacts are emitted",
                "observable": ["plan.md is readable by TaskDispatcher"],
                "source": {"path": doc_rel, "lines": [16, 20], "sha256": _range_sha(16, 20)},
            }
        ],
        "steps": steps
        if steps is not None
        else [
            {
                "n": 1,
                "title": "Render plan.md draft",
                "requirements": ["CMP-10"],
                "files": ["src/auto_developer/compiler.py"],
            },
            {
                "n": 2,
                "title": "Emit team manifests and plans",
                "requirements": ["CMP-10", "CMP-5"],
                "files": ["src/auto_developer/compiler.py", "tests/test_cmp10_emit_plan_teams.py"],
            },
        ],
        "verification": {
            "commands": ["python3 -m unittest tests/test_cmp10_emit_plan_teams.py"],
            "source": {"path": doc_rel, "lines": [21, 25], "sha256": _range_sha(21, 25)},
        },
    }
    if teams is not None:
        ir["teams"] = teams
    return ir


class TestCmp10EmitPlanTeams(unittest.TestCase):
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

    def test_emit_writes_plan_md_ja_without_teams(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_base_ir(root), name="no_teams.json")
            ir_sha = hashlib.sha256(ir_path.read_bytes()).hexdigest()

            proc = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
            self.assertIn("plan.md", proc.stdout)

            out_dir = root / ".dev" / "compile" / "no_teams"
            plan_out = out_dir / "plan.md"
            self.assertTrue(plan_out.is_file())
            self.assertFalse((out_dir / "teams.json").exists())
            self.assertFalse((out_dir / "plans").exists())

            text = plan_out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("> "))
            self.assertIn("INV-12", text)
            self.assertIn("no_teams.json", text)
            self.assertIn(ir_sha, text)

            self.assertIn("## ステップ", text)
            step1 = "- [ ] 1. Render plan.md draft（CMP-10）"
            step2 = "- [ ] 2. Emit team manifests and plans（CMP-10, CMP-5）"
            self.assertIn(step1, text)
            self.assertIn(step2, text)
            self.assertLess(text.index(step1), text.index(step2))
            self.assertIn("src/auto_developer/compiler.py", text)
            self.assertIn("tests/test_cmp10_emit_plan_teams.py", text)

            tasks = TaskDispatcher().parse_plan_text(text)
            self.assertEqual([t.task_id for t in tasks], ["1", "2"])
            self.assertTrue(all(not t.is_completed for t in tasks))

            self.assertIn("## 既存テスト変更の理由", text)
            self.assertIn("（影響分析の後に、人間が書く。既定は「既存テストは変更しない」）", text)
            self.assertIn(
                "このフェーズで前のステップが作ったテストの資料は、後のステップの検査に合わせて直してよい（アサーションは変えない）",
                text,
            )
            self.assertIn("免疫テスト（`tests/immunity/**`）は変更しない", text)
            self.assertIn("CLI を変えたステップでは `docs/cli.md` を作り直す", text)

    def test_emit_plan_md_en_and_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ir_path = self._write_ir(root, _make_base_ir(root, teams=[]), name="en_plan.json")
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

            plan_a = (out_a / "plan.md").read_bytes()
            plan_b = (out_b / "plan.md").read_bytes()
            self.assertEqual(plan_a, plan_b)

            text = plan_a.decode("utf-8")
            self.assertIn("## Steps", text)
            self.assertIn("- [ ] 1. Render plan.md draft (CMP-10)", text)
            self.assertIn("## Reasons for Existing Test Changes", text)
            self.assertIn("docs/cli.md", text)
            self.assertNotIn("既存テスト変更の理由", text)
            self.assertFalse((out_a / "teams.json").exists())
            self.assertFalse((out_a / "plans").exists())

    def test_emit_with_teams_writes_teams_json_and_team_plans(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            teams = [
                {"id": "alpha", "owns": ["src/alpha/**"]},
                {"id": "beta", "owns": ["src/beta/**"]},
            ]
            steps = [
                {
                    "n": 1,
                    "title": "Build alpha module",
                    "requirements": ["CMP-10"],
                    "files": ["src/alpha/a.py"],
                },
                {
                    "n": 2,
                    "title": "Build beta module",
                    "requirements": ["CMP-5"],
                    "files": ["src/beta/b.py"],
                },
                {
                    "n": 3,
                    "title": "Wire alpha and beta",
                    "requirements": ["CMP-10", "CMP-5"],
                    "files": ["src/alpha/c.py", "src/beta/d.py"],
                },
            ]
            ir_path = self._write_ir(
                root,
                _make_base_ir(root, steps=steps, teams=teams),
                name="multi_team.json",
            )

            proc = self._run_cli(["--work-dir", str(root), "compile", "--emit", str(ir_path)])
            self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

            # Never write teams.json to repo root
            self.assertFalse((root / "teams.json").exists())
            self.assertFalse((root / "plans").exists())

            out_dir = root / ".dev" / "compile" / "multi_team"
            teams_json_path = out_dir / "teams.json"
            alpha_plan_path = out_dir / "plans" / "alpha.md"
            beta_plan_path = out_dir / "plans" / "beta.md"
            self.assertTrue(teams_json_path.is_file())
            self.assertTrue(alpha_plan_path.is_file())
            self.assertTrue(beta_plan_path.is_file())

            manifest = load_teams_manifest(root, manifest_path=teams_json_path)
            self.assertEqual(manifest.version, 1)
            self.assertEqual(manifest.project, "multi_team")
            self.assertEqual([t.id for t in manifest.teams], ["alpha", "beta"])
            self.assertEqual(manifest.teams[0].plan, "plans/alpha.md")
            self.assertEqual(manifest.teams[1].plan, "plans/beta.md")
            self.assertEqual(manifest.teams[0].depends_on, [])
            self.assertEqual(manifest.teams[1].depends_on, [])

            alpha_text = alpha_plan_path.read_text(encoding="utf-8")
            self.assertIn("INV-12", alpha_text)
            self.assertIn("Build alpha module", alpha_text)
            self.assertNotIn("Build beta module", alpha_text)
            self.assertNotIn("Wire alpha and beta", alpha_text)
            self.assertEqual([t.task_id for t in TaskDispatcher().parse_plan_text(alpha_text)], ["1"])

            beta_text = beta_plan_path.read_text(encoding="utf-8")
            self.assertIn("INV-12", beta_text)
            self.assertIn("Build beta module", beta_text)
            self.assertNotIn("Build alpha module", beta_text)
            self.assertNotIn("Wire alpha and beta", beta_text)
            self.assertEqual([t.task_id for t in TaskDispatcher().parse_plan_text(beta_text)], ["2"])

    def test_emit_plan_and_teams_symlink_safety(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            teams = [
                {"id": "alpha", "owns": ["src/alpha/**"]},
            ]
            steps = [
                {
                    "n": 1,
                    "title": "Build alpha module",
                    "requirements": ["CMP-10"],
                    "files": ["src/alpha/a.py"],
                }
            ]
            ir_path = self._write_ir(root, _make_base_ir(root, steps=steps, teams=teams))
            protected = root / "PLAN.md"
            protected.write_text("# Protected\n", encoding="utf-8")

            # 1. Symlink at plan.md
            sym_plan_dir = root / ".dev" / "compile" / "sym_plan"
            sym_plan_dir.mkdir(parents=True, exist_ok=True)
            (sym_plan_dir / "plan.md").symlink_to(protected)
            rc1 = main(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(sym_plan_dir), "--force"]
            )
            self.assertEqual(rc1, 2)
            self.assertEqual(protected.read_text(encoding="utf-8"), "# Protected\n")
            self.assertFalse((sym_plan_dir / "ir.json").exists())

            # 2. Symlink at teams.json
            sym_teams_dir = root / ".dev" / "compile" / "sym_teams"
            sym_teams_dir.mkdir(parents=True, exist_ok=True)
            (sym_teams_dir / "teams.json").symlink_to(protected)
            rc2 = main(
                ["--work-dir", str(root), "compile", "--emit", str(ir_path), "--out-dir", str(sym_teams_dir), "--force"]
            )
            self.assertEqual(rc2, 2)
            self.assertEqual(protected.read_text(encoding="utf-8"), "# Protected\n")
            self.assertFalse((sym_teams_dir / "ir.json").exists())

            # 3. Symlink at plans directory
            escape_dir = root / "escape_plans"
            escape_dir.mkdir()
            sym_plans_dir = root / ".dev" / "compile" / "sym_plans_dir"
            sym_plans_dir.mkdir(parents=True, exist_ok=True)
            (sym_plans_dir / "plans").symlink_to(escape_dir)
            rc3 = main(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--emit",
                    str(ir_path),
                    "--out-dir",
                    str(sym_plans_dir),
                    "--force",
                ]
            )
            self.assertEqual(rc3, 2)
            self.assertEqual(list(escape_dir.iterdir()), [])
            self.assertFalse((sym_plans_dir / "ir.json").exists())

            # 4. Symlink at plans/alpha.md
            sym_team_plan_dir = root / ".dev" / "compile" / "sym_team_plan"
            (sym_team_plan_dir / "plans").mkdir(parents=True, exist_ok=True)
            (sym_team_plan_dir / "plans" / "alpha.md").symlink_to(protected)
            rc4 = main(
                [
                    "--work-dir",
                    str(root),
                    "compile",
                    "--emit",
                    str(ir_path),
                    "--out-dir",
                    str(sym_team_plan_dir),
                    "--force",
                ]
            )
            self.assertEqual(rc4, 2)
            self.assertEqual(protected.read_text(encoding="utf-8"), "# Protected\n")
            self.assertFalse((sym_team_plan_dir / "ir.json").exists())


if __name__ == "__main__":
    unittest.main()
