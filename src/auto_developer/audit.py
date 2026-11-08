from __future__ import annotations

from dataclasses import asdict, dataclass
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
