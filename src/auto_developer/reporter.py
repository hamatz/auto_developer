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
        initial_checkpoint = summary_data.get("initial_checkpoint")
        final_commit = summary_data.get("final_commit")
        executive_summary = summary_data.get("executive_summary")
        quick_try_command = summary_data.get("quick_try_command")
        run_command = summary_data.get("run_command")
        verify_command = summary_data.get("verify_command")
        known_limitations = summary_data.get("known_limitations") or []

        lines = [
            "# AUTO DEVELOPER / MORNING BRIEF",
            "",
            "おはようございます。直近の開発自律稼働サマリーです。",
            "",
        ]

        # 1. 成果の要約 (Executive Summary) / 機能P
        if executive_summary:
            lines.extend(
                [
                    "## 成果の要約 (Executive Summary)",
                    executive_summary,
                    "",
                ]
            )

        # 2. 動作確認コマンド (Quick Try) / 機能P, 機能R
        if run_command or verify_command:
            lines.append("## 動作確認コマンド (Quick Try)")
            if run_command:
                lines.extend(
                    [
                        "### 1. 成果物を利用するコマンド (Run)",
                        "```bash",
                        run_command,
                        "```",
                        "",
                    ]
                )
            if verify_command:
                lines.extend(
                    [
                        "### 2. 検証ゲートを再実行するコマンド (Verify)",
                        "```bash",
                        verify_command,
                        "```",
                        "",
                    ]
                )
        elif quick_try_command:
            lines.extend(
                [
                    "## 動作確認コマンド (Quick Try)",
                    "```bash",
                    quick_try_command,
                    "```",
                    "",
                ]
            )

        # 3. 概要 (Status Overview) / 機能R
        status_lines = [
            "## 概要 (Status Overview)",
            "| 指標 | 状況 |",
            "| :--- | :--- |",
            f"| **稼働ステータス** | **{status}** |",
            f"| **タスク進捗** | **{succeeded} / {total}** 完了 |",
            f"| **要確認・隔離** | **{quarantined_count}** 件 |",
        ]
        if initial_checkpoint and final_commit:
            status_lines.extend(
                [
                    f"| **初期チェックポイント** | `{initial_checkpoint}` |",
                    f"| **最新コミット (HEAD)** | `{final_commit}` |",
                ]
            )
        else:
            status_lines.append(f"| **直前正常チェックポイント** | `{checkpoint}` |")
        status_lines.append("")
        status_lines.append("## 今回の成果と監査証跡 (Milestones & Audit Evidence)")
        lines.extend(status_lines)

        completed_tasks = summary_data.get("completed_tasks", [])
        if completed_tasks:
            lines.extend(
                [
                    "| Task | Title | Commit | Verified Tree | Checker Review |",
                    "| :--- | :--- | :--- | :--- | :--- |",
                ]
            )
            for item in completed_tasks:
                tid = item.get("task_id", "")
                title = item.get("title", "")
                commit_sha = item.get("commit", "")[:7]
                tree_sha = item.get("evidence_tree_sha", "")[:7] or "N/A"
                checker = item.get("checker_decision", "APPROVE")
                lines.append(
                    f"| `task-{tid}` | {title} | `{commit_sha}` | `{tree_sha}` | {checker} |"
                )
        else:
            lines.append("* (完了した新規タスクはありません)")

        # 4. 既知の制約・境界条件 (Known Limitations) / 機能P
        if known_limitations:
            lines.extend(
                [
                    "",
                    "## 既知の制約・境界条件 (Known Limitations)",
                ]
            )
            for lim in known_limitations:
                lines.append(f"- {lim}")

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
                    "1. **隔離ブランチの作業差分を確認する**:",
                    "   ```bash",
                    f"   git diff {checkpoint}..{branch}",
                    "   ```",
                    "2. **失敗ログを詳細に調査する**:",
                    "   ```bash",
                    f"   cat {log_p}",
                    "   ```",
                    "3. **隔離ブランチの作業成果を引き継ぐ / 手動修正する**:",
                    "   ```bash",
                    f"   git checkout {branch}",
                    "   ```",
                    "4. **今回は保留し、隔離ブランチを破棄する**:",
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
        executive_summary = summary_data.get("executive_summary")
        quick_try = summary_data.get("quick_try_command")

        lines = [
            "============================================================",
            "               AUTO DEVELOPER / MORNING BRIEF               ",
            "============================================================",
            f" Status: {status}",
            f" Progress: {succeeded}/{total} tasks completed ({quarantined_count} quarantined)",
            f" Checkpoint: {checkpoint}",
        ]
        if executive_summary:
            lines.append(f" Summary: {executive_summary}")
        if quick_try:
            lines.append(f" Quick Try: {quick_try}")
        lines.extend(
            [
                "------------------------------------------------------------",
                " [Completed Milestones & Audit]",
            ]
        )
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
                    f"  1. Diff:     git diff {checkpoint}..{branch}",
                    f"  2. Log:      cat {log_p}",
                    f"  3. Checkout: git checkout {branch}",
                    f"  4. Drop:     git branch -D {branch}",
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
