from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import DEFAULT_VERIFY_SH, main
from auto_developer.guard import GitGuard
from auto_developer.integration_gate import IntegrationGate


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )


def _init_repo(repo_dir: Path, *, with_ir: bool = False) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test User")
    (repo_dir / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Plan\n- [ ] 1. Task\n", encoding="utf-8")
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    verify_sh = repo_dir / "scripts" / "verify_local.sh"
    verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    verify_sh.chmod(0o755)
    if with_ir:
        (repo_dir / "docs" / "designs").mkdir(parents=True, exist_ok=True)
        (repo_dir / "docs" / "designs" / "x.ir.json").write_text(
            '{"schema": "auto-dev-ir/1"}\n', encoding="utf-8"
        )
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "init")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


class TestGate14ProtectDesignIrs(unittest.TestCase):
    """GATE-14: docs/designs/*.ir.json を保護ファイルとして扱う。"""

    def test_guard_rejects_untracked_modified_and_deleted_design_ir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_repo(repo, with_ir=True)
            guard = GitGuard(repo_dir=repo)

            # 1. 変更の検知
            ir_path = repo / "docs" / "designs" / "x.ir.json"
            ir_path.write_text('{"schema": "auto-dev-ir/1", "tampered": true}\n', encoding="utf-8")
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertFalse(guard.check_protected_files_untouched(chk, allow_immunity_additions=True))
            self.assertIn("docs/designs/x.ir.json", guard.get_modified_protected_files(chk))

            # 2. 削除の検知
            _git(repo, "checkout", "--", "docs/designs/x.ir.json")
            ir_path.unlink()
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertFalse(guard.check_protected_files_untouched(chk, allow_immunity_additions=True))

            # 3. 未追跡の新規作成の検知
            _git(repo, "checkout", "--", "docs/designs/x.ir.json")
            new_ir = repo / "docs" / "designs" / "y.ir.json"
            new_ir.write_text('{"schema": "auto-dev-ir/1"}\n', encoding="utf-8")
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertFalse(guard.check_protected_files_untouched(chk, allow_immunity_additions=True))
            self.assertIn("docs/designs/y.ir.json", guard.get_modified_protected_files(chk))
            new_ir.unlink()

            # 4. docs/designs/*.md やサブディレクトリ配下は保護対象外
            (repo / "docs" / "designs" / "notes.md").write_text("# Design\n", encoding="utf-8")
            sub = repo / "docs" / "designs" / "sub"
            sub.mkdir(parents=True, exist_ok=True)
            (sub / "nested.ir.json").write_text("{}\n", encoding="utf-8")
            self.assertTrue(guard.check_protected_files_untouched(chk))

    def test_guard_rejects_design_ir_in_untracked_dir_and_allows_nested(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_repo(repo, with_ir=False)
            guard = GitGuard(repo_dir=repo)

            # docs/designs/ が未追跡の状態でも直下の *.ir.json 新規作成を検知する
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True, exist_ok=True)
            untracked_ir = designs_dir / "x.ir.json"
            untracked_ir.write_text('{"schema": "auto-dev-ir/1"}\n', encoding="utf-8")
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertFalse(guard.check_protected_files_untouched(chk, allow_immunity_additions=True))
            self.assertIn("docs/designs/x.ir.json", guard.get_modified_protected_files(chk))
            untracked_ir.unlink()

            # ネストしたサブディレクトリ配下の *.ir.json は保護対象外
            sub_dir = designs_dir / "sub"
            sub_dir.mkdir(parents=True, exist_ok=True)
            (sub_dir / "x.ir.json").write_text('{"schema": "auto-dev-ir/1"}\n', encoding="utf-8")
            self.assertTrue(guard.check_protected_files_untouched(chk))
            self.assertEqual(guard.get_modified_protected_files(chk), [])

    def test_verify_step_rejects_design_ir_modification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_repo(repo, with_ir=True)
            (repo / "docs" / "designs" / "x.ir.json").write_text(
                '{"schema": "auto-dev-ir/1", "tampered": true}\n', encoding="utf-8"
            )
            rc = main(["--work-dir", str(repo), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(rc, 10)

    def test_init_verify_sh_protects_design_irs(self) -> None:
        self.assertIn("docs/designs/*.ir.json", DEFAULT_VERIFY_SH)
        self.assertIn("docs/designs/[^/]+\\.ir\\.json$", DEFAULT_VERIFY_SH)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")
            rc = main(["--work-dir", str(repo), "init"])
            self.assertEqual(rc, 0)
            (repo / "tests").mkdir(exist_ok=True)
            (repo / "tests" / "test_dummy.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
                encoding="utf-8",
            )
            (repo / "docs" / "designs").mkdir(parents=True, exist_ok=True)
            (repo / "docs" / "designs" / "a.ir.json").write_text("{}\n", encoding="utf-8")
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "init")
            chk = _git(repo, "rev-parse", "HEAD").stdout.strip()

            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")}
            env["PYTHON"] = sys.executable
            verify_sh = repo / "scripts" / "verify_local.sh"

            # 正常時は通過する（docs/designs/*.md の追加も可）
            (repo / "docs" / "designs" / "readme.md").write_text("# doc\n", encoding="utf-8")
            ok_proc = subprocess.run(
                ["bash", str(verify_sh), chk], cwd=repo, env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(ok_proc.returncode, 0, ok_proc.stdout + ok_proc.stderr)

            # 未追跡の *.ir.json 追加で失敗する
            untracked_ir = repo / "docs" / "designs" / "new.ir.json"
            untracked_ir.write_text("{}\n", encoding="utf-8")
            fail_untracked = subprocess.run(
                ["bash", str(verify_sh), chk], cwd=repo, env=env, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(fail_untracked.returncode, 0)
            untracked_ir.unlink()

            # 追跡済み *.ir.json の変更で失敗する
            (repo / "docs" / "designs" / "a.ir.json").write_text('{"changed": true}\n', encoding="utf-8")
            fail_diff = subprocess.run(
                ["bash", str(verify_sh), chk], cwd=repo, env=env, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(fail_diff.returncode, 0)

    def test_init_verify_sh_protects_untracked_dir_and_allows_nested_ir(self) -> None:
        self.assertIn(":(glob)docs/designs/*.ir.json", DEFAULT_VERIFY_SH)
        self.assertIn("git status --porcelain -uall", DEFAULT_VERIFY_SH)

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")
            rc = main(["--work-dir", str(repo), "init"])
            self.assertEqual(rc, 0)
            (repo / "tests").mkdir(exist_ok=True)
            (repo / "tests" / "test_dummy.py").write_text(
                "import unittest\nclass T(unittest.TestCase):\n    def test_ok(self): pass\n",
                encoding="utf-8",
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "init")
            chk = _git(repo, "rev-parse", "HEAD").stdout.strip()

            env = {k: v for k, v in os.environ.items() if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")}
            env["PYTHON"] = sys.executable
            verify_sh = repo / "scripts" / "verify_local.sh"

            # 1. docs/designs/ が完全に未追跡の状態で docs/designs/x.ir.json を新規作成すると失敗する
            designs_dir = repo / "docs" / "designs"
            designs_dir.mkdir(parents=True, exist_ok=True)
            untracked_ir = designs_dir / "x.ir.json"
            untracked_ir.write_text("{}\n", encoding="utf-8")
            fail_untracked_dir = subprocess.run(
                ["bash", str(verify_sh), chk], cwd=repo, env=env, capture_output=True, text=True, check=False
            )
            self.assertNotEqual(fail_untracked_dir.returncode, 0)
            untracked_ir.unlink()

            # 2. ネストした docs/designs/sub/x.ir.json の新規作成は保護対象外（通過する）
            sub_dir = designs_dir / "sub"
            sub_dir.mkdir(parents=True, exist_ok=True)
            nested_ir = sub_dir / "x.ir.json"
            nested_ir.write_text("{}\n", encoding="utf-8")
            ok_nested_untracked = subprocess.run(
                ["bash", str(verify_sh), chk], cwd=repo, env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(ok_nested_untracked.returncode, 0, ok_nested_untracked.stdout + ok_nested_untracked.stderr)

            # 3. 追跡済みの docs/designs/sub/x.ir.json を変更しても git diff 側で保護対象外（通過する）
            _git(repo, "add", "docs/designs/sub/x.ir.json")
            _git(repo, "commit", "-m", "add nested ir")
            chk2 = _git(repo, "rev-parse", "HEAD").stdout.strip()
            nested_ir.write_text('{"changed": true}\n', encoding="utf-8")
            ok_nested_diff = subprocess.run(
                ["bash", str(verify_sh), chk2], cwd=repo, env=env, capture_output=True, text=True, check=False
            )
            self.assertEqual(ok_nested_diff.returncode, 0, ok_nested_diff.stdout + ok_nested_diff.stderr)

    def test_integration_gate_detects_design_ir_modification(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_repo(repo, with_ir=True)
            (repo / "docs" / "designs" / "x.ir.json").write_text(
                '{"schema": "auto-dev-ir/1", "hacked": true}\n', encoding="utf-8"
            )
            _git(repo, "add", "docs/designs/x.ir.json")
            _git(repo, "commit", "-m", "tamper ir")
            gate = IntegrationGate(repo_dir=repo)
            ok, code, out = gate.run_post_integration_verification(base_commit=chk)
            self.assertFalse(ok)
            self.assertEqual(code, 10)
            self.assertIn("docs/designs/x.ir.json", out)

    def test_skill_documents_human_commits_approved_ir(self) -> None:
        skill_path = Path(__file__).resolve().parents[1] / "skills" / "nightly-compile" / "SKILL.md"
        text = skill_path.read_text(encoding="utf-8")
        self.assertIn("docs/designs/<name>.ir.json", text)
        self.assertTrue(
            "human" in text.lower() or "人間" in text,
            "SKILL.md must document that the human commits approved IRs",
        )


if __name__ == "__main__":
    unittest.main()
