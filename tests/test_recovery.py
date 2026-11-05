import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.models import JobState
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import CommandRunnerAdapter


class TestRecoveryBaseBranch(unittest.TestCase):
    def test_job_state_base_branch_persistence(self):
        """機能V: JobState に base_branch が記録され、保存・復元できること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            state_file = Path(tmpdir) / "job_state.json"
            state = JobState(
                job_id="test-job-1",
                base_branch="feature/experiment",
            )
            state.save(state_file)

            loaded = JobState.load(state_file)
            self.assertEqual(loaded.base_branch, "feature/experiment")

    def test_orchestrator_initializes_base_branch(self):
        """機能V: Orchestrator が起動時に現在の Git ブランチを base_branch として保存すること。"""
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "custom-main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\nlogs/\n", encoding="utf-8")
            (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. タスク1\n", encoding="utf-8")
            verify_sh = repo / "verify.sh"
            verify_sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            verify_sh.chmod(0o755)

            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            class DummyRunner(CommandRunnerAdapter):
                def run_maker(self, task, context):
                    (repo / "file.txt").write_text("done\n", encoding="utf-8")
                    return 0, "ok"

                def run_checker(self, task, diff, spec_content):
                    return True, "APPROVE"

            state_path = repo / ".dev" / "job_state.json"
            orchestrator = NightlyOrchestrator(
                repo_dir=repo,
                plan_path=repo / "PLAN.md",
                spec_path=repo / "SPEC.md",
                verify_script=verify_sh,
                report_path=repo / "NIGHTLY_REPORT.md",
                state_path=state_path,
                runner_adapter=DummyRunner(),
                dry_run=False,
            )
            exit_code = orchestrator.run()
            self.assertEqual(exit_code, 0)

            loaded_state = JobState.load(state_path)
            self.assertEqual(loaded_state.base_branch, "custom-main")


class TestRecoveryInspect(unittest.TestCase):
    def test_inspect_scenario_03_committed_ahead_of_state(self):
        """機能W: Gitコミット成功直後にクラッシュした場合、COMMITTED_AHEAD_OF_STATE と診断すること。"""
        from auto_developer.evidence import EvidenceStore
        from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [x] 1. タスク1\n", encoding="utf-8")
            (repo / "code.py").write_text("initial = 1\n", encoding="utf-8")

            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # タスク1の実装コミット（Git側は正常に確定）
            (repo / "code.py").write_text("initial = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            committed_tree = subprocess.run(["git", "write-tree"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "commit", "-m", "feat: task 1"], cwd=repo, check=True)
            head_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertNotEqual(head_commit, checkpoint)

            # Evidence を発行し、Phase E の commit 前に verify_and_consume で消費
            evidence_store = EvidenceStore(repo)
            evidence_store.issue_evidence(
                task_id="1",
                checkpoint=checkpoint,
                tree_sha=committed_tree,
                spec_sha="spec_sha",
            )
            evidence_store.verify_and_consume(
                task_id="1",
                checkpoint=checkpoint,
                expected_tree_sha=committed_tree,
            )

            # JobState は CHECKER フェーズで停止（コミット直後に SIGKILL された Scenario 03 の状態）
            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-crashed-03",
                status="RUNNING",
                current_task_id="1",
                current_phase="CHECKER",
                checkpoint_hash=checkpoint,
                base_branch="main",
                completed_tasks=[],
            )
            job_state.save(state_file)

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            report = engine.inspect()

            self.assertEqual(report.diagnosis, RecoveryDiagnosis.COMMITTED_AHEAD_OF_STATE)
            self.assertTrue(report.can_auto_recover)
            self.assertEqual(report.head_commit, head_commit)
            self.assertEqual(report.checkpoint_commit, checkpoint)
            self.assertEqual(report.task_id, "1")

    def test_inspect_scenario_04_quarantine_incomplete(self):
        """機能W: Quarantine処理の途中でクラッシュした場合、QUARANTINE_INCOMPLETE と診断すること。"""
        from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. 失敗タスク\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # Quarantine ブランチを作成して checkout（Scenario 04: ブランチ作成直後に SIGKILL）
            quarantine_branch = "quarantine/1-1793812132"
            subprocess.run(["git", "checkout", "-b", quarantine_branch], cwd=repo, check=True)

            # 未コミットの変更・未追跡ファイルが残存
            (repo / "probe.py").write_text("# dirty file\n", encoding="utf-8")
            quarantine_dir = repo / ".dev" / "quarantines"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            snapshot_file = quarantine_dir / "snapshot_task_1_1793812132.tar.gz"
            import tarfile
            with tarfile.open(snapshot_file, "w:gz") as tar:
                tar.add(repo / "probe.py", arcname="probe.py")

            # JobState は MAKER Attempt 2 のまま（Quarantine 保存前にクラッシュ）
            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-crashed-04",
                status="RUNNING",
                current_task_id="1",
                current_phase="MAKER",
                current_attempt=2,
                checkpoint_hash=checkpoint,
                base_branch="main",
            )
            job_state.save(state_file)

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            report = engine.inspect()

            self.assertEqual(report.diagnosis, RecoveryDiagnosis.QUARANTINE_INCOMPLETE)
            self.assertTrue(report.can_auto_recover)
            self.assertEqual(report.current_branch, quarantine_branch)
            self.assertEqual(report.base_branch, "main")
            self.assertIsNotNone(report.quarantine_snapshot)

    def test_inspect_scenario_01_02_crashed_in_flight(self):
        """機能W: Maker実行中や検証直後にクラッシュした場合、CRASHED_IN_FLIGHT と診断すること。"""
        from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. 実行中クラッシュタスク\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # 作業ツリーに未コミットファイルが存在
            (repo / "in_flight.py").write_text("wip = True\n", encoding="utf-8")

            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-crashed-01",
                status="RUNNING",
                current_task_id="1",
                current_phase="MAKER",
                checkpoint_hash=checkpoint,
                base_branch="main",
            )
            job_state.save(state_file)

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            report = engine.inspect()

            self.assertEqual(report.diagnosis, RecoveryDiagnosis.CRASHED_IN_FLIGHT)
            self.assertTrue(report.can_auto_recover)
            self.assertEqual(report.checkpoint_commit, checkpoint)
            self.assertFalse(report.tree_clean)

    def test_inspect_unexplained_head_advance_is_unknown_inconsistency(self):
        """機能W (P0-3): Evidenceのない未説明のHEAD前進は HEALTHY と誤判定せず UNKNOWN_INCONSISTENCY と診断すること。"""
        from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. タスク\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # 不明なコミットを作成して HEAD を前進させる（Evidence Record は作成しない）
            (repo / "unexplained.py").write_text("x = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "unexplained commit"], cwd=repo, check=True)
            head_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertNotEqual(head_commit, checkpoint)

            # JobState は RUNNING のまま
            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-unexplained",
                status="RUNNING",
                current_task_id="1",
                checkpoint_hash=checkpoint,
                base_branch="main",
            )
            job_state.save(state_file)

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            report = engine.inspect()

            # HEALTHY ではなく UNKNOWN_INCONSISTENCY と判定されること
            self.assertEqual(
                report.diagnosis,
                RecoveryDiagnosis.UNKNOWN_INCONSISTENCY,
                f"Unexplained HEAD advance must NOT be diagnosed as HEALTHY, got: {report.diagnosis}",
            )
            self.assertFalse(report.can_auto_recover, "Must NOT allow auto recovery for unexplained HEAD advance")


class TestRecoveryPlanAndApply(unittest.TestCase):
    def test_plan_and_apply_scenario_03_committed_ahead(self):
        """機能X: Scenario 03 の状態から plan を生成し、apply でコミットを再利用して JobState を COMPLETED に前進させること。"""
        from auto_developer.evidence import EvidenceStore
        from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. タスク1\n", encoding="utf-8")
            (repo / "code.py").write_text("initial = 1\n", encoding="utf-8")

            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # タスク1の実装コミット（Git側は正常に確定）
            (repo / "code.py").write_text("initial = 2\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [x] 1. タスク1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            committed_tree = subprocess.run(["git", "write-tree"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "commit", "-m", "feat: task 1"], cwd=repo, check=True)
            head_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertNotEqual(head_commit, checkpoint)

            # Evidence を発行し消費
            evidence_store = EvidenceStore(repo)
            evidence_store.issue_evidence(
                task_id="1",
                checkpoint=checkpoint,
                tree_sha=committed_tree,
                spec_sha="spec_sha",
            )
            evidence_store.verify_and_consume(
                task_id="1",
                checkpoint=checkpoint,
                expected_tree_sha=committed_tree,
            )

            # JobState は CHECKER フェーズで停止（コミット直後に SIGKILL された Scenario 03 の状態）
            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-crashed-03",
                status="RUNNING",
                current_task_id="1",
                current_phase="CHECKER",
                checkpoint_hash=checkpoint,
                base_branch="main",
                completed_tasks=[],
            )
            job_state.save(state_file)

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            plan = engine.plan()

            self.assertEqual(plan.diagnosis, RecoveryDiagnosis.COMMITTED_AHEAD_OF_STATE)
            self.assertTrue(plan.can_auto_recover)
            self.assertTrue(len(plan.actions) > 0)

            # 復旧適用
            result = engine.apply()
            self.assertTrue(result.success, f"Recovery apply failed: {result.message}")
            self.assertEqual(result.resulting_state, "COMPLETED")

            # 適用後の検証
            recovered_state = JobState.load(state_file)
            self.assertEqual(recovered_state.status, "COMPLETED")
            self.assertEqual(len(recovered_state.completed_tasks), 1)
            self.assertEqual(recovered_state.completed_tasks[0]["commit"], head_commit)

            # コミットが巻き戻されていないこと
            current_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(current_head, head_commit)

            # 再度 inspect すると HEALTHY になっていること（冪等性）
            post_report = engine.inspect()
            self.assertEqual(post_report.diagnosis, RecoveryDiagnosis.HEALTHY)

    def test_plan_and_apply_scenario_04_quarantine_incomplete(self):
        """機能X: Scenario 04 の状態から plan を生成し、apply でベースブランチ復帰と安全隔離を完了させること。"""
        from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\nlogs/\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. 失敗タスク\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # Quarantine ブランチを作成して checkout
            quarantine_branch = "quarantine/1-1793812132"
            subprocess.run(["git", "checkout", "-b", quarantine_branch], cwd=repo, check=True)

            # 未コミットの変更・未追跡ファイルが残存
            (repo / "probe.py").write_text("# dirty file\n", encoding="utf-8")
            quarantine_dir = repo / ".dev" / "quarantines"
            quarantine_dir.mkdir(parents=True, exist_ok=True)
            snapshot_file = quarantine_dir / "snapshot_task_1_1793812132.tar.gz"
            import tarfile
            with tarfile.open(snapshot_file, "w:gz") as tar:
                tar.add(repo / "probe.py", arcname="probe.py")

            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-crashed-04",
                status="RUNNING",
                current_task_id="1",
                current_phase="MAKER",
                current_attempt=2,
                checkpoint_hash=checkpoint,
                base_branch="main",
            )
            job_state.save(state_file)

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            plan = engine.plan()

            self.assertEqual(plan.diagnosis, RecoveryDiagnosis.QUARANTINE_INCOMPLETE)
            self.assertTrue(plan.can_auto_recover)

            # 復旧適用
            result = engine.apply()
            self.assertTrue(result.success, f"Recovery apply failed: {result.message}")
            self.assertEqual(result.resulting_state, "FAILED")

            # 適用後の検証
            # 1. カレントブランチが main に復帰していること
            current_branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(current_branch, "main")

            # 2. 作業ツリーが完全にクリーンであること
            status_out = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(status_out, "", f"Working tree must be clean, but got: {status_out}")

            # 3. HEAD が checkpoint と一致していること
            current_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(current_head, checkpoint)

            # 4. JobState が FAILED となり、quarantined_task が設定されていること
            recovered_state = JobState.load(state_file)
            self.assertEqual(recovered_state.status, "FAILED")
            self.assertIsNotNone(recovered_state.quarantined_task)
            self.assertEqual(recovered_state.quarantined_task["task_id"], "1")

            # 5. 隔離ブランチ (quarantine/1-1793812132) 側に probe.py が確実にコミットされていること！ (P0-2)
            quarantine_tree_files = subprocess.run(
                ["git", "ls-tree", "-r", "--name-only", quarantine_branch],
                cwd=repo,
                capture_output=True,
                text=True,
                check=True,
            ).stdout
            self.assertIn("probe.py", quarantine_tree_files, f"probe.py must be committed to quarantine branch, got: {quarantine_tree_files}")

            # 6. 有効な tar アーカイブが生成・検証され、probe.py が含まれていること！ (P0-2)
            import tarfile
            snapshots = list((repo / ".dev" / "quarantines").glob("snapshot_task_*.tar.gz"))
            self.assertTrue(len(snapshots) > 0, "Valid tar snapshot must exist")
            valid_snapshots = [s for s in snapshots if tarfile.is_tarfile(s)]
            self.assertTrue(len(valid_snapshots) > 0, "Snapshot must be a valid tarfile")
            with tarfile.open(valid_snapshots[-1], "r:gz") as tar:
                tar_names = tar.getnames()
                self.assertTrue(any("probe.py" in n for n in tar_names), f"probe.py must be in valid tar archive, got: {tar_names}")

    def test_plan_and_apply_scenario_01_crashed_in_flight_preserves_uncommitted_files(self):
        """機能X (P0-1): CRASHED_IN_FLIGHT の復旧時、未コミット・未追跡ファイルが消失せずアーカイブとブランチに完全保全されること。"""
        import tarfile
        from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. タスク\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # Maker が作成した未追跡ファイルと変更ファイル
            (repo / "feature.py").write_text("class Feature:\n    pass\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. タスク (wip)\n", encoding="utf-8")

            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-crashed-in-flight",
                status="RUNNING",
                current_task_id="1",
                current_phase="MAKER",
                checkpoint_hash=checkpoint,
                base_branch="main",
            )
            job_state.save(state_file)

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            plan = engine.plan()

            self.assertEqual(plan.diagnosis, RecoveryDiagnosis.CRASHED_IN_FLIGHT)
            self.assertTrue(plan.can_auto_recover)

            # 復旧適用
            result = engine.apply()
            self.assertTrue(result.success, f"Recovery apply failed: {result.message}")

            # 1. 本線作業ツリーはクリーンに checkpoint に復元されていること
            status_out = subprocess.run(["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertEqual(status_out, "", f"Working tree must be clean, but got: {status_out}")
            self.assertFalse((repo / "feature.py").exists(), "feature.py must not remain on base branch")

            # 2. しかし退避アーカイブ (.tar.gz) が存在し、feature.py が確実に含まれていること！ (P0-1)
            snapshots = list((repo / ".dev" / "quarantines").glob("snapshot_task_*.tar.gz"))
            self.assertTrue(len(snapshots) > 0, "Quarantine snapshot must be created before resetting work")
            with tarfile.open(snapshots[-1], "r:gz") as tar:
                names = tar.getnames()
                self.assertTrue(any("feature.py" in n for n in names), f"feature.py must be preserved in tar, got: {names}")

            # 3. 隔離ブランチが作成され、成果がコミットされていること！
            branches = subprocess.run(["git", "branch"], cwd=repo, capture_output=True, text=True, check=True).stdout
            self.assertIn("quarantine/", branches, f"Quarantine branch must exist, got: {branches}")


class TestRecoveryCLI(unittest.TestCase):
    def test_cli_recover_inspect_plan_apply(self):
        """機能X: auto-dev recover inspect / plan / apply サブコマンドが正常に動作すること。"""
        from auto_developer.cli import main
        from auto_developer.evidence import EvidenceStore

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. タスク1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # タスクコミット
            (repo / "code.py").write_text("val = 1\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            committed_tree = subprocess.run(["git", "write-tree"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "commit", "-m", "feat: task 1"], cwd=repo, check=True)
            head_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            self.assertNotEqual(head_commit, checkpoint)

            # Evidence
            evidence_store = EvidenceStore(repo)
            evidence_store.issue_evidence(task_id="1", checkpoint=checkpoint, tree_sha=committed_tree)
            evidence_store.verify_and_consume(task_id="1", checkpoint=checkpoint, expected_tree_sha=committed_tree)

            # Scenario 03 JobState
            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-crashed-cli",
                status="RUNNING",
                current_task_id="1",
                current_phase="CHECKER",
                checkpoint_hash=checkpoint,
                base_branch="main",
            )
            job_state.save(state_file)

            # 1. auto-dev recover inspect
            exit_code = main(["--work-dir", str(repo), "recover", "inspect"])
            self.assertEqual(exit_code, 1, "inspect should return 1 when inconsistency is diagnosed")

            # 2. auto-dev recover plan
            exit_code = main(["--work-dir", str(repo), "recover", "plan"])
            self.assertEqual(exit_code, 0)

            # 3. auto-dev recover apply
            exit_code = main(["--work-dir", str(repo), "recover", "apply"])
            self.assertEqual(exit_code, 0)

            # 4. apply 後の再 inspect は HEALTHY (exit 0) になること
            exit_code = main(["--work-dir", str(repo), "recover", "inspect"])
            self.assertEqual(exit_code, 0, "inspect should return 0 after successful recovery")

    def test_cli_recover_json_output(self):
        """機能X: auto-dev recover --json で有効なJSON文字列が出力されること。"""
        import io
        import json
        from unittest.mock import patch

        from auto_developer.cli import main

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            with patch("sys.stdout", new_callable=io.StringIO) as mock_out:
                exit_code = main(["--work-dir", str(repo), "recover", "inspect", "--json"])
                self.assertEqual(exit_code, 0)
                data = json.loads(mock_out.getvalue())
                self.assertEqual(data["diagnosis"], "HEALTHY")

    def test_plan_and_apply_scenario_03_multitask_keeps_job_running_for_remaining_tasks(self):
        """機能X (P1-1): 複数タスクの途中コミットでクラッシュした場合、後続タスクのためにRUNNINGと新checkpointを維持すること。"""
        from auto_developer.evidence import EvidenceStore
        from auto_developer.recovery import RecoveryDiagnosis, RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. タスク1\n- [ ] 2. タスク2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init checkpoint"], cwd=repo, check=True)
            checkpoint = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # タスク1の実装コミット
            (repo / "code1.py").write_text("x = 1\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [x] 1. タスク1\n- [ ] 2. タスク2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            committed_tree = subprocess.run(["git", "write-tree"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()
            subprocess.run(["git", "commit", "-m", "feat: task 1"], cwd=repo, check=True)
            head_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

            # Evidence
            evidence_store = EvidenceStore(repo)
            evidence_store.issue_evidence(task_id="1", checkpoint=checkpoint, tree_sha=committed_tree)
            evidence_store.verify_and_consume(task_id="1", checkpoint=checkpoint, expected_tree_sha=committed_tree)

            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-multitask",
                status="RUNNING",
                current_task_id="1",
                current_phase="CHECKER",
                checkpoint_hash=checkpoint,
                base_branch="main",
            )
            job_state.save(state_file)

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            result = engine.apply()
            self.assertTrue(result.success)

            # 後続タスク2が残っているため、ジョブ全体は COMPLETED ではなく RUNNING を維持し、
            # 次の checkpoint はタスク1のコミット (head_commit) に更新されていること！ (P1-1)
            recovered_state = JobState.load(state_file)
            self.assertEqual(recovered_state.status, "RUNNING")
            self.assertEqual(recovered_state.checkpoint_hash, head_commit)
            self.assertEqual(len(recovered_state.completed_tasks), 1)

    def test_recover_apply_rejects_when_orchestrator_process_is_actively_running(self):
        """機能X (P1-2): オーケストレータープロセスが現在実行中の場合、recover apply は排他制御により拒否すること。"""
        import os
        from auto_developer.recovery import RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-running",
                status="RUNNING",
                base_branch="main",
            )
            job_state.save(state_file)

            # 現在生存している自分自身の PID をロックファイルに記録
            lock_file = repo / ".dev" / "orchestrator.lock"
            lock_file.write_text(str(os.getpid()), encoding="utf-8")

            engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
            result = engine.apply()

            # 実行中プロセスがあるため、apply は拒否されなければならない
            self.assertFalse(result.success, "Must reject recovery apply while process is actively running")
            self.assertIn("actively running", result.message)

    def test_recover_apply_rejects_when_lock_is_held_by_flock(self):
        """機能X (P1-2): auto_developer.lock が flock で排他ロックされている場合、recover apply は拒否すること。"""
        import fcntl
        from auto_developer.recovery import RecoveryEngine

        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            state_file = repo / ".dev" / "job_state.json"
            job_state = JobState(
                job_id="job-running-lock",
                status="RUNNING",
                base_branch="main",
            )
            job_state.save(state_file)

            lock_file = repo / ".dev" / "auto_developer.lock"
            with open(lock_file, "w") as lock_fd:
                # 排他ロックを取得したまま保持
                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

                engine = RecoveryEngine(repo_dir=repo, state_path=state_file)
                result = engine.apply()

                # 排他ロック保持中のため拒否されること
                self.assertFalse(result.success, "Must reject recovery apply while flock is held")
                self.assertIn("exclusive lock held", result.message)

                fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)


if __name__ == "__main__":
    unittest.main()
