from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path

from auto_developer.bundler import EvidenceBundler
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import MockRunnerAdapter

SPEC_TEXT = "# SPEC\n\n## 2. 要件\n- [ ] **APP-1**: `ok()` returns True.\n"


class _FileWritingMockRunner(MockRunnerAdapter):
    def __init__(self, repo_dir: Path) -> None:
        super().__init__(checker_approve=True, checker_reason="All good.\nDECISION: APPROVE")
        self.repo_dir = repo_dir

    def run_maker(self, task, context):  # type: ignore[override]
        (self.repo_dir / "app.py").write_text("def ok():\n    return True\n", encoding="utf-8")
        return 0, "done"


class TestGate7AndBdl4(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "config", "user.name", "Tester"],
            ["git", "config", "user.email", "tester@example.com"],
        ):
            subprocess.run(cmd, cwd=self.repo, check=True, capture_output=True)
        (self.repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n", encoding="utf-8")
        (self.repo / "SPEC.md").write_text(SPEC_TEXT, encoding="utf-8")
        (self.repo / "PLAN.md").write_text("# PLAN\n\n## ステップ\n- [ ] 1. Implement ok\n", encoding="utf-8")
        verify = self.repo / "verify.sh"
        verify.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        verify.chmod(0o755)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run_orch_one_task(self) -> Path:
        runner = _FileWritingMockRunner(self.repo)
        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            plan_path=self.repo / "PLAN.md",
            spec_path=self.repo / "SPEC.md",
            verify_script=self.repo / "verify.sh",
            runner_adapter=runner,
            maker_model="gemini-3.8-flash",
            checker_model="claude-opus-5-5-medium",
        )
        rc = orch.run()
        self.assertEqual(rc, 0)
        bundles = sorted((self.repo / ".dev" / "bundles").glob("bundle_*.zip"))
        self.assertTrue(bundles)
        return bundles[-1]

    def test_gate7_orchestrator_records_review_files_and_checker_review_metadata(self) -> None:
        bundle_zip = self._run_orch_one_task()
        ev_dir = self.repo / ".git" / "nightly_evidence"
        rec = json.loads((ev_dir / "records" / "task_1.json").read_text(encoding="utf-8"))
        review = rec.get("checker", {}).get("review", {})
        self.assertEqual(review.get("decision_source"), "checker_output")
        for key in ("output_sha256", "diff_sha256", "spec_sha256"):
            self.assertRegex(str(review.get(key, "")), r"^[0-9a-f]{64}$")
        self.assertIsInstance(review.get("recorded_at"), (int, float))

        copies_dir = ev_dir / "reviews" / "task_1"
        out_copy = copies_dir / "checker_output.txt"
        diff_copy = copies_dir / "diff.patch"
        spec_copy = copies_dir / "spec_excerpt.md"
        self.assertTrue(out_copy.is_file())
        self.assertTrue(diff_copy.is_file())
        self.assertTrue(spec_copy.is_file())
        self.assertEqual(hashlib.sha256(out_copy.read_bytes()).hexdigest(), review["output_sha256"])
        self.assertEqual(hashlib.sha256(diff_copy.read_bytes()).hexdigest(), review["diff_sha256"])
        self.assertEqual(hashlib.sha256(spec_copy.read_bytes()).hexdigest(), review["spec_sha256"])

        # BDL-4: bundle contains review files and passes verify-bundle
        with zipfile.ZipFile(bundle_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
            self.assertEqual(manifest.get("format_version"), 3)
            self.assertIn("evidence/reviews/task_1/checker_output.txt", zf.namelist())
            self.assertIn("evidence/reviews/task_1/diff.patch", zf.namelist())
            self.assertIn("evidence/reviews/task_1/spec_excerpt.md", zf.namelist())

        res = EvidenceBundler(repo_dir=self.repo).verify_bundle(bundle_zip)
        self.assertTrue(res.is_valid, res.errors)
        self.assertEqual(res.status, "VERIFIED")

    def test_bdl4_verify_bundle_rejects_v2_and_tampered_or_reject_review_output(self) -> None:
        bundle_zip = self._run_orch_one_task()
        bundler = EvidenceBundler(repo_dir=self.repo)

        # 1. format_version=2 rejected with v0.9 guidance
        v2_zip = self.repo / "v2.zip"
        with zipfile.ZipFile(bundle_zip, "r") as zin, zipfile.ZipFile(v2_zip, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "bundle_manifest.json":
                    m = json.loads(data.decode("utf-8"))
                    m["format_version"] = 2
                    data = json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8")
                zout.writestr(item, data)
        res_v2 = bundler.verify_bundle(v2_zip)
        self.assertFalse(res_v2.is_valid)
        self.assertEqual(res_v2.status, "INVALID")
        self.assertTrue(any("v0.9" in e for e in res_v2.errors), res_v2.errors)

        # 2. Tampered checker_output.txt (even if file_hashes updated, checker.review hash mismatches)
        tampered_zip = self.repo / "tampered_out.zip"
        new_out = b"All good.\nDECISION: REJECT\n"
        with zipfile.ZipFile(bundle_zip, "r") as zin, zipfile.ZipFile(tampered_zip, "w") as zout:
            for item in zin.infolist():
                data = zin.read(item.filename)
                if item.filename == "evidence/reviews/task_1/checker_output.txt":
                    data = new_out
                elif item.filename == "bundle_manifest.json":
                    m = json.loads(data.decode("utf-8"))
                    m["file_hashes"]["evidence/reviews/task_1/checker_output.txt"] = hashlib.sha256(
                        new_out
                    ).hexdigest()
                    data = json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8")
                zout.writestr(item, data)
        res_tampered = bundler.verify_bundle(tampered_zip)
        self.assertFalse(res_tampered.is_valid)
        self.assertEqual(res_tampered.status, "INVALID")

        # 3. Missing checker.review in bundled evidence -> INVALID
        missing_rev_zip = self.repo / "missing_rev.zip"
        with zipfile.ZipFile(bundle_zip, "r") as zin, zipfile.ZipFile(missing_rev_zip, "w") as zout:
            ev_bytes = b""
            for item in zin.infolist():
                if item.filename == "evidence/task_1.json":
                    ev_obj = json.loads(zin.read(item.filename).decode("utf-8"))
                    ev_obj.get("checker", {}).pop("review", None)
                    ev_bytes = json.dumps(ev_obj, ensure_ascii=False, indent=2).encode("utf-8")
                    zout.writestr(item, ev_bytes)
                elif item.filename != "bundle_manifest.json":
                    zout.writestr(item, zin.read(item.filename))
            m = json.loads(zin.read("bundle_manifest.json").decode("utf-8"))
            m["file_hashes"]["evidence/task_1.json"] = hashlib.sha256(ev_bytes).hexdigest()
            zout.writestr("bundle_manifest.json", json.dumps(m, ensure_ascii=False, indent=2).encode("utf-8"))
        res_missing = bundler.verify_bundle(missing_rev_zip)
        self.assertFalse(res_missing.is_valid)
        self.assertEqual(res_missing.status, "INVALID")

    def test_bdl4_excludes_record_without_review_evidence_as_integrity_only(self) -> None:
        self._run_orch_one_task()
        ev_file = self.repo / ".git" / "nightly_evidence" / "records" / "task_1.json"
        ev_data = json.loads(ev_file.read_text(encoding="utf-8"))
        ev_data["checker"].pop("review", None)
        ev_file.write_text(json.dumps(ev_data, ensure_ascii=False, indent=2), encoding="utf-8")

        bundler = EvidenceBundler(repo_dir=self.repo)
        out_zip = bundler.create_bundle(dest_path=self.repo / "no_rev_bundle.zip")
        with zipfile.ZipFile(out_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
            self.assertEqual(manifest.get("tasks"), [])
            excluded = manifest.get("excluded_records", [])
            self.assertTrue(
                any(str(e.get("task_id")) == "1" and e.get("reason") == "NO_REVIEW_EVIDENCE" for e in excluded),
                excluded,
            )

        res = bundler.verify_bundle(out_zip)
        self.assertEqual(res.status, "INTEGRITY_ONLY", f"errors={res.errors}, warnings={res.warnings}")
        combined = "\n".join(res.warnings + res.errors)
        self.assertIn("NO_REVIEW_EVIDENCE", combined)
        self.assertIn("1", combined)

    def test_gate7_rejects_missing_decision_line_and_records_rejected_review_files(self) -> None:
        class _NoDecisionRunner(_FileWritingMockRunner):
            def run_checker(self, task, diff, spec_content):  # type: ignore[override]
                return True, "Looks fine without decision line"

        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            plan_path=self.repo / "PLAN.md",
            spec_path=self.repo / "SPEC.md",
            verify_script=self.repo / "verify.sh",
            runner_adapter=_NoDecisionRunner(self.repo),
            maker_model="gemini-3.8-flash",
            checker_model="claude-opus-5-5-medium",
        )
        rc = orch.run()
        self.assertNotEqual(rc, 0)
        rej_base = self.repo / ".git" / "nightly_evidence" / "reviews" / "rejected"
        attempt_dirs = sorted(rej_base.glob("*/task_1/attempt_*"))
        self.assertTrue(attempt_dirs, f"Expected rejected attempt dir under {rej_base}")
        copies_dir = attempt_dirs[0]
        self.assertTrue((copies_dir / "checker_output.txt").is_file())
        self.assertEqual(
            (copies_dir / "checker_output.txt").read_text(encoding="utf-8"),
            "Looks fine without decision line",
        )
        self.assertTrue((copies_dir / "diff.patch").is_file())
        self.assertTrue((copies_dir / "spec_excerpt.md").is_file())
        rec = json.loads((copies_dir / "review.json").read_text(encoding="utf-8"))
        self.assertEqual(rec["evidence_token"], "")
        self.assertEqual(rec["maker_model"], "gemini-3.8-flash")
        self.assertEqual(rec["checker_model"], "claude-opus-5-5-medium")
        self.assertRegex(rec["checker_output_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(rec["diff_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(rec["spec_sha256"], r"^[0-9a-f]{64}$")
        self.assertIsInstance(rec["recorded_at"], (int, float))

    def test_parse_checker_decision_strict_full_json_or_unique_line(self) -> None:
        from auto_developer.runner import parse_checker_decision

        self.assertEqual(parse_checker_decision('  {"decision": "APPROVE"} \n'), "APPROVE")
        self.assertIsNone(parse_checker_decision('Some prose\n{"decision": "APPROVE"}\n'))
        self.assertEqual(parse_checker_decision('{"decision": "APPROVE"}\nDECISION: REJECT\n'), "REJECT")
        self.assertIsNone(parse_checker_decision('DECISION: APPROVE\nDECISION: REJECT\n'))


if __name__ == "__main__":
    unittest.main()
