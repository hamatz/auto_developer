import contextlib
import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from auto_developer import cli
from auto_developer.bundler import EvidenceBundler
from auto_developer.dispatcher import resolve_target_plan_path
from auto_developer.evidence import EvidenceStore, resolve_evidence_records_dir
from auto_developer.manifest import ManifestValidationError
from auto_developer.reporter import MorningReporter
from tests.review_test_helpers import ensure_synthetic_review_evidence


def _init_git_repo(repo_dir: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo_dir, check=True, capture_output=True)
    (repo_dir / "README.md").write_text("init\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)


class TestBDL5RecordReading(unittest.TestCase):
    def test_resolve_evidence_records_dir_matches_evidence_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            normal_repo = tmp_path / "normal"
            normal_repo.mkdir()
            _init_git_repo(normal_repo)
            self.assertEqual(resolve_evidence_records_dir(normal_repo), EvidenceStore(normal_repo).records_dir)
            self.assertEqual(
                resolve_evidence_records_dir(normal_repo),
                normal_repo / ".git" / "nightly_evidence" / "records",
            )

            wt_repo = tmp_path / "wt"
            wt_repo.mkdir()
            (wt_repo / ".git").write_text("gitdir: ../normal/.git/worktrees/wt\n", encoding="utf-8")
            self.assertEqual(resolve_evidence_records_dir(wt_repo), EvidenceStore(wt_repo).records_dir)
            self.assertEqual(resolve_evidence_records_dir(wt_repo), wt_repo / ".dev" / "evidence")

    def test_reporter_ignores_archive_and_extraneous_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            (tmp_path / "PLAN.md").write_text("# Plan\n- [x] 1. Done\n", encoding="utf-8")

            records_dir = resolve_evidence_records_dir(tmp_path)
            records_dir.mkdir(parents=True, exist_ok=True)
            archive_dir = tmp_path / ".git" / "nightly_evidence" / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            extra_dir = tmp_path / ".dev" / "evidence"
            extra_dir.mkdir(parents=True, exist_ok=True)

            (records_dir / "task_1.json").write_text(
                json.dumps({"task_id": "1", "title": "1. Done", "status": "COMPLETED", "commit": "HEAD"}),
                encoding="utf-8",
            )
            (archive_dir / "task_2.json").write_text(
                json.dumps({"task_id": "2", "title": "2. Archived", "status": "COMPLETED", "commit": "HEAD"}),
                encoding="utf-8",
            )
            (extra_dir / "task_3.json").write_text(
                json.dumps({"task_id": "3", "title": "3. Extra", "status": "COMPLETED", "commit": "HEAD"}),
                encoding="utf-8",
            )

            report_path = tmp_path / "NIGHTLY_REPORT.md"
            MorningReporter(repo_dir=tmp_path).generate_standalone_report(report_path=report_path, repo_dir=tmp_path)
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("| `task-1` | Done |", content)
            self.assertNotIn("2. Archived", content)
            self.assertNotIn("3. Extra", content)

    def test_reporter_fallback_without_parsed_tasks_lists_only_completed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            (tmp_path / "PLAN.md").write_text("# Empty Plan\n", encoding="utf-8")

            records_dir = resolve_evidence_records_dir(tmp_path)
            records_dir.mkdir(parents=True, exist_ok=True)
            (records_dir / "task_1.json").write_text(
                json.dumps({"task_id": "1", "title": "Task One", "status": "COMPLETED", "commit": "HEAD"}),
                encoding="utf-8",
            )
            (records_dir / "task_2.json").write_text(
                json.dumps({"task_id": "2", "title": "Task Two", "status": "FAILED", "commit": "HEAD"}),
                encoding="utf-8",
            )

            report_path = tmp_path / "NIGHTLY_REPORT.md"
            MorningReporter(repo_dir=tmp_path).generate_standalone_report(report_path=report_path, repo_dir=tmp_path)
            content = report_path.read_text(encoding="utf-8")
            self.assertIn("**1 / 2** 完了", content)
            self.assertIn("Task One", content)
            self.assertNotIn("Task Two", content)

    def test_bundler_ignores_extraneous_dirs_in_normal_repo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            (tmp_path / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
            (tmp_path / "PLAN.md").write_text("# Plan\n- [x] 1. Done\n", encoding="utf-8")

            records_dir = resolve_evidence_records_dir(tmp_path)
            records_dir.mkdir(parents=True, exist_ok=True)
            archive_dir = tmp_path / ".git" / "nightly_evidence" / "archive"
            archive_dir.mkdir(parents=True, exist_ok=True)
            extra_dir = tmp_path / ".dev" / "evidence"
            extra_dir.mkdir(parents=True, exist_ok=True)

            (records_dir / "task_1.json").write_text(
                json.dumps({"task_id": "1", "status": "COMPLETED", "commit": "HEAD", "checker": {"decision": "APPROVE"}}),
                encoding="utf-8",
            )
            (archive_dir / "task_2.json").write_text(
                json.dumps({"task_id": "2", "status": "COMPLETED", "commit": "HEAD"}),
                encoding="utf-8",
            )
            (extra_dir / "task_3.json").write_text(
                json.dumps({"task_id": "3", "status": "COMPLETED", "commit": "HEAD"}),
                encoding="utf-8",
            )
            ensure_synthetic_review_evidence(tmp_path, "1")

            bundle_path = EvidenceBundler(repo_dir=tmp_path).create_bundle(dest_path=tmp_path / "bundle.zip")
            with zipfile.ZipFile(bundle_path, "r") as zf:
                names = zf.namelist()
            self.assertIn("evidence/task_1.json", names)
            self.assertNotIn("evidence/task_2.json", names)
            self.assertNotIn("evidence/task_3.json", names)

    def test_invalid_teams_json_propagates_and_cli_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _init_git_repo(tmp_path)
            (tmp_path / "teams.json").write_text("{invalid json", encoding="utf-8")
            subprocess.run(["git", "add", "teams.json"], cwd=tmp_path, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "add invalid teams.json"], cwd=tmp_path, check=True, capture_output=True)
            subprocess.run(["git", "checkout", "-b", "team/alpha"], cwd=tmp_path, check=True, capture_output=True)

            with self.assertRaises(ManifestValidationError):
                resolve_target_plan_path(tmp_path)

            for subcmd in ("report", "brief", "bundle"):
                err_buf = io.StringIO()
                with (
                    patch.object(sys, "argv", ["auto-dev", "--work-dir", str(tmp_path), subcmd]),
                    contextlib.redirect_stderr(err_buf),
                ):
                    rc = cli.main()
                self.assertEqual(rc, 2)
                self.assertIn("TEAMS_CONFIG_ERROR:", err_buf.getvalue())
