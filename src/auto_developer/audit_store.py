from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from auto_developer.audit import AuditFinding


class AuditStore:
    """監査指摘 (AuditFinding) および再現テストコードの永続化管理を担当する。"""

    def __init__(
        self,
        audit_dir: Path | str | None = None,
        repo_dir: Path | str | None = None,
    ) -> None:
        if repo_dir is not None and audit_dir is None:
            self.audit_dir = Path(repo_dir) / ".dev" / "audit"
        elif audit_dir is not None:
            self.audit_dir = Path(audit_dir)
        else:
            self.audit_dir = Path(".dev/audit")

        self.findings_dir = self.audit_dir / "findings"
        self.repro_tests_dir = self.audit_dir / "repro_tests"

    def _ensure_dirs(self) -> None:
        self.findings_dir.mkdir(parents=True, exist_ok=True)
        self.repro_tests_dir.mkdir(parents=True, exist_ok=True)

    def save_finding(self, finding: AuditFinding) -> tuple[Path, Path]:
        """指摘メタデータを JSON に、再現コードを .py に保存する。

        初回登録時に code_hash を確定し、再保存時のコード改ざん・ハッシュ上書きを拒絶する (GGG.2)。
        """
        self._ensure_dirs()
        py_filename = f"{finding.finding_id}.py"
        json_filename = f"{finding.finding_id}.json"

        py_path = self.repro_tests_dir / py_filename
        json_path = self.findings_dir / json_filename

        if json_path.exists():
            existing_data = json.loads(json_path.read_text(encoding="utf-8"))
            existing_hash = str(existing_data.get("code_hash", "")).strip()

            # 1. 保存対象の再現テストコードが既存の code_hash と一致するか検証
            if finding.reproduction_test_code:
                candidate_hash = hashlib.sha256(
                    finding.reproduction_test_code.encode("utf-8")
                ).hexdigest()
                if existing_hash and candidate_hash != existing_hash:
                    raise ValueError(
                        f"CODE_HASH_TAMPERED: Cannot overwrite immutable repro test for '{finding.finding_id}'. "
                        f"Expected {existing_hash}, got candidate {candidate_hash}"
                    )
            elif existing_hash and py_path.exists():
                # 空の場合は既存コードを維持
                finding.reproduction_test_code = py_path.read_text(encoding="utf-8")

            # 2. ディスク上の .py ファイルが外部改変されていないか検証
            if py_path.exists() and existing_hash:
                disk_code = py_path.read_text(encoding="utf-8")
                disk_hash = hashlib.sha256(disk_code.encode("utf-8")).hexdigest()
                if disk_hash != existing_hash:
                    raise ValueError(
                        f"CODE_HASH_TAMPERED: Disk file '{py_path.name}' was modified externally! "
                        f"Expected {existing_hash}, got disk {disk_hash}"
                    )

            code_hash = existing_hash
        else:
            # 初回登録：再現テストコードを保存し、code_hash を不変値として確定
            py_path.write_text(finding.reproduction_test_code, encoding="utf-8")
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

    def get_finding(self, finding_id: str) -> AuditFinding | None:
        """load_finding のエイリアス。"""
        return self.load_finding(finding_id)

    def get_repro_test(self, finding_id: str) -> str | None:
        """保存された再現テストコードを読み込む。"""
        py_path = self.repro_tests_dir / f"{finding_id}.py"
        if py_path.exists():
            return py_path.read_text(encoding="utf-8")
        return None
