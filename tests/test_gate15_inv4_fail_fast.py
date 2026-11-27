from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.metrics import MetricsLog

ORCH = Path(__file__).resolve().parents[1] / "scripts" / "nightly_orchestrator.py"
SKILL_PATH = Path(__file__).resolve().parents[1] / "skills" / "nightly-run" / "SKILL.md"


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


class TestGate15Inv4FailFast(unittest.TestCase):
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

        self.plan = self.repo / "PLAN.md"
        self.plan.write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 78: test\n\n## ステップ\n- [ ] 1. テストタスク\n- [ ] 2. 次タスク\n",
            encoding="utf-8",
        )
        self.spec_text = "# SPEC\n\n- [ ] **APP-1**: x は 1 である。\n"
        (self.repo / "SPEC.md").write_text(self.spec_text, encoding="utf-8")

        self.ok_sh = self.repo / "ok.sh"
        self.ok_sh.write_text("#!/usr/bin/env bash\necho ok > .verify_ran\nexit 0\n", encoding="utf-8")
        self.ng_sh = self.repo / "ng.sh"
        self.ng_sh.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
        for p in (self.ok_sh, self.ng_sh):
            p.chmod(0o755)

        (self.repo / ".gitignore").write_text(".verify_ran\n.dev/\n", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)
        self.checkpoint = self._head()
        (self.repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.io_idx = 0

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _head(self) -> str:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _orch(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ORCH), "--plan", str(self.plan), *args],
            cwd=cwd or self.repo,
            env=_clean_env(),
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def _record_review(self, decision: str | None, task_id: str = "1") -> subprocess.CompletedProcess[str]:
        self.io_idx += 1
        io = Path(self._tmp.name) / f"checker_io_{self.io_idx}"
        io.mkdir(parents=True, exist_ok=True)
        res = self._orch("review-input", "--task-id", task_id, "--out-dir", str(io))
        self.assertEqual(res.returncode, 0, f"review-input failed: {res.stderr}")
        spec_file = io / "spec_excerpt.md"
        spec_file.write_text(self.spec_text, encoding="utf-8")
        out = io / "review.txt"
        if decision is None:
            out.write_text("truncated output without decision\n", encoding="utf-8")
        else:
            out.write_text(f"review body\nDECISION: {decision}\n", encoding="utf-8")
        return self._orch(
            "record-review",
            "--task-id",
            task_id,
            "--checker-output",
            str(out),
            "--diff-file",
            str(io / "diff.patch"),
            "--spec-file",
            str(spec_file),
            "--maker-model",
            "gemini-4-hydrogen-medium",
            "--checker-model",
            "claude-sonnet-5-5",
            "--reason",
            f"decision={decision}",
        )

    def _read_events(self) -> list[dict[str, object]]:
        events_path = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        if not events_path.exists():
            return []
        return [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_verify_step_stops_after_reject_and_verify_failure(self) -> None:
        # 1st verify passes, then Checker REJECT (failure #1)
        v1 = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v1.returncode, 0, v1.stderr)
        r1 = self._record_review("REJECT", task_id="1")
        self.assertEqual(r1.returncode, 0, r1.stderr)

        # 2nd verify fails (failure #2)
        v2 = self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertNotEqual(v2.returncode, 0)

        # Remove marker to prove 3rd verify-step does NOT run ok.sh
        marker = self.repo / ".verify_ran"
        if marker.exists():
            marker.unlink()
        evidence_path = self.repo / ".git" / "nightly_evidence" / "task_1.json"
        evidence_before = evidence_path.read_text(encoding="utf-8") if evidence_path.exists() else None

        # 3rd verify-step MUST stop with INV4_FAIL_FAST: and exit 1
        v3 = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v3.returncode, 1, f"stdout={v3.stdout}\nstderr={v3.stderr}")
        self.assertTrue(
            any(line.startswith("INV4_FAIL_FAST:") for line in v3.stderr.splitlines()),
            f"Expected INV4_FAIL_FAST: in stderr, got:\n{v3.stderr}",
        )
        self.assertIn("rollback-step", v3.stderr)
        self.assertFalse(marker.exists(), "Verification script should NOT have been executed")
        evidence_after = evidence_path.read_text(encoding="utf-8") if evidence_path.exists() else None
        self.assertEqual(evidence_after, evidence_before)

        events = self._read_events()
        verify_events = [e for e in events if e.get("event") == "verify_step"]
        self.assertEqual(verify_events[-1].get("result"), "FAIL_FAST_STOPPED")

    def test_verify_step_stops_after_two_verify_failures(self) -> None:
        v1 = self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertNotEqual(v1.returncode, 0)
        v2 = self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertNotEqual(v2.returncode, 0)

        v3 = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v3.returncode, 1)
        self.assertIn("INV4_FAIL_FAST:", v3.stderr)

    def test_commit_step_stops_after_two_rejects_without_modifying_state(self) -> None:
        # 1st verify passes + REJECT (#1)
        self.assertEqual(
            self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1").returncode,
            0,
        )
        self.assertEqual(self._record_review("REJECT", task_id="1").returncode, 0)

        # 2nd verify passes + REJECT (#2) -> now 2 failures, evidence still exists from 2nd verify
        self.assertEqual(
            self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1").returncode,
            0,
        )
        self.assertEqual(self._record_review("REJECT", task_id="1").returncode, 0)

        evidence_path = self.repo / ".git" / "nightly_evidence" / "task_1.json"
        self.assertTrue(evidence_path.exists())
        evidence_before = evidence_path.read_text(encoding="utf-8")
        plan_before = self.plan.read_text(encoding="utf-8")

        c1 = self._orch(
            "commit-step",
            "--task-id",
            "1",
            "--checkpoint",
            self.checkpoint,
            "--maker-model",
            "gemini-4-hydrogen-medium",
            "--checker-model",
            "claude-sonnet-5-5",
            "--checker-reason",
            "attempt",
        )
        self.assertEqual(c1.returncode, 4, f"stdout={c1.stdout}\nstderr={c1.stderr}")
        self.assertIn("INV4_FAIL_FAST:", c1.stderr)
        self.assertEqual(self._head(), self.checkpoint)
        self.assertEqual(self.plan.read_text(encoding="utf-8"), plan_before)
        self.assertTrue(evidence_path.exists())
        self.assertEqual(evidence_path.read_text(encoding="utf-8"), evidence_before)
        commit_events = [e for e in self._read_events() if e.get("event") == "commit_step"]
        self.assertEqual(commit_events, [], f"commit-step should not record FAIL_FAST_STOPPED: {commit_events}")

    def test_commit_step_stops_after_verify_failure_and_reject(self) -> None:
        # 1st verify fails (#1), then 2nd verify passes + REJECT (#2)
        v1 = self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertNotEqual(v1.returncode, 0)
        self.assertEqual(
            self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1").returncode,
            0,
        )
        self.assertEqual(self._record_review("REJECT", task_id="1").returncode, 0)

        evidence_path = self.repo / ".git" / "nightly_evidence" / "task_1.json"
        self.assertTrue(evidence_path.exists())
        evidence_before = evidence_path.read_text(encoding="utf-8")
        plan_before = self.plan.read_text(encoding="utf-8")

        c1 = self._orch(
            "commit-step",
            "--task-id",
            "1",
            "--checkpoint",
            self.checkpoint,
            "--maker-model",
            "gemini-4-hydrogen-medium",
            "--checker-model",
            "claude-sonnet-5-5",
            "--checker-reason",
            "mixed failures",
        )
        self.assertEqual(c1.returncode, 4, f"stdout={c1.stdout}\nstderr={c1.stderr}")
        self.assertIn("INV4_FAIL_FAST:", c1.stderr)
        self.assertIn("verify_failures=1", c1.stderr)
        self.assertIn("checker_rejects=1", c1.stderr)
        self.assertEqual(self._head(), self.checkpoint)
        self.assertEqual(self.plan.read_text(encoding="utf-8"), plan_before)
        self.assertEqual(evidence_path.read_text(encoding="utf-8"), evidence_before)
        commit_events = [e for e in self._read_events() if e.get("event") == "commit_step"]
        self.assertEqual(commit_events, [])

    def test_preflight_does_not_reset_and_rollback_resets_count(self) -> None:
        v1 = self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertNotEqual(v1.returncode, 0)
        # pre-flight requires a clean working tree; remove uncommitted app.py before pre-flight
        app_py = self.repo / "app.py"
        if app_py.exists():
            app_py.unlink()
        # pre-flight (task_start) must succeed, record task_start, and NOT reset failure count
        pf = self._orch("pre-flight")
        self.assertEqual(pf.returncode, 0, f"pre-flight failed:\nstdout={pf.stdout}\nstderr={pf.stderr}")
        self.assertTrue(
            any(e.get("event") == "task_start" and str(e.get("task_id")) == "1" for e in self._read_events()),
            f"Expected task_start event in events.jsonl, got: {self._read_events()}",
        )
        app_py.write_text("x = 1\n", encoding="utf-8")
        v2 = self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertNotEqual(v2.returncode, 0)

        v3 = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v3.returncode, 1)
        self.assertIn("INV4_FAIL_FAST:", v3.stderr)

        # rollback-step resets the count
        rb = self._orch("rollback-step", "--checkpoint", self.checkpoint, "--task-id", "1", "--reason", "inv4")
        self.assertEqual(rb.returncode, 0, rb.stderr)

        (self.repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        v4 = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v4.returncode, 0, f"stdout={v4.stdout}\nstderr={v4.stderr}")

    def test_commit_step_committed_resets_failure_count(self) -> None:
        # 1 verify failure, then pass + APPROVE + commit-step (COMMITTED)
        self.assertNotEqual(
            self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1").returncode,
            0,
        )
        self.assertEqual(
            self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1").returncode,
            0,
        )
        self.assertEqual(self._record_review("APPROVE", task_id="1").returncode, 0)
        c1 = self._orch(
            "commit-step",
            "--task-id",
            "1",
            "--checkpoint",
            self.checkpoint,
            "--maker-model",
            "gemini-4-hydrogen-medium",
            "--checker-model",
            "claude-sonnet-5-5",
            "--checker-decision",
            "APPROVE",
            "--checker-reason",
            "ok",
        )
        self.assertEqual(c1.returncode, 0, f"stdout={c1.stdout}\nstderr={c1.stderr}")

        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        counts = metrics.count_task_failures_since_reset("1", phase="Phase 78")
        self.assertEqual(counts, {"verify_failures": 0, "checker_rejects": 0, "total": 0})

        # A single subsequent failure on the same task_id must NOT trigger INV4_FAIL_FAST
        self.plan.write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 78: test\n\n## ステップ\n- [ ] 1. 再テスト\n- [ ] 2. 次タスク\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "reopen"], cwd=self.repo, check=True, capture_output=True)
        new_chk = self._head()
        (self.repo / "app.py").write_text("x = 2\n", encoding="utf-8")

        self.assertNotEqual(
            self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", new_chk, "--task-id", "1").returncode,
            0,
        )
        self.assertEqual(metrics.count_task_failures_since_reset("1", phase="Phase 78")["total"], 1)
        v_next = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", new_chk, "--task-id", "1")
        self.assertEqual(v_next.returncode, 0, v_next.stderr)

    def test_fail_fast_stopped_not_counted_as_additional_failure(self) -> None:
        self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")

        # Repeated fail-fast stops must not increment the failure counter
        v3 = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        v4 = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v3.returncode, 1)
        self.assertEqual(v4.returncode, 1)

        stopped = [
            e for e in self._read_events()
            if e.get("event") == "verify_step" and e.get("result") == "FAIL_FAST_STOPPED"
        ]
        self.assertEqual(len(stopped), 2)
        metrics = MetricsLog(repo_dir=self.repo, plan_path=self.plan)
        counts = metrics.count_task_failures_since_reset("1", phase="Phase 78")
        self.assertEqual(counts, {"verify_failures": 2, "checker_rejects": 0, "total": 2})

    def test_team_and_phase_isolation(self) -> None:
        events_path = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        events_path.parent.mkdir(parents=True, exist_ok=True)
        mixed_records = [
            # Previous phase failures for same task & team -> must be ignored
            {"schema": 1, "ts": 100, "event": "verify_step", "phase": "Phase 77", "task_id": "1", "team": "alpha", "result": "FAILED"},
            {"schema": 1, "ts": 101, "event": "verify_step", "phase": "Phase 77", "task_id": "1", "team": "alpha", "result": "FAILED"},
            {"schema": 1, "ts": 102, "event": "verify_step", "phase": "Phase 77", "task_id": "1", "result": "FAILED"},
            {"schema": 1, "ts": 103, "event": "verify_step", "phase": "Phase 77", "task_id": "1", "result": "FAILED"},
            # Other team failures in current phase -> must be ignored for team alpha & team=None
            {"schema": 1, "ts": 104, "event": "verify_step", "phase": "Phase 78", "task_id": "1", "team": "beta", "result": "FAILED"},
            {"schema": 1, "ts": 105, "event": "checker_review", "phase": "Phase 78", "task_id": "1", "team": "beta", "decision": "REJECT"},
            # Other task failure in same phase & team -> must be ignored
            {"schema": 1, "ts": 106, "event": "checker_review", "phase": "Phase 78", "task_id": "2", "team": "alpha", "decision": "REJECT"},
            # 1 failure for team alpha in current phase -> count must be 1 (< 2)
            {"schema": 1, "ts": 107, "event": "verify_step", "phase": "Phase 78", "task_id": "1", "team": "alpha", "result": "FAILED"},
        ]
        events_path.write_text("\n".join(json.dumps(r) for r in mixed_records) + "\n", encoding="utf-8")

        m_alpha = MetricsLog(repo_dir=self.repo, plan_path=self.plan, team_id="alpha")
        m_beta = MetricsLog(repo_dir=self.repo, plan_path=self.plan, team_id="beta")
        m_none = MetricsLog(repo_dir=self.repo, plan_path=self.plan, team_id=None)

        self.assertEqual(m_alpha.count_task_failures_since_reset("1", phase="Phase 78", team_id="alpha")["total"], 1)
        self.assertEqual(m_beta.count_task_failures_since_reset("1", phase="Phase 78", team_id="beta")["total"], 2)
        self.assertEqual(m_none.count_task_failures_since_reset("1", phase="Phase 78", team_id=None)["total"], 0)

        # CLI verify-step for single-repo (team=None) must NOT be blocked by Phase 77 or team alpha/beta failures
        v_ok = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v_ok.returncode, 0, f"stdout={v_ok.stdout}\nstderr={v_ok.stderr}")

        # A 2nd failure in team alpha triggers >= 2 for team alpha only
        with open(events_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"schema": 1, "ts": 108, "event": "checker_review", "phase": "Phase 78", "task_id": "1", "team": "alpha", "decision": "REJECT"}) + "\n")
        self.assertEqual(
            m_alpha.count_task_failures_since_reset("1", phase="Phase 78", team_id="alpha"),
            {"verify_failures": 1, "checker_rejects": 1, "total": 2},
        )
        self.assertEqual(m_none.count_task_failures_since_reset("1", phase="Phase 78", team_id=None)["total"], 0)

    def test_undecided_review_does_not_count_toward_failures(self) -> None:
        # 1st verify passes + undecided review (exit 4) -> 0 failures
        self.assertEqual(
            self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1").returncode,
            0,
        )
        r_undecided = self._record_review(None, task_id="1")
        self.assertEqual(r_undecided.returncode, 4)

        # 1 verify failure -> total 1 failure (still < 2)
        self._orch("--verify-script", str(self.ng_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")

        # Next verify-step should still run (total=1 < 2)
        v_next = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v_next.returncode, 0, v_next.stderr)

    def test_unreadable_events_log_warns_and_proceeds(self) -> None:
        # Block events.jsonl with a directory -> unreadable
        blocked = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        blocked.mkdir(parents=True, exist_ok=True)

        v1 = self._orch("--verify-script", str(self.ok_sh), "verify-step", "--checkpoint", self.checkpoint, "--task-id", "1")
        self.assertEqual(v1.returncode, 0, v1.stderr)
        self.assertIn("METRICS_WARNING: Failed to read events log", v1.stderr)
        self.assertIn("skipping INV-4 fail-fast check", v1.stderr)
        self.assertTrue((self.repo / ".verify_ran").exists())

    def test_skill_documents_inv4_fail_fast(self) -> None:
        text = SKILL_PATH.read_text(encoding="utf-8")
        self.assertIn("INV4_FAIL_FAST:", text)
        self.assertIn("rollback-step", text)


if __name__ == "__main__":
    unittest.main()
