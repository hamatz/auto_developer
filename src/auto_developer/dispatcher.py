from __future__ import annotations

import contextlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from auto_developer.audit import AuditFinding
    from auto_developer.audit_store import AuditStore
    from auto_developer.verifier import RedTestVerifier

from auto_developer.models import Task


class TaskDispatcher:
    """PLAN.md のタスクパースと状態管理を担当するモジュール。"""

    # Matches "- [ ] 1. タスク名" or "- [x] 1. タスク名"
    TASK_PATTERN = re.compile(
        r"^\s*-\s*\[([ xX])\]\s*(\d+|[a-zA-Z0-9_\-]+)\.\s*(.+?)(?:\s*\*\*\[FAILED\]\*\*)?\s*$"
    )

    def parse_plan(self, plan_path: Path) -> list[Task]:
        """PLAN.md を行単位で読み込み、Taskオブジェクトのリストを生成する。"""
        if not plan_path.exists():
            raise FileNotFoundError(f"PLAN file not found: {plan_path}")

        tasks: list[Task] = []
        lines = plan_path.read_text(encoding="utf-8").splitlines()

        for idx, line in enumerate(lines):
            match = self.TASK_PATTERN.match(line)
            if match:
                check_box, task_id, title = match.groups()
                is_completed = check_box.lower() == "x"
                is_failed = "**[FAILED]**" in line

                tasks.append(
                    Task(
                        task_id=task_id.strip(),
                        title=title.strip(),
                        line_number=idx,
                        is_completed=is_completed,
                        is_failed=is_failed,
                    )
                )

        return tasks

    def get_next_pending_task(self, tasks: list[Task]) -> Task | None:
        """未完了かつ未失敗の最初のタスクを取得する。"""
        for task in tasks:
            if not task.is_completed and not task.is_failed:
                return task
        return None

    def mark_task_status(self, plan_path: Path, task: Task, status: str) -> None:
        """PLAN.md 内の指定タスクのステータス記法を更新する。"""
        lines = plan_path.read_text(encoding="utf-8").splitlines()
        if task.line_number >= len(lines):
            raise IndexError(f"Task line number {task.line_number} exceeds plan length.")

        target_line = lines[task.line_number]
        match = self.TASK_PATTERN.match(target_line)
        if not match:
            # Fallback: search by task_id and title
            for idx, line in enumerate(lines):
                if f"{task.task_id}." in line and task.title in line:
                    task = Task(task.task_id, task.title, idx, task.is_completed, task.is_failed)
                    target_line = line
                    break

        if status == "completed":
            new_line = f"- [x] {task.task_id}. {task.title}"
        elif status == "failed":
            new_line = f"- [ ] {task.task_id}. {task.title} **[FAILED]**"
        else:
            raise ValueError(f"Unsupported status: {status}")

        lines[task.line_number] = new_line
        plan_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class AuditDispatcher:
    """監査指摘 (AuditFinding) を RedTestVerifier で検証し、真正な Red を PLAN.md に自動登録する。"""

    def __init__(
        self,
        repo_dir: Path | str | None = None,
        verifier: RedTestVerifier | None = None,
        audit_store: AuditStore | None = None,
        max_consecutive_cycles: int = 5,
    ) -> None:
        self.repo_dir = Path(repo_dir) if repo_dir else Path.cwd()
        self.max_consecutive_cycles = max_consecutive_cycles
        self.budget_path = self.repo_dir / ".dev" / "audit" / "budget.json"

        if verifier is None:
            from auto_developer.verifier import RedTestVerifier

            self.verifier = RedTestVerifier(repo_dir=self.repo_dir)
        else:
            self.verifier = verifier

        if audit_store is None:
            from auto_developer.audit_store import AuditStore

            self.audit_store = AuditStore(audit_dir=self.repo_dir / ".dev" / "audit")
        else:
            self.audit_store = audit_store

    @staticmethod
    def _extract_module_key(location: str | None) -> str:
        """location 先頭の空白・`:`・`(` までのトークンを抽出する。取れなければ UNKNOWN。"""
        if not location:
            return "UNKNOWN"

        m = re.match(r"^\s*([^:\s(]+)", location)
        if m and m.group(1).strip():
            return m.group(1).strip()
        return "UNKNOWN"

    def _load_budget(self) -> dict[str, dict[str, Any]]:
        """台帳 .dev/audit/budget.json を読み込む。存在しないか不正なら空辞書を返す。"""
        if self.budget_path.is_file():
            try:
                data = json.loads(self.budget_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
            except (json.JSONDecodeError, OSError):
                pass
        return {}

    def _save_budget(self, budget: dict[str, dict[str, Any]]) -> None:
        """台帳 .dev/audit/budget.json に保存する。"""
        self.budget_path.parent.mkdir(parents=True, exist_ok=True)
        self.budget_path.write_text(
            json.dumps(budget, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    def _get_head_commit_sha(self) -> str:
        """Git リポジトリの最新 HEAD コミット SHA を取得する。"""
        import subprocess

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
        except (OSError, subprocess.SubprocessError):
            return "unknown"
        return "unknown"

    def dispatch_finding(
        self,
        finding: AuditFinding,
        plan_path: Path | str | None = None,
        cycle_id: str | None = None,
    ) -> AuditFinding:
        """単一の AuditFinding を重要度トリアージし、適切な経路に振り分ける (WWW.1)。"""
        sev = (finding.severity or "P2").strip().upper()
        effective_cycle_id = cycle_id or finding.target_commit or self._get_head_commit_sha()

        # 1. INFO: どこにも書かず AuditStore にのみ保存する (status NOTED)
        if sev == "INFO":
            finding.status = "NOTED"
            self.audit_store.save_finding(finding)
            return finding

        # 2. P2: Red 検証を行わず BACKLOG.md に 1 行追記する (status BACKLOGGED)
        if sev == "P2":
            finding.status = "BACKLOGGED"
            backlog_path = self.repo_dir / "BACKLOG.md"
            if not backlog_path.exists():
                backlog_path.write_text("# BACKLOG.md\n\n", encoding="utf-8")

            backlog_content = backlog_path.read_text(encoding="utf-8")
            fid = finding.finding_id if finding.finding_id.startswith("AUD-") else f"AUD-{finding.finding_id}"

            # 同一 finding_id が既に存在する場合は追記しない（冪等）
            if f"[{fid}]" not in backlog_content and f"[{finding.finding_id}]" not in backlog_content:
                backlog_line = f"- [{fid}] {finding.description} (Severity: P2, Loc: {finding.location}, Cycle: {effective_cycle_id})"
                if not backlog_content.endswith("\n"):
                    backlog_content += "\n"
                backlog_path.write_text(backlog_content + backlog_line + "\n", encoding="utf-8")

            self.audit_store.save_finding(finding)
            return finding

        # 3. P0 / P1: 従来どおり RedTestVerifier で検証し、GENUINE_RED のみ PLAN.md に登録する
        target_plan = Path(plan_path) if plan_path else self.repo_dir / "PLAN.md"

        # 1. 再現テストコードの Red 検証
        verification = self.verifier.verify_candidate_test(finding.reproduction_test_code)
        finding.red_verification_reason = verification.reason
        finding.red_verification_output = verification.output
        if not (verification.is_reproduced and verification.reason == "GENUINE_RED"):
            finding.status = "REJECTED"
            self.audit_store.save_finding(finding)
            return finding

        finding.status = "VERIFIED_RED"
        self._register_step(finding, target_plan)
        return finding

    def _register_step(self, finding: AuditFinding, target_plan: Path) -> None:
        """GENUINE_RED な指摘を PLAN.md の ## ステップ にタスク行として登録する。"""
        import hashlib

        finding.before_commit_sha = self._get_head_commit_sha()
        finding.code_hash = hashlib.sha256(
            finding.reproduction_test_code.encode("utf-8")
        ).hexdigest()

        if not target_plan.exists():
            target_plan.write_text("# PLAN.md\n\n## ステップ\n", encoding="utf-8")

        content = target_plan.read_text(encoding="utf-8")
        lines = content.splitlines()

        # 2. 二重登録防止 (Idempotency)
        # 既に未着手 (- [ ]) の同一指摘トークンが存在する場合は二重追加を防ぐ (AUD-019)
        is_already_open = any(
            line.strip().startswith("- [ ]") and f"[{finding.finding_id}]" in line
            for line in lines
        )
        if is_already_open:
            finding.status = "DISPATCHED"
            with contextlib.suppress(ValueError):
                self.audit_store.save_finding(finding)
            return

        # 過去の完了済みタスクや既存ストアとの ID 衝突回避 (Cycle間の finding_id 再利用対策: AUD-018)
        json_file = self.audit_store.findings_dir / f"{finding.finding_id}.json"
        has_completed_collision = any(
            line.strip().startswith("- [x]") and f"[{finding.finding_id}]" in line
            for line in lines
        )
        has_store_collision = False
        if json_file.exists():
            try:
                data = json.loads(json_file.read_text(encoding="utf-8"))
                if data.get("code_hash") != finding.code_hash:
                    has_store_collision = True
            except OSError:
                pass

        if has_completed_collision or has_store_collision:
            import re

            existing_nums = [int(n) for n in re.findall(r"AUD-(?:AUD-)?(\d+)", content)]
            store_nums = [
                int(m.group(1))
                for p in self.audit_store.findings_dir.glob("*.json")
                if (m := re.search(r"(\d+)", p.stem))
            ]
            all_nums = existing_nums + store_nums
            next_num = max(all_nums, default=0) + 1
            finding.finding_id = f"AUD-{next_num:03d}"

        # 3. タスク行の作成と挿入 (TaskDispatcher互換書式)
        task_id = f"AUD-{finding.finding_id}"
        task_line = (
            f"- [ ] {task_id}. Fix: [{finding.finding_id}] {finding.description} "
            f"(Severity: {finding.severity}, Loc: {finding.location})"
        )
        step_idx = -1
        stop_idx = -1
        for idx, line in enumerate(lines):
            if line.startswith("## ステップ"):
                step_idx = idx
            elif line.startswith("## ") and step_idx != -1 and idx > step_idx:
                stop_idx = idx
                break

        if stop_idx != -1:
            lines.insert(stop_idx, task_line)
        else:
            lines.append(task_line)

        target_plan.write_text("\n".join(lines) + "\n", encoding="utf-8")
        finding.status = "DISPATCHED"
        self.audit_store.save_finding(finding)

    def _append_escalations_to_plan(
        self,
        escalated: list[tuple[AuditFinding, int]],
        target_plan: Path,
    ) -> None:
        """エスカレーションされた指摘を PLAN.md の「## 止まっていること・判断待ち」節に追記する (WWW.2)。"""
        if not target_plan.exists():
            target_plan.write_text("# PLAN.md\n\n## 止まっていること・判断待ち\n", encoding="utf-8")

        content = target_plan.read_text(encoding="utf-8")
        lines = content.splitlines()

        section_idx = -1
        for idx, line in enumerate(lines):
            if line.startswith("## 止まっていること・判断待ち"):
                section_idx = idx
                break

        new_lines: list[str] = []
        for finding, streak in escalated:
            fid = finding.finding_id if finding.finding_id.startswith("AUD-") else f"AUD-{finding.finding_id}"
            if f"[{fid}]" in content or f"[{finding.finding_id}]" in content:
                continue
            new_lines.append(
                f"- ESCALATED [{fid}] {finding.description} "
                f"(Severity: {finding.severity}, Loc: {finding.location}, Streak: {streak})"
            )

        if not new_lines:
            return

        if section_idx != -1:
            next_section_idx = len(lines)
            for idx in range(section_idx + 1, len(lines)):
                if lines[idx].startswith("## "):
                    next_section_idx = idx
                    break

            filtered_section: list[str] = []
            for line in lines[section_idx + 1:next_section_idx]:
                stripped = line.strip()
                if stripped in ("- なし。", "- なし"):
                    continue
                filtered_section.append(line)

            for nl in new_lines:
                if nl not in filtered_section:
                    filtered_section.append(nl)

            lines = lines[:section_idx + 1] + filtered_section + lines[next_section_idx:]
        else:
            lines.append("\n## 止まっていること・判断待ち")
            lines.extend(new_lines)

        target_plan.write_text("\n".join(lines) + "\n", encoding="utf-8")

    def process_findings(
        self,
        findings: list[AuditFinding],
        plan_path: Path | str | None = None,
        cycle_id: str | None = None,
    ) -> list[AuditFinding]:
        """複数の AuditFinding を処理し、正常に登録 (DISPATCHED) された指摘のリストを返す (WWW.2)。"""
        if cycle_id is None:
            first_target = findings[0].target_commit if findings else None
            effective_cycle_id = first_target or self._get_head_commit_sha()
        else:
            effective_cycle_id = cycle_id

        target_plan = Path(plan_path) if plan_path else self.repo_dir / "PLAN.md"
        budget = self._load_budget()

        if not findings:
            # 指摘が0件のサイクルでも、P0/P1の登録がなかったモジュールの streak は 0 に戻す
            for mod in list(budget.keys()):
                if budget[mod].get("last_cycle_id") != effective_cycle_id:
                    budget[mod] = {
                        "streak": 0,
                        "last_cycle_id": effective_cycle_id,
                        "last_action": "RESET",
                    }
            self._save_budget(budget)
            return []

        # 各指摘の事前評価（INFO, P2, P0/P1）
        candidate_genuine: list[tuple[AuditFinding, str]] = []
        for finding in findings:
            sev = (finding.severity or "P2").strip().upper()
            if sev in ("INFO", "P2"):
                self.dispatch_finding(finding, plan_path=target_plan, cycle_id=effective_cycle_id)
            else:
                verification = self.verifier.verify_candidate_test(finding.reproduction_test_code)
                finding.red_verification_reason = verification.reason
                finding.red_verification_output = verification.output
                if not (verification.is_reproduced and verification.reason == "GENUINE_RED"):
                    finding.status = "REJECTED"
                    self.audit_store.save_finding(finding)
                else:
                    finding.status = "VERIFIED_RED"
                    mod_key = self._extract_module_key(finding.location)
                    candidate_genuine.append((finding, mod_key))

        # モジュールごとにグループ化
        genuine_by_module: dict[str, list[AuditFinding]] = {}
        for f, mod in candidate_genuine:
            genuine_by_module.setdefault(mod, []).append(f)

        dispatched: list[AuditFinding] = []
        escalated_entries: list[tuple[AuditFinding, int]] = []

        # GENUINE_RED となった P0/P1 があるモジュールを処理
        for mod, mod_findings in genuine_by_module.items():
            mod_entry = budget.get(mod, {"streak": 0, "last_cycle_id": None, "last_action": None})
            current_streak = int(mod_entry.get("streak", 0))
            last_cycle = mod_entry.get("last_cycle_id")
            last_action = mod_entry.get("last_action")

            if last_cycle == effective_cycle_id:
                # 既にこの cycle_id で処理済み（冪等性: 前回のアクションを維持）
                for f in mod_findings:
                    if last_action == "ESCALATED":
                        f.status = "ESCALATED"
                        self.audit_store.save_finding(f)
                    else:
                        self._register_step(f, target_plan)
                        f.status = "DISPATCHED"
                        dispatched.append(f)
                continue

            # 新規サイクル
            if current_streak >= self.max_consecutive_cycles:
                # 予算上限超過 -> ESCALATED
                for f in mod_findings:
                    f.status = "ESCALATED"
                    self.audit_store.save_finding(f)
                    escalated_entries.append((f, current_streak))
                budget[mod] = {
                    "streak": current_streak,
                    "last_cycle_id": effective_cycle_id,
                    "last_action": "ESCALATED",
                }
            else:
                # 予算内 -> DISPATCHED、streak を 1 増やす（1 サイクルにつき 1 回）
                for f in mod_findings:
                    self._register_step(f, target_plan)
                    f.status = "DISPATCHED"
                    dispatched.append(f)
                budget[mod] = {
                    "streak": current_streak + 1,
                    "last_cycle_id": effective_cycle_id,
                    "last_action": "DISPATCHED",
                }

        # そのサイクルで P0/P1 の登録がなかったモジュールは streak を 0 に戻す
        for mod in list(budget.keys()):
            if (
                mod not in genuine_by_module
                and budget[mod].get("last_cycle_id") != effective_cycle_id
            ):
                budget[mod] = {
                    "streak": 0,
                    "last_cycle_id": effective_cycle_id,
                    "last_action": "RESET",
                }

        self._save_budget(budget)

        if escalated_entries:
            self._append_escalations_to_plan(escalated_entries, target_plan)

        return dispatched

