from __future__ import annotations

import ast
import contextlib
import hashlib
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    import resource
except ImportError:
    resource = None  # type: ignore[assignment]

from auto_developer.guard import validate_test_ast


class DeterministicVerifier:
    """ローカル検証スクリプトの実行と判定を担当。"""

    def run_verification(
        self, script_path: Path, timeout_sec: int = 300, cwd: Path | None = None
    ) -> tuple[int, str]:
        """検証スクリプトを実行し、(exit_code, output) を返す。"""
        if not script_path.exists():
            return 1, f"Verification script not found: {script_path}"

        working_dir = cwd or script_path.parent
        try:
            proc = subprocess.run(
                ["bash", str(script_path)],
                cwd=working_dir,
                capture_output=True,
                text=True,
                check=False,
                timeout=timeout_sec,
            )
            output = proc.stdout + "\n" + proc.stderr
            return proc.returncode, output.strip()
        except subprocess.TimeoutExpired as e:
            return 124, f"Verification timed out after {timeout_sec}s: {e}"
        except Exception as e:  # noqa: BLE001
            return 1, f"Failed to execute verification script: {e}"


@dataclass
class RedVerificationResult:
    """再現テスト検証結果。"""

    is_reproduced: bool  # 真正な Red (アサーション失敗等) の場合のみ True
    exit_code: int
    reason: str  # GENUINE_RED, ALREADY_GREEN, SYNTAX_ERROR, IMPORT_ERROR, NO_TESTS_FOUND, TIMEOUT, EXECUTION_ERROR
    output: str = ""
    reason_detail: str = ""
    tests_run: int = 0
    failures: int = 0
    errors: int = 0
    process_limit_status: str = ""


class CommitCheckoutError(RuntimeError):
    """指定されたコミットのチェックアウトに失敗した際のエラー (Fail-Closed) (III.1)。"""


class RedTestVerifier:
    """監査役や外部から提示されたテスト候補が、現在のコードベースで確実に Red になるかを判定するハーネス。"""

    SECRET_ENV_PATTERN = re.compile(
        r"(TOKEN|SECRET|KEY|AUTH|CREDENTIAL|PASSWORD|PASS|API|PRIVATE)", re.IGNORECASE
    )
    DANGEROUS_ENV_PREFIXES = (
        "LD_",
        "DYLD_",
        "PYTHON",
    )

    def __init__(self, repo_dir: Path | None = None) -> None:
        self.repo_dir = repo_dir or Path.cwd()

    def verify_candidate_test(
        self, test_code: str, timeout_sec: int = 30, commit: str | None = None
    ) -> RedVerificationResult:
        """テストコード文字列を検証する。"""
        # 1. 構文チェック (SyntaxError の即時検出)
        try:
            tree = ast.parse(test_code)
        except SyntaxError as e:
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="SYNTAX_ERROR",
                output=str(e),
                reason_detail=f"SyntaxError: {e}",
            )

        # 2. トップレベル AST 検査 (モンキーパッチ・実行文の遮断)
        if not validate_test_ast(tree):
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="TOP_LEVEL_EXEC_FORBIDDEN",
                output="Top-level executable statements, monkeypatching, or forbidden module hooks detected in candidate test.",
                reason_detail="Candidate test contains top-level executable statements or forbidden hooks (AST validation failed).",
            )

        # 3. テストケースの存在確認
        has_test_class = any(
            isinstance(node, ast.ClassDef)
            and any(
                (isinstance(base, ast.Name) and "TestCase" in base.id)
                or (isinstance(base, ast.Attribute) and base.attr == "TestCase")
                for base in node.bases
            )
            for node in ast.walk(tree)
        )
        has_test_func = any(
            isinstance(node, ast.FunctionDef) and node.name.startswith("test_")
            for node in ast.walk(tree)
        )
        if not (has_test_class or has_test_func):
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=0,
                reason="NO_TESTS_FOUND",
                output="No TestCase classes or test_* functions found in candidate code.",
                reason_detail="No tests found",
            )

        # 3. 独立した一時クローン環境を作成して実行 (Physical Clone Isolation)
        with tempfile.TemporaryDirectory() as tmpdir:
            clone_path = Path(tmpdir) / "isolated_repo"
            try:
                self._create_isolated_clone(clone_path, commit=commit)
            except CommitCheckoutError as e:
                return RedVerificationResult(
                    is_reproduced=False,
                    exit_code=1,
                    reason="COMMIT_CHECKOUT_FAILED",
                    output=f"COMMIT_CHECKOUT_FAILED: {e}",
                    reason_detail=str(e),
                )
            test_file = clone_path / "test_candidate.py"
            test_file.write_text(test_code, encoding="utf-8")
            return self._execute_test(
                test_file, str(clone_path), timeout_sec, sandbox_dir=clone_path
            )

    def _create_isolated_clone(self, target_dir: Path, commit: str | None = None) -> Path:
        """元リポジトリから隔離されたクリーンな一時クローン（作業コピー）を作成する。
        Git リポジトリの場合は git clone --local --no-hardlinks で物理コピーを作成し、
        非Gitリポジトリの場合は shutil.copytree で複製する。
        commit が指定された場合はそのコミットにチェックアウト（デタッチドHEAD）し、未コミット変更は同期しない (HHH.1)。
        commit 指定時のクローン・チェックアウト失敗時は、現行ツリーへのフォールバックを禁止して CommitCheckoutError を送出する (III.1)。
        """
        resolved_repo = self.repo_dir.resolve()
        target_dir.parent.mkdir(parents=True, exist_ok=True)

        is_git_repo = (resolved_repo / ".git").is_dir()
        cloned_ok = False
        if is_git_repo:
            try:
                res = subprocess.run(
                    [
                        "git",
                        "clone",
                        "--local",
                        "--no-hardlinks",
                        str(resolved_repo),
                        str(target_dir),
                    ],
                    capture_output=True,
                    check=False,
                )
                if res.returncode == 0:
                    cloned_ok = True
                    if commit:
                        chk = subprocess.run(
                            ["git", "checkout", "--detach", commit],
                            cwd=target_dir,
                            capture_output=True,
                            check=False,
                        )
                        if chk.returncode != 0:
                            cloned_ok = False
                    else:
                        # 未コミットのファイル（ステージ済み・未追跡）も一時クローンへコピー
                        self._sync_uncommitted_files(resolved_repo, target_dir)
            except (OSError, subprocess.SubprocessError):
                cloned_ok = False
        elif commit is not None:
            raise CommitCheckoutError(
                f"Cannot check out commit '{commit}': Repository is not a git repository"
            )

        if not cloned_ok:
            if commit is not None:
                raise CommitCheckoutError(
                    f"Failed to check out requested commit '{commit}' into isolated clone"
                )
            if target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            shutil.copytree(
                resolved_repo,
                target_dir,
                dirs_exist_ok=True,
                symlinks=False,
                ignore=shutil.ignore_patterns(".git", ".dev/bundles", "__pycache__"),
            )

        return target_dir

    def _sync_uncommitted_files(self, source_repo: Path, target_repo: Path) -> None:
        """元リポジトリの未コミットの変更・新規ファイルを一時クローンへ同期する。"""
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            res = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=source_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode == 0 and res.stdout.strip():
                for line in res.stdout.splitlines():
                    parts = line.strip().split(maxsplit=1)
                    if len(parts) == 2:
                        rel_path = parts[1]
                        src_file = source_repo / rel_path
                        tgt_file = target_repo / rel_path
                        if src_file.is_file() and not src_file.is_symlink():
                            tgt_file.parent.mkdir(parents=True, exist_ok=True)
                            shutil.copy2(src_file, tgt_file)

    def verify_candidate_test_file(
        self, test_file_path: Path, timeout_sec: int = 30
    ) -> RedVerificationResult:
        """ファイルパスからテストコードを読み出して検証する。"""
        if not test_file_path.exists():
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="EXECUTION_ERROR",
                output=f"Test file not found: {test_file_path}",
                reason_detail=f"File not found: {test_file_path}",
            )
        code = test_file_path.read_text(encoding="utf-8")
        return self.verify_candidate_test(code, timeout_sec=timeout_sec)

    RUNNER_CODE = """
import json
import os
import sys
import unittest
from pathlib import Path

search_dir = sys.argv[1]
pattern = sys.argv[2]
protected_repo = sys.argv[3] if len(sys.argv) > 3 else ""

if protected_repo:
    try:
        resolved_protected = str(Path(protected_repo).resolve())
    except Exception:
        resolved_protected = protected_repo

    def _security_audit_hook(event, args):
        # 1. chmod / chown
        if event in ("os.chmod", "os.chown"):
            if args and args[0]:
                try:
                    p = str(Path(args[0]).resolve())
                except Exception:
                    p = str(args[0])
                if p == resolved_protected or p.startswith(resolved_protected + os.sep):
                    raise PermissionError(f"Security audit hook: '{event}' on protected repo '{p}' is forbidden.")
        # 2. write open
        elif event == "open":
            if args and len(args) >= 2:
                path_arg, mode_arg = args[0], args[1]
                flags_arg = args[2] if len(args) > 2 else None
                is_write = (mode_arg and any(m in mode_arg for m in ("w", "a", "x", "+"))) or (
                    flags_arg is not None and bool(flags_arg & (os.O_WRONLY | os.O_RDWR))
                )
                if is_write and path_arg:
                    try:
                        p = str(Path(path_arg).resolve())
                    except Exception:
                        p = str(path_arg)
                    if p == resolved_protected or p.startswith(resolved_protected + os.sep):
                        raise PermissionError(f"Security audit hook: write to protected repo '{p}' is forbidden.")
        # 3. delete / rename / mkdir
        elif event in ("os.remove", "os.unlink", "os.rmdir", "os.mkdir"):
            if args and args[0]:
                try:
                    p = str(Path(args[0]).resolve())
                except Exception:
                    p = str(args[0])
                if p == resolved_protected or p.startswith(resolved_protected + os.sep):
                    raise PermissionError(f"Security audit hook: '{event}' on protected repo '{p}' is forbidden.")
        elif event == "os.rename":
            for arg in args[:2]:
                if arg:
                    try:
                        p = str(Path(arg).resolve())
                    except Exception:
                        p = str(arg)
                    if p == resolved_protected or p.startswith(resolved_protected + os.sep):
                        raise PermissionError(f"Security audit hook: 'os.rename' involving protected repo '{p}' is forbidden.")

    sys.addaudithook(_security_audit_hook)

loader = unittest.defaultTestLoader
try:
    suite = loader.discover(start_dir=search_dir, pattern=pattern)
except Exception as e:
    data = {
        "tests_run": 0,
        "failures": [],
        "errors": [{"test": "loader", "err": str(e)}],
    }
    print("===AUTO_DEV_TEST_RESULT===")
    print(json.dumps(data))
    sys.exit(1)

result = unittest.TestResult()
suite.run(result)

data = {
    "tests_run": result.testsRun,
    "failures": [{"test": str(test), "err": err} for test, err in result.failures],
    "errors": [{"test": str(test), "err": err} for test, err in result.errors],
}
print("===AUTO_DEV_TEST_RESULT===")
print(json.dumps(data))
sys.exit(0 if (result.testsRun > 0 and len(result.failures) == 0 and len(result.errors) == 0) else 1)
"""

    @contextlib.contextmanager
    def _lockdown_repo(self, repo_dir: Path) -> Iterator[None]:
        """候補テスト実行中、対象リポジトリ配下の全ファイルおよびディレクトリから
        書き込み権限を一時的に剥奪（a-w）して OS 権限レベルで読取専用化し、
        さらに macOS では User Immutable Flag (chflags uchg) を設定して、
        テスト終了後に元のパーミッションおよびフラグを確実に復元する。
        """
        original_modes: list[tuple[Path, int]] = []
        resolved_repo = repo_dir.resolve()
        write_mask = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
        has_chflags = sys.platform == "darwin" and shutil.which("chflags") is not None

        items: list[tuple[Path, int, bool]] = []
        try:
            if resolved_repo.exists():
                for root, dirs, files in os.walk(resolved_repo):
                    root_path = Path(root)
                    for f in files:
                        p = root_path / f
                        try:
                            st = p.lstat()
                            if not stat.S_ISLNK(st.st_mode):
                                items.append((p, st.st_mode, False))
                        except OSError:
                            pass
                    for d in dirs:
                        p = root_path / d
                        try:
                            st = p.lstat()
                            if not stat.S_ISLNK(st.st_mode):
                                items.append((p, st.st_mode, True))
                        except OSError:
                            pass
                try:
                    st_repo = resolved_repo.lstat()
                    items.append((resolved_repo, st_repo.st_mode, True))
                except OSError:
                    pass

                # ファイルの書き込み権限を先に剥奪
                for p, mode, is_dir in items:
                    if not is_dir:
                        try:
                            p.chmod(mode & ~write_mask)
                            original_modes.append((p, mode))
                        except OSError:
                            pass

                # ディレクトリの書き込み権限を剥奪 (読取・実行権は維持)
                for p, mode, is_dir in items:
                    if is_dir:
                        try:
                            p.chmod(mode & ~write_mask)
                            original_modes.append((p, mode))
                        except OSError:
                            pass

                # macOS の場合、カーネルレベルで chmod 権限復元を物理阻止する uchg フラグを設定
                if has_chflags:
                    with contextlib.suppress(OSError, subprocess.SubprocessError):
                        subprocess.run(
                            ["chflags", "-R", "uchg", str(resolved_repo)],
                            capture_output=True,
                            check=False,
                        )

            yield

        finally:
            # macOS の uchg フラグを解除
            if has_chflags:
                with contextlib.suppress(OSError, subprocess.SubprocessError):
                    subprocess.run(
                        ["chflags", "-R", "nouchg", str(resolved_repo)],
                        capture_output=True,
                        check=False,
                    )

            # 復元: ディレクトリ (親) から先に元の書き込み権限を復元
            for p, mode in reversed(original_modes):
                try:
                    if p.exists() and p.is_dir():
                        p.chmod(mode)
                except OSError:
                    pass
            # 次にファイルのパーミッションを復元
            for p, mode in original_modes:
                try:
                    if p.exists() and not p.is_dir():
                        p.chmod(mode)
                except OSError:
                    pass

    def _compute_nproc_limit(self, margin: int = 50) -> tuple[tuple[int, int] | None, str]:
        """現在のユーザープロセス数に基づいて RLIMIT_NPROC の (soft, hard) 上限を計算する (JJJ.3)。

        RLIMIT_NPROC はプロセス単位ではなくユーザー(UID)全体のプロセス数上限であるため、
        現在のプロセス数 + マージンで設定する。取得に失敗した場合は制限を設定せずステータスを返す。
        """
        if not hasattr(resource, "RLIMIT_NPROC") or os.name != "posix":
            return None, "unsupported"

        try:
            _cur_soft, cur_hard = resource.getrlimit(resource.RLIMIT_NPROC)
            res = subprocess.run(
                ["ps", "-u", str(os.getuid()), "-o", "pid="],
                capture_output=True,
                text=True,
                check=False,
            )
            if res.returncode != 0:
                return None, f"ps_failed(code={res.returncode})"
            lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
            cur_proc_count = len(lines)
            target = cur_proc_count + margin
            if cur_hard != resource.RLIM_INFINITY and target > cur_hard:
                target = cur_hard
            return (target, cur_hard), f"applied: limit={target} (cur_count={cur_proc_count}, margin={margin})"
        except Exception as e:  # noqa: BLE001
            return None, f"error: {e}"

    def _apply_resource_limits(
        self,
        timeout_sec: int,
        nproc_limits: tuple[int, int] | None = None,
    ) -> None:
        """Apply OS-level execution resource boundaries to child process (POSIX)."""
        with contextlib.suppress(Exception):
            # 1. ファイル出力上限 (巨大ファイル作成によるディスク浪費・DoS防止: 10MB)
            if hasattr(resource, "RLIMIT_FSIZE"):
                max_fsize = 10 * 1024 * 1024
                resource.setrlimit(resource.RLIMIT_FSIZE, (max_fsize, max_fsize))

            # 2. 仮想メモリ上限 (過剰メモリ消費・OOM爆弾の防止: 1GB)
            if hasattr(resource, "RLIMIT_AS"):
                max_as = 1024 * 1024 * 1024
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_AS, (max_as, max_as))

            # 3. CPU 時間上限 (タイムアウト + マージン)
            if hasattr(resource, "RLIMIT_CPU"):
                cpu_limit = max(1, timeout_sec + 5)
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_CPU, (cpu_limit, cpu_limit))

            # 4. プロセス生成数上限 (RLIMIT_NPROC: 動的計算された上限を設定) (JJJ.3)
            if nproc_limits and hasattr(resource, "RLIMIT_NPROC"):
                with contextlib.suppress(ValueError, OSError):
                    resource.setrlimit(resource.RLIMIT_NPROC, nproc_limits)

    def _get_sandbox_backend(self) -> str | None:
        """利用可能な OS サンドボックス機構を検出する (macOS: sandbox-exec, Linux: bwrap)。"""
        if sys.platform == "darwin" and shutil.which("sandbox-exec"):
            return "sandbox-exec"
        if shutil.which("bwrap"):
            return "bwrap"
        return None

    def _capture_repo_snapshot(self) -> dict[str, Any]:
        """元リポジトリの改ざん検知用スナップショットを取得する (JJJ.2)。"""
        resolved_repo = self.repo_dir.resolve()
        is_git = (resolved_repo / ".git").is_dir()
        if is_git:
            # 1. HEAD ハッシュ
            proc_head = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=resolved_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            head_commit = proc_head.stdout.strip() if proc_head.returncode == 0 else ""

            # 2. ステータス (追跡・未追跡・無視ファイルすべて)
            proc_status = subprocess.run(
                ["git", "status", "--porcelain", "--ignored", "--untracked-files=all"],
                cwd=resolved_repo,
                capture_output=True,
                text=True,
                check=False,
            )
            status_text = proc_status.stdout if proc_status.returncode == 0 else ""

            # 3. diff ハッシュ
            diff_args = ["git", "diff", "HEAD", "--binary"] if head_commit else ["git", "diff", "--binary"]
            proc_diff = subprocess.run(
                diff_args,
                cwd=resolved_repo,
                capture_output=True,
                check=False,
            )
            diff_hash = hashlib.sha256(proc_diff.stdout).hexdigest() if proc_diff.returncode == 0 else ""

            return {
                "type": "git",
                "head": head_commit,
                "status": status_text,
                "diff_hash": diff_hash,
            }

        # 非 Git リポジトリ: ファイルツリー走査
        file_hashes: dict[str, str] = {}
        for p in sorted(resolved_repo.rglob("*")):
            if p.is_file():
                rel = str(p.relative_to(resolved_repo))
                with contextlib.suppress(OSError):
                    file_hashes[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
        return {
            "type": "tree",
            "files": file_hashes,
        }

    def _compare_repo_snapshots(
        self, before: dict[str, Any], after: dict[str, Any]
    ) -> list[str]:
        """実行前後のスナップショットを比較し、改ざんされた相対パス一覧を返す (JJJ.2)。"""
        if before.get("type") == "git" and after.get("type") == "git":
            if before == after:
                return []
            changed_paths: list[str] = []
            if before.get("head") != after.get("head"):
                changed_paths.append(".git/HEAD")
            before_lines = set(before.get("status", "").splitlines())
            after_lines = set(after.get("status", "").splitlines())
            for line in after_lines - before_lines:
                parts = line.strip().split(maxsplit=1)
                changed_paths.append(parts[1] if len(parts) == 2 else line.strip())
            for line in before_lines - after_lines:
                parts = line.strip().split(maxsplit=1)
                changed_paths.append(parts[1] if len(parts) == 2 else line.strip())
            if not changed_paths and before.get("diff_hash") != after.get("diff_hash"):
                changed_paths.append("tracked_diff_modified")
            return sorted(set(changed_paths))
        if before.get("type") == "tree" and after.get("type") == "tree":
            b_files = before.get("files", {})
            a_files = after.get("files", {})
            all_keys = set(b_files.keys()) | set(a_files.keys())
            diffs = [k for k in all_keys if b_files.get(k) != a_files.get(k)]
            return sorted(diffs)
        return ["snapshot_type_mismatch"]

    def _execute_test(
        self,
        test_file: Path,
        search_dir: str,
        timeout_sec: int,
        sandbox_dir: Path | None = None,
    ) -> RedVerificationResult:
        """構造化 TestResult を用いて候補テストを実行し、Red/Green/無効を判定する。
        本体リポジトリ汚染を防ぐため、実行作業ディレクトリは使い捨ての一時クローン sandbox_dir に隔離し、
        シークレット等の機密環境変数を除外サニタイズした上で、本体作業ツリーを OS 権限レベルおよび
        カーネルファイルフラグで読取専用にロックダウンして実行する。
        さらに、OS レベルのサンドボックス (macOS sandbox-exec / Linux bwrap) の内側でのみ実行し、
        外部ネットワーク接続および元リポジトリへのファイル書き込みをカーネルレベルで遮断する (JJJ.1)。
        また、プロセスグループ分離 (start_new_session=True) により子孫プロセスを一括回収する (JJJ.3)。

        【既知の制約 (Known Limitation)】:
        プロセスグループ単位での SIGKILL による一括回収（リーピング）を行うが、
        子プロセスが意図的に `setsid()` や独自プロセスグループ (`setpgid`) を呼び出して
        セッション・プロセスグループを切り離した場合、親からのプロセスグループシグナルは
        届かない。完全なプロセスツリー追跡およびプロセス隔離には、OS レベルの
        cgroup やコンテナ (PID namespace) が必要である。
        """
        nproc_limits, proc_limit_status = self._compute_nproc_limit()

        def _res(**kwargs: Any) -> RedVerificationResult:
            if "process_limit_status" not in kwargs:
                kwargs["process_limit_status"] = proc_limit_status
            return RedVerificationResult(**kwargs)

        # 0. root (UID 0) 実行検知と安全遮断 (OSパーミッション迂回の防止)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="ROOT_EXECUTION_FORBIDDEN",
                output="Running candidate tests as root (UID 0) bypasses OS permission lockdown and is forbidden.",
                reason_detail="Running candidate tests as root (UID 0) bypasses OS permission lockdown and is forbidden.",
            )

        # 0.1 OS サンドボックス基盤の確認 (Fail-Closed: サンドボックス不可時は即座に拒絶)
        backend = self._get_sandbox_backend()
        if not backend:
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="SANDBOX_UNAVAILABLE",
                output="OS sandbox mechanism (sandbox-exec on macOS or bwrap on Linux) is not available or failed to initialize.",
                reason_detail="OS sandbox execution environment is unavailable (Fail-Closed).",
            )

        # 1. 環境変数のサニタイズ（シークレット・APIキー・危険動的変数の除外）
        sanitized_env: dict[str, str] = {}
        for k, v in os.environ.items():
            if self.SECRET_ENV_PATTERN.search(k):
                continue
            if any(k.startswith(pfx) for pfx in self.DANGEROUS_ENV_PREFIXES):
                continue
            sanitized_env[k] = v

        # 2. 実行作業ディレクトリの隔離 (本体 repo_dir への書き込み防止)
        exec_cwd = sandbox_dir or Path(search_dir)
        exec_resolved = exec_cwd.resolve()
        repo_resolved = self.repo_dir.resolve()

        # PYTHONPATH は元リポジトリではなく、一時クローンのパスを設定
        src_path = str(exec_resolved / "src")
        repo_path = str(exec_resolved)
        sanitized_env["PYTHONPATH"] = f"{src_path}:{repo_path}"
        sanitized_env["TMPDIR"] = str(exec_resolved)

        runner_args = [
            sys.executable,
            "-c",
            self.RUNNER_CODE,
            search_dir,
            test_file.name,
            str(repo_resolved),
        ]

        profile_file: Path | None = None
        if backend == "sandbox-exec":
            profile_content = f"""(version 1)
(allow default)
(deny file-write*)
(allow file-write* (subpath "{exec_resolved}"))
(allow file-write* (literal "/dev/null"))
(allow file-write* (literal "/dev/zero"))
(allow file-write* (literal "/dev/dtracehelper"))
(deny file-write* (subpath "{repo_resolved}"))
(deny network*)
"""
            with tempfile.NamedTemporaryFile("w", suffix=".sb", delete=False, dir=str(exec_resolved)) as tf:
                tf.write(profile_content)
                tf.flush()
                profile_file = Path(tf.name)
            cmd = ["sandbox-exec", "-f", str(profile_file)] + runner_args
        elif backend == "bwrap":
            cmd = [
                "bwrap",
                "--ro-bind", "/", "/",
                "--bind", str(exec_resolved), str(exec_resolved),
                "--dev", "/dev",
                "--proc", "/proc",
                "--unshare-net",
                "--die-with-parent",
            ] + runner_args
        else:
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="SANDBOX_UNAVAILABLE",
                output=f"Unknown sandbox backend: {backend}",
                reason_detail="Unknown sandbox backend.",
            )

        preexec = (
            (lambda: self._apply_resource_limits(timeout_sec, nproc_limits))
            if os.name == "posix"
            else None
        )

        before_snapshot = self._capture_repo_snapshot()

        proc = subprocess.Popen(
            cmd,
            cwd=exec_cwd,
            env=sanitized_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=bool(os.name == "posix"),
            preexec_fn=preexec,  # noqa: PLW1509
        )
        pgid = proc.pid
        stdout = ""
        stderr = ""

        try:
            try:
                with self._lockdown_repo(self.repo_dir):
                    stdout, stderr = proc.communicate(timeout=timeout_sec)
            except subprocess.TimeoutExpired as e:
                if pgid and os.name == "posix":
                    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                        os.killpg(pgid, signal.SIGKILL)
                with contextlib.suppress(Exception):
                    stdout, stderr = proc.communicate(timeout=5)
                return _res(
                    is_reproduced=False,
                    exit_code=124,
                    reason="TIMEOUT",
                    output=str(e),
                    reason_detail=f"Test timed out after {timeout_sec}s",
                )
            except Exception as e:  # noqa: BLE001
                if pgid and os.name == "posix":
                    with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                        os.killpg(pgid, signal.SIGKILL)
                return _res(
                    is_reproduced=False,
                    exit_code=1,
                    reason="EXECUTION_ERROR",
                    output=str(e),
                    reason_detail=str(e),
                )
        finally:
            # 正常終了時・例外時にも残存した子孫プロセスグループ全体を一括回収 (JJJ.3)
            if pgid and os.name == "posix":
                with contextlib.suppress(ProcessLookupError, PermissionError, OSError):
                    os.killpg(pgid, signal.SIGKILL)
            if profile_file and profile_file.exists():
                with contextlib.suppress(OSError):
                    profile_file.unlink()

        # 3. 元リポジトリ改ざん検知 (JJJ.2: 多層防御)
        after_snapshot = self._capture_repo_snapshot()
        tampered_paths = self._compare_repo_snapshots(before_snapshot, after_snapshot)
        if tampered_paths:
            return _res(
                is_reproduced=False,
                exit_code=1,
                reason="REPO_TAMPER_DETECTED",
                output=f"Parent repository tampering detected after candidate test execution. Modified paths: {tampered_paths}",
                reason_detail=f"Parent repository was tampered with during test execution: {', '.join(tampered_paths)}",
            )

        returncode = proc.returncode if proc.returncode is not None else 1
        if returncode != 0 and ("sandbox-exec:" in stderr or "sandbox_apply:" in stderr):
            return _res(
                is_reproduced=False,
                exit_code=returncode,
                reason="SANDBOX_UNAVAILABLE",
                output=stderr,
                reason_detail="OS sandbox failed to execute candidate test.",
            )

        output = (stdout + "\n" + stderr).strip()

        # 構造化 TestResult (JSON) の抽出
        marker = "===AUTO_DEV_TEST_RESULT==="
        parsed_result: dict[str, Any] | None = None
        if marker in stdout:
            try:
                json_part = stdout.split(marker, 1)[1].strip()
                parsed_result = json.loads(json_part)
            except (json.JSONDecodeError, IndexError, ValueError):
                parsed_result = None

        if parsed_result is not None:
            tests_run: int = parsed_result.get("tests_run", 0)
            failures: list[dict[str, str]] = parsed_result.get("failures", [])
            errors: list[dict[str, str]] = parsed_result.get("errors", [])

            # 判定 A: 実行件数チェック (0件実行)
            if tests_run == 0 or returncode == 5:
                return _res(
                    is_reproduced=False,
                    exit_code=returncode,
                    reason="NO_TESTS_RAN",
                    output=output,
                    reason_detail="No test methods were executed during unittest run (ran_count=0).",
                    tests_run=0,
                    failures=len(failures),
                    errors=len(errors),
                )

            # 判定 B: すでに Green (エラーも失敗もなし)
            if tests_run > 0 and len(failures) == 0 and len(errors) == 0:
                return _res(
                    is_reproduced=False,
                    exit_code=0,
                    reason="ALREADY_GREEN",
                    output=output,
                    reason_detail="Candidate test passed against current codebase without failures.",
                    tests_run=tests_run,
                    failures=0,
                    errors=0,
                )

            # 判定 C / D / F: エラー (errors) が存在する場合
            if len(errors) > 0:
                err_text = " ".join(e.get("err", "") for e in errors)
                # インポート不能 / モジュール未発見
                if (
                    "ImportError" in err_text
                    or "ModuleNotFoundError" in err_text
                    or any("_FailedTest" in str(e) for e in errors)
                ):
                    return _res(
                        is_reproduced=False,
                        exit_code=returncode,
                        reason="IMPORT_ERROR",
                        output=output,
                        reason_detail="Test failed due to ImportError / ModuleNotFoundError.",
                        tests_run=tests_run,
                        failures=len(failures),
                        errors=len(errors),
                    )
                # setUp() / tearDown() での環境例外
                if "in setUp" in err_text or "in tearDown" in err_text:
                    return _res(
                        is_reproduced=False,
                        exit_code=returncode,
                        reason="SETUP_ERROR",
                        output=output,
                        reason_detail="Test failure occurred during setUp/tearDown rather than test body assertion.",
                        tests_run=tests_run,
                        failures=len(failures),
                        errors=len(errors),
                    )
                # テスト本体内での予期せぬ例外 (アサーション失敗ではない)
                return _res(
                    is_reproduced=False,
                    exit_code=returncode,
                    reason="UNHANDLED_EXCEPTION",
                    output=output,
                    reason_detail="Test exited with error/exception without an assertion failure (failures=0, errors>0).",
                    tests_run=tests_run,
                    failures=len(failures),
                    errors=len(errors),
                )

            # 判定 E: アサーション失敗 (failures > 0, errors == 0) による真正な Red
            if len(failures) > 0:
                return _res(
                    is_reproduced=True,
                    exit_code=returncode,
                    reason="GENUINE_RED",
                    output=output,
                    reason_detail="Candidate test failed as expected with assertion failure (Genuine Red).",
                    tests_run=tests_run,
                    failures=len(failures),
                    errors=len(errors),
                )

        # フォールバック (構造化 JSON が取得できなかった場合)
        m_ran = re.search(r"Ran (\d+) tests?", output)
        ran_count = int(m_ran.group(1)) if m_ran else 0
        m_fail = re.search(r"failures=(\d+)", output)
        fail_count = int(m_fail.group(1)) if m_fail else (1 if "FAIL:" in output else 0)
        m_err = re.search(r"errors=(\d+)", output)
        err_count = int(m_err.group(1)) if m_err else (1 if "ERROR:" in output else 0)

        if ran_count == 0 or "NO TESTS RAN" in output or returncode == 5:
            return _res(
                is_reproduced=False,
                exit_code=returncode,
                reason="NO_TESTS_RAN",
                output=output,
                reason_detail="No test methods were executed during unittest run (ran_count=0).",
                tests_run=ran_count,
                failures=fail_count,
                errors=err_count,
            )

        if returncode == 0:
            return _res(
                is_reproduced=False,
                exit_code=0,
                reason="ALREADY_GREEN",
                output=output,
                reason_detail="Candidate test passed against current codebase without failures.",
                tests_run=ran_count,
                failures=0,
                errors=0,
            )

        if (
            "ImportError: Failed to import test module" in output
            or "ModuleNotFoundError:" in output
            or "ImportError:" in output
        ):
            return _res(
                is_reproduced=False,
                exit_code=returncode,
                reason="IMPORT_ERROR",
                output=output,
                reason_detail="Test failed due to ImportError / ModuleNotFoundError.",
                tests_run=ran_count,
                failures=fail_count,
                errors=err_count,
            )

        if "in setUp" in output or "in tearDown" in output:
            return _res(
                is_reproduced=False,
                exit_code=returncode,
                reason="SETUP_ERROR",
                output=output,
                reason_detail="Test failure occurred during setUp/tearDown rather than test body assertion.",
                tests_run=ran_count,
                failures=fail_count,
                errors=err_count,
            )

        return _res(
            is_reproduced=False,
            exit_code=returncode,
            reason="UNHANDLED_EXCEPTION",
            output=output,
            reason_detail="Test exited with error/exception without structured test result.",
            tests_run=ran_count,
            failures=fail_count,
            errors=err_count,
        )
