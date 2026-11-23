"""STAT-1: Repository status snapshot builder and formatter."""

from __future__ import annotations

import json
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from auto_developer.dispatcher import TaskDispatcher, strip_html_comments_preserving_lines
from auto_developer.evidence import EvidenceStore
from auto_developer.metrics import MetricsLog

STATUS_SCHEMA = "auto-dev-status/1"
_TASK_EVENTS = frozenset({"verify_step", "checker_review", "commit_step", "rollback_step"})
_BLOCKED_HEADING_RE = re.compile(r"^#{1,6}\s+.*(?:止まっていること|判断待ち|Blocked|Waiting)", re.IGNORECASE)
_ANY_HEADING_RE = re.compile(r"^#{1,6}\s+")
_REFS_RE = re.compile(r"\[refs:\s*([^\]]+)\]")


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
            if item_text and not item_text.startswith("["):
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
    audit_path = repo_dir / ".dev" / "audit_stage.json"
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
    outdated = bool(head_sha and target_commit and str(target_commit) != head_sha)
    return {
        "present": True,
        "status": raw.get("status"),
        "mode": raw.get("mode"),
        "target_commit": target_commit,
        "findings_count": findings_count,
        "p0_p1_count": p0_p1_count,
        "outdated": outdated,
        "reason": None,
    }


def _ref_exists(repo_dir: Path, ref_name: str) -> bool:
    ok, _, _ = _run_git_text(repo_dir, ["show-ref", "--verify", "--quiet", ref_name])
    return ok


def _collect_teams_snapshot(
    repo_dir: Path,
    attention: list[dict[str, Any]],
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
            for rec in wt_store.get_records():
                ev_records.append({
                    "task_id": rec.get("task_id"),
                    "tree_sha": rec.get("tree_sha"),
                    "commit": rec.get("commit"),
                    "status": rec.get("status"),
                })

        enriched_teams.append({
            **team,
            "no_changes": worker_status == "NO_CHANGES",
            "evidence_records": ev_records,
        })

        if failed_tasks > 0 or worker_status in {"FAILED", "STALE", "TIMEOUT", "CRASHED"}:
            attention.append({
                "kind": "TEAM_FAILURE",
                "severity": "HIGH",
                "task_id": None,
                "message": f"Team '{tid}' status={worker_status}, failed_tasks={failed_tasks}",
            })
        for qb in quarantines:
            attention.append({
                "kind": "TEAM_QUARANTINE",
                "severity": "HIGH",
                "task_id": None,
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
    events_by_task: dict[str, list[dict[str, Any]]] = {}
    for ev in all_events:
        if ev.get("event") not in _TASK_EVENTS:
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

    for q_br in quarantine_branches:
        attention.append({
            "kind": "QUARANTINE_BRANCH",
            "severity": "HIGH",
            "task_id": None,
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
            attention.append({
                "kind": "AUDIT_OUTDATED",
                "severity": "MEDIUM",
                "task_id": None,
                "message": f"Independent audit target ({audit_info.get('target_commit')}) is older than HEAD ({head_sha})",
            })

    teams_info = _collect_teams_snapshot(resolved_repo, attention)

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
            lines.append(
                f"  - {tm.get('team_id')}: status={tm.get('worker_status')} "
                f"tasks={tm.get('completed_tasks')}/{tm.get('total_tasks')} "
                f"no_changes={tm.get('no_changes')} branch={tm.get('branch')}"
            )

    lines.append("")
    if audit.get("present"):
        lines.append(
            f"Audit: status={audit.get('status')} mode={audit.get('mode')} "
            f"findings={audit.get('findings_count')} outdated={audit.get('outdated')}"
        )
    else:
        lines.append(f"Audit: {audit.get('reason') or 'not available'}")

    return "\n".join(lines)
