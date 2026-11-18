"""成果の計測基盤: イベントログ記録モジュール (SPEC.md 機能XXX.3)。"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any


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
            content = self.plan_path.read_text(encoding="utf-8", errors="replace")

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
            else:
                first_pass_rate = round(first_pass_count / tasks_committed, 4)
                mean_verify_attempts = round(attempts_sum / tasks_committed, 2)
        else:
            first_pass_rate = None
            mean_verify_attempts = None

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

    def compute_phase_metrics(self, phase: str | int | None = None) -> dict[str, Any]:
        """フェーズの各指標および直前フェーズとの差分を算出する (XXX.4)。"""
        target_phase = self.normalize_phase_name(phase)
        events = self.read_all_events()

        # イベント由来指標の算出
        event_metrics = self._compute_event_metrics_for_phase(target_phase, events)

        # git 由来指標（ステップ 6 で完全実装、未算出項目は null / source: git）
        git_metrics: dict[str, dict[str, Any]] = {
            "planned_steps": {"value": None, "source": "git"},
            "unplanned_steps": {"value": None, "source": "git"},
            "audit_steps_added": {"value": None, "source": "git"},
            "pipeline_commits": {"value": None, "source": "git"},
            "human_commits": {"value": None, "source": "git"},
            "lead_time_hours": {"value": None, "source": "git"},
        }

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
            for k, info in all_metrics.items():
                curr_val = info.get("value")
                prev_entry = prev_event_metrics.get(k)
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
            f"# {phase} Metrics Summary",
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
