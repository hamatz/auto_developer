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
        """タスクの変更をアトミックにコミットする。"""
        self._run_git(["add", "-A"])
        msg = f"feat: {task.title} [refs: {task.task_id}]"
        self._run_git(["commit", "-m", msg])
        return self.get_current_checkpoint()

    def get_current_branch(self) -> str:
        """現在のブランチ名を取得する。detached HEAD の場合は空文字。"""
        proc = self._run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        branch = proc.stdout.strip()
        return "" if branch == "HEAD" else branch

    def quarantine_and_restore(self, checkpoint: str, task_id: str) -> str:
        """作業ツリーの変更を破棄せず隔離ブランチに退避コミットし、本線をチェックポイントに復元する。"""
        import time

        proc_status = self._run_git(["status", "--porcelain"])
        has_changes = bool(proc_status.stdout.strip())

        quarantine_branch = ""
        current_branch = self.get_current_branch()

        if has_changes:
            clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
            timestamp = int(time.time())
            quarantine_branch = f"quarantine/{clean_id}-{timestamp}"

            # 新規ブランチを作成して退避コミット
            self._run_git(["checkout", "-b", quarantine_branch])
            self._run_git(["add", "-A"])
            self._run_git(["commit", "-m", f"quarantine: save failed work for task {task_id} before rollback"])

            # 元のブランチに戻る
            if current_branch:
                self._run_git(["checkout", current_branch])
            else:
                self._run_git(["checkout", checkpoint])

        # 作業ツリーをチェックポイントに完全復元
        self._run_git(["reset", "--hard", checkpoint])
        self._run_git(["clean", "-fd"])

        return quarantine_branch

    def rollback_and_clean(self, checkpoint: str) -> None:
        """作業ツリーをチェックポイントに巻き戻し、未追跡ファイルを強制削除して完全初期化する。"""
        self._run_git(["reset", "--hard", checkpoint])
        self._run_git(["clean", "-fd"])
