from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

VALID_SEVERITIES: frozenset[str] = frozenset({"P0", "P1", "P2", "INFO"})
VALID_STATUSES: frozenset[str] = frozenset(
    {"REPORTED", "VERIFIED_RED", "DISPATCHED", "RESOLVED", "REJECTED"}
)


def detect_model_family(model_name: str) -> str:
    """モデル識別子から大分類ファミリ（gemini, claude, openai, mock等）を推定する。"""
    if not model_name:
        return "unknown"
    name = model_name.strip().lower()
    if "mock" in name:
        return "mock"
    if "gemini" in name:
        return "gemini"
    if any(k in name for k in ("claude", "opus", "sonnet", "haiku", "anthropic")):
        return "claude"
    if any(k in name for k in ("gpt", "o1", "o3", "o4", "openai")):
        return "openai"
    return "unknown"


def verify_model_independence(
    maker_model: str,
    auditor_model: str,
    allow_same_model_override: bool = False,
    strict: bool = True,
) -> tuple[bool, str]:
    """MakerモデルとAuditor/Checkerモデルの独立性を検証する (AGENTS.md Rule 6 / EEE.1 / FFF.3)。"""
    if not maker_model or not maker_model.strip():
        return False, "INDEPENDENT_AUDIT_VIOLATION: maker_model cannot be empty or whitespace"
    if not auditor_model or not auditor_model.strip():
        return False, "INDEPENDENT_AUDIT_VIOLATION: auditor_model cannot be empty or whitespace"

    m_norm = maker_model.strip()
    a_norm = auditor_model.strip()

    if allow_same_model_override:
        return True, f"Override enabled: Maker='{m_norm}', Auditor='{a_norm}'"

    if m_norm.lower() == a_norm.lower():
        msg = (
            f"INDEPENDENT_AUDIT_VIOLATION: maker_model '{m_norm}' and auditor_model '{a_norm}' are identical. "
            "AGENTS.md Rule 6 forbids self-audit by the identical model."
        )
        return False, msg

    m_fam = detect_model_family(m_norm)
    a_fam = detect_model_family(a_norm)

    if strict and (m_fam == "unknown" or a_fam == "unknown"):
        msg = (
            f"INDEPENDENT_AUDIT_VIOLATION: Unrecognized model family (Maker='{m_norm}' -> '{m_fam}', "
            f"Auditor='{a_norm}' -> '{a_fam}'). Cannot guarantee cross-model independence."
        )
        return False, msg

    if m_fam != "mock" and m_fam != "unknown" and m_fam == a_fam:
        msg = (
            f"INDEPENDENT_AUDIT_VIOLATION: maker_model '{m_norm}' and auditor_model '{a_norm}' "
            f"belong to the same family '{m_fam}'. AGENTS.md Rule 6 requires cross-model audit "
            "from distinct families (e.g., Gemini Maker vs Claude Opus Auditor)."
        )
        return False, msg

    return (
        True,
        f"Independent audit policy satisfied: Maker family '{m_fam}' ({m_norm}) vs Auditor family '{a_fam}' ({a_norm}).",
    )


@dataclass
class AuditConfig:
    """独立監査の設定とモデル独立性検証を保持する設定モデル。"""

    maker_model: str
    auditor_model: str
    allow_same_model_override: bool = False

    def validate(self) -> bool:
        ok, reason = verify_model_independence(
            self.maker_model,
            self.auditor_model,
            self.allow_same_model_override,
        )
        if not ok:
            raise ValueError(reason)
        return True


@dataclass
class AuditFinding:
    """独立監査役（外部または自動）によって報告された指摘モデル。"""

    finding_id: str
    severity: str
    spec_reference: str
    location: str
    description: str
    reproduction_test_code: str
    status: str = "REPORTED"
    red_verification_reason: str | None = None
    red_verification_output: str | None = None
    before_commit_sha: str | None = None
    code_hash: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AuditFinding:
        return cls(
            finding_id=data["finding_id"],
            severity=data["severity"],
            spec_reference=data["spec_reference"],
            location=data["location"],
            description=data["description"],
            reproduction_test_code=data["reproduction_test_code"],
            status=data.get("status", "REPORTED"),
            red_verification_reason=data.get("red_verification_reason"),
            red_verification_output=data.get("red_verification_output"),
            before_commit_sha=data.get("before_commit_sha"),
            code_hash=data.get("code_hash"),
        )


class AuditRunner:
    """外部監査結果または静的・動的解析指摘を受け取り、構造化された AuditFinding を生成・検証する。"""

    def parse_findings(self, records: list[dict[str, Any]]) -> list[AuditFinding]:
        findings: list[AuditFinding] = []
        required_fields = {
            "finding_id",
            "severity",
            "spec_reference",
            "location",
            "description",
            "reproduction_test_code",
        }

        for record in records:
            missing = required_fields - record.keys()
            if missing:
                raise ValueError(f"Record is missing required fields: {sorted(missing)}")

            severity = record["severity"]
            if severity not in VALID_SEVERITIES:
                raise ValueError(
                    f"Invalid severity '{severity}'. Must be one of {sorted(VALID_SEVERITIES)}"
                )

            repro_code = record["reproduction_test_code"]
            if not repro_code or not repro_code.strip():
                raise ValueError("reproduction_test_code must not be empty or whitespace")

            status = record.get("status", "REPORTED")
            if status not in VALID_STATUSES:
                raise ValueError(
                    f"Invalid status '{status}'. Must be one of {sorted(VALID_STATUSES)}"
                )

            findings.append(
                AuditFinding(
                    finding_id=record["finding_id"],
                    severity=severity,
                    spec_reference=record["spec_reference"],
                    location=record["location"],
                    description=record["description"],
                    reproduction_test_code=repro_code,
                    status=status,
                )
            )

        return findings

    def run_audit(self, audit_input: Any) -> list[AuditFinding]:
        """監査入力を受け取り、構造化された指摘リストを返す。"""
        if isinstance(audit_input, list):
            return self.parse_findings(audit_input)
        raise TypeError(f"Unsupported audit input type: {type(audit_input).__name__}")


class IndependentAuditRunner:
    """SPEC.md と git diff のみを厳格に入力とし、独立したLLMセッションから構造化 AuditFinding を抽出するランナー (EEE.2)。"""

    DEFAULT_PROMPT_TEMPLATE = (
        "You are an Independent Code Auditor (Checker/Auditor).\n"
        "Review the following SPEC.md and git diff according to AGENTS.md rules.\n"
        "Your role is critical audit only: identify any bugs, specification deviations, or unmitigated risks.\n"
        "For each issue found, provide a concrete Python unittest reproduction test that genuinely reproduces the bug.\n\n"
        "--- SPEC.md ---\n{spec_content}\n\n"
        "--- GIT DIFF ---\n{git_diff}\n\n"
        "--- OUTPUT FORMAT ---\n"
        "Return ONLY a JSON array of findings (or markdown codeblock ```json ... ```):\n"
        "[\n"
        "  {\n"
        '    "finding_id": "<ID>",\n'
        '    "severity": "P0" | "P1" | "P2" | "INFO",\n'
        '    "spec_reference": "<SPEC ref>",\n'
        '    "location": "<path:line>",\n'
        '    "description": "<summary>",\n'
        '    "reproduction_test_code": "<executable Python unittest code>",\n'
        '    "status": "REPORTED"\n'
        "  }\n"
        "]\n"
        'If no issues are found, return an empty array `[]` or `{"findings": []}`.'
    )

    def __init__(
        self,
        auditor_cmd_template: str = "",
        model_name: str = "claude-opus-5.5",
        timeout_sec: int = 600,
        config: AuditConfig | None = None,
    ) -> None:
        self.auditor_cmd_template = auditor_cmd_template
        self.model_name = model_name
        self.timeout_sec = timeout_sec
        self.config = config
        self.audit_runner = AuditRunner()

    def build_audit_prompt(self, spec_content: str, git_diff: str) -> str:
        return (
            self.DEFAULT_PROMPT_TEMPLATE
            .replace("{spec_content}", spec_content)
            .replace("{git_diff}", git_diff)
        )

    def parse_response(self, output: str) -> list[AuditFinding]:
        import json
        import re

        json_match = re.search(r"```json\s*(.*?)\s*```", output, re.DOTALL)
        raw_json_str = json_match.group(1) if json_match else output.strip()

        data: Any = None
        try:
            data = json.loads(raw_json_str)
        except json.JSONDecodeError:
            bracket_match = re.search(r"(\[.*\]|\{.*\})", raw_json_str, re.DOTALL)
            if bracket_match:
                try:
                    data = json.loads(bracket_match.group(1))
                except json.JSONDecodeError:
                    pass

        if data is None:
            raise ValueError(f"Could not parse valid JSON from audit response: {output[:200]}")

        if isinstance(data, dict):
            if "findings" in data and isinstance(data["findings"], list):
                data = data["findings"]
            else:
                data = [data] if "finding_id" in data else []

        if not isinstance(data, list):
            raise TypeError(f"Expected a list of findings, got {type(data).__name__}")

        if not data:
            return []

        return self.audit_runner.parse_findings(data)

    def audit(self, spec_content: str, git_diff: str) -> list[AuditFinding]:
        import subprocess

        # 独立性ポリシーの事前検証 (FFF.3, GGG.3)
        if self.config:
            self.config.validate()
            if self.config.auditor_model != self.model_name:
                raise ValueError(
                    f"INDEPENDENT_AUDIT_VIOLATION: Runner model '{self.model_name}' "
                    f"does not match config.auditor_model '{self.config.auditor_model}'"
                )
        else:
            ok, reason = verify_model_independence(
                maker_model="gemini-3.8-flash",
                auditor_model=self.model_name,
                strict=True,
            )
            if not ok:
                raise ValueError(reason)

        if not self.auditor_cmd_template:
            raise RuntimeError(
                "No auditor_cmd_template configured for IndependentAuditRunner (Fail-Closed)"
            )

        prompt = self.build_audit_prompt(spec_content, git_diff)
        cmd_str = self.auditor_cmd_template.replace("{model}", self.model_name)
        proc = subprocess.run(
            ["bash", "-c", cmd_str],
            input=prompt,
            capture_output=True,
            text=True,
            check=False,
            timeout=self.timeout_sec,
        )
        if proc.returncode != 0:
            raise RuntimeError(
                f"Auditor execution failed with exit {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
            )
        output = proc.stdout
        return self.parse_response(output)


class AuditResolutionManager:
    """AUDタスク解決時のBefore Red / After Green検証、Evidence拘束、および免疫テスト自動昇格を管理する (EEE.3)。"""

    def __init__(self, repo_dir: Path | None = None) -> None:
        from auto_developer.audit_store import AuditStore

        self.repo_dir = repo_dir or Path.cwd()
        self.audit_store = AuditStore(repo_dir=self.repo_dir)

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

    def extract_finding_id(self, task_id: str) -> str | None:
        if task_id.startswith("AUD-"):
            return task_id[4:]
        return None

    def resolve_task(self, task_id: str) -> dict[str, Any]:
        """AUDタスクのBefore Red / After Greenを実コミット上で検証し、免疫テストとして自動昇格配置する (HHH.1)。"""
        import hashlib
        import re
        import subprocess
        import time

        from auto_developer.verifier import RedTestVerifier

        finding_id = self.extract_finding_id(task_id)
        if not finding_id:
            return {"success": False, "reason": f"Task ID '{task_id}' is not an AUD task"}

        finding = self.audit_store.get_finding(finding_id)
        if not finding:
            return {"success": False, "reason": f"Finding '{finding_id}' not found in audit store"}

        # 0. Git リポジトリおよび作業ツリーのクリーン性検証 (HHH.1)
        is_git_repo = (self.repo_dir / ".git").is_dir()
        if not is_git_repo:
            return {
                "success": False,
                "reason": "UNCOMMITTED_AFTER_STATE: Repository is not a git repository",
                "error": "UNCOMMITTED_AFTER_STATE",
            }

        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode != 0:
            return {
                "success": False,
                "reason": "UNCOMMITTED_AFTER_STATE: Failed to inspect git status",
                "error": "UNCOMMITTED_AFTER_STATE",
            }

        uncommitted_files: list[str] = []
        for line in status_proc.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split(maxsplit=1)
            if len(parts) >= 2:
                file_path = parts[1].strip()
                if " -> " in file_path:
                    file_path = file_path.split(" -> ")[1].strip()
                if not file_path.startswith(".dev/") and file_path != ".dev":
                    uncommitted_files.append(file_path)

        if uncommitted_files:
            return {
                "success": False,
                "reason": f"UNCOMMITTED_AFTER_STATE: Working tree contains uncommitted changes: {uncommitted_files}",
                "error": "UNCOMMITTED_AFTER_STATE",
            }

        # 1. Before Commit の実在性検証 (HHH.1)
        before_commit = (finding.before_commit_sha or "").strip()
        if not before_commit or before_commit == "unknown":
            return {
                "success": False,
                "reason": f"INVALID_BEFORE_COMMIT: finding.before_commit_sha is invalid or missing ('{before_commit}')",
                "error": "INVALID_BEFORE_COMMIT",
            }

        cat_proc = subprocess.run(
            ["git", "cat-file", "-e", f"{before_commit}^{{commit}}"],
            cwd=self.repo_dir,
            capture_output=True,
            check=False,
        )
        if cat_proc.returncode != 0:
            return {
                "success": False,
                "reason": f"INVALID_BEFORE_COMMIT: before_commit_sha '{before_commit}' is not a valid commit in git",
                "error": "INVALID_BEFORE_COMMIT",
            }

        # 2. After Commit の前進および子孫関係の検証 (HHH.1)
        after_commit = self._get_head_commit_sha()
        if not after_commit or after_commit == "unknown":
            return {
                "success": False,
                "reason": "INVALID_AFTER_COMMIT: Failed to obtain valid HEAD commit",
                "error": "INVALID_AFTER_COMMIT",
            }

        if after_commit == before_commit:
            return {
                "success": False,
                "reason": f"NO_COMMIT_ADVANCE: after_commit '{after_commit}' is identical to before_commit",
                "error": "NO_COMMIT_ADVANCE",
            }

        ancestor_proc = subprocess.run(
            ["git", "merge-base", "--is-ancestor", before_commit, after_commit],
            cwd=self.repo_dir,
            capture_output=True,
            check=False,
        )
        if ancestor_proc.returncode != 0:
            return {
                "success": False,
                "reason": f"NO_COMMIT_ADVANCE: after_commit '{after_commit}' is not a descendant of before_commit '{before_commit}'",
                "error": "NO_COMMIT_ADVANCE",
            }

        # 3. Before Red 真正性の検証 (FFF.2)
        if finding.status not in ("VERIFIED_RED", "DISPATCHED"):
            return {
                "success": False,
                "reason": (
                    f"UNVERIFIED_BEFORE_RED: Finding '{finding_id}' has status '{finding.status}', "
                    "expected 'VERIFIED_RED' or 'DISPATCHED'"
                ),
                "error": "UNVERIFIED_BEFORE_RED",
            }

        if finding.red_verification_reason != "GENUINE_RED":
            return {
                "success": False,
                "reason": (
                    f"UNVERIFIED_BEFORE_RED: Finding '{finding_id}' lacks verified genuine red proof "
                    f"(red_verification_reason='{finding.red_verification_reason}')"
                ),
                "error": "UNVERIFIED_BEFORE_RED",
            }

        repro_code = self.audit_store.get_repro_test(finding_id)
        if not repro_code:
            repro_code = finding.reproduction_test_code
        if not repro_code:
            return {
                "success": False,
                "reason": f"No reproduction test code found for finding '{finding_id}'",
            }

        # 再現テストコードの改ざん検知
        if finding.code_hash:
            actual_repro_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
            if actual_repro_hash != finding.code_hash:
                return {
                    "success": False,
                    "reason": (
                        f"TAMPERED_REPRO_TEST: reproduction test code hash mismatch "
                        f"(expected={finding.code_hash[:8]}, actual={actual_repro_hash[:8]})"
                    ),
                    "error": "TAMPERED_REPRO_TEST",
                }

        # 4. 修正前コミット (Before Red) および修正後コミット (After Green) の隔離環境での再実証 (HHH.1)
        verifier = RedTestVerifier(repo_dir=self.repo_dir)

        before_verification = verifier.verify_candidate_test(repro_code, commit=before_commit)
        if not (before_verification.is_reproduced and before_verification.reason == "GENUINE_RED"):
            return {
                "success": False,
                "reason": (
                    f"BEFORE_RED_NOT_REPRODUCED: test did not genuinely reproduce red on before_commit '{before_commit[:8]}' "
                    f"(reason={before_verification.reason}, exit={before_verification.exit_code}):\n{before_verification.output}"
                ),
                "error": "BEFORE_RED_NOT_REPRODUCED",
            }

        after_verification = verifier.verify_candidate_test(repro_code, commit=after_commit)
        if not (after_verification.reason == "ALREADY_GREEN" and after_verification.exit_code == 0):
            return {
                "success": False,
                "reason": (
                    f"AFTER_GREEN_FAILED: test still failed or produced error on after_commit '{after_commit[:8]}' "
                    f"(reason={after_verification.reason}, exit={after_verification.exit_code}):\n{after_verification.output}"
                ),
                "error": "AFTER_GREEN_FAILED",
            }

        # 5. 免疫テストとしての昇格・配置 (非破壊的確定 FFF.1)
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", finding_id)
        immunity_rel_path = f"tests/immunity/test_immunity_{safe_name}.py"
        target_path = self.repo_dir / immunity_rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)

        is_already_promoted = False
        if target_path.exists():
            existing_content = target_path.read_text(encoding="utf-8")
            existing_hash = hashlib.sha256(existing_content.encode("utf-8")).hexdigest()
            candidate_hash = hashlib.sha256(repro_code.encode("utf-8")).hexdigest()
            if existing_hash != candidate_hash:
                return {
                    "success": False,
                    "reason": (
                        f"IMMUNITY_COLLISION: target immunity test '{immunity_rel_path}' already exists "
                        f"with different content (hash={existing_hash[:8]} vs candidate={candidate_hash[:8]}). "
                        "Refusing to overwrite existing immunity test."
                    ),
                    "error": "IMMUNITY_COLLISION",
                }
            is_already_promoted = True

        if not is_already_promoted:
            target_path.write_text(repro_code, encoding="utf-8")

        # 6. 免疫テスト安全性の機械的検証 (AST 検査 & 不干渉)
        from auto_developer.guard import GitGuard, validate_test_ast

        if not validate_test_ast(target_path):
            if not is_already_promoted:
                target_path.unlink(missing_ok=True)
            return {
                "success": False,
                "reason": "PROMOTION_FAILED: Promoted test failed AST safety validation",
            }

        guard = GitGuard(repo_dir=self.repo_dir)
        if not guard.verify_mutual_non_interference(immunity_rel_path):
            if not is_already_promoted:
                target_path.unlink(missing_ok=True)
            return {
                "success": False,
                "reason": "PROMOTION_FAILED: Promoted test interfered with existing test suite",
            }

        proof = {
            "finding_id": finding_id,
            "before_commit": before_commit,
            "after_commit": after_commit,
            "repro_test_sha256": finding.code_hash or hashlib.sha256(repro_code.encode("utf-8")).hexdigest(),
            "before_status": "VERIFIED_RED",
            "after_status": "GREEN",
            "before_result": {
                "reason": before_verification.reason,
                "exit_code": before_verification.exit_code,
            },
            "after_result": {
                "reason": after_verification.reason,
                "exit_code": after_verification.exit_code,
            },
            "before_red_reason": before_verification.reason,
            "after_green_reason": after_verification.reason,
            "immunity_test_path": immunity_rel_path,
            "resolved_at": time.time(),
        }

        # 7. LESSONS.md への自動教訓記録 (EEE.4 / FFF.3: Fail-Closed)
        lesson_recorded = self.record_lesson(finding, proof)
        if not lesson_recorded:
            finding.status = "LEARNING_RECORD_FAILED"
            self.audit_store.save_finding(finding)
            return {
                "success": False,
                "reason": "LEARNING_RECORD_FAILED: Failed to record lesson in LESSONS.md",
                "error": "LEARNING_RECORD_FAILED",
                "proof": proof,
            }

        finding.status = "RESOLVED"
        self.audit_store.save_finding(finding)

        return {
            "success": True,
            "proof": proof,
            "finding": finding.to_dict(),
            "immunity_test_path": immunity_rel_path,
        }

    def record_lesson(
        self,
        finding: AuditFinding,
        proof: dict[str, Any],
        lessons_path: Path | None = None,
    ) -> bool:
        """解決されたAUD指摘から LESSONS.md に構造化された教訓を自動追記する (EEE.4)。"""
        from datetime import datetime

        target_path = lessons_path or (self.repo_dir / "LESSONS.md")
        if not target_path.exists():
            return False

        content = target_path.read_text(encoding="utf-8")
        token = f"[{finding.finding_id}]"
        if token in content:
            return True  # 既に記録済み (二重追記防止)

        today_str = datetime.now().strftime("%Y-%m-%d")
        new_lesson = (
            f"### [{today_str}] [{finding.finding_id}] {finding.description}\n"
            f"- Context: {finding.location} (Severity: {finding.severity}, Spec: {finding.spec_reference})\n"
            f"- Trap: {finding.description}\n"
            f"- Rule: Ensure compliance with {finding.spec_reference} to prevent regression of {finding.finding_id}.\n"
            f"- Rung: 検査 & 作り（自動昇格免疫テスト）\n"
            f"- Immunity Test: {proof.get('immunity_test_path', 'unknown')}\n"
            f"- Proof: Verified Before Red ({proof.get('before_status', 'VERIFIED_RED')}) and After Green ({proof.get('after_status', 'GREEN')})\n\n"
        )

        header = "## 恒久教訓インデックス\n"
        if header in content:
            parts = content.split(header, 1)
            updated_content = parts[0] + header + "\n" + new_lesson + parts[1].lstrip("\n")
        else:
            updated_content = content.rstrip() + "\n\n" + header + "\n" + new_lesson

        target_path.write_text(updated_content, encoding="utf-8")
        return True


@dataclass
class LessonEntry:
    title: str
    date: str
    context: str = ""
    trap: str = ""
    rule: str = ""
    rung: str = ""
    immunity_test: str = ""
    proof: str = ""
    raw_markdown: str = ""


class LessonInjector:
    """LESSONS.md から関連する教訓を検索し、Makerプロンプト/コンテキストへ注入する (FFF.4 / Level 7 Rule 10)。"""

    def __init__(
        self,
        repo_dir: Path | str | None = None,
        lessons_path: Path | str | None = None,
    ) -> None:
        self.repo_dir = Path(repo_dir) if repo_dir else Path.cwd()
        self.lessons_path = Path(lessons_path) if lessons_path else self.repo_dir / "LESSONS.md"

    def parse_lessons(self) -> list[LessonEntry]:
        """LESSONS.md の『## 恒久教訓インデックス』配下の教訓エントリを構造化パースする。"""
        import re

        if not self.lessons_path.exists():
            return []

        content = self.lessons_path.read_text(encoding="utf-8")
        header = "## 恒久教訓インデックス"
        if header in content:
            _, body = content.split(header, 1)
        else:
            body = content

        entries: list[LessonEntry] = []
        raw_sections = body.split("\n### ")
        for sec in raw_sections:
            sec = sec.strip()
            if not sec or sec.startswith("#"):
                continue

            lines = sec.splitlines()
            title_line = lines[0].strip()
            date_match = re.match(r"^\[(.*?)\]\s*(.*)$", title_line)
            date_str = date_match.group(1) if date_match else ""
            title_str = title_line

            context = ""
            trap = ""
            rule = ""
            rung = ""
            immunity_test = ""
            proof = ""

            for line in lines[1:]:
                line_s = line.strip()
                if line_s.startswith("- Context:"):
                    context = line_s[len("- Context:") :].strip()
                elif line_s.startswith("- Trap:"):
                    trap = line_s[len("- Trap:") :].strip()
                elif line_s.startswith("- Rule:"):
                    rule = line_s[len("- Rule:") :].strip()
                elif line_s.startswith("- Rung:"):
                    rung = line_s[len("- Rung:") :].strip()
                elif line_s.startswith("- Immunity Test:"):
                    immunity_test = line_s[len("- Immunity Test:") :].strip()
                elif line_s.startswith("- Proof:"):
                    proof = line_s[len("- Proof:") :].strip()

            entries.append(
                LessonEntry(
                    title=title_str,
                    date=date_str,
                    context=context,
                    trap=trap,
                    rule=rule,
                    rung=rung,
                    immunity_test=immunity_test,
                    proof=proof,
                    raw_markdown=f"### {sec}",
                )
            )

        return entries

    def find_relevant_lessons(self, query: str, max_results: int = 5) -> list[LessonEntry]:
        """クエリ（タスク名、ファイルパス、説明文等）から関連教訓をスコアリング検索する。"""
        import re

        entries = self.parse_lessons()
        if not entries:
            return []

        if not query or not query.strip():
            return entries[:max_results]

        tokens = [t.lower() for t in re.findall(r"[a-zA-Z0-9_\-\.]+", query) if len(t) >= 2]
        if not tokens:
            return entries[:max_results]

        scored: list[tuple[int, LessonEntry]] = []
        for entry in entries:
            score = 0
            haystack_title = entry.title.lower()
            haystack_ctx = entry.context.lower()
            haystack_trap = entry.trap.lower()
            haystack_rule = entry.rule.lower()

            for tok in tokens:
                if tok in haystack_title:
                    score += 10
                if tok in haystack_ctx:
                    score += 6
                if tok in haystack_trap:
                    score += 4
                if tok in haystack_rule:
                    score += 4

            if score > 0:
                scored.append((score, entry))

        if not scored:
            return entries[:max_results]

        scored.sort(key=lambda x: x[0], reverse=True)
        return [e for _, e in scored[:max_results]]

    def format_injection(self, entries: list[LessonEntry]) -> str:
        """Maker プロンプトやコンテキストに注入するための Markdown ブロックを生成する。"""
        if not entries:
            return ""
        lines = ["## 関連する教訓・Traps (from LESSONS.md / Rule 10)"]
        for entry in entries:
            lines.append(f"### {entry.title}")
            if entry.trap:
                lines.append(f"- Trap: {entry.trap}")
            if entry.rule:
                lines.append(f"- Rule: {entry.rule}")
            if entry.immunity_test:
                lines.append(f"- Immunity Test: {entry.immunity_test}")
            lines.append("")
        return "\n".join(lines).strip()
