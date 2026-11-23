from __future__ import annotations

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

from auto_developer.cli import DEFAULT_VERIFY_SH, main
from auto_developer.guard import GitGuard
from auto_developer.integration_gate import IntegrationGate
from auto_developer.manifest import ManifestValidationError


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )


def _init_team_repo(repo_dir: Path, *, commit_teams: bool = True, custom_plan: str | None = None) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test Runner")
    _git(repo_dir, "config", "user.email", "test@example.com")

    (repo_dir / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
    (repo_dir / "SPEC.md").write_text("# SPEC\n- TEAM-11\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Root PLAN\n\n## ステップ\n- [ ] 1. Root task\n", encoding="utf-8")
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    verify_sh = repo_dir / "scripts" / "verify_local.sh"
    verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    verify_sh.chmod(0o755)

    (repo_dir / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repo_dir / "src" / "ui" / "view.py").write_text("VIEW = 1\n", encoding="utf-8")

    core_plan_rel = custom_plan or "plans/core.md"
    core_plan = repo_dir / core_plan_rel
    core_plan.parent.mkdir(parents=True, exist_ok=True)
    core_plan.write_text(
        "# PLAN (core)\n\n## 現在の目標\n- Phase 66\n\n## ステップ\n- [ ] 1. Update core module\n",
        encoding="utf-8",
    )

    ui_plan = repo_dir / "plans" / "ui.md"
    ui_plan.parent.mkdir(parents=True, exist_ok=True)
    ui_plan.write_text(
        "# PLAN (ui)\n\n## 現在の目標\n- Phase 66\n\n## ステップ\n- [ ] 1. Update ui module\n",
        encoding="utf-8",
    )

    if commit_teams:
        teams_payload = {
            "version": 1,
            "project": "demo",
            "base": {"ref": "main", "require_clean": True},
            "teams": [
                {"id": "core", "plan": core_plan_rel, "owns": ["src/core/**"], "depends_on": []},
                {"id": "ui", "plan": "plans/ui.md", "owns": ["src/ui/**"], "depends_on": []},
            ],
            "integration": {
                "strategy": "merge-sequential",
                "require_team_evidence": True,
                "reverify_after_merge": True,
                "update_main": False,
            },
        }
        (repo_dir / "teams.json").write_text(json.dumps(teams_payload, indent=2) + "\n", encoding="utf-8")

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "initial commit")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


class TestTeamsJsonProtectedFile(unittest.TestCase):
    """TEAM-11 / INV-1: teams.json を保護ファイルとして扱う。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        self.checkpoint = _init_team_repo(self.repo_dir, commit_teams=True)
        self.guard = GitGuard(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_guard_rejects_modifying_or_deleting_teams_json(self) -> None:
        teams_file = self.repo_dir / "teams.json"
        teams_file.write_text('{"version": 1, "project": "tampered", "teams": []}\n', encoding="utf-8")
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint))
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

        _git(self.repo_dir, "checkout", "--", "teams.json")
        teams_file.unlink()
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint))
        self.assertFalse(self.guard.check_protected_files_untouched(self.checkpoint, allow_immunity_additions=True))

    def test_guard_rejects_untracked_teams_json(self) -> None:
        tmp2 = tempfile.TemporaryDirectory()
        try:
            repo2 = Path(tmp2.name)
            chk2 = _init_team_repo(repo2, commit_teams=False)
            guard2 = GitGuard(repo_dir=repo2)
            (repo2 / "teams.json").write_text('{"version": 1}\n', encoding="utf-8")
            self.assertFalse(guard2.check_protected_files_untouched(chk2))
            self.assertFalse(guard2.check_protected_files_untouched(chk2, allow_immunity_additions=True))
        finally:
            tmp2.cleanup()

    def test_default_verify_sh_protects_teams_json(self) -> None:
        self.assertIn("teams.json", DEFAULT_VERIFY_SH)
        self.assertIn("teams\\.json", DEFAULT_VERIFY_SH)

    def test_integration_gate_detects_teams_json_modification(self) -> None:
        (self.repo_dir / "teams.json").write_text('{"version": 1, "project": "hacked", "teams": []}\n', encoding="utf-8")
        _git(self.repo_dir, "add", "teams.json")
        _git(self.repo_dir, "commit", "-m", "tamper teams.json")
        gate = IntegrationGate(repo_dir=self.repo_dir)
        ok, code, out = gate.run_post_integration_verification(base_commit=self.checkpoint)
        self.assertFalse(ok)
        self.assertEqual(code, 10)
        self.assertIn("teams.json", out)


class TestTeamContextResolution(unittest.TestCase):
    """TEAM-11: ブランチ team/<id> とコミット済み teams.json からチーム文脈を決める。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def test_resolves_from_committed_teams_json_not_working_tree(self) -> None:
        from auto_developer.manifest import resolve_team_context

        _init_team_repo(self.repo_dir, commit_teams=True, custom_plan="docs/core-plan.md")
        wt_dir = self.repo_dir / ".dev" / "worktrees" / "core"
        _git(self.repo_dir, "worktree", "add", "-b", "team/core", str(wt_dir), "main")

        # Tamper with working-tree teams.json inside the worktree
        (wt_dir / "teams.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "teams": [
                    {"id": "core", "plan": "plans/hacked.md", "owns": ["**"], "depends_on": []},
                ],
            }),
            encoding="utf-8",
        )

        ctx = resolve_team_context(wt_dir)
        self.assertIsNotNone(ctx)
        assert ctx is not None
        self.assertEqual(ctx.team_id, "core")
        self.assertEqual(ctx.plan_rel_path, "docs/core-plan.md")
        self.assertEqual(ctx.plan_path, (wt_dir / "docs/core-plan.md").resolve())
        self.assertIn("src/core/**", ctx.owns_patterns)
        self.assertIn("docs/core-plan.md", ctx.owns_patterns)
        self.assertNotIn("**", ctx.owns_patterns)

    def test_team_branch_without_committed_teams_json_fails_even_if_untracked_exists(self) -> None:
        from auto_developer.manifest import resolve_team_context

        _init_team_repo(self.repo_dir, commit_teams=False)
        wt_dir = self.repo_dir / ".dev" / "worktrees" / "core"
        _git(self.repo_dir, "worktree", "add", "-b", "team/core", str(wt_dir), "main")

        # Place an untracked teams.json in working tree — must NOT be trusted!
        (wt_dir / "teams.json").write_text(
            json.dumps({
                "version": 1,
                "project": "demo",
                "teams": [{"id": "core", "owns": ["src/core/**"], "depends_on": []}],
            }),
            encoding="utf-8",
        )

        with self.assertRaises(ManifestValidationError):
            resolve_team_context(wt_dir)


class TestTeamWorktreeCliSubcommands(unittest.TestCase):
    """TEAM-11: worktree 内の pre-flight / verify-step / commit-step / rollback-step / run の連動。"""

    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        _init_team_repo(self.repo_dir, commit_teams=True)
        self.wt_dir = self.repo_dir / ".dev" / "worktrees" / "core"
        _git(self.repo_dir, "worktree", "add", "-b", "team/core", str(self.wt_dir), "main")
        self.wt_checkpoint = _git(self.wt_dir, "rev-parse", "HEAD").stdout.strip()

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _run_cli(self, *args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
        target_cwd = cwd or self.wt_dir
        env = os.environ.copy()
        existing_pp = env.get("PYTHONPATH", "")
        env["PYTHONPATH"] = f"{src_dir}:{existing_pp}" if existing_pp else src_dir
        return subprocess.run(
            [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(target_cwd), *args],
            cwd=target_cwd,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )

    def test_pre_flight_resolves_team_plan_in_worktree(self) -> None:
        proc = self._run_cli("pre-flight")
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertTrue(data["clean"])
        self.assertEqual(data["team_id"], "core")
        self.assertEqual(data["plan"], "plans/core.md")
        self.assertEqual(data["next_task"]["id"], "1")
        self.assertEqual(data["next_task"]["title"], "Update core module")

        # Explicit contradicting --plan must be rejected with exit 2
        bad = self._run_cli("--plan", "plans/ui.md", "pre-flight")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("TEAMS_CONFIG_ERROR:", bad.stderr)

        # --team-id and --owns must be rejected outside `run` (not silently ignored)
        for bad_global in [
            ("--owns", "src/ui/**", "verify-step", "--checkpoint", self.wt_checkpoint, "--task-id", "1"),
            ("verify-step", "--checkpoint", self.wt_checkpoint, "--task-id", "1", "--owns", "src/ui/**"),
            ("--team-id", "core", "pre-flight"),
            ("pre-flight", "--team-id", "core"),
        ]:
            rejected = self._run_cli(*bad_global)
            self.assertEqual(rejected.returncode, 2)
            self.assertIn("error:", rejected.stderr.lower())

    def test_verify_step_and_commit_step_with_auto_included_plan_and_boundary_guard(self) -> None:
        # 1. Valid modification inside src/core/**
        (self.wt_dir / "src" / "core" / "app.py").write_text("VALUE = 2\n", encoding="utf-8")
        v_ok = self._run_cli("verify-step", "--checkpoint", self.wt_checkpoint, "--task-id", "1")
        self.assertEqual(v_ok.returncode, 0, v_ok.stderr)
        self.assertIn("VERIFICATION PASSED", v_ok.stdout)

        from review_test_helpers import record_test_review

        rr_ok = record_test_review(self.wt_dir, "1", reason="looks good")
        self.assertEqual(rr_ok.returncode, 0, rr_ok.stderr)

        # plans/core.md was updated to [x], PLAN.md was untouched
        self.assertIn("- [x] 1. Update core module", (self.wt_dir / "plans" / "core.md").read_text(encoding="utf-8"))
        self.assertIn("- [ ] 1. Root task", (self.wt_dir / "PLAN.md").read_text(encoding="utf-8"))

        # 2. Tampering outside owns AFTER verify-step must be rejected at commit-step without committing
        (self.wt_dir / "src" / "ui" / "rogue.py").write_text("ROGUE = 1\n", encoding="utf-8")
        c_bad = self._run_cli(
            "commit-step",
            "--task-id", "1",
            "--checkpoint", self.wt_checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "APPROVE",
            "--checker-reason", "looks good",
        )
        self.assertNotEqual(c_bad.returncode, 0)
        self.assertIn("BOUNDARY", c_bad.stderr.upper())
        self.assertEqual(_git(self.wt_dir, "rev-parse", "HEAD").stdout.strip(), self.wt_checkpoint)

        # Remove rogue file: commit-step without re-verifying must fail with exit 3 (TEAM-21)
        (self.wt_dir / "src" / "ui" / "rogue.py").unlink()
        c_invalid = self._run_cli(
            "commit-step",
            "--task-id", "1",
            "--checkpoint", self.wt_checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "APPROVE",
            "--checker-reason", "looks good",
        )
        self.assertEqual(c_invalid.returncode, 3, c_invalid.stderr)
        self.assertEqual(_git(self.wt_dir, "rev-parse", "HEAD").stdout.strip(), self.wt_checkpoint)

        # Re-run verify-step, then commit-step cleanly
        v_reverify = self._run_cli("verify-step", "--checkpoint", self.wt_checkpoint, "--task-id", "1")
        self.assertEqual(v_reverify.returncode, 0, v_reverify.stderr)
        self.assertIn("VERIFICATION PASSED", v_reverify.stdout)

        rr_reverify = record_test_review(self.wt_dir, "1", reason="looks good")
        self.assertEqual(rr_reverify.returncode, 0, rr_reverify.stderr)

        c_ok = self._run_cli(
            "commit-step",
            "--task-id", "1",
            "--checkpoint", self.wt_checkpoint,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
            "--checker-decision", "APPROVE",
            "--checker-reason", "looks good",
        )
        self.assertEqual(c_ok.returncode, 0, c_ok.stderr)
        self.assertIn("COMMITTED:", c_ok.stdout)
        self.assertNotEqual(_git(self.wt_dir, "rev-parse", "HEAD").stdout.strip(), self.wt_checkpoint)

    def test_verify_step_rejects_out_of_owns_and_rollback_step_quarantines(self) -> None:
        # Modify outside owns (src/ui/view.py) and untracked file
        (self.wt_dir / "src" / "ui" / "view.py").write_text("HACKED = True\n", encoding="utf-8")
        (self.wt_dir / "rogue_untracked.txt").write_text("rogue\n", encoding="utf-8")

        v_bad = self._run_cli("verify-step", "--checkpoint", self.wt_checkpoint, "--task-id", "1")
        self.assertNotEqual(v_bad.returncode, 0)
        self.assertIn("BOUNDARY", v_bad.stderr.upper())
        # plans/core.md must be reverted to [ ]
        self.assertIn("- [ ] 1. Update core module", (self.wt_dir / "plans" / "core.md").read_text(encoding="utf-8"))

        # Verify teams.json tampering is also blocked by verify-step
        _git(self.wt_dir, "checkout", "--", "src/ui/view.py")
        (self.wt_dir / "rogue_untracked.txt").unlink()
        (self.wt_dir / "teams.json").write_text('{"version": 1}\n', encoding="utf-8")
        v_prot = self._run_cli("verify-step", "--checkpoint", self.wt_checkpoint, "--task-id", "1")
        self.assertEqual(v_prot.returncode, 10)
        self.assertIn("CIRCUIT BREAKER", v_prot.stderr)
        _git(self.wt_dir, "checkout", "--", "teams.json")

        # Now test rollback-step with out-of-owns change
        (self.wt_dir / "src" / "ui" / "view.py").write_text("HACKED = True\n", encoding="utf-8")
        rb = self._run_cli(
            "rollback-step",
            "--checkpoint", self.wt_checkpoint,
            "--task-id", "1",
            "--reason", "Boundary violation in ui",
        )
        self.assertEqual(rb.returncode, 0, rb.stderr)
        self.assertIn("quarantine/core-1-", rb.stdout)
        self.assertEqual(_git(self.wt_dir, "status", "--porcelain").stdout.strip(), "")
        self.assertEqual((self.wt_dir / "src" / "ui" / "view.py").read_text(encoding="utf-8"), "VIEW = 1\n")

    def test_run_auto_resolves_team_context_and_rejects_contradictions(self) -> None:
        maker_ok = "python3 -c \"from pathlib import Path; Path('src/core/app.py').write_text('VALUE = 99\\n')\""
        checker_ok = "echo 'DECISION: APPROVE'"

        # 1. Contradicting explicit flags must fail with exit 2
        for bad_args in [
            ["--team-id", "ui"],
            ["--owns", "src/ui/**"],
            ["--owns", "src/**"],
            ["--plan", "plans/ui.md"],
            ["--plan", "PLAN.md"],
        ]:
            proc = self._run_cli(
                "run",
                *bad_args,
                "--maker-cmd", maker_ok,
                "--checker-cmd", checker_ok,
                "--maker-model", "gemini-3.8-flash",
                "--checker-model", "claude-opus-5-5-medium",
            )
            self.assertEqual(proc.returncode, 2, f"Expected exit 2 for {bad_args}, got {proc.returncode}: {proc.stderr}")
            self.assertIn("TEAMS_CONFIG_ERROR:", proc.stderr)

        # 2. Rogue maker modifying src/ui/view.py is quarantined with exit 1
        maker_rogue = "python3 -c \"from pathlib import Path; Path('src/ui/view.py').write_text('HACK\\n')\""
        proc_rogue = self._run_cli(
            "run",
            "--maker-cmd", maker_rogue,
            "--checker-cmd", checker_ok,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
        )
        self.assertEqual(proc_rogue.returncode, 1, proc_rogue.stderr)
        self.assertEqual(_git(self.wt_dir, "status", "--porcelain").stdout.strip(), "")
        self.assertEqual((self.wt_dir / "src" / "ui" / "view.py").read_text(encoding="utf-8"), "VIEW = 1\n")
        q_branches = _git(self.wt_dir, "branch", "--list", "quarantine/*/core/1-*").stdout.strip()
        self.assertTrue(len(q_branches) > 0)

        # Reset plans/core.md if needed (it was restored to checkpoint) and run valid maker
        proc_ok = self._run_cli(
            "run",
            "--team-id", "core",
            "--owns", "src/core/**",
            "--plan", "plans/core.md",
            "--maker-cmd", maker_ok,
            "--checker-cmd", checker_ok,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
        )
        self.assertEqual(proc_ok.returncode, 0, f"stderr: {proc_ok.stderr}\nstdout: {proc_ok.stdout}")
        self.assertIn("- [x] 1. Update core module", (self.wt_dir / "plans" / "core.md").read_text(encoding="utf-8"))
        self.assertEqual((self.wt_dir / "src" / "core" / "app.py").read_text(encoding="utf-8"), "VALUE = 99\n")

    def test_run_on_main_with_explicit_team_id_checks_teams_json(self) -> None:
        maker_ok = "python3 -c \"from pathlib import Path; Path('src/core/app.py').write_text('VALUE = 42\\n')\""
        checker_ok = "echo 'DECISION: APPROVE'"

        # Contradicting --owns with --team-id on main is rejected
        bad = main([
            "--work-dir", str(self.repo_dir),
            "run",
            "--team-id", "core",
            "--owns", "src/ui/**",
            "--maker-cmd", maker_ok,
            "--checker-cmd", checker_ok,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
        ])
        self.assertEqual(bad, 2)

        # Unknown --team-id on main when teams.json exists is rejected
        unknown = main([
            "--work-dir", str(self.repo_dir),
            "run",
            "--team-id", "nonexistent",
            "--maker-cmd", maker_ok,
            "--checker-cmd", checker_ok,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
        ])
        self.assertEqual(unknown, 2)

        # Valid --team-id on main when teams.json exists succeeds and resolves plan/owns
        ok = main([
            "--work-dir", str(self.repo_dir),
            "run",
            "--team-id", "core",
            "--maker-cmd", maker_ok,
            "--checker-cmd", checker_ok,
            "--maker-model", "gemini-3.8-flash",
            "--checker-model", "claude-opus-5-5-medium",
        ])
        self.assertEqual(ok, 0)
        self.assertIn("- [x] 1. Update core module", (self.repo_dir / "plans" / "core.md").read_text(encoding="utf-8"))

    def test_run_on_main_without_teams_json_supports_explicit_flags(self) -> None:
        tmp2 = tempfile.TemporaryDirectory()
        try:
            repo2 = Path(tmp2.name)
            _init_team_repo(repo2, commit_teams=False)
            maker_rogue = "python3 -c \"from pathlib import Path; Path('src/ui/view.py').write_text('HACK\\n')\""
            maker_ok = "python3 -c \"from pathlib import Path; Path('src/core/app.py').write_text('VALUE = 77\\n')\""
            checker_ok = "echo 'DECISION: APPROVE'"

            rogue = main([
                "--work-dir", str(repo2),
                "run",
                "--team-id", "core",
                "--owns", "src/core/**",
                "--plan", "plans/core.md",
                "--maker-cmd", maker_rogue,
                "--checker-cmd", checker_ok,
                "--maker-model", "gemini-3.8-flash",
                "--checker-model", "claude-opus-5-5-medium",
            ])
            self.assertEqual(rogue, 1)
            self.assertEqual(_git(repo2, "status", "--porcelain").stdout.strip(), "")

            ok = main([
                "--work-dir", str(repo2),
                "run",
                "--team-id", "core",
                "--owns", "src/core/**",
                "--plan", "plans/core.md",
                "--maker-cmd", maker_ok,
                "--checker-cmd", checker_ok,
                "--maker-model", "gemini-3.8-flash",
                "--checker-model", "claude-opus-5-5-medium",
            ])
            self.assertEqual(ok, 0)
            self.assertIn("- [x] 1. Update core module", (repo2 / "plans" / "core.md").read_text(encoding="utf-8"))
        finally:
            tmp2.cleanup()

    def test_direct_orchestrator_in_team_worktree_resolves_context(self) -> None:
        from auto_developer.orchestrator import NightlyOrchestrator
        from auto_developer.runner import RunnerAdapter

        class CoreWriterAdapter(RunnerAdapter):
            def __init__(self, wt: Path) -> None:
                self.wt = wt

            def run_maker(self, task: object, context: dict[str, object]) -> tuple[int, str]:
                (self.wt / "src" / "core" / "app.py").write_text("VALUE = 123\n", encoding="utf-8")
                return 0, "updated core"

            def run_checker(self, task: object, diff: str, spec_content: str) -> tuple[bool, str]:
                return True, "APPROVE"

        orch = NightlyOrchestrator(
            repo_dir=self.wt_dir,
            runner_adapter=CoreWriterAdapter(self.wt_dir),
        )
        self.assertEqual(orch.run(), 0)
        self.assertEqual(orch.team_id, "core")
        self.assertIn("- [x] 1. Update core module", (self.wt_dir / "plans" / "core.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
