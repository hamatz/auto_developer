"""STAT-1: Repository status snapshot builder and formatter."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auto_developer.bundler import check_audit_diff_status
from auto_developer.compiler import IR_SCHEMA_VERSION, CompileIssue, validate_ir_sources
from auto_developer.dispatcher import TaskDispatcher, strip_html_comments_preserving_lines
from auto_developer.evidence import EvidenceStore, resolve_evidence_records_dir
from auto_developer.metrics import MetricsLog

STATUS_SCHEMA = "auto-dev-status/2"
_TASK_EVENTS = frozenset({"verify_step", "checker_review", "commit_step", "rollback_step"})
_INTEGRATION_EVENTS = frozenset({
    "integration_start",
    "integration_merge",
    "integration_reverify",
    "rc_tag",
    "integration_failed",
})
_TIMELINE_EVENTS = _TASK_EVENTS | {"task_start"} | _INTEGRATION_EVENTS
_BLOCKED_HEADING_RE = re.compile(r"^#{1,6}\s+.*(?:止まっていること|判断待ち|Blocked|Waiting)", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+")
_REFS_RE = re.compile(r"\[refs:\s*([^\]]+)\]")
_EMPTY_BLOCKED_PLACEHOLDERS = frozenset({"なし", "なし。", "none", "-"})
_TRAILING_REQ_PAREN_RE = re.compile(r"(?:\(([^()]*)\)|（([^（）]*)）)\s*$")
_REQ_ID_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Z]+-[0-9]+(?![A-Za-z0-9_])")


def _run_git_text(repo_dir: Path, args: list[str]) -> tuple[bool, str, str]:
    try:
        proc = subprocess.run(
            ["git"] + args,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        return proc.returncode == 0, proc.stdout.strip(), proc.stderr.strip()
    except OSError as exc:
        return False, "", str(exc)


def _extract_blocked_items(plan_path: Path) -> list[str]:
    if not plan_path.is_file():
        return []
    try:
        cleaned = strip_html_comments_preserving_lines(plan_path.read_text(encoding="utf-8"))
    except OSError:
        return []

    items: list[str] = []
    in_section = False
    for raw_line in cleaned.splitlines():
        line = raw_line.strip()
        if _BLOCKED_HEADING_RE.match(line):
            in_section = True
            continue
        if in_section and _ANY_HEADING_RE.match(line):
            in_section = False
            continue
        if in_section and line.startswith(("- ", "* ")):
            item_text = line[2:].strip()
            if item_text and not item_text.startswith("[") and item_text.lower() not in _EMPTY_BLOCKED_PLACEHOLDERS:
                items.append(item_text)
    return items


def _collect_commit_refs(repo_dir: Path) -> dict[str, str]:
    ok, out, _ = _run_git_text(repo_dir, ["log", "--format=%H\t%s"])
    refs: dict[str, str] = {}
    if not ok or not out:
        return refs
    for line in out.splitlines():
        if "\t" not in line:
            continue
        sha, subject = line.split("\t", 1)
        m = _REFS_RE.search(subject)
        if m:
            task_id = m.group(1).strip()
            if task_id and task_id not in refs:
                refs[task_id] = sha.strip()
    return refs


def _read_audit_stage(repo_dir: Path, head_sha: str | None) -> dict[str, Any]:
    audit_path = resolve_evidence_records_dir(repo_dir) / "audit_stage.json"
    if not audit_path.is_file():
        return {"present": False, "status": None, "reason": "audit_stage.json not found"}
    try:
        raw = json.loads(audit_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"present": True, "status": None, "reason": f"Failed to read audit_stage.json: {exc}"}
    if not isinstance(raw, dict):
        return {"present": True, "status": None, "reason": "audit_stage.json is not a JSON object"}

    findings = raw.get("findings")
    findings_count = len(findings) if isinstance(findings, list) else raw.get("findings_count")
    p0_p1_count = (
        sum(
            1
            for f in findings
            if isinstance(f, dict) and str(f.get("severity", "")).upper() in {"P0", "P1"}
        )
        if isinstance(findings, list)
        else 0
    )
    target_commit = raw.get("target_commit") or (
        raw.get("audit_metadata", {}).get("target_commit")
        if isinstance(raw.get("audit_metadata"), dict)
        else None
    )
    diff_status, diff_msg = check_audit_diff_status(
        repo_dir,
        str(target_commit) if target_commit is not None else None,
        head_sha,
    )
    outdated = diff_status != "OK"
    return {
        "present": True,
        "status": raw.get("status"),
        "mode": raw.get("mode"),
        "target_commit": target_commit,
        "findings_count": findings_count,
        "p0_p1_count": p0_p1_count,
        "outdated": outdated,
        "outdated_reason": diff_msg or None,
        "reason": None,
    }


def _ref_exists(repo_dir: Path, ref_name: str) -> bool:
    ok, _, _ = _run_git_text(repo_dir, ["show-ref", "--verify", "--quiet", ref_name])
    return ok


def _collect_team_failure_reason(
    team: dict[str, Any],
    wt_path: Path,
    wt_exists: bool,
) -> str | None:
    reasons: list[str] = []
    worker_error = str(team.get("worker_error") or "").strip()
    if worker_error:
        reasons.append(worker_error)

    if wt_exists:
        logs_dir = wt_path / "logs" / "nightly"
        if logs_dir.is_dir():
            for log_file in sorted(logs_dir.glob("task_*_failure.log")):
                try:
                    lines = log_file.read_text(encoding="utf-8", errors="replace").splitlines()
                except OSError:
                    continue
                phase = ""
                reason = ""
                for raw_line in lines[:10]:
                    stripped = raw_line.strip()
                    if stripped.startswith("Failure Phase:"):
                        phase = stripped[len("Failure Phase:"):].strip()
                    elif stripped.startswith("Reason:"):
                        reason = stripped[len("Reason:"):].strip()
                parts = []
                if phase:
                    parts.append(f"Failure Phase: {phase}")
                if reason:
                    parts.append(f"Reason: {reason}")
                if parts:
                    entry = ", ".join(parts)
                    if entry not in reasons:
                        reasons.append(entry)
    return "; ".join(reasons) if reasons else None


def _count_review_inputs_for_evidence(
    events: list[dict[str, Any]],
    task_id: str,
    evidence: dict[str, Any],
    *,
    current_phase: str = "unknown",
) -> int:
    """Count review_input events associated with the given evidence record."""
    target_issued_at = evidence.get("issued_at")
    if target_issued_at is None:
        return 0
    target_checkpoint = str(evidence.get("checkpoint", "")).strip()
    count = 0
    for ev in events:
        if ev.get("event") != "review_input":
            continue
        ev_phase = ev.get("phase")
        if current_phase != "unknown" and ev_phase and ev_phase != "unknown" and ev_phase != current_phase:
            continue
        if str(ev.get("task_id", "")).strip() != str(task_id).strip():
            continue
        if ev.get("evidence_issued_at") != target_issued_at:
            continue
        ev_cp = str(ev.get("checkpoint", "")).strip()
        if target_checkpoint and ev_cp and ev_cp != target_checkpoint:
            continue
        count += 1
    return count


def _check_review_not_recorded(
    *,
    ev_store: EvidenceStore,
    events: list[dict[str, Any]],
    task_id: str,
    latest_evidence: dict[str, Any] | None,
    attention: list[dict[str, Any]],
    current_phase: str = "unknown",
    team_id: str | None = None,
) -> None:
    """Append REVIEW_NOT_RECORDED to attention if review_input count exceeds review history."""
    if latest_evidence is None:
        return
    review_input_count = _count_review_inputs_for_evidence(
        events,
        task_id,
        latest_evidence,
        current_phase=current_phase,
    )
    if review_input_count <= 0:
        return
    review_history_count = ev_store.count_review_history_for_evidence(task_id, latest_evidence)
    if review_input_count > review_history_count:
        missing_reviews = review_input_count - review_history_count
        prefix = f"Team '{team_id}' task {task_id}" if team_id else f"Task {task_id}"
        item: dict[str, Any] = {
            "kind": "REVIEW_NOT_RECORDED",
            "severity": "HIGH",
            "task_id": task_id,
            "review_input_count": review_input_count,
            "review_history_count": review_history_count,
            "message": (
                f"{prefix} has {review_input_count} review_input event(s) "
                f"but only {review_history_count} recorded review(s) "
                f"({missing_reviews} unrecorded)"
            ),
        }
        if team_id:
            item["team_id"] = team_id
        attention.append(item)


def _collect_teams_snapshot(
    repo_dir: Path,
    attention: list[dict[str, Any]],
    seen_quarantines: set[str] | None = None,
) -> dict[str, Any] | None:
    if not (repo_dir / "teams.json").is_file():
        return None

    from auto_developer.multi_team import get_teams_status

    payload, rc, err = get_teams_status(repo_dir)
    if rc != 0 or payload is None:
        reason = err or "Failed to read teams status"
        attention.append({
            "kind": "TEAMS_STATUS_ERROR",
            "severity": "HIGH",
            "task_id": None,
            "message": reason,
        })
        return {
            "job_id": None,
            "job_state": None,
            "integration": None,
            "teams": [],
            "reason": reason,
        }

    job_id = payload.get("job_id")
    job_state = payload.get("job_state")
    raw_rc_tag = payload.get("rc_tag")
    rc_tag_name = str(raw_rc_tag).removeprefix("refs/tags/") if raw_rc_tag else (f"rc/{job_id}" if job_id else None)
    int_branch = f"integration/{job_id}" if job_id else None
    integration = {
        "integration_branch": int_branch,
        "integration_branch_exists": bool(int_branch and _ref_exists(repo_dir, f"refs/heads/{int_branch}")),
        "rc_tag": rc_tag_name,
        "rc_tag_exists": bool(rc_tag_name and _ref_exists(repo_dir, f"refs/tags/{rc_tag_name}")),
    }

    enriched_teams: list[dict[str, Any]] = []
    for team in payload.get("teams") or []:
        tid = str(team.get("team_id", ""))
        wt_path = Path(str(team.get("worktree_path", "")))
        wt_exists = bool(team.get("worktree_exists")) and wt_path.is_dir()
        worker_status = str(team.get("worker_status") or "UNKNOWN")
        quarantines = list(team.get("quarantine_branches") or [])
        failed_tasks = int(team.get("failed_tasks") or 0)

        ev_records: list[dict[str, Any]] = []
        if wt_exists:
            wt_store = EvidenceStore(repo_dir=wt_path)
            wt_records_by_task: dict[str, dict[str, Any]] = {}
            for rec in wt_store.get_records():
                rec_tid = str(rec.get("task_id", "")).strip()
                if rec_tid and rec_tid not in wt_records_by_task:
                    wt_records_by_task[rec_tid] = rec
                ev_records.append({
                    "task_id": rec.get("task_id"),
                    "tree_sha": rec.get("tree_sha"),
                    "commit": rec.get("commit"),
                    "status": rec.get("status"),
                })
            wt_task_ids: set[str] = set(wt_records_by_task.keys())
            if wt_store.evidence_dir.is_dir():
                for p in sorted(wt_store.evidence_dir.glob("task_*.json")):
                    stem_tid = p.stem[len("task_"):]
                    if stem_tid:
                        wt_task_ids.add(stem_tid)
            if wt_task_ids:
                wt_metrics = MetricsLog(repo_dir=wt_path)
                wt_events = wt_metrics.read_all_events()
                wt_phase = wt_metrics.get_current_phase()
                for wt_tid in sorted(wt_task_ids):
                    wt_latest = wt_store.get_evidence(wt_tid) or wt_records_by_task.get(wt_tid)
                    _check_review_not_recorded(
                        ev_store=wt_store,
                        events=wt_events,
                        task_id=wt_tid,
                        latest_evidence=wt_latest,
                        attention=attention,
                        current_phase=wt_phase,
                        team_id=tid,
                    )

        failure_reason = _collect_team_failure_reason(team, wt_path, wt_exists)

        enriched_teams.append({
            **team,
            "no_changes": worker_status == "NO_CHANGES",
            "failure_reason": failure_reason,
            "evidence_records": ev_records,
        })

        if failed_tasks > 0 or worker_status in {"FAILED", "STALE", "TIMEOUT", "CRASHED"}:
            msg = f"Team '{tid}' status={worker_status}, failed_tasks={failed_tasks}"
            if failure_reason:
                msg += f", reason={failure_reason}"
            attention.append({
                "kind": "TEAM_FAILURE",
                "severity": "HIGH",
                "task_id": None,
                "message": msg,
            })
        for qb in quarantines:
            if seen_quarantines is not None:
                if qb in seen_quarantines:
                    continue
                seen_quarantines.add(qb)
            attention.append({
                "kind": "TEAM_QUARANTINE",
                "severity": "HIGH",
                "task_id": None,
                "quarantine_branch": qb,
                "message": f"Team '{tid}' quarantine branch: {qb}",
            })

    if job_state == "FAILED":
        attention.append({
            "kind": "TEAM_JOB_FAILED",
            "severity": "HIGH",
            "task_id": None,
            "message": f"Team job '{job_id}' is in FAILED state",
        })

    return {
        "job_id": job_id,
        "job_state": job_state,
        "integration": integration,
        "teams": enriched_teams,
        "reason": None,
    }


def _to_finite_float(raw: Any) -> float | None:
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        try:
            val = float(raw)
        except OverflowError:
            return None
        return val if math.isfinite(val) else None
    return None


def _format_utc_iso(ts_sec: float) -> str | None:
    try:
        return datetime.fromtimestamp(ts_sec, tz=timezone.utc).isoformat()
    except (OverflowError, OSError, ValueError):
        return None


def _parse_event_ts(ev: dict[str, Any]) -> tuple[float, str | None]:
    raw_ts_ms = _to_finite_float(ev.get("ts_ms"))
    if raw_ts_ms is not None:
        ts_sec = raw_ts_ms / 1000.0
        if math.isfinite(ts_sec):
            return ts_sec, _format_utc_iso(ts_sec)
    raw_ts = _to_finite_float(ev.get("ts"))
    if raw_ts is not None:
        return raw_ts, _format_utc_iso(raw_ts)
    raw_iso = ev.get("timestamp")
    if isinstance(raw_iso, str) and raw_iso.strip():
        try:
            dt = datetime.fromisoformat(raw_iso.strip())
            return dt.timestamp(), raw_iso.strip()
        except ValueError:
            return 0.0, raw_iso.strip()
    return 0.0, None


def _compute_commit_numstat(
    repo_dir: Path,
    commit_sha: str,
    cache: dict[str, tuple[int | None, int | None]],
) -> tuple[int | None, int | None]:
    """Compute (additions, deletions) for a non-merge commit via `git show --numstat`."""
    if commit_sha in cache:
        return cache[commit_sha]

    ok_p, parents_out, _ = _run_git_text(repo_dir, ["show", "-s", "--format=%P", commit_sha])
    if not ok_p:
        cache[commit_sha] = (None, None)
        return (None, None)
    # Merge commits (2+ parents) are not counted.
    if len(parents_out.split()) > 1:
        cache[commit_sha] = (None, None)
        return (None, None)

    ok_stat, stat_out, _ = _run_git_text(repo_dir, ["show", "--numstat", "--format=", commit_sha])
    if not ok_stat:
        cache[commit_sha] = (None, None)
        return (None, None)

    additions = 0
    deletions = 0
    has_numeric = False
    for line in stat_out.splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        add_s, del_s = parts[0].strip(), parts[1].strip()
        if add_s.isdigit():
            additions += int(add_s)
            has_numeric = True
        if del_s.isdigit():
            deletions += int(del_s)
            has_numeric = True

    result = (additions, deletions) if has_numeric else (None, None)
    cache[commit_sha] = result
    return result


def _read_team_plan_tasks(
    repo_dir: Path,
    team: dict[str, Any],
    dispatcher: TaskDispatcher,
) -> list[dict[str, str]]:
    tid = str(team.get("team_id") or "")
    wt_path = Path(str(team.get("worktree_path") or ""))
    wt_exists = bool(team.get("worktree_exists")) and wt_path.is_dir()
    plan_rel = str(team.get("plan") or f"plans/{tid}.md")
    branch = str(team.get("branch") or f"team/{tid}")
    branch_exists = bool(team.get("branch_exists"))

    content = ""
    wt_plan = wt_path / plan_rel
    if wt_exists and wt_plan.is_file():
        try:
            content = wt_plan.read_text(encoding="utf-8")
        except OSError:
            content = ""
    elif branch_exists:
        ok, out, _ = _run_git_text(repo_dir, ["show", f"{branch}:{plan_rel}"])
        if ok:
            content = out
    if not content and (repo_dir / plan_rel).is_file():
        try:
            content = (repo_dir / plan_rel).read_text(encoding="utf-8")
        except OSError:
            content = ""

    tasks = dispatcher.parse_plan_text(content) if content else []
    return [
        {
            "task_id": t.task_id,
            "title": t.title,
            "status": "failed" if t.is_failed else ("completed" if t.is_completed else "pending"),
        }
        for t in tasks
    ]


def _build_backlog(
    repo_dir: Path,
    tasks_raw: list[Any],
    teams_info: dict[str, Any] | None,
    dispatcher: TaskDispatcher,
) -> dict[str, list[dict[str, str]]]:
    if isinstance(teams_info, dict):
        backlog: dict[str, list[dict[str, str]]] = {}
        for tm in teams_info.get("teams") or []:
            tid = str(tm.get("team_id") or "").strip()
            if tid:
                backlog[tid] = _read_team_plan_tasks(repo_dir, tm, dispatcher)
        return backlog

    return {
        "plan": [
            {
                "task_id": t.task_id,
                "title": t.title,
                "status": "failed" if t.is_failed else ("completed" if t.is_completed else "pending"),
            }
            for t in tasks_raw
        ]
    }


def _extract_requirement_ids_from_title(title: str) -> list[str]:
    """Extract requirement IDs ([A-Z]+-[0-9]+) from the last trailing parentheses of a task title."""
    match = _TRAILING_REQ_PAREN_RE.search(title.rstrip())
    if match is None:
        return []
    inner = match.group(1) if match.group(1) is not None else match.group(2)
    if not inner:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for token in _REQ_ID_TOKEN_RE.findall(inner):
        if token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _collect_design_irs_and_ir_reqs(
    repo_dir: Path,
    attention: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    """Read approved IRs from docs/designs/*.ir.json, verify CMP-1 sources, and collect requirement metadata."""
    designs_dir = repo_dir / "docs" / "designs"
    if not designs_dir.is_dir():
        return [], {}

    design_irs: list[dict[str, Any]] = []
    ir_reqs: dict[str, dict[str, Any]] = {}

    for ir_path in sorted(designs_dir.glob("*.ir.json")):
        if not ir_path.is_file():
            continue
        rel_path = ir_path.relative_to(repo_dir).as_posix()
        try:
            raw_bytes = ir_path.read_bytes()
        except OSError as exc:
            reason = f"Failed to read IR file: {exc}"
            design_irs.append({
                "path": rel_path,
                "sha256": None,
                "status": "error",
                "mismatches": [],
                "reason": reason,
            })
            continue

        file_sha256 = hashlib.sha256(raw_bytes).hexdigest()
        try:
            data = json.loads(raw_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            reason = f"Failed to parse IR JSON: {exc}"
            design_irs.append({
                "path": rel_path,
                "sha256": file_sha256,
                "status": "error",
                "mismatches": [],
                "reason": reason,
            })
            continue

        if not isinstance(data, dict) or data.get("schema") != IR_SCHEMA_VERSION:
            got_schema = data.get("schema") if isinstance(data, dict) else type(data).__name__
            reason = f"Unexpected IR schema {got_schema!r} (expected {IR_SCHEMA_VERSION!r})"
            design_irs.append({
                "path": rel_path,
                "sha256": file_sha256,
                "status": "error",
                "mismatches": [],
                "reason": reason,
            })
            continue

        errors: list[CompileIssue] = []
        warnings: list[CompileIssue] = []
        validate_ir_sources(data, repo_dir, errors, warnings)
        mismatches = [issue.to_dict() for issue in errors]

        if mismatches:
            ir_status = "drift"
            attention.append({
                "kind": "DESIGN_DRIFT",
                "severity": "MEDIUM",
                "task_id": None,
                "design_ir": rel_path,
                "mismatches": mismatches,
                "message": (
                    f"Design IR '{rel_path}' has {len(mismatches)} source mismatch(es) "
                    "with design document"
                ),
            })
        else:
            ir_status = "ok"

        design_irs.append({
            "path": rel_path,
            "sha256": file_sha256,
            "status": ir_status,
            "mismatches": mismatches,
            "reason": None,
        })

        reqs_raw = data.get("requirements")
        if isinstance(reqs_raw, list):
            for req in reqs_raw:
                if not isinstance(req, dict):
                    continue
                req_id = req.get("id")
                if not isinstance(req_id, str) or not req_id.strip():
                    continue
                req_id = req_id.strip()
                if req_id not in ir_reqs:
                    stmt = req.get("statement")
                    src = req.get("source")
                    ir_reqs[req_id] = {
                        "statement": stmt if isinstance(stmt, str) else None,
                        "source": src if isinstance(src, dict) else None,
                    }

    return design_irs, ir_reqs


def _build_requirements_snapshot(
    backlog: dict[str, list[dict[str, str]]],
    ir_reqs: dict[str, dict[str, Any]],
    *,
    is_teams: bool,
) -> list[dict[str, Any]]:
    """Combine plan task requirement IDs and approved IR requirements into STAT-11 requirements[]."""
    tasks_by_req: dict[str, list[dict[str, Any]]] = {}
    for group_key, task_list in backlog.items():
        for t in task_list:
            req_ids = _extract_requirement_ids_from_title(str(t.get("title") or ""))
            for rid in req_ids:
                task_ref: dict[str, Any] = {
                    "task_id": t["task_id"],
                    "title": t["title"],
                    "status": t["status"],
                }
                if is_teams:
                    task_ref["team"] = group_key
                tasks_by_req.setdefault(rid, []).append(task_ref)

    ordered_ids: list[str] = []
    seen_ids: set[str] = set()
    for rid in (*ir_reqs.keys(), *tasks_by_req.keys()):
        if rid not in seen_ids:
            seen_ids.add(rid)
            ordered_ids.append(rid)

    requirements: list[dict[str, Any]] = []
    for rid in ordered_ids:
        meta = ir_reqs.get(rid) or {}
        ref_tasks = tasks_by_req.get(rid, [])
        done_count = sum(1 for t in ref_tasks if t.get("status") == "completed")
        if ref_tasks and done_count == len(ref_tasks):
            req_status = "done"
        elif done_count > 0:
            req_status = "in_progress"
        else:
            req_status = "pending"

        requirements.append({
            "id": rid,
            "statement": meta.get("statement"),
            "source": meta.get("source"),
            "tasks": ref_tasks,
            "status": req_status,
        })
    return requirements


def _collect_timeline(
    repo_dir: Path,
    all_main_events: list[dict[str, Any]],
    current_phase: str,
    plan_task_ids: set[str],
    teams_info: dict[str, Any] | None,
    backlog: dict[str, list[dict[str, str]]],
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Build chronologically ordered STAT-4 timeline scoped to the latest job or current plan."""
    candidates: list[tuple[float, int, dict[str, Any]]] = []
    seq = 0

    if isinstance(teams_info, dict):
        latest_job_id = teams_info.get("job_id")
        team_ids = {
            str(tm.get("team_id"))
            for tm in (teams_info.get("teams") or [])
            if tm.get("team_id")
        }
        for ev in all_main_events:
            ev_name = str(ev.get("event") or "")
            if ev_name not in _TIMELINE_EVENTS:
                continue
            if not latest_job_id or ev.get("job_id") != latest_job_id:
                continue
            if ev_name not in _INTEGRATION_EVENTS and ev.get("team") not in team_ids:
                continue
            ts_num, _ = _parse_event_ts(ev)
            candidates.append((ts_num, seq, ev))
            seq += 1

        for tm in teams_info.get("teams") or []:
            tid = str(tm.get("team_id") or "").strip()
            if not tid:
                continue
            wt_path = Path(str(tm.get("worktree_path") or ""))
            if not wt_path.is_dir():
                continue
            team_task_ids = {t["task_id"] for t in backlog.get(tid, [])}
            wt_events = MetricsLog(repo_dir=wt_path, team_id=tid).read_all_events()
            for ev in wt_events:
                ev_name = str(ev.get("event") or "")
                if ev_name not in _TIMELINE_EVENTS or ev_name in _INTEGRATION_EVENTS:
                    continue
                ev_tid = ev.get("task_id")
                if team_task_ids and ev_tid is not None and str(ev_tid) not in team_task_ids:
                    continue
                normalized_ev = ev if ev.get("team") else {**ev, "team": tid}
                ts_num, _ = _parse_event_ts(normalized_ev)
                candidates.append((ts_num, seq, normalized_ev))
                seq += 1
    else:
        for ev in all_main_events:
            ev_name = str(ev.get("event") or "")
            if ev_name not in _TIMELINE_EVENTS or ev_name in _INTEGRATION_EVENTS:
                continue
            ev_tid = str(ev.get("task_id") or "").strip()
            if not ev_tid or ev_tid not in plan_task_ids:
                continue
            ev_phase = ev.get("phase")
            if current_phase != "unknown" and ev_phase and ev_phase != "unknown" and ev_phase != current_phase:
                continue
            ts_num, _ = _parse_event_ts(ev)
            candidates.append((ts_num, seq, ev))
            seq += 1

    candidates.sort(key=lambda item: (item[0], item[1]))

    numstat_cache: dict[str, tuple[int | None, int | None]] = {}
    seen_events: set[tuple[Any, ...]] = set()
    timeline: list[dict[str, Any]] = []

    for _, _, ev in candidates:
        ev_name = str(ev.get("event") or "")
        team_val = str(ev["team"]) if ev.get("team") else None
        task_val = str(ev["task_id"]) if ev.get("task_id") is not None else None
        commit_raw = ev.get("commit")
        commit_sha = str(commit_raw).strip() if commit_raw else None

        dedup_key = (
            ev_name,
            team_val,
            task_val,
            ev.get("ts"),
            ev.get("ts_ms"),
            ev.get("timestamp"),
            ev.get("attempt"),
            ev.get("result"),
            ev.get("decision"),
            commit_sha,
            ev.get("tag"),
        )
        if dedup_key in seen_events:
            continue
        seen_events.add(dedup_key)

        _, iso_ts = _parse_event_ts(ev)
        if commit_sha:
            additions, deletions = _compute_commit_numstat(repo_dir, commit_sha, numstat_cache)
        else:
            additions, deletions = (None, None)

        entry: dict[str, Any] = {
            "ts": ev.get("ts"),
            "timestamp": iso_ts,
            "event": ev_name,
            "team": team_val,
            "task_id": task_val,
            "attempt": ev.get("attempt"),
            "result": ev.get("result"),
            "decision": ev.get("decision"),
            "commit": commit_sha,
            "additions": additions,
            "deletions": deletions,
        }
        for opt_key in (
            "ts_ms",
            "tag",
            "reason",
            "exit_code",
            "job_id",
            "model",
            "checker_model",
            "maker_model",
            "quarantine_branch",
        ):
            if opt_key in ev and ev[opt_key] is not None:
                entry[opt_key] = ev[opt_key]
        timeline.append(entry)

    timeline_start = next((e["timestamp"] for e in timeline if e.get("timestamp") is not None), None)
    timeline_end = next((e["timestamp"] for e in reversed(timeline) if e.get("timestamp") is not None), None)
    return timeline, timeline_start, timeline_end


def collect_status_snapshot(
    repo_dir: Path,
    plan_path: Path | None = None,
) -> dict[str, Any]:
    """Build STAT-1 status snapshot dictionary for single-team or multi-team repositories."""
    resolved_repo = repo_dir.resolve()
    effective_plan = (plan_path or (resolved_repo / "PLAN.md")).resolve()

    ok_head, head_out, head_err = _run_git_text(resolved_repo, ["rev-parse", "HEAD"])
    head_sha: str | None = head_out if ok_head and head_out else None

    ok_br, br_out, br_err = _run_git_text(resolved_repo, ["rev-parse", "--abbrev-ref", "HEAD"])
    branch: str | None = br_out if ok_br and br_out else None
    repo_reason = None
    if head_sha is None:
        repo_reason = head_err or "Cannot resolve HEAD"
    elif branch is None:
        repo_reason = br_err or "Cannot resolve branch"

    # Quarantine branches
    ok_q, q_out, _ = _run_git_text(resolved_repo, ["branch", "--list", "quarantine/*"])
    quarantine_branches = [
        line.lstrip("* ").strip()
        for line in q_out.splitlines()
        if line.strip()
    ] if ok_q and q_out else []

    # Plan tasks
    dispatcher = TaskDispatcher()
    plan_reason: str | None = None
    tasks_raw = []
    if effective_plan.is_file():
        try:
            tasks_raw = dispatcher.parse_plan(effective_plan)
        except OSError as exc:
            plan_reason = f"Failed to read {effective_plan.name}: {exc}"
    else:
        plan_reason = f"{effective_plan.name} not found"

    blocked_items = _extract_blocked_items(effective_plan)
    commit_refs = _collect_commit_refs(resolved_repo)

    # Evidence records
    ev_store = EvidenceStore(repo_dir=resolved_repo)
    records_by_task: dict[str, dict[str, Any]] = {}
    for rec in ev_store.get_records():
        tid = str(rec.get("task_id", "")).strip()
        if tid and tid not in records_by_task:
            records_by_task[tid] = rec

    # Metrics events
    metrics = MetricsLog(repo_dir=resolved_repo, plan_path=effective_plan)
    all_events = metrics.read_all_events()
    current_phase = metrics.get_current_phase()
    events_by_task: dict[str, list[dict[str, Any]]] = {}
    for ev in all_events:
        if ev.get("event") not in _TASK_EVENTS:
            continue
        ev_phase = ev.get("phase")
        if current_phase != "unknown" and ev_phase and ev_phase != "unknown" and ev_phase != current_phase:
            continue
        tid = str(ev.get("task_id", "")).strip()
        if not tid:
            continue
        events_by_task.setdefault(tid, []).append({
            "event": ev.get("event"),
            "timestamp": ev.get("timestamp"),
            "result": ev.get("result"),
            "decision": ev.get("decision"),
            "model": ev.get("model") or ev.get("checker_model"),
            "maker_model": ev.get("maker_model"),
        })

    attention: list[dict[str, Any]] = []
    task_entries: list[dict[str, Any]] = []

    for t in tasks_raw:
        status_str = "failed" if t.is_failed else ("completed" if t.is_completed else "pending")
        ev_rec = records_by_task.get(t.task_id)
        review_rec = ev_store.get_review_record(t.task_id)
        raw_checker = (ev_rec or {}).get("checker")
        checker_meta: dict[str, Any] = raw_checker if isinstance(raw_checker, dict) else {}
        checker_decision = (
            checker_meta.get("decision")
            or (review_rec or {}).get("decision")
        )
        checker_model = (
            checker_meta.get("model")
            or (review_rec or {}).get("checker_model")
        )
        maker_model = (
            checker_meta.get("maker_model")
            or (review_rec or {}).get("maker_model")
        )
        commit_sha = commit_refs.get(t.task_id) or (ev_rec or {}).get("commit")
        task_events = events_by_task.get(t.task_id, [])

        has_evidence = ev_rec is not None
        has_commit_ref = t.task_id in commit_refs
        has_review = bool(review_rec is not None or checker_decision)

        task_entries.append({
            "task_id": t.task_id,
            "title": t.title,
            "status": status_str,
            "events": task_events,
            "evidence": {
                "present": has_evidence,
                "commit": commit_sha,
                "has_review": has_review,
                "checker_decision": checker_decision,
                "checker_model": checker_model,
                "maker_model": maker_model,
            },
            "commit_ref": {
                "found": has_commit_ref,
                "sha": commit_refs.get(t.task_id),
            },
        })

        if t.is_completed and (not has_commit_ref or not has_evidence):
            missing_parts = []
            if not has_commit_ref:
                missing_parts.append(f"[refs: {t.task_id}] commit")
            if not has_evidence:
                missing_parts.append("Evidence record")
            attention.append({
                "kind": "PLAN_EVIDENCE_MISMATCH",
                "severity": "HIGH",
                "task_id": t.task_id,
                "message": f"Task {t.task_id} is marked [x] in PLAN.md but missing: {', '.join(missing_parts)}",
            })
        if t.is_failed:
            attention.append({
                "kind": "FAILED_TASK",
                "severity": "HIGH",
                "task_id": t.task_id,
                "message": f"Task {t.task_id} is marked [FAILED]: {t.title}",
            })
        if not t.is_completed:
            for ev in task_events:
                if ev.get("event") == "verify_step" and ev.get("result") not in (None, "PASSED"):
                    attention.append({
                        "kind": "VERIFY_FAILURE",
                        "severity": "HIGH",
                        "task_id": t.task_id,
                        "message": f"Task {t.task_id} verification failed: {ev.get('result')}",
                    })
                if ev.get("event") == "checker_review" and str(ev.get("decision", "")).upper() == "REJECT":
                    attention.append({
                        "kind": "CHECKER_REJECT",
                        "severity": "MEDIUM",
                        "task_id": t.task_id,
                        "message": f"Task {t.task_id} was rejected by Checker ({ev.get('model') or 'unknown'})",
                    })
        _check_review_not_recorded(
            ev_store=ev_store,
            events=all_events,
            task_id=t.task_id,
            latest_evidence=ev_store.get_evidence(t.task_id) or ev_rec,
            attention=attention,
            current_phase=current_phase,
        )

    seen_quarantines: set[str] = set()
    teams_info = _collect_teams_snapshot(resolved_repo, attention, seen_quarantines=seen_quarantines)

    for q_br in quarantine_branches:
        if q_br in seen_quarantines:
            continue
        seen_quarantines.add(q_br)
        attention.append({
            "kind": "QUARANTINE_BRANCH",
            "severity": "HIGH",
            "task_id": None,
            "quarantine_branch": q_br,
            "message": f"Quarantine branch present: {q_br}",
        })

    for b_item in blocked_items:
        attention.append({
            "kind": "BLOCKED_ITEM",
            "severity": "HIGH",
            "task_id": None,
            "message": f"Blocked / awaiting decision: {b_item}",
        })

    audit_info = _read_audit_stage(resolved_repo, head_sha)
    if audit_info.get("present"):
        p0_p1 = audit_info.get("p0_p1_count") or 0
        if p0_p1 > 0:
            attention.append({
                "kind": "AUDIT_P0_P1",
                "severity": "HIGH",
                "task_id": None,
                "message": f"Independent audit has {p0_p1} P0/P1 finding(s)",
            })
        if audit_info.get("outdated"):
            outdated_msg = str(
                audit_info.get("outdated_reason")
                or f"Independent audit target ({audit_info.get('target_commit')}) is older than HEAD ({head_sha})"
            )
            attention.append({
                "kind": "AUDIT_OUTDATED",
                "severity": "MEDIUM",
                "task_id": None,
                "message": outdated_msg,
            })

    plan_task_ids = {t.task_id for t in tasks_raw}
    backlog = _build_backlog(resolved_repo, tasks_raw, teams_info, dispatcher)
    design_irs, ir_reqs = _collect_design_irs_and_ir_reqs(resolved_repo, attention)
    requirements = _build_requirements_snapshot(
        backlog,
        ir_reqs,
        is_teams=isinstance(teams_info, dict),
    )
    timeline, timeline_start, timeline_end = _collect_timeline(
        repo_dir=resolved_repo,
        all_main_events=all_events,
        current_phase=current_phase,
        plan_task_ids=plan_task_ids,
        teams_info=teams_info,
        backlog=backlog,
    )

    timeline_quarantines = {
        str(ev["quarantine_branch"]).strip()
        for ev in timeline
        if ev.get("event") == "rollback_step" and ev.get("quarantine_branch")
    }
    for item in attention:
        if item.get("kind") in ("QUARANTINE_BRANCH", "TEAM_QUARANTINE"):
            q_br = str(item.get("quarantine_branch") or "").strip()
            in_timeline = bool(q_br and q_br in timeline_quarantines)
            item["scope"] = "current" if in_timeline else "previous"
            suffix = " (current timeline)" if in_timeline else " (previous run)"
            if not str(item.get("message") or "").endswith(suffix):
                item["message"] = f"{item.get('message', '')}{suffix}"

    return {
        "schema": STATUS_SCHEMA,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "repo": {
            "head": head_sha,
            "branch": branch,
            "reason": repo_reason,
        },
        "plan": {
            "path": str(effective_plan.relative_to(resolved_repo))
            if effective_plan.is_relative_to(resolved_repo)
            else str(effective_plan),
            "tasks": task_entries,
            "blocked_items": blocked_items,
            "reason": plan_reason,
        },
        "quarantine_branches": quarantine_branches,
        "audit": audit_info,
        "teams": teams_info,
        "attention": attention,
        "timeline": timeline,
        "timeline_start": timeline_start,
        "timeline_end": timeline_end,
        "backlog": backlog,
        "requirements": requirements,
        "design_irs": design_irs,
    }


def format_status_text(snapshot: dict[str, Any]) -> str:
    """Format a STAT-1 status snapshot for human-readable terminal output."""
    repo = snapshot.get("repo") or {}
    plan = snapshot.get("plan") or {}
    tasks = plan.get("tasks") or []
    attention = snapshot.get("attention") or []
    quarantine = snapshot.get("quarantine_branches") or []
    audit = snapshot.get("audit") or {}

    lines = [
        f"=== Auto Developer Status ({snapshot.get('schema')}) ===",
        f"HEAD:   {repo.get('head') or 'null'} (branch: {repo.get('branch') or 'null'})",
        f"Plan:   {plan.get('path')} ({len(tasks)} tasks)",
    ]
    if repo.get("reason"):
        lines.append(f"Repo Note: {repo['reason']}")
    if plan.get("reason"):
        lines.append(f"Plan Note: {plan['reason']}")

    lines.append("")
    lines.append(f"Attention ({len(attention)}):")
    if attention:
        for item in attention:
            lines.append(f"  - [{item.get('severity', 'INFO')}] {item.get('kind')}: {item.get('message')}")
    else:
        lines.append("  (none)")

    lines.append("")
    lines.append("Tasks:")
    for t in tasks:
        ev = t.get("evidence") or {}
        dec = ev.get("checker_decision") or "-"
        lines.append(f"  - [{t.get('status')}] {t.get('task_id')}. {t.get('title')} (review={dec})")

    if quarantine:
        lines.append("")
        lines.append("Quarantine Branches:")
        for qb in quarantine:
            lines.append(f"  - {qb}")

    teams = snapshot.get("teams")
    if isinstance(teams, dict):
        lines.append("")
        lines.append(f"Teams (job={teams.get('job_id') or 'none'}, state={teams.get('job_state') or 'unknown'}):")
        integration = teams.get("integration") or {}
        if integration:
            lines.append(
                f"  Integration: branch={integration.get('integration_branch')} "
                f"(exists={integration.get('integration_branch_exists')}), "
                f"rc_tag={integration.get('rc_tag')} (exists={integration.get('rc_tag_exists')})"
            )
        for tm in teams.get("teams") or []:
            line = (
                f"  - {tm.get('team_id')}: status={tm.get('worker_status')} "
                f"tasks={tm.get('completed_tasks')}/{tm.get('total_tasks')} "
                f"no_changes={tm.get('no_changes')} branch={tm.get('branch')}"
            )
            if tm.get("failed_tasks"):
                line += f" failed_tasks={tm.get('failed_tasks')}"
            if tm.get("failure_reason"):
                line += f" reason={tm.get('failure_reason')}"
            lines.append(line)

    lines.append("")
    if audit.get("present"):
        lines.append(
            f"Audit: status={audit.get('status')} mode={audit.get('mode')} "
            f"findings={audit.get('findings_count')} outdated={audit.get('outdated')}"
        )
    else:
        lines.append(f"Audit: {audit.get('reason') or 'not available'}")

    return "\n".join(lines)
