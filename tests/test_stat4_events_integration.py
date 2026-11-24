from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from auto_developer.bundler import BundleVerificationResult, EvidenceBundler
from auto_developer.integration_gate import IntegrationGate, TeamIntegrationInput


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


class TestStat4EventsIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp_dir.name).resolve() / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.name", "Stat4 Int Tester")
        _git(self.repo, "config", "user.email", "stat4int@example.com")
        (self.repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
        (self.repo / "SPEC.md").write_text("# SPEC\n\n- [ ] **APP-1**: sample\n", encoding="utf-8")
        (self.repo / "PLAN.md").write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 73: test\n\n## ステップ\n- [ ] 1. Sample task\n",
            encoding="utf-8",
        )
        scripts_dir = self.repo / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        self.verify_sh = scripts_dir / "verify_local.sh"
        self.verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.verify_sh.chmod(0o755)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "chore: init")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _read_events(self) -> list[dict[str, Any]]:
        events_path = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        self.assertTrue(events_path.is_file(), f"events.jsonl missing: {events_path}")
        return [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def _make_team_branch(
        self, team_id: str, filename: str, content: str
    ) -> tuple[TeamIntegrationInput, str, BundleVerificationResult]:
        _git(self.repo, "checkout", "-b", f"team/{team_id}", "main")
        target = self.repo / filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        _git(self.repo, "add", filename)
        _git(self.repo, "commit", "-m", f"feat({team_id}): add {filename}")
        head_sha = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        _git(self.repo, "checkout", "main")

        bundle_path = Path(self.tmp_dir.name) / f"{team_id}.zip"
        bundle_path.write_bytes(b"fake-bundle")
        ver_res = BundleVerificationResult(
            is_valid=True,
            status="VERIFIED",
            job_id="job-stat4-int",
            head_commit=head_sha,
            tasks=[{"id": "1", "title": f"{team_id} task", "evidence": {"token": f"tok-{team_id}"}}],
        )
        inp = TeamIntegrationInput(
            team_id=team_id,
            bundle_path=bundle_path,
            branch=f"team/{team_id}",
            head_commit=head_sha,
        )
        return inp, head_sha, ver_res

    def test_integration_success_records_start_merge_reverify_rc_tag(self) -> None:
        core_inp, core_sha, core_ver = self._make_team_branch("core", "core.txt", "core\n")
        ui_inp, ui_sha, ui_ver = self._make_team_branch("ui", "ui.txt", "ui\n")

        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.side_effect = [core_ver, ui_ver]

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler)
        res = gate.execute_integration(
            job_id="job-stat4-ok",
            inputs=[core_inp, ui_inp],
            base_commit="main",
            verify_script=self.verify_sh,
        )
        self.assertTrue(res.success, res.errors)
        self.assertEqual(res.status, "SUCCESS")

        events = self._read_events()
        names = [e["event"] for e in events]
        self.assertEqual(
            names,
            [
                "integration_start",
                "integration_merge",
                "integration_merge",
                "integration_reverify",
                "rc_tag",
            ],
        )

        self.assertEqual(events[0].get("job_id"), "job-stat4-ok")
        self.assertEqual(events[1].get("team"), "core")
        self.assertEqual(events[1].get("commit"), core_sha)
        self.assertEqual(events[2].get("team"), "ui")
        self.assertEqual(events[2].get("commit"), ui_sha)
        self.assertEqual(events[3].get("result"), "PASSED")
        self.assertEqual(events[3].get("exit_code"), 0)
        self.assertEqual(events[4].get("tag"), "rc/job-stat4-ok")
        self.assertEqual(events[4].get("commit"), res.release_candidate_commit)

    def test_integration_reverify_failure_records_reverify_and_failed(self) -> None:
        core_inp, _, core_ver = self._make_team_branch("core", "core.txt", "core\n")
        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.return_value = core_ver

        fail_sh = self.repo / "scripts" / "fail_verify.sh"
        fail_sh.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        fail_sh.chmod(0o755)
        _git(self.repo, "add", "scripts/fail_verify.sh")
        _git(self.repo, "commit", "-m", "chore: add failing verify script")

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler)
        res = gate.execute_integration(
            job_id="job-stat4-reverify-fail",
            inputs=[core_inp],
            base_commit="main",
            verify_script=fail_sh,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "VERIFICATION_FAILED")

        events = self._read_events()
        names = [e["event"] for e in events]
        self.assertEqual(
            names,
            [
                "integration_start",
                "integration_merge",
                "integration_reverify",
                "integration_failed",
            ],
        )
        self.assertEqual(events[2].get("result"), "FAILED")
        self.assertNotEqual(events[2].get("exit_code"), 0)
        self.assertTrue(events[3].get("reason"))

    def test_integration_conflict_records_integration_failed(self) -> None:
        core_inp, _, core_ver = self._make_team_branch("core", "shared.txt", "from core\n")
        ui_inp, _, ui_ver = self._make_team_branch("ui", "shared.txt", "from ui\n")

        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.side_effect = [core_ver, ui_ver]

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler)
        res = gate.execute_integration(
            job_id="job-stat4-conflict",
            inputs=[core_inp, ui_inp],
            base_commit="main",
            verify_script=self.verify_sh,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "CONFLICT")

        events = self._read_events()
        names = [e["event"] for e in events]
        self.assertEqual(
            names,
            ["integration_start", "integration_merge", "integration_failed"],
        )
        self.assertEqual(events[1].get("team"), "core")
        merged_teams = [e.get("team") for e in events if e["event"] == "integration_merge"]
        self.assertNotIn("ui", merged_teams)
        failed = [e for e in events if e["event"] == "integration_failed"]
        self.assertEqual(len(failed), 1)
        self.assertTrue(failed[0].get("reason"))

    def test_integration_audit_failed_records_integration_failed(self) -> None:
        core_inp, _, _ = self._make_team_branch("core", "core.txt", "core\n")
        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.return_value = BundleVerificationResult(
            is_valid=False,
            status="INVALID",
            job_id="job-stat4-audit-fail",
            errors=["tampered bundle"],
        )

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler)
        res = gate.execute_integration(
            job_id="job-stat4-audit-fail",
            inputs=[core_inp],
            base_commit="main",
            verify_script=self.verify_sh,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "AUDIT_FAILED")

        events = self._read_events()
        self.assertEqual([e["event"] for e in events], ["integration_start", "integration_failed"])
        self.assertEqual(events[1].get("job_id"), "job-stat4-audit-fail")
        self.assertTrue(events[1].get("reason"))

    def test_integration_branch_already_exists_records_integration_failed(self) -> None:
        core_inp, _, core_ver = self._make_team_branch("core", "core.txt", "core\n")
        _git(self.repo, "branch", "integration/job-stat4-dup-br", "main")

        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.return_value = core_ver

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler)
        res = gate.execute_integration(
            job_id="job-stat4-dup-br",
            inputs=[core_inp],
            base_commit="main",
            verify_script=self.verify_sh,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "BRANCH_ALREADY_EXISTS")

        events = self._read_events()
        self.assertEqual([e["event"] for e in events], ["integration_start", "integration_failed"])
        self.assertEqual(events[1].get("job_id"), "job-stat4-dup-br")
        self.assertIn("already exists", str(events[1].get("reason")))

    def test_integration_rc_tag_immutable_breach_records_integration_failed(self) -> None:
        core_inp, _, core_ver = self._make_team_branch("core", "core.txt", "core\n")
        _git(self.repo, "tag", "rc/job-stat4-rc-breach", "main")

        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.return_value = core_ver

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler)
        res = gate.execute_integration(
            job_id="job-stat4-rc-breach",
            inputs=[core_inp],
            base_commit="main",
            verify_script=self.verify_sh,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "RC_TAG_IMMUTABLE_BREACH")

        events = self._read_events()
        self.assertEqual(
            [e["event"] for e in events],
            [
                "integration_start",
                "integration_merge",
                "integration_reverify",
                "integration_failed",
            ],
        )
        self.assertEqual(events[3].get("job_id"), "job-stat4-rc-breach")
        self.assertIn("different commit", str(events[3].get("reason")))

    def test_integration_unexpected_exception_records_integration_failed(self) -> None:
        core_inp, _, core_ver = self._make_team_branch("core", "core.txt", "core\n")
        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.return_value = core_ver

        def _fault_hook(checkpoint: str, _ctx: dict[str, Any]) -> None:
            if checkpoint == "before_verify":
                raise RuntimeError("injected unexpected failure")

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler, fault_hook=_fault_hook)
        res = gate.execute_integration(
            job_id="job-stat4-exc",
            inputs=[core_inp],
            base_commit="main",
            verify_script=self.verify_sh,
        )
        self.assertFalse(res.success)
        self.assertEqual(res.status, "ERROR")

        events = self._read_events()
        self.assertEqual(
            [e["event"] for e in events],
            ["integration_start", "integration_merge", "integration_failed"],
        )
        self.assertEqual(events[2].get("job_id"), "job-stat4-exc")
        self.assertIn("injected unexpected failure", str(events[2].get("reason")))

    def test_integration_existing_matching_rc_tag_still_records_rc_tag(self) -> None:
        core_inp, _, core_ver = self._make_team_branch("core", "core.txt", "core\n")
        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.return_value = core_ver

        def _pre_tag(checkpoint: str, ctx: dict[str, Any]) -> None:
            if checkpoint == "before_verify":
                target_dir = Path(ctx["target_dir"])
                _git(target_dir, "tag", "rc/job-stat4-idempotent-rc", "HEAD")

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler, fault_hook=_pre_tag)
        res = gate.execute_integration(
            job_id="job-stat4-idempotent-rc",
            inputs=[core_inp],
            base_commit="main",
            verify_script=self.verify_sh,
        )
        self.assertTrue(res.success, res.errors)
        self.assertEqual(res.status, "SUCCESS")

        events = self._read_events()
        self.assertEqual(
            [e["event"] for e in events],
            [
                "integration_start",
                "integration_merge",
                "integration_reverify",
                "rc_tag",
            ],
        )
        self.assertEqual(events[3].get("tag"), "rc/job-stat4-idempotent-rc")
        self.assertEqual(events[3].get("commit"), res.release_candidate_commit)

    def test_integration_failed_not_duplicated_when_post_failure_cleanup_raises(self) -> None:
        core_inp, _, core_ver = self._make_team_branch("core", "shared.txt", "from core\n")
        ui_inp, _, ui_ver = self._make_team_branch("ui", "shared.txt", "from ui\n")

        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.side_effect = [core_ver, ui_ver]

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler)
        orig_remove = gate.worktree_manager.remove_worktree
        call_count = 0

        def _failing_first_remove(*args: Any, **kwargs: Any) -> Any:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ValueError("cleanup boom")
            return orig_remove(*args, **kwargs)

        with patch.object(gate.worktree_manager, "remove_worktree", side_effect=_failing_first_remove):
            res = gate.execute_integration(
                job_id="job-stat4-no-dup-fail",
                inputs=[core_inp, ui_inp],
                base_commit="main",
                verify_script=self.verify_sh,
            )

        self.assertFalse(res.success)
        self.assertEqual(res.status, "ERROR")

        events = self._read_events()
        failed_events = [e for e in events if e["event"] == "integration_failed"]
        self.assertEqual(len(failed_events), 1, f"Duplicate integration_failed: {events}")
        self.assertEqual(
            [e["event"] for e in events],
            ["integration_start", "integration_merge", "integration_failed"],
        )

    def test_integration_inv8_write_failure_does_not_change_result(self) -> None:
        core_inp, _, core_ver = self._make_team_branch("core", "core.txt", "core\n")

        # 1. events.jsonl をディレクトリで塞ぐ
        events_path = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        events_path.mkdir(parents=True, exist_ok=True)

        mock_bundler = MagicMock(spec=EvidenceBundler)
        mock_bundler.verify_bundle.return_value = core_ver

        gate = IntegrationGate(repo_dir=self.repo, bundler=mock_bundler)
        res = gate.execute_integration(
            job_id="job-stat4-inv8-dir",
            inputs=[core_inp],
            base_commit="main",
            verify_script=self.verify_sh,
        )
        self.assertTrue(res.success, res.errors)
        self.assertEqual(res.status, "SUCCESS")

        # 2. record_event 自体が例外を投げても結果が変わらない
        with patch("auto_developer.metrics.MetricsLog.record_event", side_effect=RuntimeError("disk full")):
            res2 = gate.execute_integration(
                job_id="job-stat4-inv8-exc",
                inputs=[core_inp],
                base_commit="main",
                verify_script=self.verify_sh,
            )
        self.assertTrue(res2.success, res2.errors)
        self.assertEqual(res2.status, "SUCCESS")


if __name__ == "__main__":
    unittest.main()
