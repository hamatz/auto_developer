"""GATE-21: tests/ 配下のテスト記述規約を検証する静的検査テスト。"""

from __future__ import annotations

import ast
import re
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# Python 3.10 に存在しない標準ライブラリモジュール（3.11+ で追加）。
# テストで読み込む場合は ImportError / ModuleNotFoundError を捕捉する try ブロック内に置くこと。
PYTHON_311_PLUS_STDLIB_MODULES: frozenset[str] = frozenset(
    {
        "tomllib",
    }
)

# どうしても対象外にするものがある場合は (tests/ からの相対パス, 識別子): 理由 で登録する。
ALLOWLISTED_HYGIENE_EXCEPTIONS: dict[tuple[str, str], str] = {}

_GIT_INIT_CMD_RE = re.compile(r'(?:^|[\s;&|(`"\'])git\s+init(?=$|[\s;&|)`"\'])')
_BRANCH_FLAG_RE = re.compile(r'(?:^|\s)(?:-b(?:\s|$)|--initial-branch(?:=|\s|$))')
_PRIVKEY_BEGIN = "-----BEGIN"
_PRIVKEY_END = "PRIVATE KEY-----"


def _collect_docstring_node_ids(tree: ast.AST) -> set[int]:
    """モジュール・クラス・関数の先頭 docstring ノードの id を収集する。"""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if (
            isinstance(
                node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
            )
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            ids.add(id(node.body[0].value))
    return ids


def _has_branch_flag_in_list(str_elts: list[str]) -> bool:
    return "-b" in str_elts or any(
        s == "--initial-branch" or s.startswith("--initial-branch=") for s in str_elts
    )


def _is_import_error_handler(handler: ast.ExceptHandler) -> bool:
    if handler.type is None:
        return True
    names: list[str] = []
    if isinstance(handler.type, ast.Name):
        names.append(handler.type.id)
    elif isinstance(handler.type, ast.Tuple):
        for elt in handler.type.elts:
            if isinstance(elt, ast.Name):
                names.append(elt.id)
    return any(
        n in {"ImportError", "ModuleNotFoundError", "Exception", "BaseException"}
        for n in names
    )


def _find_unprotected_311_imports(
    tree: ast.AST, rel_path: str
) -> list[str]:
    """try (ImportError/ModuleNotFoundError) の外にある Python 3.11+ 標準ライブラリ import を検出する。"""
    violations: list[str] = []

    def _visit(node: ast.AST, in_guarded_try: bool) -> None:
        if isinstance(node, ast.Try):
            guarded = in_guarded_try or any(
                _is_import_error_handler(h) for h in node.handlers
            )
            for child in node.body:
                _visit(child, guarded)
            for child in node.handlers:
                _visit(child, in_guarded_try)
            for child in node.orelse:
                _visit(child, in_guarded_try)
            for child in node.finalbody:
                _visit(child, in_guarded_try)
            return

        if isinstance(node, ast.Import) and not in_guarded_try:
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in PYTHON_311_PLUS_STDLIB_MODULES:
                    violations.append(
                        f"{rel_path}:{node.lineno}: '{top}' imported outside try/except ImportError"
                    )
        elif (
            isinstance(node, ast.ImportFrom)
            and not in_guarded_try
            and node.level == 0
            and node.module
        ):
            top = node.module.split(".")[0]
            if top in PYTHON_311_PLUS_STDLIB_MODULES:
                violations.append(
                    f"{rel_path}:{node.lineno}: 'from {node.module} import ...' outside try/except ImportError"
                )

        for child in ast.iter_child_nodes(node):
            _visit(child, in_guarded_try)

    _visit(tree, False)
    return violations


def _is_allowlisted(rel_path: str, text: str) -> bool:
    return any(
        allow_path == rel_path and allow_sub in text
        for (allow_path, allow_sub) in ALLOWLISTED_HYGIENE_EXCEPTIONS
    )


def find_test_hygiene_violations(tests_dir: Path) -> list[str]:
    """tests/ 配下（immunity/ を除く）の GATE-21 違反をすべて返す。"""
    violations: list[str] = []

    for py_file in sorted(tests_dir.rglob("*.py")):
        rel_path = py_file.relative_to(tests_dir).as_posix()
        if rel_path == "immunity" or rel_path.startswith("immunity/"):
            continue

        source = py_file.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(py_file))
        docstring_ids = _collect_docstring_node_ids(tree)

        # 1. git init のブランチ指定漏れ & 3. 秘密鍵マーカーの単一文字列定数
        for node in ast.walk(tree):
            if isinstance(node, (ast.List, ast.Tuple)):
                str_elts = [
                    elt.value
                    for elt in node.elts
                    if isinstance(elt, ast.Constant) and isinstance(elt.value, str)
                ]
                if "git" in str_elts and "init" in str_elts:
                    git_idx = str_elts.index("git")
                    if (
                        git_idx + 1 < len(str_elts)
                        and str_elts[git_idx + 1] == "init"
                        and not _has_branch_flag_in_list(str_elts)
                    ):
                        detail = repr(str_elts)
                        if not _is_allowlisted(rel_path, detail):
                            violations.append(
                                f"{rel_path}:{node.lineno}: git init list/tuple missing -b or --initial-branch: {detail}"
                            )

            elif isinstance(node, ast.Call):
                func_name = ""
                if isinstance(node.func, ast.Name):
                    func_name = node.func.id
                elif isinstance(node.func, ast.Attribute):
                    func_name = node.func.attr
                if "git" in func_name.lower():
                    str_args = [
                        arg.value
                        for arg in node.args
                        if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    ]
                    if (
                        str_args
                        and str_args[0] == "init"
                        and not _has_branch_flag_in_list(str_args)
                    ):
                        detail = f"{func_name}({str_args})"
                        if not _is_allowlisted(rel_path, detail):
                            violations.append(
                                f"{rel_path}:{node.lineno}: git helper call with 'init' missing -b or --initial-branch: {detail}"
                            )

            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                text = node.value
                if (
                    _PRIVKEY_BEGIN in text
                    and _PRIVKEY_END in text
                    and not _is_allowlisted(rel_path, _PRIVKEY_BEGIN)
                ):
                    violations.append(
                        f"{rel_path}:{node.lineno}: private key marker literal must be assembled at runtime"
                    )

                if id(node) not in docstring_ids and "git" in text and "init" in text:
                    for offset, line in enumerate(text.splitlines()):
                        if (
                            _GIT_INIT_CMD_RE.search(line)
                            and not re.search(r"\bgit\s+init\s+failed\b", line)
                            and not _BRANCH_FLAG_RE.search(line)
                        ):
                            stripped = line.strip()
                            if not _is_allowlisted(rel_path, stripped):
                                violations.append(
                                    f"{rel_path}:{node.lineno + offset}: git init command string missing -b or --initial-branch: {stripped}"
                                )

        # 2. Python 3.10 にない標準ライブラリモジュールの try 外 import
        violations.extend(_find_unprotected_311_imports(tree, rel_path))

    return violations


class TestGate21TestHygiene(unittest.TestCase):
    """GATE-21: テストの書き方の規則を静的に検証する。"""

    def test_tests_comply_with_gate21_hygiene_rules(self) -> None:
        violations = find_test_hygiene_violations(TESTS_DIR)
        self.assertEqual(
            violations,
            [],
            "GATE-21 test hygiene violations found:\n" + "\n".join(violations),
        )

    def test_allowlist_entries_have_non_empty_reasons(self) -> None:
        for key, reason in ALLOWLISTED_HYGIENE_EXCEPTIONS.items():
            self.assertTrue(
                reason.strip(), f"Allowlist entry {key} must have a non-empty reason"
            )

    def test_detector_catches_all_three_violation_types_and_skips_immunity(
        self,
    ) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_tests = Path(tmp)
            (tmp_tests / "immunity").mkdir()
            # immunity/ 配下は検査対象外
            (tmp_tests / "immunity" / "test_ignored.py").write_text(
                'import tomllib\ncmd = ["git", "init"]\n',
                encoding="utf-8",
            )
            bad = tmp_tests / "test_bad.py"
            bad.write_text(
                "\n".join(
                    [
                        "import tomllib",
                        'cmd_list = ["git", "init"]',
                        'cmd_str = "' + "git " + 'init && git status"',
                        f'key = "{_PRIVKEY_BEGIN} {_PRIVKEY_END}"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            found = find_test_hygiene_violations(tmp_tests)
            self.assertEqual(len(found), 4)
            self.assertTrue(any("tomllib" in v for v in found))
            self.assertTrue(any("list/tuple" in v for v in found))
            self.assertTrue(any("command string" in v for v in found))
            self.assertTrue(any("private key marker" in v for v in found))

            good = tmp_tests / "test_good.py"
            good.write_text(
                "\n".join(
                    [
                        "try:",
                        "    import tomllib",
                        "except ModuleNotFoundError:",
                        "    tomllib = None",
                        'cmd_list = ["git", "init", "-b", "main"]',
                        'cmd_str = "git init --initial-branch=main && git status"',
                        f'key = "{_PRIVKEY_BEGIN} " + "{_PRIVKEY_END}"',
                    ]
                )
                + "\n",
                encoding="utf-8",
            )
            bad.unlink()
            self.assertEqual(find_test_hygiene_violations(tmp_tests), [])


if __name__ == "__main__":
    unittest.main()
