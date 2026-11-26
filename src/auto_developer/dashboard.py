"""STAT-2 / STAT-6: Self-contained offline replayable HTML dashboard renderer and writer."""

from __future__ import annotations

import html
import json
import os
import subprocess
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from auto_developer.status import STATUS_SCHEMA, collect_status_snapshot

DEFAULT_DASHBOARD_REFRESH_SECONDS = 5
DASHBOARD_CONFIG_REL = Path(".dev") / "dashboard.json"

_GAP_THRESHOLD_SEC = 600.0
_COMPRESSED_GAP_SEC = 3.0
_MIN_EVENT_GAP_SEC = 1.0
_TEAM_SCOPED_EVENTS = frozenset({
    "task_start",
    "verify_step",
    "checker_review",
    "commit_step",
    "rollback_step",
})
_EVENT_KIND_MAP = {
    "task_start": "task_start",
    "verify_step": "verify",
    "checker_review": "review",
    "commit_step": "commit",
    "rollback_step": "rollback",
    "integration_start": "integration_start",
    "integration_merge": "merge",
    "integration_reverify": "reverify",
    "rc_tag": "rc_tag",
    "integration_failed": "integration_failed",
}


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _safe_json_for_script(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False)
    return raw.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")


def _parse_ts_seconds(ev: dict[str, Any]) -> float | None:
    raw_ts = ev.get("ts")
    if isinstance(raw_ts, (int, float)) and not isinstance(raw_ts, bool):
        return float(raw_ts)
    raw_iso = ev.get("timestamp")
    if isinstance(raw_iso, str) and raw_iso.strip():
        try:
            return datetime.fromisoformat(raw_iso.strip()).timestamp()
        except ValueError:
            return None
    return None


def _format_gap_label(seconds: float) -> str:
    mins = round(seconds / 60.0)
    if mins >= 60:
        hours = mins // 60
        rem = mins % 60
        return f"+{hours}h{rem:02d}m gap" if rem else f"+{hours}h gap"
    return f"+{mins}m gap"


def _event_source(ev_name: str) -> str:
    if ev_name in {"commit_step", "integration_merge"}:
        return "git"
    if ev_name in {"integration_start", "integration_reverify", "rc_tag", "integration_failed"}:
        return "job.json"
    return "evidence"


def _sort_timeline_events(timeline_raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort timeline events chronologically while preserving file/input order on ties."""
    keyed: list[tuple[float, int, dict[str, Any]]] = []
    last_ts = float("-inf")
    for idx, ev in enumerate(timeline_raw):
        ts_val = _parse_ts_seconds(ev)
        if ts_val is not None:
            last_ts = ts_val
            sort_ts = ts_val
        else:
            sort_ts = last_ts
        keyed.append((sort_ts, idx, ev))
    keyed.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in keyed]


def _format_req_source(source: Any) -> str | None:
    """Format a CMP-1 requirement source dict as '<path>:L<start>-L<end>'."""
    if not isinstance(source, dict):
        return None
    path = source.get("path")
    if not isinstance(path, str) or not path.strip():
        return None
    lines = source.get("lines")
    if (
        isinstance(lines, list)
        and len(lines) == 2
        and isinstance(lines[0], int)
        and not isinstance(lines[0], bool)
        and isinstance(lines[1], int)
        and not isinstance(lines[1], bool)
    ):
        return f"{path.strip()}:L{lines[0]}-L{lines[1]}"
    return path.strip()


def _build_replay_payload(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Normalize an auto-dev-status/2 snapshot into the STAT-6 replay payload."""
    repo = snapshot.get("repo") or {}
    plan = snapshot.get("plan") or {}
    backlog = snapshot.get("backlog") or {}
    teams_block = snapshot.get("teams")
    attention_raw = snapshot.get("attention") or []
    timeline_raw = _sort_timeline_events(list(snapshot.get("timeline") or []))

    base_ref = str(repo.get("branch") or "main")
    head_full = str(repo.get("head") or "")
    base_commit = head_full[:7] if head_full else "HEAD"

    is_teams = isinstance(teams_block, dict)
    reqs_raw = snapshot.get("requirements") or []
    requirements_out: list[dict[str, Any]] = []
    task_reqs_map: dict[tuple[str, str], list[str]] = {}
    if isinstance(reqs_raw, list):
        for req in reqs_raw:
            if not isinstance(req, dict):
                continue
            rid = str(req.get("id") or "").strip()
            if not rid:
                continue
            stmt_raw = req.get("statement")
            stmt = str(stmt_raw) if isinstance(stmt_raw, str) else None
            src_label = _format_req_source(req.get("source"))
            req_tasks_out: list[dict[str, str]] = []
            for tref in req.get("tasks") or []:
                if not isinstance(tref, dict):
                    continue
                t_team = str(tref.get("team") or ("plan" if not is_teams else ""))
                t_id = str(tref.get("task_id") or "")
                if not t_id:
                    continue
                req_tasks_out.append({"team": t_team, "id": t_id})
                bucket = task_reqs_map.setdefault((t_team, t_id), [])
                if rid not in bucket:
                    bucket.append(rid)
            requirements_out.append({
                "id": rid,
                "statement": stmt,
                "source": src_label,
                "status": str(req.get("status") or "pending"),
                "tasks": req_tasks_out,
            })

    teams_out: list[dict[str, Any]] = []
    title_lookup: dict[tuple[str, str], str] = {}
    fallback_title_lookup: dict[str, str] = {}

    if is_teams:
        assert isinstance(teams_block, dict)
        mode = "teams"
        job_id = str(teams_block.get("job_id") or "teams")
        for tm in teams_block.get("teams") or []:
            tid = str(tm.get("team_id") or "")
            if not tid:
                continue
            branch = str(tm.get("branch") or f"team/{tid}")
            owns_raw = tm.get("owns")
            owns = (
                " · ".join(str(x) for x in owns_raw)
                if isinstance(owns_raw, list)
                else str(owns_raw or "")
            )
            raw_tasks = backlog.get(tid) or []
            tasks_out: list[dict[str, Any]] = [
                {
                    "id": str(t.get("task_id", "")),
                    "title": str(t.get("title", "")),
                    "status": str(t.get("status", "pending")),
                    "reqs": task_reqs_map.get((tid, str(t.get("task_id", ""))), []),
                }
                for t in raw_tasks
            ]
            for t in tasks_out:
                title_lookup[(tid, t["id"])] = t["title"]
                fallback_title_lookup.setdefault(t["id"], t["title"])
            teams_out.append({
                "id": tid,
                "branch": branch,
                "owns": owns,
                "tasks": tasks_out,
            })
    else:
        mode = "single"
        job_id = f"single · {base_ref}"
        raw_tasks = backlog.get("plan")
        if raw_tasks is None:
            raw_tasks = plan.get("tasks") or []
        tasks_out = [
            {
                "id": str(t.get("task_id", "")),
                "title": str(t.get("title", "")),
                "status": str(t.get("status", "pending")),
                "reqs": task_reqs_map.get(("plan", str(t.get("task_id", ""))), []),
            }
            for t in raw_tasks
        ]
        for t in tasks_out:
            title_lookup[("plan", t["id"])] = t["title"]
            fallback_title_lookup[t["id"]] = t["title"]
        teams_out.append({
            "id": "plan",
            "branch": base_ref,
            "owns": str(plan.get("path") or "PLAN.md"),
            "tasks": tasks_out,
        })

    default_maker = ""
    default_checker = ""
    for ev in timeline_raw:
        if not default_maker and ev.get("maker_model"):
            default_maker = str(ev["maker_model"])
        if not default_checker and (ev.get("checker_model") or ev.get("model")):
            default_checker = str(ev.get("checker_model") or ev.get("model"))
    if not default_maker or not default_checker:
        for t in plan.get("tasks") or []:
            ev_meta = t.get("evidence") or {}
            if not default_maker and ev_meta.get("maker_model"):
                default_maker = str(ev_meta["maker_model"])
            if not default_checker and ev_meta.get("checker_model"):
                default_checker = str(ev_meta["checker_model"])

    events_out: list[dict[str, Any]] = []
    gaps_out: list[dict[str, Any]] = []
    replay_t = 0.0
    prev_real_t: float | None = None

    for ev in timeline_raw:
        real_t = _parse_ts_seconds(ev)
        if events_out:
            if prev_real_t is not None and real_t is not None:
                delta = max(0.0, real_t - prev_real_t)
                if delta > _GAP_THRESHOLD_SEC:
                    gap_start = replay_t
                    replay_t += _COMPRESSED_GAP_SEC
                    gaps_out.append({
                        "t0": round(gap_start, 3),
                        "t1": round(replay_t, 3),
                        "real_gap": round(delta, 1),
                        "label": _format_gap_label(delta),
                    })
                else:
                    replay_t += max(_MIN_EVENT_GAP_SEC, delta)
            else:
                replay_t += _MIN_EVENT_GAP_SEC
        if real_t is not None:
            prev_real_t = real_t

        ev_name = str(ev.get("event") or "")
        kind = _EVENT_KIND_MAP.get(ev_name, ev_name)
        team_val = ev.get("team")
        if not is_teams and not team_val and ev_name in _TEAM_SCOPED_EVENTS:
            team_val = "plan"
        task_val = str(ev["task_id"]) if ev.get("task_id") is not None else None
        commit_raw = ev.get("commit")
        commit_short = str(commit_raw)[:7] if commit_raw else None
        task_title = ""
        if task_val is not None:
            task_title = (
                title_lookup.get((str(team_val or ""), task_val))
                or fallback_title_lookup.get(task_val)
                or f"Task {task_val}"
            )

        events_out.append({
            "t": round(replay_t, 3),
            "kind": kind,
            "team": str(team_val) if team_val else None,
            "task": task_val,
            "attempt": ev.get("attempt") or 1,
            "result": ev.get("result"),
            "decision": ev.get("decision"),
            "commit": commit_short,
            "add": ev.get("additions") if ev.get("additions") is not None else 0,
            "del": ev.get("deletions") if ev.get("deletions") is not None else 0,
            "title": task_title if kind == "commit" else None,
            "tag": ev.get("tag"),
            "reason": ev.get("reason"),
            "exit_code": ev.get("exit_code"),
            "maker_model": ev.get("maker_model"),
            "checker_model": ev.get("checker_model") or ev.get("model"),
            "src": _event_source(ev_name),
        })

    duration = round(replay_t, 3) if events_out else 0.0
    timeline_quarantines = {
        str(ev["quarantine_branch"]).strip()
        for ev in timeline_raw
        if ev.get("event") == "rollback_step" and ev.get("quarantine_branch")
    }
    attention_out: list[dict[str, Any]] = []
    for a in attention_raw:
        att_item: dict[str, Any] = {
            "severity": str(a.get("severity") or "INFO"),
            "kind": str(a.get("kind") or ""),
            "text": str(a.get("message") or a.get("text") or ""),
        }
        scope = a.get("scope")
        if not scope and att_item["kind"] in ("QUARANTINE_BRANCH", "TEAM_QUARANTINE") and a.get("quarantine_branch"):
            scope = "current" if str(a["quarantine_branch"]).strip() in timeline_quarantines else "previous"
        if scope in ("current", "previous"):
            att_item["scope"] = scope
        attention_out.append(att_item)

    return {
        "schema": snapshot.get("schema"),
        "project": str(repo.get("name") or "auto-dev"),
        "job_id": job_id,
        "mode": mode,
        "base": {
            "ref": base_ref,
            "commit": base_commit,
            "title": f"Base {base_ref} @ {base_commit}",
        },
        "models": {
            "maker": default_maker or "maker",
            "checker": default_checker or "checker",
        },
        "teams": teams_out,
        "requirements": requirements_out,
        "duration": duration,
        "gaps": gaps_out,
        "events": events_out,
        "attention": attention_out,
    }


def _render_attention_initial(attention: list[dict[str, Any]]) -> tuple[str, str]:
    if not attention:
        return (
            "clear",
            '<span class="tag"><span>NOTHING TO DECIDE</span></span>No quarantines, no blocked items.',
        )
    grouped: dict[str, list[dict[str, Any]]] = {}
    for item in attention:
        kind = str(item.get("kind") or "NOTICE")
        grouped.setdefault(kind, []).append(item)
    groups_html: list[str] = []
    for kind, items in grouped.items():
        li_parts: list[str] = []
        for a in items:
            scope = a.get("scope")
            scope_attr = f' data-scope="{_e(scope)}"' if scope in ("current", "previous") else ""
            scope_badge = (
                '<span class="att-scope current">current</span> '
                if scope == "current"
                else ('<span class="att-scope previous">previous</span> ' if scope == "previous" else "")
            )
            li_parts.append(
                f'<li{scope_attr}>{scope_badge}[{_e(a.get("severity", "INFO"))}] '
                f'{_e(a.get("message") or a.get("text") or "")}</li>'
            )
        lis = "".join(li_parts)
        groups_html.append(
            f'<details class="att-group" data-kind="{_e(kind)}">'
            f'<summary><span class="att-kind">{_e(kind)}</span>'
            f'<span class="att-cnt">{len(items)}</span></summary>'
            f'<ul class="att-list">{lis}</ul>'
            f"</details>"
        )
    return (
        "alert",
        '<span class="tag"><span>NEEDS YOU</span></span>' + f'<div class="att-groups">{"".join(groups_html)}</div>',
    )


def render_dashboard_html(
    snapshot: dict[str, Any],
    refresh_seconds: int | None = None,
) -> str:
    """Render a self-contained offline replayable HTML dashboard from a STAT-5 snapshot."""
    schema = snapshot.get("schema")
    schema_banner = ""
    if schema != STATUS_SCHEMA:
        schema_banner = (
            '<div class="attention alert" id="schema-mismatch">'
            '<span class="tag"><span>SCHEMA MISMATCH</span></span>'
            f"Expected Schema <code>{_e(STATUS_SCHEMA)}</code>, "
            f"got <code>{_e(schema)}</code>."
            "</div>"
        )

    is_live = refresh_seconds is not None and refresh_seconds > 0
    meta_refresh = (
        f'<meta http-equiv="refresh" content="{int(refresh_seconds)}">'
        if is_live and refresh_seconds is not None
        else ""
    )
    mode_pill_cls = "pill live" if is_live else "pill"
    mode_pill_label = "LIVE" if is_live else "NIGHT REPLAY"
    live_js = "true" if is_live else "false"

    payload = _build_replay_payload(snapshot)
    attention_raw = snapshot.get("attention") or []
    att_cls, att_html = _render_attention_initial(attention_raw)
    base_ref = _e(payload["base"]["ref"])
    job_pill = _e(payload["job_id"])
    reqs_total = len(payload.get("requirements") or [])
    reqs_wrap_style = "" if reqs_total > 0 else ' style="display:none"'
    snapshot_json = _safe_json_for_script(payload)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
{meta_refresh}
<title>auto-dev // night replay</title>
<style>
  :root {{
    --bg: #F7F6F2; --surface: #FFFFFF; --subtle: #F0EFEA; --line: #DCDAD1;
    --ink: #18181B; --ink2: #52525B; --muted: #8E8D86;
    --maker: #2563EB; --maker-bg: #EFF6FF;
    --review: #D97706; --review-bg: #FFFBEB;
    --bad: #E11D48; --bad-bg: #FFF1F2;
    --ok: #059669; --ok-bg: #ECFDF5;
    --display: 'Futura','Century Gothic','Avenir Next',-apple-system,BlinkMacSystemFont,sans-serif;
    --sans: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,'Hiragino Sans','Noto Sans JP',sans-serif;
    --mono: 'SF Mono','JetBrains Mono',Menlo,Monaco,Consolas,monospace;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ background: var(--bg); color: var(--ink); font-family: var(--sans); height: 100vh; display: flex; flex-direction: column; overflow: hidden; }}

  header {{ background: var(--surface); border-bottom: 1px solid var(--line); padding: 10px 20px; display: flex; align-items: center; gap: 18px; }}
  .brand {{ display: flex; align-items: baseline; gap: 10px; min-width: 0; }}
  .brand h1 {{ font-family: var(--display); font-size: 19px; font-weight: 700; letter-spacing: -0.02em; white-space: nowrap; }}
  .pill {{ font-family: var(--mono); font-size: 10.5px; font-weight: 600; padding: 3px 9px; border-radius: 999px; background: var(--subtle); border: 1px solid var(--line); color: var(--ink2); white-space: nowrap; }}
  .pill.live {{ background: var(--ok-bg); color: var(--ok); border-color: #A7F3D0; }}
  .counters {{ display: flex; gap: 16px; font-family: var(--mono); font-size: 11.5px; color: var(--ink2); margin-left: auto; }}
  .counters strong {{ font-family: var(--display); font-size: 15px; color: var(--ink); margin-left: 3px; }}
  .counters .warn strong {{ color: var(--bad); }}
  .counters #c-q-wrap {{ cursor: help; }}

  .attention {{ padding: 7px 20px; font-size: 12.5px; display: flex; gap: 10px; align-items: flex-start; border-bottom: 1px solid var(--line); flex-wrap: wrap; }}
  .attention.clear {{ background: var(--ok-bg); color: #065F46; align-items: center; }}
  .attention.alert {{ background: var(--bad-bg); color: #9F1239; }}
  .attention .tag {{ font-family: var(--mono); font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 4px; background: currentColor; flex-shrink: 0; }}
  .attention .tag span {{ color: #fff; }}
  .att-groups {{ display: flex; flex-wrap: wrap; gap: 6px; align-items: flex-start; }}
  details.att-group {{ background: rgba(255,255,255,.72); border: 1px solid rgba(159,18,57,.25); border-radius: 6px; padding: 2px 8px; font-size: 11.5px; }}
  details.att-group summary {{ cursor: pointer; font-family: var(--mono); font-size: 11px; font-weight: 700; list-style: none; display: inline-flex; align-items: center; gap: 5px; }}
  details.att-group summary::-webkit-details-marker {{ display: none; }}
  details.att-group summary::before {{ content: '▸'; font-size: 10px; }}
  details.att-group[open] summary::before {{ content: '▾'; }}
  .att-cnt {{ background: #9F1239; color: #fff; border-radius: 999px; padding: 0 6px; font-size: 10px; }}
  .att-list {{ margin: 4px 0 2px 16px; font-size: 11.5px; line-height: 1.45; }}
  .att-scope {{ display: inline-block; font-family: var(--mono); font-size: 9.5px; font-weight: 700; padding: 0 5px; border-radius: 4px; margin-right: 4px; }}
  .att-scope.current {{ background: var(--bad); color: #fff; }}
  .att-scope.previous {{ background: var(--subtle); color: var(--ink2); border: 1px solid var(--line); }}

  .main {{ flex: 1; display: grid; grid-template-columns: 1fr 300px; min-height: 0; }}
  .floor {{ position: relative; overflow: auto; background-image: radial-gradient(var(--line) 1.2px, transparent 1.2px); background-size: 20px 20px; display: flex; flex-direction: column; }}
  .stage {{ position: relative; width: 1000px; height: 470px; margin: 8px auto 0; flex-shrink: 0; }}
  .gantt {{ width: 1000px; margin: 0 auto 14px; background: var(--surface); border: 1px solid var(--line); border-radius: 10px; padding: 10px 14px 8px; flex-shrink: 0; cursor: pointer; }}
  .gantt svg {{ width: 100%; display: block; }}
  .g-label {{ font-family: var(--mono); font-size: 10px; fill: var(--ink2); }}
  .g-axis {{ font-family: var(--mono); font-size: 9px; fill: var(--muted); }}
  .g-gap {{ font-family: var(--mono); font-size: 8.5px; fill: var(--review); font-weight: 700; }}
  .g-legend {{ display: flex; gap: 14px; font-family: var(--mono); font-size: 10px; color: var(--ink2); margin-top: 4px; flex-wrap: wrap; }}
  .g-legend i {{ display: inline-block; width: 10px; height: 8px; border-radius: 2px; margin-right: 4px; vertical-align: middle; }}
  svg.wires {{ position: absolute; inset: 0; width: 100%; height: 100%; pointer-events: none; }}
  .wire {{ fill: none; stroke: #D2D0C6; stroke-width: 2.2; transition: stroke .3s; }}
  .wire.active {{ stroke: #93C5FD; stroke-width: 2.8; }}
  .wire.done {{ stroke: var(--ok); stroke-width: 2.4; }}

  .node {{ position: absolute; background: var(--surface); border: 1.5px solid var(--line); border-radius: 10px; box-shadow: 0 4px 14px rgba(24,24,27,.05); transition: border-color .25s, box-shadow .25s; }}
  .node.making {{ border-color: var(--maker); box-shadow: 0 0 0 3px rgba(37,99,235,.12); }}
  .node.verifying {{ border-color: #7C3AED; box-shadow: 0 0 0 3px rgba(124,58,237,.12); }}
  .node.reviewing {{ border-color: var(--review); box-shadow: 0 0 0 3px rgba(217,119,6,.12); }}
  .node.failed {{ border-color: var(--bad); box-shadow: 0 0 0 4px rgba(225,29,72,.16); }}
  .node.done {{ border-color: var(--ok); }}
  .head {{ padding: 6px 11px; border-bottom: 1px solid var(--line); border-radius: 8px 8px 0 0; background: var(--subtle); display: flex; justify-content: space-between; align-items: center; gap: 8px; }}
  .making .head {{ background: var(--maker-bg); }} .verifying .head {{ background: #F5F3FF; }} .reviewing .head {{ background: var(--review-bg); }} .failed .head {{ background: var(--bad-bg); }} .done .head {{ background: var(--ok-bg); }}
  .head .k {{ font-family: var(--mono); font-size: 10px; font-weight: 700; color: var(--ink2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .state {{ font-family: var(--mono); font-size: 9.5px; font-weight: 700; padding: 2px 7px; border-radius: 4px; background: var(--surface); color: var(--ink2); white-space: nowrap; }}
  .making .state {{ background: var(--maker); color: #fff; }} .verifying .state {{ background: #7C3AED; color: #fff; }} .reviewing .state {{ background: var(--review); color: #fff; }} .failed .state {{ background: var(--bad); color: #fff; }} .done .state {{ background: var(--ok); color: #fff; }}
  .body {{ padding: 10px 11px; display: flex; flex-direction: column; gap: 7px; }}
  .title {{ font-family: var(--display); font-size: 13.5px; font-weight: 700; line-height: 1.3; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
  .scope {{ font-family: var(--mono); font-size: 10px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  .bay {{ background: var(--bg); border: 1px solid var(--line); border-radius: 6px; padding: 5px 7px; min-height: 32px; display: flex; flex-wrap: wrap; gap: 5px; align-items: center; }}
  .bay .idle {{ font-family: var(--mono); font-size: 10px; color: var(--muted); }}
  .bot {{ font-family: var(--mono); font-size: 10px; font-weight: 700; padding: 2px 7px; border-radius: 4px; display: inline-flex; gap: 4px; align-items: center; }}
  .bot.new {{ animation: pop .3s cubic-bezier(.22,1,.36,1); }}
  @keyframes pop {{ from {{ opacity: 0; transform: scale(.6) translateY(5px); }} to {{ opacity: 1; transform: none; }} }}
  .bot.maker {{ background: var(--maker); color: #fff; }} .bot.checker {{ background: var(--review); color: #fff; }} .bot.verifier {{ background: #7C3AED; color: #fff; }}
  .bot.work {{ animation: bob .8s infinite alternate; }} .bot.work.new {{ animation: pop .3s ease, bob .8s infinite alternate; }} @keyframes bob {{ to {{ transform: translateY(-2px); }} }}
  .bot.retired {{ background: var(--subtle); color: var(--muted); border: 1px solid var(--line); text-decoration: line-through; font-weight: 400; animation: none; }}
  .bot.passed {{ background: var(--ok-bg); color: var(--ok); border: 1px solid #A7F3D0; animation: none; }}
  .bot.rejected {{ background: var(--bad-bg); color: var(--bad); border: 1px solid #FECDD3; animation: none; }}
  .pips {{ display: flex; gap: 4px; align-items: center; font-family: var(--mono); font-size: 10px; color: var(--muted); flex-wrap: wrap; }}
  .pip {{ width: 18px; height: 6px; border-radius: 3px; background: var(--line); }}
  .pip.cur {{ background: #93C5FD; }} .pip.ok {{ background: var(--ok); }} .pip.bad {{ background: var(--bad); }}
  .port {{ position: absolute; width: 10px; height: 10px; border-radius: 50%; background: var(--surface); border: 2px solid var(--ink); top: 50%; transform: translateY(-50%); }}
  .port.in {{ left: -6px; }} .port.out {{ right: -6px; }}
  .note {{ font-family: var(--mono); font-size: 10px; color: var(--ink2); }}
  .note.bad {{ color: var(--bad); }} .note.ok {{ color: var(--ok); }}
  .tickets {{ display: flex; flex-direction: column; gap: 6px; max-height: 290px; overflow-y: auto; }}
  .ticket {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--c, var(--ink2)); border-radius: 6px; padding: 6px 8px; font-size: 11px; line-height: 1.3; box-shadow: 0 1px 3px rgba(0,0,0,.04); }}
  .ticket .tk {{ font-family: var(--mono); font-size: 9.5px; font-weight: 700; color: var(--c, var(--ink2)); }}
  .ticket .tt {{ display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }}
  .req-badges {{ display: flex; flex-wrap: wrap; gap: 4px; margin-top: 3px; }}
  .req-badge {{ font-family: var(--mono); font-size: 9px; font-weight: 700; padding: 1px 5px; border-radius: 4px; background: var(--subtle); border: 1px solid var(--line); color: var(--ink2); cursor: help; }}
  .ticket.empty {{ border-left-color: var(--ok); color: var(--ok); font-family: var(--mono); font-size: 10.5px; text-align: center; }}
  .bl-count {{ font-family: var(--display); font-size: 22px; font-weight: 700; }}
  .lock {{ font-family: var(--mono); font-size: 10px; padding: 6px 9px; border: 1px dashed var(--line); border-radius: 6px; color: var(--ink2); background: var(--bg); }}

  .packet {{ position: absolute; transform: translate(-50%,-50%); font-family: var(--mono); font-size: 10px; font-weight: 700; padding: 3px 8px; border-radius: 999px; color: #fff; box-shadow: 0 3px 10px rgba(0,0,0,.18); pointer-events: none; z-index: 15; white-space: nowrap; }}
  .packet.diff {{ background: var(--maker); }} .packet.commit {{ background: var(--ok); }} .packet.bad {{ background: var(--bad); }} .packet.plan {{ background: var(--ink); }}

  .player {{ background: var(--surface); border-top: 1px solid var(--line); padding: 8px 18px; display: flex; align-items: center; gap: 12px; font-family: var(--mono); font-size: 11px; flex-shrink: 0; }}
  .btn {{ font-family: var(--mono); font-size: 11.5px; font-weight: 600; padding: 5px 11px; border-radius: 6px; border: 1px solid var(--line); background: var(--surface); color: var(--ink); cursor: pointer; }}
  .btn:hover {{ background: var(--subtle); }} .btn.primary {{ background: var(--ink); color: #fff; border-color: var(--ink); }} .btn.on {{ background: var(--subtle); border-color: var(--ink2); }}
  input[type=range] {{ flex: 1; accent-color: var(--ink); }}
  .clock {{ min-width: 120px; text-align: right; color: var(--ink2); }}
  .ticker {{ background: var(--surface); border-top: 1px solid var(--line); padding: 8px 18px; font-family: var(--mono); font-size: 11px; display: flex; gap: 10px; align-items: center; flex-shrink: 0; }}
  .ticker .tag {{ background: var(--ink); color: #fff; padding: 2px 7px; border-radius: 4px; font-weight: 700; font-size: 10px; }}

  aside {{ background: var(--surface); border-left: 1px solid var(--line); padding: 16px; display: flex; flex-direction: column; gap: 12px; min-height: 0; overflow: hidden; }}
  .t-title {{ font-family: var(--display); font-size: 12.5px; font-weight: 700; letter-spacing: .06em; text-transform: uppercase; }}
  .t-sub {{ font-family: var(--mono); font-size: 10.5px; color: var(--muted); }}
  .loc {{ background: var(--bg); border: 1px solid var(--line); border-radius: 8px; padding: 10px 12px; display: flex; justify-content: space-between; align-items: baseline; }}
  .loc b {{ font-family: var(--display); font-size: 24px; color: var(--ok); }}
  .tower {{ flex: 1 1 0; min-height: 0; display: flex; flex-direction: column-reverse; gap: 7px; overflow-y: auto; padding: 8px; background: var(--bg); border: 1px solid var(--line); border-radius: 8px; }}
  .blk {{ background: var(--surface); border: 1px solid var(--line); border-left: 4px solid var(--ok); border-radius: 6px; padding: 8px 10px; font-family: var(--mono); font-size: 10.5px; display: flex; flex-direction: column; gap: 3px; }}
  .blk.new {{ animation: drop .4s cubic-bezier(.22,1,.36,1); }}
  .blk.base {{ border-left-color: var(--muted); background: var(--subtle); color: var(--ink2); animation: none; }}
  .blk.merge {{ border-left-color: var(--ink); }}
  .blk .top {{ display: flex; justify-content: space-between; font-weight: 700; gap: 6px; }}
  .blk .msg {{ color: var(--ink2); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }}
  @keyframes drop {{ from {{ opacity: 0; transform: translateY(-16px) scale(.95); }} to {{ opacity: 1; transform: none; }} }}
  .log {{ flex: 0 0 170px; overflow-y: auto; font-family: var(--mono); font-size: 10.5px; border-top: 1px solid var(--line); padding-top: 8px; display: flex; flex-direction: column; gap: 3px; }}
  .log div {{ flex-shrink: 0; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; color: var(--ink2); }}
  .log .t {{ color: var(--muted); margin-right: 6px; }}
  .log .src {{ color: var(--muted); font-size: 9px; margin-left: 4px; }}
</style>
</head>
<body>
<header>
  <div class="brand">
    <h1>auto-dev</h1>
    <span class="{mode_pill_cls}" id="mode-pill">{mode_pill_label}</span>
    <span class="pill" id="job-pill">{job_pill}</span>
  </div>
  <div class="counters">
    <span>Tasks<strong id="c-tasks">0/0</strong></span>
    <span id="c-reqs-wrap"{reqs_wrap_style}>Reqs<strong id="c-reqs">0/{reqs_total}</strong></span>
    <span>Reviews<strong id="c-reviews">0</strong></span>
    <span>Retries<strong id="c-retries">0</strong></span>
    <span id="c-q-wrap" title="Quarantined tasks in the current timeline (excludes prior runs)">Quarantined<strong id="c-q">0</strong></span>
  </div>
</header>
{schema_banner}
<div class="attention {att_cls}" id="attention">{att_html}</div>

<div class="main">
  <div class="floor">
    <div class="stage" id="stage">
      <svg class="wires" id="wires"></svg>
      <div id="nodes"></div>
      <div id="packets"></div>
    </div>
    <div class="gantt" id="gantt" title="Click to jump to a moment">
      <svg id="gantt-svg"></svg>
      <div class="g-legend"><span><i style="background:var(--maker)"></i>Maker</span><span><i style="background:#7C3AED"></i>verify_local.sh</span><span><i style="background:var(--review)"></i>Checker review</span><span><i style="background:var(--bad)"></i>failed / rejected</span><span><i style="background:var(--ok);border-radius:50%"></i>commit (evidence consumed)</span><span><i style="background:var(--ink)"></i>integration</span><span><i style="background:var(--review);opacity:.35"></i>&gt;10m gap compressed</span></div>
    </div>
    <div class="ticker"><span class="tag">STREAM</span><span id="ticker"></span></div>
    <div class="player">
      <button class="btn primary" id="btn-play">▶ Play</button>
      <button class="btn" id="btn-restart">↺</button>
      <button class="btn on" data-speed="1">1×</button>
      <button class="btn" data-speed="4">4×</button>
      <button class="btn" data-speed="60">60×</button>
      <input type="range" id="scrub" min="0" max="1000" value="0">
      <span class="clock" id="clock">00:00 / 00:00</span>
    </div>
  </div>
  <aside>
    <div><div class="t-title">Release candidate</div><div class="t-sub" id="rc-sub">integration branch · {base_ref} is never modified</div></div>
    <div class="loc"><span class="t-sub">VERIFIED GROWTH</span><b id="loc">+0</b></div>
    <div class="tower" id="tower"></div>
    <div class="t-title">Evidence log</div>
    <div class="log" id="log"></div>
  </aside>
</div>

<script type="application/json" id="snapshot">
{snapshot_json}
</script>

<script>
(() => {{
  const S = JSON.parse(document.getElementById('snapshot').textContent);
  const EV = (S.events || []).slice().sort((a, b) => a.t - b.t);
  const REQS = S.requirements || [];
  const reqMap = {{}};
  REQS.forEach(r => {{ if (r && r.id) reqMap[r.id] = r; }});
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({{'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}}[c]));
  const fmt = t => {{ t = Math.max(0, t); const m = Math.floor(t / 60), s = Math.floor(t % 60); return String(m).padStart(2,'0') + ':' + String(s).padStart(2,'0'); }};
  const short = m => (m || '').replace(/-(medium|large|small)$/, '');

  // ---------- layout ----------
  const W = 270, H = 158, TEAM_X = 360;
  const n = Math.max(1, (S.teams || []).length);
  const stageH = Math.max(470, n * 176 + 34);
  document.getElementById('stage').style.height = stageH + 'px';
  const gap = Math.min(190, (stageH - 30) / n);
  const topY = stageH / 2 - (gap * (n - 1)) / 2 - H / 2;
  const L = {{
    plan: {{ x: 16, y: 18, w: 250, h: stageH - 40 }},
    gate: {{ x: 735, y: stageH / 2 - 95, w: 245, h: 190 }},
  }};
  (S.teams || []).forEach((tm, i) => {{ L['team:' + tm.id] = {{ x: TEAM_X, y: topY + i * gap, w: W, h: H }}; }});
  const edges = [];
  (S.teams || []).forEach(tm => {{ edges.push({{ id: 'in-' + tm.id, from: 'plan', to: 'team:' + tm.id }}); edges.push({{ id: 'out-' + tm.id, from: 'team:' + tm.id, to: 'gate' }}); }});
  const anchor = (id, side) => {{ const b = L[id]; return {{ x: side === 'out' ? b.x + b.w : b.x, y: b.y + b.h / 2 }}; }};
  const curve = (e) => {{ const a = anchor(e.from, 'out'), b = anchor(e.to, 'in'); const dx = Math.abs(b.x - a.x) * .5; return [a, {{ x: a.x + dx, y: a.y }}, {{ x: b.x - dx, y: b.y }}, b]; }};

  // ---------- state at time T (pure reducer, so scrubbing is exact) ----------
  function stateAt(T) {{
    const st = {{ started: {{}}, verifyCounts: {{}}, teams: {{}}, gate: 'idle', gateReason: '', rc: null, merged: [], commits: [], log: [], reviews: 0, retries: 0, quarantined: 0, tasksDone: 0, edges: {{}}, last: null }};
    (S.teams || []).forEach(tm => st.teams[tm.id] = {{ phase: 'idle', task: null, attempt: 1, bots: [], done: {{}}, failed: {{}}, note: '' }});
    for (const e of EV) {{
      if (e.t > T) break;
      st.last = e;
      const tm = e.team ? st.teams[e.team] : null;
      const retire = () => {{ if (tm) tm.bots.forEach(b => {{ if (b.s === 'work') b.s = 'retired'; }}); }};
      if (tm && e.task && (e.kind === 'task_start' || e.kind === 'verify' || e.kind === 'review' || e.kind === 'commit' || e.kind === 'rollback')) {{
        st.started[e.team + ':' + e.task] = true;
      }}
      switch (e.kind) {{
        case 'task_start':
          if (!tm) break;
          st.edges['in-' + e.team] = 'active';
          tm.task = e.task; tm.attempt = e.attempt || 1; tm.phase = 'making';
          if (tm.attempt === 1) tm.bots = [];
          retire();
          tm.bots.push({{ r: 'maker', label: 'M:' + short(e.maker_model || S.models.maker) + (tm.attempt > 1 ? ' #' + tm.attempt : ''), s: 'work' }});
          tm.note = tm.attempt > 1 ? 'retry after failed verification' : '';
          break;
        case 'verify':
          if (!tm) break;
          if (e.task) {{
            const vk = e.team + ':' + e.task;
            st.verifyCounts[vk] = (st.verifyCounts[vk] || 0) + 1;
            if (st.verifyCounts[vk] > 1) st.retries++;
          }}
          retire();
          if (e.result === 'PASSED') {{ tm.phase = 'verifying'; tm.note = 'verify ✓'; }}
          else {{ tm.phase = 'failed'; tm.note = 'verify ✗' + (e.exit_code != null ? ' (exit ' + e.exit_code + ')' : ''); }}
          break;
        case 'review':
          if (!tm) break;
          st.reviews++;
          retire();
          tm.bots.push({{ r: 'checker', label: 'C:' + short(e.checker_model || S.models.checker), s: e.decision === 'APPROVE' ? 'passed' : 'rejected' }});
          if (e.decision === 'APPROVE') {{ tm.phase = 'reviewing'; tm.note = 'APPROVE by checker'; }}
          else {{ tm.phase = 'failed'; tm.note = 'REJECT by checker'; }}
          break;
        case 'commit':
          if (!tm) break;
          tm.done[e.task] = true; tm.phase = 'done'; tm.note = 'committed ' + (e.commit || '');
          st.tasksDone++;
          st.edges['out-' + e.team] = 'done';
          st.commits.push({{ team: e.team, commit: e.commit || '', add: e.add || 0, del: e.del || 0, title: e.title || '' }});
          break;
        case 'rollback':
          if (!tm) break;
          tm.failed[e.task] = true; tm.phase = 'failed';
          tm.note = 'quarantined' + (e.reason ? ' — ' + e.reason : '');
          st.quarantined++;
          break;
        case 'integration_start': st.gate = 'reviewing'; break;
        case 'merge':
          st.merged.push(e.team);
          st.edges['out-' + e.team] = 'done';
          st.commits.push({{ merge: true, team: e.team, commit: e.commit || '' }});
          break;
        case 'reverify': st.gate = e.result === 'PASSED' ? 'verifying' : 'failed'; break;
        case 'rc_tag': st.gate = 'done'; st.rc = e; break;
        case 'integration_failed': st.gate = 'failed'; st.gateReason = e.reason || 'integration failed'; break;
      }}
      st.log.push(e);
    }}
    st.reqsDone = 0;
    REQS.forEach(r => {{
      const rtasks = r && r.tasks ? r.tasks : [];
      if (rtasks.length > 0 && rtasks.every(rt => st.teams[rt.team] && st.teams[rt.team].done[rt.id])) {{
        st.reqsDone++;
      }}
    }});
    return st;
  }}

  // ---------- rendering ----------
  const total = (S.teams || []).reduce((a, t) => a + (t.tasks || []).length, 0);
  document.getElementById('job-pill').textContent = S.job_id;
  document.getElementById('rc-sub').textContent = 'integration branch · ' + S.base.ref + ' is never modified';

  const seen = new Set();
  const isNew = k => {{ if (seen.has(k)) return ''; seen.add(k); return ' new'; }};
  const TEAM_COL = ['#2563EB', '#D97706', '#7C3AED', '#0891B2'];
  const STATE_LABEL = {{ idle: 'READY', making: 'MAKER', verifying: 'VERIFY', reviewing: 'REVIEW', failed: 'STOPPED', done: 'DONE' }};
  let lastAttKey = '';
  function render(st) {{
    const att = document.getElementById('attention');
    const live = (S.attention || []).slice();
    const seenAtt = new Set(live.map(a => String(a.text || '')));
    Object.entries(st.teams).forEach(([id, t]) => {{
      if (t.phase === 'failed' && t.note.startsWith('quarantined')) {{
        const hasQuarantine = live.some(a =>
          (a.kind === 'QUARANTINE_BRANCH' || a.kind === 'TEAM_QUARANTINE') &&
          (S.mode === 'single' || String(a.text || '').includes("'" + id + "'") || String(a.text || '').includes(id))
        );
        const msg = id + ': ' + t.note;
        if (!hasQuarantine && !seenAtt.has(msg)) {{
          seenAtt.add(msg);
          live.push({{ kind: S.mode === 'teams' ? 'TEAM_QUARANTINE' : 'QUARANTINE_BRANCH', severity: 'HIGH', scope: 'current', text: msg }});
        }}
      }}
    }});
    const attKey = JSON.stringify(live) + '|' + Boolean(st.rc);
    if (attKey !== lastAttKey) {{
      lastAttKey = attKey;
      if (live.length) {{
        const openKinds = new Set(Array.from(att.querySelectorAll('details.att-group[open]')).map(d => d.dataset.kind));
        const grouped = new Map();
        live.forEach(a => {{
          const kind = String(a.kind || 'NOTICE');
          if (!grouped.has(kind)) grouped.set(kind, []);
          grouped.get(kind).push(a);
        }});
        const groupsHtml = Array.from(grouped.entries()).map(([kind, items]) => {{
          const lis = items.map(a => {{
            const sc = (a.scope === 'current' || a.scope === 'previous') ? a.scope : '';
            const scAttr = sc ? ` data-scope="${{sc}}"` : '';
            const scBadge = sc ? `<span class="att-scope ${{sc}}">${{sc}}</span> ` : '';
            return `<li${{scAttr}}>${{scBadge}}${{a.severity ? '[' + esc(a.severity) + '] ' : ''}}${{esc(a.text)}}</li>`;
          }}).join('');
          return `<details class="att-group" data-kind="${{esc(kind)}}"${{openKinds.has(kind) ? ' open' : ''}}><summary><span class="att-kind">${{esc(kind)}}</span><span class="att-cnt">${{items.length}}</span></summary><ul class="att-list">${{lis}}</ul></details>`;
        }}).join('');
        att.className = 'attention alert';
        att.innerHTML = '<span class="tag"><span>NEEDS YOU</span></span><div class="att-groups">' + groupsHtml + '</div>';
      }} else {{
        att.className = 'attention clear';
        att.innerHTML = '<span class="tag"><span>NOTHING TO DECIDE</span></span>' + (st.rc ? 'Release candidate ready for your review — merge into ' + esc(S.base.ref) + ' is up to you.' : 'No quarantines, no blocked items.');
      }}
    }}

    document.getElementById('c-tasks').textContent = st.tasksDone + '/' + total;
    const reqsWrap = document.getElementById('c-reqs-wrap');
    if (reqsWrap) reqsWrap.style.display = REQS.length ? '' : 'none';
    const reqsEl = document.getElementById('c-reqs');
    if (reqsEl) reqsEl.textContent = st.reqsDone + '/' + REQS.length;
    document.getElementById('c-reviews').textContent = st.reviews;
    document.getElementById('c-retries').textContent = st.retries;
    document.getElementById('c-q').textContent = st.quarantined;
    document.getElementById('c-q-wrap').className = st.quarantined ? 'warn' : '';

    // wires
    document.getElementById('wires').innerHTML = edges.map(e => {{
      const [a, c1, c2, b] = curve(e);
      return `<path class="wire ${{st.edges[e.id] || ''}}" d="M ${{a.x}} ${{a.y}} C ${{c1.x}} ${{c1.y}}, ${{c2.x}} ${{c2.y}}, ${{b.x}} ${{b.y}}"/>`;
    }}).join('');

    // nodes
    const node = (id, cls, k, state, inner, ports) => {{
      const b = L[id];
      return `<div class="node ${{cls}}" style="left:${{b.x}}px;top:${{b.y}}px;width:${{b.w}}px;min-height:${{b.h}}px">${{ports.in ? '<div class="port in"></div>' : ''}}<div class="head"><span class="k">${{k}}</span><span class="state">${{state}}</span></div><div class="body">${{inner}}</div>${{ports.out ? '<div class="port out"></div>' : ''}}</div>`;
    }};
    const reqBadges = (reqs) => {{
      if (!reqs || !reqs.length) return '';
      return '<div class="req-badges">' + reqs.map(rid => {{
        const m = reqMap[rid];
        const parts = [];
        if (m && m.statement) parts.push(m.statement);
        if (m && m.source) parts.push(m.source);
        const tip = parts.length ? parts.join('\\n') : rid;
        return `<span class="req-badge" title="${{esc(tip)}}">${{esc(rid)}}</span>`;
      }}).join('') + '</div>';
    }};
    const left = [];
    (S.teams || []).forEach((tm, ti) => (tm.tasks || []).forEach(x => {{ if (!st.started[tm.id + ':' + x.id]) left.push({{ tm, ti, x }}); }}));
    const tickets = left.map(o => `<div class="ticket" style="--c:${{TEAM_COL[o.ti % TEAM_COL.length]}}"><div class="tk">${{esc(o.tm.id)}} · #${{esc(o.x.id)}}</div><div class="tt">${{esc(o.x.title)}}</div>${{reqBadges(o.x.reqs)}}</div>`).join('')
      || (total === 0 ? '<div class="ticket empty">no tasks in plan</div>' : '<div class="ticket empty">backlog empty — all tickets dispatched</div>');
    const planScope = S.mode === 'teams' ? 'tickets from plans/*.md' : 'tickets from ' + esc((S.teams[0] && S.teams[0].owns) || 'PLAN.md');
    let html = node('plan', left.length ? '' : 'done', 'BACKLOG // ' + esc(S.project), left.length + ' / ' + total + ' LEFT',
      `<div style="display:flex;justify-content:space-between;align-items:baseline"><span class="scope">${{planScope}}</span><span class="bl-count">${{left.length}}</span></div>
       <div class="tickets">${{tickets}}</div>
       <div class="lock">SPEC.md · verify_local.sh · tests/immunity are protected</div>`, {{ out: true }});

    (S.teams || []).forEach(tm => {{
      const t = st.teams[tm.id];
      const cur = (tm.tasks || []).find(x => x.id === t.task);
      const title = cur ? cur.title : (t.phase === 'done' ? 'All tasks committed' : ((tm.tasks[0] && tm.tasks[0].title) || 'No tasks'));
      const pips = (tm.tasks || []).map(x => `<span class="pip ${{t.done[x.id] ? 'ok' : t.failed[x.id] ? 'bad' : (x.id === t.task ? 'cur' : '')}}"></span>`).join('');
      const bots = t.bots.map((b, i) => `<span class="bot ${{b.r}} ${{b.s}}${{isNew(tm.id + t.task + i + b.label)}}">${{b.r === 'maker' ? '◆' : '◇'}} ${{esc(b.label)}}</span>`).join('') || '<span class="idle">idle bay</span>';
      const state = t.phase === 'making' && t.attempt > 1 ? 'FIXING #' + t.attempt : STATE_LABEL[t.phase];
      html += node('team:' + tm.id, t.phase, 'WT · ' + esc(tm.branch), state,
        `<div><div class="title">${{esc(title)}}</div>${{reqBadges(cur ? cur.reqs : null)}}<div class="scope">${{esc(tm.owns)}}</div></div>
         <div class="bay">${{bots}}</div>
         <div class="pips">${{pips}}<span style="margin-left:6px" class="note ${{t.phase === 'failed' ? 'bad' : (t.phase === 'done' ? 'ok' : '')}}">${{esc(t.note)}}</span></div>`, {{ in: true, out: true }});
    }});

    const gateNote = st.rc
      ? `<div class="note ok">tag ${{esc(st.rc.tag)}}</div>`
      : (st.gateReason ? `<div class="note bad">${{esc(st.gateReason)}}</div>`
        : (st.merged.length ? `<div class="note">merged: ${{st.merged.map(esc).join(', ')}}</div>` : '<div class="note">waits for every team</div>'));
    html += node('gate', st.gate, 'INTEGRATION GATE', st.gate === 'done' ? 'RC READY' : (st.gate === 'idle' ? 'STANDBY' : STATE_LABEL[st.gate]),
      `<div class="title">Merge · re-verify · tag</div><div class="scope">evidence of every team is checked first</div>${{gateNote}}
       <div class="lock">${{esc(S.base.ref)}} @ ${{esc(S.base.commit)}} — not modified · human merges</div>`, {{ in: true }});
    document.getElementById('nodes').innerHTML = html;

    // tower
    let loc = 0;
    const blocks = [`<div class="blk base"><div class="top"><span>${{esc(S.base.commit)}}</span><span>${{esc(S.base.ref)}}</span></div><div class="msg">${{esc(S.base.title)}}</div></div>`];
    st.commits.forEach(c => {{
      if (c.merge) {{ blocks.push(`<div class="blk merge${{isNew('m' + c.commit)}}"><div class="top"><span>${{esc(c.commit)}} · merge</span><span>${{esc(c.team)}}</span></div></div>`); return; }}
      loc += (c.add || 0) - (c.del || 0);
      blocks.push(`<div class="blk${{isNew('c' + c.commit)}}"><div class="top"><span>${{esc(c.commit)}} · ${{esc(c.team)}}</span><span style="color:var(--ok)">+${{c.add || 0}} −${{c.del || 0}}</span></div><div class="msg">${{esc(c.title)}}</div></div>`);
    }});
    if (st.rc) blocks.push(`<div class="blk${{isNew('rc')}}" style="border-left-color:var(--review)"><div class="top"><span>${{esc(st.rc.commit || '')}}</span><span>RC</span></div><div class="msg">${{esc(st.rc.tag || '')}}</div></div>`);
    document.getElementById('tower').innerHTML = blocks.join('');
    document.getElementById('loc').textContent = (loc >= 0 ? '+' : '') + loc;

    // log
    document.getElementById('log').innerHTML = st.log.slice().reverse().map(e => `<div><span class="t">${{fmt(e.t)}}</span>${{esc(describe(e))}}<span class="src">${{esc(e.src)}}</span></div>`).join('');
    document.getElementById('ticker').textContent = st.last
      ? describe(st.last)
      : (EV.length ? 'Press play to replay the night, or drag the timeline.' : 'No timeline events yet — showing current backlog and attention items.');
  }}

  function describe(e) {{
    const who = e.team ? e.team + (e.task ? ' #' + e.task : '') : '';
    switch (e.kind) {{
      case 'task_start': return who + ' — ' + (e.attempt > 1 ? 'fresh Maker, attempt ' + e.attempt : 'Maker started') + ' (' + short(e.maker_model || S.models.maker) + ')';
      case 'verify': return who + ' — verify_local.sh ' + (e.result === 'PASSED' ? 'passed' : 'FAILED' + (e.exit_code != null ? ' (exit ' + e.exit_code + ')' : ''));
      case 'review': return who + ' — Checker ' + (e.decision || '') + ' (' + short(e.checker_model || S.models.checker) + ')';
      case 'commit': return who + ' — evidence consumed, committed ' + (e.commit || '') + ' (+' + (e.add || 0) + ' −' + (e.del || 0) + ')';
      case 'rollback': return who + ' — quarantined' + (e.reason ? ': ' + e.reason : '');
      case 'integration_start': return 'Integration gate — checking every team\\'s evidence';
      case 'merge': return 'Merged ' + (e.team || '') + ' into the integration branch (' + (e.commit || '') + ')';
      case 'reverify': return 'Re-verification after merge ' + (e.result === 'PASSED' ? 'passed' : 'FAILED');
      case 'rc_tag': return 'Release candidate tagged: ' + (e.tag || '') + ' — ' + S.base.ref + ' untouched';
      case 'integration_failed': return 'Integration failed: ' + (e.reason || '');
      default: return e.kind;
    }}
  }}

  // ---------- gantt (time on x, one lane per team + integration) ----------
  const G = {{ left: 70, right: 10, lane: 22, top: 10 }};
  const lanes = (S.teams || []).map(t => t.id).concat(['integration']);
  const gsvg = document.getElementById('gantt-svg');
  const GW = 1000 - 28, GH = G.top + lanes.length * G.lane + 16;
  gsvg.setAttribute('viewBox', `0 0 ${{GW}} ${{GH}}`); gsvg.style.height = GH + 'px';
  const gx = t => G.left + (S.duration > 0 ? (t / S.duration) : 0) * (GW - G.left - G.right);

  const segs = [];
  const open = {{}};
  const lastVerifyT = {{}};
  EV.forEach(e => {{
    const lane = e.team || (['integration_start','merge','reverify','rc_tag','integration_failed'].includes(e.kind) ? 'integration' : null);
    if (!lane) return;
    if (e.kind === 'task_start') open[lane] = {{ kind: 'maker', t0: e.t }};
    if (e.kind === 'verify') {{
      if (open[lane] && open[lane].kind === 'maker') segs.push({{ lane, kind: 'maker', t0: open[lane].t0, t1: e.t }});
      segs.push({{ lane, kind: e.result === 'PASSED' ? 'verify' : 'bad', t0: e.t, t1: e.t + 0.15, mark: true }});
      lastVerifyT[lane] = e.t;
      open[lane] = null;
    }}
    if (e.kind === 'review') {{
      const t0 = lastVerifyT[lane] != null ? lastVerifyT[lane] : e.t;
      segs.push({{ lane, kind: e.decision === 'APPROVE' ? 'review' : 'bad', t0, t1: e.t }});
      open[lane] = null;
    }}
    if (e.kind === 'commit') segs.push({{ lane, kind: 'commit', t0: e.t, t1: e.t, dot: true }});
    if (e.kind === 'rollback') segs.push({{ lane, kind: 'bad', t0: e.t, t1: e.t, dot: true }});
    if (e.kind === 'integration_start') open.integration = {{ t0: e.t }};
    if (e.kind === 'rc_tag') {{
      if (open.integration) segs.push({{ lane, kind: 'integration', t0: open.integration.t0, t1: e.t }});
      segs.push({{ lane, kind: 'commit', t0: e.t, t1: e.t, dot: true }});
    }}
    if (e.kind === 'integration_failed') {{
      if (open.integration) segs.push({{ lane, kind: 'bad', t0: open.integration.t0, t1: e.t }});
      segs.push({{ lane, kind: 'bad', t0: e.t, t1: e.t, dot: true }});
    }}
  }});
  const COL = {{ maker: 'var(--maker)', verify: '#7C3AED', review: 'var(--review)', bad: 'var(--bad)', commit: 'var(--ok)', integration: 'var(--ink)' }};
  function renderGantt(T) {{
    let h = '';
    let lastGapLabelRight = -Infinity;
    (S.gaps || []).forEach(g => {{
      const x0 = gx(g.t0), x1 = gx(g.t1);
      const label = String(g.label || '');
      const cx = (x0 + x1) / 2;
      const halfW = (label.length * 6.5) / 2 + 6;
      const showLabel = Boolean(label && cx - halfW > lastGapLabelRight);
      if (showLabel) lastGapLabelRight = cx + halfW;
      h += `<g class="g-gap-mark">`;
      if (label) h += `<title>${{esc(label)}}</title>`;
      h += `<rect x="${{x0}}" y="2" width="${{Math.max(4, x1 - x0)}}" height="${{GH - 16}}" fill="var(--review-bg)" opacity="0.7"/>`;
      h += `<line x1="${{x0}}" x2="${{x0}}" y1="2" y2="${{GH - 14}}" stroke="var(--review)" stroke-dasharray="3 2"/>`;
      if (showLabel) h += `<text class="g-gap" x="${{cx}}" y="9" text-anchor="middle">${{esc(label)}}</text>`;
      h += `</g>`;
    }});
    lanes.forEach((ln, i) => {{
      const y = G.top + i * G.lane;
      h += `<text class="g-label" x="0" y="${{y + 14}}">${{esc(ln)}}</text><line x1="${{G.left}}" x2="${{GW - G.right}}" y1="${{y + 10}}" y2="${{y + 10}}" stroke="var(--line)" stroke-dasharray="2 3"/>`;
    }});
    if (!EV.length) {{
      h += `<text class="g-axis" x="${{(G.left + GW - G.right) / 2}}" y="${{GH / 2}}" text-anchor="middle">No timeline events recorded yet</text>`;
    }} else {{
      segs.forEach(sg => {{
        if (sg.t0 > T) return;
        const i = lanes.indexOf(sg.lane), y = G.top + i * G.lane;
        if (sg.dot) {{ h += `<circle cx="${{gx(sg.t0)}}" cy="${{y + 10}}" r="5" fill="${{COL[sg.kind]}}" stroke="#fff" stroke-width="1.5"/>`; return; }}
        const x0 = gx(sg.t0), x1 = gx(Math.min(sg.t1, T));
        h += `<rect x="${{x0}}" y="${{y + 4}}" width="${{Math.max(sg.mark ? 3 : 1, x1 - x0)}}" height="12" rx="3" fill="${{COL[sg.kind]}}"${{sg.mark ? '' : ' opacity="0.9"'}}/>`;
      }});
      const step = Math.max(1, Math.ceil(S.duration / 8));
      for (let s = 0; s <= S.duration; s += step) h += `<text class="g-axis" x="${{gx(s)}}" y="${{GH - 2}}" text-anchor="middle">${{fmt(s)}}</text>`;
      h += `<line x1="${{gx(T)}}" x2="${{gx(T)}}" y1="0" y2="${{GH - 12}}" stroke="var(--ink)" stroke-width="1.5"/>`;
    }}
    gsvg.innerHTML = h;
  }}
  document.getElementById('gantt').onclick = ev => {{
    if (S.duration <= 0) return;
    const r = gsvg.getBoundingClientRect();
    const x = (ev.clientX - r.left) / r.width * GW;
    seek(((x - G.left) / (GW - G.left - G.right)) * S.duration, false);
  }};

  // ---------- packets ----------
  function packet(edgeId, label, cls, ms) {{
    const e = edges.find(x => x.id === edgeId); if (!e) return;
    const [a, c1, c2, b] = curve(e);
    const p = document.createElement('div'); p.className = 'packet ' + cls; p.textContent = label;
    document.getElementById('packets').appendChild(p);
    const t0 = performance.now();
    (function f(now) {{
      const t = Math.min((now - t0) / ms, 1), u = 1 - t;
      p.style.left = (u*u*u*a.x + 3*u*u*t*c1.x + 3*u*t*t*c2.x + t*t*t*b.x) + 'px';
      p.style.top = (u*u*u*a.y + 3*u*u*t*c1.y + 3*u*t*t*c2.y + t*t*t*b.y) + 'px';
      if (t < 1) requestAnimationFrame(f); else p.remove();
    }})(t0);
  }}
  function fire(e) {{
    if (e.kind === 'task_start' && (e.attempt || 1) === 1) packet('in-' + e.team, 'ticket ' + e.team + ' #' + e.task, 'plan', 800);
    if (e.kind === 'commit') packet('out-' + e.team, '+' + (e.add || 0) + ' ' + (e.commit || ''), 'commit', 900);
    if (e.kind === 'rollback') packet('out-' + e.team, 'quarantine', 'bad', 900);
  }}

  // ---------- player ----------
  const scrub = document.getElementById('scrub'), clock = document.getElementById('clock'), playBtn = document.getElementById('btn-play');
  let T = 0, speed = 1, playing = false, lastFrame = 0;
  function seek(t, animate) {{
    const prev = T; T = Math.max(0, Math.min(S.duration, t));
    if (animate) EV.forEach(e => {{ if (e.t > prev && e.t <= T) fire(e); }});
    render(stateAt(T));
    renderGantt(T);
    if (!animate) seen.clear();
    scrub.value = S.duration > 0 ? Math.round((T / S.duration) * 1000) : 0;
    clock.textContent = fmt(T) + ' / ' + fmt(S.duration);
  }}
  function loop(now) {{
    if (!playing) return;
    const dt = (now - lastFrame) / 1000; lastFrame = now;
    seek(T + dt * speed, true);
    if (T >= S.duration) {{ playing = false; playBtn.textContent = '▶ Play'; return; }}
    requestAnimationFrame(loop);
  }}
  playBtn.onclick = () => {{
    if (S.duration <= 0) return;
    if (T >= S.duration) seek(0, false);
    playing = !playing;
    playBtn.textContent = playing ? '⏸ Pause' : '▶ Play';
    lastFrame = performance.now();
    if (playing) requestAnimationFrame(loop);
  }};
  document.getElementById('btn-restart').onclick = () => {{ playing = false; playBtn.textContent = '▶ Play'; document.getElementById('packets').innerHTML = ''; seek(0, false); }};
  document.querySelectorAll('[data-speed]').forEach(b => b.onclick = () => {{ speed = +b.dataset.speed; document.querySelectorAll('[data-speed]').forEach(x => x.classList.toggle('on', x === b)); }});
  scrub.oninput = () => seek((scrub.value / 1000) * S.duration, false);
  const LIVE = {live_js};
  const params = new URLSearchParams(location.search);
  if (params.has('t')) seek(+params.get('t'), false);
  else if (LIVE) seek(S.duration, false);
  else seek(0, false);
  if (params.has('autoplay')) playBtn.click();
}})();
</script>
</body>
</html>
"""


def resolve_main_repo_dir(repo_dir: Path) -> Path:
    """Resolve the main repository root even when called from inside a git worktree."""
    resolved = repo_dir.resolve()
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            cwd=resolved,
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            common_dir = Path(proc.stdout.strip())
            if not common_dir.is_absolute():
                common_dir = (resolved / common_dir).resolve()
            else:
                common_dir = common_dir.resolve()
            if common_dir.name == ".git":
                return common_dir.parent
    except OSError:
        return resolved
    return resolved


def resolve_dashboard_output_path(main_repo: Path, out_raw: str | Path | None = None) -> Path:
    """Resolve the dashboard HTML output path relative to main_repo."""
    if out_raw:
        p = Path(out_raw)
        return p if p.is_absolute() else (main_repo / p)
    return main_repo / ".dev" / "dashboard.html"


def load_dashboard_config(repo_dir: Path) -> dict[str, Any]:
    """Load per-repository .dev/dashboard.json from the main repository."""
    main_repo = resolve_main_repo_dir(repo_dir)
    cfg_path = main_repo / DASHBOARD_CONFIG_REL
    if not cfg_path.is_file():
        return {}
    try:
        data = json.loads(cfg_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_dashboard_config(
    repo_dir: Path,
    *,
    enabled: bool,
    out_path: Path | None = None,
) -> Path:
    """Persist per-repository dashboard auto-update settings in .dev/dashboard.json."""
    resolved_work = repo_dir.resolve()
    main_repo = resolve_main_repo_dir(resolved_work)
    cfg_path = main_repo / DASHBOARD_CONFIG_REL
    existing = load_dashboard_config(main_repo)

    out_value: str | None
    if out_path is not None:
        resolved_out = out_path.resolve() if out_path.is_absolute() else (resolved_work / out_path).resolve()
        out_value = str(resolved_out)
    elif enabled:
        out_value = str(main_repo / ".dev" / "dashboard.html")
    else:
        existing_out = existing.get("out")
        out_value = str(existing_out) if isinstance(existing_out, str) and existing_out else None

    payload: dict[str, Any] = {"enabled": enabled}
    if out_value is not None:
        payload["out"] = out_value

    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return cfg_path


def is_dashboard_auto_enabled(repo_dir: Path) -> tuple[bool, Path | None]:
    """Return (enabled, output_path) checking ephemeral env var first, then .dev/dashboard.json."""
    env_flag = os.environ.get("AUTO_DEV_DASHBOARD", "").strip()
    if env_flag == "0":
        return False, None
    main_repo = resolve_main_repo_dir(repo_dir)
    cfg = load_dashboard_config(main_repo)
    raw_out = cfg.get("out")
    cfg_out = str(raw_out) if isinstance(raw_out, str) and raw_out.strip() else None
    if env_flag == "1":
        env_out = os.environ.get("AUTO_DEV_DASHBOARD_OUT", "").strip() or None
        return True, resolve_dashboard_output_path(main_repo, env_out or cfg_out)
    if cfg.get("enabled") is True:
        return True, resolve_dashboard_output_path(main_repo, cfg_out)
    return False, None


def write_dashboard(
    repo_dir: Path,
    out_path: Path | None = None,
    plan_path: Path | None = None,
    refresh_seconds: int | None = None,
) -> Path:
    """Collect STAT-5 snapshot and write self-contained replayable HTML dashboard atomically."""
    resolved_repo = resolve_main_repo_dir(repo_dir)
    target = out_path or (resolved_repo / ".dev" / "dashboard.html")
    snapshot = collect_status_snapshot(resolved_repo, plan_path=plan_path)
    html_text = render_dashboard_html(snapshot, refresh_seconds=refresh_seconds)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".dashboard_", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(html_text)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise
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
