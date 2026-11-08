from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

VALID_SEVERITIES: frozenset[str] = frozenset({"P0", "P1", "P2", "INFO"})
VALID_STATUSES: frozenset[str] = frozenset(
    {"REPORTED", "VERIFIED_RED", "DISPATCHED", "RESOLVED", "REJECTED"}
)


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
