from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

from auto_developer.models import Task
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import RunnerAdapter

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return env


def _run_cli(work_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(work_dir), *args],
        cwd=work_dir,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


class _ScriptedRunner(RunnerAdapter):
    def __init__(
        self,
        repo_dir: Path,
        *,
        fail_first_verify: bool = False,
        reject_checker: bool = False,
    ) -> None:
        self.repo_dir = repo_dir
        self.fail_first_verify = fail_first_verify
        self.reject_checker = reject_checker

    def run_maker(self, task: Task, context: dict[str, Any] | None = None) -> tuple[int, str]:
        attempt = int((context or {}).get("attempt", 1))
        target = self.repo_dir / f"task_{task.task_id}.txt"
        if self.fail_first_verify and attempt == 1:
            target.write_text("FAIL\n", encoding="utf-8")
        else:
            target.write_text("OK\n", encoding="utf-8")
        return 0, f"maker attempt {attempt} done"

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        if self.reject_checker:
            return False, "DECISION: REJECT\nRejected by scripted checker"
        return True, "DECISION: APPROVE\nApproved by scripted checker"


class TestStat4EventsRun(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmp_dir.name) / "repo"
        self.repo.mkdir()
        _git(self.repo, "init", "-b", "main")
        _git(self.repo, "config", "user.name", "Stat4 Tester")
        _git(self.repo, "config", "user.email", "stat4@example.com")
        (self.repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
        (self.repo / "SPEC.md").write_text("# SPEC\n\n- [ ] **APP-1**: sample\n", encoding="utf-8")
        (self.repo / "PLAN.md").write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 73: test\n\n## ステップ\n- [ ] 1. Sample task\n",
            encoding="utf-8",
        )
        scripts_dir = self.repo / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        self.verify_sh = scripts_dir / "verify_local.sh"
        self.verify_sh.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [ -f task_1.txt ] && grep -q '^FAIL$' task_1.txt; then\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        self.verify_sh.chmod(0o755)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "chore: init")

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _read_events(self, events_path: Path) -> list[dict[str, Any]]:
        self.assertTrue(events_path.is_file(), f"events.jsonl missing: {events_path}")
        return [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_run_records_task_start_verify_checker_commit_events(self) -> None:
        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_ScriptedRunner(self.repo),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        code = orch.run()
        self.assertEqual(code, 0)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(names, ["task_start", "verify_step", "checker_review", "commit_step"])

        ts_ev, vs_ev, cr_ev, cs_ev = events
        self.assertEqual(ts_ev["task_id"], "1")
        self.assertEqual(ts_ev["attempt"], 1)
        self.assertEqual(ts_ev["phase"], "Phase 73")
        self.assertNotIn("team", ts_ev)

        self.assertEqual(vs_ev["task_id"], "1")
        self.assertEqual(vs_ev["attempt"], 1)
        self.assertEqual(vs_ev["result"], "PASSED")
        self.assertEqual(vs_ev["exit_code"], 0)

        self.assertEqual(cr_ev["task_id"], "1")
        self.assertEqual(cr_ev["decision"], "APPROVE")
        self.assertEqual(cr_ev["checker_model"], "claude-sonnet-5-5")
        self.assertEqual(cr_ev["checker_family"], "claude")
        self.assertEqual(cr_ev["maker_model"], "gemini-3.8-flash")
        self.assertEqual(cr_ev["maker_family"], "gemini")

        head_sha = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.assertEqual(cs_ev["task_id"], "1")
        self.assertEqual(cs_ev["result"], "COMMITTED")
        self.assertEqual(cs_ev["commit"], head_sha)
        self.assertEqual(cs_ev["checker_decision"], "APPROVE")
        self.assertEqual(cs_ev["checker_model"], "claude-sonnet-5-5")
        self.assertEqual(cs_ev["checker_family"], "claude")
        self.assertEqual(cs_ev["maker_model"], "gemini-3.8-flash")
        self.assertEqual(cs_ev["maker_family"], "gemini")

    def test_run_retry_records_attempt_1_and_2_events(self) -> None:
        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_ScriptedRunner(self.repo, fail_first_verify=True),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        code = orch.run()
        self.assertEqual(code, 0)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(
            names,
            ["task_start", "verify_step", "task_start", "verify_step", "checker_review", "commit_step"],
        )
        self.assertEqual(events[0]["attempt"], 1)
        self.assertEqual(events[1]["attempt"], 1)
        self.assertEqual(events[1]["result"], "FAILED")
        self.assertEqual(events[1]["exit_code"], 1)
        self.assertEqual(events[2]["attempt"], 2)
        self.assertEqual(events[3]["attempt"], 2)
        self.assertEqual(events[3]["result"], "PASSED")
        self.assertEqual(events[3]["exit_code"], 0)

    def test_run_checker_reject_records_checker_review_and_rollback_step(self) -> None:
        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_ScriptedRunner(self.repo, reject_checker=True),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        code = orch.run()
        self.assertEqual(code, 1)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(names, ["task_start", "verify_step", "checker_review", "rollback_step"])
        self.assertEqual(events[2]["decision"], "REJECT")
        self.assertEqual(events[2]["checker_model"], "claude-sonnet-5-5")
        self.assertEqual(events[2]["checker_family"], "claude")
        self.assertEqual(events[3]["task_id"], "1")
        self.assertTrue(events[3]["quarantine_branch"])

    def test_run_non_interference_when_events_blocked(self) -> None:
        blocked = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        blocked.mkdir(parents=True)
        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_ScriptedRunner(self.repo),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        code = orch.run()
        self.assertEqual(code, 0)

    def test_pre_flight_records_task_start_only_when_pending_task_exists(self) -> None:
        res = _run_cli(self.repo, "pre-flight")
        self.assertEqual(res.returncode, 0, res.stderr)
        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "task_start")
        self.assertEqual(events[0]["task_id"], "1")
        self.assertEqual(events[0]["attempt"], 1)

        # Mark task done and commit; pre-flight with no pending tasks must NOT write task_start
        (self.repo / "PLAN.md").write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 73: test\n\n## ステップ\n- [x] 1. Sample task\n",
            encoding="utf-8",
        )
        _git(self.repo, "add", "PLAN.md")
        _git(self.repo, "commit", "-m", "done")
        res2 = _run_cli(self.repo, "pre-flight")
        self.assertEqual(res2.returncode, 0, res2.stderr)
        events2 = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        self.assertEqual(len(events2), 1)

    def test_teams_run_records_team_id_in_worktree_events(self) -> None:
        team_repo = Path(self.tmp_dir.name) / "team_repo"
        team_repo.mkdir()
        _git(team_repo, "init", "-b", "main")
        _git(team_repo, "config", "user.name", "E2E Runner")
        _git(team_repo, "config", "user.email", "e2e@example.com")

        self.assertEqual(_run_cli(team_repo, "init").returncode, 0)
        self.assertEqual(_run_cli(team_repo, "teams", "init", "--teams", "core,ui").returncode, 0)
        (team_repo / "PLAN.md").write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 73: teams\n\n## ステップ\n- [ ] 1. Root\n",
            encoding="utf-8",
        )
        for tid in ("core", "ui"):
            (team_repo / "plans" / f"{tid}.md").write_text(
                f"# PLAN ({tid})\n\n## 現在の目標\n- Phase 73: {tid}\n\n## ステップ\n- [ ] 1. {tid} task\n",
                encoding="utf-8",
            )
        (team_repo / "src" / "core").mkdir(parents=True, exist_ok=True)
        (team_repo / "src" / "ui").mkdir(parents=True, exist_ok=True)
        (team_repo / "src" / "core" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
        (team_repo / "src" / "ui" / "__init__.py").write_text("Y = 1\n", encoding="utf-8")
        tests_dir = team_repo / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_ok.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
            encoding="utf-8",
        )
        _git(team_repo, "add", "-A")
        _git(team_repo, "commit", "-m", "init teams")

        maker_script = Path(self.tmp_dir.name) / "maker.py"
        maker_script.write_text(
            "import pathlib\n"
            "tid = pathlib.Path.cwd().name\n"
            "pathlib.Path(f'src/{tid}/mod.py').write_text(f'{tid} = 1\\n')\n",
            encoding="utf-8",
        )
        checker_script = Path(self.tmp_dir.name) / "checker.py"
        checker_script.write_text(
            "print('Looks good')\nprint('DECISION: APPROVE')\n",
            encoding="utf-8",
        )

        run_res = _run_cli(
            team_repo,
            "teams",
            "run",
            "--maker-cmd",
            f"{sys.executable} {maker_script}",
            "--checker-cmd",
            f"{sys.executable} {checker_script}",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-sonnet-5-5",
        )
        self.assertEqual(run_res.returncode, 0, f"stdout={run_res.stdout}\nstderr={run_res.stderr}")

        for tid in ("core", "ui"):
            wt_dir = team_repo / ".dev" / "worktrees" / tid
            events = self._read_events(wt_dir / ".dev" / "metrics" / "events.jsonl")
            event_names = [e["event"] for e in events]
            for expected in ("task_start", "verify_step", "checker_review", "commit_step"):
                self.assertIn(expected, event_names, f"{tid} missing {expected}: {event_names}")
            for ev in events:
                self.assertEqual(ev.get("team"), tid, f"Event missing team={tid}: {ev}")

    def test_pre_flight_in_team_worktree_records_team_id(self) -> None:
        team_repo = Path(self.tmp_dir.name) / "team_pf_repo"
        team_repo.mkdir()
        _git(team_repo, "init", "-b", "main")
        _git(team_repo, "config", "user.name", "E2E Runner")
        _git(team_repo, "config", "user.email", "e2e@example.com")

        self.assertEqual(_run_cli(team_repo, "init").returncode, 0)
        self.assertEqual(_run_cli(team_repo, "teams", "init", "--teams", "core,ui").returncode, 0)
        (team_repo / "PLAN.md").write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 73: teams\n\n## ステップ\n- [ ] 1. Root\n",
            encoding="utf-8",
        )
        for tid in ("core", "ui"):
            (team_repo / "plans" / f"{tid}.md").write_text(
                f"# PLAN ({tid})\n\n## 現在の目標\n- Phase 73: {tid}\n\n## ステップ\n- [ ] 1. {tid} task\n",
                encoding="utf-8",
            )
        _git(team_repo, "add", "-A")
        _git(team_repo, "commit", "-m", "init teams")
        self.assertEqual(_run_cli(team_repo, "teams", "prepare").returncode, 0)

        core_wt = team_repo / ".dev" / "worktrees" / "core"
        pf_res = _run_cli(core_wt, "pre-flight")
        self.assertEqual(pf_res.returncode, 0, pf_res.stderr)
        pf_events = self._read_events(core_wt / ".dev" / "metrics" / "events.jsonl")
        self.assertEqual(pf_events[0]["event"], "task_start")
        self.assertEqual(pf_events[0]["team"], "core")

    def test_pre_flight_non_interference_when_events_blocked(self) -> None:
        blocked = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        blocked.mkdir(parents=True)
        res = _run_cli(self.repo, "pre-flight")
        self.assertEqual(res.returncode, 0, res.stderr)
        payload = json.loads(res.stdout)
        self.assertTrue(payload["clean"])
        self.assertEqual(payload["next_task"]["id"], "1")
        self.assertIn("METRICS_WARNING", res.stderr)

    def test_run_verify_boundary_violation_and_double_failure_rollback_with_team(self) -> None:
        (self.repo / "src" / "core").mkdir(parents=True, exist_ok=True)
        (self.repo / "src" / "core" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
        self.verify_sh.write_text(
            "#!/usr/bin/env bash\n"
            "set -euo pipefail\n"
            "if [ -f src/core/mod.py ] && grep -q 'LEAK' src/core/mod.py; then\n"
            "  echo 'leak' > outside_leak.txt\n"
            "  exit 0\n"
            "fi\n"
            "if [ -f src/core/mod.py ] && grep -q 'FAIL' src/core/mod.py; then\n"
            "  exit 1\n"
            "fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        self.verify_sh.chmod(0o755)
        _git(self.repo, "add", "-A")
        _git(self.repo, "commit", "-m", "setup boundary verify")

        class _TeamMakerRunner(RunnerAdapter):
            def __init__(self, repo_dir: Path) -> None:
                self.repo_dir = repo_dir

            def run_maker(self, task: Task, context: dict[str, Any] | None = None) -> tuple[int, str]:
                attempt = int((context or {}).get("attempt", 1))
                leak_file = self.repo_dir / "outside_leak.txt"
                if leak_file.exists():
                    leak_file.unlink()
                mode = "LEAK" if attempt == 1 else "FAIL"
                (self.repo_dir / "src" / "core" / "mod.py").write_text(
                    f"MODE = '{mode}'\n", encoding="utf-8"
                )
                return 0, f"maker attempt {attempt}"

            def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
                return True, "DECISION: APPROVE\nok"

        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_TeamMakerRunner(self.repo),
            team_id="core",
            owns_patterns=["src/core/**"],
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        code = orch.run()
        self.assertEqual(code, 1)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(
            names,
            ["task_start", "verify_step", "task_start", "verify_step", "rollback_step"],
        )
        self.assertEqual(events[1]["attempt"], 1)
        self.assertEqual(events[1]["result"], "BOUNDARY_VIOLATION")
        self.assertEqual(events[1]["exit_code"], 1)
        self.assertEqual(events[3]["attempt"], 2)
        self.assertEqual(events[3]["result"], "FAILED")
        self.assertEqual(events[3]["exit_code"], 1)
        self.assertEqual(events[4]["task_id"], "1")
        self.assertTrue(events[4]["quarantine_branch"])
        for ev in events:
            self.assertEqual(ev.get("team"), "core")

    def test_run_commit_step_evidence_error(self) -> None:
        from unittest.mock import patch

        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_ScriptedRunner(self.repo),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        with patch.object(orch.evidence_store, "verify_and_consume", return_value=False):
            code = orch.run()
        self.assertEqual(code, 3)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(names, ["task_start", "verify_step", "checker_review", "commit_step"])
        self.assertEqual(events[3]["result"], "EVIDENCE_ERROR")
        self.assertIsNone(events[3]["commit"])

    def test_run_checker_missing_decision_records_unknown(self) -> None:
        class _MissingDecisionRunner(_ScriptedRunner):
            def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
                return True, "Looks good without decision line"

        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_MissingDecisionRunner(self.repo),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        code = orch.run()
        self.assertEqual(code, 1)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(names, ["task_start", "verify_step", "checker_review", "rollback_step"])
        self.assertEqual(events[2]["decision"], "UNKNOWN")
        self.assertEqual(events[3]["task_id"], "1")

    def test_run_checker_failed_with_approve_output_does_not_record_approve(self) -> None:
        class _ApproveTextButFailedRunner(_ScriptedRunner):
            def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
                return False, "DECISION: APPROVE\nChecker exited non-zero after printing APPROVE"

        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_ApproveTextButFailedRunner(self.repo),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        code = orch.run()
        self.assertEqual(code, 1)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(names, ["task_start", "verify_step", "checker_review", "rollback_step"])
        self.assertNotEqual(events[2]["decision"], "APPROVE")
        self.assertEqual(events[2]["decision"], "REJECT")

    def test_run_events_match_low_level_cli_schema_and_vocabulary(self) -> None:
        # 1. Low-level CLI path (success: pre-flight -> verify-step(FAIL) -> verify-step(PASS) -> record-review -> commit-step)
        cli_repo = Path(self.tmp_dir.name) / "cli_repo"
        cli_repo.mkdir()
        _git(cli_repo, "init", "-b", "main")
        _git(cli_repo, "config", "user.name", "Stat4 Tester")
        _git(cli_repo, "config", "user.email", "stat4@example.com")
        (cli_repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
        (cli_repo / "SPEC.md").write_text("# SPEC\n\n- [ ] **APP-1**: sample\n", encoding="utf-8")
        (cli_repo / "PLAN.md").write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 73: test\n\n## ステップ\n- [ ] 1. Sample task\n",
            encoding="utf-8",
        )
        (cli_repo / "scripts").mkdir(parents=True, exist_ok=True)
        cli_verify = cli_repo / "scripts" / "verify_local.sh"
        cli_verify.write_text(self.verify_sh.read_text(encoding="utf-8"), encoding="utf-8")
        cli_verify.chmod(0o755)
        _git(cli_repo, "add", "-A")
        _git(cli_repo, "commit", "-m", "chore: init")
        chk = _git(cli_repo, "rev-parse", "HEAD").stdout.strip()

        self.assertEqual(_run_cli(cli_repo, "pre-flight").returncode, 0)
        (cli_repo / "task_1.txt").write_text("FAIL\n", encoding="utf-8")
        self.assertNotEqual(
            _run_cli(cli_repo, "--verify-script", str(cli_verify), "verify-step", "--checkpoint", chk, "--task-id", "1").returncode,
            0,
        )
        (cli_repo / "task_1.txt").write_text("OK\n", encoding="utf-8")
        self.assertEqual(
            _run_cli(cli_repo, "--verify-script", str(cli_verify), "verify-step", "--checkpoint", chk, "--task-id", "1").returncode,
            0,
        )
        from review_test_helpers import record_test_review

        rr = record_test_review(
            cli_repo,
            "1",
            plan_path=cli_repo / "PLAN.md",
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
            reason="looks good",
        )
        self.assertEqual(rr.returncode, 0, rr.stderr)
        cs = _run_cli(
            cli_repo,
            "commit-step",
            "--task-id",
            "1",
            "--checkpoint",
            chk,
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-sonnet-5-5",
            "--checker-decision",
            "APPROVE",
            "--checker-reason",
            "looks good",
        )
        self.assertEqual(cs.returncode, 0, cs.stderr)

        # Low-level rollback-step
        (cli_repo / "task_2_tmp.txt").write_text("dirty\n", encoding="utf-8")
        head2 = _git(cli_repo, "rev-parse", "HEAD").stdout.strip()
        rb = _run_cli(cli_repo, "rollback-step", "--checkpoint", head2, "--task-id", "1", "--reason", "manual rollback")
        self.assertEqual(rb.returncode, 0, rb.stderr)

        cli_events = self._read_events(cli_repo / ".git" / "nightly_evidence" / "events.jsonl")
        cli_by_type: dict[str, list[dict[str, Any]]] = {}
        for ev in cli_events:
            cli_by_type.setdefault(ev["event"], []).append(ev)

        # 2. Orchestrator run() path (retry success + separate reject rollback)
        orch_ok = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_ScriptedRunner(self.repo, fail_first_verify=True),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        self.assertEqual(orch_ok.run(), 0)
        run_ok_events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")

        rb_repo = Path(self.tmp_dir.name) / "rb_repo"
        rb_repo.mkdir()
        _git(rb_repo, "init", "-b", "main")
        _git(rb_repo, "config", "user.name", "Stat4 Tester")
        _git(rb_repo, "config", "user.email", "stat4@example.com")
        (rb_repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
        (rb_repo / "SPEC.md").write_text("# SPEC\n\n- [ ] **APP-1**: sample\n", encoding="utf-8")
        (rb_repo / "PLAN.md").write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 73: test\n\n## ステップ\n- [ ] 1. Sample task\n",
            encoding="utf-8",
        )
        (rb_repo / "scripts").mkdir(parents=True, exist_ok=True)
        rb_verify = rb_repo / "scripts" / "verify_local.sh"
        rb_verify.write_text(self.verify_sh.read_text(encoding="utf-8"), encoding="utf-8")
        rb_verify.chmod(0o755)
        _git(rb_repo, "add", "-A")
        _git(rb_repo, "commit", "-m", "chore: init")

        orch_rb = NightlyOrchestrator(
            repo_dir=rb_repo,
            runner_adapter=_ScriptedRunner(rb_repo, reject_checker=True),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        self.assertEqual(orch_rb.run(), 1)
        run_rb_events = self._read_events(rb_repo / ".git" / "nightly_evidence" / "events.jsonl")

        run_by_type: dict[str, list[dict[str, Any]]] = {}
        for ev in run_ok_events + run_rb_events:
            run_by_type.setdefault(ev["event"], []).append(ev)

        for ev_name in ("task_start", "verify_step", "checker_review", "commit_step", "rollback_step"):
            self.assertIn(ev_name, cli_by_type, f"CLI missing {ev_name}")
            self.assertIn(ev_name, run_by_type, f"run() missing {ev_name}")
            self.assertEqual(
                set(run_by_type[ev_name][0].keys()),
                set(cli_by_type[ev_name][0].keys()),
                f"Field mismatch for {ev_name}",
            )

        self.assertEqual(
            {e["result"] for e in run_by_type["verify_step"]},
            {e["result"] for e in cli_by_type["verify_step"]},
        )
        self.assertEqual(
            {e["result"] for e in run_by_type["commit_step"]},
            {e["result"] for e in cli_by_type["commit_step"]},
        )

    def test_run_maker_failure_records_rollback_step(self) -> None:
        class _FailingMakerRunner(_ScriptedRunner):
            def run_maker(self, task: Task, context: dict[str, Any] | None = None) -> tuple[int, str]:
                (self.repo_dir / "task_1.txt").write_text("partial\n", encoding="utf-8")
                return 1, "maker failed intentionally"

        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_FailingMakerRunner(self.repo),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        code = orch.run()
        self.assertEqual(code, 1)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(names, ["task_start", "rollback_step"])
        self.assertEqual(events[1]["task_id"], "1")
        self.assertTrue(events[1]["quarantine_branch"])
        self.assertIn("maker failed intentionally", events[1]["reason"])

    def test_run_commit_step_git_commit_failure_records_commit_error_and_rollback(self) -> None:
        from unittest.mock import patch

        orch = NightlyOrchestrator(
            repo_dir=self.repo,
            runner_adapter=_ScriptedRunner(self.repo),
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        with patch.object(
            orch.git_guard,
            "commit_task",
            side_effect=subprocess.CalledProcessError(1, ["git", "commit"]),
        ):
            code = orch.run()
        self.assertNotEqual(code, 0)

        events = self._read_events(self.repo / ".git" / "nightly_evidence" / "events.jsonl")
        names = [e["event"] for e in events]
        self.assertEqual(
            names,
            ["task_start", "verify_step", "checker_review", "commit_step", "rollback_step"],
        )
        self.assertEqual(events[3]["result"], "COMMIT_ERROR")
        self.assertIsNone(events[3]["commit"])
        self.assertEqual(events[4]["task_id"], "1")

    def test_teams_run_rollback_records_team_and_non_interference(self) -> None:
        team_repo = Path(self.tmp_dir.name) / "team_rb_repo"
        team_repo.mkdir()
        _git(team_repo, "init", "-b", "main")
        _git(team_repo, "config", "user.name", "E2E Runner")
        _git(team_repo, "config", "user.email", "e2e@example.com")

        self.assertEqual(_run_cli(team_repo, "init").returncode, 0)
        self.assertEqual(_run_cli(team_repo, "teams", "init", "--teams", "core,ui").returncode, 0)
        (team_repo / "PLAN.md").write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 73: teams\n\n## ステップ\n- [ ] 1. Root\n",
            encoding="utf-8",
        )
        for tid in ("core", "ui"):
            (team_repo / "plans" / f"{tid}.md").write_text(
                f"# PLAN ({tid})\n\n## 現在の目標\n- Phase 73: {tid}\n\n## ステップ\n- [ ] 1. {tid} task\n",
                encoding="utf-8",
            )
        (team_repo / "src" / "core").mkdir(parents=True, exist_ok=True)
        (team_repo / "src" / "ui").mkdir(parents=True, exist_ok=True)
        (team_repo / "src" / "core" / "__init__.py").write_text("X = 1\n", encoding="utf-8")
        (team_repo / "src" / "ui" / "__init__.py").write_text("Y = 1\n", encoding="utf-8")
        tests_dir = team_repo / "tests"
        tests_dir.mkdir(exist_ok=True)
        (tests_dir / "test_ok.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
            encoding="utf-8",
        )
        _git(team_repo, "add", "-A")
        _git(team_repo, "commit", "-m", "init teams")

        maker_script = Path(self.tmp_dir.name) / "maker_rb.py"
        maker_script.write_text(
            "import pathlib\n"
            "tid = pathlib.Path.cwd().name\n"
            "pathlib.Path(f'src/{tid}/mod.py').write_text(f'{tid} = 1\\n')\n",
            encoding="utf-8",
        )
        checker_reject = Path(self.tmp_dir.name) / "checker_rb.py"
        checker_reject.write_text(
            "print('DECISION: REJECT')\n",
            encoding="utf-8",
        )

        # 1. teams run with checker reject -> rollback_step has team=<tid>
        rb_res = _run_cli(
            team_repo,
            "teams",
            "run",
            "--maker-cmd",
            f"{sys.executable} {maker_script}",
            "--checker-cmd",
            f"{sys.executable} {checker_reject}",
            "--maker-model",
            "gemini-3.8-flash",
            "--checker-model",
            "claude-sonnet-5-5",
        )
        self.assertNotEqual(rb_res.returncode, 0)
        for tid in ("core", "ui"):
            wt_dir = team_repo / ".dev" / "worktrees" / tid
            events = self._read_events(wt_dir / ".dev" / "metrics" / "events.jsonl")
            rb_events = [e for e in events if e["event"] == "rollback_step"]
            self.assertEqual(len(rb_events), 1, f"{tid} missing rollback_step: {events}")
            self.assertEqual(rb_events[0].get("team"), tid)

        # 2. INV-8 non-interference in team mode when metrics events.jsonl is blocked by a directory
        core_wt = team_repo / ".dev" / "worktrees" / "core"
        core_metrics = core_wt / ".dev" / "metrics" / "events.jsonl"
        core_metrics.unlink()
        core_metrics.mkdir(parents=True)
        (core_wt / "plans" / "core.md").write_text(
            "# PLAN (core)\n\n## 現在の目標\n- Phase 73: core\n\n## ステップ\n- [ ] 2. core task 2\n",
            encoding="utf-8",
        )
        _git(core_wt, "add", "plans/core.md")
        _git(core_wt, "commit", "-m", "add core task 2")

        checker_ok = Path(self.tmp_dir.name) / "checker_ok.py"
        checker_ok.write_text("print('DECISION: APPROVE')\n", encoding="utf-8")
        orch_core = NightlyOrchestrator(
            repo_dir=core_wt,
            runner_adapter=_ScriptedRunner(core_wt),
            team_id="core",
            owns_patterns=["src/core/**"],
            maker_model="gemini-3.8-flash",
            checker_model="claude-sonnet-5-5",
        )
        # _ScriptedRunner writes task_1.txt which violates owns; use a core-scoped runner
        class _CoreOkRunner(RunnerAdapter):
            def run_maker(self, task: Task, context: dict[str, Any] | None = None) -> tuple[int, str]:
                (core_wt / "src" / "core" / "mod2.py").write_text("CORE2 = 1\n", encoding="utf-8")
                return 0, "ok"

            def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
                return True, "DECISION: APPROVE\nok"

        orch_core.runner_adapter = _CoreOkRunner()
        self.assertEqual(orch_core.run(), 0)


if __name__ == "__main__":
    unittest.main()
