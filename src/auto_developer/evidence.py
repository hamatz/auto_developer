from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, cast


def _extract_diff_rel_path(header_line: str) -> str:
    """diff --git ヘッダ行から変更対象ファイルの相対パスを抽出する。"""
    body = header_line.rstrip("\r\n")
    if body.startswith('diff --git "'):
        parts = body.split('" "')
        if len(parts) == 2:
            rhs = parts[1].rstrip('"')
            return rhs.removeprefix("b/")
    idx = body.rfind(" b/")
    if idx != -1:
        return body[idx + 3 :]
    return body


def _is_gate8_secret_filename(rel_path: str) -> bool:
    """GATE-8: .env*・鍵ファイル・SSH鍵名のみをファイル単位の省略対象とする（secret/token/credential は除外）。"""
    from auto_developer.guard import GitGuard

    base_name = Path(rel_path).name
    return any(pattern.search(base_name) for pattern in GitGuard.SECRET_EXCLUDE_PATTERNS[:3])


def redact_canonical_diff(raw_diff: bytes) -> bytes:
    """GATE-8: 正規の差分から INV-5 の規則に従い機密行・機密ファイル内容を決定的に省略する。"""
    if not raw_diff:
        return raw_diff

    from auto_developer.guard import GitGuard

    lines = raw_diff.splitlines(keepends=True)
    sections: list[list[bytes]] = []
    for line in lines:
        if line.startswith(b"diff --git "):
            sections.append([line])
        elif sections:
            sections[-1].append(line)
        else:
            sections.append([line])

    out: list[bytes] = []
    for section in sections:
        first_text = section[0].decode("utf-8", errors="replace")
        if first_text.startswith("diff --git "):
            rel_path = _extract_diff_rel_path(first_text)
            if _is_gate8_secret_filename(rel_path):
                out.append(f"[auto-dev: INV-5 の規則により省略: {rel_path} (変更あり)]\n".encode())
                continue

        for line in section:
            if (
                line.startswith((b"+", b"-", b" "))
                and not line.startswith((b"+++ ", b"--- "))
                and GitGuard.has_secret_content(line[1:])
            ):
                ending = b"\n" if line.endswith(b"\n") else b""
                out.append("[auto-dev: INV-5 の規則により省略]".encode() + ending)
            else:
                out.append(line)

    return b"".join(out)


def compute_canonical_diff(work_dir: Path, checkpoint: str) -> bytes:
    """GATE-5 / GATE-8: 作業ツリーのインデックスを変更せず、checkpoint から未追跡ファイルを含む正規の差分を算出し、INV-5 機密を省略する。"""
    with tempfile.TemporaryDirectory() as tmp_dir:
        tmp_index = Path(tmp_dir) / "index"
        env = dict(os.environ)
        env["GIT_INDEX_FILE"] = str(tmp_index)
        subprocess.run(
            ["git", "add", "-A"],
            cwd=work_dir,
            env=env,
            check=True,
            capture_output=True,
        )
        proc = subprocess.run(
            ["git", "-c", "core.quotePath=false", "diff", "--cached", "--no-color", "--no-ext-diff", "--no-renames", checkpoint],
            cwd=work_dir,
            env=env,
            check=True,
            capture_output=True,
        )
        return redact_canonical_diff(proc.stdout)


def resolve_evidence_records_dir(repo_dir: Path | None = None) -> Path:
    """BDL-5: EvidenceStore の保存規則に従ってレコード格納ディレクトリを1つの関数で解決する。"""
    resolved_repo = repo_dir or Path.cwd()
    git_path = resolved_repo / ".git"
    if git_path.is_dir():
        return git_path / "nightly_evidence" / "records"
    return resolved_repo / ".dev" / "evidence"


class EvidenceStore:
    """検証合格の証跡（Evidence Token）の発行・検証・失効および監査レコード（Evidence Record）の永続化を管理するモジュール。"""

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()
        git_path = self.repo_dir / ".git"
        if git_path.is_dir():
            self.evidence_dir = git_path / "nightly_evidence"
        else:
            self.evidence_dir = self.repo_dir / ".dev" / "evidence"
        self.records_dir = resolve_evidence_records_dir(self.repo_dir)

    def issue_evidence(
        self,
        task_id: str,
        checkpoint: str,
        tree_sha: str = "",
        spec_sha: str = "",
        details: dict[str, Any] | None = None,
        red_first: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """検証合格の証跡を発行し、.git 配下に永続化する。"""
        import hashlib
        import json
        import time

        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        now = time.time()
        raw = f"{task_id}:{checkpoint}:{tree_sha}:{spec_sha}:{now}"
        token = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        evidence: dict[str, Any] = {
            "task_id": task_id,
            "checkpoint": checkpoint,
            "tree_sha": tree_sha,
            "spec_sha": spec_sha,
            "issued_at": now,
            "token": token,
            "details": details or {},
        }
        if red_first is not None:
            evidence["red_first"] = red_first
        path = self.evidence_dir / f"task_{task_id}.json"
        path.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
        return evidence

    def verify_and_consume(
        self,
        task_id: str,
        checkpoint: str | None = None,
        expected_tree_sha: str | None = None,
        expected_spec_sha: str | None = None,
    ) -> bool:
        """指定タスクの有効な証跡が存在するか検証し、Tree SHA と SPEC ハッシュが一致すれば消費して監査レコードを作成する。"""
        import json
        import time

        path = self.evidence_dir / f"task_{task_id}.json"
        if not path.exists():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if str(data.get("task_id")) != str(task_id):
                return False
            if checkpoint and str(data.get("checkpoint")) != str(checkpoint):
                return False
            if expected_tree_sha and str(data.get("tree_sha")) != str(expected_tree_sha):
                return False
            if expected_spec_sha and str(data.get("spec_sha")) != str(expected_spec_sha):
                return False

            # トークンを失効（ワンタイム消費・リプレイ防止）
            path.unlink()

            # 永続監査レコードを作成・保存 (機能N / P1-1)
            self.records_dir.mkdir(parents=True, exist_ok=True)
            record_path = self.records_dir / f"task_{task_id}.json"
            record_data = dict(data)
            record_data["consumed_at"] = time.time()
            record_data["status"] = "CONSUMED"
            record_path.write_text(
                json.dumps(record_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return True
        except (OSError, json.JSONDecodeError):
            return False

    def archive_record(
        self,
        task_id: str,
        status: str = "COMPLETED",
        commit_info: dict[str, Any] | None = None,
        checker_info: dict[str, Any] | None = None,
        verification_info: dict[str, Any] | None = None,
        attempts: list[dict[str, Any]] | None = None,
        team_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """コミット完了後に監査レコードを最終更新する。"""
        import json

        self.records_dir.mkdir(parents=True, exist_ok=True)
        record_path = self.records_dir / f"task_{task_id}.json"
        if not record_path.exists():
            import time

            data = {
                "task_id": task_id,
                "status": status,
                "created_at": int(time.time()),
            }
        else:
            try:
                data = cast(dict[str, Any], json.loads(record_path.read_text(encoding="utf-8")))
            except (OSError, json.JSONDecodeError):
                data = {"task_id": task_id, "status": status}

        try:
            data["status"] = status
            if commit_info is not None:
                data["commit"] = commit_info
                if commit_info.get("tree_sha"):
                    data["tree_sha"] = commit_info["tree_sha"]
            if checker_info is not None:
                data["checker"] = checker_info
            if team_profile is not None:
                data["team_profile"] = team_profile
            if verification_info is not None:
                data["verification"] = verification_info
            elif (
                "verification" not in data
                and "details" in data
                and isinstance(data["details"], dict)
                and "verification" in data["details"]
            ):
                data["verification"] = data["details"]["verification"]

            if attempts is not None:
                data["attempts"] = attempts
            elif (
                "attempts" not in data
                and "details" in data
                and isinstance(data["details"], dict)
                and "attempts" in data["details"]
            ):
                data["attempts"] = data["details"]["attempts"]

            record_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def get_records(self) -> list[dict[str, Any]]:
        """永続化されたすべての監査レコードをタスクID順で取得する。"""
        import json

        if not self.records_dir.exists():
            return []
        records: list[dict[str, Any]] = []
        for p in sorted(self.records_dir.glob("task_*.json")):
            try:
                data = cast(dict[str, Any], json.loads(p.read_text(encoding="utf-8")))
                records.append(data)
            except (OSError, json.JSONDecodeError):
                continue

        # task_id の数値順にソート（数値変換可能な場合）
        def _sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
            tid = str(item.get("task_id", ""))
            return (0, int(tid), "") if tid.isdigit() else (1, 0, tid)

        records.sort(key=_sort_key)
        return records

    def get_record(self, task_id: str) -> dict[str, Any] | None:
        """指定タスクの監査レコードを取得する。"""
        import json

        record_path = self.records_dir / f"task_{task_id}.json"
        if not record_path.exists():
            return None
        try:
            return cast(dict[str, Any], json.loads(record_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None

    def get_evidence(self, task_id: str) -> dict[str, Any] | None:
        """指定タスクの有効な検証証跡（未消費）を取得する。"""
        import json

        path = self.evidence_dir / f"task_{task_id}.json"
        if not path.exists():
            return None
        try:
            data = cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None
        if str(data.get("task_id")) != str(task_id):
            return None
        return data

    def record_review(
        self,
        *,
        task_id: str,
        evidence: dict[str, Any],
        decision: str,
        checker_output_path: Path,
        diff_path: Path,
        spec_path: Path,
        maker_model: str,
        maker_family: str,
        maker_family_source: str,
        checker_model: str,
        checker_family: str,
        checker_family_source: str,
        reason: str = "",
        team_profile: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """GATE-5: 査読の3ファイルの写しを Evidence 保存先へ置き、照合用メタデータを記録する。

        decision が空文字のとき（判定が1つに決まらない出力）は GATE-12 の history/<連番>/ にのみ残し、
        最新の記録（reviews/task_<id>.json と直下の写し）は変更しない。
        """
        import hashlib
        import json
        import shutil
        import time

        self.evidence_dir.mkdir(parents=True, exist_ok=True)
        reviews_dir = self.evidence_dir / "reviews"
        copies_dir = reviews_dir / f"task_{task_id}"
        copies_dir.mkdir(parents=True, exist_ok=True)

        def _sha256(p: Path) -> str:
            return hashlib.sha256(p.read_bytes()).hexdigest()

        base_record: dict[str, Any] = {
            "task_id": str(task_id),
            "evidence_token": str(evidence.get("token", "")),
            "evidence_issued_at": evidence.get("issued_at"),
            "checkpoint": str(evidence.get("checkpoint", "")),
            "decision": decision,
            "checker_output_path": str(checker_output_path.resolve()),
            "checker_output_sha256": _sha256(checker_output_path),
            "diff_path": str(diff_path.resolve()),
            "diff_sha256": _sha256(diff_path),
            "spec_path": str(spec_path.resolve()),
            "spec_sha256": _sha256(spec_path),
            "maker_model": maker_model,
            "maker_family": maker_family,
            "maker_family_source": maker_family_source,
            "checker_model": checker_model,
            "checker_family": checker_family,
            "checker_family_source": checker_family_source,
            "reason": reason,
            "recorded_at": time.time(),
        }
        if team_profile is not None:
            base_record["team_profile"] = team_profile

        # GATE-12: 記録のたびに 3 ファイルの写しと記録を history/<連番>/ に残し、上書きしない
        history_base = copies_dir / "history"
        history_base.mkdir(parents=True, exist_ok=True)
        seq = 1
        while (history_base / str(seq)).exists():
            seq += 1
        hist_dir = history_base / str(seq)
        hist_dir.mkdir(parents=True, exist_ok=False)
        hist_out = hist_dir / "checker_output.txt"
        hist_diff = hist_dir / "diff.patch"
        hist_spec = hist_dir / "spec_excerpt.md"
        shutil.copyfile(checker_output_path, hist_out)
        shutil.copyfile(diff_path, hist_diff)
        shutil.copyfile(spec_path, hist_spec)
        hist_record = dict(base_record)
        hist_record["copies"] = {
            "checker_output": str(hist_out),
            "diff": str(hist_diff),
            "spec": str(hist_spec),
        }
        (hist_dir / "review.json").write_text(
            json.dumps(hist_record, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        if not decision.strip():
            return hist_record

        out_copy = copies_dir / "checker_output.txt"
        diff_copy = copies_dir / "diff.patch"
        spec_copy = copies_dir / "spec_excerpt.md"
        shutil.copyfile(hist_out, out_copy)
        shutil.copyfile(hist_diff, diff_copy)
        shutil.copyfile(hist_spec, spec_copy)

        record = dict(base_record)
        record["copies"] = {
            "checker_output": str(out_copy),
            "diff": str(diff_copy),
            "spec": str(spec_copy),
        }
        review_path = reviews_dir / f"task_{task_id}.json"
        review_path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        return record

    def has_decided_review_for_evidence(self, task_id: str, evidence: dict[str, Any]) -> bool:
        """指定 Evidence（検証トークン）に対して判定の決まった査読が既に記録されているかを返す。"""
        existing = self.get_review_record(task_id)
        if existing is None:
            return False
        dec = str(existing.get("decision", "")).strip().upper()
        if dec not in ("APPROVE", "REJECT"):
            return False
        current_token = str(evidence.get("token", "")).strip()
        existing_token = str(existing.get("evidence_token", "")).strip()
        if current_token and existing_token:
            return existing_token == current_token
        return (
            existing.get("evidence_issued_at") is not None
            and existing.get("evidence_issued_at") == evidence.get("issued_at")
        )

    def count_review_history_for_evidence(self, task_id: str, evidence: dict[str, Any]) -> int:
        """指定 Evidence に対する GATE-12 査読履歴（判定の決まらない出力を含む）の件数を返す。"""
        import json

        history_base = self.evidence_dir / "reviews" / f"task_{task_id}" / "history"
        if not history_base.is_dir():
            return 0
        target_token = str(evidence.get("token", "")).strip()
        target_issued_at = evidence.get("issued_at")
        count = 0
        for p in sorted(history_base.iterdir()):
            if not p.is_dir():
                continue
            review_file = p / "review.json"
            if not review_file.is_file():
                continue
            try:
                rec = json.loads(review_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(rec, dict):
                continue
            rec_token = str(rec.get("evidence_token", "")).strip()
            if target_token and rec_token:
                if rec_token == target_token:
                    count += 1
            elif target_issued_at is not None and rec.get("evidence_issued_at") == target_issued_at:
                count += 1
        return count

    def get_review_record(self, task_id: str) -> dict[str, Any] | None:
        """指定タスクの査読記録を取得する。"""
        import json

        review_path = self.evidence_dir / "reviews" / f"task_{task_id}.json"
        if not review_path.exists():
            return None
        try:
            return cast(dict[str, Any], json.loads(review_path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            return None

    def clear_evidence(self, task_id: str | None = None) -> None:
        """証跡ファイルを破棄する。"""
        if task_id:
            path = self.evidence_dir / f"task_{task_id}.json"
            if path.exists():
                path.unlink()
        elif self.evidence_dir.exists():
            for p in self.evidence_dir.glob("task_*.json"):
                p.unlink()
