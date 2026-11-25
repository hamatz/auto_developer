"""Spec Compiler (CMP-1..CMP-3): IR validation and design outline extraction."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

IR_SCHEMA_VERSION = "auto-dev-ir/1"
CHECK_OUTPUT_SCHEMA_VERSION = "auto-dev-compile-check/1"

REQ_ID_RE = re.compile(r"^[A-Z]+-[0-9]+$")
AC_ID_RE = re.compile(r"^AC-[0-9]+$")
DECISION_ID_RE = re.compile(r"^D-[0-9]+$")


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


def check_ir_file(ir_path: Path) -> tuple[int, dict[str, Any], list[CompileIssue]]:
    """Load and validate an IR JSON file.

    Returns (exit_code, json_payload, issues).
    Exit codes: 0 = OK, 1 = validation error, 2 = unreadable/invalid JSON/wrong schema.
    """
    issues: list[CompileIssue] = []
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
    all_issues = [*errors, *warnings]
    payload = {
        "schema": CHECK_OUTPUT_SCHEMA_VERSION,
        "errors": [e.to_dict() for e in errors],
        "warnings": [w.to_dict() for w in warnings],
    }
    exit_code = 1 if errors else 0
    return exit_code, payload, all_issues
