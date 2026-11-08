from __future__ import annotations

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


if __name__ == "__main__":
    unittest.main()
