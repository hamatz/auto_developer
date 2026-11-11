from __future__ import annotations

import hashlib
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.audit import (
    AuditFinding,
    AuditResolutionManager,
)
from auto_developer.audit_store import AuditStore
from auto_developer.evidence import EvidenceStore


class TestAuditResolution(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)

        # Git リポジトリ初期化 (HHH.1)
        subprocess.run(["git", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "AutoDev Test"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "autodev@example.com"], cwd=self.repo_dir, check=True, capture_output=True)

        self.src_dir = self.repo_dir / "src"
        self.src_dir.mkdir(parents=True, exist_ok=True)
        self.immunity_dir = self.repo_dir / "tests" / "immunity"
        self.immunity_dir.mkdir(parents=True, exist_ok=True)

        # 初期アプリコード（バグあり: 0除算で -999 を返しアサーション失敗を起こす）
        self.app_py = self.src_dir / "app.py"
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    if b == 0:\n"
            "        return -999\n"
            "    return a // b\n",
            encoding="utf-8",
        )

        # 初期 LESSONS.md
        self.lessons_md = self.repo_dir / "LESSONS.md"
        self.lessons_md.write_text(
            "# Repository Lessons Learned\n\n## 恒久教訓インデックス\n",
            encoding="utf-8",
        )

        # 初期コミット（Before Commit）
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: initial commit with seed bug"], cwd=self.repo_dir, check=True, capture_output=True)
        self.before_commit_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

        self.audit_store = AuditStore(repo_dir=self.repo_dir)
        self.manager = AuditResolutionManager(repo_dir=self.repo_dir)

    def tearDown(self) -> None:
        self.tmp_dir.cleanup()

    def _commit_fix(self) -> str:
        """バグ修正を適用し、Git にコミットして After Commit SHA を返す。"""
        self.app_py.write_text(
            "def safe_div(a: int, b: int) -> int:\n"
            "    if b == 0:\n"
            "        return 0\n"
            "    return a // b\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "fix: safe_div handles zero divisor"], cwd=self.repo_dir, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    def test_audit_resolution_resolves_and_promotes_immunity(self) -> None:
        """機能EEE.3 / HHH.1: 修正コミット完了したAUDタスクがBefore Red/After Greenを実証し、免疫テストへ自動昇格されること。"""
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestDiv(unittest.TestCase):\n"
            "    def test_zero(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        code_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
        finding = AuditFinding(
            finding_id="BUG-ZERO-DIV",
            severity="P0",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Divide by zero in safe_div",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha=self.before_commit_sha,
            code_hash=code_hash,
        )
        self.audit_store.save_finding(finding)

        # 1. 修正前（Before Commit のまま）: コミット前進がないため NO_COMMIT_ADVANCE で拒絶される
        res_before = self.manager.resolve_task("AUD-BUG-ZERO-DIV")
        self.assertFalse(res_before["success"])
        self.assertEqual(res_before.get("error"), "NO_COMMIT_ADVANCE")

        # 2. 修正実施してコミット
        after_commit = self._commit_fix()
        self.assertNotEqual(after_commit, self.before_commit_sha)

        # 3. 修正後: resolve_task が成功し、After Green が確認されること
        res_after = self.manager.resolve_task("AUD-BUG-ZERO-DIV")
        self.assertTrue(res_after["success"], res_after.get("reason"))
        proof = res_after["proof"]
        self.assertEqual(proof["finding_id"], "BUG-ZERO-DIV")
        self.assertEqual(proof["before_commit"], self.before_commit_sha)
        self.assertEqual(proof["after_commit"], after_commit)
        self.assertEqual(proof["before_status"], "VERIFIED_RED")
        self.assertEqual(proof["after_status"], "GREEN")
        self.assertEqual(proof["before_result"]["reason"], "GENUINE_RED")
        self.assertEqual(proof["after_result"]["reason"], "ALREADY_GREEN")

        # 4. 免疫テストとして昇格配置されたことを検証
        promoted_path = self.repo_dir / proof["immunity_test_path"]
        self.assertTrue(promoted_path.exists())
        self.assertIn("class TestDiv", promoted_path.read_text(encoding="utf-8"))

        # 5. Finding のステータスが RESOLVED に更新されていること
        updated_finding = self.audit_store.get_finding("BUG-ZERO-DIV")
        self.assertIsNotNone(updated_finding)
        self.assertEqual(updated_finding.status, "RESOLVED")

    def test_resolve_task_rejects_non_git_repo(self) -> None:
        """機能HHH.1: 非Gitリポジトリ環境での解決呼び出しは UNCOMMITTED_AFTER_STATE で安全拒絶されること。"""
        with tempfile.TemporaryDirectory() as non_git_tmp:
            non_git_repo = Path(non_git_tmp)
            mgr = AuditResolutionManager(repo_dir=non_git_repo)
            store = AuditStore(repo_dir=non_git_repo)
            finding = AuditFinding(
                finding_id="BUG-NON-GIT",
                severity="P1",
                spec_reference="SPEC.md",
                location="app.py:1",
                description="Non-git test",
                reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n",
                status="VERIFIED_RED",
                red_verification_reason="GENUINE_RED",
                before_commit_sha="1234567890abcdef1234567890abcdef12345678",
            )
            store.save_finding(finding)
            res = mgr.resolve_task("AUD-BUG-NON-GIT")
            self.assertFalse(res["success"])
            self.assertEqual(res.get("error"), "UNCOMMITTED_AFTER_STATE")

    def test_resolve_task_rejects_uncommitted_after_state(self) -> None:
        """機能HHH.1: 作業ツリーに未コミット変更が存在する場合、UNCOMMITTED_AFTER_STATE で拒絶され一切変更されないこと。"""
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestUncommitted(unittest.TestCase):\n"
            "    def test_zero(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        finding = AuditFinding(
            finding_id="BUG-UNCOMMITTED",
            severity="P0",
            spec_reference="SPEC.md",
            location="src/app.py:2",
            description="Uncommitted test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha=self.before_commit_sha,
            code_hash=hashlib.sha256(repro_code.encode("utf-8")).hexdigest(),
        )
        self.audit_store.save_finding(finding)

        # ファイルを変更するが、git commit しない
        self.app_py.write_text("def safe_div(a: int, b: int) -> int: return 0\n", encoding="utf-8")

        res = self.manager.resolve_task("AUD-BUG-UNCOMMITTED")
        self.assertFalse(res["success"])
        self.assertEqual(res.get("error"), "UNCOMMITTED_AFTER_STATE")

        # 免疫テストが作成されていないこと
        target_immunity = self.repo_dir / "tests" / "immunity" / "test_immunity_BUG_UNCOMMITTED.py"
        self.assertFalse(target_immunity.exists())

        # Finding が RESOLVED になっていないこと
        stored = self.audit_store.get_finding("BUG-UNCOMMITTED")
        self.assertIsNotNone(stored)
        self.assertNotEqual(stored.status, "RESOLVED")

    def test_resolve_task_rejects_invalid_before_commit(self) -> None:
        """機能HHH.1: before_commit_sha が実在しないコミットや不正値の場合、INVALID_BEFORE_COMMIT で拒絶されること。"""
        self._commit_fix()

        repro_code = "import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n"
        finding = AuditFinding(
            finding_id="BUG-INVALID-BEFORE",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/app.py:2",
            description="Invalid before commit test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha="0000000000000000000000000000000000000000",  # 不在のコミット
            code_hash=hashlib.sha256(repro_code.encode("utf-8")).hexdigest(),
        )
        self.audit_store.save_finding(finding)

        res = self.manager.resolve_task("AUD-BUG-INVALID-BEFORE")
        self.assertFalse(res["success"])
        self.assertEqual(res.get("error"), "INVALID_BEFORE_COMMIT")

    def test_resolve_task_rejects_no_commit_advance_identical(self) -> None:
        """機能HHH.1: after_commit が before_commit と同一の場合、NO_COMMIT_ADVANCE で拒絶されること。"""
        repro_code = "import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n"
        finding = AuditFinding(
            finding_id="BUG-NO-ADVANCE",
            severity="P1",
            spec_reference="SPEC.md",
            location="src/app.py:2",
            description="No advance commit test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha=self.before_commit_sha,  # HEAD と同じ
            code_hash=hashlib.sha256(repro_code.encode("utf-8")).hexdigest(),
        )
        self.audit_store.save_finding(finding)

        res = self.manager.resolve_task("AUD-BUG-NO-ADVANCE")
        self.assertFalse(res["success"])
        self.assertEqual(res.get("error"), "NO_COMMIT_ADVANCE")

    def test_evidence_records_red_green_proof(self) -> None:
        """機能EEE.3: Before Red / After Green 証跡が Evidence Record に確実に拘束・保存されること。"""
        ev_store = EvidenceStore(repo_dir=self.repo_dir)
        proof = {
            "finding_id": "BUG-ZERO-DIV",
            "before_commit": self.before_commit_sha,
            "after_commit": "after1234567890abcdef1234567890abcdef12",
            "before_status": "VERIFIED_RED",
            "after_status": "GREEN",
            "immunity_test_path": "tests/immunity/test_immunity_BUG_ZERO_DIV.py",
        }
        ev = ev_store.issue_evidence(
            task_id="AUD-BUG-ZERO-DIV",
            checkpoint=self.before_commit_sha,
            tree_sha="tree123",
            spec_sha="spec123",
            details={
                "verification": {
                    "exit_code": 0,
                    "red_green_proof": proof,
                }
            },
        )
        self.assertEqual(ev["details"]["verification"]["red_green_proof"]["after_status"], "GREEN")

        consumed = ev_store.verify_and_consume("AUD-BUG-ZERO-DIV", checkpoint=self.before_commit_sha)
        self.assertTrue(consumed)

        archived = ev_store.archive_record(
            task_id="AUD-BUG-ZERO-DIV",
            verification_info={"exit_code": 0, "red_green_proof": proof},
        )
        self.assertIsNotNone(archived)
        self.assertEqual(archived["verification"]["red_green_proof"]["finding_id"], "BUG-ZERO-DIV")

    def test_resolve_task_rejects_overwriting_different_existing_immunity_test(self) -> None:
        """機能FFF.1 / HHH.1: 異なる内容の既存免疫テストが存在する場合、IMMUNITY_COLLISION で拒絶され元ファイルが保護されること。"""
        self._commit_fix()

        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestNew(unittest.TestCase):\n"
            "    def test_new(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        finding = AuditFinding(
            finding_id="BUG-COLLIDE",
            severity="P0",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Collide test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha=self.before_commit_sha,
            code_hash=hashlib.sha256(repro_code.encode("utf-8")).hexdigest(),
        )
        self.audit_store.save_finding(finding)

        # 既存の免疫テスト（異なる内容）を事前配置してコミット（作業ツリーをクリーンに保つ）
        target_immunity_file = self.repo_dir / "tests" / "immunity" / "test_immunity_BUG_COLLIDE.py"
        original_content = "# ORIGINAL IMMUNITY TEST CONTENT\n"
        target_immunity_file.write_text(original_content, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: add existing immunity test"], cwd=self.repo_dir, check=True, capture_output=True)

        res = self.manager.resolve_task("AUD-BUG-COLLIDE")
        self.assertFalse(res["success"])
        self.assertEqual(res.get("error"), "IMMUNITY_COLLISION")

        # 元ファイルが一切上書き・削除されていないこと
        self.assertEqual(target_immunity_file.read_text(encoding="utf-8"), original_content)

    def test_resolve_task_idempotent_when_existing_immunity_test_has_same_hash(self) -> None:
        """機能FFF.1 / HHH.1: 同一ハッシュの既存免疫テストが存在する場合、上書きせず冪等に成功すること。"""
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestSame(unittest.TestCase):\n"
            "    def test_same(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        code_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
        finding = AuditFinding(
            finding_id="BUG-IDEMPOTENT",
            severity="P1",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Idempotent test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha=self.before_commit_sha,
            code_hash=code_hash,
        )
        self.audit_store.save_finding(finding)

        self._commit_fix()

        # 同一内容のファイルを事前に配置してコミット（クリーンツリー維持）
        target_immunity_file = self.repo_dir / "tests" / "immunity" / "test_immunity_BUG_IDEMPOTENT.py"
        target_immunity_file.write_text(repro_code, encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "chore: add identical immunity test"], cwd=self.repo_dir, check=True, capture_output=True)

        res = self.manager.resolve_task("AUD-BUG-IDEMPOTENT")
        self.assertTrue(res["success"], res.get("reason"))
        self.assertEqual(target_immunity_file.read_text(encoding="utf-8"), repro_code)

    def test_resolve_task_rejects_unverified_before_red_status_reported(self) -> None:
        """機能FFF.2 / HHH.1: status='REPORTED' のまま実証されていないFindingは UNVERIFIED_BEFORE_RED で拒絶されること。"""
        self._commit_fix()
        finding = AuditFinding(
            finding_id="BUG-UNVERIFIED",
            severity="P1",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Unverified red finding",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n",
            status="REPORTED",
            before_commit_sha=self.before_commit_sha,
        )
        self.audit_store.save_finding(finding)

        res = self.manager.resolve_task("AUD-BUG-UNVERIFIED")
        self.assertFalse(res["success"])
        self.assertEqual(res.get("error"), "UNVERIFIED_BEFORE_RED")

    def test_resolve_task_rejects_missing_genuine_red_proof(self) -> None:
        """機能FFF.2 / HHH.1: statusがVERIFIED_REDでも GENUINE_RED 証跡を持たないFindingは拒絶されること。"""
        self._commit_fix()
        finding = AuditFinding(
            finding_id="BUG-NO-PROOF",
            severity="P1",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Finding lacking GENUINE_RED proof",
            reproduction_test_code="import unittest\nclass T(unittest.TestCase):\n    def test(self): pass\n",
            status="VERIFIED_RED",
            red_verification_reason=None,
            before_commit_sha=self.before_commit_sha,
        )
        self.audit_store.save_finding(finding)

        res = self.manager.resolve_task("AUD-BUG-NO-PROOF")
        self.assertFalse(res["success"])
        self.assertEqual(res.get("error"), "UNVERIFIED_BEFORE_RED")

    def test_resolve_task_binds_full_cryptographic_proof(self) -> None:
        """機能FFF.2 / HHH.1: 解決時に before_commit, after_commit, repro_test_sha256, before_result, after_result が完全拘束されること。"""
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestFull(unittest.TestCase):\n"
            "    def test_full(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        code_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
        finding = AuditFinding(
            finding_id="BUG-CRYPTO-PROOF",
            severity="P0",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Full cryptographic proof test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha=self.before_commit_sha,
            code_hash=code_hash,
        )
        self.audit_store.save_finding(finding)

        after_commit = self._commit_fix()

        res = self.manager.resolve_task("AUD-BUG-CRYPTO-PROOF")
        self.assertTrue(res["success"], res.get("reason"))
        proof = res["proof"]
        self.assertEqual(proof["before_commit"], self.before_commit_sha)
        self.assertEqual(proof["after_commit"], after_commit)
        self.assertEqual(proof["repro_test_sha256"], code_hash)
        self.assertEqual(proof["before_status"], "VERIFIED_RED")
        self.assertEqual(proof["after_status"], "GREEN")
        self.assertEqual(proof["before_result"]["reason"], "GENUINE_RED")
        self.assertEqual(proof["after_result"]["reason"], "ALREADY_GREEN")

    def test_resolve_task_fails_when_record_lesson_fails(self) -> None:
        """機能FFF.3 / HHH.1: LESSONS.md への記録に失敗した場合、RESOLVEDにせず LEARNING_RECORD_FAILED で停止すること。"""
        repro_code = (
            "import unittest\n"
            "from app import safe_div\n\n"
            "class TestLessonFail(unittest.TestCase):\n"
            "    def test_l(self):\n"
            "        self.assertEqual(safe_div(10, 0), 0)\n"
        )
        code_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
        finding = AuditFinding(
            finding_id="BUG-LESSON-FAIL",
            severity="P0",
            spec_reference="SPEC.md 2.1",
            location="src/app.py:2",
            description="Lesson fail test",
            reproduction_test_code=repro_code,
            status="VERIFIED_RED",
            red_verification_reason="GENUINE_RED",
            before_commit_sha=self.before_commit_sha,
            code_hash=code_hash,
        )
        self.audit_store.save_finding(finding)

        self._commit_fix()

        # LESSONS.md を削除してコミット（クリーンツリー維持）
        lessons_path = self.repo_dir / "LESSONS.md"
        if lessons_path.exists():
            subprocess.run(["git", "rm", "LESSONS.md"], cwd=self.repo_dir, check=True, capture_output=True)
            subprocess.run(["git", "commit", "-m", "chore: remove LESSONS.md to trigger failure"], cwd=self.repo_dir, check=True, capture_output=True)

        res = self.manager.resolve_task("AUD-BUG-LESSON-FAIL")
        self.assertFalse(res["success"])
        self.assertEqual(res.get("error"), "LEARNING_RECORD_FAILED")

        # Finding のステータスが LEARNING_RECORD_FAILED に更新され、RESOLVED になっていないこと
        updated_finding = self.audit_store.get_finding("BUG-LESSON-FAIL")
        self.assertIsNotNone(updated_finding)
        self.assertEqual(updated_finding.status, "LEARNING_RECORD_FAILED")


if __name__ == "__main__":
    unittest.main()
