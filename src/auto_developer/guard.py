from __future__ import annotations

import re
import subprocess
from pathlib import Path

from auto_developer.models import Task


class GitGuard:
    """Git作業ツリーの整合性管理、不変条件外部検査、および隔離退避を担当。"""

    PROTECTED_PATTERNS = (
        re.compile(r"^\.github/"),
        re.compile(r"^SPEC\.md$"),
        re.compile(r"^scripts/verify_local\.sh$"),
        re.compile(r"^tests/immunity/"),
    )
    BASE_PROTECTED_PATTERNS = (
        re.compile(r"^\.github/"),
        re.compile(r"^SPEC\.md$"),
        re.compile(r"^scripts/verify_local\.sh$"),
    )
    IMMUNITY_PATTERN = re.compile(r"^tests/immunity/")
    VALID_IMMUNITY_FILE_PATTERN = re.compile(r"^tests/immunity/test_[a-zA-Z0-9_\-]+\.py$")

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()

    def _run_git(self, args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + args,
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        )

    def assert_clean_working_tree(self) -> None:
        """作業ツリーがクリーンである（未コミットの変更がない）ことを検証する。"""
        proc = self._run_git(["status", "--porcelain"])
        if proc.stdout.strip():
            raise RuntimeError(
                f"Dirty working tree: uncommitted changes detected in {self.repo_dir}:\n{proc.stdout}"
            )

    def get_current_checkpoint(self) -> str:
        """現在の HEAD コミットハッシュを取得する。"""
        proc = self._run_git(["rev-parse", "HEAD"])
        return proc.stdout.strip()

    def get_working_tree_sha(self) -> str:
        """現在の作業ツリーのステージ済み Git Tree SHA を取得する (git write-tree)。"""
        self._run_git(["add", "-A"])
        proc = self._run_git(["write-tree"])
        return proc.stdout.strip()

    def get_commit_tree_sha(self, commit_ref: str = "HEAD") -> str:
        """指定したコミットの Git Tree SHA を取得する (git rev-parse <ref>^{tree})。"""
        proc = self._run_git(["rev-parse", f"{commit_ref}^{{tree}}"])
        return proc.stdout.strip()

    def get_spec_sha(self, spec_path: Path | None = None) -> str:
        """SPEC.md の SHA-256 ダイジェストを取得する。"""
        import hashlib
        p = spec_path or (self.repo_dir / "SPEC.md")
        if not p.exists():
            return ""
        return hashlib.sha256(p.read_bytes()).hexdigest()

    def has_changes_since(self, checkpoint: str) -> bool:
        """チェックポイント以降に変更（差分または未追跡・ステージ済みファイル）が存在するか判定する。"""
        proc_diff = self._run_git(["diff", checkpoint])
        if proc_diff.stdout.strip():
            return True
        proc_status = self._run_git(["status", "--porcelain"])
        return bool(proc_status.stdout.strip())

    def _is_valid_new_immunity_file(self, rel_path: str) -> bool:
        """新規免疫テストファイルが通常ファイルかつ命名規則を満たし、シンボリックリンクでないかを検査する。"""
        if not self.VALID_IMMUNITY_FILE_PATTERN.match(rel_path):
            return False
        target = self.repo_dir / rel_path
        if not target.exists():
            return False
        if target.is_symlink():
            return False
        return target.is_file()

    def check_protected_files_untouched(
        self, checkpoint: str, allow_immunity_additions: bool = False
    ) -> bool:
        """チェックポイント以降に変更されたファイル（未追跡・ステージ済を含む）に保護対象が含まれていないか検査する。
        allow_immunity_additions=True の場合、tests/immunity/ 配下への新規テスト追加のみ許可し、
        既存の免疫テストの変更・削除・改変は依然として厳格に遮断する。
        さらに、新規追加されるファイルは通常ファイル（非symlink）かつ test_*.py の命名規則を満たす必要がある。
        """
        if not allow_immunity_additions:
            files_to_check: set[str] = set()
            proc_diff = self._run_git(["diff", "--name-only", checkpoint])
            for line in proc_diff.stdout.splitlines():
                if line.strip():
                    files_to_check.add(line.strip())

            proc_status = self._run_git(["status", "--porcelain"])
            for line in proc_status.stdout.splitlines():
                if not line.strip():
                    continue
                raw_path = line[3:].strip()
                if " -> " in raw_path:
                    for p in raw_path.split(" -> "):
                        files_to_check.add(p.strip().strip('"'))
                else:
                    files_to_check.add(raw_path.strip('"'))

            for file_path in files_to_check:
                for pattern in self.PROTECTED_PATTERNS:
                    if pattern.search(file_path):
                        return False
            return True

        # allow_immunity_additions=True の場合:
        # A. 基本保護対象 (.github/, SPEC.md, scripts/verify_local.sh) の検査
        files_to_check_base: set[str] = set()
        proc_diff = self._run_git(["diff", "--name-only", checkpoint])
        for line in proc_diff.stdout.splitlines():
            if line.strip():
                files_to_check_base.add(line.strip())

        proc_status = self._run_git(["status", "--porcelain"])
        for line in proc_status.stdout.splitlines():
            if not line.strip():
                continue
            raw_path = line[3:].strip()
            if " -> " in raw_path:
                for p in raw_path.split(" -> "):
                    files_to_check_base.add(p.strip().strip('"'))
            else:
                files_to_check_base.add(raw_path.strip('"'))

        for file_path in files_to_check_base:
            for pattern in self.BASE_PROTECTED_PATTERNS:
                if pattern.search(file_path):
                    return False

        # B. tests/immunity/ の非対称検査
        try:
            proc_ls = self._run_git(["ls-tree", "-r", "--name-only", checkpoint, "--", "tests/immunity"])
            existing_immunity_files = {line.strip() for line in proc_ls.stdout.splitlines() if line.strip()}
        except subprocess.CalledProcessError:
            existing_immunity_files = set()

        # B-1: diff による検査 (--name-status)
        proc_diff_status = self._run_git(["diff", "--name-status", checkpoint])
        for line in proc_diff_status.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.strip().split("\t")
            status_code = parts[0]
            paths = parts[1:]
            for p in paths:
                p_clean = p.strip().strip('"')
                if self.IMMUNITY_PATTERN.search(p_clean):
                    if p_clean in existing_immunity_files:
                        return False
                    if not status_code.startswith("A"):
                        return False
                    if not self._is_valid_new_immunity_file(p_clean):
                        return False

        # B-2: status による検査 (未追跡・ステージ状態)
        for line in proc_status.stdout.splitlines():
            if not line.strip():
                continue
            xy = line[:2]
            raw_path = line[3:].strip()
            paths = (
                [p.strip().strip('"') for p in raw_path.split(" -> ")]
                if " -> " in raw_path
                else [raw_path.strip('"')]
            )
            for p in paths:
                if self.IMMUNITY_PATTERN.search(p):
                    if p in existing_immunity_files:
                        return False
                    if xy == "??":
                        if not self._is_valid_new_immunity_file(p):
                            return False
                        continue
                    if "M" in xy or "D" in xy or "R" in xy:
                        return False
                    if not self._is_valid_new_immunity_file(p):
                        return False

        return True

    def commit_task(self, task: Task) -> str:
        """タスクの変更をアトミックにコミットする。すでにステージ済みのIndexから直接コミットを作成する。"""
        msg = f"feat: {task.title} [refs: {task.task_id}]"
        self._run_git(["commit", "-m", msg])
        return self.get_current_checkpoint()

    def get_current_branch(self) -> str:
        """現在のブランチ名を取得する。detached HEAD の場合は空文字。"""
        proc = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        branch = proc.stdout.strip()
        return "" if branch == "HEAD" else branch

    SECRET_EXCLUDE_PATTERNS = (
        re.compile(r"^\.env(\..+)?$"),
        re.compile(r"\.(key|pem|pkcs12|pfx)$", re.IGNORECASE),
        re.compile(r"(id_rsa|id_ed25519|id_ecdsa)", re.IGNORECASE),
        re.compile(r"(secret|token|credential)", re.IGNORECASE),
    )

    SECRET_CONTENT_PATTERNS = (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),  # gitleaks:allow
        re.compile(r"\bsk-[a-zA-Z0-9]{20,}\b"),  # gitleaks:allow
        re.compile(r"\bAIza[0-9A-Za-z-_]{35}\b"),  # gitleaks:allow
        re.compile(r"\bya29\.[0-9A-Za-z-_]+\b"),  # gitleaks:allow
        re.compile(r"\bgh[pousr]_[0-9A-Za-z]{36,}\b"),  # gitleaks:allow
        re.compile(r"\bxox[baprs]-[0-9A-Za-z-]+\b"),  # gitleaks:allow
        re.compile(r"\bAKIA[0-9A-Z]{16}\b"),  # gitleaks:allow
        re.compile(r"(api[_-]?key|secret[_-]?key|private[_-]?key|auth[_-]?token)\s*[:=]\s*['\"][a-zA-Z0-9_\-]{16,}['\"]", re.IGNORECASE),  # gitleaks:allow
    )

    @classmethod
    def is_secret_file(cls, filename: str) -> bool:
        """シークレット・認証情報ファイル名パターンに合致するか判定する。"""
        base_name = Path(filename).name
        return any(pattern.search(base_name) for pattern in cls.SECRET_EXCLUDE_PATTERNS)

    @classmethod
    def has_secret_content(cls, content: bytes | str) -> bool:
        """テキストまたはバイナリ内に認証情報・秘密鍵パターンが含まれているか検査する。"""
        if isinstance(content, bytes):
            try:
                text = content.decode("utf-8", errors="ignore")
            except UnicodeDecodeError:
                return False
        else:
            text = content
        return any(pattern.search(text) for pattern in cls.SECRET_CONTENT_PATTERNS)

    def sanitize_staged_changes(self) -> list[str]:
        """ステージされたファイルの内容を検査し、シークレットを含むファイルをステージから除外する。"""
        proc = self._run_git(["diff", "--cached", "--name-only"])
        staged_files = [f.strip() for f in proc.stdout.splitlines() if f.strip()]
        excluded = []
        for file_rel in staged_files:
            file_path = self.repo_dir / file_rel
            if file_path.exists() and file_path.is_file():
                try:
                    content = file_path.read_bytes()
                    if self.has_secret_content(content):
                        self._run_git(["reset", "HEAD", "--", file_rel])
                        excluded.append(file_rel)
                except OSError:
                    pass
        return excluded

    def create_quarantine_snapshot(
        self,
        task_id: str,
        checkpoint: str = "",
        job_id: str = "",
    ) -> Path:
        """作業ツリーの変更・未追跡ファイルを物理アーカイブへ安全に退避し、照合用 Manifest を生成する。
        秘密情報 (.env や鍵) はスナップショットから厳格に除外される。
        """
        import hashlib
        import json
        import tarfile
        import time

        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
        timestamp = int(time.time())
        quarantine_dir = self.repo_dir / ".dev" / "quarantines"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        archive_path = quarantine_dir / f"snapshot_task_{clean_id}_{timestamp}.tar.gz"
        manifest_path = quarantine_dir / f"manifest_task_{clean_id}_{timestamp}.json"

        # 同一秒内の作成でも既存アーカイブを上書きしないようユニーク性を保証
        counter = 1
        while archive_path.exists() or manifest_path.exists():
            archive_path = quarantine_dir / f"snapshot_task_{clean_id}_{timestamp}_{counter}.tar.gz"
            manifest_path = quarantine_dir / f"manifest_task_{clean_id}_{timestamp}_{counter}.json"
            counter += 1

        manifest_files = []
        with tarfile.open(archive_path, "w:gz") as tar:
            for item in sorted(self.repo_dir.rglob("*")):
                if not item.is_file():
                    continue
                rel_path = item.relative_to(self.repo_dir)
                rel_posix = rel_path.as_posix()
                if rel_posix.startswith((".git", ".dev")):
                    continue
                if self.is_secret_file(item.name) or any(self.is_secret_file(p) for p in rel_path.parts):
                    continue
                try:
                    content = item.read_bytes()
                    if self.has_secret_content(content):
                        continue
                    file_sha = hashlib.sha256(content).hexdigest()
                    manifest_files.append({"path": rel_posix, "sha256": file_sha})
                    tar.add(item, arcname=rel_posix)
                except OSError:
                    pass

        if not archive_path.exists() or archive_path.stat().st_size == 0:
            raise RuntimeError(f"Quarantine aborted: Failed to create snapshot archive at {archive_path}")

        manifest_data = {
            "job_id": job_id,
            "task_id": str(task_id),
            "checkpoint": checkpoint,
            "created_at": timestamp,
            "archive_name": archive_path.name,
            "files": manifest_files,
        }
        manifest_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")

        return archive_path

    def quarantine_and_restore(
        self,
        checkpoint: str,
        task_id: str,
        job_id: str = "",
        team_id: str = "",
    ) -> str:
        """作業ツリーの変更を破棄せず物理アーカイブおよび隔離ブランチへ退避し、退避確認後に本線をチェックポイントに復元する。
        秘密情報 (.env や鍵) は Git 隔離ブランチおよびスナップショットから厳格に除外される。
        """
        import time

        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
        clean_team = re.sub(r"[^a-zA-Z0-9_\-]", "_", team_id) if team_id else ""
        team_prefix = f"{clean_team}-" if clean_team else ""
        timestamp = int(time.time())

        proc_status = self._run_git(["status", "--porcelain"])
        has_changes = bool(proc_status.stdout.strip())

        quarantine_branch = ""
        current_branch = self.get_current_branch()

        if has_changes:
            # 1. 物理スナップショットの安全作成 (Manifest 連携)
            self.create_quarantine_snapshot(task_id=f"{team_prefix}{task_id}", checkpoint=checkpoint, job_id=job_id)

            # 2. 隔離ブランチへの安全なステージとコミット (追跡済みファイルのみ add -u でステージし、シークレット検査を実施)
            quarantine_branch = f"quarantine/{team_prefix}{clean_id}-{timestamp}"
            self._run_git(["checkout", "-b", quarantine_branch])
            self._run_git(["add", "-u"])
            self.sanitize_staged_changes()
            self._run_git(["commit", "--allow-empty", "-m", f"quarantine: save failed work for task {task_id} before rollback"])

            # 3. 元のブランチに戻る
            if current_branch:
                self._run_git(["checkout", current_branch])
            else:
                self._run_git(["checkout", checkpoint])

        # 4. 退避の成功を確認後、本線作業ツリーをチェックポイントに復元
        self._run_git(["reset", "--hard", checkpoint])
        self._run_git(["clean", "-fd"])

        return quarantine_branch

    def rollback_and_clean(self, checkpoint: str) -> None:
        """作業ツリーをチェックポイントに巻き戻し、未追跡ファイルを強制削除して完全初期化する。"""
        self._run_git(["reset", "--hard", checkpoint])
        self._run_git(["clean", "-fd"])
