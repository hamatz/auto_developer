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

    def test_verify_bundle_with_orchestrator_commit_dict_format(self):
        """オーケストレーターが生成する dict 形式 ({"sha": ...}) の commit 情報を正しく検証できること。"""
        res_commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_commit = res_commit.stdout.strip()
        res_tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=self.repo, capture_output=True, text=True, check=True)
        head_tree = res_tree.stdout.strip()
        import hashlib
        spec_hash = hashlib.sha256((self.repo / "SPEC.md").read_bytes()).hexdigest()

        records_dir = self.repo / ".git" / "nightly_evidence" / "records"
        # orchestrator.py / cli.py と同等の commit dict 構造
        (records_dir / "task_1.json").write_text(
            json.dumps({
                "task_id": "1",
                "checkpoint": "chk1",
                "commit": {
                    "sha": head_commit,
                    "tree_sha": head_tree,
                    "tree_verified": True,
                },
                "tree_sha": head_tree,
                "spec_sha": spec_hash,
                "checker": {"decision": "APPROVE", "reason": "All checks passed"},
                "status": "COMPLETED",
            }),
            encoding="utf-8",
        )

        bundler = EvidenceBundler(repo_dir=self.repo)
        out_zip = bundler.create_bundle(dest_path=self.repo / "dict_commit_bundle.zip")

        result = bundler.verify_bundle(out_zip)
        self.assertTrue(result.is_valid, f"Verification failed with errors: {result.errors}")
        self.assertEqual(result.status, "VERIFIED")
        self.assertEqual(result.tasks[0]["commit"], head_commit)

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

    def test_verify_bundle_rejects_empty_head_commit_in_manifest(self):
        """P1: Manifest の head_commit / head_tree_sha が空文字または欠落の場合、必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base_empty_head.zip")

        with zipfile.ZipFile(base_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        manifest["head_commit"] = ""
        manifest["head_tree_sha"] = ""

        empty_head_zip = self.repo / "empty_head.zip"
        with zipfile.ZipFile(base_zip, "r") as zf, zipfile.ZipFile(empty_head_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(manifest).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))

        res = bundler.verify_bundle(empty_head_zip)
        self.assertEqual(res.status, "INVALID", "Empty HEAD commit in manifest must be INVALID")
        self.assertFalse(res.is_valid)

    def test_verify_bundle_rejects_extra_unhashed_files_in_zip(self):
        """P1: ZIP 内に Manifest で宣言されていない未知ファイル (mystery.txt) がある場合、必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base_extra.zip")

        extra_zip = self.repo / "extra_file.zip"
        with zipfile.ZipFile(base_zip, "r") as zf, zipfile.ZipFile(extra_zip, "w") as out_zf:
            for name in zf.namelist():
                out_zf.writestr(name, zf.read(name))
            out_zf.writestr("mystery.txt", b"unauthorized surprise content\n")

        res = bundler.verify_bundle(extra_zip)
        self.assertEqual(res.status, "INVALID", "Extra unhashed file in ZIP must be INVALID")
        self.assertFalse(res.is_valid)

    def test_verify_bundle_rejects_manifest_task_field_tampering(self):
        """P1: Manifest 側のタスク情報 (tree_sha等) が Evidence Record と不一致の場合、必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base_tamper.zip")

        with zipfile.ZipFile(base_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        self.assertTrue(len(manifest.get("tasks", [])) > 0)
        # Manifest の Task 1 の tree_sha をゼロ改ざん (Sol氏再現ケース)
        manifest["tasks"][0]["tree_sha"] = "0000000000000000000000000000000000000000"

        tampered_zip = self.repo / "tampered_task.zip"
        with zipfile.ZipFile(base_zip, "r") as zf, zipfile.ZipFile(tampered_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(manifest).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))

        res = bundler.verify_bundle(tampered_zip)
        self.assertEqual(res.status, "INVALID", "Tampered task tree_sha in manifest must be INVALID")
        self.assertFalse(res.is_valid)

    def test_verify_bundle_rejects_duplicate_tasks_in_manifest(self):
        """P1: Manifest 内に重複したタスクIDが存在する場合、必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base_dup.zip")

        with zipfile.ZipFile(base_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        self.assertTrue(len(manifest.get("tasks", [])) > 0)
        # 同じタスクを重複追加 (Sol氏再現ケース)
        manifest["tasks"].append(manifest["tasks"][0].copy())

        dup_zip = self.repo / "dup_task.zip"
        with zipfile.ZipFile(base_zip, "r") as zf, zipfile.ZipFile(dup_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(manifest).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))

        res = bundler.verify_bundle(dup_zip)
        self.assertEqual(res.status, "INVALID", "Duplicate tasks in manifest must be INVALID")
        self.assertFalse(res.is_valid)

    def test_verify_bundle_rejects_empty_task_commit_in_manifest(self):
        """P1: Manifest 側のタスク情報の commit が空文字の場合、必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base_empty_commit.zip")

        with zipfile.ZipFile(base_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        self.assertTrue(len(manifest.get("tasks", [])) > 0)
        # Manifest の Task 1 の commit を空文字にする (Sol氏再現ケース)
        manifest["tasks"][0]["commit"] = ""

        tampered_zip = self.repo / "empty_task_commit.zip"
        with zipfile.ZipFile(base_zip, "r") as zf, zipfile.ZipFile(tampered_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(manifest).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))

        res = bundler.verify_bundle(tampered_zip)
        self.assertEqual(res.status, "INVALID", "Empty task commit in manifest must be INVALID")
        self.assertFalse(res.is_valid)

    def test_verify_bundle_rejects_task_spec_sha_mismatch(self):
        """P1: Manifest 側のタスク情報の spec_sha が Evidence と不一致の場合、必ず INVALID で遮断すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "base_spec_mismatch.zip")

        with zipfile.ZipFile(base_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
        self.assertTrue(len(manifest.get("tasks", [])) > 0)
        # Manifest の Task 1 の spec_sha を不正値にする (Sol氏再現ケース)
        manifest["tasks"][0]["spec_sha"] = "ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff"

        tampered_zip = self.repo / "mismatched_spec_sha.zip"
        with zipfile.ZipFile(base_zip, "r") as zf, zipfile.ZipFile(tampered_zip, "w") as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    out_zf.writestr(name, json.dumps(manifest).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))

        res = bundler.verify_bundle(tampered_zip)
        self.assertEqual(res.status, "INVALID", "Mismatched task spec_sha in manifest must be INVALID")
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

    def test_verify_bundle_rejects_outdated_audit_when_code_modified_post_audit(self):
        """VVV.3 / Sol氏 Phase 61 考察: 監査対象コミット以降にプロダクトコード（src/等）が変更された場合、
        未監査コードの混入として audit_stage_verified=False (AUDIT_OUTDATED) で拒絶すること。
        """
        bundler = EvidenceBundler(repo_dir=self.repo)

        # 1. 監査対象となる初期コミットのハッシュを取得
        rev_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        audit_target_commit = rev_res.stdout.strip()

        # 2. 監査後にプロダクトコード (src/) を変更して新コミットを作成
        src_file = self.repo / "src" / "app.py"
        src_file.parent.mkdir(parents=True, exist_ok=True)
        src_file.write_text("print('modified after audit')", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "Post-audit code modification"], cwd=self.repo, check=True)

        # 3. audit_stage.json を配置（target_commit は変更前のコミット）
        evidence_dir = self.repo / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "audit_stage.json").write_text(
            json.dumps({
                "status": "SUCCESS",
                "mode": "LIVE",
                "provider": "anthropic",
                "provider_request_id": "msg_live_valid_12345",
                "audit_metadata": {
                    "mode": "LIVE",
                    "provider": "anthropic",
                    "provider_request_id": "msg_live_valid_12345",
                    "auditor_model": "claude-opus-5.5",
                    "target_commit": audit_target_commit,
                },
            }),
            encoding="utf-8",
        )

        # 4. バンドル作成と検証
        bundle_zip = self.repo / "outdated_audit_bundle.zip"
        bundler.create_bundle(dest_path=bundle_zip)
        res = bundler.verify_bundle(bundle_zip)

        # 未監査のコード変更があるため、audit_stage_verified は False となり AUDIT_OUTDATED 警告が出ること
        self.assertFalse(res.audit_stage_verified)
        self.assertTrue(any("AUDIT_OUTDATED" in w for w in res.warnings))

    def test_verify_bundle_rejects_missing_or_invalid_audit_target_commit(self):
        """AUD-002: Live 監査記録の target_commit が欠落または無効な場合、Fail-Closed で audit_stage_verified=False とすること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)

        evidence_dir = self.repo / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "audit_stage.json").write_text(
            json.dumps({
                "status": "SUCCESS",
                "mode": "LIVE",
                "provider": "anthropic",
                "provider_request_id": "msg_live_valid_12345",
                "audit_metadata": {
                    "mode": "LIVE",
                    "provider": "anthropic",
                    "provider_request_id": "msg_live_valid_12345",
                    "auditor_model": "claude-opus-5.5",
                    # target_commit is completely missing
                },
            }),
            encoding="utf-8",
        )

        bundle_zip = self.repo / "missing_target_commit_bundle.zip"
        bundler.create_bundle(dest_path=bundle_zip)
        res = bundler.verify_bundle(bundle_zip)
        self.assertFalse(res.audit_stage_verified)
        self.assertTrue(any("AUDIT_TARGET_COMMIT_INVALID" in w for w in res.warnings))

    def test_verify_bundle_rejects_when_post_audit_git_diff_fails(self):
        """AUD-002: target_commit が存在しないコミットハッシュの場合、git diff 失敗を検知して audit_stage_verified=False とすること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)

        evidence_dir = self.repo / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "audit_stage.json").write_text(
            json.dumps({
                "status": "SUCCESS",
                "mode": "LIVE",
                "provider": "anthropic",
                "provider_request_id": "msg_live_valid_12345",
                "audit_metadata": {
                    "mode": "LIVE",
                    "provider": "anthropic",
                    "provider_request_id": "msg_live_valid_12345",
                    "auditor_model": "claude-opus-5.5",
                    "target_commit": "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef",
                },
            }),
            encoding="utf-8",
        )

        bundle_zip = self.repo / "bad_diff_target_commit_bundle.zip"
        bundler.create_bundle(dest_path=bundle_zip)
        res = bundler.verify_bundle(bundle_zip)
        self.assertFalse(res.audit_stage_verified)
        self.assertTrue(any("AUDIT_DIFF_FAILED" in w for w in res.warnings))

    def test_verify_bundle_rejects_symbolic_ref_target_commit(self):
        """AUD-001: target_commit が 'HEAD' や短縮SHA等の非40文字16進数の場合、AUDIT_TARGET_COMMIT_INVALID で拒絶すること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)

        evidence_dir = self.repo / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "audit_stage.json").write_text(
            json.dumps({
                "status": "SUCCESS",
                "mode": "LIVE",
                "provider": "anthropic",
                "provider_request_id": "msg_live_valid_12345",
                "audit_metadata": {
                    "mode": "LIVE",
                    "provider": "anthropic",
                    "provider_request_id": "msg_live_valid_12345",
                    "auditor_model": "claude-opus-5.5",
                    "target_commit": "HEAD",  # Symbolic ref must be rejected
                },
            }),
            encoding="utf-8",
        )

        bundle_zip = self.repo / "symbolic_target_commit_bundle.zip"
        bundler.create_bundle(dest_path=bundle_zip)
        res = bundler.verify_bundle(bundle_zip)
        self.assertFalse(res.audit_stage_verified)
        self.assertTrue(any("AUDIT_TARGET_COMMIT_INVALID" in w for w in res.warnings))

    def test_verify_bundle_rejects_renamed_product_file_post_audit(self):
        """AUD-003: 監査後に scripts/ 内のファイルがリネーム/移動された場合、--no-renames で検知して AUDIT_OUTDATED とすること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)

        # 1. scripts/tool.sh を追加して監査対象コミットを作成
        tool_file = self.repo / "scripts" / "tool.sh"
        tool_file.parent.mkdir(parents=True, exist_ok=True)
        tool_file.write_text("#!/bin/bash\necho ok\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "Add tool.sh"], cwd=self.repo, check=True)
        rev_res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=self.repo, capture_output=True, text=True, check=True)
        audit_target_commit = rev_res.stdout.strip()

        # 2. scripts/tool.sh を bin/tool.sh にリネーム（Git rename detection により通常は bin/tool.sh のみが出現する）
        bin_dir = self.repo / "bin"
        bin_dir.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "mv", "scripts/tool.sh", "bin/tool.sh"], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-m", "Move tool.sh to bin/"], cwd=self.repo, check=True)

        # 3. audit_stage.json を配置
        evidence_dir = self.repo / ".git" / "nightly_evidence" / "records"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        (evidence_dir / "audit_stage.json").write_text(
            json.dumps({
                "status": "SUCCESS",
                "mode": "LIVE",
                "provider": "anthropic",
                "provider_request_id": "msg_live_valid_12345",
                "audit_metadata": {
                    "mode": "LIVE",
                    "provider": "anthropic",
                    "provider_request_id": "msg_live_valid_12345",
                    "auditor_model": "claude-opus-5.5",
                    "target_commit": audit_target_commit,
                },
            }),
            encoding="utf-8",
        )

        bundle_zip = self.repo / "renamed_tool_bundle.zip"
        bundler.create_bundle(dest_path=bundle_zip)
        res = bundler.verify_bundle(bundle_zip)
        self.assertFalse(res.audit_stage_verified)
        self.assertTrue(any("AUDIT_OUTDATED" in w for w in res.warnings))

    def test_feature_www_3_find_release_blockers(self):
        """機能WWW.3: find_release_blockers は未完了の P0/P1 および ESCALATED のみを返し、P2 や完了タスクは返さないこと。"""
        from auto_developer.bundler import find_release_blockers

        plan_text = (
            "## ステップ\n"
            "- [x] 1. 完了タスク (Severity: P0, Loc: a.py:1)\n"
            "- [ ] 2. 未完了P2タスク (Severity: P2, Loc: b.py:2)\n"
            "- [ ] 3. 未完了P1タスク (Severity: P1, Loc: c.py:3)\n"
            "- [ ] 4. 未完了P0タスク (Severity: P0, Loc: d.py:4)\n"
            "\n"
            "## 止まっていること・判断待ち\n"
            "- ESCALATED [AUD-999] Severe bug (Severity: P1, Loc: e.py:5, Streak: 5)\n"
        )

        blockers = find_release_blockers(plan_text)
        self.assertEqual(len(blockers), 3)
        self.assertTrue(any("未完了P1タスク" in b for b in blockers))
        self.assertTrue(any("未完了P0タスク" in b for b in blockers))
        self.assertTrue(any("ESCALATED [AUD-999]" in b for b in blockers))
        self.assertFalse(any("未完了P2タスク" in b for b in blockers))
        self.assertFalse(any("完了タスク" in b for b in blockers))

    def test_feature_www_3_bundle_includes_backlog_and_manifest_backlog_count(self):
        """機能WWW.3: Bundle に BACKLOG.md が同梱され、manifest に backlog_count が記録されること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)

        # BACKLOG.md を作成
        backlog_path = self.repo / "BACKLOG.md"
        backlog_path.write_text(
            "# BACKLOG.md\n\n"
            "- [AUD-1001] Edge case defect (Severity: P2, Loc: a.py:1, Cycle: c1)\n"
            "- [AUD-1002] Another minor issue (Severity: P2, Loc: b.py:2, Cycle: c2)\n",
            encoding="utf-8",
        )

        bundle_zip = self.repo / "backlog_bundle.zip"
        bundler.create_bundle(dest_path=bundle_zip)

        with zipfile.ZipFile(bundle_zip, "r") as zf:
            self.assertIn("BACKLOG.md", zf.namelist())
            manifest_bytes = zf.read("bundle_manifest.json")
            manifest = json.loads(manifest_bytes.decode("utf-8"))
            self.assertEqual(manifest.get("backlog_count"), 2)

    def test_feature_www_3_verify_bundle_rejects_unresolved_release_blockers(self):
        """機能WWW.3: verify_bundle は PLAN.md に未解決の P0/P1 または ESCALATED がある場合に RELEASE_BLOCKED で不合格にすること。"""
        bundler = EvidenceBundler(repo_dir=self.repo)

        # PLAN.md に未完了 P1 を追加
        (self.repo / "PLAN.md").write_text(
            "# PLAN.md\n\n## ステップ\n"
            "- [x] 1. Initial done task\n"
            "- [ ] 2. Blocked task (Severity: P1, Loc: src/app.py:1)\n",
            encoding="utf-8",
        )

        bundle_zip = self.repo / "blocker_bundle.zip"
        bundler.create_bundle(dest_path=bundle_zip)
        res = bundler.verify_bundle(bundle_zip)

        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, "INVALID")
        self.assertTrue(
            any(e.startswith("RELEASE_BLOCKED:") for e in res.errors),
            f"Expected error starting with RELEASE_BLOCKED:, got errors: {res.errors}",
        )

    def test_feature_www_3_verify_bundle_allows_p2_in_plan_and_backlog(self):
        """機能WWW.3: P2 の残件（PLAN.md の P2 タスクや BACKLOG.md）はリリースを止めないこと。"""
        bundler = EvidenceBundler(repo_dir=self.repo)

        # PLAN.md に完了タスクと未完了 P2 タスク
        (self.repo / "PLAN.md").write_text(
            "# PLAN.md\n\n## ステップ\n"
            "- [x] 1. Initial done task\n"
            "- [ ] 2. Minor edge case (Severity: P2, Loc: src/app.py:1)\n",
            encoding="utf-8",
        )
        # BACKLOG.md にも P2
        (self.repo / "BACKLOG.md").write_text(
            "# BACKLOG.md\n\n- [AUD-1001] Minor P2 issue (Severity: P2, Loc: a.py:1, Cycle: c1)\n",
            encoding="utf-8",
        )

        bundle_zip = self.repo / "p2_allowed_bundle.zip"
        bundler.create_bundle(dest_path=bundle_zip)
        res = bundler.verify_bundle(bundle_zip)

        # RELEASE_BLOCKED エラーが発生しないこと
        self.assertFalse(any(e.startswith("RELEASE_BLOCKED:") for e in res.errors))
        self.assertTrue(res.is_valid)
        self.assertEqual(res.status, "VERIFIED")

    def _rewrite_manifest(self, base_zip: Path, out_zip: Path, mutator) -> None:
        """ZIP内の bundle_manifest.json だけを書き換えて新しい ZIP を作成するヘルパー。"""
        with zipfile.ZipFile(base_zip, "r") as zf, zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as out_zf:
            for name in zf.namelist():
                if name == "bundle_manifest.json":
                    manifest = json.loads(zf.read(name).decode("utf-8"))
                    mutator(manifest)
                    out_zf.writestr(name, json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
                else:
                    out_zf.writestr(name, zf.read(name))

    def test_rel_1_bundle_records_format_version_and_tool_version(self):
        """REL-1: bundle_manifest.json に format_version=2 (int) と tool_version (__version__) が記録されること。"""
        import auto_developer
        from auto_developer.cli import VERSION

        bundler = EvidenceBundler(repo_dir=self.repo)
        out_zip = bundler.create_bundle(dest_path=self.repo / "rel1_bundle.zip")

        with zipfile.ZipFile(out_zip, "r") as zf:
            manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))

        self.assertIn("format_version", manifest)
        self.assertIs(type(manifest["format_version"]), int)
        self.assertEqual(manifest["format_version"], 2)
        self.assertEqual(manifest.get("tool_version"), auto_developer.__version__)
        self.assertEqual(manifest.get("tool_version"), VERSION)

    def test_rel_1_verify_bundle_rejects_missing_format_version_with_cli_exit_1(self):
        """REL-1: format_version がない v0.8 以前の Bundle を verify_bundle / CLI verify-bundle が拒否し終了コード 1 を返すこと。"""
        import io

        from auto_developer.cli import main

        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "rel1_base.zip")
        legacy_zip = self.repo / "rel1_legacy_no_fv.zip"
        self._rewrite_manifest(base_zip, legacy_zip, lambda m: m.pop("format_version", None))

        res = bundler.verify_bundle(legacy_zip)
        self.assertFalse(res.is_valid)
        self.assertEqual(res.status, "INVALID")
        self.assertTrue(
            any("v0.8" in err and "v0.8.0" in err for err in res.errors),
            f"Expected v0.8.0 guidance message in errors, got: {res.errors}",
        )

        # CLI verify-bundle (text & --json) returns exit code 1
        captured = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured
            exit_code = main(["--work-dir", str(self.repo), "verify-bundle", str(legacy_zip)])
        finally:
            sys.stdout = old_stdout
        self.assertEqual(exit_code, 1)
        self.assertIn("v0.8.0", captured.getvalue())

        captured_json = io.StringIO()
        old_stdout = sys.stdout
        try:
            sys.stdout = captured_json
            exit_code_json = main(["--work-dir", str(self.repo), "verify-bundle", str(legacy_zip), "--json"])
        finally:
            sys.stdout = old_stdout
        self.assertEqual(exit_code_json, 1)
        parsed = json.loads(captured_json.getvalue())
        self.assertFalse(parsed["is_valid"])
        self.assertEqual(parsed["status"], "INVALID")
        self.assertTrue(any("v0.8.0" in err for err in parsed["errors"]))

    def test_rel_1_verify_bundle_rejects_old_or_invalid_format_versions(self):
        """REL-1: format_version が 2 未満または整数以外の不正値、および未対応バージョンの Bundle を拒否すること。"""
        from auto_developer.cli import main

        bundler = EvidenceBundler(repo_dir=self.repo)
        base_zip = bundler.create_bundle(dest_path=self.repo / "rel1_base_invalid.zip")

        for idx, bad_fv in enumerate([1, 0, -1, None, "2", 2.0, True]):
            bad_zip = self.repo / f"rel1_bad_fv_{idx}.zip"
            self._rewrite_manifest(base_zip, bad_zip, lambda m, v=bad_fv: m.__setitem__("format_version", v))
            res = bundler.verify_bundle(bad_zip)
            self.assertFalse(res.is_valid, f"format_version={bad_fv!r} must be rejected")
            self.assertEqual(res.status, "INVALID", f"format_version={bad_fv!r} must be INVALID")
            self.assertTrue(
                any("v0.8" in err and "v0.8.0" in err for err in res.errors),
                f"Expected v0.8.0 guidance for format_version={bad_fv!r}, got: {res.errors}",
            )
            if bad_fv == 1:
                self.assertEqual(main(["--work-dir", str(self.repo), "verify-bundle", str(bad_zip)]), 1)

        # 未対応の将来バージョン (3) も拒否すること
        future_zip = self.repo / "rel1_future_fv.zip"
        self._rewrite_manifest(base_zip, future_zip, lambda m: m.__setitem__("format_version", 3))
        res_future = bundler.verify_bundle(future_zip)
        self.assertFalse(res_future.is_valid)
        self.assertEqual(res_future.status, "INVALID")
        self.assertTrue(any("format_version" in err for err in res_future.errors))

        # 証跡なし (v0.8 なら INTEGRITY_ONLY -> exit 2) の旧形式 Bundle でも exit 1 (INVALID) で拒否すること
        import shutil
        shutil.rmtree(self.repo / ".git" / "nightly_evidence" / "records")
        no_ev_zip = self.repo / "rel1_no_ev_base.zip"
        bundler.create_bundle(dest_path=no_ev_zip)
        no_ev_legacy_zip = self.repo / "rel1_no_ev_legacy.zip"
        self._rewrite_manifest(no_ev_zip, no_ev_legacy_zip, lambda m: m.pop("format_version", None))
        self.assertEqual(main(["--work-dir", str(self.repo), "verify-bundle", str(no_ev_legacy_zip)]), 1)




if __name__ == "__main__":
    unittest.main()
