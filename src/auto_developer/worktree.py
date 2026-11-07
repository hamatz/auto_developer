from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

TEAM_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")

SECRET_PATTERNS = (
    re.compile(r"^\.env(\..+)?$", re.IGNORECASE),
    re.compile(r"\.(key|pem|pkcs12|pfx)$", re.IGNORECASE),
    re.compile(r"(token|secret|credential|password)", re.IGNORECASE),
)

NOISE_PATTERNS = (
    re.compile(r"(^|/)\.DS_Store$"),
    re.compile(r"(^|/)__MACOSX(/|$)"),
    re.compile(r"(^|/)Thumbs\.db$", re.IGNORECASE),
)


class WorktreeError(Exception):
    """Git Worktree操作に関する例外。"""


@dataclass(frozen=True)
class WorktreeInfo:
    team_id: str
    path: Path
    branch: str
    head_commit: str


class WorktreeManager:
    """チーム単位の独立 Git Worktree を管理するクラス。"""

    def __init__(self, repo_dir: Path, worktree_base: Path | None = None) -> None:
        self.repo_dir = repo_dir.resolve()
        self.worktree_base = (worktree_base or (self.repo_dir / ".dev" / "worktrees")).resolve()

    def _validate_team_id(self, team_id: str) -> Path:
        """チームIDの妥当性を検証し、管理領域直下の安全なパスを返す。"""
        if not team_id or not isinstance(team_id, str):
            raise WorktreeError("Team ID must be a non-empty string.")

        if not TEAM_ID_PATTERN.match(team_id):
            raise WorktreeError(
                f"Invalid team ID format: '{team_id}'. "
                "Must start with lowercase letter, contain only [a-z0-9_-], and be 1-64 chars."
            )

        target_dir = (self.worktree_base / team_id).resolve()
        try:
            rel = target_dir.relative_to(self.worktree_base)
            if len(rel.parts) != 1 or rel.parts[0] != team_id:
                raise WorktreeError(f"Path traversal detected: '{team_id}' escapes worktree base.")
        except ValueError as err:
            raise WorktreeError(f"Path traversal detected: '{team_id}' escapes worktree base.") from err

        return target_dir

    def create_worktree(
        self,
        team_id: str,
        base_branch: str = "main",
        branch_name: str | None = None,
    ) -> Path:
        """チーム専用の Git Worktree を安全に作成する。"""
        target_dir = self._validate_team_id(team_id)
        if target_dir.exists():
            raise WorktreeError(f"Worktree directory for team '{team_id}' already exists: {target_dir}")

        branch = branch_name or f"feature/{team_id}"

        # 既存ブランチのチェック
        chk_branch = subprocess.run(
            ["git", "branch", "--list", branch],
            cwd=self.repo_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if chk_branch.stdout.strip():
            raise WorktreeError(f"Branch '{branch}' already exists. Cannot create fresh worktree.")

        self.worktree_base.mkdir(parents=True, exist_ok=True)

        cmd = ["git", "worktree", "add", "-b", branch, str(target_dir), base_branch]
        res = subprocess.run(cmd, cwd=self.repo_dir, check=False, capture_output=True, text=True)
        if res.returncode != 0:
            raise WorktreeError(f"Failed to create worktree for team '{team_id}': {res.stderr.strip()}")

        return target_dir

    def list_worktrees(self) -> list[WorktreeInfo]:
        """管理下（worktree_base 配下）の Git Worktree 一覧を取得する。"""
        res = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=self.repo_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            raise WorktreeError(f"Failed to list worktrees: {res.stderr.strip()}")

        worktrees: list[WorktreeInfo] = []
        current_wt: Path | None = None
        current_head = ""
        current_branch = ""

        lines = res.stdout.splitlines()
        for line in lines:
            if line.startswith("worktree "):
                current_wt = Path(line[len("worktree ") :].strip()).resolve()
                current_head = ""
                current_branch = ""
            elif line.startswith("HEAD "):
                current_head = line[len("HEAD ") :].strip()
            elif line.startswith("branch "):
                ref = line[len("branch ") :].strip()
                current_branch = ref.removeprefix("refs/heads/")
            elif line == "":
                if current_wt is not None:
                    try:
                        rel = current_wt.relative_to(self.worktree_base)
                        # 直下のディレクトリのみを対象とする
                        if len(rel.parts) == 1:
                            team_id = rel.parts[0]
                            if TEAM_ID_PATTERN.match(team_id):
                                worktrees.append(
                                    WorktreeInfo(
                                        team_id=team_id,
                                        path=current_wt,
                                        branch=current_branch,
                                        head_commit=current_head,
                                    )
                                )
                    except ValueError:
                        pass
                current_wt = None

        if current_wt is not None:
            try:
                rel = current_wt.relative_to(self.worktree_base)
                if len(rel.parts) == 1:
                    team_id = rel.parts[0]
                    if TEAM_ID_PATTERN.match(team_id):
                        worktrees.append(
                            WorktreeInfo(
                                team_id=team_id,
                                path=current_wt,
                                branch=current_branch,
                                head_commit=current_head,
                            )
                        )
            except ValueError:
                pass

        return worktrees

    def remove_worktree(
        self,
        team_id: str,
        force: bool = False,
        delete_branch: bool = False,
        purge_unmerged: bool = False,
    ) -> bool:
        """チーム専用の Git Worktree を安全に削除する。
        未コミット成果がある場合は自動 Quarantine 保存を行い、未マージブランチは purge_unmerged=True が
        明示されない限り force=True でも破棄せず保全する。
        戻り値: ブランチが実際に削除された場合は True、保全・スキップされた場合は False。
        """
        target_dir = self._validate_team_id(team_id)

        # 未コミット変更の残存検査と成果保全
        if target_dir.exists():
            status_res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=target_dir,
                check=False,
                capture_output=True,
                text=True,
            )
            if status_res.returncode != 0:
                raise WorktreeError(
                    f"git status failed in worktree '{team_id}': {status_res.stderr.strip()}. "
                    "Aborting removal to prevent unverified data loss."
                )
            has_uncommitted = bool(status_res.stdout.strip())
            if has_uncommitted:
                if not force:
                    raise WorktreeError(
                        f"Cannot remove worktree '{team_id}' with uncommitted changes without force. "
                        "Preserving uncommitted work."
                    )
                # force=True の場合でも直接破壊せず Quarantine スナップショットを作成して退避保全 (機密サニタイズ)
                import tarfile
                import time

                quarantines_dir = self.repo_dir / ".dev" / "quarantines"
                quarantines_dir.mkdir(parents=True, exist_ok=True)
                snapshot_name = f"snapshot_worktree_{team_id}_{int(time.time())}.tar.gz"
                snapshot_path = quarantines_dir / snapshot_name

                def _quarantine_filter(tarinfo: tarfile.TarInfo) -> tarfile.TarInfo | None:
                    normalized = tarinfo.name.replace("\\", "/")
                    filename = Path(normalized).name
                    for pat in SECRET_PATTERNS:
                        if pat.search(filename):
                            return None
                    for pat in NOISE_PATTERNS:
                        if pat.search(normalized):
                            return None
                    if "/.git" in normalized or normalized.endswith("/.git"):
                        return None
                    return tarinfo

                with tarfile.open(snapshot_path, "w:gz") as tar:
                    tar.add(target_dir, arcname=team_id, filter=_quarantine_filter)

        branch_to_delete = f"feature/{team_id}"

        # 一覧から実際のブランチ名を取得
        for wt in self.list_worktrees():
            if wt.team_id == team_id:
                branch_to_delete = wt.branch
                break

        cmd = ["git", "worktree", "remove"]
        if force:
            cmd.append("--force")
        cmd.append(str(target_dir))

        if target_dir.exists():
            res = subprocess.run(cmd, cwd=self.repo_dir, check=False, capture_output=True, text=True)
            if res.returncode != 0:
                # shutil.rmtree などによる直接削除は行わず、安全にエラーを送出
                raise WorktreeError(
                    f"Failed to remove worktree for team '{team_id}': {res.stderr.strip()}"
                )

        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_dir, check=False, capture_output=True)

        branch_deleted = False
        if delete_branch and branch_to_delete:
            # 未マージコミットの保護検査
            merged_res = subprocess.run(
                ["git", "branch", "--merged", "HEAD"],
                cwd=self.repo_dir,
                check=False,
                capture_output=True,
                text=True,
            )
            merged_branches = {b.strip().lstrip("* ") for b in merged_res.stdout.splitlines()}
            is_unmerged = branch_to_delete not in merged_branches

            if is_unmerged and not purge_unmerged:
                print(
                    f"Preserving unmerged branch '{branch_to_delete}' (not merged into HEAD). "
                    "Pass purge_unmerged=True if destruction is explicitly intended.",
                    file=sys.stderr,
                )
            else:
                del_flag = "-D" if (force or purge_unmerged) else "-d"
                del_res = subprocess.run(
                    ["git", "branch", del_flag, branch_to_delete],
                    cwd=self.repo_dir,
                    check=False,
                    capture_output=True,
                )
                branch_deleted = (del_res.returncode == 0)

        return branch_deleted

    def cleanup_all(
        self,
        force: bool = False,
        delete_branches: bool = False,
        purge_unmerged: bool = False,
    ) -> None:
        """管理下の全 Worktree を安全にクリーンアップする。デフォルトではブランチを保全する。"""
        worktrees = self.list_worktrees()
        for wt in worktrees:
            self.remove_worktree(
                team_id=wt.team_id,
                force=force,
                delete_branch=delete_branches,
                purge_unmerged=purge_unmerged,
            )

        subprocess.run(["git", "worktree", "prune"], cwd=self.repo_dir, check=False, capture_output=True)
        if self.worktree_base.exists():
            try:
                if not any(self.worktree_base.iterdir()):
                    self.worktree_base.rmdir()
            except OSError:
                pass
