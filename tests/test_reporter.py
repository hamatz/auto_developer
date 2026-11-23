import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.reporter import MorningReporter


class TestMorningReporterEvidenceVerification(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Git リポジトリ初期化
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo, check=True)

        (self.repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True)

        self.records_dir = self.repo / ".git" / "nightly_evidence" / "records"
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_verified_evidence_record_displays_approved(self):
        """実 Evidence Record が存在し Tree SHA と Checker が正常なら APPROVE (Verified) と表示される。"""
        res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_tree = res_tree.stdout.strip()
        res_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_commit = res_head.stdout.strip()

        (self.records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "tree_sha": head_tree,
                "status": "COMPLETED",
                "checker": {"decision": "APPROVE", "reason": "All good"},
                "commit": {"tree_sha": head_tree, "sha": head_commit},
            }),
            encoding="utf-8",
        )

        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": head_commit,
                "completed_tasks": [
                    {
                        "task_id": "1",
                        "title": "Task 1",
                        "commit": head_commit,
                        "evidence_tree_sha": head_tree,
                        "committed_tree_sha": head_tree,
                    }
                ],
            },
        )

        content = report_path.read_text(encoding="utf-8")
        self.assertIn("APPROVE (Verified)", content)

    def test_missing_evidence_record_does_not_display_approved(self):
        """Evidence Record が存在しない場合、APPROVE を詐称せず UNVERIFIED と表示される。"""
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": "c1c0da8",
                "completed_tasks": [
                    {
                        "task_id": "99",
                        "title": "Unverified Task",
                        "commit": "c1c0da8",
                        # checker_decision を指定しない、またはあっても実レコードがない
                    }
                ],
            },
        )

        content = report_path.read_text(encoding="utf-8")
        self.assertNotIn("APPROVE (Verified)", content)
        self.assertTrue("UNVERIFIED" in content or "NO_RECORD" in content)

    def test_remote_guidance_omits_git_push_when_no_origin(self):
        """Git リモート (origin) が未設定の場合、git push origin main は案内しない。"""
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": "c1c0da8",
                "completed_tasks": [],
            },
        )

        content = report_path.read_text(encoding="utf-8")
        self.assertNotIn("git push origin", content)
        self.assertIn("git log", content)

    def test_write_report_ignores_archive_directory(self):
        """BDL-5: archive 配下の証跡レコードは読まず UNVERIFIED (No Evidence Record) となること。"""
        # テスト用コミット作成
        test_file = self.repo / "test_dd4.txt"
        test_file.write_text("dd4 verification", encoding="utf-8")
        subprocess.run(["git", "add", "test_dd4.txt"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "feat: test dd4"], cwd=self.repo, check=True)
        commit_sha = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()
        tree_sha = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True).stdout.strip()

        # archive 配下にレコードを配置
        archive_dir = self.repo / ".git" / "nightly_evidence" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        rec_file = archive_dir / "task_1.json"
        rec_file.write_text(
            json.dumps({
                "task_id": "1",
                "status": "COMPLETED",
                "tree_sha": tree_sha,
                "commit": {
                    "sha": commit_sha,
                    "tree_sha": tree_sha,
                },
                "checker": {
                    "decision": "APPROVE",
                    "reason": "Verified archive record",
                },
            }),
            encoding="utf-8",
        )

        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_path,
            {
                "status": "COMPLETED",
                "total_tasks": 1,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": commit_sha,
                "completed_tasks": [
                    {
                        "task_id": "1",
                        "title": "DD4 Task",
                        "commit": commit_sha,
                    }
                ],
            },
        )

        content = report_path.read_text(encoding="utf-8")
        self.assertNotIn(f"`{tree_sha[:7]}`", content)
        self.assertIn("UNVERIFIED (No Evidence Record)", content)

    def test_feature_www_5_audit_counts_split(self):
        """機能WWW.5: 監査ステージの結果として、登録（DISPATCHED）・Backlog 送り（BACKLOGGED）・エスカレーション（ESCALATED）の件数を分けて表示すること。"""
        from auto_developer.audit import AuditFinding

        dispatched_f = AuditFinding(
            finding_id="AUD-001",
            severity="P1",
            spec_reference="WWW.1",
            location="src/mod_a.py:10",
            description="Severe defect 1",
            reproduction_test_code="def test_fail(): assert False",
            status="DISPATCHED",
        )
        backlogged_f1 = AuditFinding(
            finding_id="AUD-002",
            severity="P2",
            spec_reference="WWW.1",
            location="src/mod_b.py:20",
            description="Minor edge case 1",
            reproduction_test_code="def test_edge(): pass",
            status="BACKLOGGED",
        )
        backlogged_f2 = AuditFinding(
            finding_id="AUD-003",
            severity="P2",
            spec_reference="WWW.1",
            location="src/mod_b.py:25",
            description="Minor edge case 2",
            reproduction_test_code="def test_edge2(): pass",
            status="BACKLOGGED",
        )
        escalated_f = AuditFinding(
            finding_id="AUD-004",
            severity="P1",
            spec_reference="WWW.2",
            location="src/mod_c.py:30",
            description="Recurring defect",
            reproduction_test_code="def test_rec(): assert False",
            status="ESCALATED",
            streak=5,
        )

        summary_data = {
            "status": "COMPLETED",
            "total_tasks": 1,
            "succeeded_count": 1,
            "quarantined_count": 0,
            "checkpoint": "c1c0da8",
            "completed_tasks": [],
            "audit_stage": {
                "status": "SUCCESS",
                "dispatched": [dispatched_f],
                "backlogged": [backlogged_f1, backlogged_f2],
                "escalated": [escalated_f],
                "rejected": [],
            },
        }

        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(report_path, summary_data)

        content = report_path.read_text(encoding="utf-8")
        self.assertIn("DISPATCHED", content)
        self.assertIn("BACKLOGGED", content)
        self.assertIn("ESCALATED", content)
        # 件数の確認: DISPATCHED 1件, BACKLOGGED 2件, ESCALATED 1件
        self.assertTrue("1 件" in content or "1件" in content)
        self.assertTrue("2 件" in content or "2件" in content)

    def test_feature_www_5_escalated_findings_listed_under_action_required(self):
        """機能WWW.5: ESCALATED が 1 件以上あれば「あなたに判断してほしいこと」節にモジュール名・streak・該当 finding を列挙すること。"""
        from auto_developer.audit import AuditFinding

        escalated_f = AuditFinding(
            finding_id="AUD-999",
            severity="P1",
            spec_reference="WWW.2",
            location="src/auto_developer/verifier.py:166",
            description="Repetitive failure in verifier",
            reproduction_test_code="def test_fail(): assert False",
            status="ESCALATED",
            streak=5,
        )

        summary_data = {
            "status": "COMPLETED",
            "total_tasks": 1,
            "succeeded_count": 1,
            "quarantined_count": 0,
            "checkpoint": "c1c0da8",
            "completed_tasks": [],
            "audit_stage": {
                "status": "SUCCESS",
                "dispatched": [],
                "backlogged": [],
                "escalated": [escalated_f],
                "rejected": [],
            },
        }

        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(report_path, summary_data)

        content = report_path.read_text(encoding="utf-8")
        # 「あなたに判断してほしいこと」節以降のテキストを取得
        section_idx = content.find("## あなたに判断してほしいこと")
        self.assertNotEqual(section_idx, -1)
        action_section = content[section_idx:]

        # モジュール名、streak、該当 finding の情報が含まれていること
        self.assertIn("src/auto_developer/verifier.py", action_section)
        self.assertIn("5", action_section)
        self.assertIn("AUD-999", action_section)
        self.assertIn("Repetitive failure in verifier", action_section)

    def test_duplicate_reported_separately_from_rejected(self) -> None:
        """XXX.2: 重複再報告が不正/偽Red排除数と分離されて表示されること。"""
        summary_data = {
            "status": "COMPLETED",
            "checkpoint": "abcdef1",
            "completed_tasks": [],
            "audit_stage": {
                "status": "SUCCESS",
                "dispatched": [],
                "backlogged": [],
                "escalated": [],
                "duplicates": [{"finding_id": "AUD-001"}],
                "rejected": [({"finding_id": "AUD-002"}, "TRIVIAL_RED")],
            },
        }
        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(report_path, summary_data)

        content = report_path.read_text(encoding="utf-8")
        self.assertIn("- **重複再報告 (DUPLICATE)**: 1 件", content)
        self.assertIn("- **不正/偽Red排除数**: 1 件", content)


class TestStandaloneReportGeneration(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Git リポジトリ初期化
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo, check=True)

        (self.repo / "README.md").write_text("# Test Repo\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True)

        self.records_dir = self.repo / ".git" / "nightly_evidence" / "records"
        self.records_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_generate_standalone_report_from_plan_and_evidence(self):
        """job_state.json がない環境でも PLAN.md、HEAD、evidence records から完全なレポートが生成されること (YYY.2)。"""
        # PLAN.md を作成
        plan_content = """# PLAN.md
## 現在の目標
- Phase 65: Test Phase

## ステップ
- [x] 1. First Task (YYY.1)
- [ ] 2. Second Task (YYY.2)
"""
        (self.repo / "PLAN.md").write_text(plan_content, encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "feat: complete task 1 [refs: 1]"], cwd=self.repo, check=True)

        res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_tree = res_tree.stdout.strip()
        res_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_commit = res_head.stdout.strip()

        # Evidence record を作成
        (self.records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "tree_sha": head_tree,
                "status": "COMPLETED",
                "checker": {"decision": "APPROVE", "reason": "All good", "model": "claude-opus-5.5"},
                "commit": {"tree_sha": head_tree, "sha": head_commit},
            }),
            encoding="utf-8",
        )

        reporter = MorningReporter(repo_dir=self.repo)
        report_path = self.repo / "NIGHTLY_REPORT.md"
        generated_path = reporter.generate_standalone_report(report_path=report_path, repo_dir=self.repo)

        self.assertEqual(generated_path, report_path)
        self.assertTrue(report_path.is_file())
        content = report_path.read_text(encoding="utf-8")

        # レポート内容の検証
        self.assertIn(head_commit, content)
        self.assertIn("1 / 2** 完了", content)
        self.assertIn("IN_PROGRESS", content)
        self.assertIn("`task-1`", content)
        self.assertIn("First Task", content)
        self.assertIn("APPROVE (Verified)", content)

    def test_cli_report_subcommand(self):
        """auto-dev report によりスタンドアロンレポートが正常に生成されること (YYY.2)。"""
        from auto_developer.cli import main

        # PLAN.md 作成
        (self.repo / "PLAN.md").write_text("# PLAN\n## ステップ\n- [x] 1. Done task\n", encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "task 1"], cwd=self.repo, check=True)

        report_target = self.repo / "custom_report.md"
        ret = main(["--work-dir", str(self.repo), "report", "--out", str(report_target)])
        self.assertEqual(ret, 0)
        self.assertTrue(report_target.is_file())
        self.assertIn("Done task", report_target.read_text(encoding="utf-8"))

    def test_cli_brief_auto_generates_when_no_job_state(self):
        """job_state.json がない場合でも auto-dev brief が最新 HEAD からレポートを生成・表示すること (YYY.2)。"""
        import io
        from unittest.mock import patch

        from auto_developer.cli import main

        (self.repo / "PLAN.md").write_text("# PLAN\n## ステップ\n- [x] 1. Auto brief task\n", encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "task 1"], cwd=self.repo, check=True)

        res_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_commit = res_head.stdout.strip()

        buf = io.StringIO()
        with patch("sys.stdout", buf):
            ret = main(["--work-dir", str(self.repo), "brief"])
        self.assertEqual(ret, 0)
        output = buf.getvalue()
        self.assertIn("Auto brief task", output)
        self.assertIn(head_commit, output)
        # NIGHTLY_REPORT.md が自動作成されていること
        self.assertTrue((self.repo / "NIGHTLY_REPORT.md").is_file())

    def test_generate_standalone_report_detects_tree_mismatch(self):
        """実コミットの Tree SHA と Evidence Record の Tree SHA が不一致なら TAMPERED (Tree Mismatch) となること。"""
        # PLAN.md 作成
        (self.repo / "PLAN.md").write_text("# PLAN\n## ステップ\n- [x] 1. Tampered task\n", encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "task 1 [refs: 1]"], cwd=self.repo, check=True)

        res_head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_commit = res_head.stdout.strip()

        # レコードに嘘の tree_sha を入れる
        (self.records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "tree_sha": "0000000000000000000000000000000000000000",
                "status": "COMPLETED",
                "checker": {"decision": "APPROVE", "reason": "Tampered"},
                "commit": {"tree_sha": "0000000000000000000000000000000000000000", "sha": head_commit},
            }),
            encoding="utf-8",
        )

        reporter = MorningReporter(repo_dir=self.repo)
        report_p = self.repo / "NIGHTLY_REPORT.md"
        reporter.generate_standalone_report(report_path=report_p, repo_dir=self.repo)
        content = report_p.read_text(encoding="utf-8")
        self.assertIn("TAMPERED (Tree Mismatch)", content)

    def test_uncommitted_task_not_misattributed_to_head(self):
        """完了マークはあるが証跡もコミットもないタスクが HEAD に誤帰属されず UNVERIFIED となること。"""
        (self.repo / "PLAN.md").write_text("# PLAN\n## ステップ\n- [x] 1. Phantom task\n", encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "unrelated commit"], cwd=self.repo, check=True)

        reporter = MorningReporter(repo_dir=self.repo)
        report_p = self.repo / "NIGHTLY_REPORT.md"
        reporter.generate_standalone_report(report_path=report_p, repo_dir=self.repo)
        content = report_p.read_text(encoding="utf-8")
        self.assertIn("UNVERIFIED (No Evidence Record)", content)
        self.assertIn("| `task-1` | Phantom task | `` |", content)

    def test_forged_record_with_nonexistent_or_unancestral_commit_is_tampered(self):
        """実在しないコミットや HEAD の祖先でないコミットを持つ Evidence Record は TAMPERED と判定されること。"""
        (self.repo / "PLAN.md").write_text("# PLAN\n## ステップ\n- [x] 1. Forged task\n", encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "legit commit"], cwd=self.repo, check=True)

        # 1. 存在しないコミット SHA を指定した偽装レコード
        (self.records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "tree_sha": "abcdef1234567890abcdef1234567890abcdef12",
                "status": "COMPLETED",
                "checker": {"decision": "APPROVE", "reason": "Forged"},
                "commit": {"tree_sha": "abcdef1234567890abcdef1234567890abcdef12", "sha": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"},
            }),
            encoding="utf-8",
        )

        reporter = MorningReporter(repo_dir=self.repo)
        report_p = self.repo / "NIGHTLY_REPORT.md"
        reporter.generate_standalone_report(report_path=report_p, repo_dir=self.repo)
        content = report_p.read_text(encoding="utf-8")
        self.assertIn("TAMPERED (Tree Mismatch)", content)

    def test_quarantine_branch_detected_in_standalone_report(self):
        """quarantine/* ブランチが存在する場合、要確認・隔離件数として検出・集計されること。"""
        (self.repo / "PLAN.md").write_text("# PLAN\n## ステップ\n- [ ] 1. Pending task\n", encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "init plan"], cwd=self.repo, check=True)

        # 隔離ブランチの作成
        subprocess.run(["git", "branch", "quarantine/task-1-test"], cwd=self.repo, check=True)

        reporter = MorningReporter(repo_dir=self.repo)
        report_p = self.repo / "NIGHTLY_REPORT.md"
        reporter.generate_standalone_report(report_path=report_p, repo_dir=self.repo)
        content = report_p.read_text(encoding="utf-8")
        self.assertIn("| **要確認・隔離** | **1** 件 |", content)

    def test_bdl_3_standalone_report_selects_only_completed_tasks_and_ignores_stale_records(self):
        """BDL-3: スタンドアロンレポートは対象計画の - [x] タスクのみを対象とし、未完了・範囲外・前フェーズ残骸の記録を含めないこと。"""
        (self.repo / "PLAN.md").write_text(
            "# PLAN\n\n"
            "<!--\n- [x] 90. Commented\n-->\n\n"
            "## ステップ\n"
            "- [x] 1. Done task\n"
            "- [ ] 2. Pending task\n\n"
            "## 別の節\n"
            "- [x] 91. Out of scope\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "task 1 [refs: 1]"], cwd=self.repo, check=True)
        head_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        head_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        for tid in ["1", "2", "8", "90", "91"]:
            (self.records_dir / f"task_{tid}.json").write_text(
                json.dumps({
                    "task_id": tid,
                    "tree_sha": head_tree,
                    "status": "COMPLETED",
                    "checker": {"decision": "APPROVE", "reason": "ok"},
                    "commit": {"tree_sha": head_tree, "sha": head_commit},
                }),
                encoding="utf-8",
            )

        reporter = MorningReporter(repo_dir=self.repo)
        report_p = self.repo / "NIGHTLY_REPORT.md"
        reporter.generate_standalone_report(report_path=report_p, repo_dir=self.repo)
        content = report_p.read_text(encoding="utf-8")

        self.assertIn("`task-1`", content)
        self.assertIn("APPROVE (Verified)", content)
        for excluded_id in ["2", "8", "90", "91"]:
            self.assertNotIn(f"`task-{excluded_id}`", content)

    def test_bdl_3_standalone_report_fallback_when_plan_has_no_readable_tasks(self):
        """BDL-3: 計画ファイルにタスクとして読める行がない場合は、従来どおり全記録を対象にすること。"""
        (self.repo / "PLAN.md").write_text("# PLAN\n- [x] Unnumbered task\n", encoding="utf-8")
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "unnumbered"], cwd=self.repo, check=True)
        head_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        head_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        (self.records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "tree_sha": head_tree,
                "status": "COMPLETED",
                "checker": {"decision": "APPROVE", "reason": "ok"},
                "commit": {"tree_sha": head_tree, "sha": head_commit},
            }),
            encoding="utf-8",
        )

        reporter = MorningReporter(repo_dir=self.repo)
        report_p = self.repo / "NIGHTLY_REPORT.md"
        reporter.generate_standalone_report(report_path=report_p, repo_dir=self.repo)
        content = report_p.read_text(encoding="utf-8")
        self.assertIn("`task-1`", content)
        self.assertIn("APPROVE (Verified)", content)

    def test_bdl_3_write_report_filters_uncompleted_or_stale_tasks_when_plan_present(self):
        """BDL-3: write_report も計画ファイルにタスクがある場合は完了タスクのみを対象にすること。"""
        (self.repo / "PLAN.md").write_text(
            "# PLAN\n\n## ステップ\n- [x] 1. Done task\n- [ ] 2. Pending task\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "PLAN.md"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "plan"], cwd=self.repo, check=True)
        head_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        head_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        for tid in ["1", "2", "8"]:
            (self.records_dir / f"task_{tid}.json").write_text(
                json.dumps({
                    "task_id": tid,
                    "tree_sha": head_tree,
                    "status": "COMPLETED",
                    "checker": {"decision": "APPROVE", "reason": "ok"},
                    "commit": {"tree_sha": head_tree, "sha": head_commit},
                }),
                encoding="utf-8",
            )

        reporter = MorningReporter(repo_dir=self.repo)
        report_p = self.repo / "NIGHTLY_REPORT.md"
        reporter.write_report(
            report_p,
            {
                "status": "COMPLETED",
                "total_tasks": 2,
                "succeeded_count": 1,
                "quarantined_count": 0,
                "checkpoint": head_commit,
                "completed_tasks": [
                    {"task_id": "1", "title": "Done task", "commit": head_commit},
                    {"task_id": "2", "title": "Pending task", "commit": head_commit},
                    {"task_id": "8", "title": "Stale task", "commit": head_commit},
                ],
            },
        )
        content = report_p.read_text(encoding="utf-8")
        self.assertIn("`task-1`", content)
        self.assertNotIn("`task-2`", content)
        self.assertNotIn("`task-8`", content)

    def test_bdl_3_team_worktree_report_uses_team_plan(self):
        """BDL-3: チーム worktree で auto-dev report / generate_standalone_report を呼ぶとチームの計画ファイルと証拠が使われること。"""
        from auto_developer.cli import main

        with tempfile.TemporaryDirectory() as td:
            repo = Path(td)
            subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
            subprocess.run(["git", "config", "user.name", "Test User"], cwd=repo, check=True)
            subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)

            (repo / ".gitignore").write_text(".dev/\nlogs/\nNIGHTLY_REPORT.md\n", encoding="utf-8")
            (repo / "SPEC.md").write_text("# SPEC\n- BDL-3\n", encoding="utf-8")
            (repo / "PLAN.md").write_text(
                "# Root PLAN\n\n## ステップ\n- [ ] 1. Root pending\n- [x] 9. Root done\n",
                encoding="utf-8",
            )
            (repo / "plans").mkdir(parents=True, exist_ok=True)
            (repo / "plans" / "core.md").write_text(
                "# PLAN (core)\n\n## ステップ\n- [ ] 1. Core task\n",
                encoding="utf-8",
            )
            (repo / "src" / "core").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "core" / "app.py").write_text("X = 1\n", encoding="utf-8")
            (repo / "teams.json").write_text(
                json.dumps({
                    "version": 1,
                    "project": "demo",
                    "base": {"ref": "main", "require_clean": True},
                    "teams": [
                        {"id": "core", "plan": "plans/core.md", "owns": ["src/core/**"], "depends_on": []},
                    ],
                }),
                encoding="utf-8",
            )
            subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
            subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

            main_records = repo / ".git" / "nightly_evidence" / "records"
            main_records.mkdir(parents=True, exist_ok=True)
            (main_records / "task_9.json").write_text(
                json.dumps({
                    "task_id": "9",
                    "commit": {"sha": "a" * 40, "tree_sha": "b" * 40},
                    "tree_sha": "b" * 40,
                    "checker": {"decision": "APPROVE"},
                    "status": "COMPLETED",
                }),
                encoding="utf-8",
            )

            self.assertEqual(main(["--work-dir", str(repo), "teams", "prepare", "--job-id", "job-rpt-bdl3"]), 0)
            core_wt = repo / ".dev" / "worktrees" / "core"
            (core_wt / "plans" / "core.md").write_text(
                "# PLAN (core)\n\n## ステップ\n- [x] 1. Core task\n",
                encoding="utf-8",
            )
            (core_wt / "src" / "core" / "app.py").write_text("X = 2\n", encoding="utf-8")
            subprocess.run(["git", "add", "-A"], cwd=core_wt, check=True)
            subprocess.run(["git", "commit", "-m", "core task 1 [refs: 1]"], cwd=core_wt, check=True)

            wt_head = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=core_wt, capture_output=True, text=True, check=True
            ).stdout.strip()
            wt_tree = subprocess.run(
                ["git", "rev-parse", "HEAD^{tree}"], cwd=core_wt, capture_output=True, text=True, check=True
            ).stdout.strip()
            wt_ev = core_wt / ".dev" / "evidence"
            wt_ev.mkdir(parents=True, exist_ok=True)
            (wt_ev / "task_1.json").write_text(
                json.dumps({
                    "task_id": "1",
                    "commit": {"sha": wt_head, "tree_sha": wt_tree},
                    "tree_sha": wt_tree,
                    "status": "COMPLETED",
                    "checker": {"decision": "APPROVE", "reason": "ok"},
                }),
                encoding="utf-8",
            )

            self.assertEqual(main(["--work-dir", str(core_wt), "report"]), 0)
            content = (core_wt / "NIGHTLY_REPORT.md").read_text(encoding="utf-8")
            self.assertIn("`task-1`", content)
            self.assertIn("Core task", content)
            self.assertIn("APPROVE (Verified)", content)
            self.assertNotIn("`task-9`", content)



if __name__ == "__main__":
    unittest.main()
