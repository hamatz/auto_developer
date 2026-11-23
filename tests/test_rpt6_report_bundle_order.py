from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import patch

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.bundler import EvidenceBundler
from auto_developer.models import JobState, Task
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter
from auto_developer.runner import RunnerAdapter


def _init_git_repo(repo_dir: Path, verify_exit: int = 0, plan_text: str = "## ステップ\n- [ ] 1. Sample task\n") -> Path:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "TestUser"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo_dir, check=True)

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\nCore specification.\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text(plan_text, encoding="utf-8")

    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    verify_sh = scripts_dir / "verify_local.sh"
    verify_sh.write_text(f"#!/usr/bin/env bash\nexit {verify_exit}\n", encoding="utf-8")
    verify_sh.chmod(0o755)

    subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True, capture_output=True)
    return verify_sh


def _clean_subprocess_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing_pp}" if existing_pp else src_dir
    return env


def _read_bundle_report(bundle_path: Path) -> str:
    with zipfile.ZipFile(bundle_path, "r") as zf:
        self_names = zf.namelist()
        if "NIGHTLY_REPORT.md" not in self_names:
            raise AssertionError(f"NIGHTLY_REPORT.md missing from bundle {bundle_path}: {self_names}")
        return zf.read("NIGHTLY_REPORT.md").decode("utf-8")


class _SuccessRunner(RunnerAdapter):
    def __init__(self, repo_dir: Path, filename: str = "app.py", content: str = "x = 1\n") -> None:
        self.repo_dir = repo_dir
        self.filename = filename
        self.content = content

    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        target = self.repo_dir / self.filename
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# task {task.task_id}\n{self.content}", encoding="utf-8")
        return 0, f"created {self.filename}"

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        return True, "APPROVE: implemented\nDECISION: APPROVE"


class _RejectingCheckerRunner(RunnerAdapter):
    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir

    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        (self.repo_dir / "work.py").write_text("val = 42\n", encoding="utf-8")
        return 0, "wrote work.py"

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        return False, "REJECT: violates spec"


class _BoundaryViolatingRunner(RunnerAdapter):
    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir

    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        (self.repo_dir / "outside.py").write_text("bad = True\n", encoding="utf-8")
        return 0, "wrote outside.py"

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        return True, "APPROVE"


class _ExceptionRaisingRunner(RunnerAdapter):
    def __init__(self, repo_dir: Path, exc_message: str = "unexpected runner boom") -> None:
        self.repo_dir = repo_dir
        self.exc_message = exc_message

    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        (self.repo_dir / "partial.py").write_text("wip = 1\n", encoding="utf-8")
        raise RuntimeError(self.exc_message)

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        return True, "APPROVE"


class TestRpt6ReportAndBundleOrder(unittest.TestCase):
    """RPT-6: auto-dev run / NightlyOrchestrator.run でのレポートと Bundle の生成順序。"""

    def _spy_order(self) -> tuple[list[str], Any, Any]:
        call_order: list[str] = []
        orig_write_report = MorningReporter.write_report
        orig_create_bundle = EvidenceBundler.create_bundle

        def wrapped_write_report(self_rep: MorningReporter, report_path: Path, summary_data: dict[str, Any], *args: Any, **kwargs: Any) -> Any:
            call_order.append("write_report")
            return orig_write_report(self_rep, report_path, summary_data, *args, **kwargs)

        def wrapped_create_bundle(self_bun: EvidenceBundler, *args: Any, **kwargs: Any) -> Path:
            call_order.append("create_bundle")
            return orig_create_bundle(self_bun, *args, **kwargs)

        return call_order, wrapped_write_report, wrapped_create_bundle

    def test_success_writes_report_with_bundle_path_before_creating_bundle(self) -> None:
        """成功時: 先に bundle_path を含むレポートを書いてから Bundle を作り、Bundle 内に最新レポートが入ること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=0)
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"

            call_order, wrapped_write, wrapped_create = self._spy_order()
            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_SuccessRunner(repo),
            )
            with patch.object(MorningReporter, "write_report", wrapped_write), patch.object(
                EvidenceBundler, "create_bundle", wrapped_create
            ):
                exit_code = orch.run()

            self.assertEqual(exit_code, 0)
            self.assertEqual(call_order, ["write_report", "create_bundle"])

            job_state = JobState.load(state_path)
            expected_rel = f".dev/bundles/bundle_{job_state.job_id}.zip"
            bundle_path = repo / expected_rel
            self.assertTrue(bundle_path.is_file(), f"Bundle not found at {bundle_path}")

            disk_report = report_path.read_text(encoding="utf-8")
            bundle_report = _read_bundle_report(bundle_path)
            self.assertEqual(bundle_report, disk_report)
            self.assertIn("COMPLETED", bundle_report)
            self.assertIn(expected_rel, bundle_report)

    def test_second_run_bundle_contains_fresh_report_without_stale_warning(self) -> None:
        """2回目以降の実行でも、Bundle 内の NIGHTLY_REPORT.md が最新 HEAD を含み stale 警告が出ないこと。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=0, plan_text="## ステップ\n- [ ] 1. First task\n")
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"

            orch1 = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_SuccessRunner(repo, filename="first.py", content="v = 1\n"),
            )
            self.assertEqual(orch1.run(), 0)
            first_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()

            # 2つ目のタスクをコミットして2回目を実行
            (repo / "PLAN.md").write_text(
                "## ステップ\n- [x] 1. First task\n- [ ] 2. Second task\n",
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "PLAN.md"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "plan: add task 2"], cwd=repo, check=True, capture_output=True)

            orch2 = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_SuccessRunner(repo, filename="second.py", content="v = 2\n"),
            )
            self.assertEqual(orch2.run(), 0)
            second_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertNotEqual(first_head, second_head)

            job_state2 = JobState.load(state_path)
            bundle2 = repo / ".dev" / "bundles" / f"bundle_{job_state2.job_id}.zip"
            self.assertTrue(bundle2.is_file())

            bundle2_report = _read_bundle_report(bundle2)
            self.assertIn(second_head[:7], bundle2_report)
            self.assertIn("Second task", bundle2_report)

            verify_res = EvidenceBundler(repo_dir=repo, plan_path=repo / "PLAN.md").verify_bundle(bundle2)
            self.assertEqual(verify_res.status, "VERIFIED", msg=f"errors={verify_res.errors}, warnings={verify_res.warnings}")
            stale_warnings = [w for w in verify_res.warnings if "stale" in w.lower()]
            self.assertEqual(stale_warnings, [], f"Unexpected stale warning: {verify_res.warnings}")

    def test_failure_verifier_twice_writes_report_before_bundle(self) -> None:
        """検証2回失敗時: レポートを書いてから Bundle を作り、Bundle に失敗結果と隔離ブランチ・bundle_path が入ること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=1)
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"

            call_order, wrapped_write, wrapped_create = self._spy_order()
            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_SuccessRunner(repo),
            )
            with patch.object(MorningReporter, "write_report", wrapped_write), patch.object(
                EvidenceBundler, "create_bundle", wrapped_create
            ):
                exit_code = orch.run()

            self.assertEqual(exit_code, 1)
            self.assertEqual(call_order, ["write_report", "create_bundle"])

            job_state = JobState.load(state_path)
            self.assertEqual(job_state.status, "FAILED")
            self.assertIsNotNone(job_state.quarantined_task)
            q_branch = job_state.quarantined_task["quarantined_branch"]

            expected_rel = f".dev/bundles/bundle_{job_state.job_id}.zip"
            bundle_path = repo / expected_rel
            self.assertTrue(bundle_path.is_file())

            bundle_report = _read_bundle_report(bundle_path)
            self.assertEqual(bundle_report, report_path.read_text(encoding="utf-8"))
            self.assertIn("STOPPED (FAIL-FAST)", bundle_report)
            self.assertIn(q_branch, bundle_report)
            self.assertIn(expected_rel, bundle_report)

    def test_failure_checker_reject_writes_report_before_bundle(self) -> None:
        """Checker 拒否時: レポートを書いてから Bundle を作り、Bundle 内に今回の失敗レポートが入ること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=0)
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"

            call_order, wrapped_write, wrapped_create = self._spy_order()
            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_RejectingCheckerRunner(repo),
            )
            with patch.object(MorningReporter, "write_report", wrapped_write), patch.object(
                EvidenceBundler, "create_bundle", wrapped_create
            ):
                exit_code = orch.run()

            self.assertEqual(exit_code, 1)
            self.assertEqual(call_order, ["write_report", "create_bundle"])

            job_state = JobState.load(state_path)
            expected_rel = f".dev/bundles/bundle_{job_state.job_id}.zip"
            bundle_path = repo / expected_rel
            self.assertTrue(bundle_path.is_file())

            bundle_report = _read_bundle_report(bundle_path)
            self.assertEqual(bundle_report, report_path.read_text(encoding="utf-8"))
            self.assertIn("STOPPED (FAIL-FAST)", bundle_report)
            self.assertIn(expected_rel, bundle_report)

    def test_failure_boundary_violation_writes_report_before_bundle(self) -> None:
        """境界違反時: レポートを書いてから Bundle を作り、Bundle 内に失敗結果と隔離情報が入ること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=0)
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"

            call_order, wrapped_write, wrapped_create = self._spy_order()
            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_BoundaryViolatingRunner(repo),
                owns_patterns=["allowed/**"],
            )
            with patch.object(MorningReporter, "write_report", wrapped_write), patch.object(
                EvidenceBundler, "create_bundle", wrapped_create
            ):
                exit_code = orch.run()

            self.assertEqual(exit_code, 1)
            self.assertEqual(call_order, ["write_report", "create_bundle"])

            job_state = JobState.load(state_path)
            expected_rel = f".dev/bundles/bundle_{job_state.job_id}.zip"
            bundle_path = repo / expected_rel
            self.assertTrue(bundle_path.is_file())

            bundle_report = _read_bundle_report(bundle_path)
            self.assertEqual(bundle_report, report_path.read_text(encoding="utf-8"))
            self.assertIn("BOUNDARY_VIOLATION", bundle_report)
            self.assertIn(expected_rel, bundle_report)

    def test_failure_unexpected_exception_writes_report_before_bundle_and_preserves_worktree(self) -> None:
        """例外終了時: 作業ツリーを破壊せず（INV-3）、レポートを書いてから Bundle を作り、元の例外を再送出すること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=0)
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"

            call_order, wrapped_write, wrapped_create = self._spy_order()
            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_ExceptionRaisingRunner(repo, exc_message="boom during maker"),
            )
            with patch.object(MorningReporter, "write_report", wrapped_write), patch.object(
                EvidenceBundler, "create_bundle", wrapped_create
            ), self.assertRaisesRegex(RuntimeError, "boom during maker"):
                orch.run()

            self.assertEqual(call_order, ["write_report", "create_bundle"])
            # INV-3: 例外で終了した際に未退避の作業ファイルを破壊的に削除しないこと
            self.assertTrue((repo / "partial.py").exists())

            job_state = JobState.load(state_path)
            expected_rel = f".dev/bundles/bundle_{job_state.job_id}.zip"
            bundle_path = repo / expected_rel
            self.assertTrue(bundle_path.is_file())

            bundle_report = _read_bundle_report(bundle_path)
            self.assertEqual(bundle_report, report_path.read_text(encoding="utf-8"))
            self.assertIn("FAILED", bundle_report)
            self.assertIn("boom during maker", bundle_report)
            self.assertIn(expected_rel, bundle_report)

    def test_report_write_failure_skips_bundle_and_preserves_failure_exit_code_and_quarantine(self) -> None:
        """失敗時に write_report が例外を起こした場合、create_bundle を呼ばず、終了コード 1・隔離状態（INV-3）が壊れないこと。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=1)
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"
            report_path.write_text("# STALE REPORT\n", encoding="utf-8")

            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_SuccessRunner(repo),
            )
            with patch.object(MorningReporter, "write_report", side_effect=OSError("disk full")), patch.object(
                EvidenceBundler, "create_bundle"
            ) as mock_create_bundle:
                exit_code = orch.run()

            self.assertEqual(exit_code, 1)
            mock_create_bundle.assert_not_called()
            status_out = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertEqual(status_out, "")

            job_state = JobState.load(state_path)
            self.assertEqual(job_state.status, "FAILED")
            self.assertIsNotNone(job_state.quarantined_task)
            bundle_path = repo / ".dev" / "bundles" / f"bundle_{job_state.job_id}.zip"
            self.assertFalse(bundle_path.exists())

    def test_bundle_creation_failure_does_not_break_failure_exit_code_quarantine_or_report(self) -> None:
        """失敗時に create_bundle が例外を起こしても、終了コード 1・隔離状態（INV-3）・レポート書き込みが壊れないこと。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=1)
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"

            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_SuccessRunner(repo),
            )
            with patch.object(EvidenceBundler, "create_bundle", side_effect=RuntimeError("zip error")):
                exit_code = orch.run()

            self.assertEqual(exit_code, 1)
            status_out = subprocess.run(
                ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True
            ).stdout.strip()
            self.assertEqual(status_out, "")

            job_state = JobState.load(state_path)
            self.assertEqual(job_state.status, "FAILED")
            self.assertIsNotNone(job_state.quarantined_task)
            self.assertTrue(report_path.is_file())
            self.assertIn("STOPPED (FAIL-FAST)", report_path.read_text(encoding="utf-8"))

    def test_success_report_or_bundle_failure_preserves_exit_zero(self) -> None:
        """成功時に write_report 失敗なら create_bundle をスキップし、どちらも終了コード 0 を維持すること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo1 = Path(tmpdir) / "repo1"
            verify_sh1 = _init_git_repo(repo1, verify_exit=0)
            state1 = repo1 / ".dev" / "job_state.json"
            (repo1 / "NIGHTLY_REPORT.md").write_text("# STALE REPORT\n", encoding="utf-8")
            orch1 = NightlyOrchestrator(
                repo_dir=repo1,
                plan_path=repo1 / "PLAN.md",
                spec_path=repo1 / "SPEC.md",
                verify_script=verify_sh1,
                report_path=repo1 / "NIGHTLY_REPORT.md",
                state_path=state1,
                runner_adapter=_SuccessRunner(repo1),
            )
            with patch.object(MorningReporter, "write_report", side_effect=OSError("cannot write")), patch.object(
                EvidenceBundler, "create_bundle"
            ) as mock_create_bundle:
                self.assertEqual(orch1.run(), 0)
            mock_create_bundle.assert_not_called()
            js1 = JobState.load(state1)
            self.assertFalse((repo1 / ".dev" / "bundles" / f"bundle_{js1.job_id}.zip").exists())

            repo2 = Path(tmpdir) / "repo2"
            verify_sh2 = _init_git_repo(repo2, verify_exit=0)
            state2 = repo2 / ".dev" / "job_state.json"
            report2 = repo2 / "NIGHTLY_REPORT.md"
            orch2 = NightlyOrchestrator(
                repo_dir=repo2,
                plan_path=repo2 / "PLAN.md",
                spec_path=repo2 / "SPEC.md",
                verify_script=verify_sh2,
                report_path=report2,
                state_path=state2,
                runner_adapter=_SuccessRunner(repo2),
            )
            with patch.object(EvidenceBundler, "create_bundle", side_effect=RuntimeError("bundle boom")):
                self.assertEqual(orch2.run(), 0)
            self.assertTrue(report2.is_file())
            self.assertIn("COMPLETED", report2.read_text(encoding="utf-8"))

    def test_evidence_verify_failure_writes_report_before_bundle_and_keeps_exit_3(self) -> None:
        """Evidence 検証失敗（終了コード 3）時にもレポートを書いてから Bundle を作り、終了コード 3 と作業ツリー（INV-3）を保つこと。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir) / "repo"
            verify_sh = _init_git_repo(repo, verify_exit=0)
            state_path = repo / ".dev" / "job_state.json"
            report_path = repo / "NIGHTLY_REPORT.md"

            call_order, wrapped_write, wrapped_create = self._spy_order()
            orch = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=report_path,
                state_path=state_path,
                runner_adapter=_SuccessRunner(repo),
            )
            with patch.object(orch.evidence_store, "verify_and_consume", return_value=False), patch.object(
                MorningReporter, "write_report", wrapped_write
            ), patch.object(EvidenceBundler, "create_bundle", wrapped_create):
                exit_code = orch.run()

            self.assertEqual(exit_code, 3)
            self.assertEqual(call_order, ["write_report", "create_bundle"])
            self.assertTrue((repo / "app.py").exists())
            job_state = JobState.load(state_path)
            bundle_path = repo / ".dev" / "bundles" / f"bundle_{job_state.job_id}.zip"
            self.assertTrue(bundle_path.is_file())
            bundle_report = _read_bundle_report(bundle_path)
            self.assertEqual(bundle_report, report_path.read_text(encoding="utf-8"))
            self.assertIn("EVIDENCE_ERROR", bundle_report)

    def test_cli_auto_dev_run_writes_report_before_bundle_on_success_and_failure(self) -> None:
        """CLI (`python -m auto_developer.cli run`) 経由で成功時・失敗時ともに最新レポート入りの Bundle が作られること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            # 1. 成功ケース
            repo_ok = Path(tmpdir) / "repo_ok"
            _init_git_repo(repo_ok, verify_exit=0)
            import shlex

            py_exe = shlex.quote(sys.executable)
            maker_ok = (
                f"{py_exe} -c \"from pathlib import Path; Path('impl.py').write_text('ok=1\\n', encoding='utf-8')\""
            )
            checker_ok = f"{py_exe} -c \"print('DECISION: APPROVE')\""
            proc_ok = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "auto_developer.cli",
                    "--work-dir",
                    str(repo_ok),
                    "run",
                    "--maker-cmd",
                    maker_ok,
                    "--maker-model",
                    "gemini-3.8-flash",
                    "--checker-cmd",
                    checker_ok,
                    "--checker-model",
                    "claude-opus-5.5",
                ],
                cwd=repo_ok,
                env=_clean_subprocess_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_ok.returncode, 0, msg=proc_ok.stderr + proc_ok.stdout)
            ok_state = json.loads((repo_ok / ".dev" / "job_state.json").read_text(encoding="utf-8"))
            ok_bundle = repo_ok / ".dev" / "bundles" / f"bundle_{ok_state['job_id']}.zip"
            self.assertTrue(ok_bundle.is_file())
            ok_bundle_report = _read_bundle_report(ok_bundle)
            self.assertIn("COMPLETED", ok_bundle_report)
            self.assertEqual(ok_bundle_report, (repo_ok / "NIGHTLY_REPORT.md").read_text(encoding="utf-8"))

            # 2. 失敗ケース（検証失敗）
            repo_ng = Path(tmpdir) / "repo_ng"
            _init_git_repo(repo_ng, verify_exit=1)
            proc_ng = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "auto_developer.cli",
                    "--work-dir",
                    str(repo_ng),
                    "run",
                    "--maker-cmd",
                    maker_ok,
                    "--maker-model",
                    "gemini-3.8-flash",
                    "--checker-cmd",
                    checker_ok,
                    "--checker-model",
                    "claude-opus-5.5",
                ],
                cwd=repo_ng,
                env=_clean_subprocess_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(proc_ng.returncode, 1, msg=proc_ng.stderr + proc_ng.stdout)
            ng_state = json.loads((repo_ng / ".dev" / "job_state.json").read_text(encoding="utf-8"))
            ng_bundle = repo_ng / ".dev" / "bundles" / f"bundle_{ng_state['job_id']}.zip"
            self.assertTrue(ng_bundle.is_file())
            ng_bundle_report = _read_bundle_report(ng_bundle)
            self.assertIn("STOPPED (FAIL-FAST)", ng_bundle_report)
            self.assertIn(ng_state["quarantined_task"]["quarantined_branch"], ng_bundle_report)
            self.assertEqual(ng_bundle_report, (repo_ng / "NIGHTLY_REPORT.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
