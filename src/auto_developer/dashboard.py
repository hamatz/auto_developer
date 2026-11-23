"""STAT-2: Self-contained offline HTML dashboard renderer and writer."""

from __future__ import annotations

import html
import time
from pathlib import Path
from typing import Any

from auto_developer.status import STATUS_SCHEMA, collect_status_snapshot


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _render_attention_section(attention: list[dict[str, Any]]) -> str:
    if not attention:
        return (
            '<section class="card ok-card"><h2>Attention (0)</h2>'
            "<p>No items require human attention.</p></section>"
        )
    items_html = []
    for item in attention:
        sev = _e(item.get("severity", "INFO"))
        kind = _e(item.get("kind", ""))
        msg = _e(item.get("message", ""))
        tid = item.get("task_id")
        tid_badge = f'<span class="badge">task { _e(tid) }</span> ' if tid else ""
        items_html.append(
            f'<li class="att-item att-{sev.lower()}">'
            f'<span class="badge sev-{sev.lower()}">{sev}</span> '
            f"<strong>{kind}</strong> {tid_badge}— {msg}</li>"
        )
    return (
        f'<section class="card warn-card"><h2>Attention ({len(attention)})</h2>'
        f"<ul>{''.join(items_html)}</ul></section>"
    )


def _render_single_team_tasks(tasks: list[dict[str, Any]]) -> str:
    if not tasks:
        return '<section class="card"><h2>Tasks (0)</h2><p>No tasks found.</p></section>'
    rows = []
    for t in tasks:
        tid = _e(t.get("task_id", ""))
        title = _e(t.get("title", ""))
        status = _e(t.get("status", "pending"))
        ev = t.get("evidence") or {}
        events = t.get("events") or []
        last_verify = next(
            (e.get("result") for e in reversed(events) if e.get("event") == "verify_step"),
            "PASSED" if ev.get("present") else "-",
        )
        decision = ev.get("checker_decision") or "-"
        model = ev.get("checker_model") or "-"
        review_str = f"{decision} ({model})" if decision != "-" else "-"
        commit_sha = ev.get("commit") or (t.get("commit_ref") or {}).get("sha") or "-"
        commit_short = str(commit_sha)[:8] if commit_sha != "-" else "-"
        rows.append(
            f"<tr>"
            f"<td><code>{tid}</code></td>"
            f"<td>{title}</td>"
            f'<td><span class="status status-{status}">{status}</span></td>'
            f"<td>{_e(last_verify)}</td>"
            f"<td>{_e(review_str)}</td>"
            f"<td><code>{_e(commit_short)}</code></td>"
            f"</tr>"
        )
    return (
        f'<section class="card"><h2>Tasks ({len(tasks)})</h2>'
        "<table><thead><tr>"
        "<th>ID</th><th>Title</th><th>Status</th><th>Verify</th><th>Review</th><th>Commit</th>"
        f"</tr></thead><tbody>{''.join(rows)}</tbody></table></section>"
    )


def _render_teams_section(teams_block: dict[str, Any]) -> str:
    job_id = _e(teams_block.get("job_id") or "none")
    job_state = _e(teams_block.get("job_state") or "unknown")
    team_cols = []
    for tm in teams_block.get("teams") or []:
        tid = _e(tm.get("team_id", ""))
        w_status = _e(tm.get("worker_status", "UNKNOWN"))
        comp = _e(tm.get("completed_tasks", 0))
        total = _e(tm.get("total_tasks", 0))
        branch = _e(tm.get("branch", ""))
        no_chg = "YES" if tm.get("no_changes") else "no"
        q_list = tm.get("quarantine_branches") or []
        q_str = ", ".join(_e(q) for q in q_list) if q_list else "none"
        ev_count = len(tm.get("evidence_records") or [])
        team_cols.append(
            f'<div class="team-col">'
            f"<h3>{tid}</h3>"
            f"<p>Status: <strong>{w_status}</strong></p>"
            f"<p>Tasks: {comp}/{total} | NO_CHANGES: {no_chg}</p>"
            f"<p>Branch: <code>{branch}</code></p>"
            f"<p>Evidence records: {ev_count}</p>"
            f"<p>Quarantine: {q_str}</p>"
            f"</div>"
        )

    integration = teams_block.get("integration") or {}
    int_br = _e(integration.get("integration_branch") or f"integration/{job_id}")
    int_exists = bool(integration.get("integration_branch_exists"))
    rc_tag = _e(integration.get("rc_tag") or f"rc/{job_id}")
    rc_exists = bool(integration.get("rc_tag_exists"))

    return (
        f'<section class="card"><h2>Teams (job={job_id}, state={job_state})</h2>'
        f'<div class="team-grid">{"".join(team_cols)}</div>'
        f'<div class="integration-box">'
        f"<h3>Integration Stage</h3>"
        f"<p>Integration branch: <code>{int_br}</code> (exists={int_exists}) | "
        f"RC tag: <code>{rc_tag}</code> (exists={rc_exists})</p>"
        f"<p>Note: Auto-merge to <code>main</code> is NOT performed; integration stops at the "
        f"<code>rc/</code> tag for human review and manual merge.</p>"
        f"</div></section>"
    )


def render_dashboard_html(
    snapshot: dict[str, Any],
    refresh_seconds: int | None = None,
) -> str:
    """Render a self-contained offline HTML dashboard from a STAT-1 snapshot."""
    schema = snapshot.get("schema")
    schema_banner = ""
    if schema != STATUS_SCHEMA:
        schema_banner = (
            f'<section class="card warn-card"><h2>Schema Mismatch</h2>'
            f"<p>Expected schema <code>{_e(STATUS_SCHEMA)}</code>, "
            f"got <code>{_e(schema)}</code>.</p></section>"
        )

    repo = snapshot.get("repo") or {}
    plan = snapshot.get("plan") or {}
    attention = snapshot.get("attention") or []
    tasks = plan.get("tasks") or []
    teams_block = snapshot.get("teams")
    audit = snapshot.get("audit") or {}

    meta_refresh = (
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
        if refresh_seconds is not None and refresh_seconds > 0
        else ""
    )

    body_main = (
        _render_teams_section(teams_block)
        if isinstance(teams_block, dict)
        else _render_single_team_tasks(tasks)
    )

    audit_summary = (
        f"status={_e(audit.get('status'))}, mode={_e(audit.get('mode'))}, "
        f"findings={_e(audit.get('findings_count'))}, outdated={_e(audit.get('outdated'))}"
        if audit.get("present")
        else _e(audit.get("reason") or "not available")
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{meta_refresh}
<title>Auto Developer Dashboard</title>
<style>
  body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; margin: 24px; background: #f7f8fa; color: #1f2937; }}
  h1, h2, h3 {{ margin-top: 0; }}
  .meta {{ color: #4b5563; font-size: 0.9rem; margin-bottom: 16px; }}
  .card {{ background: #ffffff; border: 1px solid #e5e7eb; border-radius: 8px; padding: 16px; margin-bottom: 16px; }}
  .warn-card {{ border-color: #dc2626; background: #fef2f2; }}
  .ok-card {{ border-color: #16a34a; background: #f0fdf4; }}
  .badge {{ display: inline-block; padding: 2px 6px; border-radius: 4px; font-size: 0.75rem; font-weight: 600; background: #e5e7eb; }}
  .sev-high {{ background: #fee2e2; color: #991b1b; }}
  .sev-medium {{ background: #fef3c7; color: #92400e; }}
  table {{ width: 100%; border-collapse: collapse; }}
  th, td {{ text-align: left; padding: 8px; border-bottom: 1px solid #e5e7eb; font-size: 0.9rem; }}
  .status {{ padding: 2px 6px; border-radius: 4px; font-size: 0.8rem; }}
  .status-completed {{ background: #dcfce7; color: #166534; }}
  .status-failed {{ background: #fee2e2; color: #991b1b; }}
  .status-pending {{ background: #e5e7eb; color: #374151; }}
  .team-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr)); gap: 12px; }}
  .team-col {{ border: 1px solid #d1d5db; border-radius: 6px; padding: 12px; background: #f9fafb; }}
  .integration-box {{ margin-top: 12px; padding: 12px; border: 1px dashed #6b7280; border-radius: 6px; background: #f3f4f6; }}
</style>
</head>
<body>
  <h1>Auto Developer Dashboard</h1>
  <div class="meta">
    Generated: {_e(snapshot.get("generated_at"))} |
    HEAD: <code>{_e(repo.get("head") or "null")}</code> |
    Branch: <code>{_e(repo.get("branch") or "null")}</code> |
    Schema: <code>{_e(schema)}</code>
  </div>
  {schema_banner}
  {_render_attention_section(attention)}
  {body_main}
  <section class="card">
    <h2>Audit</h2>
    <p>{audit_summary}</p>
  </section>
</body>
</html>
"""


def write_dashboard(
    repo_dir: Path,
    out_path: Path | None = None,
    plan_path: Path | None = None,
    refresh_seconds: int | None = None,
) -> Path:
    """Collect STAT-1 snapshot and write self-contained HTML dashboard."""
    resolved_repo = repo_dir.resolve()
    target = out_path or (resolved_repo / ".dev" / "dashboard.html")
    snapshot = collect_status_snapshot(resolved_repo, plan_path=plan_path)
    html_text = render_dashboard_html(snapshot, refresh_seconds=refresh_seconds)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(html_text, encoding="utf-8")
    return target


def watch_dashboard(
    repo_dir: Path,
    out_path: Path | None = None,
    plan_path: Path | None = None,
    interval_sec: int = 5,
) -> int:
    """Regenerate HTML dashboard at fixed intervals until KeyboardInterrupt."""
    if interval_sec < 1:
        raise ValueError("interval_sec must be >= 1")
    try:
        while True:
            out_file = write_dashboard(
                repo_dir,
                out_path=out_path,
                plan_path=plan_path,
                refresh_seconds=interval_sec,
            )
            print(f"DASHBOARD UPDATED: {out_file} (refresh every {interval_sec}s, Ctrl-C to stop)")
            time.sleep(interval_sec)
    except KeyboardInterrupt:
        print("\nDashboard watch stopped.")
        return 0
