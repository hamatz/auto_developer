"""成果の計測基盤: イベントログ記録モジュール (SPEC.md 機能XXX.3)。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from auto_developer.dispatcher import extract_plan_scope_lines, strip_html_comments_preserving_lines


class MetricsLog:
    """決定論的パイプラインの各段階におけるイベントログを記録する (機能XXX.3)。"""

    def __init__(self, repo_dir: Path | None = None, plan_path: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()
        self.plan_path = plan_path or (self.repo_dir / "PLAN.md")
        git_dir = self.repo_dir / ".git"
        if git_dir.is_dir():
            self.events_path = git_dir / "nightly_evidence" / "events.jsonl"
        else:
            self.events_path = self.repo_dir / ".dev" / "metrics" / "events.jsonl"
            self._ensure_dev_ignored()

    def _ensure_dev_ignored(self) -> None:
        """worktree等のフォールバック時に .dev/ が作業ツリーを汚染しないよう git exclude に登録する。"""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "--git-path", "info/exclude"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                raw = res.stdout.strip()
                p = Path(raw)
                exclude_file = p if p.is_absolute() else (self.repo_dir / p)
                if exclude_file.exists():
                    lines = exclude_file.read_text(encoding="utf-8", errors="replace").splitlines()
                    if not any(line.strip() in (".dev/", ".dev") for line in lines):
                        with open(exclude_file, "a", encoding="utf-8") as f:
                            f.write("\n.dev/\n")
        except Exception as e:  # noqa: BLE001
            print(f"METRICS_WARNING: Failed to ensure .dev/ ignored: {e}", file=sys.stderr)

    def get_current_phase(self) -> str:
        """PLAN.md「## 現在の目標」節から最初の Phase <数字> を抽出する。取れなければ 'unknown'。"""
        try:
            if not self.plan_path.exists():
                return "unknown"
            content = strip_html_comments_preserving_lines(
                self.plan_path.read_text(encoding="utf-8", errors="replace")
            )

            in_goal_section = False
            for line in content.splitlines():
                line_s = line.strip()
                if line_s.startswith("## 現在の目標"):
                    in_goal_section = True
                    continue
                elif in_goal_section and line_s.startswith("## "):
                    break
                if in_goal_section:
                    m = re.search(r"\bPhase\s+\d+\b", line_s)
                    if m:
                        raw = m.group(0)
                        num_match = re.search(r"\d+", raw)
                        if num_match:
                            return f"Phase {num_match.group(0)}"
                        return raw
            return "unknown"
        except Exception as e:  # noqa: BLE001
            print(f"METRICS_WARNING: Failed to extract phase from plan: {e}", file=sys.stderr)
            return "unknown"

    def get_current_head(self) -> str:
        """最新の HEAD コミット SHA を取得する。"""
        try:
            res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0:
                return res.stdout.strip()
        except Exception as e:  # noqa: BLE001
            print(f"METRICS_WARNING: Failed to get current HEAD: {e}", file=sys.stderr)
            return "unknown"
        return "unknown"

    def count_verify_attempts(self, task_id: str, checkpoint: str, phase: str | None = None) -> int:
        """同一 phase・task_id・checkpoint の既存 verify_step 件数をカウントする。"""
        try:
            if not self.events_path.exists() or not self.events_path.is_file():
                return 0
            target_phase = phase or self.get_current_phase()
            count = 0
            with open(self.events_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    try:
                        record = json.loads(line_s)
                    except json.JSONDecodeError:
                        continue
                    if (
                        isinstance(record, dict)
                        and record.get("event") == "verify_step"
                        and str(record.get("task_id")) == str(task_id)
                        and str(record.get("checkpoint")) == str(checkpoint)
                        and str(record.get("phase")) == target_phase
                    ):
                        count += 1
            return count
        except Exception as e:  # noqa: BLE001
            print(f"METRICS_WARNING: Failed to count verify attempts: {e}", file=sys.stderr)
            return 0

    def record_event(self, event: str, **kwargs: Any) -> bool:
        """イベントを1行のJSONで追記する。書き込み失敗時も例外を上げず警告のみを出力する (非干渉)。"""
        try:
            phase = kwargs.pop("phase", None) or self.get_current_phase()
            head = kwargs.pop("head", None) or self.get_current_head()
            record: dict[str, Any] = {
                "schema": 1,
                "ts": int(time.time()),
                "event": event,
                "phase": phase,
                "head": head,
                **kwargs,
            }
            self.events_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.events_path, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            return True
        except Exception as e:  # noqa: BLE001
            print(f"METRICS_WARNING: Failed to record metrics event '{event}': {e}", file=sys.stderr)
            return False

    def read_all_events(self) -> list[dict[str, Any]]:
        """記録済みの全イベントログを読み出す。存在しないかエラー時は空リストを返す。"""
        if not self.events_path.exists() or not self.events_path.is_file():
            return []
        events: list[dict[str, Any]] = []
        try:
            with open(self.events_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line_s = line.strip()
                    if not line_s:
                        continue
                    try:
                        record = json.loads(line_s)
                        if isinstance(record, dict):
                            events.append(record)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:  # noqa: BLE001
            print(f"METRICS_WARNING: Failed to read events log: {e}", file=sys.stderr)
        return events

    def normalize_phase_name(self, phase: str | int | None) -> str:
        """任意のフェーズ指定（例: 64, 'Phase 64'）を 'Phase <数字>' に正規化する。"""
        if phase is None or not str(phase).strip():
            return self.get_current_phase()
        p_str = str(phase).strip()
        m = re.search(r"\d+", p_str)
        if m:
            return f"Phase {m.group(0)}"
        return p_str

    def _compute_event_metrics_for_phase(
        self, phase: str, events: list[dict[str, Any]]
    ) -> dict[str, dict[str, Any]]:
        """イベントログからフェーズの指標群を算出する (XXX.4)。"""
        phase_events = [e for e in events if e.get("phase") == phase]

        # イベントが1件も存在しないフェーズ（過去フェーズ等）は全項目 null とする (推定値で埋めない)
        if not phase_events:
            return {
                "tasks_committed": {"value": None, "source": "event"},
                "first_pass_rate": {"value": None, "source": "event"},
                "mean_verify_attempts": {"value": None, "source": "event"},
                "verify_failures": {"value": None, "source": "event"},
                "circuit_breakers": {"value": None, "source": "event"},
                "quarantines": {"value": None, "source": "event"},
                "checker_rejects": {"value": None, "source": "event"},
                "checker_policy_violations": {"value": None, "source": "event"},
                "audit_dispatched": {"value": None, "source": "event"},
                "audit_backlogged": {"value": None, "source": "event"},
                "audit_escalated": {"value": None, "source": "event"},
                "audit_duplicate": {"value": None, "source": "event"},
                "audit_rejected": {"value": None, "source": "event"},
                "genuine_p0p1_per_100k_tokens": {"value": None, "source": "event"},
            }

        # コミット完了タスク
        commit_events = [
            e for e in phase_events
            if e.get("event") == "commit_step" and e.get("result") == "COMMITTED"
        ]
        committed_task_ids: set[str] = set()
        for e in commit_events:
            tid = e.get("task_id")
            if tid is not None:
                committed_task_ids.add(str(tid))
        tasks_committed = len(committed_task_ids)

        # verify_step の集計
        verify_events = [e for e in phase_events if e.get("event") == "verify_step"]
        verify_by_task: dict[str, list[dict[str, Any]]] = {}
        for ve in verify_events:
            tid = str(ve.get("task_id", ""))
            verify_by_task.setdefault(tid, []).append(ve)

        if tasks_committed > 0:
            first_pass_count = 0
            attempts_sum = 0
            has_missing_verify = False
            for tid in committed_task_ids:
                t_verifies = verify_by_task.get(tid, [])
                if not t_verifies:
                    has_missing_verify = True
                    break
                t_verifies_sorted = sorted(
                    t_verifies,
                    key=lambda x: (x.get("attempt", 1), x.get("ts", 0)),
                )
                if t_verifies_sorted and t_verifies_sorted[0].get("result") == "PASSED":
                    first_pass_count += 1
                attempts_sum += len(t_verifies)

            if has_missing_verify:
                first_pass_rate: float | None = None
                mean_verify_attempts: float | None = None
                rework_tasks_ratio: float | None = None
            else:
                first_pass_rate = round(first_pass_count / tasks_committed, 4)
                mean_verify_attempts = round(attempts_sum / tasks_committed, 2)
                rework_count = sum(
                    1 for tid in committed_task_ids if len(verify_by_task.get(tid, [])) >= 2
                )
                rework_tasks_ratio = round(rework_count / tasks_committed, 4)
        else:
            first_pass_rate = None
            mean_verify_attempts = None
            rework_tasks_ratio = None

        verify_failures = sum(1 for e in verify_events if e.get("result") == "FAILED")
        circuit_breakers = sum(1 for e in verify_events if e.get("result") == "CIRCUIT_BREAKER")
        quarantines = sum(1 for e in phase_events if e.get("event") == "rollback_step")
        checker_rejects = sum(
            1 for e in phase_events
            if e.get("event") == "checker_review" and str(e.get("decision", "")).upper() == "REJECT"
        )
        checker_policy_violations = sum(
            1 for e in phase_events
            if e.get("event") == "commit_step" and e.get("result") == "CHECKER_POLICY_VIOLATION"
        )

        # 監査イベント
        audit_events = [e for e in phase_events if e.get("event") == "audit_stage"]
        audit_dispatched = sum(int(e.get("dispatched") or 0) for e in audit_events)
        audit_backlogged = sum(int(e.get("backlogged") or 0) for e in audit_events)
        audit_escalated = sum(int(e.get("escalated") or 0) for e in audit_events)
        audit_duplicate = sum(int(e.get("duplicate") or 0) for e in audit_events)
        audit_rejected = sum(int(e.get("rejected") or 0) for e in audit_events)

        total_p0p1 = audit_dispatched + audit_escalated
        # トークン不明なら null (推定値で埋めない)
        if not audit_events:
            genuine_p0p1_per_100k_tokens: float | None = None
        else:
            all_tokens_known = all(
                e.get("input_tokens") is not None and e.get("output_tokens") is not None
                for e in audit_events
            )
            if all_tokens_known:
                total_tokens = sum(
                    int(e.get("input_tokens", 0)) + int(e.get("output_tokens", 0))
                    for e in audit_events
                )
                if total_tokens > 0:
                    genuine_p0p1_per_100k_tokens = round((total_p0p1 / total_tokens) * 100000, 2)
                else:
                    genuine_p0p1_per_100k_tokens = None
            else:
                genuine_p0p1_per_100k_tokens = None

        return {
            "tasks_committed": {"value": tasks_committed, "source": "event"},
            "first_pass_rate": {"value": first_pass_rate, "source": "event"},
            "mean_verify_attempts": {"value": mean_verify_attempts, "source": "event"},
            "rework_tasks_ratio": {"value": rework_tasks_ratio, "source": "event"},
            "verify_failures": {"value": verify_failures, "source": "event"},
            "circuit_breakers": {"value": circuit_breakers, "source": "event"},
            "quarantines": {"value": quarantines, "source": "event"},
            "checker_rejects": {"value": checker_rejects, "source": "event"},
            "checker_policy_violations": {"value": checker_policy_violations, "source": "event"},
            "audit_dispatched": {"value": audit_dispatched, "source": "event"},
            "audit_backlogged": {"value": audit_backlogged, "source": "event"},
            "audit_escalated": {"value": audit_escalated, "source": "event"},
            "audit_duplicate": {"value": audit_duplicate, "source": "event"},
            "audit_rejected": {"value": audit_rejected, "source": "event"},
            "genuine_p0p1_per_100k_tokens": {"value": genuine_p0p1_per_100k_tokens, "source": "event"},
        }

    def _extract_phase_num_from_text(self, text: str) -> int | None:
        """PLAN.md テキストの「## 現在の目標」節から Phase 番号を抽出する。"""
        in_goal = False
        for line in strip_html_comments_preserving_lines(text).splitlines():
            line_s = line.strip()
            if line_s.startswith("## 現在の目標"):
                in_goal = True
                continue
            elif in_goal and line_s.startswith("## "):
                break
            if in_goal:
                m = re.search(r"\bPhase\s+(\d+)\b", line_s)
                if m:
                    return int(m.group(1))
        return None

    def _extract_steps_from_plan(self, content: str) -> tuple[set[str], set[str]]:
        """PLAN.md テキストの読み取り範囲から数値ステップ ID と AUD- ステップ ID を抽出する。"""
        num_steps: set[str] = set()
        aud_steps: set[str] = set()
        for _, line in extract_plan_scope_lines(content):
            line_s = line.strip()
            m_num = re.search(r"^-\s*\[[ xX]\]\s*(\d+)\.", line_s)
            if m_num:
                num_steps.add(m_num.group(1))
                continue
            m_aud = re.search(r"^-\s*\[[ xX]\]\s*(AUD-[A-Za-z0-9_\-]+)", line_s)
            if m_aud:
                aud_steps.add(m_aud.group(1))
        return num_steps, aud_steps

    def _compute_git_metrics_for_phase(
        self, target_phase: str
    ) -> dict[str, dict[str, Any]]:
        """git 履歴からフェーズ範囲、計画外ステップ、コミット種別、所要時間を算出する (XXX.4)。"""
        null_result: dict[str, dict[str, Any]] = {
            "planned_steps": {"value": None, "source": "git"},
            "unplanned_steps": {"value": None, "source": "git"},
            "audit_steps_added": {"value": None, "source": "git"},
            "pipeline_commits": {"value": None, "source": "git"},
            "out_of_path_commits": {"value": None, "source": "git"},
            "lead_time_hours": {"value": None, "source": "git"},
        }
        num_match = re.search(r"\d+", target_phase)
        if not num_match:
            return null_result
        target_num = int(num_match.group(0))

        try:
            res = subprocess.run(
                ["git", "log", "--reverse", "--format=%H %ct", "--", "PLAN.md"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0 or not res.stdout.strip():
                return null_result

            commits_info = [
                line.strip().split()
                for line in res.stdout.strip().splitlines()
                if line.strip()
            ]

            start_commit: str | None = None
            start_ts: int | None = None
            first_next_commit: str | None = None

            for sha, ts_str in commits_info:
                show_res = subprocess.run(
                    ["git", "show", f"{sha}:PLAN.md"],
                    cwd=self.repo_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if show_res.returncode != 0:
                    continue
                plan_text = show_res.stdout
                phase_num = self._extract_phase_num_from_text(plan_text)
                if phase_num is None:
                    continue
                if start_commit is None and phase_num == target_num:
                    start_commit = sha
                    start_ts = int(ts_str)
                elif start_commit is not None and phase_num > target_num:
                    first_next_commit = sha
                    break

            if start_commit is None:
                return null_result

            if first_next_commit is not None:
                rev_res = subprocess.run(
                    ["git", "rev-parse", f"{first_next_commit}~1"],
                    cwd=self.repo_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if rev_res.returncode != 0 or not rev_res.stdout.strip():
                    return null_result
                end_commit = rev_res.stdout.strip()
            else:
                head_res = subprocess.run(
                    ["git", "rev-parse", "HEAD"],
                    cwd=self.repo_dir,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if head_res.returncode != 0 or not head_res.stdout.strip():
                    return null_result
                end_commit = head_res.stdout.strip()

            # コミット範囲の列挙
            check_parent = subprocess.run(
                ["git", "rev-parse", "--verify", f"{start_commit}^"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if check_parent.returncode == 0:
                rev_range = f"{start_commit}^..{end_commit}"
                cmd = ["git", "rev-list", "--topo-order", "--no-merges", rev_range]
                rev_list_res = subprocess.run(
                    cmd, cwd=self.repo_dir, capture_output=True, text=True, check=False
                )
                if rev_list_res.returncode != 0:
                    commit_shas: list[str] | None = None
                else:
                    commit_shas = [
                        c.strip()
                        for c in rev_list_res.stdout.strip().splitlines()
                        if c.strip()
                    ]
            else:
                # start_commit がルートコミットの場合
                cmd = ["git", "rev-list", "--topo-order", "--no-merges", f"{start_commit}..{end_commit}"]
                rev_list_res = subprocess.run(
                    cmd, cwd=self.repo_dir, capture_output=True, text=True, check=False
                )
                if rev_list_res.returncode != 0:
                    commit_shas = None
                else:
                    commit_shas = [
                        c.strip()
                        for c in rev_list_res.stdout.strip().splitlines()
                        if c.strip()
                    ] + [start_commit]

            if commit_shas is None:
                pipeline_commits: int | None = None
                out_of_path_commits: int | None = None
            else:
                p_count = 0
                h_count = 0
                has_commit_error = False
                for c_sha in commit_shas:
                    msg_res = subprocess.run(
                        ["git", "log", "-1", "--format=%s%n%b", c_sha],
                        cwd=self.repo_dir,
                        capture_output=True,
                        text=True,
                        check=False,
                    )
                    if msg_res.returncode != 0:
                        has_commit_error = True
                        break
                    msg = msg_res.stdout
                    if "[refs:" in msg:
                        p_count += 1
                    else:
                        h_count += 1
                if has_commit_error:
                    pipeline_commits = None
                    out_of_path_commits = None
                else:
                    pipeline_commits = p_count
                    out_of_path_commits = h_count

            # lead_time_hours の算出
            ts_res = subprocess.run(
                ["git", "log", "-1", "--format=%ct", end_commit],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if ts_res.returncode != 0 or not ts_res.stdout.strip() or start_ts is None:
                lead_time_hours: float | None = None
            else:
                try:
                    end_ts = int(ts_res.stdout.strip())
                    lead_time_hours = round(
                        max(0, end_ts - start_ts) / 3600.0, 2
                    )
                except ValueError:
                    lead_time_hours = None

            # ステップ数の集計
            start_plan_res = subprocess.run(
                ["git", "show", f"{start_commit}:PLAN.md"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            end_plan_res = subprocess.run(
                ["git", "show", f"{end_commit}:PLAN.md"],
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                check=False,
            )
            if start_plan_res.returncode != 0 or end_plan_res.returncode != 0:
                planned_steps: int | None = None
                unplanned_steps: int | None = None
                audit_steps_added: int | None = None
            else:
                start_num_steps, start_aud_steps = self._extract_steps_from_plan(
                    start_plan_res.stdout
                )
                end_num_steps, end_aud_steps = self._extract_steps_from_plan(
                    end_plan_res.stdout
                )
                planned_steps = len(start_num_steps)
                unplanned_steps = len(end_num_steps - start_num_steps)
                audit_steps_added = len(end_aud_steps - start_aud_steps)

            return {
                "planned_steps": {"value": planned_steps, "source": "git"},
                "unplanned_steps": {"value": unplanned_steps, "source": "git"},
                "audit_steps_added": {"value": audit_steps_added, "source": "git"},
                "pipeline_commits": {"value": pipeline_commits, "source": "git"},
                "out_of_path_commits": {"value": out_of_path_commits, "source": "git"},
                "lead_time_hours": {"value": lead_time_hours, "source": "git"},
            }
        except Exception as e:  # noqa: BLE001
            print(f"METRICS_WARNING: Failed to compute git metrics: {e}", file=sys.stderr)
            return null_result

    def compute_phase_metrics(self, phase: str | int | None = None) -> dict[str, Any]:
        """フェーズの各指標および直前フェーズとの差分を算出する (XXX.4)。"""
        target_phase = self.normalize_phase_name(phase)
        events = self.read_all_events()

        # イベント由来指標の算出
        event_metrics = self._compute_event_metrics_for_phase(target_phase, events)

        # git 由来指標の算出
        git_metrics = self._compute_git_metrics_for_phase(target_phase)

        all_metrics = {**event_metrics, **git_metrics}

        # 直前フェーズの判定
        num_match = re.search(r"\d+", target_phase)
        prev_phase: str | None = None
        if num_match:
            prev_num = int(num_match.group(0)) - 1
            if prev_num >= 0:
                prev_phase = f"Phase {prev_num}"

        delta: dict[str, Any] = {}
        if prev_phase:
            prev_event_metrics = self._compute_event_metrics_for_phase(prev_phase, events)
            prev_git_metrics = self._compute_git_metrics_for_phase(prev_phase)
            prev_all_metrics = {**prev_event_metrics, **prev_git_metrics}

            for k, info in all_metrics.items():
                curr_val = info.get("value")
                prev_entry = prev_all_metrics.get(k)
                prev_val = prev_entry.get("value") if prev_entry else None
                if (
                    curr_val is not None
                    and prev_val is not None
                    and isinstance(curr_val, (int, float))
                    and isinstance(prev_val, (int, float))
                ):
                    diff = curr_val - prev_val
                    delta[k] = round(diff, 4) if isinstance(diff, float) else diff
                else:
                    delta[k] = None
        else:
            for k in all_metrics:
                delta[k] = None

        return {
            "phase": target_phase,
            "metrics": all_metrics,
            "delta": delta,
        }

    def format_metrics_report(self, result: dict[str, Any]) -> str:
        """フェーズ集計結果をコンソール/マークダウン形式の表に整形する。"""
        phase = result.get("phase", "unknown")
        metrics = result.get("metrics", {})
        delta = result.get("delta", {})

        lines = [
            f"## {phase}",
            "",
            "| 指標 | 値 | 直前差分 | 情報源 |",
            "| :--- | :--- | :--- | :--- |",
        ]
        for k, info in metrics.items():
            val = info.get("value")
            val_str = str(val) if val is not None else "-"
            diff = delta.get(k)
            if diff is not None and isinstance(diff, (int, float)):
                diff_str = f"+{diff}" if diff > 0 else str(diff)
            else:
                diff_str = "-"
            source = info.get("source", "unknown")
            lines.append(f"| {k} | {val_str} | {diff_str} | {source} |")
        return "\n".join(lines)
