"""テスト: MetricsLog とイベント記録（SPEC.md 機能XXX.3）。"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.metrics import MetricsLog

ORCH = Path(__file__).resolve().parents[1] / "scripts" / "nightly_orchestrator.py"


class TestMetricsLog(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        for cmd in (
            ["git", "init"],
            ["git", "config", "user.name", "Tester"],
            ["git", "config", "user.email", "tester@example.com"],
        ):
            subprocess.run(cmd, cwd=self.repo, check=True, capture_output=True)
        self.plan = self.repo / "PLAN.md"
        self.plan.write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 64: Outcome Metrics\n\n## ステップ\n- [ ] 1. タスク\n",
            encoding="utf-8",
        )
        (self.repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
        self.verify = self.repo / "verify.sh"
        self.verify.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.verify.chmod(0o755)
        self.failing_verify = self.repo / "fail_verify.sh"
        self.failing_verify.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        self.failing_verify.chmod(0o755)

        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)
        self.checkpoint = self._head()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _orch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ORCH), "--plan", str(self.plan), *args],
            cwd=self.repo, capture_output=True, text=True, check=False, timeout=120,
        )

    def test_metrics_log_records_common_fields(self) -> None:
        metrics = MetricsLog(repo_dir=self.repo)
        ok = metrics.record_event("test_event", foo="bar")
        self.assertTrue(ok)

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        self.assertTrue(events_file.exists())
        lines = events_file.read_text(encoding="utf-8").strip().splitlines()
        self.assertEqual(len(lines), 1)
        data = json.loads(lines[0])
        self.assertEqual(data["schema"], 1)
        self.assertEqual(data["event"], "test_event")
        self.assertEqual(data["phase"], "Phase 64")
        self.assertEqual(data["head"], self.checkpoint)
        self.assertEqual(data["foo"], "bar")

    def test_phase_extraction_strict_to_goal_section(self) -> None:
        metrics = MetricsLog(repo_dir=self.repo)
        self.assertEqual(metrics.get_current_phase(), "Phase 64")

        # 「## 現在の目標」節がない場合は unknown（他節の Phase は拾わない）
        self.plan.write_text("# PLAN\n\n## 過去の履歴\n- Phase 50\n\n## ステップ\n- [ ] 1. タスク\n", encoding="utf-8")
        self.assertEqual(metrics.get_current_phase(), "unknown")

    def test_git_as_file_worktree_fallback_and_verify_commit_cycle(self) -> None:
        """worktree（.gitがファイル）で.gitignoreに.dev/がなくても除外され、ツリー汚染なくコミット完遂すること。"""
        wt_repo = Path(self._tmp.name) / "wt"
        # .gitignore には .dev/ を入れない（exclude による除外保証を検証）
        subprocess.run(["git", "worktree", "add", "-b", "wt-branch", str(wt_repo)], cwd=self.repo, check=True, capture_output=True)
        wt_plan = wt_repo / "PLAN.md"
        wt_plan.write_text("# PLAN\n\n## 現在の目標\n- Phase 64: Metrics\n\n## ステップ\n- [ ] 1. wtタスク\n", encoding="utf-8")
        wt_verify = wt_repo / "verify.sh"
        wt_verify.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        wt_verify.chmod(0o755)
        subprocess.run(["git", "add", "PLAN.md", "verify.sh"], cwd=wt_repo, check=True)
        subprocess.run(["git", "commit", "-m", "setup wt"], cwd=wt_repo, check=True, capture_output=True)
        wt_checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_repo, capture_output=True, text=True, check=True).stdout.strip()

        # 変更作成
        (wt_repo / "feature.py").write_text("print('wt')\n", encoding="utf-8")

        # verify-step 実行（.dev/metrics/events.jsonl に記録される）
        v_res = subprocess.run(
            [sys.executable, str(ORCH), "--plan", str(wt_plan), "--verify-script", str(wt_verify),
             "verify-step", "--checkpoint", wt_checkpoint, "--task-id", "1"],
            cwd=wt_repo, capture_output=True, text=True, check=False,
        )
        self.assertEqual(v_res.returncode, 0, f"verify-step in worktree failed: {v_res.stderr}")

        # commit-step 実行（.dev/metrics/events.jsonl の存在がツリーSHAを狂わせず、正常にコミットできること）
        c_res = subprocess.run(
            [sys.executable, str(ORCH), "--plan", str(wt_plan),
             "commit-step", "--task-id", "1", "--checkpoint", wt_checkpoint,
             "--maker-model", "gemini-3.8-flash",
             "--checker-model", "claude-opus-5-5-medium",
             "--checker-decision", "APPROVE",
             "--checker-reason", "wt test"],
            cwd=wt_repo, capture_output=True, text=True, check=False,
        )
        self.assertEqual(c_res.returncode, 0, f"commit-step in worktree failed: {c_res.stderr}")
        self.assertIn("COMMITTED", c_res.stdout)

        # コミットされたツリーに events.jsonl や .dev/ が含まれていないこと
        ls_tree = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", "HEAD"],
            cwd=wt_repo, capture_output=True, text=True, check=True
        ).stdout.splitlines()
        self.assertNotIn(".dev/metrics/events.jsonl", ls_tree)
        self.assertFalse(any(path.startswith(".dev") for path in ls_tree))

        # イベントファイル自体は .dev/metrics/events.jsonl に正しく追記されていること
        events_file = wt_repo / ".dev" / "metrics" / "events.jsonl"
        self.assertTrue(events_file.exists())
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        event_names = [e["event"] for e in lines]
        self.assertIn("verify_step", event_names)
        self.assertIn("commit_step", event_names)

    def test_verify_step_and_commit_step_record_events(self) -> None:
        (self.repo / "app.py").write_text("def run(): pass\n", encoding="utf-8")
        v_res = self._orch(
            "--verify-script", str(self.verify),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1"
        )
        self.assertEqual(v_res.returncode, 0)

        c_res = self._orch(
            "commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "APPROVE",
            "--checker-reason", "looks good",
        )
        self.assertEqual(c_res.returncode, 0)

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        self.assertTrue(events_file.exists())
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        v_events = [e for e in lines if e["event"] == "verify_step"]
        c_events = [e for e in lines if e["event"] == "commit_step"]
        self.assertEqual(len(v_events), 1)
        self.assertEqual(v_events[0]["result"], "PASSED")
        self.assertEqual(v_events[0]["attempt"], 1)

        self.assertEqual(len(c_events), 1)
        self.assertEqual(c_events[0]["result"], "COMMITTED")
        self.assertEqual(c_events[0]["checker_decision"], "APPROVE")
        self.assertEqual(c_events[0]["checker_family"], "claude")
        self.assertEqual(c_events[0]["maker_family"], "gemini")

    def test_verify_step_failed_and_attempt_increment(self) -> None:
        (self.repo / "app.py").write_text("broken\n", encoding="utf-8")
        # 1回目の失敗
        v1 = self._orch(
            "--verify-script", str(self.failing_verify),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1"
        )
        self.assertNotEqual(v1.returncode, 0)

        # 2回目の失敗
        v2 = self._orch(
            "--verify-script", str(self.failing_verify),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1"
        )
        self.assertNotEqual(v2.returncode, 0)

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        v_events = [e for e in lines if e["event"] == "verify_step"]
        self.assertEqual(len(v_events), 2)
        self.assertEqual(v_events[0]["result"], "FAILED")
        self.assertEqual(v_events[0]["attempt"], 1)
        self.assertEqual(v_events[1]["result"], "FAILED")
        self.assertEqual(v_events[1]["attempt"], 2)

    def test_verify_step_circuit_breaker(self) -> None:
        # 保護ファイルを改変して verify-step を呼ぶ
        (self.repo / "SPEC.md").write_text("# HACK\n", encoding="utf-8")
        res = self._orch(
            "--verify-script", str(self.verify),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1"
        )
        self.assertEqual(res.returncode, 10)
        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        cb_events = [e for e in lines if e["event"] == "verify_step" and e["result"] == "CIRCUIT_BREAKER"]
        self.assertEqual(len(cb_events), 1)
        self.assertEqual(cb_events[0]["exit_code"], 10)

    def test_commit_step_policy_violation_and_evidence_error_events(self) -> None:
        (self.repo / "app.py").write_text("ok\n", encoding="utf-8")
        self._orch("--verify-script", str(self.verify), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")

        # 1. 同一系統モデル違反 (CHECKER_POLICY_VIOLATION)
        res_viol = self._orch(
            "commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "gemini-2.5-pro",
            "--checker-decision", "APPROVE",
        )
        self.assertEqual(res_viol.returncode, 4)

        # 2. 証跡のないタスク2をコミット (EVIDENCE_ERROR)
        self.plan.write_text("# PLAN\n## 現在の目標\nPhase 64\n## ステップ\n- [ ] 1. t1\n- [ ] 2. t2\n", encoding="utf-8")
        res_ev = self._orch(
            "commit-step", "--task-id", "2", "--checkpoint", self.checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "APPROVE",
        )
        self.assertEqual(res_ev.returncode, 3)

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        c_events = [e for e in lines if e["event"] == "commit_step"]
        results = [e["result"] for e in c_events]
        self.assertIn("CHECKER_POLICY_VIOLATION", results)
        self.assertIn("EVIDENCE_ERROR", results)

    def test_rollback_step_event(self) -> None:
        (self.repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
        res = self._orch(
            "rollback-step", "--checkpoint", self.checkpoint, "--task-id", "1", "--reason", "Test failure"
        )
        self.assertEqual(res.returncode, 0)
        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        rb_events = [e for e in lines if e["event"] == "rollback_step"]
        self.assertEqual(len(rb_events), 1)
        self.assertEqual(rb_events[0]["reason"], "Test failure")
        self.assertIsNotNone(rb_events[0]["quarantine_branch"])

    def test_record_review_subcommand_case_insensitive_and_validation(self) -> None:
        v_res = self._orch(
            "--verify-script", str(self.verify),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1",
        )
        self.assertEqual(v_res.returncode, 0, v_res.stderr)
        io_dir = Path(self._tmp.name) / "checker_io"
        self.assertEqual(
            self._orch("review-input", "--task-id", "1", "--out-dir", str(io_dir)).returncode,
            0,
        )
        diff_file = io_dir / "diff.patch"
        spec_file = io_dir / "spec.md"
        spec_file.write_text("# SPEC\n", encoding="utf-8")
        out_rej = io_dir / "review_rej.txt"
        out_rej.write_text("Missing edge case\nDECISION: REJECT\n", encoding="utf-8")

        # 大文字小文字を受け付けて正規化して記録すること
        res = self._orch(
            "--spec", str(spec_file),
            "record-review", "--task-id", "1",
            "--checker-output", str(out_rej),
            "--diff-file", str(diff_file),
            "--spec-file", str(spec_file),
            "--decision", "Reject",
            "--checker-model", "claude-opus-5-5-medium",
            "--maker-model", "gemini-3.8-flash",
            "--reason", "Missing edge case",
        )
        self.assertEqual(res.returncode, 0, res.stderr)
        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        r_events = [e for e in lines if e["event"] == "checker_review"]
        self.assertEqual(len(r_events), 1)
        self.assertEqual(r_events[0]["decision"], "REJECT")
        self.assertEqual(r_events[0]["checker_family"], "claude")
        self.assertEqual(r_events[0]["maker_family"], "gemini")

        # 判定行のない出力ファイルは拒絶されること
        out_invalid = io_dir / "review_invalid.txt"
        out_invalid.write_text("MAYBE\n", encoding="utf-8")
        res_invalid = self._orch(
            "--spec", str(spec_file),
            "record-review", "--task-id", "1",
            "--checker-output", str(out_invalid),
            "--diff-file", str(diff_file),
            "--spec-file", str(spec_file),
            "--checker-model", "claude-opus-5-5-medium",
            "--maker-model", "gemini-3.8-flash",
        )
        self.assertEqual(res_invalid.returncode, 4)

    def test_metrics_non_interference_when_write_fails_across_all_gates(self) -> None:
        """書き込み先がディレクトリで塞がれていても、全ゲートの動作・終了コードが一切変わらないこと。"""
        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        events_file.mkdir(parents=True)

        # 1. verify-step PASSED
        (self.repo / "app.py").write_text("code\n", encoding="utf-8")
        v_res = self._orch(
            "--verify-script", str(self.verify),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1"
        )
        self.assertEqual(v_res.returncode, 0)
        self.assertIn("METRICS_WARNING", v_res.stderr)
        self.assertIn("VERIFICATION PASSED", v_res.stdout)

        # 2. commit-step POLICY_VIOLATION (exit 4, Evidence未消費, HEAD不変)
        viol_res = self._orch(
            "commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "gemini-2.5-pro",
            "--checker-decision", "APPROVE",
            "--checker-reason", "bad model",
        )
        self.assertEqual(viol_res.returncode, 4)
        self.assertIn("METRICS_WARNING", viol_res.stderr)
        self.assertEqual(self._head(), self.checkpoint)

        # 3. commit-step COMMITTED (exit 0, Evidence消費, HEAD移動)
        c_res = self._orch(
            "commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "APPROVE",
            "--checker-reason", "ok",
        )
        self.assertEqual(c_res.returncode, 0)
        self.assertIn("METRICS_WARNING", c_res.stderr)
        self.assertIn("COMMITTED", c_res.stdout)
        new_head = self._head()
        self.assertNotEqual(new_head, self.checkpoint)

        # 4. commit-step EVIDENCE_ERROR (一度消費された証跡でのリプレイ)
        replay_res = self._orch(
            "commit-step", "--task-id", "1", "--checkpoint", self.checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "APPROVE",
        )
        self.assertEqual(replay_res.returncode, 3)
        self.assertIn("METRICS_WARNING", replay_res.stderr)

        # 5. rollback-step
        rb_res = self._orch(
            "rollback-step", "--checkpoint", new_head, "--task-id", "1", "--reason", "fail"
        )
        self.assertEqual(rb_res.returncode, 0)
        self.assertIn("METRICS_WARNING", rb_res.stderr)

        # 6. record-review
        v2_res = self._orch(
            "--verify-script", str(self.verify),
            "verify-step", "--checkpoint", new_head, "--task-id", "1",
        )
        self.assertEqual(v2_res.returncode, 0, v2_res.stderr)
        io_dir = Path(self._tmp.name) / "checker_io_6"
        self.assertEqual(
            self._orch("review-input", "--task-id", "1", "--out-dir", str(io_dir)).returncode,
            0,
        )
        spec_file = io_dir / "spec.md"
        spec_file.write_text("# SPEC\n", encoding="utf-8")
        out_ok = io_dir / "review_ok.txt"
        out_ok.write_text("OK\nDECISION: APPROVE\n", encoding="utf-8")
        rr_res = self._orch(
            "--spec", str(spec_file),
            "record-review", "--task-id", "1",
            "--checker-output", str(out_ok),
            "--diff-file", str(io_dir / "diff.patch"),
            "--spec-file", str(spec_file),
            "--decision", "APPROVE",
            "--checker-model", "claude-opus-5-5-medium",
            "--maker-model", "gemini-3.8-flash",
        )
        self.assertEqual(rr_res.returncode, 0, rr_res.stderr)
        self.assertIn("METRICS_WARNING", rr_res.stderr)

    def test_audit_stage_event_recorded_on_success(self) -> None:
        """run_audit_stage 成功時に audit_stage イベントが全属性揃って記録されること。"""
        from unittest.mock import MagicMock

        from auto_developer.audit import AuditFinding
        from auto_developer.orchestrator import NightlyOrchestrator

        orch = NightlyOrchestrator(repo_dir=self.repo, plan_path=self.plan)
        mock_auditor = MagicMock()

        f_p0 = AuditFinding(finding_id="AUD-001", description="p0 bug", severity="P0", location="foo.py:1", spec_reference="XXX.1", reproduction_test_code="def test_p0(): pass")
        f_p1 = AuditFinding(finding_id="AUD-002", description="p1 bug", severity="P1", location="bar.py:1", spec_reference="XXX.1", reproduction_test_code="def test_p1(): pass")
        f_p2 = AuditFinding(finding_id="AUD-003", description="p2 note", severity="P2", location="baz.py:1", spec_reference="XXX.1", reproduction_test_code="def test_p2(): pass")
        f_dup = AuditFinding(finding_id="AUD-004", description="dup", severity="P1", location="dup.py:1", spec_reference="XXX.1", reproduction_test_code="def test_dup(): pass")
        f_rej = AuditFinding(finding_id="AUD-005", description="rej", severity="INFO", location="rej.py:1", spec_reference="XXX.1", reproduction_test_code="def test_rej(): pass")

        mock_auditor.audit_repository.return_value = [f_p0, f_p1, f_p2, f_dup, f_rej]
        mock_auditor.screen_and_dispatch_findings.return_value = {
            "dispatched": [f_p0, f_p1],
            "rejected": [(f_rej, "Adversarial finding")],
            "backlogged": [f_p2],
            "escalated": [],
            "duplicates": [f_dup],
            "total_processed": 5,
            "genuine_count": 2,
            "rejected_count": 1,
            "duplicate_count": 1,
        }
        mock_auditor.last_run_metadata = {
            "mode": "LIVE",
            "usage": {
                "input_tokens": 15000,
                "output_tokens": 1200,
            },
        }

        res = orch.run_audit_stage(
            baseline_commit=self.checkpoint,
            target_commit="HEAD",
            auditor_runner=mock_auditor,
        )
        self.assertEqual(res["status"], "SUCCESS")

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        self.assertTrue(events_file.exists())
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        audit_events = [e for e in lines if e.get("event") == "audit_stage"]
        self.assertEqual(len(audit_events), 1)

        ae = audit_events[0]
        self.assertEqual(ae["schema"], 1)
        self.assertEqual(ae["phase"], "Phase 64")
        self.assertEqual(ae["status"], "SUCCESS")
        self.assertEqual(ae["findings_by_severity"], {"P0": 1, "P1": 2, "P2": 1, "INFO": 1})
        self.assertEqual(ae["dispatched"], 2)
        self.assertEqual(ae["backlogged"], 1)
        self.assertEqual(ae["escalated"], 0)
        self.assertEqual(ae["duplicate"], 1)
        self.assertEqual(ae["rejected"], 1)
        self.assertEqual(ae["input_tokens"], 15000)
        self.assertEqual(ae["output_tokens"], 1200)

    def test_audit_stage_event_recorded_on_failure(self) -> None:
        """run_audit_stage 失敗時にも status (AUDIT_PENDING / AUDIT_FAILED) のイベントが記録されること。"""
        from unittest.mock import MagicMock

        from auto_developer.orchestrator import NightlyOrchestrator

        orch = NightlyOrchestrator(repo_dir=self.repo, plan_path=self.plan)
        mock_auditor = MagicMock()
        mock_auditor.audit_repository.side_effect = RuntimeError("NO_INDEPENDENT_AUDITOR_AVAILABLE: timeout")

        res = orch.run_audit_stage(
            baseline_commit=self.checkpoint,
            target_commit="HEAD",
            auditor_runner=mock_auditor,
        )
        self.assertEqual(res["status"], "AUDIT_PENDING")

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        self.assertTrue(events_file.exists())
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        audit_events = [e for e in lines if e.get("event") == "audit_stage"]
        self.assertEqual(len(audit_events), 1)

        ae = audit_events[0]
        self.assertEqual(ae["status"], "AUDIT_PENDING")
        self.assertEqual(ae["findings_by_severity"], {})
        self.assertEqual(ae["dispatched"], 0)
        self.assertEqual(ae["backlogged"], 0)
        self.assertEqual(ae["escalated"], 0)
        self.assertEqual(ae["duplicate"], 0)
        self.assertEqual(ae["rejected"], 0)
        self.assertIsNone(ae["input_tokens"])
        self.assertIsNone(ae["output_tokens"])

    def test_audit_stage_non_interference_when_write_fails(self) -> None:
        """events.jsonl の書き込みが失敗しても run_audit_stage は中断せず METRICS_WARNING を出力すること。"""
        import io
        from unittest.mock import MagicMock

        from auto_developer.orchestrator import NightlyOrchestrator

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        events_file.mkdir(parents=True)

        orch = NightlyOrchestrator(repo_dir=self.repo, plan_path=self.plan)
        mock_auditor = MagicMock()
        mock_auditor.audit_repository.return_value = []
        mock_auditor.screen_and_dispatch_findings.return_value = {
            "dispatched": [],
            "rejected": [],
            "backlogged": [],
            "escalated": [],
            "duplicates": [],
            "total_processed": 0,
            "genuine_count": 0,
            "rejected_count": 0,
            "duplicate_count": 0,
        }
        mock_auditor.last_run_metadata = {"mode": "LIVE"}

        captured_stderr = io.StringIO()
        orig_stderr = sys.stderr
        try:
            sys.stderr = captured_stderr
            res = orch.run_audit_stage(
                baseline_commit=self.checkpoint,
                target_commit="HEAD",
                auditor_runner=mock_auditor,
            )
        finally:
            sys.stderr = orig_stderr

        self.assertEqual(res["status"], "SUCCESS")
        self.assertIn("METRICS_WARNING", captured_stderr.getvalue())

    def test_audit_stage_event_recorded_on_skipped(self) -> None:
        """監査役が設定されていない場合 (SKIPPED) に audit_stage イベントが1件記録されること。"""
        from auto_developer.orchestrator import NightlyOrchestrator

        orch = NightlyOrchestrator(repo_dir=self.repo, plan_path=self.plan)
        orch.auditor_runner = None
        orch.auditor_model = ""
        orch.auditor_cmd_template = ""

        res = orch.run_audit_stage(
            baseline_commit=self.checkpoint,
            target_commit="HEAD",
            auditor_runner=None,
        )
        self.assertEqual(res["status"], "SKIPPED")

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        self.assertTrue(events_file.exists())
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        audit_events = [e for e in lines if e.get("event") == "audit_stage"]
        self.assertEqual(len(audit_events), 1)

        ae = audit_events[0]
        self.assertEqual(ae["status"], "SKIPPED")
        self.assertEqual(ae["findings_by_severity"], {})
        self.assertEqual(ae["dispatched"], 0)
        self.assertEqual(ae["backlogged"], 0)
        self.assertEqual(ae["escalated"], 0)
        self.assertEqual(ae["duplicate"], 0)
        self.assertEqual(ae["rejected"], 0)
        self.assertIsNone(ae["input_tokens"])
        self.assertIsNone(ae["output_tokens"])

    def test_audit_stage_non_interference_on_skip_and_failure_paths(self) -> None:
        """skip 経路および failure 経路で events.jsonl が書けなくても処理が中断せず warning が出ること。"""
        import io
        from unittest.mock import MagicMock

        from auto_developer.orchestrator import NightlyOrchestrator

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        events_file.mkdir(parents=True)

        orch = NightlyOrchestrator(repo_dir=self.repo, plan_path=self.plan)
        orch.auditor_runner = None
        orch.auditor_model = ""
        orch.auditor_cmd_template = ""

        # 1. Skip path
        captured_stderr = io.StringIO()
        orig_stderr = sys.stderr
        try:
            sys.stderr = captured_stderr
            res_skip = orch.run_audit_stage(
                baseline_commit=self.checkpoint,
                target_commit="HEAD",
                auditor_runner=None,
            )
        finally:
            sys.stderr = orig_stderr
        self.assertEqual(res_skip["status"], "SKIPPED")
        self.assertIn("METRICS_WARNING", captured_stderr.getvalue())

        # 2. Failure path
        mock_auditor = MagicMock()
        mock_auditor.audit_repository.side_effect = RuntimeError("Audit failed")
        captured_stderr = io.StringIO()
        try:
            sys.stderr = captured_stderr
            res_fail = orch.run_audit_stage(
                baseline_commit=self.checkpoint,
                target_commit="HEAD",
                auditor_runner=mock_auditor,
            )
        finally:
            sys.stderr = orig_stderr
        self.assertEqual(res_fail["status"], "AUDIT_FAILED")
        self.assertIn("METRICS_WARNING", captured_stderr.getvalue())

    def test_audit_stage_exactly_one_event_when_step_after_screening_raises(self) -> None:
        """screening 後の処理で例外が発生した場合、SUCCESS は記録されず AUDIT_FAILED の1件のみ記録されること。"""
        from unittest.mock import MagicMock

        from auto_developer.orchestrator import NightlyOrchestrator

        orch = NightlyOrchestrator(repo_dir=self.repo, plan_path=self.plan)
        mock_auditor = MagicMock()
        mock_auditor.audit_repository.return_value = []
        # screen_res["dispatched"] が無効な型などで後続が TypeError を起こすケースを模倣
        mock_auditor.screen_and_dispatch_findings.return_value = {
            "dispatched": None,  # len(None) will raise TypeError in main try block
            "rejected": [],
            "backlogged": [],
            "escalated": [],
            "duplicates": [],
            "total_processed": 0,
            "genuine_count": 0,
            "rejected_count": 0,
            "duplicate_count": 0,
        }
        mock_auditor.last_run_metadata = {"mode": "LIVE"}

        res = orch.run_audit_stage(
            baseline_commit=self.checkpoint,
            target_commit="HEAD",
            auditor_runner=mock_auditor,
        )
        self.assertEqual(res["status"], "AUDIT_FAILED")

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        self.assertTrue(events_file.exists())
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        audit_events = [e for e in lines if e.get("event") == "audit_stage"]
        # 重複せず、かつ SUCCESS ではなく AUDIT_FAILED の 1 件のみであること
        self.assertEqual(len(audit_events), 1)
        self.assertEqual(audit_events[0]["status"], "AUDIT_FAILED")

    def test_audit_stage_handles_zero_tokens(self) -> None:
        """トークン数が 0 の場合にも None にフォールバックせず 0 として記録されること。"""
        from unittest.mock import MagicMock

        from auto_developer.orchestrator import NightlyOrchestrator

        orch = NightlyOrchestrator(repo_dir=self.repo, plan_path=self.plan)
        mock_auditor = MagicMock()
        mock_auditor.audit_repository.return_value = []
        mock_auditor.screen_and_dispatch_findings.return_value = {
            "dispatched": [],
            "rejected": [],
            "backlogged": [],
            "escalated": [],
            "duplicates": [],
            "total_processed": 0,
            "genuine_count": 0,
            "rejected_count": 0,
            "duplicate_count": 0,
        }
        mock_auditor.last_run_metadata = {
            "mode": "LIVE",
            "usage": {
                "input_tokens": 0,
                "output_tokens": 0,
            },
        }

        res = orch.run_audit_stage(
            baseline_commit=self.checkpoint,
            target_commit="HEAD",
            auditor_runner=mock_auditor,
        )
        self.assertEqual(res["status"], "SUCCESS")

        events_file = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        lines = [json.loads(line) for line in events_file.read_text(encoding="utf-8").strip().splitlines()]
        audit_events = [e for e in lines if e.get("event") == "audit_stage"]
        self.assertEqual(len(audit_events), 1)
        self.assertEqual(audit_events[0]["input_tokens"], 0)
        self.assertEqual(audit_events[0]["output_tokens"], 0)

    def test_compute_phase_metrics_from_events(self) -> None:
        """イベントログからフェーズ指標と直前フェーズとの差分が正しく集計されること (XXX.4)。"""
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)

        # Phase 63 イベント (比較用)
        metrics.record_event("verify_step", phase="Phase 63", task_id="1", checkpoint="c63", result="PASSED", exit_code=0, duration_sec=10.0, attempt=1)
        metrics.record_event("commit_step", phase="Phase 63", task_id="1", result="COMMITTED", commit="sha63_1", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")
        metrics.record_event("audit_stage", phase="Phase 63", status="SUCCESS", findings_by_severity={"P1": 1}, dispatched=1, backlogged=0, escalated=0, duplicate=0, rejected=0, input_tokens=10000, output_tokens=1000)

        # Phase 64 イベント
        # Task 1: 1回目で合格してコミット
        metrics.record_event("verify_step", phase="Phase 64", task_id="1", checkpoint="c64", result="PASSED", exit_code=0, duration_sec=5.0, attempt=1)
        metrics.record_event("commit_step", phase="Phase 64", task_id="1", result="COMMITTED", commit="sha64_1", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")
        # Task 2: 1回目失敗 (FAILED)、2回目合格 (PASSED) してコミット
        metrics.record_event("verify_step", phase="Phase 64", task_id="2", checkpoint="c64", result="FAILED", exit_code=1, duration_sec=4.0, attempt=1)
        metrics.record_event("verify_step", phase="Phase 64", task_id="2", checkpoint="c64", result="PASSED", exit_code=0, duration_sec=6.0, attempt=2)
        metrics.record_event("commit_step", phase="Phase 64", task_id="2", result="COMMITTED", commit="sha64_2", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")
        # その他イベント: サーキットブレーカー、ロールバック (隔離)、Checker ポリシー違反、Checker 査読 REJECT
        metrics.record_event("verify_step", phase="Phase 64", task_id="3", checkpoint="c64", result="CIRCUIT_BREAKER", exit_code=10, duration_sec=1.0, attempt=1)
        metrics.record_event("rollback_step", phase="Phase 64", task_id="3", checkpoint="c64", quarantine_branch="quarantine/3", reason="circuit breaker")
        metrics.record_event("commit_step", phase="Phase 64", task_id="3", result="CHECKER_POLICY_VIOLATION", commit="unknown", checker_decision="APPROVE", checker_model="gemini-2.5-pro", checker_family="gemini", maker_model="gemini-3.8-flash", maker_family="gemini")
        metrics.record_event("checker_review", phase="Phase 64", task_id="3", decision="REJECT", checker_model="claude-opus-5.5", maker_model="gemini-3.8-flash")
        # 監査ステージ: 2件 P0/P1 (dispatched=1, escalated=1), 200,000 tokens
        metrics.record_event(
            "audit_stage", phase="Phase 64", status="SUCCESS",
            findings_by_severity={"P0": 1, "P1": 1, "P2": 1},
            dispatched=1, backlogged=1, escalated=1, duplicate=1, rejected=1,
            input_tokens=150000, output_tokens=50000,
        )

        res = metrics.compute_phase_metrics("Phase 64")
        self.assertEqual(res["phase"], "Phase 64")
        m = res["metrics"]

        # tasks_committed: 2 tasks (Task 1 and Task 2)
        self.assertEqual(m["tasks_committed"]["value"], 2)
        self.assertEqual(m["tasks_committed"]["source"], "event")

        # first_pass_rate: 1 of 2 committed passed on attempt 1 -> 0.5 (50%)
        self.assertEqual(m["first_pass_rate"]["value"], 0.5)
        self.assertEqual(m["first_pass_rate"]["source"], "event")

        # mean_verify_attempts: Task 1 had 1 attempt, Task 2 had 2 attempts -> mean = 1.5
        self.assertEqual(m["mean_verify_attempts"]["value"], 1.5)
        self.assertEqual(m["mean_verify_attempts"]["source"], "event")

        # verify_failures: 1
        self.assertEqual(m["verify_failures"]["value"], 1)

        # circuit_breakers: 1
        self.assertEqual(m["circuit_breakers"]["value"], 1)

        # quarantines: 1
        self.assertEqual(m["quarantines"]["value"], 1)

        # checker_rejects: 1
        self.assertEqual(m["checker_rejects"]["value"], 1)

        # checker_policy_violations: 1
        self.assertEqual(m["checker_policy_violations"]["value"], 1)

        # 監査集計
        self.assertEqual(m["audit_dispatched"]["value"], 1)
        self.assertEqual(m["audit_backlogged"]["value"], 1)
        self.assertEqual(m["audit_escalated"]["value"], 1)
        self.assertEqual(m["audit_duplicate"]["value"], 1)
        self.assertEqual(m["audit_rejected"]["value"], 1)

        # genuine_p0p1_per_100k_tokens: (1 + 1) / 200,000 * 100,000 = 1.0
        self.assertEqual(m["genuine_p0p1_per_100k_tokens"]["value"], 1.0)
        self.assertEqual(m["genuine_p0p1_per_100k_tokens"]["source"], "event")

        # git 由来の項目 (source: "git")
        self.assertEqual(m["planned_steps"]["value"], 1)
        self.assertEqual(m["planned_steps"]["source"], "git")

        # 直前フェーズ (Phase 63) との差分
        delta = res.get("delta")
        self.assertIsNotNone(delta)
        # tasks_committed in Phase 64 was 2, Phase 63 was 1 -> delta = 1
        self.assertEqual(delta["tasks_committed"], 1)
        # first_pass_rate: 0.5 - 1.0 = -0.5
        self.assertEqual(delta["first_pass_rate"], -0.5)

    def test_cli_metrics_subcommand_json_and_text(self) -> None:
        """auto-dev metrics サブコマンドの --json 出力およびテキスト出力が機能すること。"""
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        metrics.record_event("commit_step", phase="Phase 64", task_id="1", result="COMMITTED", commit="abc", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")

        # 1. --json mode
        res_json = self._orch("metrics", "--phase", "64", "--json")
        self.assertEqual(res_json.returncode, 0)
        data = json.loads(res_json.stdout)
        self.assertEqual(data["phase"], "Phase 64")
        self.assertEqual(data["metrics"]["tasks_committed"]["value"], 1)
        self.assertEqual(data["metrics"]["tasks_committed"]["source"], "event")

        # 2. Text mode
        res_text = self._orch("metrics", "--phase", "64")
        self.assertEqual(res_text.returncode, 0)
        self.assertIn("Phase 64", res_text.stdout)
        self.assertIn("tasks_committed", res_text.stdout)

    def test_phase_with_no_events_reports_null_not_zero(self) -> None:
        """イベントが1件も存在しないフェーズでは、0ではなく null (None) が返ること (推定値で埋めない)。"""
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        res = metrics.compute_phase_metrics("Phase 50")
        self.assertEqual(res["phase"], "Phase 50")
        for k, info in res["metrics"].items():
            self.assertIsNone(info["value"], f"Metric '{k}' should be None when phase has no events")
            if k in ("planned_steps", "unplanned_steps", "audit_steps_added", "pipeline_commits", "out_of_path_commits", "lead_time_hours"):
                self.assertEqual(info["source"], "git")
            else:
                self.assertEqual(info["source"], "event")
        # delta もすべて None
        for k, d_val in res["delta"].items():
            self.assertIsNone(d_val, f"Delta for '{k}' should be None when phase has no events")

    def test_phase_with_missing_verify_events_reports_null_attempts(self) -> None:
        """コミットされたタスクに verify_step イベントが欠落している場合、推定値を使わず null を返すこと。"""
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        # コミットはあるが verify_step イベントは存在しない
        metrics.record_event("commit_step", phase="Phase 64", task_id="1", result="COMMITTED", commit="sha1", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")
        res = metrics.compute_phase_metrics("Phase 64")
        m = res["metrics"]
        self.assertEqual(m["tasks_committed"]["value"], 1)
        self.assertIsNone(m["first_pass_rate"]["value"])
        self.assertIsNone(m["mean_verify_attempts"]["value"])
        self.assertIsNone(m["rework_tasks_ratio"]["value"])

    def test_rework_tasks_ratio_computation(self) -> None:
        """コミットされたタスクのうち verify_step が 2回以上行われたタスクの割合 (rework_tasks_ratio) が正しく算出されること。"""
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        # Task 1: 1回の verify でコミット
        metrics.record_event("verify_step", phase="Phase 65", task_id="1", checkpoint="c1", result="PASSED", exit_code=0, duration_sec=1.0)
        metrics.record_event("commit_step", phase="Phase 65", task_id="1", result="COMMITTED", commit="sha1", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")

        # Task 2: 2回の verify でコミット (rework あり)
        metrics.record_event("verify_step", phase="Phase 65", task_id="2", checkpoint="c2", result="FAILED", exit_code=1, duration_sec=1.0)
        metrics.record_event("verify_step", phase="Phase 65", task_id="2", checkpoint="c2", result="PASSED", exit_code=0, duration_sec=1.0)
        metrics.record_event("commit_step", phase="Phase 65", task_id="2", result="COMMITTED", commit="sha2", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")

        res = metrics.compute_phase_metrics("Phase 65")
        m = res["metrics"]
        self.assertEqual(m["tasks_committed"]["value"], 2)
        # 2タスク中1タスクがrework
        self.assertEqual(m["rework_tasks_ratio"]["value"], 0.5)
        self.assertEqual(m["rework_tasks_ratio"]["source"], "event")

    def test_audit_stage_with_unknown_or_partial_tokens_reports_null_token_ratio(self) -> None:
        """一部またはすべての監査ステージでトークン数が不明な場合、比率は歪めず null とすること。"""
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        # 1件目: トークンあり
        metrics.record_event("audit_stage", phase="Phase 64", status="SUCCESS", findings_by_severity={"P1": 1}, dispatched=1, backlogged=0, escalated=0, duplicate=0, rejected=0, input_tokens=1000, output_tokens=100)
        # 2件目: トークンなし (None)
        metrics.record_event("audit_stage", phase="Phase 64", status="SUCCESS", findings_by_severity={"P0": 1}, dispatched=1, backlogged=0, escalated=0, duplicate=0, rejected=0, input_tokens=None, output_tokens=None)

        res = metrics.compute_phase_metrics("Phase 64")
        m = res["metrics"]
        self.assertEqual(m["audit_dispatched"]["value"], 2)
        self.assertIsNone(m["genuine_p0p1_per_100k_tokens"]["value"])

    def test_phase_with_no_previous_phase_reports_null_delta(self) -> None:
        """直前フェーズが存在しない場合 (Phase 0 など)、全指標の差分が null となること。"""
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        metrics.record_event("commit_step", phase="Phase 0", task_id="1", result="COMMITTED", commit="sha0", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")
        res = metrics.compute_phase_metrics("Phase 0")
        for k, d_val in res["delta"].items():
            self.assertIsNone(d_val, f"Delta for '{k}' should be None when there is no previous phase")

    def test_compute_phase_metrics_git_history(self) -> None:
        """git 履歴から planned_steps, unplanned_steps, audit_steps_added, pipeline/human commits, lead_time が算出されること (XXX.4)。"""
        # Phase 63 用の履歴を作成
        p63_plan = (
            "# PLAN\n\n## 現在の目標\n- Phase 63: Target\n\n"
            "## ステップ\n- [ ] 1. タスク1\n- [ ] 2. タスク2\n"
        )
        self.plan.write_text(p63_plan, encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: start phase 63"], cwd=self.repo, check=True)

        # パイプラインコミット 1 ([refs: を含む)
        (self.repo / "f1.txt").write_text("1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "feat: task 1 [refs: 1]"], cwd=self.repo, check=True)

        # 計画外ステップと AUD ステップの追加
        p63_plan_updated = (
            "# PLAN\n\n## 現在の目標\n- Phase 63: Target\n\n"
            "## ステップ\n- [x] 1. タスク1\n- [x] 2. タスク2\n- [ ] 3. 計画外タスク\n- [ ] AUD-001: 監査タスク\n"
        )
        self.plan.write_text(p63_plan_updated, encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "feat: add unplanned and aud steps [refs: 2]"], cwd=self.repo, check=True)

        # 人間コミット (refs なし、非マージ)
        (self.repo / "note.txt").write_text("human note\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "docs: human documentation note"], cwd=self.repo, check=True)

        # 次のフェーズ Phase 64 の開始コミット
        p64_plan = (
            "# PLAN\n\n## 現在の目標\n- Phase 64: Next\n\n"
            "## ステップ\n- [ ] 1. 新タスク\n"
        )
        self.plan.write_text(p64_plan, encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "chore: start phase 64"], cwd=self.repo, check=True)

        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        res = metrics.compute_phase_metrics("Phase 63")
        m = res["metrics"]

        # planned_steps: 開始時点で存在した数値ステップ (1, 2) -> 2
        self.assertEqual(m["planned_steps"]["value"], 2)
        self.assertEqual(m["planned_steps"]["source"], "git")

        # unplanned_steps: 終了時点で増えた数値ステップ (3) -> 1
        self.assertEqual(m["unplanned_steps"]["value"], 1)
        self.assertEqual(m["unplanned_steps"]["source"], "git")

        # audit_steps_added: 増えた AUD- ステップ (AUD-001) -> 1
        self.assertEqual(m["audit_steps_added"]["value"], 1)
        self.assertEqual(m["audit_steps_added"]["source"], "git")

        # pipeline_commits: 範囲内で [refs: を含むコミット -> 2
        self.assertEqual(m["pipeline_commits"]["value"], 2)
        self.assertEqual(m["pipeline_commits"]["source"], "git")

        # out_of_path_commits: 範囲内で [refs: を含まないコミット -> 2 (start commit と docs commit)
        self.assertEqual(m["out_of_path_commits"]["value"], 2)
        self.assertEqual(m["out_of_path_commits"]["source"], "git")

        # lead_time_hours: >= 0.0
        self.assertIsNotNone(m["lead_time_hours"]["value"])
        self.assertGreaterEqual(m["lead_time_hours"]["value"], 0.0)
        self.assertEqual(m["lead_time_hours"]["source"], "git")

    def test_cli_metrics_append_to(self) -> None:
        """--append-to PATH により ## Phase <N> 節が追記され、重複時は終了コード 1 となること (XXX.4)。"""
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        metrics.record_event("commit_step", phase="Phase 64", task_id="1", result="COMMITTED", commit="abc", checker_decision="APPROVE", checker_model="claude-opus-5.5", checker_family="claude", maker_model="gemini-3.8-flash", maker_family="gemini")

        target_file = self.repo / "METRICS.md"
        target_file.write_text("# Metrics Log\n\n", encoding="utf-8")

        # 1. 正常追記
        res = self._orch("metrics", "--phase", "64", "--append-to", str(target_file))
        self.assertEqual(res.returncode, 0)
        content = target_file.read_text(encoding="utf-8")
        self.assertIn("## Phase 64", content)
        self.assertIn("tasks_committed", content)

        # 2. 重複追記の抑止 (同じ節が既にあれば終了コード 1、ファイル内容は不変)
        res_dup = self._orch("metrics", "--phase", "64", "--append-to", str(target_file))
        self.assertEqual(res_dup.returncode, 1)
        self.assertIn("ERROR:", res_dup.stderr)
        self.assertEqual(target_file.read_text(encoding="utf-8"), content)

        # 3. 部分一致の誤検知なし (Phase 6 は Phase 64 に誤一致しない)
        res_p6 = self._orch("metrics", "--phase", "6", "--append-to", str(target_file))
        self.assertEqual(res_p6.returncode, 0)
        content_after = target_file.read_text(encoding="utf-8")
        self.assertIn("## Phase 6\n", content_after)

    def test_git_metrics_failure_paths_return_null_instead_of_fabrications(self) -> None:
        """git コマンドや PLAN.md 読み取りが失敗した場合、0などの捏造値ではなく null (None) が返ること。"""
        from unittest.mock import patch

        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)

        # subprocess.run がエラーを返すケースを模倣
        orig_run = subprocess.run
        def mock_run(cmd, *args, **kwargs):
            if isinstance(cmd, list) and cmd[0] == "git" and "rev-list" in cmd:
                # rev-list 失敗をシミュレート
                return subprocess.CompletedProcess(cmd, returncode=128, stdout="", stderr="fatal: bad revision")
            return orig_run(cmd, *args, **kwargs)

        with patch("subprocess.run", side_effect=mock_run):
            res = metrics.compute_phase_metrics("Phase 64")
            m = res["metrics"]
            self.assertIsNone(m["pipeline_commits"]["value"])
            self.assertIsNone(m["out_of_path_commits"]["value"])


if __name__ == "__main__":
    unittest.main()
