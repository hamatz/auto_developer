"""Spec Compiler (CMP-1..CMP-3): IR validation and design outline extraction."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, TypeGuard

from auto_developer.boundary import _match_path

IR_SCHEMA_VERSION = "auto-dev-ir/1"
CHECK_OUTPUT_SCHEMA_VERSION = "auto-dev-compile-check/1"
OUTLINE_OUTPUT_SCHEMA_VERSION = "auto-dev-outline/1"

REQ_ID_RE = re.compile(r"^[A-Z]+-[0-9]+$")
AC_ID_RE = re.compile(r"^AC-[0-9]+$")
DECISION_ID_RE = re.compile(r"^D-[0-9]+$")

_HEADING_IR_MAP: tuple[tuple[str, str], ...] = (
    ("detailed implementation plan", "steps"),
    ("architectural decisions", "decisions"),
    ("alternatives considered", "alternatives"),
    ("critical user journeys", "acceptance"),
    ("verification plan", "verification"),
    ("technical plan", "steps"),
    ("the problem", "purpose"),
    ("non-goals", "out_of_scope"),
    ("overview", "purpose"),
    ("glossary", "terms"),
    ("用語定義", "terms"),
    ("goals", "requirements"),
    ("cuj", "acceptance"),
)

_TRAILING_PAREN_RE = re.compile(r"(?:\s*(?:\([^)]*\)|（[^）]*）))+\s*$")
_LEADING_NUM_RE = re.compile(r"^\s*\d+(?:\.\d+)*[.)]?\s+")
_FENCE_OPEN_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")
_FENCE_CLOSE_RE = re.compile(r"^\s{0,3}(`{3,}|~{3,})\s*$")
_ATX_HEADING_RE = re.compile(r"^\s{0,3}(#{1,6})\s+(.*)$")
_UNRESOLVED_MARKER_RE = re.compile(r"(?<![A-Za-z])(?:TBD|TODO)(?![A-Za-z])|要検討|未定", re.IGNORECASE)
_SPEC_SECTION4_HEADING_RE = re.compile(r"^\s*##\s+4(?:\.|\s|$)")
_SPEC_H2_HEADING_RE = re.compile(r"^\s*##(?!#)\s+")
_SPEC_REQ_LINE_RE = re.compile(r"^\s*-\s+\[[xX]\]\s+\*\*([A-Z]+-[0-9]+)(?:\s|\*\*)")


@dataclass(frozen=True)
class CompileIssue:
    """Single validation error or warning from `auto-dev compile --check`."""

    severity: Literal["ERROR", "WARN"]
    rule: str
    location: str
    message: str

    def to_dict(self) -> dict[str, str]:
        return {
            "rule": self.rule,
            "location": self.location,
            "message": self.message,
        }

    def format_line(self) -> str:
        return f"{self.severity} {self.rule} {self.location}: {self.message}"


def _is_non_empty_str(val: Any) -> bool:
    return isinstance(val, str) and bool(val.strip())


def _is_int(val: Any) -> bool:
    return isinstance(val, int) and not isinstance(val, bool)


def _add_error(errors: list[CompileIssue], location: str, message: str, rule: str = "F-1") -> None:
    errors.append(CompileIssue(severity="ERROR", rule=rule, location=location, message=message))


def _add_warning(warnings: list[CompileIssue], location: str, message: str, rule: str = "F-1") -> None:
    warnings.append(CompileIssue(severity="WARN", rule=rule, location=location, message=message))


def _validate_str_list(
    val: Any,
    location: str,
    errors: list[CompileIssue],
    *,
    non_empty_items: bool = False,
) -> list[str] | None:
    if not isinstance(val, list):
        _add_error(errors, location, "must be a list of strings")
        return None
    result: list[str] = []
    for idx, item in enumerate(val):
        if not isinstance(item, str) or (non_empty_items and not item.strip()):
            msg = "must be a non-empty string" if non_empty_items else "must be a string"
            _add_error(errors, f"{location}[{idx}]", msg)
        else:
            result.append(item)
    return result


def _validate_element_source(src: Any, location: str, errors: list[CompileIssue]) -> None:
    if not isinstance(src, dict):
        _add_error(errors, location, "must be an object {path, lines, sha256}")
        return

    path = src.get("path")
    if not isinstance(path, str) or not path.strip():
        _add_error(errors, f"{location}.path", "must be a non-empty relative path string")
    elif Path(path).is_absolute():
        _add_error(errors, f"{location}.path", "must be a relative path from repository root")

    lines = src.get("lines")
    if (
        not isinstance(lines, list)
        or len(lines) != 2
        or not _is_int(lines[0])
        or not _is_int(lines[1])
        or lines[0] < 1
        or lines[1] < lines[0]
    ):
        _add_error(
            errors,
            f"{location}.lines",
            "must be [start, end] with 1-based integers and start <= end",
        )

    sha256 = src.get("sha256")
    if not _is_non_empty_str(sha256):
        _add_error(errors, f"{location}.sha256", "must be a non-empty string")


def _validate_top_level_source(sources: Any, errors: list[CompileIssue]) -> None:
    if not isinstance(sources, list) or len(sources) == 0:
        _add_error(errors, "source", "must be a non-empty list of {path, sha256}")
        return
    for idx, item in enumerate(sources):
        loc = f"source[{idx}]"
        if not isinstance(item, dict):
            _add_error(errors, loc, "must be an object {path, sha256}")
            continue
        path = item.get("path")
        if not isinstance(path, str) or not path.strip():
            _add_error(errors, f"{loc}.path", "must be a non-empty relative path string")
        elif Path(path).is_absolute():
            _add_error(errors, f"{loc}.path", "must be a relative path from repository root")
        sha256 = item.get("sha256")
        if not _is_non_empty_str(sha256):
            _add_error(errors, f"{loc}.sha256", "must be a non-empty string")


def _validate_optional_source_holder(item: Any, location: str, errors: list[CompileIssue]) -> None:
    if isinstance(item, str):
        if not item.strip():
            _add_error(errors, location, "must not be an empty string")
        return
    if isinstance(item, dict):
        if "source" in item and item["source"] is not None:
            _validate_element_source(item["source"], f"{location}.source", errors)
        return
    _add_error(errors, location, "must be a string or an object")


def _validate_requirements(
    reqs: Any, errors: list[CompileIssue]
) -> tuple[set[str], list[tuple[int, list[str]]]]:
    req_ids: set[str] = set()
    req_acceptances: list[tuple[int, list[str]]] = []
    if not isinstance(reqs, list) or len(reqs) == 0:
        _add_error(errors, "requirements", "must be a non-empty list")
        return req_ids, req_acceptances

    for idx, req in enumerate(reqs):
        loc = f"requirements[{idx}]"
        if not isinstance(req, dict):
            _add_error(errors, loc, "must be an object")
            continue

        req_id = req.get("id")
        if not isinstance(req_id, str) or not REQ_ID_RE.fullmatch(req_id):
            _add_error(errors, f"{loc}.id", "must match ^[A-Z]+-[0-9]+$")
        elif req_id in req_ids:
            _add_error(errors, f"{loc}.id", f"duplicate requirement id {req_id!r}")
        else:
            req_ids.add(req_id)

        if not _is_non_empty_str(req.get("statement")):
            _add_error(errors, f"{loc}.statement", "must be a non-empty string")

        kind = req.get("kind")
        if kind not in ("new", "modify"):
            _add_error(errors, f"{loc}.kind", "must be 'new' or 'modify'")

        if "acceptance" not in req:
            _add_error(errors, f"{loc}.acceptance", "is required and must be a list of strings")
        else:
            acc = _validate_str_list(req.get("acceptance"), f"{loc}.acceptance", errors)
            if acc is not None:
                req_acceptances.append((idx, acc))

        if "source" not in req:
            _add_error(errors, f"{loc}.source", "is required")
        else:
            _validate_element_source(req.get("source"), f"{loc}.source", errors)

        if "fingerprints" in req and req["fingerprints"] is not None:
            _validate_str_list(req["fingerprints"], f"{loc}.fingerprints", errors)

    return req_ids, req_acceptances


def _validate_acceptance(acc_list: Any, errors: list[CompileIssue]) -> set[str]:
    ac_ids: set[str] = set()
    if not isinstance(acc_list, list):
        _add_error(errors, "acceptance", "must be a list")
        return ac_ids

    for idx, item in enumerate(acc_list):
        loc = f"acceptance[{idx}]"
        if not isinstance(item, dict):
            _add_error(errors, loc, "must be an object")
            continue

        ac_id = item.get("id")
        if not isinstance(ac_id, str) or not AC_ID_RE.fullmatch(ac_id):
            _add_error(errors, f"{loc}.id", "must match ^AC-[0-9]+$")
        elif ac_id in ac_ids:
            _add_error(errors, f"{loc}.id", f"duplicate acceptance id {ac_id!r}")
        else:
            ac_ids.add(ac_id)

        for field in ("given", "when", "then"):
            if not isinstance(item.get(field), str):
                _add_error(errors, f"{loc}.{field}", "must be a string")

        if "observable" not in item:
            _add_error(errors, f"{loc}.observable", "is required and must be a list of strings")
        else:
            _validate_str_list(item.get("observable"), f"{loc}.observable", errors)

        if "source" not in item:
            _add_error(errors, f"{loc}.source", "is required")
        else:
            _validate_element_source(item.get("source"), f"{loc}.source", errors)

    return ac_ids


def _validate_decisions(dec_list: Any, errors: list[CompileIssue]) -> None:
    if not isinstance(dec_list, list):
        _add_error(errors, "decisions", "must be a list")
        return

    dec_ids: set[str] = set()
    for idx, item in enumerate(dec_list):
        loc = f"decisions[{idx}]"
        if not isinstance(item, dict):
            _add_error(errors, loc, "must be an object")
            continue

        dec_id = item.get("id")
        if not isinstance(dec_id, str) or not DECISION_ID_RE.fullmatch(dec_id):
            _add_error(errors, f"{loc}.id", "must match ^D-[0-9]+$")
        elif dec_id in dec_ids:
            _add_error(errors, f"{loc}.id", f"duplicate decision id {dec_id!r}")
        else:
            dec_ids.add(dec_id)

        if not _is_non_empty_str(item.get("statement")):
            _add_error(errors, f"{loc}.statement", "must be a non-empty string")

        if "forbids" in item and item["forbids"] is not None:
            _validate_str_list(item["forbids"], f"{loc}.forbids", errors)

        if "source" not in item:
            _add_error(errors, f"{loc}.source", "is required")
        else:
            _validate_element_source(item.get("source"), f"{loc}.source", errors)


def _validate_steps(steps_list: Any, req_ids: set[str], errors: list[CompileIssue]) -> None:
    if not isinstance(steps_list, list):
        _add_error(errors, "steps", "must be a list")
        return

    for idx, item in enumerate(steps_list):
        loc = f"steps[{idx}]"
        if not isinstance(item, dict):
            _add_error(errors, loc, "must be an object")
            continue

        n = item.get("n")
        expected_n = idx + 1
        if not _is_int(n) or n != expected_n:
            _add_error(errors, f"{loc}.n", f"must be sequential integer starting at 1 (expected {expected_n})")

        if not _is_non_empty_str(item.get("title")):
            _add_error(errors, f"{loc}.title", "must be a non-empty string")

        if "requirements" not in item:
            _add_error(errors, f"{loc}.requirements", "is required and must be a list of strings")
        else:
            step_reqs = _validate_str_list(item.get("requirements"), f"{loc}.requirements", errors)
            if step_reqs is not None:
                for r_idx, ref_id in enumerate(step_reqs):
                    if ref_id not in req_ids:
                        _add_error(
                            errors,
                            f"{loc}.requirements[{r_idx}]",
                            f"references unknown requirement id {ref_id!r}",
                        )

        if "files" not in item:
            _add_error(errors, f"{loc}.files", "is required and must be a list of strings")
        else:
            _validate_str_list(item.get("files"), f"{loc}.files", errors, non_empty_items=True)


def _validate_teams(teams_list: Any, errors: list[CompileIssue]) -> None:
    if not isinstance(teams_list, list):
        _add_error(errors, "teams", "must be a list")
        return

    team_ids: set[str] = set()
    for idx, item in enumerate(teams_list):
        loc = f"teams[{idx}]"
        if not isinstance(item, dict):
            _add_error(errors, loc, "must be an object")
            continue

        team_id = item.get("id")
        if not isinstance(team_id, str) or not team_id.strip():
            _add_error(errors, f"{loc}.id", "must be a non-empty string")
        elif team_id in team_ids:
            _add_error(errors, f"{loc}.id", f"duplicate team id {team_id!r}")
        else:
            team_ids.add(team_id)

        if "owns" not in item:
            _add_error(errors, f"{loc}.owns", "is required and must be a list of strings")
        else:
            _validate_str_list(item.get("owns"), f"{loc}.owns", errors, non_empty_items=True)


def _validate_verification(ver: Any, errors: list[CompileIssue]) -> None:
    if not isinstance(ver, dict):
        _add_error(errors, "verification", "must be an object")
        return
    if "commands" not in ver:
        _add_error(errors, "verification.commands", "is required and must be a list of strings")
    else:
        _validate_str_list(ver.get("commands"), "verification.commands", errors)
    if "source" not in ver:
        _add_error(errors, "verification.source", "is required")
    else:
        _validate_element_source(ver.get("source"), "verification.source", errors)


def validate_ir_schema(data: dict[str, Any]) -> tuple[list[CompileIssue], list[CompileIssue]]:
    """Validate an already-parsed IR dict (with schema == auto-dev-ir/1)."""
    errors: list[CompileIssue] = []
    warnings: list[CompileIssue] = []

    if "source" not in data:
        _add_error(errors, "source", "is required")
    else:
        _validate_top_level_source(data.get("source"), errors)

    if "purpose" in data and data["purpose"] is not None:
        _validate_optional_source_holder(data["purpose"], "purpose", errors)

    for list_field in ("terms", "out_of_scope", "alternatives", "open_questions"):
        if list_field in data and data[list_field] is not None:
            val = data[list_field]
            if not isinstance(val, list):
                _add_error(errors, list_field, "must be a list")
            else:
                for idx, item in enumerate(val):
                    _validate_optional_source_holder(item, f"{list_field}[{idx}]", errors)

    if "requirements" not in data:
        _add_error(errors, "requirements", "is required")
        req_ids: set[str] = set()
        req_acceptances: list[tuple[int, list[str]]] = []
    else:
        req_ids, req_acceptances = _validate_requirements(data.get("requirements"), errors)

    ac_ids: set[str] = set()
    if "acceptance" in data and data["acceptance"] is not None:
        ac_ids = _validate_acceptance(data["acceptance"], errors)

    for req_idx, acc_refs in req_acceptances:
        for ref_idx, ac_id in enumerate(acc_refs):
            if ac_id not in ac_ids:
                _add_error(
                    errors,
                    f"requirements[{req_idx}].acceptance[{ref_idx}]",
                    f"references unknown acceptance id {ac_id!r}",
                )

    if "decisions" in data and data["decisions"] is not None:
        _validate_decisions(data["decisions"], errors)

    if "steps" in data and data["steps"] is not None:
        _validate_steps(data["steps"], req_ids, errors)

    if "teams" in data and data["teams"] is not None:
        _validate_teams(data["teams"], errors)

    if "verification" in data and data["verification"] is not None:
        _validate_verification(data["verification"], errors)

    return errors, warnings


def _is_valid_relative_path(path: Any) -> TypeGuard[str]:
    return isinstance(path, str) and bool(path.strip()) and not Path(path).is_absolute()


def _is_valid_line_range(lines: Any) -> TypeGuard[list[int]]:
    return (
        isinstance(lines, list)
        and len(lines) == 2
        and _is_int(lines[0])
        and _is_int(lines[1])
        and lines[0] >= 1
        and lines[1] >= lines[0]
    )


def _load_source_file(
    rel_path: str,
    resolved_root: Path,
    cache: dict[str, tuple[bytes, list[bytes]] | str],
) -> tuple[bytes, list[bytes]] | str:
    if rel_path in cache:
        return cache[rel_path]

    try:
        resolved_file = (resolved_root / rel_path).resolve()
    except (OSError, RuntimeError) as exc:
        err = f"failed to resolve source path {rel_path!r}: {exc}"
        cache[rel_path] = err
        return err

    if not resolved_file.is_relative_to(resolved_root):
        err = f"source path escapes workspace root: {rel_path!r}"
        cache[rel_path] = err
        return err

    if not resolved_file.exists():
        err = f"source file not found: {rel_path!r}"
        cache[rel_path] = err
        return err

    if not resolved_file.is_file():
        err = f"source path is not a regular file: {rel_path!r}"
        cache[rel_path] = err
        return err

    try:
        raw_bytes = resolved_file.read_bytes()
    except OSError as exc:
        err = f"failed to read source file {rel_path!r}: {exc}"
        cache[rel_path] = err
        return err

    loaded = (raw_bytes, raw_bytes.splitlines(keepends=True))
    cache[rel_path] = loaded
    return loaded


def _check_top_level_sources(
    sources: Any,
    resolved_root: Path,
    cache: dict[str, tuple[bytes, list[bytes]] | str],
    errors: list[CompileIssue],
    warnings: list[CompileIssue],
) -> None:
    if not isinstance(sources, list):
        return
    for idx, item in enumerate(sources):
        if not isinstance(item, dict):
            continue
        rel_path = item.get("path")
        if not _is_valid_relative_path(rel_path):
            continue
        loaded = _load_source_file(rel_path, resolved_root, cache)
        if isinstance(loaded, str):
            _add_error(errors, f"source[{idx}].path", loaded)
            continue
        raw_bytes, _ = loaded
        expected_sha = item.get("sha256")
        if _is_non_empty_str(expected_sha):
            actual_sha = hashlib.sha256(raw_bytes).hexdigest()
            if actual_sha != expected_sha:
                _add_warning(
                    warnings,
                    f"source[{idx}].sha256",
                    f"file sha256 mismatch (expected {expected_sha}, actual {actual_sha})",
                )


def _check_element_source(
    src: Any,
    location: str,
    resolved_root: Path,
    cache: dict[str, tuple[bytes, list[bytes]] | str],
    errors: list[CompileIssue],
) -> None:
    if not isinstance(src, dict):
        return
    rel_path = src.get("path")
    if not _is_valid_relative_path(rel_path):
        return
    loaded = _load_source_file(rel_path, resolved_root, cache)
    if isinstance(loaded, str):
        _add_error(errors, f"{location}.path", loaded)
        return
    _, file_lines = loaded
    lines = src.get("lines")
    if not _is_valid_line_range(lines):
        return
    start, end = int(lines[0]), int(lines[1])
    total_lines = len(file_lines)
    if end > total_lines:
        _add_error(
            errors,
            f"{location}.lines",
            f"line range [{start}, {end}] exceeds file length ({total_lines} lines)",
        )
        return
    expected_sha = src.get("sha256")
    if _is_non_empty_str(expected_sha):
        slice_bytes = b"".join(file_lines[start - 1 : end])
        actual_sha = hashlib.sha256(slice_bytes).hexdigest()
        if actual_sha != expected_sha:
            _add_error(
                errors,
                f"{location}.sha256",
                f"line range sha256 mismatch (expected {expected_sha}, actual {actual_sha})",
            )


def validate_ir_sources(
    data: dict[str, Any],
    work_dir: Path,
    errors: list[CompileIssue],
    warnings: list[CompileIssue],
) -> None:
    """Validate source file existence, line ranges, and SHA-256 digests against work_dir."""
    resolved_root = work_dir.resolve()
    cache: dict[str, tuple[bytes, list[bytes]] | str] = {}

    _check_top_level_sources(data.get("source"), resolved_root, cache, errors, warnings)

    purpose = data.get("purpose")
    if isinstance(purpose, dict) and purpose.get("source") is not None:
        _check_element_source(purpose["source"], "purpose.source", resolved_root, cache, errors)

    for list_field in ("terms", "out_of_scope", "alternatives"):
        items = data.get(list_field)
        if isinstance(items, list):
            for idx, item in enumerate(items):
                if isinstance(item, dict) and item.get("source") is not None:
                    _check_element_source(
                        item["source"],
                        f"{list_field}[{idx}].source",
                        resolved_root,
                        cache,
                        errors,
                    )

    for req_field in ("requirements", "acceptance", "decisions"):
        items = data.get(req_field)
        if isinstance(items, list):
            for idx, item in enumerate(items):
                if isinstance(item, dict) and "source" in item:
                    _check_element_source(
                        item.get("source"),
                        f"{req_field}[{idx}].source",
                        resolved_root,
                        cache,
                        errors,
                    )

    verification = data.get("verification")
    if isinstance(verification, dict) and "source" in verification:
        _check_element_source(
            verification.get("source"),
            "verification.source",
            resolved_root,
            cache,
            errors,
        )


def _lint_l1_acceptance(data: dict[str, Any], errors: list[CompileIssue]) -> None:
    """L-1: Every requirement must have >=1 acceptance, and each referenced AC must have >=1 observable."""
    ac_by_id: dict[str, tuple[int, dict[str, Any]]] = {}
    acc_list = data.get("acceptance")
    if isinstance(acc_list, list):
        for ac_idx, item in enumerate(acc_list):
            if isinstance(item, dict):
                ac_id = item.get("id")
                if isinstance(ac_id, str) and ac_id not in ac_by_id:
                    ac_by_id[ac_id] = (ac_idx, item)

    reqs = data.get("requirements")
    if not isinstance(reqs, list):
        return

    reported_ac_indices: set[int] = set()
    for req_idx, req in enumerate(reqs):
        if not isinstance(req, dict):
            continue
        req_id = req.get("id")
        acc_refs = req.get("acceptance")
        if not isinstance(acc_refs, list):
            continue
        if len(acc_refs) == 0:
            _add_error(
                errors,
                f"requirements[{req_idx}].acceptance",
                f"requirement {req_id!r} must reference at least one acceptance criterion",
                rule="L-1",
            )
            continue
        for ac_id in acc_refs:
            if not isinstance(ac_id, str) or ac_id not in ac_by_id:
                continue
            ac_idx, ac_item = ac_by_id[ac_id]
            if ac_idx in reported_ac_indices:
                continue
            observable = ac_item.get("observable")
            if isinstance(observable, list) and not any(
                isinstance(x, str) and x.strip() for x in observable
            ):
                reported_ac_indices.add(ac_idx)
                _add_error(
                    errors,
                    f"acceptance[{ac_idx}].observable",
                    f"acceptance criterion {ac_id!r} must define at least one observable result",
                    rule="L-1",
                )


def _lint_l8_traceability(data: dict[str, Any], warnings: list[CompileIssue]) -> None:
    """L-8: Warn on requirements not linked to any step and steps not linked to any requirement."""
    req_entries: list[tuple[int, str]] = []
    reqs = data.get("requirements")
    if isinstance(reqs, list):
        for req_idx, req in enumerate(reqs):
            if isinstance(req, dict):
                req_id = req.get("id")
                if isinstance(req_id, str) and REQ_ID_RE.fullmatch(req_id):
                    req_entries.append((req_idx, req_id))

    valid_req_ids = {rid for _, rid in req_entries}
    covered_req_ids: set[str] = set()

    steps = data.get("steps")
    if isinstance(steps, list):
        for step_idx, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            step_reqs = step.get("requirements")
            if not isinstance(step_reqs, list):
                continue
            linked = [r for r in step_reqs if isinstance(r, str) and r in valid_req_ids]
            covered_req_ids.update(linked)
            if valid_req_ids and not linked:
                step_n = step.get("n", step_idx + 1)
                _add_warning(
                    warnings,
                    f"steps[{step_idx}].requirements",
                    f"step {step_n} is not linked to any requirement",
                    rule="L-8",
                )

    for req_idx, req_id in req_entries:
        if req_id not in covered_req_ids:
            _add_warning(
                warnings,
                f"requirements[{req_idx}]",
                f"requirement {req_id!r} is not linked to any step",
                rule="L-8",
            )


def _check_unresolved_marker(val: Any, location: str, errors: list[CompileIssue]) -> None:
    if isinstance(val, str):
        match = _UNRESOLVED_MARKER_RE.search(val)
        if match:
            _add_error(
                errors,
                location,
                f"contains unresolved marker {match.group(0)!r}",
                rule="L-9",
            )


def _lint_l9_unresolved(data: dict[str, Any], errors: list[CompileIssue]) -> None:
    """L-9: Non-empty open_questions or unresolved markers (TBD/TODO/要検討/未定)."""
    open_questions = data.get("open_questions")
    if isinstance(open_questions, list) and len(open_questions) > 0:
        _add_error(
            errors,
            "open_questions",
            f"open_questions must be empty ({len(open_questions)} remaining)",
            rule="L-9",
        )

    reqs = data.get("requirements")
    if isinstance(reqs, list):
        for idx, req in enumerate(reqs):
            if isinstance(req, dict):
                _check_unresolved_marker(req.get("statement"), f"requirements[{idx}].statement", errors)

    acc_list = data.get("acceptance")
    if isinstance(acc_list, list):
        for idx, item in enumerate(acc_list):
            if isinstance(item, dict):
                for field in ("given", "when", "then"):
                    _check_unresolved_marker(item.get(field), f"acceptance[{idx}].{field}", errors)
                obs = item.get("observable")
                if isinstance(obs, list):
                    for o_idx, o_val in enumerate(obs):
                        _check_unresolved_marker(o_val, f"acceptance[{idx}].observable[{o_idx}]", errors)

    steps = data.get("steps")
    if isinstance(steps, list):
        for idx, step in enumerate(steps):
            if isinstance(step, dict):
                _check_unresolved_marker(step.get("title"), f"steps[{idx}].title", errors)


def _lint_l5_forbids(data: dict[str, Any], errors: list[CompileIssue]) -> None:
    """L-5: Decision forbids phrases must not appear verbatim (case-sensitive) in req/acceptance."""
    forbid_entries: list[tuple[int, str, int, str]] = []
    decisions = data.get("decisions")
    if isinstance(decisions, list):
        for dec_idx, dec in enumerate(decisions):
            if not isinstance(dec, dict):
                continue
            dec_id = dec.get("id")
            dec_id_str = dec_id if isinstance(dec_id, str) else f"decisions[{dec_idx}]"
            forbids = dec.get("forbids")
            if not isinstance(forbids, list):
                continue
            for f_idx, phrase in enumerate(forbids):
                if isinstance(phrase, str) and phrase.strip():
                    forbid_entries.append((dec_idx, dec_id_str, f_idx, phrase))

    if not forbid_entries:
        return

    reqs = data.get("requirements")
    if isinstance(reqs, list):
        for req_idx, req in enumerate(reqs):
            if not isinstance(req, dict):
                continue
            statement = req.get("statement")
            if not isinstance(statement, str):
                continue
            for dec_idx, dec_id_str, f_idx, phrase in forbid_entries:
                if phrase in statement:
                    _add_error(
                        errors,
                        f"requirements[{req_idx}].statement",
                        f"conflicts with decisions[{dec_idx}] ({dec_id_str}) forbids[{f_idx}] {phrase!r}",
                        rule="L-5",
                    )

    acc_list = data.get("acceptance")
    if isinstance(acc_list, list):
        for ac_idx, item in enumerate(acc_list):
            if not isinstance(item, dict):
                continue
            for field in ("given", "when", "then"):
                val = item.get(field)
                if not isinstance(val, str):
                    continue
                for dec_idx, dec_id_str, f_idx, phrase in forbid_entries:
                    if phrase in val:
                        _add_error(
                            errors,
                            f"acceptance[{ac_idx}].{field}",
                            f"conflicts with decisions[{dec_idx}] ({dec_id_str}) forbids[{f_idx}] {phrase!r}",
                            rule="L-5",
                        )


def _parse_spec_section4_ids(spec_text: str) -> set[str]:
    """Extract requirement IDs (`- [x] **<ID> `) from section 4 of SPEC.md."""
    ids: set[str] = set()
    in_section4 = False
    fence_char: str | None = None
    fence_len = 0

    for raw_line in spec_text.splitlines():
        if fence_char is not None:
            close_match = _FENCE_CLOSE_RE.match(raw_line)
            if close_match:
                marker = close_match.group(1)
                if marker[0] == fence_char and len(marker) >= fence_len:
                    fence_char = None
                    fence_len = 0
            continue

        open_match = _FENCE_OPEN_RE.match(raw_line)
        if open_match:
            marker = open_match.group(1)
            info = open_match.group(2)
            if not (marker[0] == "`" and "`" in info):
                fence_char = marker[0]
                fence_len = len(marker)
                continue

        if not in_section4:
            if _SPEC_SECTION4_HEADING_RE.match(raw_line):
                in_section4 = True
            continue

        if _SPEC_H2_HEADING_RE.match(raw_line):
            break

        req_match = _SPEC_REQ_LINE_RE.match(raw_line)
        if req_match:
            ids.add(req_match.group(1))

    return ids


def _lint_l6_spec_overlap(
    data: dict[str, Any],
    spec_section4_ids: set[str] | None,
    warnings: list[CompileIssue],
) -> None:
    """L-6: Warn if requirement ID/kind conflicts with SPEC.md section 4."""
    if spec_section4_ids is None:
        return

    reqs = data.get("requirements")
    if not isinstance(reqs, list):
        return

    for idx, req in enumerate(reqs):
        if not isinstance(req, dict):
            continue
        req_id = req.get("id")
        kind = req.get("kind")
        if not isinstance(req_id, str) or not REQ_ID_RE.fullmatch(req_id):
            continue
        if req_id in spec_section4_ids and kind != "modify":
            _add_warning(
                warnings,
                f"requirements[{idx}].kind",
                f"requirement {req_id!r} already exists in SPEC.md section 4; kind should be 'modify'",
                rule="L-6",
            )
        elif kind == "modify" and req_id not in spec_section4_ids:
            _add_warning(
                warnings,
                f"requirements[{idx}].kind",
                f"requirement {req_id!r} has kind 'modify' but is not in SPEC.md section 4",
                rule="L-6",
            )


def _lint_l7_teams(data: dict[str, Any], errors: list[CompileIssue]) -> None:
    """L-7: When teams[] is non-empty, each step file must match exactly one team's owns."""
    teams = data.get("teams")
    if not isinstance(teams, list) or len(teams) == 0:
        return

    team_entries: list[tuple[str, list[str]]] = []
    for t_idx, team in enumerate(teams):
        if not isinstance(team, dict):
            continue
        team_id = team.get("id")
        owns = team.get("owns")
        if not isinstance(owns, list):
            continue
        tid = team_id if isinstance(team_id, str) and team_id.strip() else f"teams[{t_idx}]"
        patterns = [p for p in owns if isinstance(p, str) and p.strip()]
        team_entries.append((tid, patterns))

    if not team_entries:
        return

    steps = data.get("steps")
    if not isinstance(steps, list):
        return

    for s_idx, step in enumerate(steps):
        if not isinstance(step, dict):
            continue
        files = step.get("files")
        if not isinstance(files, list):
            continue
        for f_idx, file_path in enumerate(files):
            if not isinstance(file_path, str) or not file_path.strip():
                continue
            owners = [
                tid
                for tid, patterns in team_entries
                if any(_match_path(file_path, pat) for pat in patterns)
            ]
            if len(owners) == 0:
                _add_error(
                    errors,
                    f"steps[{s_idx}].files[{f_idx}]",
                    f"file {file_path!r} is not owned by any team",
                    rule="L-7",
                )
            elif len(owners) > 1:
                _add_error(
                    errors,
                    f"steps[{s_idx}].files[{f_idx}]",
                    f"file {file_path!r} is owned by multiple teams: {', '.join(owners)}",
                    rule="L-7",
                )


def lint_ir(
    data: dict[str, Any],
    errors: list[CompileIssue],
    warnings: list[CompileIssue],
    *,
    spec_section4_ids: set[str] | None = None,
) -> None:
    """Run deterministic CMP-3 lints on an IR dict."""
    _lint_l1_acceptance(data, errors)
    _lint_l5_forbids(data, errors)
    _lint_l6_spec_overlap(data, spec_section4_ids, warnings)
    _lint_l7_teams(data, errors)
    _lint_l8_traceability(data, warnings)
    _lint_l9_unresolved(data, errors)


def check_ir_file(
    ir_path: Path,
    work_dir: Path | None = None,
    spec_path: Path | None = None,
) -> tuple[int, dict[str, Any], list[CompileIssue]]:
    """Load and validate an IR JSON file.

    Returns (exit_code, json_payload, issues).
    Exit codes: 0 = OK, 1 = validation error, 2 = unreadable/invalid JSON/wrong schema.
    """
    issues: list[CompileIssue] = []
    root_dir = work_dir or Path.cwd()

    spec_section4_ids: set[str] | None = None
    target_spec: Path | None = spec_path
    explicit_spec = spec_path is not None
    if target_spec is None:
        candidate = root_dir / "SPEC.md"
        if candidate.exists():
            target_spec = candidate

    if target_spec is not None:
        try:
            if not target_spec.exists():
                raise FileNotFoundError(f"SPEC file not found: {target_spec}")
            if not target_spec.is_file():
                raise IsADirectoryError(f"SPEC path is not a regular file: {target_spec}")
            spec_text = target_spec.read_text(encoding="utf-8")
            spec_section4_ids = _parse_spec_section4_ids(spec_text)
        except (OSError, UnicodeDecodeError) as exc:
            if explicit_spec or target_spec.exists():
                _add_error(issues, str(target_spec), f"failed to read SPEC file: {exc}")
                payload_err: dict[str, Any] = {
                    "schema": CHECK_OUTPUT_SCHEMA_VERSION,
                    "errors": [i.to_dict() for i in issues],
                    "warnings": [],
                }
                return 2, payload_err, issues
    try:
        raw = ir_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        _add_error(issues, str(ir_path), f"failed to read IR file: {exc}")
        payload: dict[str, Any] = {
            "schema": CHECK_OUTPUT_SCHEMA_VERSION,
            "errors": [i.to_dict() for i in issues],
            "warnings": [],
        }
        return 2, payload, issues

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        _add_error(issues, str(ir_path), f"invalid JSON: {exc}")
        payload = {
            "schema": CHECK_OUTPUT_SCHEMA_VERSION,
            "errors": [i.to_dict() for i in issues],
            "warnings": [],
        }
        return 2, payload, issues

    if not isinstance(data, dict):
        _add_error(issues, "<root>", "IR JSON root must be an object")
        payload = {
            "schema": CHECK_OUTPUT_SCHEMA_VERSION,
            "errors": [i.to_dict() for i in issues],
            "warnings": [],
        }
        return 2, payload, issues

    if data.get("schema") != IR_SCHEMA_VERSION:
        _add_error(
            issues,
            "schema",
            f"expected {IR_SCHEMA_VERSION!r}, got {data.get('schema')!r}",
        )
        payload = {
            "schema": CHECK_OUTPUT_SCHEMA_VERSION,
            "errors": [i.to_dict() for i in issues],
            "warnings": [],
        }
        return 2, payload, issues

    errors, warnings = validate_ir_schema(data)
    validate_ir_sources(data, root_dir, errors, warnings)
    lint_ir(data, errors, warnings, spec_section4_ids=spec_section4_ids)
    all_issues = [*errors, *warnings]
    payload = {
        "schema": CHECK_OUTPUT_SCHEMA_VERSION,
        "errors": [e.to_dict() for e in errors],
        "warnings": [w.to_dict() for w in warnings],
    }
    exit_code = 1 if errors else 0
    return exit_code, payload, all_issues


def _map_heading_to_ir(title: str) -> str | None:
    """Map a markdown heading title to an IR element name using CMP-2 rules."""
    cleaned = _TRAILING_PAREN_RE.sub("", title).strip()
    cleaned = _LEADING_NUM_RE.sub("", cleaned).strip()
    lowered = cleaned.lower()
    for prefix, ir_element in _HEADING_IR_MAP:
        if lowered.startswith(prefix):
            return ir_element
    return None


def _extract_headings(text_lines: list[str]) -> list[tuple[int, int, str]]:
    """Extract (1-based line_no, level, title) outside fenced code blocks."""
    headings: list[tuple[int, int, str]] = []
    fence_char: str | None = None
    fence_len = 0

    for line_no, raw_line in enumerate(text_lines, start=1):
        stripped = raw_line.rstrip("\r\n")
        if fence_char is not None:
            close_match = _FENCE_CLOSE_RE.match(stripped)
            if close_match:
                marker = close_match.group(1)
                if marker[0] == fence_char and len(marker) >= fence_len:
                    fence_char = None
                    fence_len = 0
            continue

        open_match = _FENCE_OPEN_RE.match(stripped)
        if open_match:
            marker = open_match.group(1)
            info = open_match.group(2)
            if not (marker[0] == "`" and "`" in info):
                fence_char = marker[0]
                fence_len = len(marker)
                continue

        heading_match = _ATX_HEADING_RE.match(stripped)
        if heading_match:
            level = len(heading_match.group(1))
            title = re.sub(r"\s+#+\s*$", "", heading_match.group(2).strip()).strip()
            if title:
                headings.append((line_no, level, title))

    return headings


def outline_design_file(
    doc_path: Path,
    work_dir: Path | None = None,
) -> tuple[int, dict[str, Any], str | None]:
    """Parse headings from a design markdown file and map them to IR elements.

    Returns (exit_code, json_payload, error_message).
    Exit codes: 0 = all required sections found, 1 = missing required sections, 2 = unreadable.
    """
    resolved_root = (work_dir or Path.cwd()).resolve()
    try:
        resolved_doc = doc_path.resolve()
        display_path = (
            resolved_doc.relative_to(resolved_root).as_posix()
            if resolved_doc.is_relative_to(resolved_root)
            else str(doc_path)
        )
    except (OSError, RuntimeError):
        display_path = str(doc_path)

    try:
        if not doc_path.exists():
            raise FileNotFoundError(f"design file not found: {doc_path}")
        if not doc_path.is_file():
            raise IsADirectoryError(f"design path is not a regular file: {doc_path}")
        raw_bytes = doc_path.read_bytes()
        byte_lines = raw_bytes.splitlines(keepends=True)
        text_lines = [bl.decode("utf-8") for bl in byte_lines]
    except (OSError, UnicodeDecodeError) as exc:
        err = f"failed to read design file: {exc}"
        payload: dict[str, Any] = {
            "schema": OUTLINE_OUTPUT_SCHEMA_VERSION,
            "error": err,
            "source": {"path": display_path, "sha256": None},
            "sections": [],
            "missing": [],
        }
        return 2, payload, err

    file_sha = hashlib.sha256(raw_bytes).hexdigest()
    total_lines = len(byte_lines)
    headings = _extract_headings(text_lines)

    sections: list[dict[str, Any]] = []
    mapped_elements: set[str] = set()
    for idx, (start_line, level, title) in enumerate(headings):
        end_line = total_lines
        for next_line, next_level, _ in headings[idx + 1 :]:
            if next_level <= level:
                end_line = next_line - 1
                break
        slice_bytes = b"".join(byte_lines[start_line - 1 : end_line])
        section_sha = hashlib.sha256(slice_bytes).hexdigest()
        ir_element = _map_heading_to_ir(title)
        if ir_element is not None:
            mapped_elements.add(ir_element)
        sections.append(
            {
                "level": level,
                "title": title,
                "ir_element": ir_element,
                "lines": [start_line, end_line],
                "sha256": section_sha,
            }
        )

    missing: list[str] = []
    if "requirements" not in mapped_elements:
        missing.append("Goals")
    if "acceptance" not in mapped_elements:
        missing.append("Critical User Journeys")
    if "verification" not in mapped_elements:
        missing.append("Verification Plan")
    if "steps" not in mapped_elements:
        missing.append("Technical Plan / Detailed Implementation Plan")

    payload = {
        "schema": OUTLINE_OUTPUT_SCHEMA_VERSION,
        "source": {"path": display_path, "sha256": file_sha},
        "sections": sections,
        "missing": missing,
    }
    exit_code = 1 if missing else 0
    return exit_code, payload, None


def format_outline_text(payload: dict[str, Any]) -> list[str]:
    """Format outline payload as human-readable lines."""
    src = payload.get("source", {})
    lines = [f"Source: {src.get('path')} (sha256: {src.get('sha256')})"]
    for sec in payload.get("sections", []):
        start, end = sec["lines"]
        target = sec["ir_element"] if sec["ir_element"] is not None else "(unmapped)"
        lines.append(
            f"L{start}-L{end} [H{sec['level']}] {sec['title']} -> {target} (sha256: {sec['sha256']})"
        )
    for item in payload.get("missing", []):
        lines.append(f"MISSING: {item}")
    return lines


EmitLang = Literal["ja", "en"]


def render_draft_header(ir_filename: str, ir_sha256: str, lang: EmitLang = "ja") -> str:
    """Render the common human-readable draft header for emitted Markdown files (CMP-5)."""
    if lang == "en":
        return (
            "> **Note**: This file is a Spec Compiler draft. "
            "A human must review, approve, and copy it into `SPEC.md` and `PLAN.md` before committing (INV-12).\n"
            ">\n"
            f"> Source IR: `{ir_filename}` (`sha256:{ir_sha256}`)\n"
        )
    return (
        "> **注意**: このファイルは Spec Compiler の下書きです。"
        "人間が読んで承認し、人間が `SPEC.md`・`PLAN.md` に写してコミットしてください（INV-12）。\n"
        ">\n"
        f"> 元の IR: `{ir_filename}` (`sha256:{ir_sha256}`)\n"
    )


def _format_source_comment(src: Any) -> str | None:
    """Format a trace HTML comment from an IR source object (CMP-7/CMP-9)."""
    if not isinstance(src, dict):
        return None
    path = src.get("path")
    lines = src.get("lines")
    sha256 = src.get("sha256")
    if (
        not _is_valid_relative_path(path)
        or not _is_valid_line_range(lines)
        or not _is_non_empty_str(sha256)
    ):
        return None
    return f"<!-- source: {path}:L{lines[0]}-L{lines[1]} sha256:{sha256} -->"


def _extract_holder_text(item: Any) -> str:
    """Extract display text from an IR string or source-holder dict."""
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("statement", "text", "title", "description"):
            val = item.get(key)
            if isinstance(val, str) and val.strip():
                return val
    return ""


def render_spec_section(
    data: dict[str, Any],
    ir_filename: str,
    ir_sha256: str,
    lang: EmitLang = "ja",
) -> str:
    """Render `spec_section.md` draft from a validated IR dict (CMP-9)."""
    lines: list[str] = [
        render_draft_header(ir_filename, ir_sha256, lang=lang).rstrip("\n"),
        "",
        "### 5.1 （フェーズの名前）" if lang == "ja" else "### 5.1 (Phase Name)",
        "",
    ]

    ac_by_id: dict[str, dict[str, Any]] = {}
    acc_list = data.get("acceptance")
    if isinstance(acc_list, list):
        for item in acc_list:
            if isinstance(item, dict):
                ac_id = item.get("id")
                if isinstance(ac_id, str) and ac_id not in ac_by_id:
                    ac_by_id[ac_id] = item

    reqs = data.get("requirements")
    if isinstance(reqs, list):
        obs_label = "観測できる結果" if lang == "ja" else "Observable"
        for req in reqs:
            if not isinstance(req, dict):
                continue
            req_id = req.get("id", "")
            statement = req.get("statement", "")
            kind = req.get("kind", "new")
            if kind == "modify":
                marker = "（修正）" if lang == "ja" else " (modify)"
            else:
                marker = ""
            lines.append(f"- [ ] **{req_id}**{marker}: {statement}")

            src_comment = _format_source_comment(req.get("source"))
            if src_comment is not None:
                lines.append(f"  {src_comment}")

            acc_refs = req.get("acceptance")
            if isinstance(acc_refs, list):
                for ac_id in acc_refs:
                    if not isinstance(ac_id, str):
                        continue
                    ac_item = ac_by_id.get(ac_id)
                    if ac_item is None:
                        continue
                    lines.append(f"  - **{ac_id}**")
                    lines.append(f"    - Given: {ac_item.get('given', '')}")
                    lines.append(f"    - When: {ac_item.get('when', '')}")
                    lines.append(f"    - Then: {ac_item.get('then', '')}")
                    obs_raw = ac_item.get("observable")
                    obs_items = (
                        [x for x in obs_raw if isinstance(x, str) and x.strip()]
                        if isinstance(obs_raw, list)
                        else []
                    )
                    if len(obs_items) == 1:
                        lines.append(f"    - {obs_label}: {obs_items[0]}")
                    elif obs_items:
                        lines.append(f"    - {obs_label}:")
                        for obs in obs_items:
                            lines.append(f"      - {obs}")

    out_of_scope = data.get("out_of_scope")
    if isinstance(out_of_scope, list) and len(out_of_scope) > 0:
        lines.append("")
        lines.append(
            "## スコープ外（SPEC の 6 章に写す）"
            if lang == "ja"
            else "## Out of Scope (copy to SPEC section 6)"
        )
        lines.append("")
        for item in out_of_scope:
            text = _extract_holder_text(item)
            if text:
                lines.append(f"- {text}")
            if isinstance(item, dict):
                oos_comment = _format_source_comment(item.get("source"))
                if oos_comment is not None:
                    lines.append(f"  {oos_comment}")

    return "\n".join(lines) + "\n"


def resolve_emit_out_dir(
    ir_path: Path,
    work_dir: Path,
    out_dir: Path | None = None,
) -> tuple[Path | None, str | None]:
    """Resolve and validate --out-dir under <work_dir>/.dev/compile/ after resolving symlinks (INV-12)."""
    resolved_root = work_dir.resolve()
    compile_root = resolved_root / ".dev" / "compile"
    try:
        resolved_compile_root = compile_root.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"failed to resolve compile output root: {exc}"

    if not resolved_compile_root.is_relative_to(resolved_root):
        return None, f"compile output root escapes workspace root: {resolved_compile_root}"

    if out_dir is None:
        stem = ir_path.stem or "draft"
        candidate = compile_root / stem
    else:
        candidate = out_dir if out_dir.is_absolute() else (resolved_root / out_dir)

    try:
        resolved_out = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        return None, f"failed to resolve output directory {candidate}: {exc}"

    if resolved_out == resolved_compile_root or not resolved_out.is_relative_to(resolved_compile_root):
        return (
            None,
            f"output directory must be a subdirectory under {resolved_compile_root} (resolved: {resolved_out})",
        )

    if resolved_out.exists() and not resolved_out.is_dir():
        return None, f"output path exists and is not a directory: {resolved_out}"

    return resolved_out, None


def emit_ir_drafts(
    ir_path: Path,
    work_dir: Path | None = None,
    *,
    out_dir: Path | None = None,
    spec_path: Path | None = None,
    lang: EmitLang = "ja",
    force: bool = False,
) -> tuple[int, dict[str, Any], list[CompileIssue], str | None]:
    """Validate an IR file and emit deterministic draft artifacts under .dev/compile/ (CMP-5).

    Returns (exit_code, json_payload, issues, error_message).
    """
    resolved_root = (work_dir or Path.cwd()).resolve()
    resolved_out, out_err = resolve_emit_out_dir(ir_path, resolved_root, out_dir=out_dir)
    if out_err is not None or resolved_out is None:
        err_msg = out_err or "invalid output directory"
        return (
            2,
            {"error": err_msg, "files": []},
            [],
            err_msg,
        )

    check_exit, check_payload, issues = check_ir_file(
        ir_path, work_dir=resolved_root, spec_path=spec_path
    )
    if check_exit != 0:
        return check_exit, check_payload, issues, None

    try:
        has_existing = resolved_out.exists() and any(resolved_out.iterdir())
    except OSError as exc:
        err_msg = f"failed to inspect output directory {resolved_out}: {exc}"
        return (
            2,
            {"error": err_msg, "files": []},
            [],
            err_msg,
        )

    if has_existing and not force:
        err_msg = (
            f"output directory already contains files: {resolved_out} "
            "(use --force to overwrite generated files)"
        )
        return (
            2,
            {"error": err_msg, "files": []},
            [],
            err_msg,
        )

    try:
        ir_bytes = ir_path.read_bytes()
        ir_data = json.loads(ir_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        err_msg = f"failed to read IR file {ir_path}: {exc}"
        return (
            2,
            {"error": err_msg, "files": []},
            [],
            err_msg,
        )

    ir_sha256 = hashlib.sha256(ir_bytes).hexdigest()
    check_bytes = (json.dumps(check_payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    spec_section_bytes = render_spec_section(
        ir_data, ir_path.name, ir_sha256, lang=lang
    ).encode("utf-8")

    files_to_write: list[tuple[str, bytes]] = [
        ("ir.json", ir_bytes),
        ("check.json", check_bytes),
        ("spec_section.md", spec_section_bytes),
    ]
    written_paths: list[str] = []
    try:
        for rel_name, _ in files_to_write:
            target = resolved_out / rel_name
            if target.is_symlink():
                err_msg = f"refusing to write to symlink target: {target}"
                return 2, {"error": err_msg, "files": []}, [], err_msg
            if target.exists() and (target.is_dir() or not target.is_file()):
                err_msg = f"target path exists and is not a regular file: {target}"
                return 2, {"error": err_msg, "files": []}, [], err_msg

        resolved_out.mkdir(parents=True, exist_ok=True)
        for rel_name, content_bytes in files_to_write:
            target = resolved_out / rel_name
            target.write_bytes(content_bytes)
            written_paths.append(str(target))
    except OSError as exc:
        err_msg = f"failed to write draft output in {resolved_out}: {exc}"
        return 2, {"error": err_msg, "files": []}, [], err_msg

    payload: dict[str, Any] = {
        "out_dir": str(resolved_out),
        "lang": lang,
        "files": written_paths,
    }
    return 0, payload, issues, None
