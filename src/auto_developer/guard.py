from __future__ import annotations

import ast
import json
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

from auto_developer.models import Task

_SNAPSHOT_EXCLUDE_CACHE_DIRS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"})
_SNAPSHOT_EXCLUDE_CACHE_SUFFIXES = frozenset({".pyc", ".pyo", ".pyd"})


def format_snapshot_only_files_lines(
    snapshot_path: Path | None,
    snapshot_only_files: list[str],
    repo_dir: Path | None = None,
    error_reason: str | None = None,
) -> list[str]:
    """スナップショットにだけある新しいファイルの一覧と tar xzf 復元コマンドを整形する (RCV-6)。"""
    if error_reason:
        reason_clean = " ".join(str(error_reason).splitlines()).strip() or "unknown error"
        return [f"Snapshot-only new files: unavailable ({reason_clean})"]
    if snapshot_only_files and snapshot_path is not None:
        if repo_dir is not None:
            try:
                snap_display = snapshot_path.relative_to(repo_dir).as_posix()
            except ValueError:
                snap_display = str(snapshot_path)
        else:
            snap_display = str(snapshot_path)
        lines = ["Snapshot-only new files (not in quarantine branch):"]
        for rel_path in snapshot_only_files:
            lines.append(f"  - {rel_path}")
        lines.append(f"Restore snapshot-only files: {shlex.join(['tar', 'xzf', snap_display, *snapshot_only_files])}")
        return lines
    return ["Snapshot-only new files: none"]


def is_safe_main_guard(stmt: ast.If) -> bool:
    """if __name__ == '__main__': unittest.main() のイディオムのみを安全と判定する。
    呼び出し対象が unittest.main() 以外の任意関数呼び出しである場合は遮断する。
    """
    test_node = stmt.test
    is_name_check = False
    if (
        isinstance(test_node, ast.Compare)
        and len(test_node.ops) == 1
        and isinstance(test_node.ops[0], ast.Eq)
    ):
        left = test_node.left
        comparator = test_node.comparators[0]
        if (
            isinstance(left, ast.Name)
            and left.id == "__name__"
            and isinstance(comparator, ast.Constant)
            and comparator.value == "__main__"
        ) or (
            isinstance(comparator, ast.Name)
            and comparator.id == "__name__"
            and isinstance(left, ast.Constant)
            and left.value == "__main__"
        ):
            is_name_check = True

    if not is_name_check:
        return False

    # else: 節が存在する場合、モジュールインポート時の迂回実行となるため即時拒絶 (Fail-Closed)
    if getattr(stmt, "orelse", None):
        return False

    # body が空の場合は拒絶
    if not stmt.body:
        return False

    for body_stmt in stmt.body:
        if isinstance(body_stmt, ast.Expr) and isinstance(body_stmt.value, ast.Call):
            call_func = body_stmt.value.func
            if (
                isinstance(call_func, ast.Attribute)
                and call_func.attr == "main"
                and isinstance(call_func.value, ast.Name)
                and call_func.value.id == "unittest"
            ):
                continue
        return False
    return True


def validate_test_ast(target: str | Path | ast.AST) -> bool:
    """候補テストおよび免疫テストのトップレベル構造を検査し、モンキーパッチや副作用を遮断する。"""
    if isinstance(target, ast.AST):
        tree = target
    elif isinstance(target, Path):
        try:
            content = target.read_text(encoding="utf-8")
            tree = ast.parse(content)
        except (OSError, SyntaxError, UnicodeDecodeError):
            return False
    elif isinstance(target, str):
        try:
            tree = ast.parse(target)
        except (SyntaxError, UnicodeDecodeError):
            return False
    else:
        return False

    for stmt in getattr(tree, "body", []):
        if isinstance(
            stmt,
            (
                ast.Import,
                ast.ImportFrom,
                ast.ClassDef,
            ),
        ):
            continue
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # setUpModule / tearDownModule / load_tests 等のモジュールレベル副作用関数は禁止
            if stmt.name in ("setUpModule", "tearDownModule", "setUp", "tearDown", "load_tests"):
                return False
            continue
        # docstring または コメント相当の文字列定数 Expr のみ許可
        if (
            isinstance(stmt, ast.Expr)
            and isinstance(stmt.value, ast.Constant)
            and isinstance(stmt.value.value, str)
        ):
            continue
        # if __name__ == '__main__': ガード (unittest.main() のみ)
        if isinstance(stmt, ast.If) and is_safe_main_guard(stmt):
            continue
        # それ以外 (ast.Assign, ast.Call 等のトップレベル実行文) はモンキーパッチ・副作用として禁止
        return False
    return True


class GitGuard:
    """Git作業ツリーの整合性管理、不変条件外部検査、および隔離退避を担当。"""

    PROTECTED_PATTERNS = (
        re.compile(r"^\.github/"),
        re.compile(r"^SPEC\.md$"),
        re.compile(r"^scripts/verify_local\.sh$"),
        re.compile(r"^tests/immunity/"),
        re.compile(r"^teams\.json$"),
    )
    BASE_PROTECTED_PATTERNS = (
        re.compile(r"^\.github/"),
        re.compile(r"^SPEC\.md$"),
        re.compile(r"^scripts/verify_local\.sh$"),
        re.compile(r"^teams\.json$"),
    )
    IMMUNITY_PATTERN = re.compile(r"^tests/immunity/")
    VALID_IMMUNITY_FILE_PATTERN = re.compile(r"^tests/immunity/test_[a-zA-Z0-9_\-]+\.py$")

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()
        self._last_working_tree_sha: str | None = None
        self.last_quarantine_snapshot: Path | None = None
        self.last_snapshot_only_files: list[str] = []
        self.last_snapshot_only_error: str | None = None

    def _run_git(self, args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git"] + args,
            cwd=self.repo_dir,
            env=env,
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
        """現在の作業ツリーの Git Tree SHA を一時インデックス (GIT_INDEX_FILE) で取得する (GATE-9)。"""
        import os
        import tempfile

        with tempfile.TemporaryDirectory() as tmp_dir:
            temp_index = Path(tmp_dir) / "index"
            env = {**os.environ, "GIT_INDEX_FILE": str(temp_index)}
            self._run_git(["add", "-A"], env=env)
            proc = self._run_git(["write-tree"], env=env)
            tree_sha = proc.stdout.strip()
        self._last_working_tree_sha = tree_sha
        return tree_sha

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

    def _run_test_subset(
        self, candidate_file: str, test_files: list[str], timeout_sec: int = 30
    ) -> dict[str, Any] | None:
        """テスト群を独立プロセスで実行し、構造化された実行結果を取得する。"""
        import sys
        runner_script = """
import json, os, sys, unittest

candidate_file = sys.argv[1]
test_files = sys.argv[2:]

if candidate_file != "NONE" and os.path.exists(candidate_file):
    import importlib.util
    spec = importlib.util.spec_from_file_location("_candidate_module", candidate_file)
    if spec and spec.loader:
        mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(mod)
        except Exception:
            pass

loader = unittest.TestLoader()
suite = unittest.TestSuite()
for f in test_files:
    dir_name = os.path.dirname(f)
    base_name = os.path.basename(f)
    try:
        loaded = loader.discover(start_dir=dir_name, pattern=base_name)
        suite.addTests(loaded)
    except Exception:
        pass

result = unittest.TestResult()
suite.run(result)

data = {
    "tests_run": result.testsRun,
    "failures": len(result.failures),
    "errors": len(result.errors),
    "failed_tests": sorted([str(t[0]) for t in result.failures]),
    "error_tests": sorted([str(t[0]) for t in result.errors]),
}
print("===MUTUAL_RESULT===")
print(json.dumps(data))
"""
        cmd = [
            sys.executable,
            "-c",
            runner_script,
            candidate_file,
            *test_files,
        ]
        try:
            proc = subprocess.run(
                cmd,
                cwd=self.repo_dir,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
                check=False,
            )
            marker = "===MUTUAL_RESULT==="
            if marker in proc.stdout:
                json_str = proc.stdout.split(marker, 1)[1].strip()
                return json.loads(json_str)  # type: ignore[no-any-return]
            return None
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
            return None

    def verify_mutual_non_interference(self, candidate_rel_path: str, timeout_sec: int = 30) -> bool:
        """新規免疫テスト候補が、既存テストスイートの実行結果に副作用・干渉（成否改変）を及ぼさないことを、
        事前・事後の実行結果比較によって機械的に証明する。
        """
        candidate_path = (self.repo_dir / candidate_rel_path).resolve()
        if not candidate_path.exists():
            return False

        # 1. AST 検査
        if not validate_test_ast(candidate_path):
            return False

        # 2. 既存テストスイートの特定 (candidate_path を除くすべての test_*.py)
        tests_dir = self.repo_dir / "tests"
        if not tests_dir.exists():
            return True

        existing_files: list[str] = []
        for f in tests_dir.rglob("test_*.py"):
            if f.is_file() and f.resolve() != candidate_path:
                existing_files.append(str(f.resolve()))

        if not existing_files:
            return True

        # 3. 事前ベースライン (NONE: 候補モジュールをインポートしない状態)
        res_before = self._run_test_subset("NONE", existing_files, timeout_sec=timeout_sec)
        if res_before is None:
            return False

        # 4. 事後比較 (候補モジュールを事前インポートした状態)
        res_after = self._run_test_subset(str(candidate_path), existing_files, timeout_sec=timeout_sec)
        if res_after is None:
            return False

        # 5. 既存テストの結果 (tests_run, failures, errors, failed_tests, error_tests) が完全一致すること
        return res_before == res_after

    def _is_valid_new_immunity_file(self, rel_path: str) -> bool:
        """新規免疫テストファイルが通常ファイルかつ命名規則を満たし、シンボリックリンクでなく、
        トップレベルにモンキーパッチ等の動的実行文を含まないかを検査する。
        """
        if not self.VALID_IMMUNITY_FILE_PATTERN.match(rel_path):
            return False
        target = self.repo_dir / rel_path
        if not target.exists():
            return False
        if target.is_symlink():
            return False
        if not target.is_file():
            return False
        return validate_test_ast(target)

    def get_modified_protected_files(
        self, checkpoint: str, allow_immunity_additions: bool = False
    ) -> list[str]:
        """チェックポイント以降に変更・追加された保護ファイルのパス一覧を返す。"""
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

        patterns = self.BASE_PROTECTED_PATTERNS if allow_immunity_additions else self.PROTECTED_PATTERNS
        matched = [
            fp for fp in sorted(files_to_check) if any(pat.search(fp) for pat in patterns)
        ]
        if allow_immunity_additions:
            for fp in sorted(files_to_check):
                if fp.startswith("tests/immunity/") and fp not in matched:
                    p_obj = self.repo_dir / fp
                    if not (
                        p_obj.exists()
                        and not p_obj.is_symlink()
                        and p_obj.is_file()
                        and p_obj.name.startswith("test_")
                        and p_obj.name.endswith(".py")
                    ):
                        matched.append(fp)
        return matched

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

    def commit_task(self, task: Task, tree_sha: str | None = None) -> str:
        """タスクの変更をアトミックにコミットする。指定または直前に求めた Tree SHA を read-tree してコミットする (GATE-9)。"""
        target_tree = tree_sha or self._last_working_tree_sha
        if target_tree:
            self._run_git(["read-tree", target_tree])
        msg = f"feat: {task.title} [refs: {task.task_id}]"
        self._run_git(["commit", "-m", msg])
        self._last_working_tree_sha = None
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

    SECRET_ENV_ALLOWLIST = frozenset({".env.example", ".env.sample", ".env.template"})
    SECRET_KEY_EXTENSIONS = (".key", ".pem", ".pkcs12", ".pfx")
    SECRET_SSH_FILENAMES = frozenset({"id_rsa", "id_ed25519", "id_ecdsa"})

    @classmethod
    def is_forbidden_commit_secret_path(cls, rel_path: str) -> bool:
        """GATE-10: コミット対象として拒否すべき機密ファイル名か判定する。"""
        base = Path(rel_path).name
        if base == ".env" or base.startswith(".env."):
            return base not in cls.SECRET_ENV_ALLOWLIST
        lower = base.lower()
        if lower.endswith(cls.SECRET_KEY_EXTENSIONS):
            return True
        return lower in cls.SECRET_SSH_FILENAMES

    def find_secret_files_in_tree(self, checkpoint: str, tree_sha: str) -> list[str]:
        """GATE-10/11: チェックポイントから tree_sha にかけて追加・変更・種類変更された機密ファイル一覧を返す。"""
        proc = self._run_git([
            "-c", "diff.renames=false",
            "-c", "core.quotePath=false",
            "diff-tree", "-r", "-z", "--name-only", "--diff-filter=AMT",
            f"{checkpoint}^{{tree}}", tree_sha,
        ])
        return [
            entry
            for entry in proc.stdout.split("\0")
            if entry and self.is_forbidden_commit_secret_path(entry)
        ]

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

    def list_snapshot_only_files(
        self,
        snapshot_path: Path | None = None,
        checkpoint: str = "",
        quarantine_branch: str = "",
    ) -> list[str]:
        """隔離直前の作業ツリーから、未追跡かつ非無視の新規ファイル（機密・キャッシュを除く）を返す (RCV-6)。"""
        _ = snapshot_path, checkpoint, quarantine_branch
        proc = self._run_git(
            ["-c", "core.quotePath=false", "ls-files", "--others", "--exclude-standard", "-z"]
        )
        result: list[str] = []
        for entry in proc.stdout.split("\0"):
            norm = entry
            while norm.startswith("./"):
                norm = norm[2:]
            if not norm:
                continue
            if norm.startswith((".dev/", ".git/")) or norm in (".dev", ".git"):
                continue
            p = Path(norm)
            if self.is_secret_file(p.name) or any(self.is_secret_file(part) for part in p.parts):
                continue
            if any(part in _SNAPSHOT_EXCLUDE_CACHE_DIRS for part in p.parts):
                continue
            if p.suffix in _SNAPSHOT_EXCLUDE_CACHE_SUFFIXES:
                continue
            result.append(norm)
        return sorted(set(result))

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

        self.last_quarantine_snapshot = None
        self.last_snapshot_only_files = []
        self.last_snapshot_only_error = None

        clean_id = re.sub(r"[^a-zA-Z0-9_\-]", "_", task_id)
        clean_team = re.sub(r"[^a-zA-Z0-9_\-]", "_", team_id) if team_id else ""
        clean_job = re.sub(r"[^a-zA-Z0-9_\-]", "_", job_id) if job_id else ""
        team_prefix = f"{clean_team}-" if clean_team else ""
        timestamp = int(time.time())

        proc_status = self._run_git(["status", "--porcelain"])
        has_changes = bool(proc_status.stdout.strip())

        quarantine_branch = ""
        current_branch = self.get_current_branch()

        if has_changes:
            # 1. 物理スナップショットの安全作成 (Manifest 連携)
            snapshot_path = self.create_quarantine_snapshot(
                task_id=f"{team_prefix}{task_id}",
                checkpoint=checkpoint,
                job_id=job_id,
            )
            self.last_quarantine_snapshot = snapshot_path

            # 1.5. スナップショットにだけある新しいファイルの一覧を隔離ブランチ作成・復元前に取得 (RCV-6)
            try:
                self.last_snapshot_only_files = self.list_snapshot_only_files(
                    snapshot_path,
                    checkpoint,
                )
            except Exception as exc:  # noqa: BLE001
                self.last_snapshot_only_files = []
                detail = " ".join(str(exc).splitlines()).strip()
                self.last_snapshot_only_error = (
                    f"{type(exc).__name__}: {detail}" if detail else type(exc).__name__
                )

            # 2. 隔離ブランチへの安全なステージとコミット (追跡済みファイルのみ add -u でステージし、シークレット検査を実施)
            if clean_team and clean_job:
                base_q_branch = f"quarantine/{clean_job}/{clean_team}/{clean_id}-{timestamp}"
            elif clean_team:
                base_q_branch = f"quarantine/{clean_team}-{clean_id}-{timestamp}"
            else:
                base_q_branch = f"quarantine/{clean_id}-{timestamp}"

            quarantine_branch = base_q_branch
            counter = 1
            while self._run_git(["branch", "--list", quarantine_branch]).stdout.strip():
                quarantine_branch = f"{base_q_branch}-{counter}"
                counter += 1

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
