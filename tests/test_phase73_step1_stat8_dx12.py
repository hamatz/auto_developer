"""Phase 73 Step 1 (STAT-8, DX-12): Red-First tests for status fixes and example .gitignore."""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import DEFAULT_GITIGNORE_ENTRIES
from auto_developer.cli import main as cli_main
from auto_developer.evidence import resolve_evidence_records_dir
from auto_developer.multi_team import WorkerRegistration, WorkerRegistry
from auto_developer.reporter import MorningReporter

_REPO_ROOT = Path(__file__).resolve().parent.parent
_EXAMPLE_DIR = _REPO_ROOT / "examples" / "two-teams"


class TestStat8AndDx12(unittest.TestCase):
    """STAT-8 and DX-12 acceptance tests."""

    def _init_repo(self, repo: Path, blocked_section: str = "- なし。\n") -> str:
        subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (repo / "PLAN.md").write_text(
            "# Plan\n\n"
            "- [ ] 1. Pending task\n\n"
            "## 止まっていること・判断待ち\n"
            f"{blocked_section}",
            encoding="utf-8",
        )
        (repo / "src").mkdir(exist_ok=True)
        (repo / "src" / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_audit_stage_read_from_evidence_records_and_uses_verify_bundle_outdated_rule(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            audited_sha = self._init_repo(repo)
            records_dir = resolve_evidence_records_dir(repo)
            records_dir.mkdir(parents=True, exist_ok=True)
            (records_dir / "audit_stage.json").write_text(
                json.dumps({
                    "status": "SUCCESS",
                    "mode": "LIVE",
                    "provider_request_id": "msg_test123",
                    "target_commit": audited_sha,
                    "findings": [],
                }),
                encoding="utf-8",
            )

            # Commit only a docs/PLAN change after audit -> NOT AUDIT_OUTDATED
            (repo / "README.md").write_text("# Doc\n", encoding="utf-8")
            subprocess.run(["git", "add", "README.md"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "docs update"], cwd=repo, check=True, capture_output=True)

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertTrue(data["audit"]["present"])
            self.assertFalse(data["audit"]["outdated"])
            self.assertNotIn("AUDIT_OUTDATED", [a["kind"] for a in data["attention"]])

            # Commit a src/ change after audit -> AUDIT_OUTDATED
            (repo / "src" / "app.py").write_text("x = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "src/app.py"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "code update"], cwd=repo, check=True, capture_output=True)

            out2 = io.StringIO()
            with contextlib.redirect_stdout(out2):
                rc2 = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc2, 0)
            data2 = json.loads(out2.getvalue())
            self.assertTrue(data2["audit"]["outdated"])
            self.assertIn("AUDIT_OUTDATED", [a["kind"] for a in data2["attention"]])

    def test_audit_stage_edge_cases_match_verify_bundle_rules(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            head_sha = self._init_repo(repo)
            records_dir = resolve_evidence_records_dir(repo)
            records_dir.mkdir(parents=True, exist_ok=True)

            # 1) target_commit == HEAD -> outdated=False
            (records_dir / "audit_stage.json").write_text(
                json.dumps({"status": "SUCCESS", "mode": "LIVE", "target_commit": head_sha, "findings": []}),
                encoding="utf-8",
            )
            out_ok = io.StringIO()
            with contextlib.redirect_stdout(out_ok):
                self.assertEqual(cli_main(["--work-dir", str(repo), "status", "--json"]), 0)
            data_ok = json.loads(out_ok.getvalue())
            self.assertFalse(data_ok["audit"]["outdated"])
            self.assertNotIn("AUDIT_OUTDATED", [a["kind"] for a in data_ok["attention"]])

            # 2) target_commit is zero-filled or invalid -> outdated=True with check_audit_diff_status message
            for bad_commit in ("0" * 40, "not-a-sha"):
                (records_dir / "audit_stage.json").write_text(
                    json.dumps({"status": "SUCCESS", "mode": "LIVE", "target_commit": bad_commit, "findings": []}),
                    encoding="utf-8",
                )
                out_bad = io.StringIO()
                with contextlib.redirect_stdout(out_bad):
                    self.assertEqual(cli_main(["--work-dir", str(repo), "status", "--json"]), 0)
                data_bad = json.loads(out_bad.getvalue())
                self.assertTrue(data_bad["audit"]["outdated"])
                self.assertIn("AUDIT_TARGET_COMMIT_INVALID", str(data_bad["audit"].get("outdated_reason", "")))
                audit_att = [a for a in data_bad["attention"] if a["kind"] == "AUDIT_OUTDATED"]
                self.assertEqual(len(audit_att), 1)
                self.assertIn("AUDIT_TARGET_COMMIT_INVALID", audit_att[0]["message"])

            # 3) target_commit is nonexistent 40-hex (git diff fails) -> outdated=True with AUDIT_DIFF_FAILED message
            (records_dir / "audit_stage.json").write_text(
                json.dumps({"status": "SUCCESS", "mode": "LIVE", "target_commit": "a" * 40, "findings": []}),
                encoding="utf-8",
            )
            out_diff_fail = io.StringIO()
            with contextlib.redirect_stdout(out_diff_fail):
                self.assertEqual(cli_main(["--work-dir", str(repo), "status", "--json"]), 0)
            data_diff_fail = json.loads(out_diff_fail.getvalue())
            self.assertTrue(data_diff_fail["audit"]["outdated"])
            self.assertIn("AUDIT_DIFF_FAILED", str(data_diff_fail["audit"].get("outdated_reason", "")))
            audit_att_fail = [a for a in data_diff_fail["attention"] if a["kind"] == "AUDIT_OUTDATED"]
            self.assertEqual(len(audit_att_fail), 1)
            self.assertIn("AUDIT_DIFF_FAILED", audit_att_fail[0]["message"])

    def test_blocked_section_ignores_none_placeholders(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(
                repo,
                blocked_section="- なし\n- なし。\n* None\n- -\n",
            )
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(repo), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())
            self.assertEqual(data["plan"]["blocked_items"], [])
            self.assertNotIn("BLOCKED_ITEM", [a["kind"] for a in data["attention"]])

    def test_quarantine_branch_not_duplicated_and_team_failure_shows_cause(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams"
            shutil.copytree(_EXAMPLE_DIR, trial)
            subprocess.run(["git", "init", "-b", "main"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "add", "-A"], cwd=trial, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=trial, check=True, capture_output=True)

            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)

            pre_out = io.StringIO()
            with contextlib.redirect_stdout(pre_out):
                cli_main(["--work-dir", str(trial), "status", "--json"])
            job_id = json.loads(pre_out.getvalue())["teams"]["job_id"]

            # 1) Create quarantine branch and ensure it appears only ONCE in attention[]
            q_branch = f"quarantine/{job_id}/cli/task-1-123"
            subprocess.run(["git", "branch", q_branch], cwd=trial, check=True, capture_output=True)

            # 2) Mark core as FAILED with worker error, and cli as FAILED with a failure log
            wt_core = trial / ".dev" / "worktrees" / "core"
            wt_cli = trial / ".dev" / "worktrees" / "cli"
            (wt_cli / "plans" / "cli.md").write_text(
                "# CLI Plan\n\n- [ ] 1. **[FAILED]** Broken CLI task\n",
                encoding="utf-8",
            )
            fail_log = wt_cli / "logs" / "nightly" / "task_1_failure.log"
            MorningReporter().write_failure_log(
                fail_log,
                {
                    "task_id": "1",
                    "title": "Broken CLI task",
                    "checkpoint": "0" * 40,
                    "failure_phase": "VERIFY",
                    "reason": "AssertionError in cli tests",
                },
            )

            registry = WorkerRegistry(trial, job_id)
            workers = registry.load()
            if "core" in workers:
                registry.update_status("core", "FAILED", details={"error": "Timed out after 10s"})
            else:
                registry.register_worker(
                    WorkerRegistration(
                        team_id="core",
                        pid=99999,
                        pgid=99999,
                        worktree_path=str(wt_core),
                        base_branch="main",
                        branch_name="team/core",
                        status="FAILED",
                        start_time=0.0,
                        updated_at=0.0,
                        details={"error": "Timed out after 10s"},
                    )
                )

            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                rc = cli_main(["--work-dir", str(trial), "status", "--json"])
            self.assertEqual(rc, 0)
            data = json.loads(out.getvalue())

            q_mentions = [
                a for a in data["attention"]
                if q_branch in str(a.get("message", ""))
            ]
            self.assertEqual(len(q_mentions), 1, f"Quarantine branch duplicated in attention: {q_mentions}")

            team_failures = [a for a in data["attention"] if a["kind"] == "TEAM_FAILURE"]
            core_msgs = [a["message"] for a in team_failures if "'core'" in a["message"]]
            cli_msgs = [a["message"] for a in team_failures if "'cli'" in a["message"]]
            self.assertTrue(core_msgs and "Timed out after 10s" in core_msgs[0], f"Missing worker error in {core_msgs}")
            self.assertTrue(
                cli_msgs
                and "failed_tasks=1" in cli_msgs[0]
                and "VERIFY" in cli_msgs[0]
                and "AssertionError in cli tests" in cli_msgs[0],
                f"Missing failure log phase/reason in {cli_msgs}",
            )

    def test_examples_two_teams_gitignore_contains_all_core11_entries(self) -> None:
        gitignore_path = _EXAMPLE_DIR / ".gitignore"
        self.assertTrue(gitignore_path.is_file(), "examples/two-teams/.gitignore must exist")
        lines = {line.strip() for line in gitignore_path.read_text(encoding="utf-8").splitlines()}
        missing = [entry for entry in DEFAULT_GITIGNORE_ENTRIES if entry not in lines]
        self.assertEqual(missing, [], f"Missing CORE-11 gitignore entries in examples/two-teams/.gitignore: {missing}")


if __name__ == "__main__":
    unittest.main()
