from __future__ import annotations

import contextlib
import hashlib
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

    def _resolve_finding_ids(
        self,
        findings: list[AuditFinding],
        target_plan: Path,
        effective_cycle_id: str,
        budget: dict[str, dict[str, object]] | None = None,
    ) -> tuple[list[AuditFinding], list[AuditFinding]]:
        """AuditStore・PLAN.md・BACKLOG.md 上の既存 ID との衝突を判定し、採番し直す (WWW.6)。

        同一指摘（同一 ID かつ同一 code_hash）の再報告は重複登録を抑止するため戻り値から除外する（冪等）。
        ただし同一 cycle_id のリトライ時はディスパッチ状態を維持する。
        完了済みタスク（- [x]）の同一指摘は回帰として AUD-018 で採番し直し、新規タスクとして戻り値に含める。
        内容の異なる ID 衝突は AUD-018 で採番し直して戻り値に含める。

        Returns:
            (active_findings, suppressed_findings)
        """
        backlog_path = self.repo_dir / "BACKLOG.md"
        plan_content = target_plan.read_text(encoding="utf-8") if target_plan.exists() else ""
        backlog_content = backlog_path.read_text(encoding="utf-8") if backlog_path.exists() else ""
        current_budget = budget if budget is not None else self._load_budget()

        # 全既存番号の収集
        existing_nums: set[int] = set()
        for n in re.findall(r"AUD-(?:AUD-)?(\d+)", plan_content):
            existing_nums.add(int(n))
        for n in re.findall(r"AUD-(?:AUD-)?(\d+)", backlog_content):
            existing_nums.add(int(n))
        if self.audit_store.findings_dir.exists():
            for p in self.audit_store.findings_dir.glob("*.json"):
                if m := re.search(r"(\d+)", p.stem):
                    existing_nums.add(int(m.group(1)))

        # 既存 ID -> code_hash のマッピング
        known_hashes: dict[str, str] = {}
        if self.audit_store.findings_dir.exists():
            for p in self.audit_store.findings_dir.glob("*.json"):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    h = str(data.get("code_hash", "")).strip()
                    stem = p.stem
                    full_id = stem if stem.startswith("AUD-") else f"AUD-{stem}"
                    num_id = stem.removeprefix("AUD-")
                    known_hashes[full_id] = h
                    known_hashes[num_id] = h
                except (OSError, json.JSONDecodeError):
                    pass

        allocated_hashes: dict[str, str] = dict(known_hashes)

        # PLAN.md 内の未完了 (- [ ]) および完了済み (- [x]) タスクの ID と Cycle
        open_plan_ids: set[str] = set()
        open_plan_cycles: dict[str, str] = {}
        completed_plan_ids: set[str] = set()
        for line in plan_content.splitlines():
            line_str = line.strip()
            cycle_match = re.search(r"Cycle:\s*([A-Za-z0-9_-]+)", line_str)
            cycle_val = cycle_match.group(1) if cycle_match else ""
            if line_str.startswith("- [ ]"):
                for token in re.findall(r"\[(AUD-[^\]]+)\]", line_str):
                    open_plan_ids.add(token)
                    open_plan_ids.add(token[4:])
                    if cycle_val:
                        open_plan_cycles[token] = cycle_val
                        open_plan_cycles[token[4:]] = cycle_val
            elif line_str.startswith("- [x]"):
                for token in re.findall(r"\[(AUD-[^\]]+)\]", line_str):
                    completed_plan_ids.add(token)
                    completed_plan_ids.add(token[4:])

        # BACKLOG.md 内の ID 一覧
        backlog_ids: set[str] = set()
        for line in backlog_content.splitlines():
            line_str = line.strip()
            if line_str.startswith("- ["):
                for token in re.findall(r"\[(AUD-[^\]]+)\]", line_str):
                    backlog_ids.add(token)
                    backlog_ids.add(token[4:])

        active_findings: list[AuditFinding] = []
        suppressed_findings: list[AuditFinding] = []

        for finding in findings:
            # 再現テストコードから常に code_hash を確定（外部入力を過信しない）
            code_str = finding.reproduction_test_code or ""
            finding.code_hash = hashlib.sha256(code_str.encode("utf-8")).hexdigest()

            fid = finding.finding_id
            full_id = fid if fid.startswith("AUD-") else f"AUD-{fid}"
            num_id = fid.removeprefix("AUD-")

            # 1. 完了済みタスク（- [x]）との衝突判定
            if full_id in completed_plan_ids or num_id in completed_plan_ids:
                existing_hash = allocated_hashes.get(full_id) or allocated_hashes.get(num_id)
                if existing_hash == finding.code_hash:
                    # 回帰不具合: 既に未完了タスクとして再オープン中か確認
                    is_hash_already_open = any(
                        allocated_hashes.get(oid) == finding.code_hash
                        for oid in open_plan_ids
                    )
                    if is_hash_already_open:
                        # 既に未完了タスクとして再オープン中: 重複して開かない（抑制）
                        suppressed_findings.append(finding)
                        continue
                    # 新たな回帰タスクとして AUD-018 で採番
                    next_num = max(existing_nums, default=0) + 1
                    new_id = f"AUD-{next_num:03d}"
                    existing_nums.add(next_num)
                    finding.finding_id = new_id
                    allocated_hashes[new_id] = finding.code_hash
                    allocated_hashes[new_id[4:]] = finding.code_hash
                    open_plan_ids.add(new_id)
                    open_plan_ids.add(new_id[4:])
                    active_findings.append(finding)
                else:
                    # 完了済みタスクと同一IDだが内容が異なる: AUD-018 で採番し直す
                    next_num = max(existing_nums, default=0) + 1
                    new_id = f"AUD-{next_num:03d}"
                    existing_nums.add(next_num)
                    finding.finding_id = new_id
                    allocated_hashes[new_id] = finding.code_hash
                    allocated_hashes[new_id[4:]] = finding.code_hash
                    active_findings.append(finding)
                continue

            # 2. 既存ID（未着手PLAN、BACKLOG、AuditStore、バッチ内割り当て済み）との衝突判定
            is_in_open = full_id in open_plan_ids or num_id in open_plan_ids
            is_in_backlog = full_id in backlog_ids or num_id in backlog_ids
            is_allocated = full_id in allocated_hashes or num_id in allocated_hashes

            if is_in_open or is_in_backlog or is_allocated:
                existing_hash = allocated_hashes.get(full_id) or allocated_hashes.get(num_id)
                if existing_hash == finding.code_hash:
                    # 同一指摘の再報告: 同一サイクルのリトライであれば DISPATCHED 状態を維持して通過させる
                    mod_key = self._extract_module_key(finding.location)
                    last_cycle = current_budget.get(mod_key, {}).get("last_cycle_id")
                    recorded_cycle = open_plan_cycles.get(full_id) or open_plan_cycles.get(num_id)
                    if is_in_open and (recorded_cycle == effective_cycle_id or last_cycle == effective_cycle_id):
                        active_findings.append(finding)
                    else:
                        # 別サイクルまたは BACKLOG/バッチ内での同一再報告: 重複登録を抑止（抑制し、除外）
                        suppressed_findings.append(finding)
                        continue
                else:
                    # 内容の異なる衝突: AUD-018 で採番し直す
                    next_num = max(existing_nums, default=0) + 1
                    new_id = f"AUD-{next_num:03d}"
                    existing_nums.add(next_num)
                    finding.finding_id = new_id
                    allocated_hashes[new_id] = finding.code_hash
                    allocated_hashes[new_id[4:]] = finding.code_hash
                    active_findings.append(finding)
            else:
                # 衝突なし: そのままのIDで登録
                allocated_hashes[full_id] = finding.code_hash
                allocated_hashes[num_id] = finding.code_hash
                if m := re.search(r"(\d+)", fid):
                    existing_nums.add(int(m.group(1)))
                active_findings.append(finding)

        return active_findings, suppressed_findings

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
        finding.before_commit_sha = self._get_head_commit_sha()
        finding.code_hash = hashlib.sha256(
            (finding.reproduction_test_code or "").encode("utf-8")
        ).hexdigest()

        if not target_plan.exists():
            target_plan.write_text("# PLAN.md\n\n## ステップ\n", encoding="utf-8")

        content = target_plan.read_text(encoding="utf-8")
        lines = content.splitlines()

        # 2. 二重登録防止 (Idempotency)
        # 既に未着手 (- [ ]) の同一指摘トークンが存在する場合は二重追加を防ぐ (AUD-019 / WWW.6)
        fid = finding.finding_id if finding.finding_id.startswith("AUD-") else f"AUD-{finding.finding_id}"
        is_already_open = any(
            line.strip().startswith("- [ ]") and (f"[{finding.finding_id}]" in line or f"[{fid}]" in line)
            for line in lines
        )
        if is_already_open:
            finding.status = "DISPATCHED"
            with contextlib.suppress(ValueError):
                self.audit_store.save_finding(finding)
            return

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
        target_plan = Path(plan_path) if plan_path else self.repo_dir / "PLAN.md"

        if cycle_id is None:
            first_target = findings[0].target_commit if findings else None
            effective_cycle_id = first_target or self._get_head_commit_sha()
        else:
            effective_cycle_id = cycle_id

        # WWW.6: 監査役による finding_id 再利用への耐性 (全経路共通: トリアージ・予算判定より前に必ず実行)
        budget = self._load_budget()
        active_findings, suppressed_findings = (
            self._resolve_finding_ids(findings, target_plan, effective_cycle_id, budget=budget)
            if findings
            else ([], [])
        )

        # WWW.2 改訂: 報告のあったモジュール（抑止された再報告）の特定
        # 過去の判定が REJECTED である指摘の再報告は「報告」に数えない
        reported_p0_p1_mods: set[str] = set()
        for f in suppressed_findings:
            if (f.severity or "P2").strip().upper() in ("P0", "P1"):
                stored = self.audit_store.load_finding(f.finding_id)
                if stored and stored.status == "REJECTED":
                    continue
                reported_p0_p1_mods.add(self._extract_module_key(f.location))

        if not active_findings:
            # 指摘が0件のサイクル（またはすべて抑止されたサイクル）でも、escalated なモジュールは維持する (WWW.2 改訂)
            for mod in list(budget.keys()):
                if budget[mod].get("last_cycle_id") != effective_cycle_id:
                    is_escalated = bool(budget[mod].get("escalated") or budget[mod].get("last_action") == "ESCALATED")
                    if is_escalated or mod in reported_p0_p1_mods:
                        budget[mod]["last_cycle_id"] = effective_cycle_id
                        if is_escalated:
                            budget[mod]["escalated"] = True
                            budget[mod]["last_action"] = "ESCALATED"
                        else:
                            budget[mod]["last_action"] = "MAINTAINED"
                    else:
                        budget[mod] = {
                            "streak": 0,
                            "last_cycle_id": effective_cycle_id,
                            "last_action": "RESET",
                            "escalated": False,
                        }
            self._save_budget(budget)
            return []

        # 各指摘の事前評価（INFO, P2, P0/P1）
        candidate_genuine: list[tuple[AuditFinding, str]] = []
        for finding in active_findings:
            sev = (finding.severity or "P2").strip().upper()
            if sev in ("INFO", "P2"):
                self.dispatch_finding(
                    finding,
                    plan_path=target_plan,
                    cycle_id=effective_cycle_id,
                )
            else:
                verification = self.verifier.verify_candidate_test(finding.reproduction_test_code)
                finding.red_verification_reason = verification.reason
                finding.red_verification_output = verification.output
                mod_key = self._extract_module_key(finding.location)
                if not (verification.is_reproduced and verification.reason == "GENUINE_RED"):
                    finding.status = "REJECTED"
                    self.audit_store.save_finding(finding)
                else:
                    finding.status = "VERIFIED_RED"
                    candidate_genuine.append((finding, mod_key))
                    reported_p0_p1_mods.add(mod_key)

        # モジュールごとにグループ化
        genuine_by_module: dict[str, list[AuditFinding]] = {}
        for f, mod in candidate_genuine:
            genuine_by_module.setdefault(mod, []).append(f)

        dispatched: list[AuditFinding] = []
        escalated_entries: list[tuple[AuditFinding, int]] = []

        # GENUINE_RED となった P0/P1 があるモジュールを処理
        for mod, mod_findings in genuine_by_module.items():
            mod_entry = budget.get(mod, {"streak": 0, "last_cycle_id": None, "last_action": None, "escalated": False})
            current_streak = int(mod_entry.get("streak", 0))
            last_cycle = mod_entry.get("last_cycle_id")
            last_action = mod_entry.get("last_action")
            is_escalated = bool(mod_entry.get("escalated") or last_action == "ESCALATED")

            # 同一サイクルで既に GENUINE_RED のディスパッチ決定済み（MAINTAINED / RESET は再判定へ進む）
            is_cycle_already_dispatched = (
                last_cycle == effective_cycle_id
                and last_action in ("DISPATCHED", "ESCALATED")
            )
            if is_cycle_already_dispatched:
                # 既にこの cycle_id で処理済み（冪等性: 前回のアクションを維持）
                for f in mod_findings:
                    if last_action == "ESCALATED":
                        f.status = "ESCALATED"
                        f.streak = current_streak
                        self.audit_store.save_finding(f)
                    else:
                        self._register_step(f, target_plan)
                        f.status = "DISPATCHED"
                        dispatched.append(f)
                continue

            # 新規サイクル: 既にエスカレーション済み、または予算上限超過 -> ESCALATED (WWW.2 改訂 sticky)
            if is_escalated or current_streak >= self.max_consecutive_cycles:
                for f in mod_findings:
                    f.status = "ESCALATED"
                    f.streak = current_streak
                    self.audit_store.save_finding(f)
                    escalated_entries.append((f, current_streak))
                budget[mod] = {
                    "streak": current_streak,
                    "last_cycle_id": effective_cycle_id,
                    "last_action": "ESCALATED",
                    "escalated": True,
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
                    "escalated": False,
                }

        # WWW.2 改訂: 報告のなかったモジュールの streak 判定
        # - escalated なモジュールは streak を 0 に戻さない（sticky）
        # - 再報告のみのモジュール（reported_p0_p1_mods）は streak を増やしも戻しもしない（維持）
        # - 当該モジュールに P0/P1 の報告が 1 件もなかった場合のみ streak を 0 に戻す
        for mod in list(budget.keys()):
            if (
                mod not in genuine_by_module
                and budget[mod].get("last_cycle_id") != effective_cycle_id
            ):
                is_escalated = bool(budget[mod].get("escalated") or budget[mod].get("last_action") == "ESCALATED")
                if is_escalated or mod in reported_p0_p1_mods:
                    budget[mod]["last_cycle_id"] = effective_cycle_id
                    if is_escalated:
                        budget[mod]["escalated"] = True
                        budget[mod]["last_action"] = "ESCALATED"
                    else:
                        budget[mod]["last_action"] = "MAINTAINED"
                else:
                    budget[mod] = {
                        "streak": 0,
                        "last_cycle_id": effective_cycle_id,
                        "last_action": "RESET",
                        "escalated": False,
                    }

        self._save_budget(budget)

        if escalated_entries:
            self._append_escalations_to_plan(escalated_entries, target_plan)

        return dispatched

