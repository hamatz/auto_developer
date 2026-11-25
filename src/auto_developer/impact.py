"""Impact analysis for Spec Compiler (`auto-dev compile --impact`, CMP-4)."""

from __future__ import annotations

import ast
import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from auto_developer.compiler import (
    IR_SCHEMA_VERSION,
    CompileIssue,
    _add_error,
    validate_ir_schema,
)

IMPACT_OUTPUT_SCHEMA_VERSION = "auto-dev-impact/1"
NO_FINGERPRINTS_NOTE = "指紋がないので調べていない"

MatchKind = Literal["definition", "reference", "string", "text"]


@dataclass(frozen=True)
class _IndexedFile:
    """Pre-indexed tracked repository file for deterministic fingerprint matching."""

    path: str
    is_test: bool
    entry_category: str | None
    is_python_ast: bool
    definitions: tuple[tuple[int, str], ...]
    references: tuple[tuple[int, str], ...]
    strings: tuple[tuple[int, str], ...]
    lines: tuple[str, ...]


def is_test_file(rel_path: str) -> bool:
    """Return True if `rel_path` is under `tests/` or named `test_*.py` / `*_test.py`."""
    parts = Path(rel_path).parts
    if not parts:
        return False
    if "tests" in parts[:-1]:
        return True
    name = parts[-1]
    return (name.startswith("test_") and name.endswith(".py")) or name.endswith("_test.py")


def _is_main_guard_compare(test: ast.expr) -> bool:
    if not isinstance(test, ast.Compare):
        return False
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq) or len(test.comparators) != 1:
        return False
    left, right = test.left, test.comparators[0]
    return (
        isinstance(left, ast.Name)
        and left.id == "__name__"
        and isinstance(right, ast.Constant)
        and right.value == "__main__"
    ) or (
        isinstance(right, ast.Name)
        and right.id == "__name__"
        and isinstance(left, ast.Constant)
        and left.value == "__main__"
    )


def _has_main_guard(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and _is_main_guard_compare(node.test):
            return True
    return False


def _entry_point_category(rel_path: str, has_main_guard: bool) -> str | None:
    parts = Path(rel_path).parts
    if len(parts) == 1 and parts[0].startswith("README") and parts[0].endswith(".md"):
        return "readme"
    if len(parts) == 3 and parts[0] == "skills" and parts[2] == "SKILL.md":
        return "skill"
    if len(parts) == 2 and parts[0] == "docs" and parts[1].endswith(".md"):
        return "doc"
    if rel_path.endswith(".py") and has_main_guard:
        return "script"
    return None


def _index_python_ast(
    rel_path: str,
    tree: ast.AST,
    *,
    test_file: bool,
) -> _IndexedFile:
    definitions: list[tuple[int, str]] = []
    references: list[tuple[int, str]] = []
    strings: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            definitions.append((node.lineno, node.name))
        elif isinstance(node, ast.Name):
            references.append((node.lineno, node.id))
        elif isinstance(node, ast.Attribute):
            references.append((node.lineno, node.attr))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings.append((node.lineno, node.value))

    category = _entry_point_category(rel_path, _has_main_guard(tree))
    return _IndexedFile(
        path=rel_path,
        is_test=test_file,
        entry_category=category,
        is_python_ast=True,
        definitions=tuple(definitions),
        references=tuple(references),
        strings=tuple(strings),
        lines=(),
    )


def _index_file(work_dir: Path, rel_path: str) -> _IndexedFile | None:
    full_path = work_dir / rel_path
    if full_path.is_symlink() or not full_path.is_file():
        return None
    try:
        raw_bytes = full_path.read_bytes()
    except OSError:
        return None
    if b"\x00" in raw_bytes:
        return None
    try:
        text = raw_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None

    test_file = is_test_file(rel_path)
    if rel_path.endswith(".py"):
        try:
            tree = ast.parse(text, filename=rel_path)
            return _index_python_ast(rel_path, tree, test_file=test_file)
        except (SyntaxError, ValueError):
            pass

    return _IndexedFile(
        path=rel_path,
        is_test=test_file,
        entry_category=_entry_point_category(rel_path, False),
        is_python_ast=False,
        definitions=(),
        references=(),
        strings=(),
        lines=tuple(text.splitlines()),
    )


def _list_tracked_files(work_dir: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=work_dir,
        capture_output=True,
        check=False,
    )
    if proc.returncode != 0:
        stderr = proc.stderr.decode("utf-8", errors="replace").strip()
        detail = f": {stderr}" if stderr else ""
        raise RuntimeError(f"git ls-files failed (exit {proc.returncode}){detail}")

    paths: set[str] = set()
    for raw_entry in proc.stdout.split(b"\x00"):
        if not raw_entry:
            continue
        rel = raw_entry.decode("utf-8", errors="replace")
        if rel == ".dev" or rel.startswith(".dev/"):
            continue
        paths.add(rel)
    return sorted(paths)


def _match_file_fingerprints(
    indexed: _IndexedFile,
    fingerprints: list[str],
) -> list[tuple[int, MatchKind, str]]:
    found: set[tuple[int, MatchKind, str]] = set()
    if indexed.is_python_ast:
        for fp in fingerprints:
            for line, name in indexed.definitions:
                if name == fp:
                    found.add((line, "definition", fp))
            for line, name in indexed.references:
                if name == fp:
                    found.add((line, "reference", fp))
            for line, val in indexed.strings:
                if fp in val:
                    found.add((line, "string", fp))
    else:
        for lineno, line_text in enumerate(indexed.lines, start=1):
            for fp in fingerprints:
                if fp in line_text:
                    found.add((lineno, "text", fp))
    return sorted(found)


def analyze_ir_impact(
    ir_path: Path,
    work_dir: Path | None = None,
) -> tuple[int, dict[str, Any], list[CompileIssue], str | None]:
    """Analyze potential repository impact of an auto-dev-ir/1 JSON file (CMP-4).

    Returns (exit_code, json_payload, issues, error_msg).
    Exit codes:
    - 0: Analysis succeeded (with or without candidate matches).
    - 1: IR failed F-1 schema validation (repository is not scanned).
    - 2: IR could not be read / invalid JSON / wrong schema / git ls-files failed.
    """
    root_dir = work_dir or Path.cwd()
    issues: list[CompileIssue] = []

    def _error_result(code: int, message: str) -> tuple[int, dict[str, Any], list[CompileIssue], str]:
        _add_error(issues, str(ir_path), message)
        payload: dict[str, Any] = {
            "schema": IMPACT_OUTPUT_SCHEMA_VERSION,
            "error": message,
            "requirements": [],
            "record_tests": [],
        }
        return code, payload, issues, message

    try:
        raw = ir_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return _error_result(2, f"failed to read IR file: {exc}")

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return _error_result(2, f"invalid JSON: {exc}")

    if not isinstance(data, dict):
        return _error_result(2, "IR JSON root must be an object")

    if data.get("schema") != IR_SCHEMA_VERSION:
        return _error_result(
            2,
            f"expected {IR_SCHEMA_VERSION!r}, got {data.get('schema')!r}",
        )

    errors, _warnings = validate_ir_schema(data)
    if errors:
        payload_f1: dict[str, Any] = {
            "schema": IMPACT_OUTPUT_SCHEMA_VERSION,
            "errors": [e.to_dict() for e in errors],
            "requirements": [],
            "record_tests": [],
        }
        return 1, payload_f1, errors, None

    try:
        tracked_paths = _list_tracked_files(root_dir)
    except RuntimeError as exc:
        return _error_result(2, str(exc))

    indexed_files: list[_IndexedFile] = []
    for rel in tracked_paths:
        idx = _index_file(root_dir, rel)
        if idx is not None:
            indexed_files.append(idx)

    raw_reqs = data.get("requirements", [])
    sorted_reqs = sorted(
        (r for r in raw_reqs if isinstance(r, dict)),
        key=lambda r: str(r.get("id", "")),
    )

    req_results: list[dict[str, Any]] = []
    for req in sorted_reqs:
        req_id = str(req["id"])
        statement = str(req.get("statement", ""))
        raw_fps = req.get("fingerprints")
        fingerprints = sorted({fp for fp in (raw_fps or []) if isinstance(fp, str) and fp})

        if not fingerprints:
            req_results.append(
                {
                    "id": req_id,
                    "statement": statement,
                    "fingerprints": [],
                    "searched": False,
                    "note": NO_FINGERPRINTS_NOTE,
                    "code_matches": [],
                    "test_matches": [],
                    "entry_points": [],
                }
            )
            continue

        code_matches: list[dict[str, Any]] = []
        test_matches: list[dict[str, Any]] = []
        entry_points: list[dict[str, Any]] = []

        for idx_file in indexed_files:
            matches = _match_file_fingerprints(idx_file, fingerprints)
            if not matches:
                continue

            target = test_matches if idx_file.is_test else code_matches
            for line, kind, fp in matches:
                target.append(
                    {
                        "path": idx_file.path,
                        "line": line,
                        "kind": kind,
                        "fingerprint": fp,
                    }
                )

            if idx_file.entry_category is not None:
                entry_points.append(
                    {
                        "path": idx_file.path,
                        "category": idx_file.entry_category,
                        "lines": sorted({m[0] for m in matches}),
                        "fingerprints": sorted({m[2] for m in matches}),
                    }
                )

        req_results.append(
            {
                "id": req_id,
                "statement": statement,
                "fingerprints": fingerprints,
                "searched": True,
                "note": None,
                "code_matches": code_matches,
                "test_matches": test_matches,
                "entry_points": entry_points,
            }
        )

    payload: dict[str, Any] = {
        "schema": IMPACT_OUTPUT_SCHEMA_VERSION,
        "requirements": req_results,
        "record_tests": [],
    }
    return 0, payload, [], None


def format_impact_text(payload: dict[str, Any]) -> list[str]:
    """Render impact analysis payload as deterministic plain text lines."""
    lines: list[str] = []
    for req in payload.get("requirements", []):
        req_id = req.get("id", "")
        if not req.get("searched"):
            note = req.get("note") or NO_FINGERPRINTS_NOTE
            lines.append(f"{req_id}: {note}")
            continue
        fps = ", ".join(req.get("fingerprints", []))
        lines.append(f"{req_id} (fingerprints: {fps}):")
        code_matches = req.get("code_matches", [])
        test_matches = req.get("test_matches", [])
        entry_points = req.get("entry_points", [])
        if code_matches:
            lines.append("  code:")
            for m in code_matches:
                lines.append(f"    {m['path']}:{m['line']} [{m['kind']}] ({m['fingerprint']})")
        if test_matches:
            lines.append("  tests:")
            for m in test_matches:
                lines.append(f"    {m['path']}:{m['line']} [{m['kind']}] ({m['fingerprint']})")
        if entry_points:
            lines.append("  entry_points:")
            for ep in entry_points:
                ep_lines = ", ".join(str(n) for n in ep.get("lines", []))
                ep_fps = ", ".join(ep.get("fingerprints", []))
                lines.append(f"    {ep['path']} [{ep['category']}] lines {ep_lines} ({ep_fps})")
        if not code_matches and not test_matches and not entry_points:
            lines.append("  (no matches)")
    return lines
