from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.bundler import EvidenceBundler
from auto_developer.manifest import TeamManifest
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.runner import RunnerAdapter
from auto_developer.worktree import WorktreeManager


class RogueTeamRunnerAdapter(RunnerAdapter):
    """領域外 (src/core/app.py) およびルートファイルを不正に変更・作成する Maker。"""

    def __init__(self, repo_dir: Path) -> None:
        self.repo_dir = repo_dir

    def run_maker(self, task: Any, context: dict[str, Any]) -> tuple[int, str]:
        # 正当な UI 変更
        (self.repo_dir / "src" / "ui" / "view.py").write_text("# Legit UI\n", encoding="utf-8")
        # 領空侵犯 (Core の書き換え)
        (self.repo_dir / "src" / "core" / "app.py").write_text("# HACKED CORE\n", encoding="utf-8")
        # 未追跡の侵入ファイル
        (self.repo_dir / "rogue.sh").write_text("#!/bin/sh\necho rogue\n", encoding="utf-8")
        return 0, "Modified view.py and hacked core app.py"

    def run_checker(self, task: Any, diff: str, spec_content: str) -> tuple[bool, str]:
        return True, "Looks good"


class TestMultiTeamFoundation(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

        # テスト用リポジトリ初期化
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        # ディレクトリ構成
        (self.repo_dir / ".gitignore").write_text(".dev/\nNIGHTLY_REPORT.md\nlogs/\n", encoding="utf-8")
        (self.repo_dir / "src" / "core").mkdir(parents=True)
        (self.repo_dir / "src" / "ui").mkdir(parents=True)
        (self.repo_dir / "src" / "core" / "app.py").write_text("# Core Base\n", encoding="utf-8")
        (self.repo_dir / "src" / "ui" / "view.py").write_text("# UI Base\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo_dir, check=True)

        self.wt_manager = WorktreeManager(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        try:
            self.wt_manager.cleanup_all(force=True)
        except (subprocess.SubprocessError, OSError):
            pass
        self.temp_dir.cleanup()

    def test_multi_team_coexistence(self) -> None:
        """2つの独立Worktreeが競合なく共存し、メインブランチを汚染しないことを実証。"""
        # 1. Team Core と Team UI の Worktree 作成
        core_wt = self.wt_manager.create_worktree(team_id="team-core")
        ui_wt = self.wt_manager.create_worktree(team_id="team-ui")

        self.assertTrue(core_wt.exists())
        self.assertTrue(ui_wt.exists())

        # 2. Team Core が自領域を変更してコミット
        (core_wt / "src" / "core" / "app.py").write_text("# Core Updated by Team Core\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=core_wt, check=True)
        subprocess.run(["git", "commit", "-m", "feat(core): update core logic"], cwd=core_wt, check=True)

        # 3. Team UI が自領域を変更してコミット
        (ui_wt / "src" / "ui" / "view.py").write_text("# UI Updated by Team UI\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=ui_wt, check=True)
        subprocess.run(["git", "commit", "-m", "feat(ui): update ui view"], cwd=ui_wt, check=True)

        # 4. メインブランチがクリーンで未変更のままであることの検証
        main_core_text = (self.repo_dir / "src" / "core" / "app.py").read_text(encoding="utf-8")
        main_ui_text = (self.repo_dir / "src" / "ui" / "view.py").read_text(encoding="utf-8")
        self.assertEqual(main_core_text, "# Core Base\n")
        self.assertEqual(main_ui_text, "# UI Base\n")

        status_main = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(status_main.stdout.strip(), "")

        # 5. 各Worktreeのブランチが独立していることの検証
        core_branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=core_wt, check=True, capture_output=True, text=True
        ).stdout.strip()
        ui_branch = subprocess.run(
            ["git", "branch", "--show-current"], cwd=ui_wt, check=True, capture_output=True, text=True
        ).stdout.strip()
        self.assertEqual(core_branch, "feature/team-core")
        self.assertEqual(ui_branch, "feature/team-ui")

    def test_multi_team_boundary_enforcement_and_automatic_quarantine(self) -> None:
        """BoundaryGuardと自動Quarantineの連携による領空侵犯遮断・成果保全・クリーン復元を実証。"""
        manifest_data = {
            "version": 1,
            "project": "multi-team-app",
            "teams": [
                {"id": "team-ui", "owns": ["src/ui/**"]},
            ],
        }
        manifest = TeamManifest.from_dict(manifest_data)
        ui_team = manifest.teams[0]

        ui_wt = self.wt_manager.create_worktree(team_id="team-ui")

        # チームWorktree内にタスク計画と検証スクリプトを用意
        (ui_wt / "PLAN.md").write_text("# PLAN\n- [ ] 1. Update UI components\n", encoding="utf-8")
        (ui_wt / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
        verify_script = ui_wt / "verify.sh"
        verify_script.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        verify_script.chmod(0o755)

        subprocess.run(["git", "add", "."], cwd=ui_wt, check=True)
        subprocess.run(["git", "commit", "-m", "init ui worktree setup"], cwd=ui_wt, check=True)

        # 不正侵入を行う Maker を用いてオーケストレーターをチームモードで起動
        runner = RogueTeamRunnerAdapter(repo_dir=ui_wt)
        orchestrator = NightlyOrchestrator(
            repo_dir=ui_wt,
            verify_script=verify_script,
            runner_adapter=runner,
            team_id=ui_team.id,
            owns_patterns=ui_team.owns,
        )

        exit_code = orchestrator.run()

        # 1. 領空侵犯によりパイプラインが Exit 1 で安全停止すること
        self.assertEqual(exit_code, 1)

        # 2. 作業ツリーが侵犯前のクリーンな状態に完全復元されていること（未追跡ファイル含めて完全消去）
        status_ui = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=ui_wt,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(status_ui.stdout.strip(), "")

        # 3. 侵犯されたコアコードが元の状態に復元されていること
        restored_core = (ui_wt / "src" / "core" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(restored_core, "# Core Base\n")
        self.assertFalse((ui_wt / "rogue.sh").exists())

        # 4. チーム専用の隔離ブランチ（quarantine/team-ui-1-...）が作成されていること
        branch_res = subprocess.run(
            ["git", "branch", "--list", "quarantine/team-ui-1-*"],
            cwd=ui_wt,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertTrue(len(branch_res.stdout.strip()) > 0)

        # 5. 成果保全用スナップショットアーカイブが存在すること
        quarantines_dir = ui_wt / ".dev" / "quarantines"
        snapshots = list(quarantines_dir.glob("snapshot_task_team-ui-1_*.tar.gz"))
        self.assertTrue(len(snapshots) > 0)

        # 6. メインリポジトリのブランチおよびファイルが一切汚染されていないこと
        main_core = (self.repo_dir / "src" / "core" / "app.py").read_text(encoding="utf-8")
        self.assertEqual(main_core, "# Core Base\n")
        status_main = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(status_main.stdout.strip(), "")

    def test_stage1_finalization_unmerged_preservation_and_strict_audit(self) -> None:
        """Stage 1 最終確定: 未マージ成果の非破壊的保全と 3値監査判定の統合検証。"""
        # 1. チーム Worktree を作成し未マージ作業をコミット
        team_wt = self.wt_manager.create_worktree(team_id="team-audit")
        (team_wt / "src" / "ui" / "view.py").write_text("# Feature X by team-audit\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=team_wt, check=True)
        subprocess.run(["git", "commit", "-m", "feat: unmerged team audit work"], cwd=team_wt, check=True)

        # 2. 安全デフォルト cleanup_all(force=False, delete_branches=False) の実行
        self.wt_manager.cleanup_all(force=False, delete_branches=False)
        self.assertFalse(team_wt.exists(), "Worktree directory should be removed")

        # 未マージブランチが物理的に保持されていることを検証
        res_br = subprocess.run(
            ["git", "branch", "--list", "feature/team-audit"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual(res_br.stdout.strip(), "feature/team-audit", "Unmerged branch must be preserved!")

        # 3. 証跡レコードがない状態での Evidence Bundler 監査判定 -> INTEGRITY_ONLY (統合拒否)
        (self.repo_dir / "SPEC.md").write_text("# Spec\nSample Spec", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# Plan\n- [ ] Task 1", encoding="utf-8")
        bundler = EvidenceBundler(repo_dir=self.repo_dir)
        no_ev_bundle = bundler.create_bundle(dest_path=self.repo_dir / "audit_test_no_ev.zip")
        res_no_ev = bundler.verify_bundle(no_ev_bundle)

        self.assertEqual(res_no_ev.status, "INTEGRITY_ONLY")
        self.assertFalse(res_no_ev.is_valid, "Bundle without evidence records must not be allowed to integrate")

        # 4. 承認済み Evidence Record を配置して再バンドル -> VERIFIED (統合許可)
        res_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True)
        head_commit = res_commit.stdout.strip()
        res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo_dir, capture_output=True, text=True, check=True)
        head_tree = res_tree.stdout.strip()
        spec_sha = hashlib.sha256((self.repo_dir / "SPEC.md").read_bytes()).hexdigest()

        records_dir = self.repo_dir / ".git" / "nightly_evidence" / "records"
        records_dir.mkdir(parents=True, exist_ok=True)
        (records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "commit": head_commit,
                "tree_sha": head_tree,
                "spec_sha": spec_sha,
                "checker": {"decision": "APPROVE", "reason": "Deterministic verify pass"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )

        verified_bundle = bundler.create_bundle(dest_path=self.repo_dir / "audit_test_verified.zip")
        res_verified = bundler.verify_bundle(verified_bundle)

        self.assertEqual(res_verified.status, "VERIFIED")
        self.assertTrue(res_verified.is_valid, "Bundle with valid evidence and APPROVE must pass verification")

        # 5. 架空 Evidence 注入ケース (Sol 氏再現シナリオ) の検証 -> INVALID (物理遮断)
        (records_dir / "task_synthetic.json").write_text(
            json.dumps({
                "task_id": "synthetic",
                "status": "COMPLETED",
                "checker": {"decision": "APPROVE"},
                "tree_sha": "0000000000000000000000000000000000000000",
                "spec_sha": "",
            }),
            encoding="utf-8",
        )
        synth_bundle = bundler.create_bundle(dest_path=self.repo_dir / "audit_test_synth.zip")
        res_synth = bundler.verify_bundle(synth_bundle)
        self.assertEqual(res_synth.status, "INVALID")
        self.assertFalse(res_synth.is_valid, "Synthetic evidence must be blocked as INVALID")
        self.assertTrue(any("tree_sha" in err.lower() for err in res_synth.errors))

        # 6. 未コミット変更の Worktree 削除時における自動 Quarantine 保全の検証
        q_wt = self.wt_manager.create_worktree(team_id="team-quarantine")
        (q_wt / "uncommitted.py").write_text("# critical uncommitted logic\n", encoding="utf-8")
        self.wt_manager.remove_worktree(team_id="team-quarantine", force=True)
        self.assertFalse(q_wt.exists())

        quarantines_dir = self.repo_dir / ".dev" / "quarantines"
        q_snapshots = list(quarantines_dir.glob("snapshot_worktree_team-quarantine_*.tar.gz"))
        self.assertTrue(len(q_snapshots) > 0, "Quarantine snapshot must be created for uncommitted work")

        # 7. Commit SHA 不正による偽造ケースの検証 -> INVALID (物理遮断)
        (records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "commit": "0000000000000000000000000000000000000000",
                "tree_sha": head_tree,
                "spec_sha": spec_sha,
                "checker": {"decision": "APPROVE"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )
        fake_commit_bundle = bundler.create_bundle(dest_path=self.repo_dir / "audit_test_fake_commit.zip")
        res_fake_commit = bundler.verify_bundle(fake_commit_bundle)
        self.assertEqual(res_fake_commit.status, "INVALID")
        self.assertFalse(res_fake_commit.is_valid)

        # 8. Quarantine 退避時の機密ファイル (.env, *.key) 存在による Worktree 削除拒否 (Fail-Closed 保全) の検証
        import tarfile

        from auto_developer.worktree import WorktreeError
        q_wt_sec = self.wt_manager.create_worktree(team_id="team-quarantine-sec")
        (q_wt_sec / "safe.py").write_text("print('safe')\n", encoding="utf-8")
        (q_wt_sec / ".env").write_text("SECRET=123\n", encoding="utf-8")
        (q_wt_sec / "id_rsa.key").write_text("KEY_DATA\n", encoding="utf-8")
        with self.assertRaises(WorktreeError):
            self.wt_manager.remove_worktree(team_id="team-quarantine-sec", force=True)
        self.assertTrue(q_wt_sec.exists(), "Worktree with .env must be preserved without deletion")

        # 機密ファイルを退避・解消した後は安全に削除され、ノイズ (.DS_Store) のみ除外されること
        (q_wt_sec / ".env").unlink()
        (q_wt_sec / "id_rsa.key").unlink()
        (q_wt_sec / ".DS_Store").write_bytes(b"\x00")
        self.wt_manager.remove_worktree(team_id="team-quarantine-sec", force=True)
        self.assertFalse(q_wt_sec.exists())

        q_sec_snapshots = sorted(quarantines_dir.glob("snapshot_worktree_team-quarantine-sec_*.tar.gz"))
        self.assertTrue(len(q_sec_snapshots) > 0)
        with tarfile.open(q_sec_snapshots[-1], "r:gz") as tar:
            tar_names = tar.getnames()
            self.assertTrue(any("safe.py" in n for n in tar_names))
            self.assertFalse(any(n.endswith(".DS_Store") for n in tar_names))

        # 9. ファイル内容にAPIキー等を含む場合の Worktree 削除停止 (Fail-Closed) の検証
        q_wt_leak = self.wt_manager.create_worktree(team_id="team-quarantine-leak")
        fake_key_name = "api" + "_key"
        fake_secret_val = "sk-" + "a1b2c3d4e5f6g7h8i9j0"  # gitleaks:allow
        (q_wt_leak / "config.json").write_text(json.dumps({fake_key_name: fake_secret_val}) + "\n", encoding="utf-8")
        from auto_developer.worktree import WorktreeError
        with self.assertRaises(WorktreeError):
            self.wt_manager.remove_worktree(team_id="team-quarantine-leak", force=True)
        self.assertTrue(q_wt_leak.exists(), "Worktree with secret content must be preserved without deletion")
        (q_wt_leak / "config.json").unlink()

        # 10. Manifest 側のタスクメタデータ改ざん遮断の検証
        import zipfile
        with zipfile.ZipFile(verified_bundle, "r") as zf:
            manifest_json = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        manifest_json["tasks"][0]["tree_sha"] = "0000000000000000000000000000000000000000"
        tampered_manifest_zip = self.repo_dir / "audit_test_manifest_tampered.zip"
        with zipfile.ZipFile(verified_bundle, "r") as zf, zipfile.ZipFile(tampered_manifest_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(manifest_json).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))
        res_tampered = bundler.verify_bundle(tampered_manifest_zip)
        self.assertEqual(res_tampered.status, "INVALID")
        self.assertFalse(res_tampered.is_valid)

        # 11. ZIP への未知ファイル追加遮断の検証
        extra_file_zip = self.repo_dir / "audit_test_extra_file.zip"
        with zipfile.ZipFile(verified_bundle, "r") as zf, zipfile.ZipFile(extra_file_zip, "w") as out_zf:
            for name in zf.namelist():
                out_zf.writestr(name, zf.read(name))
            out_zf.writestr("mystery.txt", b"mystery data")
        res_extra = bundler.verify_bundle(extra_file_zip)
        self.assertEqual(res_extra.status, "INVALID")
        self.assertFalse(res_extra.is_valid)

        # 12. Manifest 側のタスク commit 空文字遮断の検証 (P1)
        with zipfile.ZipFile(verified_bundle, "r") as zf:
            m_empty_commit = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        m_empty_commit["tasks"][0]["commit"] = ""
        empty_commit_zip = self.repo_dir / "audit_test_empty_commit.zip"
        with zipfile.ZipFile(verified_bundle, "r") as zf, zipfile.ZipFile(empty_commit_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(m_empty_commit).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))
        res_empty_c = bundler.verify_bundle(empty_commit_zip)
        self.assertEqual(res_empty_c.status, "INVALID")
        self.assertFalse(res_empty_c.is_valid)

        # 13. Manifest 側のタスク spec_sha 不一致遮断の検証 (P1)
        with zipfile.ZipFile(verified_bundle, "r") as zf:
            m_spec_mismatch = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        m_spec_mismatch["tasks"][0]["spec_sha"] = "0" * 64
        spec_mismatch_zip = self.repo_dir / "audit_test_spec_mismatch.zip"
        with zipfile.ZipFile(verified_bundle, "r") as zf, zipfile.ZipFile(spec_mismatch_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(m_spec_mismatch).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))
        res_spec_m = bundler.verify_bundle(spec_mismatch_zip)
        self.assertEqual(res_spec_m.status, "INVALID")
        self.assertFalse(res_spec_m.is_valid)


if __name__ == "__main__":
    unittest.main()
