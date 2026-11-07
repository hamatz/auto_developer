import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.reporter import MorningReporter


class TestMorningReporterEvidenceVerification(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Git リポジトリ初期化
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo, check=True)

        (self.repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True)

        self.records_dir = self.repo / ".git" / "nightly_evidence" / "records"
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_verified_evidence_record_displays_approved(self):
        """実 Evidence Record が存在し Tree SHA と Checker が正常なら APPROVE (Verified) と表示される。"""
        res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_tree = res_tree.stdout.strip()
        res_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_commit = res_head.stdout.strip()

        (self.records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "tree_sha": head_tree,
                "status": "COMPLETED",
                "checker": {"decision": "APPROVE", "reason": "All good"},
                "commit": {"tree_sha": head_tree, "sha": head_commit},
            }),
            encoding="utf-8",
        )

        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": head_commit,
                "completed_tasks": [
                    {
                        "task_id": "1",
                        "title": "Task 1",
                        "commit": head_commit,
                        "evidence_tree_sha": head_tree,
                        "committed_tree_sha": head_tree,
                    }
                ],
            },
        )

        content = report_path.read_text(encoding="utf-8")
        self.assertIn("APPROVE (Verified)", content)

    def test_missing_evidence_record_does_not_display_approved(self):
        """Evidence Record が存在しない場合、APPROVE を詐称せず UNVERIFIED と表示される。"""
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": "c1c0da8",
                "completed_tasks": [
                    {
                        "task_id": "99",
                        "title": "Unverified Task",
                        "commit": "c1c0da8",
                        # checker_decision を指定しない、またはあっても実レコードがない
                    }
                ],
            },
        )

        content = report_path.read_text(encoding="utf-8")
        self.assertNotIn("APPROVE (Verified)", content)
        self.assertTrue("UNVERIFIED" in content or "NO_RECORD" in content)

    def test_remote_guidance_omits_git_push_when_no_origin(self):
        """Git リモート (origin) が未設定の場合、git push origin main は案内しない。"""
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": "c1c0da8",
                "completed_tasks": [],
            },
        )

        content = report_path.read_text(encoding="utf-8")
        self.assertNotIn("git push origin", content)
        self.assertIn("git log", content)


if __name__ == "__main__":
    unittest.main()
