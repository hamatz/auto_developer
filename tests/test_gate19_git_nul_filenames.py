"""GATE-19: git の出力からファイル名を読むときに -z を強制する静的検査と振る舞いテスト。"""

from __future__ import annotations

import ast
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.boundary import BoundaryGuard
from auto_developer.bundler import check_audit_diff_status
from auto_developer.guard import GitGuard
from auto_developer.integration_gate import IntegrationGate

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_PACKAGE_DIR = REPO_ROOT / "src" / "auto_developer"

# SPEC 5.1 GATE-19: 対象外にする呼び出しは、許可の一覧に理由とともに書く。
# キー: (src/auto_developer/ からの相対パス, コマンド文字列の識別用部分文字列)
# 値: 対象外にする理由
ALLOWLISTED_GIT_CALLS: dict[tuple[str, str], str] = {
    (
        "cli.py",
        'PROTECTED_DIFF=$(git diff --name-only "$CHECKPOINT"',
    ): "init が生成する検証スクリプト (DEFAULT_VERIFY_SH) のシェル文字列であり、空かどうかだけを判定する。",
}


def _find_missing_z_violations(src_dir: Path) -> list[str]:
    """src/auto_developer/ 配下の Python コードから -z のない --name-only / --name-status 呼び出しを検出する。"""
    violations: list[str] = []
    target_flags = ("--name-only", "--name-status")

    for py_file in sorted(src_dir.rglob("*.py")):
        rel_path = py_file.relative_to(src_dir).as_posix()
        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))

        # 1. リスト・タプルリテラル内の ["--name-only", ...] / ["--name-status", ...] を検査
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)):
                str_elts = [
                    elt.value
                    for elt in node.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
                if (
                    any(flag in str_elts for flag in target_flags)
                    and "-z" not in str_elts
                    and "--null" not in str_elts
                ):
                    violations.append(
                        f"{rel_path}:{node.lineno}: git command list/tuple with "
                        f"--name-only/--name-status is missing '-z': {str_elts}"
                    )

            # 2. 文字列リテラル内の "git ... --name-only" / "--name-status" を検査
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if not any(flag in text for flag in target_flags):
                    continue
                for offset, line in enumerate(text.splitlines()):
                    if "git " not in line:
                        continue
                    if not any(flag in line for flag in target_flags):
                        continue
                    tokens = line.split()
                    if "-z" in tokens or "--null" in tokens:
                        continue
                    allow_hit = any(
                        allow_path == rel_path and allow_sub in line
                        for (allow_path, allow_sub) in ALLOWLISTED_GIT_CALLS
                    )
                    if not allow_hit:
                        violations.append(
                            f"{rel_path}:{node.lineno + offset}: git command string with "
                            f"--name-only/--name-status is missing '-z': {line.strip()}"
                        )

    return violations


def _git(repo_dir: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout


def _init_temp_repo(repo_dir: Path) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test User")
    _git(repo_dir, "config", "user.email", "test@example.com")
    # core.quotePath=true (git 既定) でも -z により引用符・8進数にならないことを確かめる
    _git(repo_dir, "config", "core.quotePath", "true")
    (repo_dir / "README.md").write_text("init\n", encoding="utf-8")
    _git(repo_dir, "add", "README.md")
    _git(repo_dir, "commit", "-m", "init")
    return _git(repo_dir, "rev-parse", "HEAD").strip()


class TestGate19StaticCheck(unittest.TestCase):
    """GATE-19: src/auto_developer/ の静的検査。"""

    def test_no_unlisted_git_name_only_or_status_without_z(self) -> None:
        violations = _find_missing_z_violations(SRC_PACKAGE_DIR)
        self.assertEqual(
            violations,
            [],
            "Found git --name-only/--name-status calls without '-z':\n" + "\n".join(violations),
        )

    def test_allowlist_entries_have_reasons(self) -> None:
        self.assertGreater(len(ALLOWLISTED_GIT_CALLS), 0)
        for key, reason in ALLOWLISTED_GIT_CALLS.items():
            self.assertTrue(reason.strip(), f"Allowlist entry {key} must have a non-empty reason")


class TestGate19NonAsciiFilenames(unittest.TestCase):
    """GATE-19: 日本語ファイル名が引用符・8進数ではなくそのままの名前で扱われること。"""

    def test_guard_protected_files_reports_unescaped_japanese_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_temp_repo(repo)
            target = repo / "docs" / "designs" / "設計.ir.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n", encoding="utf-8")

            guard = GitGuard(repo)
            modified = guard.get_modified_protected_files(checkpoint)
            self.assertIn("docs/designs/設計.ir.json", modified)
            self.assertFalse(guard.check_protected_files_untouched(checkpoint))

            # allow_immunity_additions=True 経路でも日本語ファイル名がそのまま検査される
            imm_file = repo / "tests" / "immunity" / "test_日本語.py"
            imm_file.parent.mkdir(parents=True, exist_ok=True)
            imm_file.write_text("import unittest\n", encoding="utf-8")
            self.assertIn("docs/designs/設計.ir.json", guard.get_modified_protected_files(checkpoint, True))

    def test_boundary_enforcer_returns_unescaped_japanese_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_temp_repo(repo)
            jp_file = repo / "src" / "日本語.py"
            jp_file.parent.mkdir(parents=True, exist_ok=True)
            jp_file.write_text("x = 1\n", encoding="utf-8")
            _git(repo, "add", "src/日本語.py")
            _git(repo, "commit", "-m", "add jp")

            enforcer = BoundaryGuard(repo, owns_patterns=["docs/**"], base_checkpoint=checkpoint)
            modified = enforcer.get_modified_files()
            self.assertEqual(modified, ["src/日本語.py"])

            result = enforcer.inspect_changes()
            self.assertFalse(result.allowed)
            self.assertIn("src/日本語.py", result.violations)

    def test_bundler_audit_outdated_message_shows_unescaped_japanese_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            target_commit = _init_temp_repo(repo)
            jp_file = repo / "src" / "日本語モジュール.py"
            jp_file.parent.mkdir(parents=True, exist_ok=True)
            jp_file.write_text("x = 1\n", encoding="utf-8")
            _git(repo, "add", "src/日本語モジュール.py")
            _git(repo, "commit", "-m", "modify jp")
            head_sha = _git(repo, "rev-parse", "HEAD").strip()

            status, msg = check_audit_diff_status(repo, target_commit, head_sha)
            self.assertEqual(status, "OUTDATED")
            self.assertIn("src/日本語モジュール.py", msg)

    def test_integration_gate_protected_diff_shows_unescaped_japanese_filename(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            base_commit = _init_temp_repo(repo)
            target = repo / "docs" / "designs" / "設計.ir.json"
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("{}\n", encoding="utf-8")
            _git(repo, "add", "docs/designs/設計.ir.json")
            _git(repo, "commit", "-m", "modify protected jp")

            gate = IntegrationGate(repo)
            ok, code, msg = gate.run_post_integration_verification(base_commit=base_commit)
            self.assertFalse(ok)
            self.assertEqual(code, 10)
            self.assertIn("docs/designs/設計.ir.json", msg)


if __name__ == "__main__":
    unittest.main()
