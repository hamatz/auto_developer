from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.bundler import EvidenceBundler


class TestEvidenceBundler(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.repo = Path(self.tmpdir.name)

        # Git リポジトリ初期化
        subprocess.run(["git", "init"], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Test User"], cwd=self.repo, check=True)

        # 基本ファイル作成
        (self.repo / "SPEC.md").write_text("# Spec\nImmutable requirements", encoding="utf-8")
        (self.repo / "PLAN.md").write_text("# Plan\n- [x] Task 1", encoding="utf-8")
        (self.repo / "NIGHTLY_REPORT.md").write_text("# Morning Brief\nTask 1 APPROVE", encoding="utf-8")
        (self.repo / ".gitignore").write_text(".dev/\nlogs/\n*.key\n.env\n", encoding="utf-8")
        (self.repo / "app.py").write_text("print('hello')", encoding="utf-8")

        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "initial commit"], cwd=self.repo, check=True)

        res_commit = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True
        )
        head_commit = res_commit.stdout.strip()

        res_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True
        )
        head_tree = res_tree.stdout.strip()

        # .git/nightly_evidence/records
        records_dir = self.repo / ".git" / "nightly_evidence" / "records"
        records_dir.mkdir(parents=True, exist_ok=True)
        import hashlib
        spec_hash = hashlib.sha256((self.repo / "SPEC.md").read_bytes()).hexdigest()
        (records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "commit": head_commit,
                "tree_sha": head_tree,
                "spec_sha": spec_hash,
                "checker": {"decision": "APPROVE", "reason": "All good"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )

        # .dev/job_state.json
        dev_dir = self.repo / ".dev"
        dev_dir.mkdir(parents=True, exist_ok=True)
        (dev_dir / "job_state.json").write_text(
            json.dumps({"job_id": "job-test-1", "status": "COMPLETED"}),
            encoding="utf-8",
        )

        # logs/nightly
        logs_dir = self.repo / "logs" / "nightly"
        logs_dir.mkdir(parents=True, exist_ok=True)
        (logs_dir / "task_1.log").write_text("Task 1 completed successfully", encoding="utf-8")

        # 機密ファイルと macOS ゴミファイルの配置 (除外テスト用)
        (self.repo / ".env").write_text("API_KEY=super_secret", encoding="utf-8")
        (self.repo / "private.key").write_text("PRIVATE_KEY_DATA", encoding="utf-8")
        (self.repo / ".DS_Store").write_bytes(b"\x00\x00\x00\x01")
        (logs_dir / ".DS_Store").write_bytes(b"\x00\x00\x00\x01")

    def tearDown(self):
        self.tmpdir.cleanup()

    def test_bundle_creation_and_sanitization(self):
        bundler = EvidenceBundler(repo_dir=self.repo)
        out_zip = bundler.create_bundle(dest_path=self.repo / "test_bundle.zip")

        self.assertTrue(out_zip.exists())
        self.assertTrue(zipfile.is_zipfile(out_zip))

        with zipfile.ZipFile(out_zip, "r") as zf:
            namelist = zf.namelist()

            # 必須成果物・証跡の同梱確認
            self.assertIn("bundle_manifest.json", namelist)
            self.assertIn("NIGHTLY_REPORT.md", namelist)
            self.assertIn("job_state.json", namelist)
            self.assertIn("evidence/task_1.json", namelist)
            self.assertIn("logs/task_1.log", namelist)
            self.assertIn("git_bundle.bundle", namelist)

            # 機密・ゴミファイルの除外確認
            for name in namelist:
                self.assertFalse(name.endswith(".env"), f"Secret file {name} should be excluded!")
                self.assertFalse(name.endswith(".key"), f"Secret file {name} should be excluded!")
                self.assertFalse(name.endswith(".DS_Store"), f"Noise file {name} should be excluded!")
                self.assertFalse("__MACOSX" in name, f"Noise dir {name} should be excluded!")

            # Manifest 検証
            manifest_data = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
            self.assertEqual(manifest_data["bundle_version"], "1.0")
            self.assertEqual(manifest_data["job_id"], "job-test-1")
            self.assertIn("file_hashes", manifest_data)
            self.assertIn("evidence/task_1.json", manifest_data["file_hashes"])
            self.assertIn("tasks", manifest_data)
            self.assertEqual(len(manifest_data["tasks"]), 1)
            self.assertEqual(manifest_data["tasks"][0]["task_id"], "1")
            self.assertEqual(manifest_data["tasks"][0]["checker_decision"], "APPROVE")

    def test_verify_bundle_success(self):
        # 実際の HEAD Tree と SPEC ハッシュを反映させた証跡を作成
        res_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_commit = res_commit.stdout.strip()
        res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_tree = res_tree.stdout.strip()
        import hashlib
        spec_hash = hashlib.sha256((self.repo / "SPEC.md").read_bytes()).hexdigest()

        records_dir = self.repo / ".git" / "nightly_evidence" / "records"
        (records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "commit": head_commit,
                "tree_sha": head_tree,
                "spec_sha": spec_hash,
                "checker": {"decision": "APPROVE", "reason": "All checks passed"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )

        bundler = EvidenceBundler(repo_dir=self.repo)
        out_zip = bundler.create_bundle(dest_path=self.repo / "valid_bundle.zip")

        result = bundler.verify_bundle(out_zip)
        self.assertTrue(result.is_valid, f"Verification failed with errors: {result.errors}")
        self.assertEqual(result.job_id, "job-test-1")
        self.assertEqual(len(result.errors), 0)
        self.assertEqual(len(result.tasks), 1)
        self.assertEqual(result.tasks[0]["task_id"], "1")
        self.assertEqual(result.tasks[0]["checker_decision"], "APPROVE")

    def test_verify_bundle_tampered_fails(self):
        bundler = EvidenceBundler(repo_dir=self.repo)
        out_zip = bundler.create_bundle(dest_path=self.repo / "tampered_bundle.zip")

        # ZIP を開き、中身のファイルを改ざんして再圧縮
        temp_dir = tempfile.TemporaryDirectory()
        with zipfile.ZipFile(out_zip, "r") as zf:
            zf.extractall(temp_dir.name)

        # NIGHTLY_REPORT.md を改ざん
        report_path = Path(temp_dir.name) / "NIGHTLY_REPORT.md"
        report_path.write_text("TAMPERED REPORT CONTENT", encoding="utf-8")

        tampered_zip = self.repo / "tampered_result.zip"
        with zipfile.ZipFile(tampered_zip, "w") as zf:
            for root, _, files in os.walk(temp_dir.name):
                for file in files:
                    full_p = Path(root) / file
                    arcname = full_p.relative_to(temp_dir.name).as_posix()
                    zf.write(full_p, arcname=arcname)
        temp_dir.cleanup()

        result = bundler.verify_bundle(tampered_zip)
        self.assertFalse(result.is_valid)
        self.assertEqual(result.status, "INVALID")
        self.assertTrue(any("Hash mismatch" in err for err in result.errors))

    def test_bundle_collects_evidence_records_from_all_locations(self):
        # archive ディレクトリと .dev/evidence ディレクトリのレコード収集
        archive_dir = self.repo / ".git" / "nightly_evidence" / "archive"
        archive_dir.mkdir(parents=True, exist_ok=True)
        (archive_dir / "task_2.json").write_text(
            json.dumps({
                "task_id": "2",
                "checkpoint": "chk2",
                "tree_sha": "tree2",
                "spec_sha": "spec2",
                "checker": {"decision": "APPROVE"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )

        dev_evidence_dir = self.repo / ".dev" / "evidence"
        dev_evidence_dir.mkdir(parents=True, exist_ok=True)
        (dev_evidence_dir / "task_3.json").write_text(
            json.dumps({
                "task_id": "3",
                "checkpoint": "chk3",
                "tree_sha": "tree3",
                "spec_sha": "spec3",
                "checker": {"decision": "APPROVE"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )

        bundler = EvidenceBundler(repo_dir=self.repo)
        out_zip = bundler.create_bundle(dest_path=self.repo / "all_locations.zip")

        with zipfile.ZipFile(out_zip, "r") as zf:
            namelist = zf.namelist()
            self.assertIn("evidence/task_1.json", namelist)
            self.assertIn("evidence/task_2.json", namelist)
            self.assertIn("evidence/task_3.json", namelist)

    def test_verify_bundle_three_way_audit(self):
        # 1. 証跡ありで完全合格 -> VERIFIED
        res_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_commit = res_commit.stdout.strip()
        res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_tree = res_tree.stdout.strip()
        import hashlib
        spec_hash = hashlib.sha256((self.repo / "SPEC.md").read_bytes()).hexdigest()

        records_dir = self.repo / ".git" / "nightly_evidence" / "records"
        (records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "commit": head_commit,
                "tree_sha": head_tree,
                "spec_sha": spec_hash,
                "checker": {"decision": "APPROVE", "reason": "All good"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )

        bundler = EvidenceBundler(repo_dir=self.repo)
        valid_zip = bundler.create_bundle(dest_path=self.repo / "valid_audit.zip")
        res_valid = bundler.verify_bundle(valid_zip)
        self.assertEqual(res_valid.status, "VERIFIED")
        self.assertTrue(res_valid.is_valid)

        # 2. 証跡レコードが0件のバンドル -> INTEGRITY_ONLY
        import shutil
        shutil.rmtree(records_dir)
        records_dir.mkdir(parents=True, exist_ok=True)
        no_evidence_zip = bundler.create_bundle(dest_path=self.repo / "no_evidence.zip")
        res_no_evidence = bundler.verify_bundle(no_evidence_zip)
        self.assertEqual(res_no_evidence.status, "INTEGRITY_ONLY")
        self.assertFalse(res_no_evidence.is_valid)
        self.assertTrue(
            any("Audit rejected: No verified Evidence Records found" in w for w in res_no_evidence.warnings)
        )

        # 3. Checker が REJECT のレコードが含まれるバンドル -> INVALID
        (records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "tree_sha": head_tree,
                "spec_sha": spec_hash,
                "checker": {"decision": "REJECT", "reason": "Bad logic"},
                "status": "FAILED",
            }),
            encoding="utf-8",
        )
        rejected_zip = bundler.create_bundle(dest_path=self.repo / "rejected.zip")
        res_rejected = bundler.verify_bundle(rejected_zip)
        self.assertEqual(res_rejected.status, "INVALID")
        self.assertFalse(res_rejected.is_valid)

    def test_verify_bundle_rejects_synthetic_or_unbound_tree_sha(self):
        """Sol氏の再現シナリオ: 架空の Evidence Record (実在しない Tree SHA / SPEC SHA) を注入した場合、確実に INVALID と判定されること。"""
        records_dir = self.repo / ".git" / "nightly_evidence" / "records"
        records_dir.mkdir(parents=True, exist_ok=True)

        # 架空の Evidence Record (Tree SHA が 0000..., SPEC SHA が空)
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

        bundler = EvidenceBundler(repo_dir=self.repo)
        synth_zip = bundler.create_bundle(dest_path=self.repo / "synthetic_bundle.zip")

        res_synth = bundler.verify_bundle(synth_zip)
        self.assertEqual(res_synth.status, "INVALID", "Synthetic evidence must be INVALID, not VERIFIED!")
        self.assertFalse(res_synth.is_valid)
        self.assertTrue(
            any("tree_sha" in err.lower() for err in res_synth.errors),
            f"Expected tree_sha validation error in: {res_synth.errors}",
        )

    def _repack_with_updated_manifest(self, base_zip: Path, out_zip: Path, file_updates: dict[str, bytes | None]) -> None:
        """指定ZIPのファイルを更新/削除し、Manifestのハッシュも再計算して整合したZIPを再生成するヘルパー。"""
        import hashlib
        extracted = {}
        with zipfile.ZipFile(base_zip, "r") as zf:
            for name in zf.namelist():
                extracted[name] = zf.read(name)

        # 更新または削除
        for name, content in file_updates.items():
            if content is None:
                extracted.pop(name, None)
            else:
                extracted[name] = content

        # bundle_manifest.json の更新
        manifest = json.loads(extracted["bundle_manifest.json"].decode("utf-8"))
        new_hashes = {}
        for name, data in extracted.items():
            if name != "bundle_manifest.json":
                new_hashes[name] = hashlib.sha256(data).hexdigest()
        manifest["file_hashes"] = new_hashes
        extracted["bundle_manifest.json"] = json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8")

        with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for name, data in extracted.items():
                zf.writestr(name, data)

    def test_verify_bundle_rejects_corrupted_git_bundle_fail_closed(self):
        """P0: Git Bundle が不正・クローン不能な場合、Warning に留めず必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base1.zip")

        corrupted_zip = self.repo / "corrupted_git.zip"
        self._repack_with_updated_manifest(base_zip, corrupted_zip, {"git_bundle.bundle": b"not a valid git bundle"})

        res = bundler.verify_bundle(corrupted_zip)
        self.assertEqual(res.status, "INVALID", "Corrupted git bundle must be INVALID")
        self.assertFalse(res.is_valid)
        self.assertTrue(any("git" in err.lower() for err in res.errors))

    def test_verify_bundle_rejects_nonexistent_or_mismatched_commit_sha(self):
        """P1: Evidence Record の commit SHA が実在しない、または Tree と不一致なら必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base2.zip")

        # task_1.json の commit を存在しない架空の SHA に改ざん
        with zipfile.ZipFile(base_zip, "r") as zf:
            ev_data = json.loads(zf.read("evidence/task_1.json").decode("utf-8"))
        ev_data["commit"] = "1111222233334444555566667777888899990000"

        tampered_ev_zip = self.repo / "tampered_commit.zip"
        self._repack_with_updated_manifest(
            base_zip, tampered_ev_zip, {"evidence/task_1.json": json.dumps(ev_data).encode("utf-8")}
        )

        res = bundler.verify_bundle(tampered_ev_zip)
        self.assertEqual(res.status, "INVALID", "Non-existent commit in evidence must be INVALID")
        self.assertFalse(res.is_valid)
        self.assertTrue(any("commit" in err.lower() for err in res.errors))

    def test_verify_bundle_rejects_missing_evidence_files_against_manifest(self):
        """P1: Manifest にタスクが記載されているが Evidence ファイルが削除された場合、必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base3.zip")

        missing_ev_zip = self.repo / "missing_ev.zip"
        self._repack_with_updated_manifest(base_zip, missing_ev_zip, {"evidence/task_1.json": None})

        res = bundler.verify_bundle(missing_ev_zip)
        self.assertEqual(res.status, "INVALID", "Missing evidence record must be INVALID")
        self.assertFalse(res.is_valid)

    def test_verify_bundle_rejects_fake_head_commit_in_manifest(self):
        """P0: Manifest の head_commit が Git Bundle の実 HEAD と不一致の場合、必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base4.zip")

        with zipfile.ZipFile(base_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        manifest["head_commit"] = "9999888877776666555544443333222211110000"

        fake_head_zip = self.repo / "fake_head.zip"
        with zipfile.ZipFile(base_zip, "r") as zf, zipfile.ZipFile(fake_head_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(manifest).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))

        res = bundler.verify_bundle(fake_head_zip)
        self.assertEqual(res.status, "INVALID", "Fake HEAD commit in manifest must be INVALID")
        self.assertFalse(res.is_valid)

    def test_cli_bundle_and_verify_bundle(self):
        from auto_developer.cli import main

        bundle_zip = self.repo / "cli_bundle.zip"
        # 1. auto-dev bundle 実行
        exit_code = main(["--work-dir", str(self.repo), "bundle", "--out", str(bundle_zip)])
        self.assertEqual(exit_code, 0)
        self.assertTrue(bundle_zip.exists())

        # 2. auto-dev verify-bundle 実行
        exit_code_verify = main(["--work-dir", str(self.repo), "verify-bundle", str(bundle_zip)])
        self.assertEqual(exit_code_verify, 0)

        # 3. auto-dev verify-bundle --json 実行
        import io
        import sys
        captured = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            exit_code_json = main(["--work-dir", str(self.repo), "verify-bundle", str(bundle_zip), "--json"])
        finally:
            sys.stdout = old_stdout
        self.assertEqual(exit_code_json, 0)
        parsed = json.loads(captured.getvalue())
        self.assertTrue(parsed["is_valid"])
        self.assertEqual(parsed["status"], "VERIFIED")

        # 4. 証跡レコードがないバンドルに対する verify-bundle -> exit code 2 (INTEGRITY_ONLY)
        import shutil

        shutil.rmtree(self.repo / ".git" / "nightly_evidence" / "records")
        no_ev_zip = self.repo / "no_ev_cli.zip"
        self.assertEqual(main(["--work-dir", str(self.repo), "bundle", "--out", str(no_ev_zip)]), 0)
        exit_code_no_ev = main(["--work-dir", str(self.repo), "verify-bundle", str(no_ev_zip)])
        self.assertEqual(exit_code_no_ev, 2)

        # 5. 改ざんされたバンドルに対する verify-bundle -> exit code 1 (INVALID)
        no_ev_zip.write_bytes(b"corrupted zip content")
        exit_code_invalid = main(["--work-dir", str(self.repo), "verify-bundle", str(no_ev_zip)])
        self.assertEqual(exit_code_invalid, 1)


if __name__ == "__main__":
    unittest.main()
