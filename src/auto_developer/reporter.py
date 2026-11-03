from __future__ import annotations

from pathlib import Path
from typing import Any


class MorningReporter:
    """NIGHTLY_REPORT.md (Morning Brief) および隔離ログの生成を担当。"""

    def write_report(self, report_path: Path, summary_data: dict[str, Any]) -> None:
        """NIGHTLY_REPORT.md を意思決定支援型 Morning Brief として作成・更新する。"""
        status = summary_data.get("status", "UNKNOWN")
        total = summary_data.get("total_tasks", 0)
        succeeded = summary_data.get("succeeded_count", 0)
        quarantined_count = summary_data.get("quarantined_count", 0)
        checkpoint = summary_data.get("checkpoint", "N/A")

        lines = [
            "# AUTO DEVELOPER / MORNING BRIEF",
            "",
            "おはようございます。直近の開発自律稼働サマリーです。",
            "",
            "## 概要 (Status Overview)",
            "| 指標 | 状況 |",
            "| :--- | :--- |",
            f"| **稼働ステータス** | **{status}** |",
            f"| **タスク進捗** | **{succeeded} / {total}** 完了 |",
            f"| **要確認・隔離** | **{quarantined_count}** 件 |",
            f"| **直前正常チェックポイント** | `{checkpoint}` |",
            "",
            "## 今回の成果 (Completed Milestones)",
        ]

        completed_tasks = summary_data.get("completed_tasks", [])
        if completed_tasks:
            for item in completed_tasks:
                lines.append(
                    f"* **task-{item['task_id']}: {item['title']}** (`{item['commit']}`)\n"
                    f"  - 検証ゲート・Checker独立査読を通過し、本線（main）にコミット済み。"
                )
        else:
            lines.append("* (完了した新規タスクはありません)")

        lines.extend(["", "## 要確認・隔離事項 (Quarantined Work)"])
        quarantined = summary_data.get("quarantined_task")
        if quarantined:
            branch = quarantined.get("quarantined_branch", "N/A")
            log_p = quarantined.get("log_path", "N/A")
            lines.extend(
                [
                    f"* **対象タスク**: task-{quarantined['task_id']} ({quarantined.get('title', 'N/A')})",
                    f"* **停止フェーズ**: `{quarantined.get('phase', 'UNKNOWN')}`",
                    f"* **隔離ブランチ**: `{branch}`",
                    f"* **詳細エラーログ**: `{log_p}`",
                    "* **失敗原因の要約**:",
                    "  ```text",
                    f"  {quarantined.get('root_cause_summary', 'No details')}",
                    "  ```",
                    "",
                    "## あなたに判断してほしいこと (Next Actions for Developer)",
                    f"タスク {quarantined['task_id']} で受入検証が停止しました。作業成果は破棄されず `{branch}` に安全に隔離されています。直前の正常状態 `{checkpoint}` は保持されています。",
                    "",
                    "以下の選択肢から次のアクションを決定してください：",
                    "",
                    f"1. **隔離ブランチの作業差分を確認する**:",
                    "   ```bash",
                    f"   git diff {checkpoint}..{branch}",
                    "   ```",
                    f"2. **失敗ログを詳細に調査する**:",
                    "   ```bash",
                    f"   cat {log_p}",
                    "   ```",
                    "3. **安全な環境で修正を再試行する**:",
                    "   ```bash",
                    "   python3 scripts/nightly_orchestrator.py --resume",
                    "   ```",
                    f"4. **今回は保留し、隔離ブランチを破棄する**:",
                    "   ```bash",
                    f"   git branch -D {branch}",
                    "   ```",
                ]
            )
        else:
            lines.extend(
                [
                    "隔離されたタスクはありません（すべてのタスクが正常完了または未着手です）。",
                    "",
                    "## あなたに判断してほしいこと (Next Actions for Developer)",
                    "- [x] すべての予定タスクが正常に検証され、本線にコミットされました。",
                    f"- `git log --oneline -n {succeeded}` でコミット内容を確認し、必要に応じてリモートへプッシュまたは PR を作成してください。",
                ]
            )

        report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def format_console_brief(self, summary_data: dict[str, Any]) -> str:
        """ターミナル表示用の簡潔な Morning Brief テキストを生成する。"""
        status = summary_data.get("status", "UNKNOWN")
        total = summary_data.get("total_tasks", 0)
        succeeded = summary_data.get("succeeded_count", 0)
        quarantined_count = summary_data.get("quarantined_count", 0)
        checkpoint = summary_data.get("checkpoint", "N/A")

        lines = [
            "============================================================",
            "               AUTO DEVELOPER / MORNING BRIEF               ",
            "============================================================",
            f" Status: {status}",
            f" Progress: {succeeded}/{total} tasks completed ({quarantined_count} quarantined)",
            f" Checkpoint: {checkpoint}",
            "------------------------------------------------------------",
            " [Completed Milestones]",
        ]
        completed = summary_data.get("completed_tasks", [])
        if completed:
            for c in completed:
                lines.append(f"  ✓ task-{c['task_id']}: {c['title']} ({c['commit'][:7]})")
        else:
            lines.append("  (none)")

        quarantined = summary_data.get("quarantined_task")
        if quarantined:
            branch = quarantined.get("quarantined_branch", "N/A")
            log_p = quarantined.get("log_path", "N/A")
            lines.extend(
                [
                    "------------------------------------------------------------",
                    " [Action Required / Quarantined Task]",
                    f"  ✗ Task {quarantined['task_id']}: {quarantined.get('title', 'N/A')}",
                    f"    Phase: {quarantined.get('phase', 'UNKNOWN')}",
                    f"    Quarantined Branch: {branch}",
                    f"    Log: {log_p}",
                    "  Root Cause Summary:",
                    f"    {quarantined.get('root_cause_summary', 'N/A')[:120]}...",
                    "------------------------------------------------------------",
                    " [Next Choices for Developer]",
                    f"  1. Diff:   git diff {checkpoint}..{branch}",
                    f"  2. Log:    cat {log_p}",
                    "  3. Resume: python3 scripts/nightly_orchestrator.py --resume",
                    f"  4. Drop:   git branch -D {branch}",
                ]
            )
        else:
            lines.extend(
                [
                    "------------------------------------------------------------",
                    " All tasks completed smoothly. Ready for review or push!",
                ]
            )
        lines.append("============================================================")
        return "\n".join(lines)

    def write_failure_log(self, log_path: Path, failure_data: dict[str, Any]) -> None:
        """logs/nightly/task_<id>_failure.log を作成する。"""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        content = [
            "=== TASK FAILURE DUMP ===",
            f"Task ID: {failure_data.get('task_id')}",
            f"Task Title: {failure_data.get('title')}",
            f"Checkpoint Commit: {failure_data.get('checkpoint')}",
            "",
            "--- ATTEMPT 1 LOG ---",
            failure_data.get("attempt_1_log", "(none)"),
            "",
            "--- ATTEMPT 2 LOG ---",
            failure_data.get("attempt_2_log", "(none)"),
            "",
            "--- WORKSPACE GIT DIFF BEFORE ROLLBACK ---",
            failure_data.get("git_diff", "(empty)"),
            "",
        ]
        log_path.write_text("\n".join(content), encoding="utf-8")
