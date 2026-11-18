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
