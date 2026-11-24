from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
NIGHTLY_RUN_SKILL = ROOT_DIR / "skills" / "nightly-run" / "SKILL.md"


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else str(SRC_DIR)
    return env


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", *args],
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


def _git(args: list[str], cwd: Path) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(repo_dir: Path, verify_script_content: str) -> str:
    _git(["init", "-b", "main"], repo_dir)
    _git(["config", "user.name", "Test User"], repo_dir)
    _git(["config", "user.email", "test@example.com"], repo_dir)

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\n- Req 1\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# PLAN\n- [ ] 1. Task one\n", encoding="utf-8")
    scripts_dir = repo_dir / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    verify_sh = scripts_dir / "verify_local.sh"
    verify_sh.write_text(verify_script_content, encoding="utf-8")
    verify_sh.chmod(0o755)

    _git(["add", "."], repo_dir)
    _git(["commit", "-m", "init"], repo_dir)
    return _git(["rev-parse", "HEAD"], repo_dir)


class TestDX13VerifyTimeout(unittest.TestCase):
    """DX-13: 検証の制限時間の優先順位、スクリプト行、打ち切り案内、スキル記載。"""

    def test_verify_step_subcommand_timeout_overrides_global_timeout(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            sha = _init_repo(
                repo,
                "#!/usr/bin/env bash\nsleep 3\nexit 0\n",
            )
            proc = _run_cli(
                ["--timeout", "10", "verify-step", "--checkpoint", sha, "--task-id", "1", "--timeout", "1"],
                cwd=repo,
            )
            self.assertEqual(proc.returncode, 124, proc.stderr)
            self.assertIn("VERIFICATION FAILED (Exit 124):\nVerification timed out after 1s", proc.stderr)
            self.assertIn("--timeout", proc.stderr)
            self.assertIn("verify-timeout=", proc.stderr)

    def test_verify_step_global_timeout_still_works(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            sha = _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=10\nsleep 3\nexit 0\n",
            )
            proc = _run_cli(
                ["--timeout", "1", "verify-step", "--checkpoint", sha, "--task-id", "1"],
                cwd=repo,
            )
            self.assertEqual(proc.returncode, 124, proc.stderr)
            self.assertIn("Verification timed out after 1s", proc.stderr)
            self.assertIn("--timeout", proc.stderr)

    def test_verify_step_uses_script_directive_within_first_20_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            padding = "\n".join(f"# line {i}" for i in range(3, 20))
            script = f"#!/usr/bin/env bash\n# comment\n{padding}\n# auto-dev: verify-timeout=1\nsleep 3\nexit 0\n"
            self.assertEqual(script.splitlines()[19], "# auto-dev: verify-timeout=1")
            sha = _init_repo(repo, script)

            proc = _run_cli(
                ["verify-step", "--checkpoint", sha, "--task-id", "1"],
                cwd=repo,
            )
            self.assertEqual(proc.returncode, 124, proc.stderr)
            self.assertIn("Verification timed out after 1s", proc.stderr)
            self.assertIn("verify-timeout=1", proc.stderr)
            self.assertIn("--timeout", proc.stderr)

    def test_verify_step_ignores_script_directive_after_line_20(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            padding = "\n".join(f"# line {i}" for i in range(2, 21))
            script = f"#!/usr/bin/env bash\n{padding}\n# auto-dev: verify-timeout=1\nexit 0\n"
            self.assertEqual(script.splitlines()[20], "# auto-dev: verify-timeout=1")
            sha = _init_repo(repo, script)

            proc = _run_cli(
                ["verify-step", "--checkpoint", sha, "--task-id", "1"],
                cwd=repo,
            )
            self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_verify_step_rejects_invalid_directive_and_non_positive_timeout(self) -> None:
        for bad_line in (
            "# auto-dev: verify-timeout=0",
            "# auto-dev: verify-timeout=-5",
            "# auto-dev: verify-timeout=abc",
            "# auto-dev: verify-timeout=",
        ):
            with self.subTest(bad_line=bad_line), tempfile.TemporaryDirectory() as tmp:
                repo = Path(tmp)
                sha = _init_repo(repo, f"#!/usr/bin/env bash\n{bad_line}\nexit 0\n")
                proc = _run_cli(
                    ["verify-step", "--checkpoint", sha, "--task-id", "1"],
                    cwd=repo,
                )
                self.assertEqual(proc.returncode, 2, proc.stderr)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            sha = _init_repo(repo, "#!/usr/bin/env bash\nexit 0\n")
            proc = _run_cli(
                ["verify-step", "--checkpoint", sha, "--task-id", "1", "--timeout", "0"],
                cwd=repo,
            )
            self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_verify_step_default_timeout_source_and_extension_hint_real_path(self) -> None:
        sys.path.insert(0, str(SRC_DIR))
        from auto_developer import cli as cli_mod

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            sha = _init_repo(
                repo,
                "#!/usr/bin/env bash\nsleep 3\nexit 0\n",
            )
            stderr_buf = io.StringIO()
            with (
                mock.patch("auto_developer.verifier.DEFAULT_VERIFY_TIMEOUT_SEC", 1),
                contextlib.redirect_stderr(stderr_buf),
            ):
                code = cli_mod.main(
                    ["--work-dir", str(repo), "verify-step", "--checkpoint", sha, "--task-id", "1"]
                )
            self.assertEqual(code, 124)
            err_out = stderr_buf.getvalue()
            lines = err_out.splitlines()
            self.assertEqual(lines[0], "VERIFICATION FAILED (Exit 124):")
            self.assertTrue(lines[1].startswith("Verification timed out after 1s"), lines)
            self.assertIn("default", err_out.lower())
            self.assertIn("--timeout", err_out)
            self.assertIn("verify-timeout=", err_out)

    def test_run_uses_directive_and_cli_timeout_precedence(self) -> None:
        maker_cmd = "python3 -c \"from pathlib import Path; Path('app.py').write_text('x=1\\n')\""
        checker_cmd = "python3 -c \"print('DECISION: APPROVE')\""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=1\nsleep 3\nexit 0\n",
            )
            # Without --timeout, run uses directive (1s) and times out -> exit 1
            proc = _run_cli(
                [
                    "run",
                    "--maker-cmd",
                    maker_cmd,
                    "--checker-cmd",
                    checker_cmd,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                ],
                cwd=repo,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Exit 124", proc.stderr)
            report_text = (repo / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("Verification timed out after 1s", report_text)
            self.assertIn("verify_local.sh directive", report_text)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=10\nsleep 3\nexit 0\n",
            )
            # Global --timeout 10 overridden by subcommand --timeout 1
            proc = _run_cli(
                [
                    "--timeout",
                    "10",
                    "run",
                    "--timeout",
                    "1",
                    "--maker-cmd",
                    maker_cmd,
                    "--checker-cmd",
                    checker_cmd,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                ],
                cwd=repo,
            )
            self.assertNotEqual(proc.returncode, 0)
            self.assertIn("Exit 124", proc.stderr)
            report_text = (repo / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("Verification timed out after 1s", report_text)
            self.assertIn("--timeout option", report_text)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=0\nexit 0\n",
            )
            proc = _run_cli(["run", "--mock"], cwd=repo)
            self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_teams_run_uses_directive_and_rejects_invalid_directive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=0\nexit 0\n",
            )
            (repo / "plans").mkdir(parents=True, exist_ok=True)
            (repo / "plans" / "core.md").write_text("# Core\n- [ ] 1. Core task\n", encoding="utf-8")
            teams_cfg = {
                "version": 1,
                "project": "test-project",
                "base": {"ref": "main"},
                "teams": [{"id": "core", "plan": "plans/core.md", "owns": ["src/**"], "depends_on": []}],
            }
            (repo / "teams.json").write_text(json.dumps(teams_cfg), encoding="utf-8")
            _git(["add", "."], repo)
            _git(["commit", "-m", "add teams"], repo)

            proc = _run_cli(["teams", "run", "--mock"], cwd=repo)
            self.assertEqual(proc.returncode, 2, proc.stderr)

    def test_teams_run_forwards_timeout_to_worker_cmd_and_resolves_directive(self) -> None:
        sys.path.insert(0, str(SRC_DIR))
        from auto_developer.multi_team import (
            MultiTeamOrchestrator,
            TeamExecutionResult,
            run_teams_job,
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=15\nexit 0\n",
            )
            (repo / "plans").mkdir(parents=True, exist_ok=True)
            (repo / "plans" / "core.md").write_text("# Core\n- [x] 1. Core task\n", encoding="utf-8")
            teams_cfg = {
                "version": 1,
                "project": "test-project",
                "base": {"ref": "main"},
                "teams": [{"id": "core", "plan": "plans/core.md", "owns": ["src/**"], "depends_on": []}],
            }
            (repo / "teams.json").write_text(json.dumps(teams_cfg), encoding="utf-8")
            _git(["add", "."], repo)
            _git(["commit", "-m", "add teams"], repo)

            captured_cmds: list[list[str]] = []

            def fake_run_workers(
                _self: MultiTeamOrchestrator,
                config: object,
                worker_cmd_factory: object,
                **_kwargs: object,
            ) -> dict[str, TeamExecutionResult]:
                wt_dir = repo / ".dev" / "worktrees" / "core"
                cmd = worker_cmd_factory("core", wt_dir)  # type: ignore[operator]
                captured_cmds.append(cmd)
                return {
                    "core": TeamExecutionResult(
                        team_id="core",
                        success=True,
                        status="NO_CHANGES",
                        branch="team/core",
                    )
                }

            with mock.patch.object(MultiTeamOrchestrator, "run_workers", new=fake_run_workers):
                payload, code, err = run_teams_job(
                    repo,
                    job_id="job-fwd-1",
                    is_mock=True,
                    verify_timeout=42,
                )
            self.assertEqual(code, 0, err)
            self.assertIsNotNone(payload)
            self.assertEqual(len(captured_cmds), 1)
            self.assertIn("--timeout", captured_cmds[0])
            idx = captured_cmds[0].index("--timeout")
            self.assertEqual(captured_cmds[0][idx + 1], "42")

    def test_teams_run_worker_timeout_precedence_directive_vs_cli(self) -> None:
        maker_cmd = "python3 -c \"from pathlib import Path; Path('src').mkdir(exist_ok=True); Path('src/x.py').write_text('x=1\\n')\""
        checker_cmd = "python3 -c \"print('DECISION: APPROVE')\""

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=1\nsleep 3\nexit 0\n",
            )
            (repo / "plans").mkdir(parents=True, exist_ok=True)
            (repo / "plans" / "core.md").write_text("# Core\n- [ ] 1. Core task\n", encoding="utf-8")
            teams_cfg = {
                "version": 1,
                "project": "test-project",
                "base": {"ref": "main"},
                "teams": [{"id": "core", "plan": "plans/core.md", "owns": ["src/**"], "depends_on": []}],
            }
            (repo / "teams.json").write_text(json.dumps(teams_cfg), encoding="utf-8")
            _git(["add", "."], repo)
            _git(["commit", "-m", "add teams"], repo)

            # Without --timeout, worker uses directive (1s) and times out with exit 124
            proc = _run_cli(
                [
                    "teams",
                    "run",
                    "--job-id",
                    "job-w-dir",
                    "--maker-cmd",
                    maker_cmd,
                    "--checker-cmd",
                    checker_cmd,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                ],
                cwd=repo,
            )
            self.assertNotEqual(proc.returncode, 0)
            wt_report = (repo / ".dev" / "worktrees" / "core" / "NIGHTLY_REPORT.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Verification timed out after 1s", wt_report)
            self.assertIn("verify_local.sh directive", wt_report)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=10\nsleep 3\nexit 0\n",
            )
            (repo / "plans").mkdir(parents=True, exist_ok=True)
            (repo / "plans" / "core.md").write_text("# Core\n- [ ] 1. Core task\n", encoding="utf-8")
            teams_cfg = {
                "version": 1,
                "project": "test-project",
                "base": {"ref": "main"},
                "teams": [{"id": "core", "plan": "plans/core.md", "owns": ["src/**"], "depends_on": []}],
            }
            (repo / "teams.json").write_text(json.dumps(teams_cfg), encoding="utf-8")
            _git(["add", "."], repo)
            _git(["commit", "-m", "add teams"], repo)

            # Explicit --timeout 1 overrides directive (10s) in worker
            proc = _run_cli(
                [
                    "teams",
                    "run",
                    "--job-id",
                    "job-w-cli",
                    "--timeout",
                    "1",
                    "--maker-cmd",
                    maker_cmd,
                    "--checker-cmd",
                    checker_cmd,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                ],
                cwd=repo,
            )
            self.assertNotEqual(proc.returncode, 0)
            wt_report = (repo / ".dev" / "worktrees" / "core" / "NIGHTLY_REPORT.md").read_text(
                encoding="utf-8"
            )
            self.assertIn("Verification timed out after 1s", wt_report)
            self.assertIn("--timeout option", wt_report)

    def test_integration_gate_resolves_timeout_from_integration_verify_script(self) -> None:
        sys.path.insert(0, str(SRC_DIR))
        from auto_developer.bundler import EvidenceBundler
        from auto_developer.multi_team import (
            MultiTeamConfig,
            MultiTeamOrchestrator,
            TeamExecutionResult,
            TeamWorkerSpec,
        )

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            # verify_local.sh has 10s, but integration_verify_script (custom_gate.sh) has 1s
            _init_repo(
                repo,
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=10\nexit 0\n",
            )
            custom_gate = repo / "scripts" / "custom_gate.sh"
            custom_gate.write_text(
                "#!/usr/bin/env bash\n# auto-dev: verify-timeout=1\nsleep 2\nexit 0\n",
                encoding="utf-8",
            )
            custom_gate.chmod(0o755)
            custom_no_dir = repo / "scripts" / "custom_gate_default.sh"
            custom_no_dir.write_text("#!/usr/bin/env bash\nsleep 2\nexit 0\n", encoding="utf-8")
            custom_no_dir.chmod(0o755)
            _git(["add", "."], repo)
            _git(["commit", "-m", "add custom gate"], repo)
            base_sha = _git(["rev-parse", "HEAD"], repo)

            # Create team/core branch with a commit and valid bundle
            _git(["checkout", "-b", "team/core"], repo)
            (repo / "src").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "feature.py").write_text("VALUE = 1\n", encoding="utf-8")
            _git(["add", "."], repo)
            _git(["commit", "-m", "core work"], repo)
            team_head = _git(["rev-parse", "HEAD"], repo)

            bundle_path = repo / ".dev" / "core_bundle.zip"
            bundle_path.parent.mkdir(parents=True, exist_ok=True)
            EvidenceBundler(repo).create_bundle(dest_path=bundle_path, job_id="job-int-gate")
            _git(["checkout", "main"], repo)

            orch = MultiTeamOrchestrator(repo_dir=repo)
            cfg = MultiTeamConfig(
                job_id="job-int-gate",
                teams=[TeamWorkerSpec(team_id="core", base_branch=base_sha)],
                base_branch=base_sha,
                integration_verify_script=custom_gate,
            )
            team_results = {
                "core": TeamExecutionResult(
                    team_id="core",
                    success=True,
                    status="COMPLETED",
                    branch="team/core",
                    head_commit=team_head,
                    bundle_path=bundle_path,
                )
            }

            # 1) timeout_sec=None -> resolves # auto-dev: verify-timeout=1 from custom_gate.sh -> times out
            with mock.patch.object(
                orch.integration_gate,
                "audit_team_inputs",
                return_value=(True, {"core": {"verified_commit": team_head}}, []),
            ):
                res_dir = orch.integrate_team_results(cfg, team_results, timeout_sec=None)
            self.assertFalse(res_dir.success)
            self.assertEqual(res_dir.details.get("exit_code"), 124, res_dir.details)
            gate_out = str(res_dir.details.get("output", ""))
            first_line = gate_out.splitlines()[0]
            self.assertTrue(first_line.startswith("Verification timed out after 1s"), gate_out)
            self.assertIn("custom_gate.sh directive", gate_out)
            self.assertIn("--timeout", gate_out)
            self.assertIn("verify-timeout=", gate_out)

            # 2) Explicit timeout_sec=5 overrides custom_gate.sh directive (1s) -> passes
            with mock.patch.object(
                orch.integration_gate,
                "audit_team_inputs",
                return_value=(True, {"core": {"verified_commit": team_head}}, []),
            ):
                res_cli = orch.integrate_team_results(cfg, team_results, timeout_sec=5)
            self.assertTrue(res_cli.success, res_cli.details)

            # 3) No directive in custom_gate_default.sh -> falls back to default (mocked to 1s) -> times out with default source
            cfg_default = MultiTeamConfig(
                job_id="job-int-default",
                teams=[TeamWorkerSpec(team_id="core", base_branch=base_sha)],
                base_branch=base_sha,
                integration_verify_script=custom_no_dir,
            )
            with (
                mock.patch.object(
                    orch.integration_gate,
                    "audit_team_inputs",
                    return_value=(True, {"core": {"verified_commit": team_head}}, []),
                ),
                mock.patch("auto_developer.verifier.DEFAULT_VERIFY_TIMEOUT_SEC", 1),
            ):
                res_def = orch.integrate_team_results(cfg_default, team_results, timeout_sec=None)
            self.assertFalse(res_def.success)
            self.assertEqual(res_def.details.get("exit_code"), 124)
            def_out = str(res_def.details.get("output", ""))
            self.assertTrue(def_out.splitlines()[0].startswith("Verification timed out after 1s"), def_out)
            self.assertIn("default", def_out.lower())
            self.assertIn("--timeout", def_out)
            self.assertIn("verify-timeout=", def_out)

    def test_nightly_run_skill_documents_timeout_resolution(self) -> None:
        text = NIGHTLY_RUN_SKILL.read_text(encoding="utf-8")
        self.assertIn("verify-timeout=", text)
        self.assertIn("--timeout", text)
        self.assertIn("300", text)


if __name__ == "__main__":
    unittest.main()
