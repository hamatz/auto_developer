from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import tempfile
import unittest
import unittest.mock
from pathlib import Path
from typing import Any

from auto_developer.bundler import EvidenceBundler
from auto_developer.evidence import EvidenceStore
from auto_developer.multi_team import (
    MultiTeamConfig,
    MultiTeamOrchestrator,
    TeamExecutionResult,
    TeamWorkerSpec,
)


class TestMultiTeamOrchestration(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)
        self._init_git_repo()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _run_git(self, args: list[str], check: bool = True) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *args],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=check,
        )

    def _init_git_repo(self) -> None:
        self._run_git(["init", "-b", "main"])
        self._run_git(["config", "user.name", "MultiTeam Test"])
        self._run_git(["config", "user.email", "multiteam@example.com"])
        readme = self.repo_dir / "README.md"
        readme.write_text("MultiTeam Repository\n", encoding="utf-8")
        spec = self.repo_dir / "SPEC.md"
        spec.write_text("# MultiTeam SPEC\n", encoding="utf-8")
        gitignore = self.repo_dir / ".gitignore"
        gitignore.write_text(".dev/\n*.zip\n", encoding="utf-8")
        self._run_git(["add", "README.md", "SPEC.md", ".gitignore"])
        self._run_git(["commit", "-m", "init: base commit"])
        res = self._run_git(["rev-parse", "HEAD"])
        self.base_commit = res.stdout.strip()

    def test_multi_team_data_structures_validation(self) -> None:
        """GG.1: TeamWorkerSpec, MultiTeamConfig, MultiTeamResult の型安全とバリデーション。"""
        # 不正な team_id の拒絶
        with self.assertRaises(ValueError):
            TeamWorkerSpec(team_id="../evil_team")

        spec_a = TeamWorkerSpec(
            team_id="team-alpha",
            assigned_tasks=["task-1"],
            extra_args=["--mock"],
            env={"WORKER_TEST": "1"},
        )
        self.assertEqual(spec_a.team_id, "team-alpha")
        self.assertEqual(spec_a.extra_args, ["--mock"])
        self.assertEqual(spec_a.env, {"WORKER_TEST": "1"})

        config = MultiTeamConfig(
            job_id="job-multi-1",
            teams=[spec_a],
            base_branch="main",
        )
        self.assertEqual(config.job_id, "job-multi-1")
        self.assertEqual(len(config.teams), 1)

    def test_run_workers_builds_correct_cli_command_order_and_arguments(self) -> None:
        """KK.1, KK.2: 標準CLIワーカー起動コマンドの引数順序 (--work-dir <wt> run) とパラメータ拡張の検証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-cli-cmd-order",
            teams=[
                TeamWorkerSpec(
                    team_id="team-cli-test",
                    extra_args=["--mock", "--iterations", "1"],
                    env={"CUSTOM_KEY": "CUSTOM_VAL"},
                ),
            ],
            base_branch="main",
        )
        orchestrator.prepare_workers(config)
        wt_path = orchestrator.worktree_manager.worktree_base / "team-cli-test"

        captured_calls: list[dict[str, Any]] = []

        def mock_subprocess_run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            captured_calls.append({"cmd": cmd, "kwargs": kwargs})
            if cmd == ["git", "rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(cmd, returncode=0, stdout="mock_head_sha\n", stderr="")
            return subprocess.CompletedProcess(cmd, returncode=0, stdout="success\n", stderr="")

        with unittest.mock.patch("subprocess.run", side_effect=mock_subprocess_run):
            results = orchestrator.run_workers(config)

        self.assertIn("team-cli-test", results)
        self.assertTrue(results["team-cli-test"].success)

        # 呼び出された CLI コマンドの検査
        cli_run_calls = [c for c in captured_calls if "auto_developer.cli" in " ".join(c["cmd"])]
        self.assertEqual(len(cli_run_calls), 1)
        executed_cmd = cli_run_calls[0]["cmd"]
        executed_kwargs = cli_run_calls[0]["kwargs"]

        # 引数の順序が python3 -m auto_developer.cli --work-dir <wt> run であること (run --work-dir は Exit 2 となる)
        expected_cmd_prefix = [
            "python3",
            "-m",
            "auto_developer.cli",
            "--work-dir",
            str(wt_path),
            "run",
            "--mock",
            "--iterations",
            "1",
        ]
        self.assertEqual(executed_cmd, expected_cmd_prefix)

        # 環境変数に team.env が反映されていること
        env_passed = executed_kwargs.get("env", {})
        self.assertEqual(env_passed.get("CUSTOM_KEY"), "CUSTOM_VAL")

        orchestrator.cleanup_workers(config)

    def test_prepare_workers_allocates_isolated_worktrees(self) -> None:
        """GG.2: 複数ワーカー用の専用 Worktree を分離作成し管理できることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-multi-wt",
            teams=[
                TeamWorkerSpec(team_id="team-frontend"),
                TeamWorkerSpec(team_id="team-backend"),
            ],
            base_branch="main",
        )

        wt_paths = orchestrator.prepare_workers(config)
        self.assertEqual(len(wt_paths), 2)
        self.assertTrue(wt_paths["team-frontend"].exists())
        self.assertTrue(wt_paths["team-backend"].exists())
        self.assertNotEqual(wt_paths["team-frontend"], wt_paths["team-backend"])

        # 後片付け
        orchestrator.cleanup_workers(config)
        self.assertFalse(wt_paths["team-frontend"].exists())
        self.assertFalse(wt_paths["team-backend"].exists())

    def test_collect_team_inputs_handles_worker_failure(self) -> None:
        """GG.2: いずれかのチームワーカーが失敗した際、安全に WORKER_FAILED となることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-multi-fail",
            teams=[
                TeamWorkerSpec(team_id="team-a"),
                TeamWorkerSpec(team_id="team-b"),
            ],
        )

        results = {
            "team-a": TeamExecutionResult(team_id="team-a", success=True, status="SUCCESS", bundle_path=Path("/tmp/a.zip")),
            "team-b": TeamExecutionResult(team_id="team-b", success=False, status="FAILED", details={"error": "Test failure"}),
        }

        res = orchestrator.integrate_team_results(config, results)
        self.assertFalse(res.success)
        self.assertEqual(res.status, "WORKER_FAILED")
        self.assertIn("team-b", res.details.get("failed_teams", []))

    def test_multi_team_config_rejects_duplicate_team_ids(self) -> None:
        """HH.2 / P1-2: MultiTeamConfig は重複する team_id を拒絶することの実証。"""
        with self.assertRaises(ValueError):
            MultiTeamConfig(
                job_id="job-dup",
                teams=[
                    TeamWorkerSpec(team_id="team-a"),
                    TeamWorkerSpec(team_id="team-a"),
                ],
            )

    def test_cleanup_workers_reports_true_when_worktrees_removed(self) -> None:
        """HH.1 / P1-1: cleanup_workers は Worktree が正常に解放された場合に True を報告することの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-clean-test",
            teams=[
                TeamWorkerSpec(team_id="team-x"),
                TeamWorkerSpec(team_id="team-y"),
            ],
            base_branch="main",
        )
        orchestrator.prepare_workers(config)
        statuses = orchestrator.cleanup_workers(config)
        self.assertEqual(statuses, {"team-x": True, "team-y": True})

    def test_prepare_workers_atomic_rollback_on_failure(self) -> None:
        """HH.2 / P1-2: prepare_workers の途中で失敗した場合、作成済みの Worktree が自動ロールバックされることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-atomic-prep",
            teams=[
                TeamWorkerSpec(team_id="team-good"),
                TeamWorkerSpec(team_id="team-bad"),
            ],
            base_branch="main",
        )

        orig_create = orchestrator.worktree_manager.create_worktree

        def mock_create(team_id: str, *args: object, **kwargs: object) -> Path:
            if team_id == "team-bad":
                raise RuntimeError("Injected worktree failure for team-bad")
            return orig_create(team_id, *args, **kwargs)  # type: ignore[arg-type]

        with (
            unittest.mock.patch.object(orchestrator.worktree_manager, "create_worktree", side_effect=mock_create),
            self.assertRaises(RuntimeError),
        ):
            orchestrator.prepare_workers(config)

        # 最初の team-good の Worktree もロールバックされて残存しないことの実証
        good_dir = orchestrator.worktree_manager.worktree_base / "team-good"
        self.assertFalse(good_dir.exists())

    def test_run_workers_executes_in_parallel_and_collects_results(self) -> None:
        """II.1, II.2: run_workers が各チームを並行実行し、結果と成果物バンドルを収集できることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-parallel-test",
            teams=[
                TeamWorkerSpec(team_id="team-alpha"),
                TeamWorkerSpec(team_id="team-beta"),
            ],
            base_branch="main",
        )
        orchestrator.prepare_workers(config)

        def mock_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
            f = wt_path / f"{team_id}.txt"
            f.write_text(f"work done by {team_id}\n", encoding="utf-8")
            subprocess.run(["git", "add", f"{team_id}.txt"], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", f"feat: {team_id} work"], cwd=wt_path, check=True)
            rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
            bundle_p = wt_path / f"bundle_{team_id}.zip"
            bundle_p.write_text("dummy bundle", encoding="utf-8")
            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                head_commit=rev,
                bundle_path=bundle_p,
            )

        results = orchestrator.run_workers(config, worker_fn=mock_worker)
        self.assertEqual(len(results), 2)
        self.assertTrue(results["team-alpha"].success)
        self.assertTrue(results["team-beta"].success)
        self.assertIsNotNone(results["team-alpha"].head_commit)
        self.assertIsNotNone(results["team-beta"].head_commit)

        orchestrator.cleanup_workers(config)

    def test_run_workers_handles_worker_crash_or_failure(self) -> None:
        """II.1, II.2: いずれかのワーカーが例外や失敗を起こした際、安全にエラー結果を記録することの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-crash-test",
            teams=[
                TeamWorkerSpec(team_id="team-ok"),
                TeamWorkerSpec(team_id="team-fail"),
            ],
            base_branch="main",
        )
        orchestrator.prepare_workers(config)

        def mock_failing_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
            if team_id == "team-fail":
                raise RuntimeError("Worker process crashed unexpectedly")
            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                bundle_path=wt_path / "bundle.zip",
            )

        results = orchestrator.run_workers(config, worker_fn=mock_failing_worker)
        self.assertTrue(results["team-ok"].success)
        self.assertFalse(results["team-fail"].success)
        self.assertEqual(results["team-fail"].status, "CRASHED")

        orchestrator.cleanup_workers(config)

    def test_multi_team_end_to_end_parallel_workers_and_integration(self) -> None:
        """JJ.1: 2ワーカー並行稼働・成果物収集・統合ゲートRC発行のエンドツーエンド受入実証。"""
        # 統合検証スクリプト (リポジトリ外に作成してクリーンツリーを維持)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as script_file:
            script_file.write("#!/bin/sh\nexit 0\n")
            verify_script = Path(script_file.name)
        verify_script.chmod(0o755)

        try:
            orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
            config = MultiTeamConfig(
                job_id="job-e2e-stage3",
                teams=[
                    TeamWorkerSpec(team_id="team-core"),
                    TeamWorkerSpec(team_id="team-ui"),
                ],
                base_branch="main",
                integration_verify_script=verify_script,
            )

            def mock_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
                # チームWorktreeで成果物作成 & コミット
                rel_file = f"src/{team_id.replace('-', '_')}.py"
                target_file = wt_path / rel_file
                target_file.parent.mkdir(parents=True, exist_ok=True)
                target_file.write_text(f"# Work for {team_id}\ndef fn_{team_id.replace('-', '_')}(): pass\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=wt_path, check=True)
                subprocess.run(["git", "commit", "-m", f"feat: {team_id} implementation"], cwd=wt_path, check=True)
                rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
                tree_sha = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
                import hashlib
                spec_sha = hashlib.sha256((wt_path / "SPEC.md").read_bytes()).hexdigest()

                # 証跡レコードとバンドルの作成
                ev_store = EvidenceStore(repo_dir=wt_path)
                ev_store.issue_evidence(
                    task_id=f"task-{team_id}",
                    checkpoint=self.base_commit,
                    tree_sha=tree_sha,
                    spec_sha=spec_sha,
                )
                ev_store.archive_record(
                    task_id=f"task-{team_id}",
                    status="COMPLETED",
                    commit_info={"sha": rev, "tree_sha": tree_sha},
                    checker_info={"decision": "APPROVE", "reason": f"Audit approved {team_id}"},
                    verification_info={"exit_code": 0},
                )

                bundles_dir = wt_path / ".dev" / "bundles"
                bundles_dir.mkdir(parents=True, exist_ok=True)
                bundle_p = bundles_dir / f"bundle_{team_id}.zip"
                bundler = EvidenceBundler(repo_dir=wt_path)
                bundler.create_bundle(dest_path=bundle_p)

                return TeamExecutionResult(
                    team_id=team_id,
                    success=True,
                    status="SUCCESS",
                    branch=f"team/{team_id}",
                    head_commit=rev,
                    bundle_path=bundle_p,
                )

            multi_res = orchestrator.execute_multi_team_pipeline(
                config=config,
                worker_fn=mock_worker,
                verify_script=verify_script,
                cleanup=True,
            )

            self.assertTrue(multi_res.success)
            self.assertEqual(multi_res.status, "SUCCESS")
            self.assertEqual(multi_res.release_candidate_tag, "rc/job-e2e-stage3")
            self.assertIsNotNone(multi_res.release_candidate_commit)

            # Git リポジトリに RC タグが実際に存在することの検証
            tag_chk = self._run_git(["rev-parse", "--verify", "refs/tags/rc/job-e2e-stage3"])
            self.assertEqual(tag_chk.returncode, 0)
            self.assertEqual(tag_chk.stdout.strip(), multi_res.release_candidate_commit)

            # 各チームのコミットが RC コミットの祖先であることの検証
            for tid in ["team-core", "team-ui"]:
                team_commit = multi_res.team_results[tid].head_commit
                assert team_commit is not None
                ancestor_chk = self._run_git(["merge-base", "--is-ancestor", team_commit, multi_res.release_candidate_commit])
                self.assertEqual(ancestor_chk.returncode, 0)

            # Worktree がクリーンアップされていることの検証
            for tid in ["team-core", "team-ui"]:
                wt_dir = orchestrator.worktree_manager.worktree_base / tid
                self.assertFalse(wt_dir.exists())

            # Evidence Bundle が Worktree 削除後もディスク上に保全されていることの検証 (LL.1, LL.2)
            for tid in ["team-core", "team-ui"]:
                b_path = multi_res.team_results[tid].bundle_path
                self.assertIsNotNone(b_path)
                assert b_path is not None
                self.assertTrue(b_path.exists())
                self.assertEqual(
                    b_path.resolve(),
                    (self.repo_dir / ".dev" / "integration_jobs" / "job-e2e-stage3" / "evidence" / f"{tid}.zip").resolve(),
                )
        finally:
            if verify_script.exists():
                verify_script.unlink()

    def test_multi_team_end_to_end_rejects_integration_on_worker_failure(self) -> None:
        """JJ.1: 片方のワーカーが失敗した際、未承認成果が統合されず WORKER_FAILED となることの実証。"""
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id="job-e2e-fail",
            teams=[
                TeamWorkerSpec(team_id="team-success"),
                TeamWorkerSpec(team_id="team-failing"),
            ],
            base_branch="main",
        )

        def mock_worker_with_failure(team_id: str, wt_path: Path) -> TeamExecutionResult:
            if team_id == "team-failing":
                return TeamExecutionResult(
                    team_id=team_id,
                    success=False,
                    status="FAILED",
                    details={"error": "Build failed on team-failing"},
                )
            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                bundle_path=wt_path / "bundle.zip",
            )

        multi_res = orchestrator.execute_multi_team_pipeline(
            config=config,
            worker_fn=mock_worker_with_failure,
            cleanup=True,
        )

        self.assertFalse(multi_res.success)
        self.assertEqual(multi_res.status, "WORKER_FAILED")
        self.assertIsNone(multi_res.release_candidate_commit)

        # RC タグが作成されていないことの検証
        tag_chk = self._run_git(["rev-parse", "--verify", "refs/tags/rc/job-e2e-fail"], check=False)
        self.assertNotEqual(tag_chk.returncode, 0)

    def test_execute_multi_team_pipeline_preserves_evidence_bundles_after_cleanup(self) -> None:
        """LL.1, LL.2: Worktree 削除前の Evidence Bundle 外出し保全 & ハッシュ照合・返却パス更新の検証。"""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as script_file:
            script_file.write("#!/bin/sh\nexit 0\n")
            verify_script = Path(script_file.name)
        verify_script.chmod(0o755)

        try:
            job_id = "job-preserve-bundle-test"
            orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
            config = MultiTeamConfig(
                job_id=job_id,
                teams=[
                    TeamWorkerSpec(team_id="team-alpha"),
                    TeamWorkerSpec(team_id="team-beta"),
                ],
                base_branch="main",
                integration_verify_script=verify_script,
            )

            def mock_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
                # Worktree 内で作業・コミット・証跡作成
                target = wt_path / f"{team_id}.txt"
                target.write_text(f"content of {team_id}\n", encoding="utf-8")
                subprocess.run(["git", "add", "."], cwd=wt_path, check=True)
                subprocess.run(["git", "commit", "-m", f"feat: {team_id}"], cwd=wt_path, check=True)
                rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
                tree_sha = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
                import hashlib
                spec_sha = hashlib.sha256((wt_path / "SPEC.md").read_bytes()).hexdigest()

                ev_store = EvidenceStore(repo_dir=wt_path)
                ev_store.issue_evidence(
                    task_id=f"task-{team_id}",
                    checkpoint=self.base_commit,
                    tree_sha=tree_sha,
                    spec_sha=spec_sha,
                )
                ev_store.archive_record(
                    task_id=f"task-{team_id}",
                    status="COMPLETED",
                    commit_info={"sha": rev, "tree_sha": tree_sha},
                    checker_info={"decision": "APPROVE", "reason": f"Approval {team_id}"},
                    verification_info={"exit_code": 0},
                )
                bundles_dir = wt_path / ".dev" / "bundles"
                bundles_dir.mkdir(parents=True, exist_ok=True)
                raw_bundle = bundles_dir / f"bundle_{team_id}.zip"
                bundler = EvidenceBundler(repo_dir=wt_path)
                bundler.create_bundle(dest_path=raw_bundle)

                return TeamExecutionResult(
                    team_id=team_id,
                    success=True,
                    status="SUCCESS",
                    branch=f"team/{team_id}",
                    head_commit=rev,
                    bundle_path=raw_bundle,
                )

            multi_res = orchestrator.execute_multi_team_pipeline(
                config=config,
                worker_fn=mock_worker,
                verify_script=verify_script,
                cleanup=True,
            )

            self.assertTrue(multi_res.success)
            expected_evidence_dir = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence"

            for tid in ["team-alpha", "team-beta"]:
                team_res = multi_res.team_results[tid]
                self.assertIsNotNone(team_res.bundle_path)
                assert team_res.bundle_path is not None

                # 1. Worktree 削除後も bundle_path がディスク上に実在すること
                self.assertTrue(team_res.bundle_path.exists(), f"Preserved bundle {team_res.bundle_path} must exist on disk")

                # 2. 保存先が Worktree 外の恒久パス (.dev/integration_jobs/<job_id>/evidence/<tid>.zip) であること
                expected_path = expected_evidence_dir / f"{tid}.zip"
                self.assertEqual(team_res.bundle_path.resolve(), expected_path.resolve())

                # 3. 保存されたバンドルの整合性が検証できること
                bundler = EvidenceBundler(repo_dir=self.repo_dir)
                v_res = bundler.verify_bundle(team_res.bundle_path)
                self.assertEqual(v_res.status, "VERIFIED")
                self.assertEqual(len(v_res.errors), 0)
        finally:
            if verify_script.exists():
                verify_script.unlink()

    def test_multi_team_end_to_end_real_subprocess_workers_and_semantic_integration(self) -> None:
        """MM.1, MM.2: 実サブプロセスによる2チーム並行稼働 & Core/UI 意味的機能結合の完全実証。"""
        # 1. リポジトリの準備 (PLAN.md と local verify_local.sh)
        plan_file = self.repo_dir / "PLAN.md"
        plan_file.write_text("# PLAN.md\n\n## ステップ\n- [ ] 1. チーム機能の実装\n", encoding="utf-8")
        scripts_dir = self.repo_dir / "scripts"
        scripts_dir.mkdir(parents=True, exist_ok=True)
        local_verify = scripts_dir / "verify_local.sh"
        local_verify.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        local_verify.chmod(0o755)
        self._run_git(["add", "PLAN.md", "scripts/verify_local.sh"])
        self._run_git(["commit", "-m", "chore: setup plan and local verify"])

        # 2. 統合検証スクリプト (Core と UI の意味的結合を実インポートして検証)
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sh", delete=False) as script_file:
            script_file.write(
                "#!/usr/bin/env bash\n"
                "set -eu\n"
                "PYTHONPATH=. python3 -c \"import sys; sys.path.insert(0, '.'); from src.ui.view import render_view; msg = render_view(); assert msg == 'UI View: Hello from Core', f'Mismatch: {msg}'\"\n"
            )
            verify_script = Path(script_file.name)
        verify_script.chmod(0o755)

        try:
            job_id = "job-subprocess-e2e"
            orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)

            core_maker_cmd = (
                "python3 -c \""
                "import pathlib; "
                "p = pathlib.Path('src/core'); p.mkdir(parents=True, exist_ok=True); "
                "(pathlib.Path('src') / '__init__.py').touch(); "
                "(p / '__init__.py').touch(); "
                "(p / 'app.py').write_text('def get_core_message() -> str:\\n    return \\\"Hello from Core\\\"\\n', encoding='utf-8')\""
            )
            core_checker_cmd = "python3 -c \"import json; print(json.dumps({'decision': 'APPROVE', 'reason': 'Core approved'}))\""

            ui_maker_cmd = (
                "python3 -c \""
                "import pathlib; "
                "p = pathlib.Path('src/ui'); p.mkdir(parents=True, exist_ok=True); "
                "(pathlib.Path('src') / '__init__.py').touch(); "
                "(p / '__init__.py').touch(); "
                "(p / 'view.py').write_text('from src.core.app import get_core_message\\ndef render_view() -> str:\\n    return f\\\"UI View: {get_core_message()}\\\"\\n', encoding='utf-8')\""
            )
            ui_checker_cmd = "python3 -c \"import json; print(json.dumps({'decision': 'APPROVE', 'reason': 'UI approved'}))\""

            python_path = str(Path.cwd() / "src")
            cwd_str = str(Path.cwd())
            worker_env = {"PYTHONPATH": f"{python_path}:{cwd_str}"}

            config = MultiTeamConfig(
                job_id=job_id,
                teams=[
                    TeamWorkerSpec(
                        team_id="team-core",
                        extra_args=["--maker-cmd", core_maker_cmd, "--checker-cmd", core_checker_cmd],
                        env=worker_env,
                    ),
                    TeamWorkerSpec(
                        team_id="team-ui",
                        extra_args=["--maker-cmd", ui_maker_cmd, "--checker-cmd", ui_checker_cmd],
                        env=worker_env,
                    ),
                ],
                base_branch="main",
                integration_verify_script=verify_script,
            )

            # worker_fn や worker_cmd_factory を渡さず、標準の CLI サブプロセス並行実行を実行
            multi_res = orchestrator.execute_multi_team_pipeline(
                config=config,
                verify_script=verify_script,
                cleanup=True,
            )

            self.assertTrue(multi_res.success, f"Pipeline failed: {multi_res.details}")
            self.assertEqual(multi_res.status, "SUCCESS")
            self.assertEqual(multi_res.release_candidate_tag, f"rc/{job_id}")
            self.assertIsNotNone(multi_res.release_candidate_commit)

            # Git タグの存在とコミットの一致
            tag_chk = self._run_git(["rev-parse", "--verify", f"refs/tags/rc/{job_id}"])
            self.assertEqual(tag_chk.returncode, 0)
            self.assertEqual(tag_chk.stdout.strip(), multi_res.release_candidate_commit)

            # 各チームコミットが RC の祖先であること
            for tid in ["team-core", "team-ui"]:
                c = multi_res.team_results[tid].head_commit
                assert c is not None
                anc_chk = self._run_git(["merge-base", "--is-ancestor", c, multi_res.release_candidate_commit])
                self.assertEqual(anc_chk.returncode, 0)

            # Worktree がクリーンアップされ、Evidence Bundle は外出し保全されていること
            expected_evidence_dir = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence"
            for tid in ["team-core", "team-ui"]:
                wt_dir = orchestrator.worktree_manager.worktree_base / tid
                self.assertFalse(wt_dir.exists())

                bundle_p = multi_res.team_results[tid].bundle_path
                self.assertIsNotNone(bundle_p)
                assert bundle_p is not None
                self.assertTrue(bundle_p.exists())
                self.assertEqual(bundle_p.resolve(), (expected_evidence_dir / f"{tid}.zip").resolve())

                # バンドルが VERIFIED であること
                bundler = EvidenceBundler(repo_dir=self.repo_dir)
                v_res = bundler.verify_bundle(bundle_p)
                self.assertEqual(v_res.status, "VERIFIED")
                self.assertEqual(len(v_res.errors), 0)
        finally:
            if verify_script.exists():
                verify_script.unlink()

    def test_cleanup_workers_preserves_worktree_when_evidence_preservation_fails(self) -> None:
        """NN.1: Evidence 退避失敗時に原本 Worktree が削除されず保全されることの実証。"""
        job_id = "job-preserve-fail-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-preserve-target"),
            ],
            base_branch="main",
        )

        def mock_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
            target = wt_path / "work.txt"
            target.write_text("important work\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", "feat: work"], cwd=wt_path, check=True)
            rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
            tree_sha = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
            import hashlib
            spec_sha = hashlib.sha256((wt_path / "SPEC.md").read_bytes()).hexdigest()

            ev_store = EvidenceStore(repo_dir=wt_path)
            ev_store.issue_evidence(
                task_id=f"task-{team_id}",
                checkpoint=self.base_commit,
                tree_sha=tree_sha,
                spec_sha=spec_sha,
            )
            ev_store.archive_record(
                task_id=f"task-{team_id}",
                status="COMPLETED",
                commit_info={"sha": rev, "tree_sha": tree_sha},
                checker_info={"decision": "APPROVE", "reason": "Approved"},
                verification_info={"exit_code": 0},
            )
            bundles_dir = wt_path / ".dev" / "bundles"
            bundles_dir.mkdir(parents=True, exist_ok=True)
            bundle_p = bundles_dir / f"bundle_{team_id}.zip"
            bundler = EvidenceBundler(repo_dir=wt_path)
            bundler.create_bundle(dest_path=bundle_p)

            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                branch=f"team/{team_id}",
                head_commit=rev,
                bundle_path=bundle_p,
            )

        wt_path = orchestrator.worktree_manager.worktree_base / "team-preserve-target"

        # shutil.copy2 に例外を注入して退避コピーを失敗させる
        with unittest.mock.patch("shutil.copy2", side_effect=OSError("injected storage failure")):
            multi_res = orchestrator.execute_multi_team_pipeline(
                config=config,
                worker_fn=mock_worker,
                cleanup=True,
            )

        # 統合は拒否されること
        self.assertFalse(multi_res.success)
        self.assertEqual(multi_res.status, "WORKER_FAILED")
        self.assertEqual(multi_res.team_results["team-preserve-target"].status, "BUNDLE_PRESERVATION_FAILED")

        # 原本保護: 退避に失敗した Worktree および内部の Bundle が削除されずに残っていること
        self.assertTrue(wt_path.exists(), f"Original worktree {wt_path} must be preserved when preservation fails")
        original_bundle = wt_path / ".dev" / "bundles" / "bundle_team-preserve-target.zip"
        self.assertTrue(original_bundle.exists(), f"Original bundle {original_bundle} must be preserved on disk")

        # 手動で後片付け
        orchestrator.worktree_manager.remove_worktree("team-preserve-target", force=True, delete_branch=True, purge_unmerged=True)

    def test_preserve_team_bundles_uses_atomic_replace_and_cleans_temp_file(self) -> None:
        """NN.2, QQ.1: preserve_team_bundles がテンポラリファイルと os.link を用いて排他的・原子的に保全することの実証。"""
        job_id = "job-atomic-preserve-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-atomic"),
            ],
            base_branch="main",
        )

        dummy_bundle = self.repo_dir / "dummy_bundle.zip"
        dummy_bundle.write_bytes(b"PK\x03\x04test_bundle_bytes_data")

        team_res = {
            "team-atomic": TeamExecutionResult(
                team_id="team-atomic",
                success=True,
                status="SUCCESS",
                bundle_path=dummy_bundle,
            )
        }

        link_calls: list[tuple[Any, Any]] = []
        orig_link = os.link

        def tracking_link(src: Any, dst: Any, **kwargs: object) -> None:
            link_calls.append((Path(src), Path(dst)))
            orig_link(src, dst, **kwargs)

        with unittest.mock.patch("os.link", side_effect=tracking_link):
            orchestrator.preserve_team_bundles(config, team_res)

        res = team_res["team-atomic"]
        self.assertTrue(res.evidence_preserved)
        self.assertEqual(res.status, "SUCCESS")
        dest_expected = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "team-atomic.zip"
        self.assertEqual(res.bundle_path.resolve(), dest_expected.resolve())
        self.assertTrue(dest_expected.exists())

        # os.link がテンポラリファイルから最終目的地へ呼び出されていること
        self.assertEqual(len(link_calls), 1)
        src_path, dst_path = link_calls[0]
        self.assertEqual(dst_path.resolve(), dest_expected.resolve())
        self.assertTrue(src_path.name.startswith("team-atomic.zip.tmp_"))

        # テンポラリファイルが残存していないこと
        evidence_dir = dest_expected.parent
        tmp_files = list(evidence_dir.glob("*.tmp_*"))
        self.assertEqual(len(tmp_files), 0)

    def test_execute_multi_team_pipeline_partial_preservation_failure_preserves_only_failed_worktree(self) -> None:
        """OO.1: 部分的な退避失敗時、成功チームのWorktreeのみ解放し、失敗チームの原本Worktreeを確実に保全することの実証。"""
        job_id = "job-partial-preserve-fail"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-preserve-ok"),
                TeamWorkerSpec(team_id="team-preserve-ng"),
            ],
            base_branch="main",
        )

        def mock_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
            target = wt_path / f"{team_id}.txt"
            target.write_text(f"work of {team_id}\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", f"feat: {team_id}"], cwd=wt_path, check=True)
            rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
            tree_sha = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
            import hashlib
            spec_sha = hashlib.sha256((wt_path / "SPEC.md").read_bytes()).hexdigest()

            ev_store = EvidenceStore(repo_dir=wt_path)
            ev_store.issue_evidence(
                task_id=f"task-{team_id}",
                checkpoint=self.base_commit,
                tree_sha=tree_sha,
                spec_sha=spec_sha,
            )
            ev_store.archive_record(
                task_id=f"task-{team_id}",
                status="COMPLETED",
                commit_info={"sha": rev, "tree_sha": tree_sha},
                checker_info={"decision": "APPROVE", "reason": "Approved"},
                verification_info={"exit_code": 0},
            )
            bundles_dir = wt_path / ".dev" / "bundles"
            bundles_dir.mkdir(parents=True, exist_ok=True)
            bundle_p = bundles_dir / f"bundle_{team_id}.zip"
            bundler = EvidenceBundler(repo_dir=wt_path)
            bundler.create_bundle(dest_path=bundle_p)

            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                branch=f"team/{team_id}",
                head_commit=rev,
                bundle_path=bundle_p,
            )

        wt_ok = orchestrator.worktree_manager.worktree_base / "team-preserve-ok"
        wt_ng = orchestrator.worktree_manager.worktree_base / "team-preserve-ng"

        orig_copy2 = shutil.copy2

        def selective_copy2(src: Any, dst: Any, **kwargs: Any) -> Any:
            if "team-preserve-ng" in str(dst):
                raise OSError("injected disk failure for team-preserve-ng")
            return orig_copy2(src, dst, **kwargs)

        with unittest.mock.patch("shutil.copy2", side_effect=selective_copy2):
            multi_res = orchestrator.execute_multi_team_pipeline(
                config=config,
                worker_fn=mock_worker,
                cleanup=True,
            )

        # 統合結果の検証: 片方失敗のため全体は WORKER_FAILED
        self.assertFalse(multi_res.success)
        self.assertEqual(multi_res.status, "WORKER_FAILED")

        # team-preserve-ok: 退避成功 & Worktree クリーンアップ完了
        res_ok = multi_res.team_results["team-preserve-ok"]
        self.assertTrue(res_ok.evidence_preserved)
        self.assertFalse(wt_ok.exists(), "Worktree for successfully preserved team-preserve-ok must be cleaned up")
        preserved_ok_bundle = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence" / "team-preserve-ok.zip"
        self.assertTrue(preserved_ok_bundle.exists())

        # team-preserve-ng: 退避失敗 & 原本 Worktree が確実に保持されていること
        res_ng = multi_res.team_results["team-preserve-ng"]
        self.assertFalse(res_ng.evidence_preserved)
        self.assertEqual(res_ng.status, "BUNDLE_PRESERVATION_FAILED")
        self.assertTrue(wt_ng.exists(), "Worktree for failed team-preserve-ng must be preserved on disk")
        original_ng_bundle = wt_ng / ".dev" / "bundles" / "bundle_team-preserve-ng.zip"
        self.assertTrue(original_ng_bundle.exists(), "Original bundle must remain physically intact in preserved worktree")

        # 手動クリーンアップ
        orchestrator.worktree_manager.remove_worktree("team-preserve-ng", force=True, delete_branch=True, purge_unmerged=True)

    def test_preserve_team_bundles_idempotent_when_existing_bundle_identical(self) -> None:
        """PP.1: 保存先に同一ハッシュのEvidence Bundleが既に存在する場合、上書きせず冪等に再利用することの実証。"""
        job_id = "job-idempotent-preserve-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-idempotent"),
            ],
            base_branch="main",
        )

        evidence_dir = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        dest_path = evidence_dir / "team-idempotent.zip"
        bundle_content = b"PK\x03\x04identical_evidence_content_bytes"
        dest_path.write_bytes(bundle_content)

        # ワーカー側の成果物 Bundle も同一内容
        worker_bundle = self.repo_dir / "worker_bundle.zip"
        worker_bundle.write_bytes(bundle_content)

        team_res = {
            "team-idempotent": TeamExecutionResult(
                team_id="team-idempotent",
                success=True,
                status="SUCCESS",
                bundle_path=worker_bundle,
            )
        }

        with unittest.mock.patch("shutil.copy2") as mock_copy, unittest.mock.patch("os.replace") as mock_replace:
            orchestrator.preserve_team_bundles(config, team_res)
            # 同一ハッシュのため上書きコピーおよび置換が実行されないこと
            mock_copy.assert_not_called()
            mock_replace.assert_not_called()

        res = team_res["team-idempotent"]
        self.assertTrue(res.evidence_preserved)
        self.assertEqual(res.status, "SUCCESS")
        self.assertEqual(res.bundle_path.resolve(), dest_path.resolve())
        self.assertEqual(dest_path.read_bytes(), bundle_content)

    def test_preserve_team_bundles_refuses_overwrite_on_different_hash_collision(self) -> None:
        """PP.2: 保存先に異なるハッシュのEvidence Bundleが存在する場合、上書きを拒否し原本Worktreeを保持することの実証。"""
        job_id = "job-collision-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-collision-target"),
            ],
            base_branch="main",
        )

        # 1. 恒久保存先に既存の別 Evidence Bundle をあらかじめ配置
        evidence_dir = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        dest_path = evidence_dir / "team-collision-target.zip"
        old_content = b"PK\x03\x04ORIGINAL_PAST_EVIDENCE_BYTES"
        dest_path.write_bytes(old_content)

        # 2. ワーカーが新しい別内容の Bundle を生成
        new_content = b"PK\x03\x04UNRELATED_NEW_EVIDENCE_BYTES"

        def mock_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
            target = wt_path / "new_work.txt"
            target.write_text("new work\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", "feat: new work"], cwd=wt_path, check=True)
            rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()

            bundles_dir = wt_path / ".dev" / "bundles"
            bundles_dir.mkdir(parents=True, exist_ok=True)
            bundle_p = bundles_dir / f"bundle_{team_id}.zip"
            bundle_p.write_bytes(new_content)

            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                branch=f"team/{team_id}",
                head_commit=rev,
                bundle_path=bundle_p,
            )

        wt_path = orchestrator.worktree_manager.worktree_base / "team-collision-target"

        multi_res = orchestrator.execute_multi_team_pipeline(
            config=config,
            worker_fn=mock_worker,
            cleanup=True,
        )

        # 衝突検知によりパイプラインは WORKER_FAILED となること
        self.assertFalse(multi_res.success)
        self.assertEqual(multi_res.status, "WORKER_FAILED")

        team_res = multi_res.team_results["team-collision-target"]
        self.assertFalse(team_res.evidence_preserved)
        self.assertEqual(team_res.status, "BUNDLE_PRESERVATION_COLLISION")

        # 既存の Evidence Bundle が上書きされず、旧内容が100%保持されていること
        self.assertEqual(dest_path.read_bytes(), old_content)

        # 新規 Worktree が原本保護され、ディスク上に残っていること
        self.assertTrue(wt_path.exists(), "Original worktree must be preserved on collision")
        new_bundle = wt_path / ".dev" / "bundles" / "bundle_team-collision-target.zip"
        self.assertTrue(new_bundle.exists())
        self.assertEqual(new_bundle.read_bytes(), new_content)

        # 手動クリーンアップ
        orchestrator.worktree_manager.remove_worktree("team-collision-target", force=True, delete_branch=True, purge_unmerged=True)

    def test_preserve_team_bundles_exclusive_link_handles_concurrent_file_exists(self) -> None:
        """QQ.1: os.link による排他的アトミック確定と、並行作成時の FileExistsError 照合（同一なら再利用、異なれば拒否）。"""
        job_id = "job-atomic-link-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-link-target"),
            ],
            base_branch="main",
        )

        evidence_dir = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence"
        dest_path = evidence_dir / "team-link-target.zip"
        bundle_content = b"PK\x03\x04NEW_TEAM_BUNDLE_BYTES"
        worker_bundle = self.repo_dir / "worker_link_bundle.zip"
        worker_bundle.write_bytes(bundle_content)

        # 1. 正常系: 新規確定時に os.link が使用され、tmp_path がアンリンクされ確定すること
        team_res = {
            "team-link-target": TeamExecutionResult(
                team_id="team-link-target",
                success=True,
                status="SUCCESS",
                bundle_path=worker_bundle,
            )
        }

        with unittest.mock.patch("os.link", wraps=os.link) as spy_link:
            orchestrator.preserve_team_bundles(config, team_res)
            self.assertTrue(spy_link.called, "os.link must be called for exclusive publication")

        res = team_res["team-link-target"]
        self.assertTrue(res.evidence_preserved)
        self.assertEqual(res.bundle_path.resolve(), dest_path.resolve())
        self.assertEqual(dest_path.read_bytes(), bundle_content)
        # tmp ファイルが残存していないこと
        tmp_files = list(evidence_dir.glob("*.tmp_*"))
        self.assertEqual(tmp_files, [])

        # 2. FileExistsError (同一ハッシュ): リンク直前に他プロセスが同一Bundleを作成した場合
        #    -> 冪等成功として扱い、tmpファイルは削除され、evidence_preserved=True となること
        dest_path.unlink()
        worker_bundle2 = self.repo_dir / "worker_link_bundle2.zip"
        worker_bundle2.write_bytes(bundle_content)
        team_res2 = {
            "team-link-target": TeamExecutionResult(
                team_id="team-link-target",
                success=True,
                status="SUCCESS",
                bundle_path=worker_bundle2,
            )
        }

        real_link = os.link
        def link_with_concurrent_identical(src: str | Path, dst: str | Path, **kwargs: object) -> None:
            # os.link の直前に別プロセスが同一内容のファイルを配置した状況を再現
            Path(dst).write_bytes(bundle_content)
            # 既存ファイルがあるため os.link は FileExistsError を送出する
            real_link(src, dst, **kwargs)

        with unittest.mock.patch("os.link", side_effect=link_with_concurrent_identical):
            orchestrator.preserve_team_bundles(config, team_res2)

        res2 = team_res2["team-link-target"]
        self.assertTrue(res2.evidence_preserved)
        self.assertEqual(res2.status, "SUCCESS")
        self.assertEqual(res2.bundle_path.resolve(), dest_path.resolve())
        self.assertEqual(list(evidence_dir.glob("*.tmp_*")), [])

        # 3. FileExistsError (異ハッシュ衝突): リンク直前に他プロセスが異なるBundleを作成した場合
        #    -> 上書き拒絶 (BUNDLE_PRESERVATION_COLLISION)、dest_path は破壊されず、evidence_preserved=False
        dest_path.unlink()
        different_content = b"PK\x03\x04DIFFERENT_CONCURRENT_CONTENT"
        worker_bundle3 = self.repo_dir / "worker_link_bundle3.zip"
        worker_bundle3.write_bytes(bundle_content)
        team_res3 = {
            "team-link-target": TeamExecutionResult(
                team_id="team-link-target",
                success=True,
                status="SUCCESS",
                bundle_path=worker_bundle3,
            )
        }

        def link_with_concurrent_different(src: str | Path, dst: str | Path, **kwargs: object) -> None:
            # os.link の直前に別プロセスが異なる内容のファイルを配置した状況を再現
            Path(dst).write_bytes(different_content)
            real_link(src, dst, **kwargs)

        with unittest.mock.patch("os.link", side_effect=link_with_concurrent_different):
            orchestrator.preserve_team_bundles(config, team_res3)

        res3 = team_res3["team-link-target"]
        self.assertFalse(res3.evidence_preserved)
        self.assertEqual(res3.status, "BUNDLE_PRESERVATION_COLLISION")
        self.assertFalse(res3.success)
        # 既存ファイルが破壊されず維持されていること
        self.assertEqual(dest_path.read_bytes(), different_content)
        # tmp ファイルがクリーンアップされていること
        self.assertEqual(list(evidence_dir.glob("*.tmp_*")), [])

    def test_preserve_team_bundles_toctou_race_pipeline_injection(self) -> None:
        """QQ.2: 存在確認直後の他プロセスファイル作成（TOCTOU競合注入）における上書き阻止・原本保護と同一内容時の冪等統合。"""
        job_id = "job-toctou-pipeline-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-toctou"),
            ],
            base_branch="main",
        )

        with tempfile.NamedTemporaryFile(suffix=".sh", mode="w", delete=False) as tf:
            tf.write("#!/bin/bash\nexit 0\n")
            verify_script = Path(tf.name)
        verify_script.chmod(0o755)

        evidence_dir = self.repo_dir / ".dev" / "integration_jobs" / job_id / "evidence"
        dest_path = evidence_dir / "team-toctou.zip"
        differing_content = b"PK\x03\x04OLD_EVIDENCE_APPEARED_AFTER_CHECK"

        def mock_worker(team_id: str, wt_path: Path) -> TeamExecutionResult:
            target = wt_path / "work.txt"
            target.write_text("worker code\n", encoding="utf-8")
            subprocess.run(["git", "add", "."], cwd=wt_path, check=True)
            subprocess.run(["git", "commit", "-m", f"feat: {team_id}"], cwd=wt_path, check=True)
            rev = subprocess.run(["git", "rev-parse", "HEAD"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
            tree_sha = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=wt_path, capture_output=True, text=True, check=True).stdout.strip()
            spec_sha = hashlib.sha256((wt_path / "SPEC.md").read_bytes()).hexdigest()

            ev_store = EvidenceStore(repo_dir=wt_path)
            ev_store.issue_evidence(task_id=f"task-{team_id}", checkpoint=self.base_commit, tree_sha=tree_sha, spec_sha=spec_sha)
            ev_store.archive_record(
                task_id=f"task-{team_id}",
                status="COMPLETED",
                commit_info={"sha": rev, "tree_sha": tree_sha},
                checker_info={"decision": "APPROVE", "reason": "ok"},
                verification_info={"exit_code": 0},
            )

            bundles_dir = wt_path / ".dev" / "bundles"
            bundles_dir.mkdir(parents=True, exist_ok=True)
            bundle_p = bundles_dir / f"bundle_{team_id}.zip"
            bundler = EvidenceBundler(repo_dir=wt_path)
            bundler.create_bundle(dest_path=bundle_p)

            return TeamExecutionResult(
                team_id=team_id,
                success=True,
                status="SUCCESS",
                branch=f"team/{team_id}",
                head_commit=rev,
                bundle_path=bundle_p,
            )

        try:
            # 1. 競合シナリオ (異なる内容): チェック直後に別プロセスが別内容を書き込んだ場合
            orig_copy2 = shutil.copy2
            def inject_differing_after_check(src: Any, dst: Any, **kwargs: object) -> Any:
                # 存在チェック (exists) の直後、tmpへのコピー中に別プロセスが dest_path を作成した状況を再現
                dest_path.write_bytes(differing_content)
                return orig_copy2(src, dst, **kwargs)

            wt_path = orchestrator.worktree_manager.worktree_base / "team-toctou"

            with unittest.mock.patch("shutil.copy2", side_effect=inject_differing_after_check):
                multi_res = orchestrator.execute_multi_team_pipeline(
                    config=config,
                    worker_fn=mock_worker,
                    verify_script=verify_script,
                    cleanup=True,
                )

            # パイプラインは WORKER_FAILED となること
            self.assertFalse(multi_res.success)
            self.assertEqual(multi_res.status, "WORKER_FAILED")

            team_res = multi_res.team_results["team-toctou"]
            self.assertFalse(team_res.evidence_preserved)
            self.assertEqual(team_res.status, "BUNDLE_PRESERVATION_COLLISION")

            # 既存の Evidence が上書きされず、他プロセスの内容が完全に保持されていること
            self.assertEqual(dest_path.read_bytes(), differing_content)

            # 新規 Worktree が原本保護され、ディスク上に残っていること
            self.assertTrue(wt_path.exists(), "Worktree must be preserved on TOCTOU collision")

            # 2. 競合シナリオ (同一内容): チェック直後に別プロセスが同一内容を書き込んだ場合
            #    -> 冪等に成功し、統合が完了して Worktree は正常にクリーンアップされること
            dest_path.unlink()
            orchestrator.worktree_manager.remove_worktree("team-toctou", force=True, delete_branch=True, purge_unmerged=True)

            def inject_identical_after_check(src: Any, dst: Any, **kwargs: object) -> Any:
                # src (ワーカーが作成した本物のBundle) と同一内容のファイルを先行書き込み
                dest_path.write_bytes(Path(src).read_bytes())
                return orig_copy2(src, dst, **kwargs)

            with unittest.mock.patch("shutil.copy2", side_effect=inject_identical_after_check):
                multi_res2 = orchestrator.execute_multi_team_pipeline(
                    config=config,
                    worker_fn=mock_worker,
                    verify_script=verify_script,
                    cleanup=True,
                )

            self.assertTrue(multi_res2.success)
            self.assertEqual(multi_res2.status, "SUCCESS")
            self.assertIsNotNone(multi_res2.release_candidate_commit)

            team_res2 = multi_res2.team_results["team-toctou"]
            self.assertTrue(team_res2.evidence_preserved)
            self.assertEqual(team_res2.status, "SUCCESS")
            # 成功時は Worktree が正常にクリーンアップされていること
            self.assertFalse(wt_path.exists(), "Worktree should be cleaned up after successful idempotent integration")
        finally:
            if verify_script.exists():
                verify_script.unlink()

    def test_run_workers_process_group_termination_and_orphan_prevention(self) -> None:
        """RR.2: タイムアウト時および強制停止時にプロセスグループ全体が回収され、孤児プロセスが残存しないことの実証。"""
        import time

        job_id = "job-orphan-prevention-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-orphan-test"),
            ],
            base_branch="main",
        )

        orchestrator.prepare_workers(config)
        wt_path = orchestrator.worktree_manager.worktree_base / "team-orphan-test"

        # 子孫プロセス（sleep 60）を起動して自身の終了を待つスクリプト
        # PIDをファイルに書き出す
        worker_script = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen(['sleep', '60'])\n"
            "with open('child.pid', 'w') as f:\n"
            "    f.write(str(p.pid))\n"
            "sys.stdout.write('CHILD_READY\\n')\n"
            "sys.stdout.flush()\n"
            "p.wait()\n"
        )

        def cmd_factory(team_id: str, path: Path) -> list[str]:
            return ["python3", "-c", worker_script]

        # 0.5 秒でタイムアウトさせる
        results = orchestrator.run_workers(
            config=config,
            worker_cmd_factory=cmd_factory,
            timeout_sec=0.5,
        )

        res = results["team-orphan-test"]
        self.assertFalse(res.success)
        self.assertEqual(res.status, "TIMEOUT")

        # 子孫プロセスのPIDを確認
        pid_file = wt_path / "child.pid"
        self.assertTrue(pid_file.exists(), "child.pid file should have been written by worker")
        child_pid = int(pid_file.read_text().strip())

        # 少し待って子プロセスがプロセスグループごと完全に終了していることを確認
        time.sleep(0.3)
        child_alive = True
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            child_alive = False

        self.assertFalse(child_alive, f"Child process {child_pid} must be terminated and not orphaned")

        # クリーンアップ
        orchestrator.worktree_manager.remove_worktree("team-orphan-test", force=True, delete_branch=True, purge_unmerged=True)

    def test_terminate_all_workers_terminates_running_subprocesses(self) -> None:
        """RR.2: terminate_all_workers が並行実行中の全ワーカーのプロセスグループを安全に回収することの実証。"""
        import concurrent.futures
        import time

        job_id = "job-terminate-all-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-term-1"),
                TeamWorkerSpec(team_id="team-term-2"),
            ],
            base_branch="main",
        )

        orchestrator.prepare_workers(config)
        wt1 = orchestrator.worktree_manager.worktree_base / "team-term-1"
        wt2 = orchestrator.worktree_manager.worktree_base / "team-term-2"

        worker_script = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen(['sleep', '60'])\n"
            "with open('child.pid', 'w') as f:\n"
            "    f.write(str(p.pid))\n"
            "sys.stdout.write('READY\\n')\n"
            "sys.stdout.flush()\n"
            "p.wait()\n"
        )

        def cmd_factory(team_id: str, path: Path) -> list[str]:
            return ["python3", "-c", worker_script]

        # 別スレッドで run_workers を開始
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(
                orchestrator.run_workers,
                config=config,
                worker_cmd_factory=cmd_factory,
                timeout_sec=30.0,
            )

            # 両方の PID ファイルが書き出されるまで待機（最大5秒）
            start_wait = time.time()
            pid1_file = wt1 / "child.pid"
            pid2_file = wt2 / "child.pid"
            while time.time() - start_wait < 5.0:
                if pid1_file.exists() and pid2_file.exists():
                    break
                time.sleep(0.05)

            self.assertTrue(pid1_file.exists())
            self.assertTrue(pid2_file.exists())
            cpid1 = int(pid1_file.read_text().strip())
            cpid2 = int(pid2_file.read_text().strip())

            # terminate_all_workers を呼び出して全ワーカーを強制回収
            term_results = orchestrator.terminate_all_workers(timeout=1.0)
            self.assertTrue(term_results.get("team-term-1", False))
            self.assertTrue(term_results.get("team-term-2", False))

            # run_workers の future が完了するまで待機
            worker_results = future.result(timeout=5.0)

        # 両方のチーム結果が非正常終了（FAILEDまたはCRASHED）となっていること
        for tid in ["team-term-1", "team-term-2"]:
            self.assertFalse(worker_results[tid].success)

        # 両方の子孫プロセスがプロセスグループごと停止していること
        time.sleep(0.3)
        for cpid in [cpid1, cpid2]:
            alive = True
            try:
                os.kill(cpid, 0)
            except ProcessLookupError:
                alive = False
            self.assertFalse(alive, f"Child process {cpid} should be terminated")

        for tid in ["team-term-1", "team-term-2"]:
            orchestrator.worktree_manager.remove_worktree(tid, force=True, delete_branch=True, purge_unmerged=True)

    def test_terminate_worker_kills_grandchild_when_parent_worker_exits_first(self) -> None:
        """SS.1: 親ワーカープロセスが先行終了した場合でも、起動時保持PGIDによって孫プロセスが確実に回収されることの実証。"""
        import time

        job_id = "job-parent-exited-first-test"
        orchestrator = MultiTeamOrchestrator(repo_dir=self.repo_dir)
        config = MultiTeamConfig(
            job_id=job_id,
            teams=[
                TeamWorkerSpec(team_id="team-parent-exit"),
            ],
            base_branch="main",
        )

        orchestrator.prepare_workers(config)
        wt = orchestrator.worktree_manager.worktree_base / "team-parent-exit"

        # 孫プロセス（sleep 60）を起動し、PIDを書き出した後、親プロセスは直ちに exit(0) する
        worker_script = (
            "import subprocess, sys, time\n"
            "p = subprocess.Popen(['sleep', '60'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
            "with open('grandchild.pid', 'w') as f:\n"
            "    f.write(str(p.pid))\n"
            "sys.stdout.write('STARTED\\n')\n"
            "sys.stdout.flush()\n"
            "sys.exit(0)\n"
        )

        def cmd_factory(team_id: str, path: Path) -> list[str]:
            return ["python3", "-c", worker_script]

        results = orchestrator.run_workers(
            config=config,
            worker_cmd_factory=cmd_factory,
            timeout_sec=10.0,
        )

        # 親プロセスは正常終了している
        self.assertTrue(results["team-parent-exit"].success)

        # 孫プロセスのPIDを確認
        pid_file = wt / "grandchild.pid"
        self.assertTrue(pid_file.exists())
        grandchild_pid = int(pid_file.read_text().strip())

        # 親は終了したが孫プロセスはまだ動いている
        alive_before = True
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            alive_before = False
        self.assertTrue(alive_before, "Grandchild should initially still be running")

        # terminate_worker_process_group を呼んで孫プロセスを回収できるか検証
        # (親が死んでいるため、起動時に保持したPGIDが必要)
        terminated = orchestrator.terminate_worker_process_group("team-parent-exit", timeout=1.0)
        self.assertTrue(terminated, "terminate_worker_process_group should succeed using stored PGID")

        time.sleep(0.3)
        alive_after = True
        try:
            os.kill(grandchild_pid, 0)
        except ProcessLookupError:
            alive_after = False
        self.assertFalse(alive_after, f"Grandchild process {grandchild_pid} must be terminated even if parent exited first")

        orchestrator.worktree_manager.remove_worktree("team-parent-exit", force=True, delete_branch=True, purge_unmerged=True)


if __name__ == "__main__":
    unittest.main()
