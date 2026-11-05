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

    def check_protected_files_untouched(self, checkpoint: str) -> bool:
        """チェックポイント以降に変更されたファイル（未追跡・ステージ済を含む）に保護対象が含まれていないか検査する。"""
        files_to_check: set[str] = set()

        # 1. Diff against checkpoint (tracked modifications)
        proc_diff = self._run_git(["diff", "--name-only", checkpoint])
        for line in proc_diff.stdout.splitlines():
            if line.strip():
                files_to_check.add(line.strip())

        # 2. Porcelain status (untracked files ??, staged files, uncommitted changes)
        proc_status = self._run_git(["status", "--porcelain"])
        for line in proc_status.stdout.splitlines():
            if not line.strip():
                continue
            # Format: XY filename or XY orig -> renamed
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

    def is_secret_file(self, filename: str) -> bool:
        """シークレット・認証情報ファイル名パターンに合致するか判定する。"""
        base_name = Path(filename).name
        return any(pattern.search(base_name) for pattern in self.SECRET_EXCLUDE_PATTERNS)

    def create_quarantine_snapshot(self, task_id: str) -> Path:
        """作業ツリーの変更・未追跡ファイルを物理アーカイブへ安全に退避する。
        秘密情報 (.env や鍵) はスナップショットから厳格に除外される。
        """
        import tarfile
        import time

        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
        timestamp = int(time.time())
        quarantine_dir = self.repo_dir / ".dev" / "quarantines"
        quarantine_dir.mkdir(parents=True, exist_ok=True)
        archive_path = quarantine_dir / f"snapshot_task_{clean_id}_{timestamp}.tar.gz"

        with tarfile.open(archive_path, "w:gz") as tar:
            for item in self.repo_dir.iterdir():
                if item.name in (".git", ".dev"):
                    continue
                if self.is_secret_file(item.name):
                    continue
                tar.add(
                    item,
                    arcname=item.name,
                    filter=lambda tarinfo: None if self.is_secret_file(Path(tarinfo.name).name) else tarinfo,
                )

        if not archive_path.exists() or archive_path.stat().st_size == 0:
            raise RuntimeError(f"Quarantine aborted: Failed to create snapshot archive at {archive_path}")

        return archive_path

    def quarantine_and_restore(self, checkpoint: str, task_id: str) -> str:
        """作業ツリーの変更を破棄せず物理アーカイブおよび隔離ブランチへ退避し、退避確認後に本線をチェックポイントに復元する。
        秘密情報 (.env や鍵) は Git 隔離ブランチおよびスナップショットから厳格に除外される。
        """
        import time

        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
        timestamp = int(time.time())

        proc_status = self._run_git(["status", "--porcelain"])
        has_changes = bool(proc_status.stdout.strip())

        quarantine_branch = ""
        current_branch = self.get_current_branch()

        if has_changes:
            # 1. 物理スナップショットの安全作成 (共通機能)
            archive_path = self.create_quarantine_snapshot(task_id)

            # 2. 隔離ブランチへの安全なステージとコミット (追跡済みファイルのみ add -u でステージし、未追跡の秘密ファイル混入を物理遮断)
            quarantine_branch = f"quarantine/{clean_id}-{timestamp}"
            self._run_git(["checkout", "-b", quarantine_branch])
            self._run_git(["add", "-u"])
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
