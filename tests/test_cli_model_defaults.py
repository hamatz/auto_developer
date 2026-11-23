from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.cli import main
from auto_developer.multi_team import run_teams_job
from auto_developer.orchestrator import NightlyOrchestrator


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo), *args],
        cwd=repo,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _init_single_repo(repo: Path) -> None:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "TestUser")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text(
        ".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n*.py[cod]\n",
        encoding="utf-8",
    )
    (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (repo / "PLAN.md").write_text(
        "## ステップ\n- [ ] 1. Create module\n",
        encoding="utf-8",
    )
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    verify_sh = scripts_dir / "verify_local.sh"
    verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    verify_sh.chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")


def _init_teams_repo(repo: Path) -> None:
    _init_single_repo(repo)
    (repo / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "core" / "__init__.py").write_text("CORE = 1\n", encoding="utf-8")
    (repo / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "ui" / "__init__.py").write_text("UI = 1\n", encoding="utf-8")
    (repo / "plans").mkdir(parents=True, exist_ok=True)
    (repo / "plans" / "core.md").write_text("## ステップ\n- [ ] 1. Core task\n", encoding="utf-8")
    (repo / "plans" / "ui.md").write_text("## ステップ\n- [ ] 1. UI task\n", encoding="utf-8")
    teams_json = {
        "version": 1,
        "project": "sample",
        "base": {"ref": "main"},
        "teams": [
            {"id": "core", "plan": "plans/core.md", "owns": ["src/core/**"], "depends_on": []},
            {"id": "ui", "plan": "plans/ui.md", "owns": ["src/ui/**"], "depends_on": []},
        ],
    }
    (repo / "teams.json").write_text(json.dumps(teams_json, indent=2) + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init teams")


class TestCliModelDefaultsUnit(unittest.TestCase):
    """DX-2: CLI の run / teams run で外部コマンド指定時にモデル名省略を終了コード 2 で拒否する。"""

    def test_run_rejects_omitted_models_when_external_cmds_given(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            stderr_buf = io.StringIO()
            with (
                patch("sys.stderr", stderr_buf),
                patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls,
            ):
                ret = main(
                    [
                        "--work-dir",
                        str(repo),
                        "run",
                        "--maker-cmd",
                        "echo maker",
                        "--checker-cmd",
                        "echo checker",
                    ]
                )
            self.assertEqual(ret, 2)
            err = stderr_buf.getvalue()
            self.assertIn("--maker-model", err)
            self.assertIn("--checker-model", err)
            mock_orch_cls.assert_not_called()

    def test_run_rejects_omitted_maker_model_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            stderr_buf = io.StringIO()
            with (
                patch("sys.stderr", stderr_buf),
                patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls,
            ):
                ret = main(
                    [
                        "--work-dir",
                        str(repo),
                        "run",
                        "--maker-cmd",
                        "echo maker",
                        "--checker-cmd",
                        "echo checker",
                        "--checker-model",
                        "claude-opus-5-5-medium",
                    ]
                )
            self.assertEqual(ret, 2)
            err = stderr_buf.getvalue()
            self.assertIn("--maker-model", err)
            self.assertNotIn("--checker-model", err)
            mock_orch_cls.assert_not_called()

    def test_run_rejects_omitted_checker_model_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            stderr_buf = io.StringIO()
            with (
                patch("sys.stderr", stderr_buf),
                patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls,
            ):
                ret = main(
                    [
                        "--work-dir",
                        str(repo),
                        "run",
                        "--maker-cmd",
                        "echo maker",
                        "--checker-cmd",
                        "echo checker",
                        "--maker-model",
                        "gemini-3.8-flash",
                    ]
                )
            self.assertEqual(ret, 2)
            err = stderr_buf.getvalue()
            self.assertIn("--checker-model", err)
            self.assertNotIn("--maker-model", err)
            mock_orch_cls.assert_not_called()

    def test_run_with_enable_audit_rejects_omitted_auditor_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            stderr_buf = io.StringIO()
            with (
                patch("sys.stderr", stderr_buf),
                patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls,
            ):
                ret = main(
                    [
                        "--work-dir",
                        str(repo),
                        "run",
                        "--maker-cmd",
                        "echo maker",
                        "--checker-cmd",
                        "echo checker",
                        "--maker-model",
                        "gemini-3.8-flash",
                        "--checker-model",
                        "claude-opus-5-5-medium",
                        "--enable-audit",
                        "--auditor-cmd",
                        "echo auditor",
                    ]
                )
            self.assertEqual(ret, 2)
            err = stderr_buf.getvalue()
            self.assertIn("--auditor-model", err)
            mock_orch_cls.assert_not_called()

    def test_run_does_not_pass_implicit_vendor_auditor_model_when_audit_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls:
                mock_inst = MagicMock()
                mock_inst.run.return_value = 0
                mock_orch_cls.return_value = mock_inst
                ret = main(
                    [
                        "--work-dir",
                        str(repo),
                        "run",
                        "--maker-cmd",
                        "echo maker",
                        "--checker-cmd",
                        "echo checker",
                        "--maker-model",
                        "my-maker-a",
                        "--checker-model",
                        "my-checker-b",
                    ]
                )
            self.assertEqual(ret, 0)
            mock_orch_cls.assert_called_once()
            _, kwargs = mock_orch_cls.call_args
            self.assertEqual(kwargs.get("maker_model"), "my-maker-a")
            self.assertEqual(kwargs.get("checker_model"), "my-checker-b")
            self.assertIsNone(kwargs.get("auditor_model"))

    def test_run_preserves_top_level_model_flags_before_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls:
                mock_inst = MagicMock()
                mock_inst.run.return_value = 0
                mock_orch_cls.return_value = mock_inst
                ret = main(
                    [
                        "--work-dir",
                        str(repo),
                        "--maker-cmd",
                        "echo maker",
                        "--checker-cmd",
                        "echo checker",
                        "--maker-model",
                        "gemini-3.8-flash",
                        "--checker-model",
                        "claude-opus-5-5-medium",
                        "run",
                    ]
                )
            self.assertEqual(ret, 0)
            mock_orch_cls.assert_called_once()
            _, kwargs = mock_orch_cls.call_args
            self.assertEqual(kwargs.get("maker_model"), "gemini-3.8-flash")
            self.assertEqual(kwargs.get("checker_model"), "claude-opus-5-5-medium")

    def test_run_mock_uses_traditional_default_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with patch("auto_developer.cli.NightlyOrchestrator") as mock_orch_cls:
                mock_inst = MagicMock()
                mock_inst.run.return_value = 0
                mock_orch_cls.return_value = mock_inst
                ret = main(["--work-dir", str(repo), "run", "--mock"])
            self.assertEqual(ret, 0)
            mock_orch_cls.assert_called_once()
            _, kwargs = mock_orch_cls.call_args
            self.assertEqual(kwargs.get("maker_model"), "gemini-3.8-flash")
            self.assertEqual(kwargs.get("checker_model"), "claude-opus-5.5")
            self.assertEqual(kwargs.get("auditor_model"), "claude-opus-5.5")

    def test_library_orchestrator_defaults_unchanged(self) -> None:
        orch = NightlyOrchestrator()
        self.assertIsNone(orch.maker_model)
        self.assertIsNone(orch.checker_model)
        self.assertIsNone(orch.auditor_model)

    def test_teams_run_rejects_omitted_models_without_calling_run_teams_job(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            stderr_buf = io.StringIO()
            with (
                patch("sys.stderr", stderr_buf),
                patch("auto_developer.cli.run_teams_job") as mock_run_teams,
            ):
                ret = main(
                    [
                        "--work-dir",
                        str(repo),
                        "teams",
                        "run",
                        "--maker-cmd",
                        "echo maker",
                        "--checker-cmd",
                        "echo checker",
                    ]
                )
            self.assertEqual(ret, 2)
            err = stderr_buf.getvalue()
            self.assertIn("--maker-model", err)
            self.assertIn("--checker-model", err)
            mock_run_teams.assert_not_called()

    def test_teams_run_mock_uses_traditional_default_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            with (
                patch("sys.stdout", io.StringIO()),
                patch(
                    "auto_developer.cli.run_teams_job",
                    return_value=({"job_id": "j1", "rc_tag": "rc/j1", "rc_commit": "abc", "teams": []}, 0, ""),
                ) as mock_run_teams,
            ):
                ret = main(["--work-dir", str(repo), "teams", "run", "--mock", "--json"])
            self.assertEqual(ret, 0)
            mock_run_teams.assert_called_once()
            _, kwargs = mock_run_teams.call_args
            self.assertEqual(kwargs.get("maker_model"), "gemini-3.8-flash")
            self.assertEqual(kwargs.get("checker_model"), "claude-opus-5.5")
            self.assertTrue(kwargs.get("is_mock"))

    def test_run_teams_job_library_defaults_unchanged(self) -> None:
        import inspect

        sig = inspect.signature(run_teams_job)
        self.assertEqual(sig.parameters["maker_model"].default, "gemini-3.8-flash")
        self.assertEqual(sig.parameters["checker_model"].default, "claude-opus-5.5")


class TestCliModelDefaultsAcceptance(unittest.TestCase):
    """DX-2: E2E サブプロセスで run / teams run のモデル名省略拒否と --mock 既定値を検証する。"""

    def test_e2e_run_rejects_omitted_models_and_allows_mock_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_single_repo(repo)

            # 1. 外部コマンド指定でモデル名なし → 終了コード 2
            res_missing = _run_cli(
                repo,
                "run",
                "--maker-cmd",
                "python3 -c \"from pathlib import Path; Path('a.py').write_text('x=1\\n')\"",
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
            )
            self.assertEqual(res_missing.returncode, 2, res_missing.stderr)
            self.assertIn("--maker-model", res_missing.stderr)
            self.assertIn("--checker-model", res_missing.stderr)
            self.assertFalse((repo / ".dev" / "job_state.json").exists())
            self.assertFalse((repo / "a.py").exists())

            # 2. --enable-audit で --auditor-cmd を指定し --auditor-model なし → 終了コード 2
            res_missing_auditor = _run_cli(
                repo,
                "run",
                "--maker-cmd",
                "python3 -c \"from pathlib import Path; Path('a.py').write_text('x=1\\n')\"",
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
                "--maker-model",
                "gemini-3.8-flash",
                "--checker-model",
                "claude-opus-5-5-medium",
                "--enable-audit",
                "--auditor-cmd",
                "echo audit",
            )
            self.assertEqual(res_missing_auditor.returncode, 2, res_missing_auditor.stderr)
            self.assertIn("--auditor-model", res_missing_auditor.stderr)
            self.assertFalse((repo / ".dev" / "job_state.json").exists())

            # 3. --mock ならモデル名省略でも従来どおり実行できる（--dry-run で終了コード 0）
            res_mock = _run_cli(repo, "run", "--mock", "--dry-run")
            self.assertEqual(res_mock.returncode, 0, f"stdout={res_mock.stdout}\nstderr={res_mock.stderr}")

    def test_e2e_teams_run_rejects_omitted_models_before_prepare_and_allows_mock(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_teams_repo(repo)

            # 1. teams run でモデル名なし → 終了コード 2、worktree 未作成
            res_both = _run_cli(
                repo,
                "teams",
                "run",
                "--job-id",
                "job-dx2-both",
                "--maker-cmd",
                "true",
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
            )
            self.assertEqual(res_both.returncode, 2, res_both.stderr)
            self.assertIn("--maker-model", res_both.stderr)
            self.assertIn("--checker-model", res_both.stderr)
            self.assertFalse((repo / ".dev" / "worktrees" / "core").exists())

            # 2. --maker-model のみ省略 → 終了コード 2
            res_maker = _run_cli(
                repo,
                "teams",
                "run",
                "--job-id",
                "job-dx2-maker",
                "--maker-cmd",
                "true",
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
                "--checker-model",
                "claude-opus-5-5-medium",
            )
            self.assertEqual(res_maker.returncode, 2, res_maker.stderr)
            self.assertIn("--maker-model", res_maker.stderr)
            self.assertFalse((repo / ".dev" / "worktrees" / "core").exists())

            # 3. --checker-model のみ省略 → 終了コード 2
            res_checker = _run_cli(
                repo,
                "teams",
                "run",
                "--job-id",
                "job-dx2-checker",
                "--maker-cmd",
                "true",
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
                "--maker-model",
                "gemini-3.8-flash",
            )
            self.assertEqual(res_checker.returncode, 2, res_checker.stderr)
            self.assertIn("--checker-model", res_checker.stderr)
            self.assertFalse((repo / ".dev" / "worktrees" / "core").exists())

            # 4. --mock ならモデル名省略でも設定検査を通過し、既定モデルがジョブ記録に使われる
            res_mock = _run_cli(repo, "teams", "run", "--job-id", "job-dx2-mock", "--mock")
            self.assertNotEqual(res_mock.returncode, 2, f"stdout={res_mock.stdout}\nstderr={res_mock.stderr}")
            job_json = repo / ".dev" / "integration_jobs" / "job-dx2-mock" / "job.json"
            self.assertTrue(job_json.is_file(), f"Missing job record: {job_json}")
            job_data = json.loads(job_json.read_text(encoding="utf-8"))
            self.assertEqual(job_data.get("maker_model"), "gemini-3.8-flash")
            self.assertEqual(job_data.get("checker_model"), "claude-opus-5.5")


if __name__ == "__main__":
    unittest.main()
