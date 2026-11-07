from __future__ import annotations

import fnmatch
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePath


class BoundaryViolationError(Exception):
    """チームの割り当てスコープ（owns）を逸脱した変更を検知した例外。"""


@dataclass(frozen=True)
class BoundaryCheckResult:
    allowed: bool
    violations: list[str]
    modified_files: list[str]


def _match_path(path_str: str, pattern: str) -> bool:
    """パス文字列が glob パターンにマッチするか判定する。"""
    normalized_path = path_str.replace("\\", "/")
    normalized_pat = pattern.replace("\\", "/")

    # ディレクトリ配下全般の再帰パターン (e.g. "src/ui/**")
    if normalized_pat.endswith("/**"):
        prefix = normalized_pat[:-3]
        if normalized_path == prefix or normalized_path.startswith(prefix + "/"):
            return True

    # PurePath.match によるマッチング
    if PurePath(normalized_path).match(normalized_pat):
        return True

    # fnmatch によるフォールバック
    return bool(fnmatch.fnmatch(normalized_path, normalized_pat))


class BoundaryGuard:
    """チームの割り当て領域（owns）に対する領空侵犯を検査・強制するガード。"""

    def __init__(
        self,
        repo_dir: Path,
        owns_patterns: list[str],
        base_checkpoint: str = "HEAD",
    ) -> None:
        self.repo_dir = repo_dir.resolve()
        self.owns_patterns = owns_patterns
        self.base_checkpoint = base_checkpoint

    def get_modified_files(self) -> list[str]:
        """base_checkpoint からの変更（追跡中・未追跡・未コミット）ファイルをすべて取得する。"""
        modified: set[str] = set()

        # 1. 追跡中コミット間の差分
        diff_res = subprocess.run(
            ["git", "diff", "--name-only", self.base_checkpoint],
            cwd=self.repo_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if diff_res.returncode == 0:
            for line in diff_res.stdout.splitlines():
                line = line.strip()
                if line:
                    modified.add(line)

        # 2. 作業ツリー内の未ステージ・ステージ済み・未追跡ファイル（個々のファイル単位で取得）
        status_res = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=self.repo_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if status_res.returncode == 0:
            for line in status_res.stdout.splitlines():
                if len(line) > 3:
                    path_part = line[3:].strip()
                    # リネーム検出 (e.g. "R  old -> new")
                    if " -> " in path_part:
                        path_part = path_part.split(" -> ")[1].strip()
                    if path_part:
                        modified.add(path_part)

        return sorted(modified)

    def inspect_changes(self) -> BoundaryCheckResult:
        """変更ファイルが owns_patterns に収まっているか検査する。"""
        modified_files = self.get_modified_files()
        violations: list[str] = []

        for file_path in modified_files:
            # owns_patterns のいずれかにマッチすれば許可
            is_matched = any(_match_path(file_path, pat) for pat in self.owns_patterns)
            if not is_matched:
                violations.append(file_path)

        allowed = len(violations) == 0
        return BoundaryCheckResult(
            allowed=allowed,
            violations=violations,
            modified_files=modified_files,
        )

    def enforce(self) -> None:
        """領空侵犯を検査し、違反があれば即座に例外を送出する。"""
        res = self.inspect_changes()
        if not res.allowed:
            raise BoundaryViolationError(
                f"Boundary violation: unauthorized modifications outside team owns: {res.violations}"
            )
