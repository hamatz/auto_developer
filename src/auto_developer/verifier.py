from __future__ import annotations

import ast
import builtins
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

from auto_developer.guard import validate_test_ast


def _eval_constant_expr(node: ast.AST, var_env: dict[str, Any] | None = None) -> tuple[bool, Any]:
    """リテラル定数、符号付き定数、定数変数、定数同士の四則演算等を安全に評価する。"""
    if isinstance(node, ast.Constant):
        return True, node.value
    if isinstance(node, ast.Name):
        if var_env is not None and node.id in var_env:
            return True, var_env[node.id]
        if node.id in ("True", "False", "None"):
            val_map = {"True": True, "False": False, "None": None}
            return True, val_map[node.id]
        return False, None
    if isinstance(node, (ast.List, ast.Tuple)):
        elems = []
        for elt in node.elts:
            ok, v = _eval_constant_expr(elt, var_env)
            if not ok:
                return False, None
            elems.append(v)
        return True, list(elems) if isinstance(node, ast.List) else tuple(elems)
    if isinstance(node, ast.Set):
        s_elems = set()
        for elt in node.elts:
            ok, v = _eval_constant_expr(elt, var_env)
            if not ok:
                return False, None
            try:
                s_elems.add(v)
            except TypeError:
                return False, None
        return True, s_elems
    if isinstance(node, ast.Dict):
        d = {}
        for k, v in zip(node.keys, node.values):
            if k is None:
                return False, None
            ok_k, vk = _eval_constant_expr(k, var_env)
            ok_v, vv = _eval_constant_expr(v, var_env)
            if not (ok_k and ok_v):
                return False, None
            try:
                d[vk] = vv
            except TypeError:
                return False, None
        return True, d
    if isinstance(node, ast.UnaryOp):
        ok, val = _eval_constant_expr(node.operand, var_env)
        if ok and isinstance(val, (int, float)):
            if isinstance(node.op, ast.USub):
                return True, -val
            if isinstance(node.op, ast.UAdd):
                return True, +val
        if ok and isinstance(node.op, ast.Not):
            return True, not val
    if isinstance(node, ast.BinOp):
        ok_l, val_l = _eval_constant_expr(node.left, var_env)
        ok_r, val_r = _eval_constant_expr(node.right, var_env)
        if (
            ok_l
            and ok_r
            and isinstance(val_l, (int, float, str))
            and isinstance(val_r, (int, float, str))
        ):
            with contextlib.suppress(TypeError, ValueError, ArithmeticError):
                if isinstance(node.op, ast.Add):
                    return True, val_l + val_r  # type: ignore[operator]
                if isinstance(node.op, ast.Sub) and isinstance(val_l, (int, float)) and isinstance(val_r, (int, float)):
                    return True, val_l - val_r
                if isinstance(node.op, ast.Mult) and isinstance(val_l, (int, float)) and isinstance(val_r, (int, float)):
                    return True, val_l * val_r
    return False, None


def _is_assert_call(call: ast.Call) -> bool:
    if (
        isinstance(call.func, ast.Attribute)
        and isinstance(call.func.value, ast.Name)
        and call.func.value.id == "self"
    ):
        attr = call.func.attr
        return attr.startswith("assert") or attr == "fail"
    return False


_BUILTIN_NAMES: set[str] = set(dir(builtins))
_STDLIB_MODULE_NAMES: set[str] = set(getattr(sys, "stdlib_module_names", ())) | {
    "ast",
    "contextlib",
    "hashlib",
    "json",
    "math",
    "os",
    "pathlib",
    "re",
    "shutil",
    "signal",
    "stat",
    "subprocess",
    "sys",
    "tempfile",
    "time",
    "unittest",
}
_NON_PRODUCT_ORIGINS: set[str] = _STDLIB_MODULE_NAMES | {
    "builtins",
    "self",
    "unittest",
    "test_local_helper",
}


def _get_root_name(node: ast.AST) -> str | None:
    curr = node
    while isinstance(curr, ast.Attribute):
        curr = curr.value
    if isinstance(curr, ast.Name):
        return curr.id
    return None


def _get_effective_origin(name: str, var_origins: dict[str, Any]) -> str | None:
    """var_origins (dict[str, str] または dict[str, set[str]]) から有効な起源を解決する (AUD-501)。
    同一変数に複数起源が存在する場合、プロダクト起源を最優先し、発振を防ぐ。
    """
    val = var_origins.get(name)
    if val is None:
        return None
    if isinstance(val, str):
        return val
    if isinstance(val, (set, list, tuple)):
        for o in val:
            if isinstance(o, str) and o not in _NON_PRODUCT_ORIGINS:
                return str(o)
        if val:
            first = next(iter(val))
            if isinstance(first, str):
                return str(first)
    return None


def _resolve_expr_origin(
    expr: ast.AST,
    imported_symbols: dict[str, str],
    imported_modules: dict[str, str],
    var_origins: dict[str, Any],
) -> str | None:
    """式 (Name / Attribute / Call / Literal) の解決元パッケージ・モジュール名または 'builtins' / 'self' / 'test_local_helper' を特定する (AUD-202 / AUD-301 / AUD-501)。"""
    if isinstance(expr, (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple)):
        return "builtins"
    if isinstance(expr, ast.Name):
        if expr.id == "self":
            return "self"
        eff = _get_effective_origin(expr.id, var_origins)
        if eff is not None:
            return eff
        if expr.id in imported_symbols:
            return imported_symbols[expr.id]
        if expr.id in imported_modules:
            return imported_modules[expr.id]
        if expr.id in _BUILTIN_NAMES:
            return "builtins"
        if expr.id in _STDLIB_MODULE_NAMES:
            return expr.id
        return None
    if isinstance(expr, ast.Attribute):
        # self.<attr> の属性アクセス (例: self.runner: AUD-601)
        if isinstance(expr.value, ast.Name) and expr.value.id == "self":
            eff = _get_effective_origin(f"self.{expr.attr}", var_origins)
            if eff is not None:
                return eff
        val_origin = _resolve_expr_origin(expr.value, imported_symbols, imported_modules, var_origins)
        if val_origin and val_origin != "self":
            return val_origin
        root_name = _get_root_name(expr)
        if root_name:
            if root_name == "self":
                return "self"
            eff = _get_effective_origin(root_name, var_origins)
            if eff is not None:
                return eff
            if root_name in imported_modules:
                return imported_modules[root_name]
            if root_name in imported_symbols:
                return imported_symbols[root_name]
            if root_name in _STDLIB_MODULE_NAMES:
                return root_name
        return None
    if isinstance(expr, ast.Call):
        is_getattr = (
            (isinstance(expr.func, ast.Name) and expr.func.id == "getattr")
            or (
                isinstance(expr.func, ast.Attribute)
                and isinstance(expr.func.value, ast.Name)
                and expr.func.value.id in ("builtins", "__builtins__")
                and expr.func.attr == "getattr"
            )
        )
        if is_getattr and len(expr.args) >= 1:
            return _resolve_expr_origin(expr.args[0], imported_symbols, imported_modules, var_origins)
        # 一般の関数・コンストラクタ呼出 (int("1"), str(x), pathlib.Path("."), etc.: AUD-401)
        func_origin = _resolve_expr_origin(expr.func, imported_symbols, imported_modules, var_origins)
        if func_origin:
            return func_origin
        return None
    return None


def _inspect_helper_fn_origins(
    fn_node: ast.FunctionDef | ast.AsyncFunctionDef,
    imported_symbols: dict[str, str],
    imported_modules: dict[str, str],
    base_origins: dict[str, Any],
) -> set[str]:
    """ヘルパー関数の return 式および内部代入から解決されるプロダクト起源を特定する (AUD-701)。"""
    res_origins: set[str] = set()
    local_origins: dict[str, set[str]] = {
        k: set(v) if isinstance(v, (set, list, tuple)) else {str(v)} for k, v in base_origins.items()
    }
    for s in ast.walk(fn_node):
        if isinstance(s, ast.Assign):
            orig = _resolve_expr_origin(s.value, imported_symbols, imported_modules, local_origins)
            if orig:
                for t in s.targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            local_origins.setdefault(n.id, set()).add(orig)
        elif isinstance(s, ast.Return) and s.value is not None:
            ret_orig = _resolve_expr_origin(s.value, imported_symbols, imported_modules, local_origins)
            if ret_orig and ret_orig not in _NON_PRODUCT_ORIGINS:
                res_origins.add(ret_orig)
    return res_origins


def _has_causal_dependency(
    node: ast.AST,
    dependent_vars: set[str],
    imported_symbols: dict[str, str] | None = None,
    imported_modules: dict[str, str] | None = None,
    var_origins: dict[str, Any] | None = None,
) -> bool:
    """ノードがプロダクト関数呼出または因果依存変数を含んでいるかを判定する (AUD-401)。"""
    for sub in ast.walk(node):
        if isinstance(sub, ast.Name) and sub.id in dependent_vars:
            return True
        if isinstance(sub, ast.Call) and not _is_assert_call(sub):
            origin = _resolve_expr_origin(
                sub.func,
                imported_symbols or {},
                imported_modules or {},
                var_origins or {},
            )
            # 呼出先が非プロダクト (builtins, stdlib, self, test_local_helper) でない場合はプロダクト呼出
            if origin not in _NON_PRODUCT_ORIGINS:
                return True
            # 非プロダクト呼出であっても、引数に因果依存変数またはプロダクト呼出が含まれていれば依存あり
            if any(
                _has_causal_dependency(arg, dependent_vars, imported_symbols, imported_modules, var_origins)
                for arg in sub.args
            ):
                return True
    return False


def _is_product_or_causal_call(
    call: ast.Call,
    dependent_vars: set[str] | None = None,
    func_node: ast.AST | None = None,
) -> bool:
    """呼出がプロダクトコードまたは因果依存変数に紐づく有効な呼出かを判定する (機能OOO.2 / AUD-003 Cycle 33 / AUD-002 Cycle 34 / AUD-020 Cycle 36 / AUD-101 Cycle 37 / AUD-202 Cycle 38 / AUD-301 Cycle 39)。
    アサーション呼出、因果依存のない孤立した Python 組み込み関数呼出 (int("1") など)、
    リテラル・定数に対するメソッド呼出 ("1".upper() など)、
    TestCase 自身のメソッド呼出 (self.id() など)、
    テスト内ローカルヘルパー呼出 (helper() など)、
    および標準ライブラリモジュール・関数呼出 (os.getcwd(), o.getcwd(), getcwd(), cwd(), getattr(os, 'getcwd') など) は除外する。
    """
    if _is_assert_call(call):
        return False

    imported_symbols: dict[str, str] = getattr(func_node, "_imported_symbols", {}) if func_node else {}
    imported_modules: dict[str, str] = getattr(func_node, "_imported_modules", {}) if func_node else {}
    var_origins: dict[str, Any] = getattr(func_node, "_var_origins", {}) if func_node else {}

    if isinstance(call.func, ast.Attribute):
        # builtins モジュール経由の呼出 (builtins.int(...) 等)
        if isinstance(call.func.value, ast.Name) and call.func.value.id in ("builtins", "__builtins__"):
            if call.func.attr not in _BUILTIN_NAMES:
                return True
            return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars) for arg in call.args))
        # リテラル定数に対するメソッド呼出 ("1".upper(), [].append(1) 等) はプロダクト呼出ではない
        if isinstance(call.func.value, (ast.Constant, ast.List, ast.Dict, ast.Set, ast.Tuple)):
            return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars) for arg in call.args))
        is_const, _ = _eval_constant_expr(call.func.value)
        if is_const:
            return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars) for arg in call.args))
        # TestCase 自身のメソッド呼出 (self.id(), self.shortDescription() 等: AUD-301)
        if isinstance(call.func.value, ast.Name) and call.func.value.id == "self":
            return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars) for arg in call.args))

        # 標準ライブラリ、builtins、self、テスト内ローカル定義等 (非プロダクト呼出: AUD-020 / AUD-101 / AUD-202 / AUD-301)
        root_name = _get_root_name(call.func)
        orig_mod = imported_modules.get(root_name) if root_name else None
        from_mod = imported_symbols.get(root_name) if root_name else None
        root_origin = _get_effective_origin(root_name, var_origins) if root_name else None
        val_origin = _resolve_expr_origin(call.func.value, imported_symbols, imported_modules, var_origins)

        # レシーバオブジェクト (val_origin) がプロダクト起源の場合、root_name が "self" (例: self.runner) であってもプロダクト呼出として承認 (AUD-601)
        if val_origin is not None and val_origin not in _NON_PRODUCT_ORIGINS:
            return True

        if any(
            o in _NON_PRODUCT_ORIGINS
            for o in (root_name, orig_mod, from_mod, root_origin, val_origin)
            if o is not None
        ):
            return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars, imported_symbols, imported_modules, var_origins) for arg in call.args))

        # プロダクト起源の確認: root_name が dependent_vars に含まれるか、モジュール・シンボル起源がプロダクトである場合のみ True (AUD-501)
        is_product = (
            (root_name is not None and root_name in (dependent_vars or set()))
            or (orig_mod is not None and orig_mod not in _NON_PRODUCT_ORIGINS)
            or (from_mod is not None and from_mod not in _NON_PRODUCT_ORIGINS)
            or (root_origin is not None and root_origin not in _NON_PRODUCT_ORIGINS)
            or (val_origin is not None and val_origin not in _NON_PRODUCT_ORIGINS)
        )
        if is_product:
            return True
        return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars, imported_symbols, imported_modules, var_origins) for arg in call.args))

    if isinstance(call.func, ast.Name):
        if call.func.id in _BUILTIN_NAMES:
            return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars) for arg in call.args))
        from_mod = imported_symbols.get(call.func.id)
        func_origin = _get_effective_origin(call.func.id, var_origins)
        if (
            call.func.id in _NON_PRODUCT_ORIGINS
            or from_mod in _NON_PRODUCT_ORIGINS
            or func_origin in _NON_PRODUCT_ORIGINS
            or (from_mod is None and call.func.id not in (dependent_vars or set()) and func_origin is None)
        ):
            return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars) for arg in call.args))
        return True

    return bool(dependent_vars and any(_has_causal_dependency(arg, dependent_vars) for arg in call.args))


def _walk_executable_calls(node: ast.AST) -> Iterator[ast.Call]:
    """遅延評価・ネスト定義（Lambda, FunctionDef等）に潜らず実行可能 Call を走査する (AUD-901)。"""
    if isinstance(node, (ast.Lambda, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return
    if isinstance(node, ast.Call):
        yield node
    for child in ast.iter_child_nodes(node):
        yield from _walk_executable_calls(child)


def _iter_executable_calls(stmts: list[ast.stmt]) -> Iterator[ast.Call]:
    """try ブロック等の文列から、実際に同期実行される Call ノードを列挙する (AUD-901)。
    静的死分岐 (if False:) や関数/ラムダ定義内の呼出を排除する。
    """
    for stmt in stmts:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        if isinstance(stmt, ast.If):
            is_const, val = _eval_constant_expr(stmt.test)
            if is_const:
                branch = stmt.body if bool(val) else stmt.orelse
                yield from _iter_executable_calls(branch)
            else:
                yield from _walk_executable_calls(stmt.test)
                yield from _iter_executable_calls(stmt.body)
                yield from _iter_executable_calls(stmt.orelse)
            continue
        if isinstance(stmt, (ast.Try, ast.With, ast.For, ast.While)):
            # 複合文の場合は直下の文を再帰探索
            body = getattr(stmt, "body", [])
            yield from _iter_executable_calls(body)
            continue
        yield from _walk_executable_calls(stmt)


def _is_in_try_block_with_call(
    call_node: ast.AST, func_node: ast.AST, dependent_vars: set[str] | None = None
) -> bool:
    """call_node が非アサーション呼出を含む try ブロックの body 内にあるか（例外検証テストの fail を救済）。"""
    for n in ast.walk(func_node):
        if isinstance(n, ast.Try):
            in_body = any(sub is call_node for item in n.body for sub in ast.walk(item))
            if in_body:
                has_call = any(
                    _is_product_or_causal_call(sub, dependent_vars, func_node)
                    for sub in _iter_executable_calls(n.body)
                )
                if has_call:
                    return True
    return False


def _handler_body_exits(stmts: list[ast.stmt]) -> bool:
    """except ハンドラの直接の文列が正常脱出 (return) するかを判定する (機能OOO.2 / AUD-003 Cycle 32 / AUD-801)。
    nested def/lambda 内の return や dead branch (if False: return) による偽装脱出を排除する。
    なお、raise による再送出はテストを ERROR にするため、後続の self.fail() を救済する正常脱出としては認めない (AUD-801)。
    """
    for stmt in stmts:
        if isinstance(stmt, ast.Return):
            return True
        if isinstance(stmt, ast.If):
            is_const, val = _eval_constant_expr(stmt.test)
            if is_const:
                branch = stmt.body if bool(val) else stmt.orelse
                if _handler_body_exits(branch):
                    return True
            elif _handler_body_exits(stmt.body) and _handler_body_exits(stmt.orelse):
                return True
    return False


def _is_preceded_by_try_with_call(
    call_node: ast.AST, func_node: ast.AST, dependent_vars: set[str] | None = None
) -> bool:
    """call_node がプロダクト呼出を含む try-except ブロックの直後（例外発生期待の fail）にあるかを判定する (機能OOO.2 / AUD-003 Cycle 33 / AUD-901)。"""
    for node in ast.walk(func_node):
        for attr in ("body", "orelse", "finalbody"):
            stmts = getattr(node, attr, None)
            if isinstance(stmts, list):
                for idx, stmt in enumerate(stmts):
                    if any(sub is call_node for sub in ast.walk(stmt)):
                        for prev in stmts[:idx]:
                            if isinstance(prev, ast.Try):
                                has_call = any(
                                    _is_product_or_causal_call(sub, dependent_vars, func_node)
                                    for sub in _iter_executable_calls(prev.body)
                                )
                                all_handlers_exit = (
                                    len(prev.handlers) > 0
                                    and all(_handler_body_exits(h.body) for h in prev.handlers)
                                )
                                if has_call and all_handlers_exit:
                                    return True
    return False


def _is_in_guarded_branch_with_call(
    call_node: ast.AST,
    func_node: ast.AST,
    dependent_vars: set[str],
) -> bool:
    """call_node がプロダクト呼出または因果依存変数を含む条件分岐 (If/While) の配下にあるかを判定する (機能OOO.2)。"""
    for n in ast.walk(func_node):
        if isinstance(n, (ast.If, ast.While)):
            in_body = any(sub is call_node for item in n.body for sub in ast.walk(item))
            in_orelse = any(sub is call_node for item in n.orelse for sub in ast.walk(item))
            if (in_body or in_orelse) and _has_causal_dependency(n.test, dependent_vars):
                return True
    return False


def _check_assert_call(
    call: ast.Call,
    var_env: dict[str, Any],
    dependent_vars: set[str],
    func_node: ast.AST,
    trivial_reasons: list[str],
) -> None:
    """アサーション呼出を現在のプログラム状態 (var_env, dependent_vars) で評価する。"""
    if not _is_assert_call(call):
        return

    attr = call.func.attr if isinstance(call.func, ast.Attribute) else ""

    # self.fail() の検証 (例外ガードまたは条件分岐因果ガード下の fail を救済: 機能OOO.2)
    if attr == "fail":
        is_in_try = _is_in_try_block_with_call(call, func_node, dependent_vars)
        is_in_guard = _is_in_guarded_branch_with_call(call, func_node, dependent_vars)
        is_after_try = _is_preceded_by_try_with_call(call, func_node, dependent_vars)
        if not (is_in_try or is_in_guard or is_after_try):
            trivial_reasons.append("Unconditional self.fail() call without exception guard")
        return

    # 引数がプロダクトコード呼出や依存変数に繋がっているか検証
    arg_has_dependency = any(_has_causal_dependency(arg, dependent_vars) for arg in call.args)
    if arg_has_dependency:
        # プロダクトコードとの因果依存があるため Trivial Red ではない
        return

    # 因果依存がない引数の固定値比較失敗判定
    if attr in ("assertEqual", "assertEquals") and len(call.args) >= 2:
        is_c1, v1 = _eval_constant_expr(call.args[0], var_env)
        is_c2, v2 = _eval_constant_expr(call.args[1], var_env)
        if is_c1 and is_c2 and v1 != v2:
            trivial_reasons.append(f"Constant equality mismatch without causal dependency: {v1!r} == {v2!r}")
    elif attr in ("assertNotEqual", "assertNotEquals") and len(call.args) >= 2:
        is_c1, v1 = _eval_constant_expr(call.args[0], var_env)
        is_c2, v2 = _eval_constant_expr(call.args[1], var_env)
        if is_c1 and is_c2 and v1 == v2:
            trivial_reasons.append(f"Constant inequality mismatch without causal dependency: {v1!r} != {v2!r}")
    elif attr in ("assertTrue", "assert_") and len(call.args) >= 1:
        is_c, val = _eval_constant_expr(call.args[0], var_env)
        if is_c and not bool(val):
            trivial_reasons.append(f"Constant assertTrue({val!r}) failure without causal dependency")
    elif attr == "assertFalse" and len(call.args) >= 1:
        is_c, val = _eval_constant_expr(call.args[0], var_env)
        if is_c and bool(val):
            trivial_reasons.append(f"Constant assertFalse({val!r}) failure without causal dependency")
    elif attr == "assertIsNone" and len(call.args) >= 1:
        is_c, val = _eval_constant_expr(call.args[0], var_env)
        if is_c and val is not None:
            trivial_reasons.append(f"Constant assertIsNone({val!r}) failure without causal dependency")
    elif attr == "assertIsNotNone" and len(call.args) >= 1:
        is_c, val = _eval_constant_expr(call.args[0], var_env)
        if is_c and val is None:
            trivial_reasons.append("Constant assertIsNotNone(None) failure without causal dependency")


def _process_stmts_in_order(
    stmts: list[ast.stmt],
    var_env: dict[str, Any],
    dependent_vars: set[str],
    func_node: ast.AST,
    trivial_reasons: list[str],
) -> None:
    """文を実行順序 (Program Order) に沿って走査し、変数の最新状態を維持しながらアサーションを順次検証する (機能OOO.1 / PPP.1 / PPP.2)。"""
    imported_symbols: dict[str, str] = getattr(func_node, "_imported_symbols", {})
    imported_modules: dict[str, str] = getattr(func_node, "_imported_modules", {})
    var_origins: dict[str, Any] = getattr(func_node, "_var_origins", {})

    for stmt in stmts:
        # 1. 複合文の再帰走査
        if isinstance(stmt, ast.If):
            for call in [n for n in ast.walk(stmt.test) if isinstance(n, ast.Call) and _is_assert_call(n)]:
                _check_assert_call(call, var_env, dependent_vars, func_node, trivial_reasons)

            is_c, test_val = _eval_constant_expr(stmt.test, var_env)
            if is_c:
                if bool(test_val):
                    # 静的に True: body のみ実行
                    _process_stmts_in_order(stmt.body, var_env, dependent_vars, func_node, trivial_reasons)
                elif stmt.orelse:
                    # 静的に False: orelse のみ実行 (body は到達不能なので走査抑止: 機能PPP.2)
                    _process_stmts_in_order(stmt.orelse, var_env, dependent_vars, func_node, trivial_reasons)
            else:
                # 動的条件: 実行時まで確定しないため両分岐を走査
                body_env, body_deps = var_env.copy(), dependent_vars.copy()
                _process_stmts_in_order(stmt.body, body_env, body_deps, func_node, trivial_reasons)
                if stmt.orelse:
                    else_env, else_deps = var_env.copy(), dependent_vars.copy()
                    _process_stmts_in_order(stmt.orelse, else_env, else_deps, func_node, trivial_reasons)
                    dependent_vars.update(else_deps)
                dependent_vars.update(body_deps)

                # 分岐内で代入・変更された変数を不確定化（Merge & Uncertainty Protection: 機能PPP.1）
                # 分岐前の古い固定値が後続アサーション判定に誤って逆流・残存しないよう var_env から破棄し dependent_vars に昇格
                assigned_in_branch: set[str] = set()
                for item in stmt.body + stmt.orelse:
                    if isinstance(item, ast.Assign):
                        for t in item.targets:
                            assigned_in_branch.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
                    elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
                        assigned_in_branch.update(n.id for n in ast.walk(item.target) if isinstance(n, ast.Name))
                    else:
                        for n in ast.walk(item):
                            if isinstance(n, ast.Name) and isinstance(getattr(n, "ctx", None), ast.Store):
                                assigned_in_branch.add(n.id)

                for name in assigned_in_branch:
                    var_env.pop(name, None)
                    dependent_vars.add(name)
            continue

        if isinstance(stmt, ast.Try):
            try_env, try_deps = var_env.copy(), dependent_vars.copy()
            _process_stmts_in_order(stmt.body, try_env, try_deps, func_node, trivial_reasons)
            dependent_vars.update(try_deps)
            for handler in stmt.handlers:
                h_env, h_deps = var_env.copy(), dependent_vars.copy()
                _process_stmts_in_order(handler.body, h_env, h_deps, func_node, trivial_reasons)
                dependent_vars.update(h_deps)
            if stmt.orelse:
                o_env, o_deps = var_env.copy(), dependent_vars.copy()
                _process_stmts_in_order(stmt.orelse, o_env, o_deps, func_node, trivial_reasons)
                dependent_vars.update(o_deps)
            if stmt.finalbody:
                f_env, f_deps = var_env.copy(), dependent_vars.copy()
                _process_stmts_in_order(stmt.finalbody, f_env, f_deps, func_node, trivial_reasons)
                dependent_vars.update(f_deps)

            try_assigned: set[str] = set()
            for item in stmt.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        try_assigned.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
                elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
                    try_assigned.update(n.id for n in ast.walk(item.target) if isinstance(n, ast.Name))
            for name in try_assigned:
                var_env.pop(name, None)
                dependent_vars.add(name)
            continue

        if isinstance(stmt, ast.While):
            for call in [n for n in ast.walk(stmt.test) if isinstance(n, ast.Call) and _is_assert_call(n)]:
                _check_assert_call(call, var_env, dependent_vars, func_node, trivial_reasons)
            is_c, test_val = _eval_constant_expr(stmt.test, var_env)
            if is_c and not bool(test_val):
                # 静的に False: body は到達不能なので走査抑止 (機能PPP.2)
                if stmt.orelse:
                    _process_stmts_in_order(stmt.orelse, var_env, dependent_vars, func_node, trivial_reasons)
                continue

            _process_stmts_in_order(stmt.body, var_env, dependent_vars, func_node, trivial_reasons)
            if stmt.orelse:
                _process_stmts_in_order(stmt.orelse, var_env, dependent_vars, func_node, trivial_reasons)
            loop_assigned: set[str] = set()
            for item in stmt.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        loop_assigned.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
                elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
                    loop_assigned.update(n.id for n in ast.walk(item.target) if isinstance(n, ast.Name))
            for name in loop_assigned:
                var_env.pop(name, None)
                dependent_vars.add(name)
            continue

        if isinstance(stmt, ast.For):
            _process_stmts_in_order(stmt.body, var_env, dependent_vars, func_node, trivial_reasons)
            if stmt.orelse:
                _process_stmts_in_order(stmt.orelse, var_env, dependent_vars, func_node, trivial_reasons)
            for_assigned: set[str] = set()
            for_assigned.update(n.id for n in ast.walk(stmt.target) if isinstance(n, ast.Name))
            for item in stmt.body:
                if isinstance(item, ast.Assign):
                    for t in item.targets:
                        for_assigned.update(n.id for n in ast.walk(t) if isinstance(n, ast.Name))
                elif isinstance(item, (ast.AnnAssign, ast.AugAssign)):
                    for_assigned.update(n.id for n in ast.walk(item.target) if isinstance(n, ast.Name))
            for name in for_assigned:
                var_env.pop(name, None)
                dependent_vars.add(name)
            continue

        if isinstance(stmt, ast.With):
            for w_item in stmt.items:
                for call in [n for n in ast.walk(w_item.context_expr) if isinstance(n, ast.Call) and _is_assert_call(n)]:
                    _check_assert_call(call, var_env, dependent_vars, func_node, trivial_reasons)
            _process_stmts_in_order(stmt.body, var_env, dependent_vars, func_node, trivial_reasons)
            continue

        # 2. 単文内のアサーション呼出の評価（代入文等の場合も、代入実行前の環境で評価）
        for call in [n for n in ast.walk(stmt) if isinstance(n, ast.Call) and _is_assert_call(n)]:
            _check_assert_call(call, var_env, dependent_vars, func_node, trivial_reasons)

        # 3. 代入文 (Assign, AnnAssign, AugAssign) による環境の更新
        if isinstance(stmt, ast.Assign):
            rhs = stmt.value
            target_names = [n.id for t in stmt.targets for n in ast.walk(t) if isinstance(n, ast.Name)]
            origin = _resolve_expr_origin(rhs, imported_symbols, imported_modules, var_origins)
            if origin:
                for t in stmt.targets:
                    if (
                        isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id == "self"
                    ):
                        existing_attr = var_origins.setdefault(f"self.{t.attr}", set())
                        if isinstance(existing_attr, set):
                            existing_attr.add(origin)
                        else:
                            var_origins[f"self.{t.attr}"] = {existing_attr, origin}
                for name in target_names:
                    existing = var_origins.setdefault(name, set())
                    if isinstance(existing, set):
                        existing.add(origin)
                    else:
                        var_origins[name] = {existing, origin}
            val_has_dep = _has_causal_dependency(rhs, dependent_vars, imported_symbols, imported_modules, var_origins)
            if val_has_dep:
                for name in target_names:
                    dependent_vars.add(name)
                    var_env.pop(name, None)
            else:
                ok_const, const_val = _eval_constant_expr(rhs, var_env)
                if ok_const:
                    for name in target_names:
                        var_env[name] = const_val
                        dependent_vars.discard(name)
                else:
                    eff_origin = _get_effective_origin(target_names[0], var_origins) if target_names else origin
                    if eff_origin and eff_origin in _NON_PRODUCT_ORIGINS:
                        for name in target_names:
                            dependent_vars.discard(name)
                            var_env.pop(name, None)
                    else:
                        for name in target_names:
                            dependent_vars.add(name)
                            var_env.pop(name, None)

        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            rhs = stmt.value
            target_names = [n.id for n in ast.walk(stmt.target) if isinstance(n, ast.Name)]
            origin = _resolve_expr_origin(rhs, imported_symbols, imported_modules, var_origins)
            if origin:
                if (
                    isinstance(stmt.target, ast.Attribute)
                    and isinstance(stmt.target.value, ast.Name)
                    and stmt.target.value.id == "self"
                ):
                    existing_attr = var_origins.setdefault(f"self.{stmt.target.attr}", set())
                    if isinstance(existing_attr, set):
                        existing_attr.add(origin)
                    else:
                        var_origins[f"self.{stmt.target.attr}"] = {existing_attr, origin}
                for name in target_names:
                    existing = var_origins.setdefault(name, set())
                    if isinstance(existing, set):
                        existing.add(origin)
                    else:
                        var_origins[name] = {existing, origin}
            val_has_dep = _has_causal_dependency(rhs, dependent_vars, imported_symbols, imported_modules, var_origins)
            if val_has_dep:
                for name in target_names:
                    dependent_vars.add(name)
                    var_env.pop(name, None)
            else:
                ok_const, const_val = _eval_constant_expr(rhs, var_env)
                if ok_const:
                    for name in target_names:
                        var_env[name] = const_val
                        dependent_vars.discard(name)
                else:
                    eff_origin = _get_effective_origin(target_names[0], var_origins) if target_names else origin
                    if eff_origin and eff_origin in _NON_PRODUCT_ORIGINS:
                        for name in target_names:
                            dependent_vars.discard(name)
                            var_env.pop(name, None)
                    else:
                        for name in target_names:
                            dependent_vars.add(name)
                            var_env.pop(name, None)

        elif isinstance(stmt, ast.AugAssign):
            target_names = [n.id for n in ast.walk(stmt.target) if isinstance(n, ast.Name)]
            for name in target_names:
                dependent_vars.add(name)
                var_env.pop(name, None)


def check_trivial_red_ast(target: ast.AST | Path | str) -> tuple[bool, str]:
    """候補テストの AST を解析し、自己完結した固定値比較や因果関係のない固定値失敗等の
    偽陽性 Red (Trivial / Fake Red) を静的に検出する (機能MMM.3 / 機能NNN.2 / 機能OOO.1)。
    """
    if isinstance(target, ast.AST):
        tree = target
    elif isinstance(target, Path):
        try:
            tree = ast.parse(target.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            return False, ""
    elif isinstance(target, str):
        try:
            tree = ast.parse(target)
        except (SyntaxError, UnicodeDecodeError):
            return False, ""
    else:
        return False, ""

    imported_symbols: dict[str, str] = {}
    imported_modules: dict[str, str] = {}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.Import):
            for alias in stmt.names:
                root_pkg = alias.name.split(".")[0]
                alias_name = alias.asname or alias.name
                imported_modules[alias_name] = root_pkg
                imported_symbols[alias_name] = root_pkg
        elif isinstance(stmt, ast.ImportFrom):
            root_pkg = stmt.module.split(".")[0] if stmt.module else ""
            for alias in stmt.names:
                alias_name = alias.asname or alias.name
                imported_symbols[alias_name] = root_pkg
                imported_modules[alias_name] = root_pkg

    module_var_origins: dict[str, set[str]] = {}
    for stmt in ast.iter_child_nodes(tree):
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and not stmt.name.startswith("test"):
            fn_origs = _inspect_helper_fn_origins(stmt, imported_symbols, imported_modules, module_var_origins)
            if fn_origs:
                module_var_origins.setdefault(stmt.name, set()).update(fn_origs)
            else:
                module_var_origins.setdefault(stmt.name, set()).add("test_local_helper")
        elif isinstance(stmt, ast.Assign):
            origin = _resolve_expr_origin(stmt.value, imported_symbols, imported_modules, module_var_origins)
            if origin:
                for t in stmt.targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            module_var_origins.setdefault(n.id, set()).add(origin)
            elif isinstance(stmt.value, ast.Lambda):
                for t in stmt.targets:
                    for n in ast.walk(t):
                        if isinstance(n, ast.Name):
                            module_var_origins.setdefault(n.id, set()).add("test_local_helper")
        elif isinstance(stmt, ast.AnnAssign) and stmt.value is not None:
            origin = _resolve_expr_origin(stmt.value, imported_symbols, imported_modules, module_var_origins)
            if origin:
                for n in ast.walk(stmt.target):
                    if isinstance(n, ast.Name):
                        module_var_origins.setdefault(n.id, set()).add(origin)
            elif isinstance(stmt.value, ast.Lambda):
                for n in ast.walk(stmt.target):
                    if isinstance(n, ast.Name):
                        module_var_origins.setdefault(n.id, set()).add("test_local_helper")

    class_self_origins: dict[str, set[str]] = {}
    for stmt in ast.walk(tree):
        if isinstance(stmt, ast.ClassDef):
            for item in stmt.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if not item.name.startswith("test"):
                        fn_origs = _inspect_helper_fn_origins(item, imported_symbols, imported_modules, module_var_origins)
                        if fn_origs:
                            class_self_origins.setdefault(f"self.{item.name}", set()).update(fn_origs)
                            module_var_origins.setdefault(item.name, set()).update(fn_origs)
                        else:
                            module_var_origins.setdefault(item.name, set()).add("test_local_helper")
                    for s in ast.walk(item):
                        if isinstance(s, ast.Assign):
                            orig = _resolve_expr_origin(s.value, imported_symbols, imported_modules, module_var_origins)
                            if orig:
                                for t in s.targets:
                                    if (
                                        isinstance(t, ast.Attribute)
                                        and isinstance(t.value, ast.Name)
                                        and t.value.id == "self"
                                    ):
                                        class_self_origins.setdefault(f"self.{t.attr}", set()).add(orig)
                        elif (
                            isinstance(s, ast.AnnAssign)
                            and s.value is not None
                            and isinstance(s.target, ast.Attribute)
                            and isinstance(s.target.value, ast.Name)
                            and s.target.value.id == "self"
                        ):
                            orig = _resolve_expr_origin(s.value, imported_symbols, imported_modules, module_var_origins)
                            if orig:
                                class_self_origins.setdefault(f"self.{s.target.attr}", set()).add(orig)

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not node.name.startswith("test"):
            continue

        var_origins: dict[str, set[str]] = {k: set(v) for k, v in module_var_origins.items()}
        var_origins.update({k: set(v) for k, v in class_self_origins.items()})
        for sub_node in ast.walk(node):
            if isinstance(sub_node, (ast.FunctionDef, ast.AsyncFunctionDef)) and sub_node is not node:
                fn_origs = _inspect_helper_fn_origins(sub_node, imported_symbols, imported_modules, var_origins)
                if fn_origs:
                    var_origins.setdefault(sub_node.name, set()).update(fn_origs)
                else:
                    var_origins.setdefault(sub_node.name, set()).add("test_local_helper")

        changed = True
        iterations = 0
        while changed and iterations < 20:
            changed = False
            iterations += 1
            for sub_stmt in ast.walk(node):
                if isinstance(sub_stmt, ast.Assign):
                    origin = _resolve_expr_origin(sub_stmt.value, imported_symbols, imported_modules, var_origins)
                    if origin:
                        for t in sub_stmt.targets:
                            if (
                                isinstance(t, ast.Attribute)
                                and isinstance(t.value, ast.Name)
                                and t.value.id == "self"
                            ):
                                existing_attr = var_origins.setdefault(f"self.{t.attr}", set())
                                if origin not in existing_attr:
                                    existing_attr.add(origin)
                                    changed = True
                            for n in ast.walk(t):
                                if isinstance(n, ast.Name):
                                    existing = var_origins.setdefault(n.id, set())
                                    if origin not in existing:
                                        existing.add(origin)
                                        changed = True
                    elif isinstance(sub_stmt.value, ast.Lambda):
                        for t in sub_stmt.targets:
                            for n in ast.walk(t):
                                if isinstance(n, ast.Name):
                                    existing = var_origins.setdefault(n.id, set())
                                    if "test_local_helper" not in existing:
                                        existing.add("test_local_helper")
                                        changed = True
                elif isinstance(sub_stmt, ast.AnnAssign) and sub_stmt.value is not None:
                    origin = _resolve_expr_origin(sub_stmt.value, imported_symbols, imported_modules, var_origins)
                    if origin:
                        if (
                            isinstance(sub_stmt.target, ast.Attribute)
                            and isinstance(sub_stmt.target.value, ast.Name)
                            and sub_stmt.target.value.id == "self"
                        ):
                            existing_attr = var_origins.setdefault(f"self.{sub_stmt.target.attr}", set())
                            if origin not in existing_attr:
                                existing_attr.add(origin)
                                changed = True
                        for n in ast.walk(sub_stmt.target):
                            if isinstance(n, ast.Name):
                                existing = var_origins.setdefault(n.id, set())
                                if origin not in existing:
                                    existing.add(origin)
                                    changed = True
                    elif isinstance(sub_stmt.value, ast.Lambda):
                        for n in ast.walk(sub_stmt.target):
                            if isinstance(n, ast.Name):
                                existing = var_origins.setdefault(n.id, set())
                                if "test_local_helper" not in existing:
                                    existing.add("test_local_helper")
                                    changed = True

        node._imported_symbols = imported_symbols  # type: ignore[attr-defined,union-attr]
        node._imported_modules = imported_modules  # type: ignore[attr-defined,union-attr]
        node._var_origins = var_origins  # type: ignore[attr-defined,union-attr]

        var_env: dict[str, Any] = {}
        dependent_vars: set[str] = set()
        trivial_reasons: list[str] = []

        # 文を実行順序 (Program Order) に沿って走査し、変数の最新状態を維持しながらアサーションを順次検証
        _process_stmts_in_order(node.body, var_env, dependent_vars, node, trivial_reasons)

        if trivial_reasons:
            return (
                True,
                f"Test {node.name} contains trivial assertion without causal dependency: {'; '.join(trivial_reasons)}",
            )

    return False, ""


DEFAULT_VERIFY_TIMEOUT_SEC = 300
VERIFY_TIMEOUT_DIRECTIVE_RE = re.compile(r"^\s*#\s*auto-dev:\s*verify-timeout\s*=\s*(.*?)\s*$")
VERIFY_TIMEOUT_DIRECTIVE_SCAN_LINES = 20


def resolve_verify_timeout(cli_timeout: int | None, script_path: Path | None) -> tuple[int, str]:
    """検証スクリプトの制限時間（秒）とその出所を優先順位に従って解決する。

    優先順位:
    1. CLI の `--timeout` 指定（サブコマンド優先）
    2. 検証スクリプト先頭 20 行以内の `# auto-dev: verify-timeout=<SEC>`
    3. 既定値 300 秒
    """
    if cli_timeout is not None:
        if cli_timeout <= 0:
            raise ValueError(f"Invalid --timeout value '{cli_timeout}': must be a positive integer.")
        return cli_timeout, f"--timeout option ({cli_timeout}s)"

    if script_path is not None and script_path.exists() and script_path.is_file():
        lines = script_path.read_text(encoding="utf-8", errors="replace").splitlines()[
            :VERIFY_TIMEOUT_DIRECTIVE_SCAN_LINES
        ]
        for line in lines:
            match = VERIFY_TIMEOUT_DIRECTIVE_RE.match(line)
            if not match:
                continue
            raw_val = match.group(1).strip()
            if not re.fullmatch(r"[0-9]+", raw_val) or int(raw_val) <= 0:
                raise ValueError(
                    f"Invalid '# auto-dev: verify-timeout=' value '{raw_val}' in {script_path}: "
                    "must be a positive integer."
                )
            sec = int(raw_val)
            return sec, f"{script_path.name} directive (# auto-dev: verify-timeout={sec})"

    return DEFAULT_VERIFY_TIMEOUT_SEC, f"default ({DEFAULT_VERIFY_TIMEOUT_SEC}s)"


DEFAULT_RED_FIRST_MODE = "warn"
RED_FIRST_MODES = frozenset({"off", "warn", "enforce"})
RED_FIRST_DIRECTIVE_RE = re.compile(r"^\s*#\s*auto-dev:\s*red-first\s*=\s*(.*?)\s*$")
RED_FIRST_FILE_TIMEOUT_SEC = 120
_RED_FIRST_CONFIRMED_REASONS = frozenset(
    {"GENUINE_RED", "IMPORT_ERROR", "UNHANDLED_EXCEPTION", "SETUP_ERROR"}
)
_RED_FIRST_SANDBOX_REASONS = frozenset({"SANDBOX_UNAVAILABLE", "ROOT_EXECUTION_FORBIDDEN"})


def resolve_red_first_mode(script_path: Path | None) -> str:
    """検証スクリプト先頭 20 行以内の `# auto-dev: red-first=<off|warn|enforce>` を解決する。"""
    if script_path is not None and script_path.exists() and script_path.is_file():
        lines = script_path.read_text(encoding="utf-8", errors="replace").splitlines()[
            :VERIFY_TIMEOUT_DIRECTIVE_SCAN_LINES
        ]
        for line in lines:
            match = RED_FIRST_DIRECTIVE_RE.match(line)
            if not match:
                continue
            raw_val = match.group(1).strip()
            if raw_val not in RED_FIRST_MODES:
                raise ValueError(
                    f"Invalid '# auto-dev: red-first=' value '{raw_val}' in {script_path}: "
                    "must be one of off, warn, enforce."
                )
            return raw_val
    return DEFAULT_RED_FIRST_MODE


def is_red_first_task(title: str) -> bool:
    """タスクの題名が Red-First の対象（`Red-First` または `Red:` を含む）か判定する。"""
    return "Red-First" in title or "Red:" in title


def is_target_python_test_file(rel_path: str) -> bool:
    """GATE-16 の対象テストファイル（`tests/immunity/` を除く `test_*.py` / `*_test.py`）か判定する。"""
    posix_path = PurePosixPath(rel_path)
    parts = posix_path.parts
    if len(parts) >= 2 and parts[0] == "tests" and parts[1] == "immunity":
        return False
    name = posix_path.name
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py")


def find_changed_test_files(repo_dir: Path, checkpoint: str) -> list[str]:
    """チェックポイントから作業ツリーまでに追加・変更された対象 Python テストファイルを返す。"""
    diff_proc = subprocess.run(
        [
            "git",
            "-c",
            "core.quotePath=false",
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            "--diff-filter=ACM",
            checkpoint,
        ],
        cwd=repo_dir,
        capture_output=True,
        check=False,
    )
    if diff_proc.returncode != 0:
        err = diff_proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git diff failed while finding changed test files: {err}")

    untracked_proc = subprocess.run(
        ["git", "-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard", "-z"],
        cwd=repo_dir,
        capture_output=True,
        check=False,
    )
    if untracked_proc.returncode != 0:
        err = untracked_proc.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"git ls-files failed while finding changed test files: {err}")

    candidates: set[str] = set()
    for raw_output in (diff_proc.stdout, untracked_proc.stdout):
        for raw_entry in raw_output.split(b"\x00"):
            if not raw_entry:
                continue
            rel_path = raw_entry.decode("utf-8", errors="replace")
            if is_target_python_test_file(rel_path) and (repo_dir / rel_path).is_file():
                candidates.add(rel_path)
    return sorted(candidates)


class DeterministicVerifier:
    """ローカル検証スクリプトの実行と判定を担当。"""

    def run_verification(
        self,
        script_path: Path,
        timeout_sec: int = DEFAULT_VERIFY_TIMEOUT_SEC,
        cwd: Path | None = None,
        timeout_source: str | None = None,
    ) -> tuple[int, str]:
        """検証スクリプトを実行し、(exit_code, output) を返す。"""
        if not script_path.exists():
            return 1, f"Verification script not found: {script_path}"

        working_dir = cwd or script_path.parent
        try:
            proc = subprocess.run(
                ["bash", str(script_path)],
                cwd=working_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
            output = proc.stdout + "\n" + proc.stderr
            return proc.returncode, output.strip()
        except subprocess.TimeoutExpired as e:
            source_label = timeout_source or f"configured ({timeout_sec}s)"
            return 124, (
                f"Verification timed out after {timeout_sec}s: {e}\n"
                f"Timeout source: {source_label}\n"
                f"To increase the timeout, pass --timeout <SEC> (e.g. `auto-dev verify-step --timeout <SEC> ...`) "
                f"or set `# auto-dev: verify-timeout=<SEC>` in the first {VERIFY_TIMEOUT_DIRECTIVE_SCAN_LINES} lines "
                f"of {script_path.name}."
            )
        except Exception as e:  # noqa: BLE001
            return 1, f"Failed to execute verification script: {e}"


@dataclass
class RedVerificationResult:
    """再現テスト検証結果。"""

    is_reproduced: bool  # 真正な Red (アサーション失敗等) の場合のみ True
    exit_code: int
    reason: str  # GENUINE_RED, ALREADY_GREEN, SYNTAX_ERROR, IMPORT_ERROR, NO_TESTS_FOUND, TIMEOUT, EXECUTION_ERROR
    output: str = ""
    reason_detail: str = ""
    tests_run: int = 0
    failures: int = 0
    errors: int = 0
    process_limit_status: str = ""


class CommitCheckoutError(RuntimeError):
    """指定されたコミットのチェックアウトに失敗した際のエラー (Fail-Closed) (III.1)。"""


_ProbeCacheKey = tuple[str, int, tuple[int, int] | None]
_SANDBOX_PROBE_CACHE: dict[_ProbeCacheKey, tuple[str | None, str]] = {}
_LINUX_PROC_DIR = Path("/proc")


def _reset_sandbox_backend_cache() -> None:
    """プロセス内のサンドボックス実働確認キャッシュを初期化する。"""
    _SANDBOX_PROBE_CACHE.clear()


def _count_linux_user_threads(uid: int, proc_dir: Path | None = None) -> int | None:
    """Linux の /proc から指定 UID（実 UID）が持つスレッド（タスク）総数を数える (SBX-6)。"""
    target_proc = proc_dir if proc_dir is not None else _LINUX_PROC_DIR
    if not target_proc.is_dir():
        return None
    total_threads = 0
    matched_any = False
    try:
        with os.scandir(target_proc) as entries:
            for entry in entries:
                if not entry.name.isdigit():
                    continue
                pid_dir = Path(entry.path)
                try:
                    status_text = (pid_dir / "status").read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                real_uid: int | None = None
                threads_from_status: int | None = None
                for line in status_text.splitlines():
                    if line.startswith("Uid:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            with contextlib.suppress(ValueError):
                                real_uid = int(parts[1])
                    elif line.startswith("Threads:"):
                        parts = line.split()
                        if len(parts) >= 2:
                            with contextlib.suppress(ValueError):
                                threads_from_status = int(parts[1])
                if real_uid != uid:
                    continue
                matched_any = True
                task_count = 0
                task_dir = pid_dir / "task"
                try:
                    with os.scandir(task_dir) as task_entries:
                        task_count = sum(1 for t in task_entries if t.name.isdigit())
                except OSError:
                    task_count = 0
                if task_count > 0:
                    total_threads += task_count
                elif threads_from_status is not None and threads_from_status > 0:
                    total_threads += threads_from_status
                else:
                    total_threads += 1
    except OSError:
        return None
    return total_threads if matched_any else None


def _run_sandbox_probe(
    cmd: list[str],
    timeout_sec: int = 10,
    preexec_fn: Callable[[], None] | None = None,
) -> tuple[bool, str]:
    """最小コマンドをサンドボックス内で 1 回実行し、(起動成功, 失敗詳細) を返す。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=max(1, timeout_sec),
            preexec_fn=preexec_fn,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, str(exc)
    if proc.returncode == 0:
        return True, ""
    detail = (proc.stderr or proc.stdout or f"exit code {proc.returncode}").strip()
    return False, detail


def _format_sandbox_unavailable_message(
    platform: str,
    *,
    binary_missing: bool = False,
    probe_error: str = "",
) -> str:
    """OS ごとの対処を含む SANDBOX_UNAVAILABLE メッセージを生成する。"""
    if platform == "darwin":
        if binary_missing:
            return (
                "SANDBOX_UNAVAILABLE: macOS 'sandbox-exec' was not found on PATH; "
                "cannot initialize OS sandbox isolation (Fail-Closed)."
            )
        reason_part = f" ({probe_error})" if probe_error else ""
        return (
            f"SANDBOX_UNAVAILABLE: macOS 'sandbox-exec' cannot be used or failed to launch{reason_part}; "
            "cannot run candidate tests without OS sandbox isolation (Fail-Closed)."
        )
    if platform.startswith("linux"):
        if binary_missing:
            return (
                "SANDBOX_UNAVAILABLE: 'bwrap' (bubblewrap) was not found on Linux. "
                "Install bubblewrap (e.g. 'sudo apt-get install -y bubblewrap') and retry."
            )
        reason_part = f" ({probe_error})" if probe_error else ""
        return (
            f"SANDBOX_UNAVAILABLE: 'bwrap' is installed on Linux but failed to launch a sandbox{reason_part}. "
            "On Ubuntu 23.10+, AppArmor restricts unprivileged user namespaces; "
            "install 'apparmor-profiles' and load the 'bwrap-userns-restrict' profile "
            "(e.g. 'sudo install -m 0644 /usr/share/apparmor/extra-profiles/bwrap-userns-restrict "
            "/etc/apparmor.d/bwrap-userns-restrict && sudo apparmor_parser -r /etc/apparmor.d/bwrap-userns-restrict')."
        )
    return (
        f"SANDBOX_UNAVAILABLE: OS sandbox is not supported on platform '{platform}' "
        "(requires sandbox-exec on macOS or bwrap on Linux)."
    )


def _macos_symlink_traversal_rules(target_path: Path) -> list[str]:
    """Build minimal literal Seatbelt rules to traverse symlinks and parent dirs (SBX-7)."""
    rules: list[str] = []
    seen_paths: set[str] = set()
    queue: list[Path] = [Path(os.path.abspath(str(target_path)))]
    hops = 0

    while queue and hops < 40:
        hops += 1
        current_target = queue.pop(0)
        norm_str = os.path.normpath(str(current_target))
        if norm_str in seen_paths:
            continue
        seen_paths.add(norm_str)

        parts = Path(norm_str).parts
        if not parts or parts[0] != "/":
            continue

        curr = Path("/")
        for idx, part in enumerate(parts[1:], start=1):
            curr = curr / part
            curr_str = str(curr)
            try:
                is_link = curr.is_symlink()
            except OSError:
                is_link = False

            if is_link:
                rules.append(f'(allow file-read* (literal "{curr_str}"))')
                try:
                    raw_link = Path(os.readlink(curr))
                except OSError:
                    break
                next_base = raw_link if raw_link.is_absolute() else (curr.parent / raw_link)
                remaining = parts[idx + 1 :]
                next_path = next_base.joinpath(*remaining) if remaining else next_base
                queue.append(Path(os.path.normpath(str(next_path))))
                break
            elif idx == len(parts) - 1:
                rules.append(f'(allow file-read* (literal "{curr_str}"))')
            else:
                rules.append(f'(allow file-read-metadata (literal "{curr_str}"))')

    return list(dict.fromkeys(rules))


class RedTestVerifier:
    """監査役や外部から提示されたテスト候補が、現在のコードベースで確実に Red になるかを判定するハーネス。"""

    SECRET_ENV_PATTERN = re.compile(
        r"(TOKEN|SECRET|KEY|AUTH|CREDENTIAL|PASSWORD|PASS|API|PRIVATE)", re.IGNORECASE
    )
    DANGEROUS_ENV_PREFIXES = (
        "LD_",
        "DYLD_",
        "PYTHON",
        "GIT_",
    )

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()

    def verify_candidate_test(
        self, test_code: str, timeout_sec: int = 30, commit: str | None = None
    ) -> RedVerificationResult:
        """テストコード文字列を検証する。"""
        # 1. 構文チェック (SyntaxError の即時検出)
        try:
            tree = ast.parse(test_code)
        except SyntaxError as e:
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="SYNTAX_ERROR",
                output=str(e),
                reason_detail=f"SyntaxError: {e}",
            )

        # 2. トップレベル AST 検査 (モンキーパッチ・実行文の遮断)
        if not validate_test_ast(tree):
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="TOP_LEVEL_EXEC_FORBIDDEN",
                output="Top-level executable statements, monkeypatching, or forbidden module hooks detected in candidate test.",
                reason_detail="Candidate test contains top-level executable statements or forbidden hooks (AST validation failed).",
            )

        # 3. テストケースの存在確認
        has_test_class = any(
            isinstance(node, ast.ClassDef)
            and any(
                (isinstance(base, ast.Name) and "TestCase" in base.id)
                or (isinstance(base, ast.Attribute) and base.attr == "TestCase")
                for base in node.bases
            )
            for node in ast.walk(tree)
        )
        has_test_func = any(
            isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            for node in ast.walk(tree)
        )
        if not (has_test_class or has_test_func):
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=0,
                reason="NO_TESTS_FOUND",
                output="No TestCase classes or test_* functions found in candidate code.",
                reason_detail="No tests found",
            )

        # 3. 独立した一時クローン環境を作成して実行 (Physical Clone Isolation)
        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir) / "isolated_repo"
            try:
                self._create_isolated_clone(clone_path, commit=commit)
            except CommitCheckoutError as e:
                return RedVerificationResult(
                    is_reproduced=False,
                    exit_code=1,
                    reason="COMMIT_CHECKOUT_FAILED",
                    output=f"COMMIT_CHECKOUT_FAILED: {e}",
                    reason_detail=str(e),
                )
            test_file = clone_path / "test_candidate.py"
            test_file.write_text(test_code, encoding="utf-8")
            return self._execute_test(
                test_file, str(clone_path), timeout_sec, sandbox_dir=clone_path
            )

    def _create_isolated_clone(self, target_dir: Path, commit: str | None = None) -> Path:
        """元リポジトリから隔離されたクリーンな一時クローン（作業コピー）を作成する。
        Git リポジトリの場合は git clone --local --no-hardlinks で物理コピーを作成し、
        非Gitリポジトリの場合は shutil.copytree で複製する。
        commit が指定された場合はそのコミットにチェックアウト（デタッチドHEAD）し、未コミット変更は同期しない (HHH.1)。
        commit 指定時のクローン・チェックアウト失敗時は、現行ツリーへのフォールバックを禁止して CommitCheckoutError を送出する (III.1)。
        """
        resolved_repo = self.repo_dir.resolve()
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        is_git_repo = (resolved_repo / ".git").is_dir()
        cloned_ok = False
        if is_git_repo:
            try:
                res = subprocess.run(
                    [
                        "git",
                        "clone",
                        "--local",
                        "--no-hardlinks",
                        str(resolved_repo),
                        str(target_dir),
                    ],
                    capture_output=True,
                    check=False,
                )
                if res.returncode == 0:
                    cloned_ok = True
                    if commit:
                        chk = subprocess.run(
                            ["git", "checkout", "--detach", commit],
                            cwd=target_dir,
                            capture_output=True,
                            check=False,
                        )
                        if chk.returncode != 0:
                            cloned_ok = False
                    else:
                        # 未コミットのファイル（ステージ済み・未追跡）も一時クローンへコピー
                        self._sync_uncommitted_files(resolved_repo, target_dir)
            except (OSError, subprocess.SubprocessError):
                cloned_ok = False
        elif commit is not None:
            raise CommitCheckoutError(
                f"Cannot check out commit '{commit}': Repository is not a git repository"
            )

        if not cloned_ok:
            if commit is not None:
                raise CommitCheckoutError(
                    f"Failed to check out requested commit '{commit}' into isolated clone"
                )
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            shutil.copytree(
                resolved_repo,
                target_dir,
                dirs_exist_ok=True,
                symlinks=False,
                ignore=shutil.ignore_patterns(".git", ".dev/bundles", "__pycache__"),
            )

        return target_dir

    def _sync_uncommitted_files(self, source_repo: Path, target_repo: Path) -> None:
        """元リポジトリの未コミットの変更・新規ファイルを一時クローンへ同期する。"""
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=source_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        rel_path = parts[1]
                        src_file = source_repo / rel_path
                        tgt_file = target_repo / rel_path
                        if src_file.is_file() and not src_file.is_symlink():
                            tgt_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, tgt_file)

    def verify_candidate_test_file(
        self, test_file_path: Path, timeout_sec: int = 30
    ) -> RedVerificationResult:
        """ファイルパスからテストコードを読み出して検証する。"""
        if not test_file_path.exists():
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="EXECUTION_ERROR",
                output=f"Test file not found: {test_file_path}",
                reason_detail=f"File not found: {test_file_path}",
            )
        code = test_file_path.read_text(encoding="utf-8")
        return self.verify_candidate_test(code, timeout_sec=timeout_sec)

    def verify_red_first_test(
        self,
        rel_path: str,
        test_code: str,
        timeout_sec: int = RED_FIRST_FILE_TIMEOUT_SEC,
        commit: str | None = None,
    ) -> RedVerificationResult:
        """GATE-17: 変更されたテストファイルをチェックポイント時点のコードに対してサンドボックス実行する。"""
        try:
            ast.parse(test_code)
        except SyntaxError as e:
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="SYNTAX_ERROR",
                output=str(e),
                reason_detail=f"SyntaxError: {e}",
            )

        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir) / "isolated_repo"
            try:
                self._create_isolated_clone(clone_path, commit=commit)
            except CommitCheckoutError as e:
                return RedVerificationResult(
                    is_reproduced=False,
                    exit_code=1,
                    reason="COMMIT_CHECKOUT_FAILED",
                    output=f"COMMIT_CHECKOUT_FAILED: {e}",
                    reason_detail=str(e),
                )
            test_file = clone_path / rel_path
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(test_code, encoding="utf-8")
            return self._execute_test(
                test_file,
                str(test_file.parent),
                timeout_sec,
                sandbox_dir=clone_path,
                tracer_root=str(clone_path),
            )

    RUNNER_CODE = """
import json
import os
import sys
import unittest
from pathlib import Path

search_dir = sys.argv[1]
pattern = sys.argv[2]
protected_repo = sys.argv[3] if len(sys.argv) > 3 else ""
tracer_root = sys.argv[4] if len(sys.argv) > 4 else search_dir

if protected_repo:
    try:
        resolved_protected = str(Path(protected_repo).resolve())
    except Exception:
        resolved_protected = protected_repo

    def _security_audit_hook(event, args):
        # 1. chmod / chown
        if event in ("os.chmod", "os.chown"):
            if args and args[0]:
                try:
                    p = str(Path(args[0]).resolve())
                except Exception:
                    p = str(args[0])
                if p == resolved_protected or p.startswith(resolved_protected + os.sep):
                    raise PermissionError(f"Security audit hook: '{event}' on protected repo '{p}' is forbidden.")
        # 2. write open
        elif event == "open":
            if args and len(args) >= 2:
                path_arg, mode_arg = args[0], args[1]
                flags_arg = args[2] if len(args) > 2 else None
                is_write = (mode_arg and any(m in mode_arg for m in ("w", "a", "x", "+"))) or (
                    flags_arg is not None and bool(flags_arg & (os.O_WRONLY | os.O_RDWR))
                )
                if is_write and path_arg:
                    try:
                        p = str(Path(path_arg).resolve())
                    except Exception:
                        p = str(path_arg)
                    if p == resolved_protected or p.startswith(resolved_protected + os.sep):
                        raise PermissionError(f"Security audit hook: write to protected repo '{p}' is forbidden.")
        # 3. delete / rename / mkdir
        elif event in ("os.remove", "os.unlink", "os.rmdir", "os.mkdir"):
            if args and args[0]:
                try:
                    p = str(Path(args[0]).resolve())
                except Exception:
                    p = str(args[0])
                if p == resolved_protected or p.startswith(resolved_protected + os.sep):
                    raise PermissionError(f"Security audit hook: '{event}' on protected repo '{p}' is forbidden.")
        elif event == "os.rename":
            for arg in args[:2]:
                if arg:
                    try:
                        p = str(Path(arg).resolve())
                    except Exception:
                        p = str(arg)
                    if p == resolved_protected or p.startswith(resolved_protected + os.sep):
                        raise PermissionError(f"Security audit hook: 'os.rename' involving protected repo '{p}' is forbidden.")

    sys.addaudithook(_security_audit_hook)

print("===AUTO_DEV_RUNNER_STARTED===", flush=True)

loader = unittest.defaultTestLoader
try:
    suite = loader.discover(start_dir=search_dir, pattern=pattern)
except Exception as e:
    data = {
        "tests_run": 0,
        "failures": [],
        "errors": [{"test": "loader", "err": str(e)}],
    }
    print("===AUTO_DEV_TEST_RESULT===")
    print(json.dumps(data))
    sys.exit(1)

result = unittest.TestResult()

executed_product_code = False
try:
    resolved_search = str(Path(tracer_root).resolve())
except Exception:
    resolved_search = tracer_root

def _product_code_tracer(frame, event, arg):
    global executed_product_code
    if event == "call":
        fn = frame.f_code.co_filename
        if fn and fn.startswith(resolved_search):
            try:
                rel = os.path.relpath(fn, resolved_search)
                parts = Path(rel).parts
                name = Path(rel).name
                is_test_file = name.startswith("test_") or name.endswith("_test.py") or "tests" in parts
                if not is_test_file:
                    executed_product_code = True
            except Exception:
                pass
    return _product_code_tracer

sys.settrace(_product_code_tracer)
try:
    suite.run(result)
finally:
    sys.settrace(None)

data = {
    "tests_run": result.testsRun,
    "failures": [{"test": str(test), "err": err} for test, err in result.failures],
    "errors": [{"test": str(test), "err": err} for test, err in result.errors],
    "executed_product_code": executed_product_code,
}
print("===AUTO_DEV_TEST_RESULT===")
print(json.dumps(data))
sys.exit(0 if (result.testsRun > 0 and len(result.failures) == 0 and len(result.errors) == 0) else 1)
"""

    @contextlib.contextmanager
    def _lockdown_repo(self, repo_dir: Path) -> Iterator[None]:
        """候補テスト実行中、対象リポジトリ配下の全ファイルおよびディレクトリから
        書き込み権限を一時的に剥奪（a-w）して OS 権限レベルで読取専用化し、
        さらに macOS では User Immutable Flag (chflags uchg) を設定して、
        テスト終了後に元のパーミッションおよびフラグを確実に復元する。
        """
        original_modes: list[tuple[Path, int]] = []
        resolved_repo = repo_dir.resolve()
        write_mask = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
        has_chflags = sys.platform == "darwin" and shutil.which("chflags") is not None

        items: list[tuple[Path, int, bool]] = []
        try:
            if resolved_repo.exists():
                for root, dirs, files in os.walk(resolved_repo):
                    root_path = Path(root)
                    for f in files:
                        p = root_path / f
                        try:
                            st = p.lstat()
                            if not stat.S_ISLNK(st.st_mode):
                                items.append((p, st.st_mode, False))
                        except OSError:
                            pass
                    for d in dirs:
                        p = root_path / d
                        try:
                            st = p.lstat()
                            if not stat.S_ISLNK(st.st_mode):
                                items.append((p, st.st_mode, True))
                        except OSError:
                            pass
                try:
                    st_repo = resolved_repo.lstat()
                    items.append((resolved_repo, st_repo.st_mode, True))
                except OSError:
                    pass

                # ファイルの書き込み権限を先に剥奪
                for p, mode, is_dir in items:
                    if not is_dir:
                        try:
                            p.chmod(mode & ~write_mask)
                            original_modes.append((p, mode))
                        except OSError:
                            pass

                # ディレクトリの書き込み権限を剥奪 (読取・実行権は維持)
                for p, mode, is_dir in items:
                    if is_dir:
                        try:
                            p.chmod(mode & ~write_mask)
                            original_modes.append((p, mode))
                        except OSError:
                            pass

                # macOS の場合、カーネルレベルで chmod 権限復元を物理阻止する uchg フラグを設定
                if has_chflags:
                    with contextlib.suppress(OSError, subprocess.SubprocessError):
                        subprocess.run(
                            ["chflags", "-R", "uchg", str(resolved_repo)],
                            capture_output=True,
                            check=False,
                        )

            yield

        finally:
            # macOS の uchg フラグを解除
            if has_chflags:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(
                        ["chflags", "-R", "nouchg", str(resolved_repo)],
                        capture_output=True,
                        check=False,
                    )

            # 復元: ディレクトリ (親) から先に元の書き込み権限を復元
            for p, mode in reversed(original_modes):
                try:
                    if p.exists() and p.is_dir():
                        p.chmod(mode)
                except OSError:
                    pass
            # 次にファイルのパーミッションを復元
            for p, mode in original_modes:
                try:
                    if p.exists() and not p.is_dir():
                        p.chmod(mode)
                except OSError:
                    pass

    def _compute_nproc_limit(self, margin: int = 50) -> tuple[tuple[int, int] | None, str]:
        """現在のユーザープロセス/スレッド数に基づいて RLIMIT_NPROC の (soft, hard) 上限を計算する (JJJ.3, SBX-6)。

        RLIMIT_NPROC はプロセス単位ではなくユーザー(UID)全体の上限であり、Linux ではスレッド（タスク）
        単位で数えられるため、Linux ではスレッド総数 + マージンで設定する。取得に失敗した場合は制限を設定せずステータスを返す。
        """
        if not hasattr(resource, "RLIMIT_NPROC") or os.name != "posix":
            return None, "unsupported"

        try:
            _cur_soft, cur_hard = resource.getrlimit(resource.RLIMIT_NPROC)
            cur_count: int | None = None
            if sys.platform.startswith("linux"):
                cur_count = _count_linux_user_threads(os.getuid())
            if cur_count is None:
                ps_cmd: list[str]
                if sys.platform.startswith("linux"):
                    ps_cmd = ["ps", "-L", "-u", str(os.getuid()), "-o", "lwp="]
                else:
                    ps_cmd = ["ps", "-u", str(os.getuid()), "-o", "pid="]
                res = subprocess.run(
                    ps_cmd,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if res.returncode != 0:
                    return None, f"ps_failed(code={res.returncode})"
                lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
                cur_count = len(lines)
            target = cur_count + margin
            if cur_hard != resource.RLIM_INFINITY and target > cur_hard:
                target = cur_hard
            return (target, cur_hard), f"applied: limit={target} (cur_count={cur_count}, margin={margin})"
        except Exception as e:  # noqa: BLE001
            return None, f"error: {e}"

    def _apply_resource_limits(
        self,
        timeout_sec: int,
        nproc_limits: tuple[int, int] | None = None,
    ) -> None:
        """Apply OS-level execution resource boundaries to child process (POSIX)."""
        with contextlib.suppress(Exception):
            # 1. ファイル出力上限 (巨大ファイル作成によるディスク浪費・DoS防止: 10MB)
            if hasattr(resource, "RLIMIT_FSIZE"):
                max_fsize = 10 * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_FSIZE, (max_fsize, max_fsize))

            # 2. 仮想メモリ上限 (過剰メモリ消費・OOM爆弾の防止: 1GB)
            if hasattr(resource, "RLIMIT_AS"):
                max_as = 1024 * 1024 * 1024
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_AS, (max_as, max_as))

            # 3. CPU 時間上限 (タイムアウト + マージン)
            if hasattr(resource, "RLIMIT_CPU"):
                cpu_limit = max(1, timeout_sec + 5)
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))

            # 4. プロセス生成数上限 (RLIMIT_NPROC: 動的計算された上限を設定) (JJJ.3)
            if nproc_limits and hasattr(resource, "RLIMIT_NPROC"):
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_NPROC, nproc_limits)

    def _probe_sandbox_backend(
        self,
        timeout_sec: int = 30,
        nproc_limits: tuple[int, int] | None = None,
    ) -> tuple[str | None, str]:
        """利用可能な OS サンドボックス機構を最小コマンドの実働で確認し、渡した値の組ごとに結果をキャッシュする (SBX-3, SBX-6)。"""
        cache_key: _ProbeCacheKey = (sys.platform, timeout_sec, nproc_limits)
        cached = _SANDBOX_PROBE_CACHE.get(cache_key)
        if cached is not None:
            return cached

        preexec: Callable[[], None] | None = (
            (lambda: self._apply_resource_limits(timeout_sec, nproc_limits))
            if os.name == "posix"
            else None
        )

        res: tuple[str | None, str]
        if sys.platform == "darwin":
            if not shutil.which("sandbox-exec"):
                res = (
                    None,
                    _format_sandbox_unavailable_message(sys.platform, binary_missing=True),
                )
            else:
                ok, err = _run_sandbox_probe(
                    ["sandbox-exec", "-p", "(version 1)(allow default)(deny network*)", "true"],
                    timeout_sec=timeout_sec,
                    preexec_fn=preexec,
                )
                res = (
                    ("sandbox-exec", "")
                    if ok
                    else (None, _format_sandbox_unavailable_message(sys.platform, probe_error=err))
                )
        elif sys.platform.startswith("linux"):
            if not shutil.which("bwrap"):
                res = (
                    None,
                    _format_sandbox_unavailable_message(sys.platform, binary_missing=True),
                )
            else:
                ok, err = _run_sandbox_probe(
                    ["bwrap", "--unshare-net", "--ro-bind", "/", "/", "true"],
                    timeout_sec=timeout_sec,
                    preexec_fn=preexec,
                )
                res = (
                    ("bwrap", "")
                    if ok
                    else (None, _format_sandbox_unavailable_message(sys.platform, probe_error=err))
                )
        else:
            res = (None, _format_sandbox_unavailable_message(sys.platform))

        _SANDBOX_PROBE_CACHE[cache_key] = res
        return res

    def _get_sandbox_backend(
        self,
        timeout_sec: int = 30,
        nproc_limits: tuple[int, int] | None = None,
    ) -> str | None:
        """利用可能な OS サンドボックス機構を検出する (macOS: sandbox-exec, Linux: bwrap)。"""
        backend, _ = self._probe_sandbox_backend(timeout_sec=timeout_sec, nproc_limits=nproc_limits)
        return backend

    def _get_sandbox_unavailable_detail(
        self,
        timeout_sec: int = 30,
        nproc_limits: tuple[int, int] | None = None,
    ) -> str:
        """SANDBOX_UNAVAILABLE 時の OS ごとの対処メッセージを返す。"""
        cache_key: _ProbeCacheKey = (sys.platform, timeout_sec, nproc_limits)
        cached = _SANDBOX_PROBE_CACHE.get(cache_key)
        if cached is not None and cached[0] is None and cached[1]:
            return cached[1]
        return _format_sandbox_unavailable_message(sys.platform)

    def _capture_repo_snapshot(self) -> dict[str, Any]:
        """元リポジトリの改ざん検知用スナップショットを取得する (JJJ.2)。"""
        resolved_repo = self.repo_dir.resolve()
        is_git = (resolved_repo / ".git").is_dir()
        if is_git:
            # 1. HEAD ハッシュ
            proc_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=resolved_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            head_commit = proc_head.stdout.strip() if proc_head.returncode == 0 else ""

            # 2. ステータス (追跡・未追跡・無視ファイルすべて)
            proc_status = subprocess.run(
                ["git", "status", "--porcelain", "--ignored", "--untracked-files=all"],
                cwd=resolved_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            status_text = proc_status.stdout if proc_status.returncode == 0 else ""

            # 2.1 未追跡・無視ファイルの内容ハッシュ・種別記録 (KKK.2 / Sol氏 Phase 50 P1指摘)
            untracked_hashes: dict[str, str] = {}
            for line in status_text.splitlines():
                if line.startswith(("?? ", "!! ")):
                    rel_path = line[3:].strip()
                    if rel_path.startswith('"') and rel_path.endswith('"'):
                        rel_path = rel_path[1:-1]
                    p = resolved_repo / rel_path
                    try:
                        if p.is_symlink():
                            target = os.readlink(p)
                            untracked_hashes[rel_path] = f"symlink:{target}"
                        elif p.is_file():
                            untracked_hashes[rel_path] = f"file:{hashlib.sha256(p.read_bytes()).hexdigest()}"
                        elif p.is_dir():
                            untracked_hashes[rel_path] = "dir"
                        else:
                            untracked_hashes[rel_path] = "missing"
                    except OSError as e:
                        untracked_hashes[rel_path] = f"error:{e}"

            # 3. diff ハッシュ
            diff_args = ["git", "diff", "HEAD", "--binary"] if head_commit else ["git", "diff", "--binary"]
            proc_diff = subprocess.run(
                diff_args,
                cwd=resolved_repo,
                capture_output=True,
                check=False,
            )
            diff_hash = hashlib.sha256(proc_diff.stdout).hexdigest() if proc_diff.returncode == 0 else ""

            return {
                "type": "git",
                "head": head_commit,
                "status": status_text,
                "untracked_hashes": untracked_hashes,
                "diff_hash": diff_hash,
            }

        # 非 Git リポジトリ: ファイルツリー走査
        file_hashes: dict[str, str] = {}
        for p in sorted(resolved_repo.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(resolved_repo))
                with contextlib.suppress(OSError):
                    file_hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        return {
            "type": "tree",
            "files": file_hashes,
        }

    def _compare_repo_snapshots(
        self, before: dict[str, Any], after: dict[str, Any]
    ) -> list[str]:
        """実行前後のスナップショットを比較し、改ざんされた相対パス一覧を返す (JJJ.2)。"""
        if before.get("type") == "git" and after.get("type") == "git":
            if before == after:
                return []
            changed_paths: list[str] = []
            if before.get("head") != after.get("head"):
                changed_paths.append(".git/HEAD")
            before_lines = set(before.get("status", "").splitlines())
            after_lines = set(after.get("status", "").splitlines())
            for line in after_lines - before_lines:
                parts = line.strip().split(maxsplit=1)
                changed_paths.append(parts[1] if len(parts) == 2 else line.strip())
            for line in before_lines - after_lines:
                parts = line.strip().split(maxsplit=1)
                changed_paths.append(parts[1] if len(parts) == 2 else line.strip())

            # 未追跡・無視ファイルの内容差分検知 (KKK.2)
            b_untracked = before.get("untracked_hashes", {})
            a_untracked = after.get("untracked_hashes", {})
            for path in set(b_untracked.keys()) | set(a_untracked.keys()):
                if b_untracked.get(path) != a_untracked.get(path):
                    changed_paths.append(path)

            if not changed_paths and before.get("diff_hash") != after.get("diff_hash"):
                changed_paths.append("tracked_diff_modified")
            return sorted(set(changed_paths))
        if before.get("type") == "tree" and after.get("type") == "tree":
            b_files = before.get("files", {})
            a_files = after.get("files", {})
            all_keys = set(b_files.keys()) | set(a_files.keys())
            diffs = [k for k in all_keys if b_files.get(k) != a_files.get(k)]
            return sorted(diffs)
        return ["snapshot_type_mismatch"]

    def _execute_test(
        self,
        test_file: Path,
        search_dir: str,
        timeout_sec: int,
        sandbox_dir: Path | None = None,
        tracer_root: str | None = None,
    ) -> RedVerificationResult:
        """構造化 TestResult を用いて候補テストを実行し、Red/Green/無効を判定する。
        本体リポジトリ汚染を防ぐため、実行作業ディレクトリは使い捨ての一時クローン sandbox_dir に隔離し、
        シークレット等の機密環境変数を除外サニタイズした上で、本体作業ツリーを OS 権限レベルおよび
        カーネルファイルフラグで読取専用にロックダウンして実行する。
        さらに、OS レベルのサンドボックス (macOS sandbox-exec / Linux bwrap) の内側でのみ実行し、
        外部ネットワーク接続および元リポジトリへのファイル書き込みをカーネルレベルで遮断する (JJJ.1)。
        また、プロセスグループ分離 (start_new_session=True) により子孫プロセスを一括回収する (JJJ.3)。

        【既知の制約 (Known Limitation)】:
        プロセスグループ単位での SIGKILL による一括回収（リーピング）を行うが、
        子プロセスが意図的に `setsid()` や独自プロセスグループ (`setpgid`) を呼び出して
        セッション・プロセスグループを切り離した場合、親からのプロセスグループシグナルは
        届かない。完全なプロセスツリー追跡およびプロセス隔離には、OS レベルの
        cgroup やコンテナ (PID namespace) が必要である。
        """
        nproc_limits, proc_limit_status = self._compute_nproc_limit()

        def _res(**kwargs: Any) -> RedVerificationResult:
            if "process_limit_status" not in kwargs:
                kwargs["process_limit_status"] = proc_limit_status
            return RedVerificationResult(**kwargs)

        # 0. root (UID 0) 実行検知と安全遮断 (OSパーミッション迂回の防止)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="ROOT_EXECUTION_FORBIDDEN",
                output="Running candidate tests as root (UID 0) bypasses OS permission lockdown and is forbidden.",
                reason_detail="Running candidate tests as root (UID 0) bypasses OS permission lockdown and is forbidden.",
            )

        # 0.1 OS サンドボックス基盤の確認 (Fail-Closed: サンドボックス不可時は即座に拒絶)
        backend = self._get_sandbox_backend(
            timeout_sec=timeout_sec,
            nproc_limits=nproc_limits,
        )
        if not backend:
            unavailable_msg = self._get_sandbox_unavailable_detail(
                timeout_sec=timeout_sec,
                nproc_limits=nproc_limits,
            )
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="SANDBOX_UNAVAILABLE",
                output=unavailable_msg,
                reason_detail=unavailable_msg,
            )

        # 1. 環境変数のサニタイズ（シークレット・APIキー・危険動的変数の除外）
        sanitized_env: dict[str, str] = {}
        for k, v in os.environ.items():
            if self.SECRET_ENV_PATTERN.search(k):
                continue
            if any(k.startswith(pfx) for pfx in self.DANGEROUS_ENV_PREFIXES):
                continue
            sanitized_env[k] = v

        # 2. 実行作業ディレクトリの隔離 (本体 repo_dir への書き込み防止)
        exec_cwd = sandbox_dir or Path(search_dir)
        exec_resolved = exec_cwd.resolve()
        repo_resolved = self.repo_dir.resolve()

        # PYTHONPATH は元リポジトリではなく、一時クローンのパスを設定
        src_path = str(exec_resolved / "src")
        repo_path = str(exec_resolved)
        sanitized_env["PYTHONPATH"] = f"{src_path}:{repo_path}"
        sanitized_env["TMPDIR"] = str(exec_resolved)

        # SBX-9: 候補テストの HOME / XDG_CONFIG_HOME を一時領域の空ディレクトリにし、
        # システム設定の参照も抑止してサンドボックス内で git がホスト設定に依存せず動くようにする
        sandbox_home_tmp = tempfile.TemporaryDirectory()
        sandbox_home_root = Path(sandbox_home_tmp.name).resolve()
        sandbox_home_dir = sandbox_home_root / "home"
        sandbox_xdg_config_dir = sandbox_home_root / "xdg_config"
        sandbox_home_dir.mkdir(parents=True, exist_ok=True)
        sandbox_xdg_config_dir.mkdir(parents=True, exist_ok=True)
        sanitized_env["HOME"] = str(sandbox_home_dir)
        sanitized_env["XDG_CONFIG_HOME"] = str(sandbox_xdg_config_dir)
        sanitized_env["GIT_CONFIG_NOSYSTEM"] = "1"

        runner_args = [
            sys.executable,
            "-c",
            self.RUNNER_CODE,
            search_dir,
            test_file.name,
            str(repo_resolved),
            tracer_root or search_dir,
        ]

        profile_file: Path | None = None
        home_resolved = Path.home().resolve()
        if backend == "sandbox-exec":
            py_raw = Path(sys.executable)
            py_bin = py_raw.resolve()
            py_prefix = Path(sys.prefix).resolve()
            py_base_prefix = Path(sys.base_prefix).resolve()

            # NNN.1: ホームディレクトリ配下の仮想環境に対する最小特権マウントルール生成
            pfx_rules: list[str] = []
            toolchain_allows: list[str] = []

            for p in [py_raw, py_bin, py_prefix, py_base_prefix]:
                if p.is_relative_to("/Applications"):
                    toolchain_allows.append('(allow file-read* (literal "/Applications"))')
                    if len(p.parts) >= 3:
                        toolchain_allows.append(f'(allow file-read* (subpath "{Path(*p.parts[:3])}"))')
                elif p.is_relative_to("/Library"):
                    if len(p.parts) >= 4:
                        toolchain_allows.append(f'(allow file-read* (subpath "{Path(*p.parts[:4])}"))')
                    elif len(p.parts) >= 3:
                        toolchain_allows.append(f'(allow file-read* (subpath "{Path(*p.parts[:3])}"))')

            if Path("/Library/Developer").exists():
                toolchain_allows.append('(allow file-read* (subpath "/Library/Developer"))')

            for pfx in [py_prefix, py_base_prefix]:
                is_in_home = (
                    pfx == home_resolved
                    or pfx.is_relative_to(home_resolved)
                )
                if is_in_home:
                    for sub in ["bin", "lib", "lib64", "include"]:
                        sub_p = pfx / sub
                        if sub_p.exists():
                            pfx_rules.append(f'(allow file-read* (subpath "{sub_p}"))')
                    cfg = pfx / "pyvenv.cfg"
                    if cfg.exists():
                        pfx_rules.append(f'(allow file-read* (literal "{cfg}"))')
                else:
                    pfx_rules.append(f'(allow file-read* (subpath "{pfx}"))')

            toolchain_rules_str = "\n".join(dict.fromkeys(toolchain_allows))
            unique_pfx_rules = list(dict.fromkeys(pfx_rules))
            pfx_rules_str = "\n".join(unique_pfx_rules)

            extra_allow_rules: list[str] = []
            for opt_path in ["/opt/homebrew", "/usr/local"]:
                if Path(opt_path).exists():
                    extra_allow_rules.append(f'(allow file-read* (subpath "{opt_path}"))')
            extra_allow_str = "\n".join(extra_allow_rules)
            symlink_rules_str = "\n".join(_macos_symlink_traversal_rules(py_raw))

            # NNN.1: 広範な /Library, /private/etc, /etc の一括 allow を廃止し、
            # /private/var に対しても db, root, log, audit 等の機密領域を明示拒否（deny）し、
            # システムライブラリ・実行作業ディレクトリのみの最小権限ホワイトリスト方式を適用
            profile_content = f"""(version 1)
(allow default)
(deny network*)
(deny file-write*)
(allow file-write* (subpath "{exec_resolved}"))
(allow file-write* (subpath "{exec_cwd}"))
(allow file-write* (subpath "{sandbox_home_root}"))
(allow file-write* (literal "/dev/null"))
(allow file-write* (literal "/dev/zero"))
(allow file-write* (literal "/dev/dtracehelper"))
(deny file-read*)
(allow file-read* (literal "/"))
(allow file-read* (literal "/private"))
(allow file-read* (literal "/private/var"))
(allow file-read* (literal "/private/var/folders"))
(allow file-read* (literal "/var"))
(allow file-read* (subpath "/var/folders"))
(allow file-read* (subpath "/private/var/folders"))
(allow file-read* (literal "/tmp"))
(allow file-read* (literal "/private/tmp"))
(allow file-read* (subpath "/tmp"))
(allow file-read* (subpath "/private/tmp"))
(allow file-read* (literal "/dev"))
(allow file-read* (subpath "/dev"))
(allow file-read* (subpath "/System"))
(allow file-read* (subpath "/usr"))
(deny file-read* (subpath "/Library"))
(deny file-read* (subpath "/private/var/db"))
(deny file-read* (subpath "/private/var/root"))
(deny file-read* (subpath "/private/var/log"))
(deny file-read* (subpath "/private/var/audit"))
(deny file-read* (subpath "/private/var/spool"))
(deny file-read* (subpath "/private/var/vm"))
(deny file-read* (subpath "/private/var/backups"))
(deny file-read* (subpath "/var/db"))
(deny file-read* (subpath "/var/root"))
(deny file-read* (subpath "/var/log"))
(deny file-read* (subpath "/var/audit"))
(deny file-read* (subpath "/var/spool"))
(deny file-read* (subpath "/var/vm"))
(deny file-read* (subpath "/var/backups"))
(deny file-read* (subpath "/private/etc"))
(deny file-read* (subpath "/etc"))
(deny file-read* (subpath "/Applications"))
(deny file-read* (subpath "/Volumes"))
(deny file-read* (subpath "/Users"))
(deny file-read* (subpath "{home_resolved}"))
{toolchain_rules_str}
{extra_allow_str}
(allow file-read* (literal "/etc/resolv.conf"))
(allow file-read* (literal "/private/etc/resolv.conf"))
(allow file-read* (literal "/etc/hosts"))
(allow file-read* (literal "/private/etc/hosts"))
(allow file-read* (subpath "/private/etc/ssl"))
(allow file-read* (subpath "/etc/ssl"))
{pfx_rules_str}
{symlink_rules_str}
(allow file-read* (literal "{py_raw}"))
(allow file-read* (literal "{py_bin}"))
(allow file-read* (subpath "{py_bin}"))
(allow file-read* (subpath "{exec_resolved}"))
(allow file-read* (subpath "{exec_cwd}"))
(allow file-read* (subpath "{sandbox_home_root}"))
(deny file-read* (subpath "{repo_resolved}"))
(deny file-write* (subpath "{repo_resolved}"))
"""
            with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False, dir=str(exec_resolved)) as tf:
                tf.write(profile_content)
                tf.flush()
                profile_file = Path(tf.name)
            cmd = ["sandbox-exec", "-f", str(profile_file)] + runner_args
        elif backend == "bwrap":
            cmd = ["bwrap"]
            # LLL.1: /etc 全体公開を廃止。システムバイナリ・ライブラリのみを公開
            for sys_dir in ["/usr", "/lib", "/lib64", "/bin", "/sbin"]:
                if Path(sys_dir).exists():
                    cmd.extend(["--ro-bind", sys_dir, sys_dir])
            # DNS解決・SSL/TLS証明書等に最低限必要な個別ファイル/ディレクトリのみ限定マウント
            for etc_item in [
                "/etc/resolv.conf",
                "/etc/ssl",
                "/etc/pki",
                "/etc/hosts",
                "/etc/nsswitch.conf",
                "/etc/localtime",
            ]:
                item_path = Path(etc_item)
                if item_path.exists():
                    cmd.extend(["--ro-bind", str(item_path), str(item_path)])
            for pfx_raw in [sys.prefix, sys.base_prefix]:
                pfx_path = Path(pfx_raw).resolve()
                if pfx_path.exists() and not any(
                    pfx_path == Path(sd) or Path(sd) in pfx_path.parents for sd in ["/usr", "/bin", "/lib", "/lib64"]
                ):
                    is_in_home = pfx_path == home_resolved or pfx_path.is_relative_to(home_resolved)
                    if is_in_home:
                        for sub in ["bin", "lib", "lib64", "include"]:
                            sub_path = pfx_path / sub
                            if sub_path.exists():
                                cmd.extend(["--ro-bind", str(sub_path), str(sub_path)])
                        cfg_path = pfx_path / "pyvenv.cfg"
                        if cfg_path.exists():
                            cmd.extend(["--ro-bind", str(cfg_path), str(cfg_path)])
                    else:
                        cmd.extend(["--ro-bind", str(pfx_path), str(pfx_path)])
            cmd.extend([
                "--dev", "/dev",
                "--proc", "/proc",
                "--tmpfs", "/tmp",
                "--dir", str(exec_resolved),
                "--bind", str(exec_resolved), str(exec_resolved),
                "--dir", str(sandbox_home_dir),
                "--bind", str(sandbox_home_dir), str(sandbox_home_dir),
                "--dir", str(sandbox_xdg_config_dir),
                "--bind", str(sandbox_xdg_config_dir), str(sandbox_xdg_config_dir),
                "--unshare-net",
                "--die-with-parent",
            ])
            cmd.extend(runner_args)
        else:
            with contextlib.suppress(OSError):
                sandbox_home_tmp.cleanup()
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="SANDBOX_UNAVAILABLE",
                output=f"Unknown sandbox backend: {backend}",
                reason_detail="Unknown sandbox backend.",
            )

        preexec = (
            (lambda: self._apply_resource_limits(timeout_sec, nproc_limits))
            if os.name == "posix"
            else None
        )

        before_snapshot = self._capture_repo_snapshot()

        pgid: int | None = None
        stdout = ""
        stderr = ""
        returncode = 1
        timed_out = False
        timeout_err: str = ""
        execution_err: str = ""

        try:
            # 起動順序是正 (KKK.3 / Sol氏 Phase 50 P1指摘):
            # 元リポジトリを先に完全ロックダウンし、保護された安全な状態の内側で Popen を起動
            with self._lockdown_repo(self.repo_dir):
                proc = subprocess.Popen(
                    cmd,
                    cwd=exec_cwd,
                    env=sanitized_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    start_new_session=bool(os.name == "posix"),
                    preexec_fn=preexec,  # noqa: PLW1509
                )
                pgid = proc.pid

                try:
                    stdout, stderr = proc.communicate(timeout=timeout_sec)
                except subprocess.TimeoutExpired as e:
                    timed_out = True
                    timeout_err = str(e)
                    if pgid and os.name == "posix":
                        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                            os.killpg(pgid, signal.SIGKILL)
                    with contextlib.suppress(Exception):
                        stdout, stderr = proc.communicate(timeout=5)
                except Exception as e:  # noqa: BLE001
                    execution_err = str(e)
                    if pgid and os.name == "posix":
                        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                            os.killpg(pgid, signal.SIGKILL)
                finally:
                    # 正常終了時・例外時にも残存した子孫プロセスグループ全体を一括回収 (JJJ.3)
                    if pgid and os.name == "posix":
                        with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                            os.killpg(pgid, signal.SIGKILL)
                returncode = proc.returncode if proc.returncode is not None else 1
        finally:
            if profile_file and profile_file.exists():
                with contextlib.suppress(OSError):
                    profile_file.unlink()
            with contextlib.suppress(OSError):
                sandbox_home_tmp.cleanup()

        # 3. 元リポジトリ改ざん検知 (JJJ.2: 多層防御)
        after_snapshot = self._capture_repo_snapshot()
        tampered_paths = self._compare_repo_snapshots(before_snapshot, after_snapshot)
        if tampered_paths:
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="REPO_TAMPER_DETECTED",
                output=f"Parent repository tampering detected after candidate test execution. Modified paths: {tampered_paths}",
                reason_detail=f"Parent repository was tampered with during test execution: {', '.join(tampered_paths)}",
            )

        if timed_out:
            return _res(
                is_reproduced=False,
                exit_code=124,
                reason="TIMEOUT",
                output=timeout_err,
                reason_detail=f"Test timed out after {timeout_sec}s",
            )

        if execution_err:
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="EXECUTION_ERROR",
                output=execution_err,
                reason_detail=execution_err,
            )

        returncode = proc.returncode if proc.returncode is not None else 1
        started_marker = "===AUTO_DEV_RUNNER_STARTED==="
        if started_marker not in stdout.splitlines():
            stderr_text = stderr.strip()
            guidance = _format_sandbox_unavailable_message(
                sys.platform,
                probe_error=stderr_text or f"exit code {returncode}",
            )
            combined_output = f"{stderr_text}\n{guidance}".strip() if stderr_text else guidance
            return _res(
                is_reproduced=False,
                exit_code=returncode,
                reason="SANDBOX_UNAVAILABLE",
                output=combined_output,
                reason_detail=guidance,
            )

        output = (stdout + "\n" + stderr).strip()

        # 構造化 TestResult (JSON) の抽出
        marker = "===AUTO_DEV_TEST_RESULT==="
        parsed_result: dict[str, Any] | None = None
        if marker in stdout:
            try:
                json_part = stdout.split(marker, 1)[1].strip()
                parsed_result = json.loads(json_part)
            except (json.JSONDecodeError, IndexError, ValueError):
                parsed_result = None

        if parsed_result is not None:
            tests_run: int = parsed_result.get("tests_run", 0)
            failures: list[dict[str, str]] = parsed_result.get("failures", [])
            errors: list[dict[str, str]] = parsed_result.get("errors", [])

            # 判定 A: 実行件数チェック (0件実行)
            if tests_run == 0 or returncode == 5:
                return _res(
                    is_reproduced=False,
                    exit_code=returncode,
                    reason="NO_TESTS_RAN",
                    output=output,
                    reason_detail="No test methods were executed during unittest run (ran_count=0).",
                    tests_run=0,
                    failures=len(failures),
                    errors=len(errors),
                )

            # 判定 B: すでに Green (エラーも失敗もなし)
            if tests_run > 0 and len(failures) == 0 and len(errors) == 0:
                return _res(
                    is_reproduced=False,
                    exit_code=0,
                    reason="ALREADY_GREEN",
                    output=output,
                    reason_detail="Candidate test passed against current codebase without failures.",
                    tests_run=tests_run,
                    failures=0,
                    errors=0,
                )

            # 判定 C / D / F: エラー (errors) が存在する場合
            if len(errors) > 0:
                err_text = " ".join(e.get("err", "") for e in errors)
                # インポート不能 / モジュール未発見
                if (
                    "ImportError" in err_text
                    or "ModuleNotFoundError" in err_text
                    or any("_FailedTest" in str(e) for e in errors)
                ):
                    return _res(
                        is_reproduced=False,
                        exit_code=returncode,
                        reason="IMPORT_ERROR",
                        output=output,
                        reason_detail="Test failed due to ImportError / ModuleNotFoundError.",
                        tests_run=tests_run,
                        failures=len(failures),
                        errors=len(errors),
                    )
                # setUp() / tearDown() での環境例外
                if "in setUp" in err_text or "in tearDown" in err_text:
                    return _res(
                        is_reproduced=False,
                        exit_code=returncode,
                        reason="SETUP_ERROR",
                        output=output,
                        reason_detail="Test failure occurred during setUp/tearDown rather than test body assertion.",
                        tests_run=tests_run,
                        failures=len(failures),
                        errors=len(errors),
                    )
                # テスト本体内での予期せぬ例外 (アサーション失敗ではない)
                return _res(
                    is_reproduced=False,
                    exit_code=returncode,
                    reason="UNHANDLED_EXCEPTION",
                    output=output,
                    reason_detail="Test exited with error/exception without an assertion failure (failures=0, errors>0).",
                    tests_run=tests_run,
                    failures=len(failures),
                    errors=len(errors),
                )

            # 判定 E: アサーション失敗 (failures > 0, errors == 0) による Red
            if len(failures) > 0:
                executed_product = parsed_result.get("executed_product_code", True)
                is_trivial_ast, trivial_msg = check_trivial_red_ast(test_file)
                if not executed_product or is_trivial_ast:
                    reason_msg = (
                        f"Candidate test failed with assertion failure, but zero product code was executed (Trivial/Fake Red): {trivial_msg}"
                        if is_trivial_ast
                        else "Candidate test failed with assertion failure, but zero product code was executed (Trivial/Fake Red)."
                    )
                    return _res(
                        is_reproduced=False,
                        exit_code=returncode,
                        reason="TRIVIAL_RED",
                        output=output,
                        reason_detail=reason_msg,
                        tests_run=tests_run,
                        failures=len(failures),
                        errors=len(errors),
                    )
                return _res(
                    is_reproduced=True,
                    exit_code=returncode,
                    reason="GENUINE_RED",
                    output=output,
                    reason_detail="Candidate test failed as expected with assertion failure (Genuine Red).",
                    tests_run=tests_run,
                    failures=len(failures),
                    errors=len(errors),
                )

        # フォールバック (構造化 JSON が取得できなかった場合)
        m_ran = re.search(r"Ran (\d+) tests?", output)
        ran_count = int(m_ran.group(1)) if m_ran else 0
        m_fail = re.search(r"failures=(\d+)", output)
        fail_count = int(m_fail.group(1)) if m_fail else (1 if "FAIL:" in output else 0)
        m_err = re.search(r"errors=(\d+)", output)
        err_count = int(m_err.group(1)) if m_err else (1 if "ERROR:" in output else 0)

        if ran_count == 0 or "NO TESTS RAN" in output or returncode == 5:
            return _res(
                is_reproduced=False,
                exit_code=returncode,
                reason="NO_TESTS_RAN",
                output=output,
                reason_detail="No test methods were executed during unittest run (ran_count=0).",
                tests_run=ran_count,
                failures=fail_count,
                errors=err_count,
            )

        if returncode == 0:
            return _res(
                is_reproduced=False,
                exit_code=0,
                reason="ALREADY_GREEN",
                output=output,
                reason_detail="Candidate test passed against current codebase without failures.",
                tests_run=ran_count,
                failures=0,
                errors=0,
            )

        if (
            "ImportError: Failed to import test module" in output
            or "ModuleNotFoundError:" in output
            or "ImportError:" in output
        ):
            return _res(
                is_reproduced=False,
                exit_code=returncode,
                reason="IMPORT_ERROR",
                output=output,
                reason_detail="Test failed due to ImportError / ModuleNotFoundError.",
                tests_run=ran_count,
                failures=fail_count,
                errors=err_count,
            )

        if "in setUp" in output or "in tearDown" in output:
            return _res(
                is_reproduced=False,
                exit_code=returncode,
                reason="SETUP_ERROR",
                output=output,
                reason_detail="Test failure occurred during setUp/tearDown rather than test body assertion.",
                tests_run=ran_count,
                failures=fail_count,
                errors=err_count,
            )

        return _res(
            is_reproduced=False,
            exit_code=returncode,
            reason="UNHANDLED_EXCEPTION",
            output=output,
            reason_detail="Test exited with error/exception without structured test result.",
            tests_run=ran_count,
            failures=fail_count,
            errors=err_count,
        )


def _classify_red_first_file_result(res: RedVerificationResult) -> str:
    """RedVerificationResult の reason を GATE-17 のファイル単位の判定へ変換する。"""
    if res.reason in _RED_FIRST_CONFIRMED_REASONS:
        return "CONFIRMED"
    if res.reason in _RED_FIRST_SANDBOX_REASONS:
        return "SANDBOX_UNAVAILABLE"
    if res.reason == "TIMEOUT":
        return "TIMEOUT"
    return "NOT_RED"


def verify_red_first_tests(
    repo_dir: Path,
    checkpoint: str,
    test_files: list[str],
    timeout_sec: int = RED_FIRST_FILE_TIMEOUT_SEC,
) -> dict[str, Any]:
    """GATE-17: 対象テストファイルをチェックポイント時点のコードに対してサンドボックス実行し集計する。"""
    if not test_files:
        return {"result": "NO_TEST_CHANGES", "file_results": []}

    verifier = RedTestVerifier(repo_dir=repo_dir)
    file_results: list[dict[str, Any]] = []
    for rel_path in test_files:
        test_path = repo_dir / rel_path
        try:
            test_code = test_path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            file_results.append(
                {
                    "file": rel_path,
                    "result": "NOT_RED",
                    "reason": "EXECUTION_ERROR",
                    "reason_detail": f"Failed to read test file: {e}",
                }
            )
            continue
        res = verifier.verify_red_first_test(
            rel_path,
            test_code,
            timeout_sec=timeout_sec,
            commit=checkpoint,
        )
        file_results.append(
            {
                "file": rel_path,
                "result": _classify_red_first_file_result(res),
                "reason": res.reason,
                "reason_detail": res.reason_detail,
            }
        )

    statuses = [entry["result"] for entry in file_results]
    if "CONFIRMED" in statuses:
        overall = "CONFIRMED"
    elif "SANDBOX_UNAVAILABLE" in statuses:
        overall = "SANDBOX_UNAVAILABLE"
    elif "TIMEOUT" in statuses:
        overall = "TIMEOUT"
    else:
        overall = "NOT_RED"

    return {
        "result": overall,
        "file_results": file_results,
    }
