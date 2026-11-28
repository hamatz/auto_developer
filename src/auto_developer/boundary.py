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


def match_path(path_str: str, pattern: str) -> bool:
    """パス文字列が glob パターンにマッチするか先頭から厳格判定する。"""
    normalized_path = path_str.replace("\\", "/").strip().lstrip("/")
    regex = _glob_to_regex(pattern)
    return bool(regex.match(normalized_path))


def _match_path(path_str: str, pattern: str) -> bool:
    """後方互換用の内部エイリアス。"""
    return match_path(path_str, pattern)


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
            ["git", "diff", "--name-status", "-z", "-M", self.base_checkpoint],
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

        diff_tokens = [tok for tok in diff_res.stdout.split("\0") if tok]
        idx = 0
        while idx < len(diff_tokens):
            status = diff_tokens[idx]
            if status.startswith(("R", "C")):
                for p in diff_tokens[idx + 1 : idx + 3]:
                    if p:
                        modified.add(p)
                idx += 3
            else:
                if idx + 1 < len(diff_tokens) and diff_tokens[idx + 1]:
                    modified.add(diff_tokens[idx + 1])
                idx += 2

        # 2. 作業ツリー内の未ステージ・ステージ済み・未追跡ファイル
        status_res = subprocess.run(
            ["git", "status", "--porcelain", "-z", "-uall"],
            cwd=self.repo_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if status_res.returncode != 0:
            raise BoundaryViolationError(
                f"Failed to inspect git status: {status_res.stderr.strip()}"
            )

        st_tokens = [tok for tok in status_res.stdout.split("\0") if tok]
        st_idx = 0
        while st_idx < len(st_tokens):
            tok = st_tokens[st_idx]
            if len(tok) < 4:
                st_idx += 1
                continue
            xy = tok[:2]
            path1 = tok[3:]
            if path1:
                modified.add(path1)
            if xy[0] in ("R", "C") or xy[1] in ("R", "C"):
                if st_idx + 1 < len(st_tokens) and st_tokens[st_idx + 1]:
                    modified.add(st_tokens[st_idx + 1])
                st_idx += 2
            else:
                st_idx += 1

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
