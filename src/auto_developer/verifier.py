from __future__ import annotations

import ast
import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

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


class RedTestVerifier:
    """監査役や外部から提示されたテスト候補が、現在のコードベースで確実に Red になるかを判定するハーネス。"""

    SECRET_ENV_PATTERN = re.compile(
        r"(TOKEN|SECRET|KEY|AUTH|CREDENTIAL|PASSWORD|PASS|API|PRIVATE)", re.IGNORECASE
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
            self._create_isolated_clone(clone_path, commit=commit)
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

        if not cloned_ok:
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
        """
        # 0. root (UID 0) 実行検知と安全遮断 (OSパーミッション迂回の防止)
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="ROOT_EXECUTION_FORBIDDEN",
                output="Running candidate tests as root (UID 0) bypasses OS permission lockdown and is forbidden.",
                reason_detail="Running candidate tests as root (UID 0) bypasses OS permission lockdown and is forbidden.",
            )

        # 1. 環境変数のサニタイズ（シークレット・APIキーの除外）
        sanitized_env: dict[str, str] = {}
        for k, v in os.environ.items():
            if self.SECRET_ENV_PATTERN.search(k):
                continue
            sanitized_env[k] = v

        # 2. 実行作業ディレクトリの隔離 (本体 repo_dir への書き込み防止)
        exec_cwd = sandbox_dir or Path(search_dir)

        # PYTHONPATH は元リポジトリではなく、一時クローンのパスを設定
        src_path = str((exec_cwd / "src").resolve())
        repo_path = str(exec_cwd.resolve())
        existing_pythonpath = sanitized_env.get("PYTHONPATH", "")
        sanitized_env["PYTHONPATH"] = f"{src_path}:{repo_path}:{existing_pythonpath}".strip(":")

        cmd = [
            sys.executable,
            "-c",
            self.RUNNER_CODE,
            search_dir,
            test_file.name,
            str(self.repo_dir.resolve()),
        ]

        try:
            with self._lockdown_repo(self.repo_dir):
                proc = subprocess.run(
                    cmd,
                    cwd=exec_cwd,
                    env=sanitized_env,
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                    check=False,
                )
        except subprocess.TimeoutExpired as e:
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=124,
                reason="TIMEOUT",
                output=str(e),
                reason_detail=f"Test timed out after {timeout_sec}s",
            )
        except Exception as e:  # noqa: BLE001
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="EXECUTION_ERROR",
                output=str(e),
                reason_detail=str(e),
            )

        output = (proc.stdout + "\n" + proc.stderr).strip()

        # 構造化 TestResult (JSON) の抽出
        marker = "===AUTO_DEV_TEST_RESULT==="
        parsed_result: dict[str, Any] | None = None
        if marker in proc.stdout:
            try:
                json_part = proc.stdout.split(marker, 1)[1].strip()
                parsed_result = json.loads(json_part)
            except (json.JSONDecodeError, IndexError, ValueError):
                parsed_result = None

        if parsed_result is not None:
            tests_run: int = parsed_result.get("tests_run", 0)
            failures: list[dict[str, str]] = parsed_result.get("failures", [])
            errors: list[dict[str, str]] = parsed_result.get("errors", [])

            # 判定 A: 実行件数チェック (0件実行)
            if tests_run == 0 or proc.returncode == 5:
                return RedVerificationResult(
                    is_reproduced=False,
                    exit_code=proc.returncode,
                    reason="NO_TESTS_RAN",
                    output=output,
                    reason_detail="No test methods were executed during unittest run (ran_count=0).",
                )

            # 判定 B: すでに Green (エラーも失敗もなし)
            if tests_run > 0 and len(failures) == 0 and len(errors) == 0:
                return RedVerificationResult(
                    is_reproduced=False,
                    exit_code=0,
                    reason="ALREADY_GREEN",
                    output=output,
                    reason_detail="Candidate test passed against current codebase without failures.",
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
                    return RedVerificationResult(
                        is_reproduced=False,
                        exit_code=proc.returncode,
                        reason="IMPORT_ERROR",
                        output=output,
                        reason_detail="Test failed due to ImportError / ModuleNotFoundError.",
                    )
                # setUp() / tearDown() での環境例外
                if "in setUp" in err_text or "in tearDown" in err_text:
                    return RedVerificationResult(
                        is_reproduced=False,
                        exit_code=proc.returncode,
                        reason="SETUP_ERROR",
                        output=output,
                        reason_detail="Test failure occurred during setUp/tearDown rather than test body assertion.",
                    )
                # テスト本体内での予期せぬ例外 (アサーション失敗ではない)
                return RedVerificationResult(
                    is_reproduced=False,
                    exit_code=proc.returncode,
                    reason="UNHANDLED_EXCEPTION",
                    output=output,
                    reason_detail="Test exited with error/exception without an assertion failure (failures=0, errors>0).",
                )

            # 判定 E: アサーション失敗 (failures > 0, errors == 0) による真正な Red
            if len(failures) > 0:
                return RedVerificationResult(
                    is_reproduced=True,
                    exit_code=proc.returncode,
                    reason="GENUINE_RED",
                    output=output,
                    reason_detail="Candidate test failed as expected with assertion failure (Genuine Red).",
                )

        # フォールバック (構造化 JSON が取得できなかった場合)
        m_ran = re.search(r"Ran (\d+) tests?", output)
        ran_count = int(m_ran.group(1)) if m_ran else 0
        if ran_count == 0 or "NO TESTS RAN" in output or proc.returncode == 5:
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=proc.returncode,
                reason="NO_TESTS_RAN",
                output=output,
                reason_detail="No test methods were executed during unittest run (ran_count=0).",
            )

        if proc.returncode == 0:
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=0,
                reason="ALREADY_GREEN",
                output=output,
                reason_detail="Candidate test passed against current codebase without failures.",
            )

        if (
            "ImportError: Failed to import test module" in output
            or "ModuleNotFoundError:" in output
            or "ImportError:" in output
        ):
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=proc.returncode,
                reason="IMPORT_ERROR",
                output=output,
                reason_detail="Test failed due to ImportError / ModuleNotFoundError.",
            )

        if "in setUp" in output or "in tearDown" in output:
            return RedVerificationResult(
                is_reproduced=False,
                exit_code=proc.returncode,
                reason="SETUP_ERROR",
                output=output,
                reason_detail="Test failure occurred during setUp/tearDown rather than test body assertion.",
            )

        return RedVerificationResult(
            is_reproduced=False,
            exit_code=proc.returncode,
            reason="UNHANDLED_EXCEPTION",
            output=output,
            reason_detail="Test exited with error/exception without structured test result.",
        )
