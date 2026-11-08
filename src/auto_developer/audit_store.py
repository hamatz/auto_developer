from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from auto_developer.audit import AuditFinding


class AuditStore:
    """監査指摘 (AuditFinding) および再現テストコードの永続化管理を担当する。"""

    def __init__(self, audit_dir: Path | str | None = None) -> None:
        self.audit_dir = Path(audit_dir) if audit_dir else Path(".dev/audit")
        self.findings_dir = self.audit_dir / "findings"
        self.repro_tests_dir = self.audit_dir / "repro_tests"

    def _ensure_dirs(self) -> None:
        self.findings_dir.mkdir(parents=True, exist_ok=True)
        self.repro_tests_dir.mkdir(parents=True, exist_ok=True)

    def save_finding(self, finding: AuditFinding) -> tuple[Path, Path]:
        """指摘メタデータを JSON に、再現コードを .py に保存する。"""
        self._ensure_dirs()
        py_filename = f"{finding.finding_id}.py"
        json_filename = f"{finding.finding_id}.json"

        py_path = self.repro_tests_dir / py_filename
        json_path = self.findings_dir / json_filename

        # 再現テストコードの書き込み
        py_path.write_text(finding.reproduction_test_code, encoding="utf-8")

        # ハッシュ計算
        code_bytes = finding.reproduction_test_code.encode("utf-8")
        code_hash = hashlib.sha256(code_bytes).hexdigest()

        # メタデータの作成と書き込み
        data: dict[str, Any] = finding.to_dict()
        data["code_hash"] = code_hash
        data["reproduction_test_file"] = str(py_path.resolve())

        json_path.write_text(
            json.dumps(data, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        return json_path, py_path

    def load_finding(self, finding_id: str) -> AuditFinding | None:
        """保存された JSON から AuditFinding を復元する。"""
        json_path = self.findings_dir / f"{finding_id}.json"
        if not json_path.exists():
            return None

        data = json.loads(json_path.read_text(encoding="utf-8"))
        py_path = self.repro_tests_dir / f"{finding_id}.py"
        if py_path.exists():
            data["reproduction_test_code"] = py_path.read_text(encoding="utf-8")

        return AuditFinding.from_dict(data)
