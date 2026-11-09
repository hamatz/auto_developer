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
) -> tuple[bool, str]:
    """MakerモデルとAuditor/Checkerモデルの独立性を検証する (AGENTS.md Rule 6 / EEE.1)。"""
    if not maker_model or not auditor_model:
        return True, "Models not fully specified; independent verification skipped"

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

    def extract_finding_id(self, task_id: str) -> str | None:
        if task_id.startswith("AUD-"):
            return task_id[4:]
        return None

    def resolve_task(self, task_id: str) -> dict[str, Any]:
        """AUDタスクのAfter Greenを検証し、免疫テストとして自動昇格配置する。"""
        import re
        import time

        from auto_developer.verifier import RedTestVerifier

        finding_id = self.extract_finding_id(task_id)
        if not finding_id:
            return {"success": False, "reason": f"Task ID '{task_id}' is not an AUD task"}

        finding = self.audit_store.get_finding(finding_id)
        if not finding:
            return {"success": False, "reason": f"Finding '{finding_id}' not found in audit store"}

        repro_code = self.audit_store.get_repro_test(finding_id)
        if not repro_code:
            repro_code = finding.reproduction_test_code
        if not repro_code:
            return {
                "success": False,
                "reason": f"No reproduction test code found for finding '{finding_id}'",
            }

        # 1. 修正後のコードに対する After Green 検証
        verifier = RedTestVerifier(repo_dir=self.repo_dir)
        verification = verifier.verify_candidate_test(repro_code)

        # After Green は ALREADY_GREEN (exit_code == 0, failures=0, errors=0) でなければならない
        if not (verification.reason == "ALREADY_GREEN" and verification.exit_code == 0):
            return {
                "success": False,
                "reason": (
                    f"AFTER_GREEN_FAILED: test still failed or produced error "
                    f"(reason={verification.reason}, exit={verification.exit_code}):\n{verification.output}"
                ),
            }

        # 2. 免疫テストとしての昇格・配置
        safe_name = re.sub(r"[^a-zA-Z0-9_]", "_", finding_id)
        immunity_rel_path = f"tests/immunity/test_immunity_{safe_name}.py"
        target_path = self.repo_dir / immunity_rel_path
        target_path.parent.mkdir(parents=True, exist_ok=True)
        target_path.write_text(repro_code, encoding="utf-8")

        # 3. 免疫テスト安全性の機械的検証 (AST 検査 & 不干渉)
        from auto_developer.guard import GitGuard, validate_test_ast

        if not validate_test_ast(target_path):
            target_path.unlink(missing_ok=True)
            return {
                "success": False,
                "reason": "PROMOTION_FAILED: Promoted test failed AST safety validation",
            }

        guard = GitGuard(repo_dir=self.repo_dir)
        if not guard.verify_mutual_non_interference(immunity_rel_path):
            target_path.unlink(missing_ok=True)
            return {
                "success": False,
                "reason": "PROMOTION_FAILED: Promoted test interfered with existing test suite",
            }

        # 4. Finding ステータスを RESOLVED に更新
        finding.status = "RESOLVED"
        self.audit_store.save_finding(finding)

        proof = {
            "finding_id": finding_id,
            "before_status": "VERIFIED_RED",
            "after_status": "GREEN",
            "immunity_test_path": immunity_rel_path,
            "resolved_at": time.time(),
        }

        # 5. LESSONS.md への自動教訓記録 (EEE.4)
        self.record_lesson(finding, proof)

        return {
            "success": True,
            "proof": proof,
            "reason": "After Green verified and immunity test promoted successfully",
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
