from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path


class BoundaryViolationError(Exception):
    """チームの割り当てスコープ（owns）を逸脱した変更を検知した例外。"""


@dataclass(frozen=True)
class BoundaryCheckResult:
    allowed: bool
    violations: list[str]
    modified_files: list[str]


def _glob_to_regex(pattern: str) -> re.Pattern[str]:
    """リポジトリルート相対 glob パターンを厳格な先頭〜末尾完全一致の正規表現に変換する。"""
    pat = pattern.replace("\\", "/").strip().lstrip("/")
    res: list[str] = ["^"]
    i = 0
    n = len(pat)

    while i < n:
        c = pat[i]
        if c == "*":
            if i + 1 < n and pat[i + 1] == "*":
                # '**' の処理
                if i + 2 < n and pat[i + 2] == "/":
                    # '**/': 0個以上のディレクトリ
                    res.append("(?:.*/)?")
                    i += 3
                    continue
                else:
                    # 末尾の '**' または単独 '**': 任意の文字列
                    res.append(".*")
                    i += 2
                    continue
            else:
                # '*' ディレクトリ区切り以外の0文字以上
                res.append("[^/]*")
                i += 1
                continue
        elif c == "?":
            res.append("[^/]")
            i += 1
            continue
        elif c in r"\.+^${}()|[]":
            res.append(f"\\{c}")
            i += 1
        else:
            res.append(c)
            i += 1

    res.append("$")
    return re.compile("".join(res))


def _match_path(path_str: str, pattern: str) -> bool:
    """パス文字列が glob パターンにマッチするか先頭から厳格判定する。"""
    normalized_path = path_str.replace("\\", "/").strip().lstrip("/")
    regex = _glob_to_regex(pattern)
    return bool(regex.match(normalized_path))


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
        """base_checkpoint からの変更（追跡中・未追跡・未コミット・リネーム元含む）ファイルをすべて取得する。
        Gitコマンド失敗時は Fail-Closed で直ちに例外を送出する。
        """
        modified: set[str] = set()

        # 1. 追跡中コミット間の差分（リネーム元・先も検出）
        diff_res = subprocess.run(
            ["git", "diff", "--name-status", "-M", self.base_checkpoint],
            cwd=self.repo_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if diff_res.returncode != 0:
            raise BoundaryViolationError(
                f"Failed to inspect git diff against checkpoint '{self.base_checkpoint}': "
                f"{diff_res.stderr.strip()}"
            )

        for line in diff_res.stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = line.split("\t")
            status = parts[0]
            if status.startswith("R") and len(parts) >= 3:
                # リネーム: R100\told\tnew -> old と new の両方を検査
                modified.add(parts[1].strip())
                modified.add(parts[2].strip())
            elif len(parts) >= 2:
                modified.add(parts[1].strip())

        # 2. 作業ツリー内の未ステージ・ステージ済み・未追跡ファイル
        status_res = subprocess.run(
            ["git", "status", "--porcelain", "-uall"],
            cwd=self.repo_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if status_res.returncode != 0:
            raise BoundaryViolationError(
                f"Failed to inspect git status: {status_res.stderr.strip()}"
            )

        for line in status_res.stdout.splitlines():
            if len(line) > 3:
                path_part = line[3:].strip()
                if " -> " in path_part:
                    old_path, new_path = path_part.split(" -> ", 1)
                    if old_path.strip():
                        modified.add(old_path.strip())
                    if new_path.strip():
                        modified.add(new_path.strip())
                elif path_part:
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
