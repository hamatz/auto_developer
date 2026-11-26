"""STAT-11: status --json requirements[], design_irs[], and DESIGN_DRIFT tests."""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from auto_developer.cli import main as cli_main
from auto_developer.compiler import IR_SCHEMA_VERSION, anchor_source_range


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)


def _run_status_json(repo: Path) -> dict[str, Any]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = cli_main(["--work-dir", str(repo), "status", "--json"])
    if rc != 0:
        raise AssertionError(f"status --json exited with {rc}")
    parsed = json.loads(out.getvalue())
    assert isinstance(parsed, dict)
    return parsed


def _build_valid_ir(repo: Path, doc_rel: str) -> dict[str, Any]:
    doc_path = repo / doc_rel
    doc_bytes = doc_path.read_bytes()
    doc_sha = hashlib.sha256(doc_bytes).hexdigest()
    _, a1, _ = anchor_source_range(f"{doc_rel}:3-3", work_dir=repo)
    _, a2, _ = anchor_source_range(f"{doc_rel}:4-4", work_dir=repo)
    _, a_ac, _ = anchor_source_range(f"{doc_rel}:6-6", work_dir=repo)
    _, a_ver, _ = anchor_source_range(f"{doc_rel}:8-8", work_dir=repo)
    return {
        "schema": IR_SCHEMA_VERSION,
        "source": [{"path": doc_rel, "sha256": doc_sha}],
        "requirements": [
            {
                "id": "REQ-1",
                "statement": "First requirement statement",
                "kind": "new",
                "acceptance": ["AC-1"],
                "source": a1,
            },
            {
                "id": "REQ-2",
                "statement": "Second requirement statement",
                "kind": "modify",
                "acceptance": ["AC-1"],
                "source": a2,
            },
        ],
        "acceptance": [
            {
                "id": "AC-1",
                "given": "repo exists",
                "when": "status runs",
                "then": "requirements are shown",
                "observable": ["status --json"],
                "source": a_ac,
            }
        ],
        "decisions": [],
        "steps": [
            {"n": 1, "title": "Implement REQ-1", "requirements": ["REQ-1"], "files": ["src/a.py"]},
            {"n": 2, "title": "Implement REQ-2", "requirements": ["REQ-2"], "files": ["src/b.py"]},
        ],
        "verification": {"commands": ["python3 -m unittest"], "source": a_ver},
    }


class TestStat11StatusRequirements(unittest.TestCase):
    """STAT-11: requirements[], design_irs[], DESIGN_DRIFT in status --json."""

    def test_plan_only_requirements_parens_and_statuses(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text(
                "# Plan\n\n"
                "- [x] 1. Done task (REQ-1)\n"
                "- [x] 2. Part A（Red-First）（REQ-2, REQ-3）\n"
                "- [ ] 3. Part B (REQ-2)\n"
                "- [ ] 4. Pending task (not-an-id)（REQ-4）\n"
                "- [ ] 5. Unscoped task (wip)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            data = _run_status_json(repo)
            self.assertEqual(data["schema"], "auto-dev-status/2")
            self.assertEqual(data["design_irs"], [])

            reqs = {r["id"]: r for r in data["requirements"]}
            self.assertEqual(set(reqs.keys()), {"REQ-1", "REQ-2", "REQ-3", "REQ-4"})

            self.assertEqual(reqs["REQ-1"]["status"], "done")
            self.assertIsNone(reqs["REQ-1"]["statement"])
            self.assertIsNone(reqs["REQ-1"]["source"])
            self.assertEqual([t["task_id"] for t in reqs["REQ-1"]["tasks"]], ["1"])

            self.assertEqual(reqs["REQ-2"]["status"], "in_progress")
            self.assertEqual([t["task_id"] for t in reqs["REQ-2"]["tasks"]], ["2", "3"])

            self.assertEqual(reqs["REQ-3"]["status"], "done")
            self.assertEqual(reqs["REQ-4"]["status"], "pending")

    def test_approved_ir_enriches_requirements_and_reports_ok(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text(
                "# Plan\n\n"
                "- [x] 1. Done task（REQ-1）\n"
                "- [ ] 2. Extra task (EXTRA-9)\n",
                encoding="utf-8",
            )
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True)
            (designs_dir / "feature.md").write_text(
                "# Feature\n\n"
                "REQ-1: First requirement\n"
                "REQ-2: Second requirement\n\n"
                "AC-1: Observable\n\n"
                "Verify: unittest\n",
                encoding="utf-8",
            )
            ir_data = _build_valid_ir(repo, "docs/designs/feature.md")
            ir_path = designs_dir / "feature.ir.json"
            ir_bytes = json.dumps(ir_data, indent=2).encode("utf-8")
            ir_path.write_bytes(ir_bytes)
            expected_ir_sha = hashlib.sha256(ir_bytes).hexdigest()

            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            data = _run_status_json(repo)
            self.assertEqual(len(data["design_irs"]), 1)
            dir_entry = data["design_irs"][0]
            self.assertEqual(dir_entry["path"], "docs/designs/feature.ir.json")
            self.assertEqual(dir_entry["sha256"], expected_ir_sha)
            self.assertEqual(dir_entry["status"], "ok")
            self.assertEqual(dir_entry["mismatches"], [])

            drift_items = [a for a in data["attention"] if a.get("kind") == "DESIGN_DRIFT"]
            self.assertEqual(drift_items, [])

            reqs = {r["id"]: r for r in data["requirements"]}
            self.assertEqual(set(reqs.keys()), {"REQ-1", "REQ-2", "EXTRA-9"})
            self.assertEqual(reqs["REQ-1"]["statement"], "First requirement statement")
            self.assertEqual(reqs["REQ-1"]["source"], ir_data["requirements"][0]["source"])
            self.assertEqual(reqs["REQ-1"]["status"], "done")

            # REQ-2 is in IR but not referenced by any task in PLAN.md
            self.assertEqual(reqs["REQ-2"]["statement"], "Second requirement statement")
            self.assertEqual(reqs["REQ-2"]["tasks"], [])
            self.assertEqual(reqs["REQ-2"]["status"], "pending")

            # EXTRA-9 is in PLAN.md only
            self.assertIsNone(reqs["EXTRA-9"]["statement"])
            self.assertIsNone(reqs["EXTRA-9"]["source"])
            self.assertEqual(reqs["EXTRA-9"]["status"], "pending")

    def test_design_drift_detected_when_source_doc_changes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# Plan\n\n- [ ] 1. Task (REQ-1)\n", encoding="utf-8")
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True)
            doc_path = designs_dir / "feature.md"
            doc_path.write_text(
                "# Feature\n\n"
                "REQ-1: First requirement\n"
                "REQ-2: Second requirement\n\n"
                "AC-1: Observable\n\n"
                "Verify: unittest\n",
                encoding="utf-8",
            )
            ir_data = _build_valid_ir(repo, "docs/designs/feature.md")
            (designs_dir / "feature.ir.json").write_text(json.dumps(ir_data, indent=2), encoding="utf-8")

            # Mutate design doc after IR approval
            doc_path.write_text(
                "# Feature\n\n"
                "REQ-1: MODIFIED requirement\n"
                "REQ-2: Second requirement\n\n"
                "AC-1: Observable\n\n"
                "Verify: unittest\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            data = _run_status_json(repo)
            self.assertEqual(len(data["design_irs"]), 1)
            dir_entry = data["design_irs"][0]
            self.assertEqual(dir_entry["status"], "drift")
            self.assertTrue(len(dir_entry["mismatches"]) >= 1)
            locations = {m["location"] for m in dir_entry["mismatches"]}
            self.assertIn("requirements[0].source.sha256", locations)

            drift_items = [a for a in data["attention"] if a.get("kind") == "DESIGN_DRIFT"]
            self.assertEqual(len(drift_items), 1)
            self.assertIn("feature.ir.json", drift_items[0]["message"])

    def test_unreadable_or_invalid_ir_reports_reason_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# Plan\n\n- [ ] 1. Task (REQ-1)\n", encoding="utf-8")
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True)
            (designs_dir / "broken.ir.json").write_text("{not valid json", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            data = _run_status_json(repo)
            self.assertEqual(len(data["design_irs"]), 1)
            entry = data["design_irs"][0]
            self.assertEqual(entry["path"], "docs/designs/broken.ir.json")
            self.assertEqual(entry["status"], "error")
            self.assertTrue(entry.get("reason"))
            self.assertFalse(any(a.get("kind") == "DESIGN_IR_ERROR" for a in data["attention"]))
            self.assertEqual(len(data["requirements"]), 1)
            self.assertEqual(data["requirements"][0]["id"], "REQ-1")

    def test_schema_mismatch_and_non_dict_ir_report_error_without_attention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# Plan\n\n- [ ] 1. Task (REQ-1)\n", encoding="utf-8")
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True)
            (designs_dir / "bad_schema.ir.json").write_text(
                json.dumps({"schema": "wrong-schema/99"}),
                encoding="utf-8",
            )
            (designs_dir / "non_dict.ir.json").write_text(json.dumps([1, 2, 3]), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            data = _run_status_json(repo)
            by_path = {e["path"]: e for e in data["design_irs"]}
            self.assertEqual(
                set(by_path.keys()),
                {"docs/designs/bad_schema.ir.json", "docs/designs/non_dict.ir.json"},
            )
            for entry in by_path.values():
                self.assertEqual(entry["status"], "error")
                self.assertTrue(entry.get("reason"))
            self.assertIn("wrong-schema/99", by_path["docs/designs/bad_schema.ir.json"]["reason"])
            self.assertIn("list", by_path["docs/designs/non_dict.ir.json"]["reason"])
            self.assertFalse(any(a.get("kind") == "DESIGN_IR_ERROR" for a in data["attention"]))

    def test_oserror_reading_ir_reports_error_without_attention(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# Plan\n\n- [ ] 1. Task (REQ-1)\n", encoding="utf-8")
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True)
            unreadable = designs_dir / "unreadable.ir.json"
            unreadable.write_text("{}", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            orig_read_bytes = Path.read_bytes

            def _fake_read_bytes(self_path: Path) -> bytes:
                if self_path.name == "unreadable.ir.json":
                    raise OSError("permission denied")
                return orig_read_bytes(self_path)

            with mock.patch.object(Path, "read_bytes", _fake_read_bytes):
                data = _run_status_json(repo)

            self.assertEqual(len(data["design_irs"]), 1)
            entry = data["design_irs"][0]
            self.assertEqual(entry["path"], "docs/designs/unreadable.ir.json")
            self.assertIsNone(entry["sha256"])
            self.assertEqual(entry["status"], "error")
            self.assertIn("permission denied", entry["reason"])
            self.assertFalse(any(a.get("kind") == "DESIGN_IR_ERROR" for a in data["attention"]))

    def test_duplicate_requirement_id_across_multiple_irs_first_wins(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# Plan\n\n- [ ] 1. Task (REQ-1)\n", encoding="utf-8")
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True)
            for name, stmt in [("a.md", "From A"), ("b.md", "From B")]:
                (designs_dir / name).write_text(
                    f"# {name}\n\nREQ-1: {stmt}\nREQ-2: Second\n\nAC-1: Obs\n\nVerify: unittest\n",
                    encoding="utf-8",
                )
            ir_a = _build_valid_ir(repo, "docs/designs/a.md")
            ir_a["requirements"][0]["statement"] = "First IR statement"
            ir_b = _build_valid_ir(repo, "docs/designs/b.md")
            ir_b["requirements"][0]["statement"] = "Second IR statement"
            (designs_dir / "a.ir.json").write_text(json.dumps(ir_a, indent=2), encoding="utf-8")
            (designs_dir / "b.ir.json").write_text(json.dumps(ir_b, indent=2), encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            data = _run_status_json(repo)
            reqs = {r["id"]: r for r in data["requirements"]}
            self.assertEqual(reqs["REQ-1"]["statement"], "First IR statement")

    def test_multiple_trailing_parentheses_only_last_group_inspected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_git_repo(repo)
            (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (repo / "PLAN.md").write_text(
                "# Plan\n\n- [ ] 1. Task (REQ-1) (wip)\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

            data = _run_status_json(repo)
            self.assertEqual(data["requirements"], [])

    def test_multi_team_requirements_include_team_id(self) -> None:
        example_dir = Path(__file__).resolve().parent.parent / "examples" / "two-teams"
        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams"
            shutil.copytree(example_dir, trial)
            (trial / "plans" / "core.md").write_text(
                "# Core Plan\n\n- [x] 1. Core part (SHARED-1)\n",
                encoding="utf-8",
            )
            (trial / "plans" / "cli.md").write_text(
                "# CLI Plan\n\n- [ ] 1. CLI part（SHARED-1）\n",
                encoding="utf-8",
            )
            _init_git_repo(trial)
            subprocess.run(["git", "add", "-A"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=trial, check=True, capture_output=True)
            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)

            data = _run_status_json(trial)
            reqs = {r["id"]: r for r in data["requirements"]}
            self.assertIn("SHARED-1", reqs)
            shared = reqs["SHARED-1"]
            self.assertEqual(shared["status"], "in_progress")
            teams_referenced = {t["team"] for t in shared["tasks"]}
            self.assertEqual(teams_referenced, {"core", "cli"})


if __name__ == "__main__":
    unittest.main()
