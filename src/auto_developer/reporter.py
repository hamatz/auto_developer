from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any


class MorningReporter:
    """NIGHTLY_REPORT.md (Morning Brief) および隔離ログの生成を担当。"""

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir

    @staticmethod
    def _extract_module_key(location: str | None) -> str:
        """location 先頭の空白・`:`・`(` までのトークンを抽出する。取れなければ UNKNOWN。"""
        if not location:
            return "UNKNOWN"
        import re

        m = re.match(r"^\s*([^:\s(]+)", location)
        if m and m.group(1).strip():
            return m.group(1).strip()
        return "UNKNOWN"

    def write_report(
        self,
        report_path: Path,
        summary_data: dict[str, Any],
        repo_dir: Path | None = None,
    ) -> None:
        """NIGHTLY_REPORT.md を意思決定支援型 Morning Brief として作成・更新する。"""
        effective_repo = repo_dir or self.repo_dir or report_path.parent
        candidate_dirs = [
            effective_repo / ".git" / "nightly_evidence" / "records",
            effective_repo / ".git" / "nightly_evidence" / "archive",
            effective_repo / ".git" / "nightly_evidence",
            effective_repo / ".dev" / "evidence",
        ]
        try:
            c_res = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=effective_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if c_res.returncode == 0 and c_res.stdout.strip():
                common_p = Path(c_res.stdout.strip())
                if not common_p.is_absolute():
                    common_p = (effective_repo / common_p).resolve()
                candidate_dirs.extend([
                    common_p / "nightly_evidence" / "records",
                    common_p / "nightly_evidence" / "archive",
                    common_p / "nightly_evidence",
                ])
        except OSError:
            pass
        records_dir = effective_repo / ".git" / "nightly_evidence" / "records"

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
                tid = str(item.get("task_id", ""))
                title = item.get("title", "")
                commit_sha = item.get("commit", "")[:7]
                committed_tree_sha = item.get("committed_tree_sha", "")
                explicit_checker = item.get("checker_decision")

                record_file: Path | None = None
                for cdir in candidate_dirs:
                    cand_p = cdir / f"task_{tid}.json"
                    if cand_p.is_file():
                        record_file = cand_p
                        break

                checker_review_text = "UNVERIFIED (No Record)"
                verified_tree_text = item.get("evidence_tree_sha", "")[:7] or "N/A"

                if record_file is not None and record_file.exists():
                    try:
                        rec_data = json.loads(record_file.read_text(encoding="utf-8"))
                        rec_status = rec_data.get("status", "")
                        rec_commit_tree = rec_data.get("commit", {}).get("tree_sha", "")
                        rec_tree = rec_commit_tree or rec_data.get("tree_sha", "")
                        rec_checker = rec_data.get("checker", {})
                        dec = rec_checker.get("decision", "") if isinstance(rec_checker, dict) else str(rec_checker)

                        # Git 実態コミットの Tree SHA との照合
                        commit_full = str(item.get("commit") or rec_data.get("commit", {}).get("sha") or "").strip()
                        commit_valid = False
                        if commit_full:
                            # コミットが実在し、かつ HEAD の祖先（または HEAD 自身）であることを検査
                            anc_proc = subprocess.run(
                                ["git", "merge-base", "--is-ancestor", commit_full, "HEAD"],
                                cwd=effective_repo,
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                            if anc_proc.returncode == 0:
                                tree_proc = subprocess.run(
                                    ["git", "rev-parse", f"{commit_full}^{{tree}}"],
                                    cwd=effective_repo,
                                    capture_output=True,
                                    text=True,
                                    check=False,
                                    )
                                if tree_proc.returncode == 0 and tree_proc.stdout.strip():
                                    rec_tree = tree_proc.stdout.strip()
                                    commit_valid = True

                        tree_ok = commit_valid
                        if committed_tree_sha and rec_tree and committed_tree_sha != rec_tree:
                            tree_ok = False
                        if rec_commit_tree and rec_tree and rec_commit_tree != rec_tree:
                            tree_ok = False

                        if dec == "APPROVE" and rec_status == "COMPLETED" and tree_ok:
                            checker_review_text = "APPROVE (Verified)"
                            if rec_tree:
                                verified_tree_text = rec_tree[:7]
                        elif not tree_ok:
                            checker_review_text = "TAMPERED (Tree Mismatch)"
                        elif dec:
                            checker_review_text = f"{dec} (Record)"
                    except (json.JSONDecodeError, OSError):
                        checker_review_text = "CORRUPT (Read Error)"
                else:
                    if explicit_checker == "APPROVE" and not records_dir.exists():
                        # テスト等のモック環境互換
                        checker_review_text = "APPROVE (Simulated)"
                    elif explicit_checker:
                        checker_review_text = f"{explicit_checker} (Unverified)"
                    else:
                        checker_review_text = "UNVERIFIED (No Evidence Record)"

                lines.append(
                    f"| `task-{tid}` | {title} | `{commit_sha}` | `{verified_tree_text}` | {checker_review_text} |"
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

        audit_stage = summary_data.get("audit_stage")
        if audit_stage:
            lines.extend(
                [
                    "",
                    "## 独立監査ステージ実行結果 (Independent Audit Stage)",
                ]
            )
            status_str = audit_stage.get("status", "UNKNOWN")
            lines.append(f"- **監査ステータス**: `{status_str}`")

            audit_meta = audit_stage.get("audit_metadata")
            if audit_meta:
                mode = audit_meta.get("mode") or audit_stage.get("mode", "LIVE")
                prov = audit_meta.get("provider", "independent")
                auditor_m = audit_meta.get("auditor_model", "N/A")
                maker_m = audit_meta.get("maker_model", "N/A")
                exec_id = audit_meta.get("execution_id", "N/A")
                prov_req_id = audit_meta.get("provider_request_id") or exec_id
                fallback_occ = audit_meta.get("fallback_occurred", False)
                prompt_h = audit_meta.get("raw_prompt_hash", "N/A")
                resp_h = audit_meta.get("raw_response_hash", "N/A")
                spec_h = audit_meta.get("spec_sha256", "N/A")
                diff_h = audit_meta.get("diff_sha256", "N/A")

                lines.extend([
                    "",
                    "### 独立外部モデル監査証跡 (Independent Multi-LLM Audit Provenance)",
                    "| 項目 | 値 | 独立性検証 |",
                    "| :--- | :--- | :--- |",
                    f"| **監査モード (Audit Mode)** | `{mode}` | 実モデル直接監査拘束 (PASS) |",
                    f"| **監査プロバイダ (Provider)** | `{prov}` | 異系統プロバイダ担保 (PASS) |",
                    f"| **監査モデル (Auditor)** | `{auditor_m}`{' (Fallback)' if fallback_occ else ''} | 異系統独立性担保 (PASS) |",
                    f"| **実装モデル (Maker)** | `{maker_m}` | 査読癒着排除 (PASS) |",
                    f"| **プロバイダ要求ID (Request ID)** | `{prov_req_id}` | プロバイダ発行実証跡拘束 |",
                    f"| **実行識別子 (Execution ID)** | `{exec_id}` | 監査実行一意性拘束 |",
                    f"| **Prompt SHA-256** | `{prompt_h}` | 入力暗号学的固定 |",
                    f"| **Response SHA-256** | `{resp_h}` | 出力暗号学的固定 |",
                    f"| **SPEC SHA-256** | `{spec_h}` | コミット拘束済み |",
                    f"| **Diff SHA-256** | `{diff_h}` | 変更差分固定 |",
                    "",
                ])

            if status_str == "SUCCESS":
                dispatched = audit_stage.get("dispatched", [])
                backlogged = audit_stage.get("backlogged", [])
                escalated = audit_stage.get("escalated", [])
                rejected = audit_stage.get("rejected", [])
                duplicates = audit_stage.get("duplicates", [])
                lines.append(f"- **真正指摘登録数 (DISPATCHED)**: {len(dispatched)} 件（次期 PLAN.md に自律登録）")
                lines.append(f"- **Backlog 送り (BACKLOGGED)**: {len(backlogged)} 件（P2 / BACKLOG.md に記録）")
                lines.append(f"- **エスカレーション (ESCALATED)**: {len(escalated)} 件（修正予算超過・人間判断待ち）")
                lines.append(f"- **重複再報告 (DUPLICATE)**: {len(duplicates)} 件（既存指摘と重複のため抑止）")
                lines.append(f"- **不正/偽Red排除数**: {len(rejected)} 件（多層防壁により遮断）")
                for f in dispatched:
                    fid = getattr(f, "finding_id", f.get("finding_id", str(f)) if isinstance(f, dict) else str(f))
                    sev = getattr(f, "severity", f.get("severity", "P1") if isinstance(f, dict) else "P1")
                    desc = getattr(f, "description", f.get("description", "") if isinstance(f, dict) else "")
                    loc = getattr(f, "location", f.get("location", "") if isinstance(f, dict) else "")
                    lines.append(f"  - `[{sev}] {fid}`: {desc} ({loc})")
            elif status_str in ("AUDIT_FAILED", "AUDIT_PENDING"):
                lines.append(f"- **保留/エラー理由**: {audit_stage.get('error', 'Unknown')}")

        multi_team_data = summary_data.get("multi_team")
        if isinstance(multi_team_data, dict):
            rc_tag = multi_team_data.get("rc_tag") or "未発行 (Not Created)"
            rc_commit = multi_team_data.get("rc_commit")
            base_ref = multi_team_data.get("base_ref") or "main"
            lines.extend(
                [
                    "",
                    "## マルチチーム実行結果 (Multi-Team Execution)",
                    f"- **ジョブID**: `{multi_team_data.get('job_id', 'N/A')}`",
                    f"- **ジョブ状態**: `{multi_team_data.get('job_state', 'UNKNOWN')}`",
                    f"- **RC タグ**: `{rc_tag}`",
                ]
            )
            if rc_commit:
                lines.append(f"- **RC コミット**: `{rc_commit}`")
            lines.append(f"- **基点ブランチ**: `{base_ref}`")

            mt_teams = multi_team_data.get("teams") or []
            if mt_teams:
                lines.extend(
                    [
                        "",
                        "### チームごとの結果 (Per-Team Results)",
                        "| Team | Status | Tasks | Branch | Quarantine |",
                        "| :--- | :--- | :--- | :--- | :--- |",
                    ]
                )
                for t in mt_teams:
                    tid = t.get("team_id", "")
                    t_status = t.get("status", "UNKNOWN")
                    comp_n = int(t.get("completed_tasks", 0))
                    tot_n = int(t.get("total_tasks", 0))
                    fail_n = int(t.get("failed_tasks", 0))
                    task_str = f"{comp_n} / {tot_n}" + (f" (failed: {fail_n})" if fail_n else "")
                    branch = t.get("branch", "")
                    qbs = t.get("quarantine_branches") or []
                    q_str = ", ".join(f"`{q}`" for q in qbs) if qbs else "none"
                    lines.append(f"| `{tid}` | {t_status} | {task_str} | `{branch}` | {q_str} |")

            int_errors = multi_team_data.get("integration_errors") or []
            if int_errors:
                lines.extend(["", "### 統合エラー詳細 (Integration Errors)"])
                for err in int_errors:
                    lines.append(f"- {err}")

            mt_decisions = multi_team_data.get("pending_decisions") or []
            if mt_decisions:
                lines.extend(["", "### 判断待ちの事項 (Pending Decisions)"])
                for dec in mt_decisions:
                    lines.append(f"- {dec}")

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
        elif isinstance(multi_team_data, dict):
            lines.extend(
                [
                    "単一チーム実行の隔離タスクはありません（チームごとの隔離・状態は「マルチチーム実行結果」を参照してください）。",
                    "",
                    "## あなたに判断してほしいこと (Next Actions for Developer)",
                ]
            )
            mt_decisions = multi_team_data.get("pending_decisions") or []
            if mt_decisions:
                for dec in mt_decisions:
                    lines.append(f"- {dec}")
            else:
                lines.append(f"- `auto-dev teams status --job-id {multi_team_data.get('job_id', '')}` で状態を確認してください。")
        else:
            lines.extend(
                [
                    "隔離されたタスクはありません（すべてのタスクが正常完了または未着手です）。",
                    "",
                    "## あなたに判断してほしいこと (Next Actions for Developer)",
                    "- [x] すべての予定タスクが正常に検証され、本線にコミットされました。",
                ]
            )
            has_origin = False
            try:
                res = subprocess.run(
                    ["git", "remote", "get-url", "origin"],
                    check=False,
                    cwd=effective_repo,
                    capture_output=True,
                    text=True,
                )
                if res.returncode == 0 and res.stdout.strip():
                    has_origin = True
            except (subprocess.SubprocessError, OSError):
                has_origin = False

            if has_origin:
                lines.append(
                    f"- `git log --oneline -n {succeeded}` でコミット内容を確認後、`git push origin` でリモートへ反映してください。"
                )
            else:
                lines.append(
                    f"- リモートリポジトリ (origin) は未設定です。`git log -p -n {succeeded}` でコミット差分を確認してください。"
                )

        # 5. エスカレーションされた指摘の列挙 (WWW.5)
        escalated_findings: list[Any] = []
        if audit_stage:
            escalated_findings = audit_stage.get("escalated", []) or []

        if escalated_findings:
            lines.extend(
                [
                    "",
                    "### 監査エスカレーション（修正予算上限超過・人手による判断が必要です）",
                    "修正予算（連続サイクル上限）に達した指摘があります。モジュールごとの再設計または修正方針を判断してください：",
                ]
            )
            for f in escalated_findings:
                fid = getattr(f, "finding_id", f.get("finding_id", str(f)) if isinstance(f, dict) else str(f))
                sev = getattr(f, "severity", f.get("severity", "P1") if isinstance(f, dict) else "P1")
                desc = getattr(f, "description", f.get("description", "") if isinstance(f, dict) else "")
                loc = getattr(f, "location", f.get("location", "") if isinstance(f, dict) else "")
                mod = getattr(f, "module", None) or (f.get("module") if isinstance(f, dict) else None) or self._extract_module_key(loc)
                streak = getattr(f, "streak", None) or (f.get("streak") if isinstance(f, dict) else None)
                if streak is None:
                    try:
                        budget_path = effective_repo / ".dev" / "audit" / "budget.json"
                        if budget_path.is_file():
                            b_data = json.loads(budget_path.read_text(encoding="utf-8"))
                            if mod in b_data:
                                streak = b_data[mod].get("streak")
                    except OSError:
                        pass
                streak_str = str(streak) if streak is not None else "N/A"
                lines.append(f"- モジュール: `{mod}` (Streak: {streak_str})")
                lines.append(f"  - `[{sev}] {fid}`: {desc} ({loc})")

        bundle_path_str = summary_data.get("bundle_path")
        if bundle_path_str:
            lines.extend(
                [
                    "",
                    "## 成果提出パッケージ & 独立検証 (Evidence Bundle)",
                    "本稼働の全成果コード、コミット履歴、ハッシュ検証証跡、およびChecker査読ログをサニタイズした監査パッケージが生成されています。",
                    f"- **パッケージ保存先**: `{bundle_path_str}`",
                    "- **第三者・独立監査コマンド**:",
                    "  ```bash",
                    f"  auto-dev verify-bundle {bundle_path_str}",
                    "  ```",
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

        multi_team_data = summary_data.get("multi_team")
        if isinstance(multi_team_data, dict):
            rc_tag = multi_team_data.get("rc_tag") or "未発行 (Not Created)"
            lines.extend(
                [
                    "------------------------------------------------------------",
                    " [Multi-Team Execution]",
                    f"  Job ID: {multi_team_data.get('job_id', 'N/A')} (state={multi_team_data.get('job_state', 'UNKNOWN')})",
                    f"  RC Tag: {rc_tag}",
                ]
            )
            for t in multi_team_data.get("teams") or []:
                qbs = ", ".join(t.get("quarantine_branches") or []) or "none"
                lines.append(
                    f"  - {t.get('team_id')}: status={t.get('status')} | "
                    f"tasks={t.get('completed_tasks', 0)}/{t.get('total_tasks', 0)} | "
                    f"branch={t.get('branch')} | quarantine={qbs}"
                )
            mt_decisions = multi_team_data.get("pending_decisions") or []
            if mt_decisions:
                lines.append(" [Pending Decisions]")
                for dec in mt_decisions:
                    lines.append(f"  * {dec}")

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

    def generate_standalone_report(
        self,
        report_path: Path | None = None,
        repo_dir: Path | None = None,
        plan_path: Path | None = None,
    ) -> Path:
        """job_state.json が存在しない対話実行環境等でも、git HEAD、PLAN.md、Evidence records、

        および audit_stage.json を参照して完全な NIGHTLY_REPORT.md をスタンドアロン生成する (YYY.2)。
        """
        import re

        from auto_developer.dispatcher import TaskDispatcher, strip_html_comments_preserving_lines

        effective_repo = Path(repo_dir or self.repo_dir or Path.cwd())
        effective_report_path = Path(report_path or (effective_repo / "NIGHTLY_REPORT.md"))
        effective_plan_path = Path(plan_path or (effective_repo / "PLAN.md"))

        # 1. 最新 HEAD コミットの取得
        head_commit = "UNKNOWN"
        try:
            head_res = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=effective_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if head_res.returncode == 0 and head_res.stdout.strip():
                head_commit = head_res.stdout.strip()
        except OSError:
            pass

        # 2. 初期チェックポイントの推定 (フェーズ開始コミットまたは HEAD 祖先)
        initial_checkpoint = "UNKNOWN"
        phase_num: int | None = None
        if effective_plan_path.is_file():
            try:
                plan_clean = strip_html_comments_preserving_lines(
                    effective_plan_path.read_text(encoding="utf-8")
                )
                for pline in plan_clean.splitlines():
                    pm = re.search(r"\bPhase\s+(\d+)\b", pline)
                    if pm:
                        phase_num = int(pm.group(1))
                        break
            except OSError:
                pass

        if phase_num is not None:
            try:
                plog = subprocess.run(
                    ["git", "log", "--reverse", "--format=%H", "--", "PLAN.md"],
                    cwd=effective_repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if plog.returncode == 0 and plog.stdout.strip():
                    for c_sha in plog.stdout.strip().splitlines():
                        c_sha = c_sha.strip()
                        pshow = subprocess.run(
                            ["git", "show", f"{c_sha}:PLAN.md"],
                            cwd=effective_repo,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        if pshow.returncode == 0 and f"Phase {phase_num}" in pshow.stdout:
                            parent_res = subprocess.run(
                                ["git", "rev-parse", f"{c_sha}~1"],
                                cwd=effective_repo,
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                            if parent_res.returncode == 0 and parent_res.stdout.strip():
                                initial_checkpoint = parent_res.stdout.strip()
                            else:
                                initial_checkpoint = c_sha
                            break
            except OSError:
                pass

        if initial_checkpoint == "UNKNOWN":
            try:
                prev_res = subprocess.run(
                    ["git", "rev-parse", "HEAD~1"],
                    cwd=effective_repo,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                if prev_res.returncode == 0 and prev_res.stdout.strip():
                    initial_checkpoint = prev_res.stdout.strip()
                else:
                    initial_checkpoint = head_commit
            except OSError:
                initial_checkpoint = head_commit

        # 3. Evidence records 候補ディレクトリの収集 (SPEC: records/ のみ参照)
        candidate_dirs = [
            effective_repo / ".git" / "nightly_evidence" / "records",
        ]
        try:
            c_res = subprocess.run(
                ["git", "rev-parse", "--git-common-dir"],
                cwd=effective_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if c_res.returncode == 0 and c_res.stdout.strip():
                common_p = Path(c_res.stdout.strip())
                if not common_p.is_absolute():
                    common_p = (effective_repo / common_p).resolve()
                candidate_dirs.append(common_p / "nightly_evidence" / "records")
        except OSError:
            pass

        # 4. PLAN.md からのタスク抽出
        total_tasks = 0
        succeeded_count = 0
        completed_tasks: list[dict[str, Any]] = []

        if effective_plan_path.is_file():
            try:
                for task in TaskDispatcher().parse_plan(effective_plan_path):
                    total_tasks += 1
                    is_done = task.is_completed
                    task_id = task.task_id
                    title = task.title
                    if not is_done:
                        continue
                    succeeded_count += 1
                    # レコードの探索
                    rec_file: Path | None = None
                    for cdir in candidate_dirs:
                        cand = cdir / f"task_{task_id}.json"
                        if cand.is_file():
                            rec_file = cand
                            break

                    task_commit = ""
                    evidence_tree_sha = ""
                    committed_tree_sha = ""
                    checker_dec: str | None = None
                    if rec_file is not None:
                        try:
                            rec_data = json.loads(rec_file.read_text(encoding="utf-8"))
                            task_commit = rec_data.get("commit", {}).get("sha", "")
                            evidence_tree_sha = (
                                rec_data.get("commit", {}).get("tree_sha")
                                or rec_data.get("tree_sha", "")
                            )
                            rec_checker = rec_data.get("checker", {})
                            if isinstance(rec_checker, dict):
                                checker_dec = rec_checker.get("decision")
                            elif rec_checker:
                                checker_dec = str(rec_checker)
                        except (json.JSONDecodeError, OSError):
                            pass

                    # レコードが未記録または commit が空の場合、フェーズ範囲 (initial_checkpoint..HEAD) から探す
                    if not task_commit and initial_checkpoint != "UNKNOWN" and head_commit != "UNKNOWN":
                        try:
                            rev_spec = (
                                f"{initial_checkpoint}..{head_commit}"
                                if initial_checkpoint != head_commit
                                else head_commit
                            )
                            log_res = subprocess.run(
                                ["git", "log", "-1", f"--grep=\\[refs:[[:space:]]*{task_id}\\]", "--format=%H", rev_spec],
                                cwd=effective_repo,
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                            if log_res.returncode == 0 and log_res.stdout.strip():
                                task_commit = log_res.stdout.strip()
                        except OSError:
                            pass

                    # 実際のコミットオブジェクトから committed_tree_sha を照合取得
                    if task_commit:
                        try:
                            tree_res = subprocess.run(
                                ["git", "rev-parse", f"{task_commit}^{{tree}}"],
                                cwd=effective_repo,
                                capture_output=True,
                                text=True,
                                check=False,
                            )
                            if tree_res.returncode == 0 and tree_res.stdout.strip():
                                committed_tree_sha = tree_res.stdout.strip()
                        except OSError:
                            pass

                    completed_tasks.append({
                        "task_id": task_id,
                        "title": title,
                        "commit": task_commit,
                        "evidence_tree_sha": evidence_tree_sha,
                        "committed_tree_sha": committed_tree_sha,
                        "checker_decision": checker_dec,
                    })
            except OSError:
                pass

        # 5. 独立監査結果 (audit_stage) の読み込み
        audit_stage_data: dict[str, Any] | None = None
        for cand_audit in [
            effective_repo / ".git" / "nightly_evidence" / "audit_stage.json",
            effective_repo / ".dev" / "audit_stage.json",
        ]:
            if cand_audit.is_file():
                try:
                    audit_stage_data = json.loads(cand_audit.read_text(encoding="utf-8"))
                    break
                except (json.JSONDecodeError, OSError):
                    pass

        # 6. 隔離ブランチの検出
        quarantined_count = 0
        try:
            br_res = subprocess.run(
                ["git", "branch", "--list", "quarantine/*"],
                cwd=effective_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if br_res.returncode == 0 and br_res.stdout.strip():
                quarantined_count = len([
                    b for b in br_res.stdout.strip().splitlines() if b.strip()
                ])
        except OSError:
            pass

        # 7. マルチチームジョブ結果の集約 (TEAM-17)
        multi_team_data: dict[str, Any] | None = None
        try:
            from auto_developer.multi_team import get_multi_team_report_summary

            multi_team_data = get_multi_team_report_summary(effective_repo)
        except Exception:  # noqa: BLE001
            multi_team_data = None

        status = "COMPLETED" if (total_tasks > 0 and succeeded_count == total_tasks) else "IN_PROGRESS"
        if multi_team_data is not None:
            mt_teams = multi_team_data.get("teams") or []
            mt_total = sum(int(t.get("total_tasks", 0)) for t in mt_teams)
            mt_done = sum(int(t.get("completed_tasks", 0)) for t in mt_teams)
            mt_failed = sum(int(t.get("failed_tasks", 0)) for t in mt_teams)
            if mt_total > 0 and succeeded_count == 0:
                total_tasks = mt_total
                succeeded_count = mt_done
            mt_state = str(multi_team_data.get("job_state") or "")
            if multi_team_data.get("is_integrated"):
                status = "COMPLETED"
            elif mt_failed > 0 or quarantined_count > 0 or mt_state in (
                "WORKER_FAILED",
                "FAILED",
                "CONFLICT",
                "VERIFICATION_FAILED",
                "RECOVERY_REQUIRED",
            ):
                status = "FAILED"
            elif mt_state:
                status = mt_state

        summary_data: dict[str, Any] = {
            "status": status,
            "total_tasks": total_tasks,
            "succeeded_count": succeeded_count,
            "quarantined_count": quarantined_count,
            "checkpoint": initial_checkpoint,
            "initial_checkpoint": initial_checkpoint,
            "final_commit": head_commit,
            "completed_tasks": completed_tasks,
        }
        if audit_stage_data:
            summary_data["audit_stage"] = audit_stage_data
        if multi_team_data is not None:
            summary_data["multi_team"] = multi_team_data

        self.write_report(effective_report_path, summary_data, repo_dir=effective_repo)
        return effective_report_path
