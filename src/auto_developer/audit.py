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
