from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
tests_dir = str(Path(__file__).resolve().parent)
if tests_dir not in sys.path:
    sys.path.insert(0, tests_dir)

from sandbox_test_helpers import require_platform_sandbox

import auto_developer.verifier as verifier_mod
from auto_developer.verifier import RedTestVerifier


def _reset_cache() -> None:
    reset_fn = getattr(verifier_mod, "_reset_sandbox_backend_cache", None)
    if callable(reset_fn):
        reset_fn()


def _probe_router(
    sandbox_binary: str,
    returncode: int,
    stderr: str = "",
    stdout: str = "",
) -> tuple[Any, list[list[str]]]:
    """サンドボックスの起動確認コマンドだけを差し替え、git 等のコマンドは通常実行するルーター。"""
    orig_run = subprocess.run
    captured: list[list[str]] = []

    def _router(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd and Path(str(cmd[0])).name == sandbox_binary:
            captured.append([str(c) for c in cmd])
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=returncode,
                stdout=stdout,
                stderr=stderr,
            )
        return orig_run(*args, **kwargs)

    return _router, captured


class TestSandboxPreflight(unittest.TestCase):
    """SBX-3: サンドボックスの実働確認・キャッシュ・OS ごとの対処メッセージを検証するテスト。"""

    def setUp(self) -> None:
        _reset_cache()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp_dir.name)
        subprocess.run(
            ["git", "init", "-b", "main"],
            cwd=self.repo_dir,
            check=True,
            capture_output=True,
        )
        subprocess.run(["git", "config", "user.name", "TestUser"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)
        (self.repo_dir / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        self.verifier = RedTestVerifier(repo_dir=self.repo_dir)
        self.sample_test = (
            "import unittest\n"
            "from app import add\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_fail(self):\n"
            "        self.assertEqual(add(1, 2), 99)\n"
        )

    def tearDown(self) -> None:
        _reset_cache()
        self.tmp_dir.cleanup()

    def test_linux_missing_bwrap_returns_none_and_install_guidance(self) -> None:
        """Linux で bwrap がない場合、None を返し bubblewrap のインストール対処を含む SANDBOX_UNAVAILABLE を返すこと。"""
        with (
            patch("auto_developer.verifier.sys.platform", "linux"),
            patch("auto_developer.verifier.shutil.which", return_value=None),
        ):
            self.assertIsNone(self.verifier._get_sandbox_backend())
            result = self.verifier.verify_candidate_test(self.sample_test)

        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "SANDBOX_UNAVAILABLE")
        self.assertNotEqual(result.exit_code, 0)
        combined = f"{result.output}\n{result.reason_detail}"
        self.assertIn("bubblewrap", combined)
        self.assertIn("bwrap", combined)

    def test_linux_bwrap_unlaunchable_returns_none_and_apparmor_guidance(self) -> None:
        """Linux で bwrap が存在しても起動できない場合、AppArmor と bwrap-userns-restrict の対処を含む SANDBOX_UNAVAILABLE を返すこと。"""
        router, captured = _probe_router(
            "bwrap",
            returncode=1,
            stderr="bwrap: setting up uid map: Permission denied",
        )
        with (
            patch("auto_developer.verifier.sys.platform", "linux"),
            patch("auto_developer.verifier.shutil.which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None),
            patch("auto_developer.verifier.subprocess.run", side_effect=router),
        ):
            self.assertIsNone(self.verifier._get_sandbox_backend())
            result = self.verifier.verify_candidate_test(self.sample_test)

        self.assertEqual(len(captured), 1, "bwrap probe must run exactly once and be cached")
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "SANDBOX_UNAVAILABLE")
        self.assertNotEqual(result.exit_code, 0)
        combined = f"{result.output}\n{result.reason_detail}"
        self.assertIn("Ubuntu 23.10", combined)
        self.assertIn("AppArmor", combined)
        self.assertIn("bwrap-userns-restrict", combined)

    def test_linux_bwrap_working_returns_bwrap_and_caches_probe(self) -> None:
        """Linux で bwrap が実際に起動できる場合、最小コマンドで確認し 'bwrap' を返し、別インスタンスでもキャッシュを使うこと。"""
        router, captured = _probe_router("bwrap", returncode=0)
        with (
            patch("auto_developer.verifier.sys.platform", "linux"),
            patch("auto_developer.verifier.shutil.which", side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None),
            patch("auto_developer.verifier.subprocess.run", side_effect=router),
        ):
            other_verifier = RedTestVerifier(repo_dir=self.repo_dir)
            self.assertEqual(self.verifier._get_sandbox_backend(), "bwrap")
            self.assertEqual(self.verifier._get_sandbox_backend(), "bwrap")
            self.assertEqual(other_verifier._get_sandbox_backend(), "bwrap")

        self.assertEqual(len(captured), 1, "Probe result must be cached process-wide across instances")
        self.assertIn("bwrap", captured[0][0])

    def test_macos_missing_sandbox_exec_returns_none_and_guidance(self) -> None:
        """macOS で sandbox-exec がない場合、None を返し macOS 向けの SANDBOX_UNAVAILABLE メッセージを返すこと。"""
        with (
            patch("auto_developer.verifier.sys.platform", "darwin"),
            patch("auto_developer.verifier.shutil.which", return_value=None),
        ):
            self.assertIsNone(self.verifier._get_sandbox_backend())
            result = self.verifier.verify_candidate_test(self.sample_test)

        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "SANDBOX_UNAVAILABLE")
        self.assertNotEqual(result.exit_code, 0)
        combined = f"{result.output}\n{result.reason_detail}"
        self.assertIn("sandbox-exec", combined)
        self.assertIn("macOS", combined)
        self.assertNotIn("bwrap", combined)

    def test_macos_sandbox_exec_unlaunchable_returns_none_and_guidance(self) -> None:
        """macOS で sandbox-exec が存在しても起動できない場合、None を返し macOS 向けの SANDBOX_UNAVAILABLE メッセージを返すこと。"""
        router, captured = _probe_router(
            "sandbox-exec",
            returncode=71,
            stderr="sandbox-exec: sandbox_apply: Operation not permitted",
        )
        with (
            patch("auto_developer.verifier.sys.platform", "darwin"),
            patch(
                "auto_developer.verifier.shutil.which",
                side_effect=lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
            ),
            patch("auto_developer.verifier.subprocess.run", side_effect=router),
        ):
            self.assertIsNone(self.verifier._get_sandbox_backend())
            result = self.verifier.verify_candidate_test(self.sample_test)

        self.assertEqual(len(captured), 1, "sandbox-exec probe must run exactly once and be cached")
        self.assertFalse(result.is_reproduced)
        self.assertEqual(result.reason, "SANDBOX_UNAVAILABLE")
        self.assertNotEqual(result.exit_code, 0)
        combined = f"{result.output}\n{result.reason_detail}"
        self.assertIn("sandbox-exec", combined)
        self.assertIn("macOS", combined)
        self.assertNotIn("bwrap", combined)

    def test_macos_sandbox_exec_working_returns_sandbox_exec_and_caches_probe(self) -> None:
        """macOS で sandbox-exec が実際に起動できる場合、最小コマンドで確認し 'sandbox-exec' を返しキャッシュすること。"""
        router, captured = _probe_router("sandbox-exec", returncode=0)
        with (
            patch("auto_developer.verifier.sys.platform", "darwin"),
            patch(
                "auto_developer.verifier.shutil.which",
                side_effect=lambda name: "/usr/bin/sandbox-exec" if name == "sandbox-exec" else None,
            ),
            patch("auto_developer.verifier.subprocess.run", side_effect=router),
        ):
            other_verifier = RedTestVerifier(repo_dir=self.repo_dir)
            self.assertEqual(self.verifier._get_sandbox_backend(), "sandbox-exec")
            self.assertEqual(other_verifier._get_sandbox_backend(), "sandbox-exec")

        self.assertEqual(len(captured), 1, "Probe result must be cached process-wide across instances")
        self.assertIn("sandbox-exec", captured[0][0])

    def test_real_macos_sandbox_probe_succeeds_and_caches(self) -> None:
        """実環境 (macOS) で実際の sandbox-exec 起動確認が成功し、2回目以降はキャッシュが使われること。"""
        require_platform_sandbox(
            self,
            expected_platform="darwin",
            backend_available=bool(shutil.which("sandbox-exec")),
            platform_skip_reason="macOS sandbox-exec is only available on macOS",
            unavailable_reason="macOS sandbox-exec is unavailable on macOS",
        )
        orig_run = subprocess.run
        probe_calls: list[list[str]] = []

        def _counting_run(*args: Any, **kwargs: Any) -> Any:
            cmd = args[0] if args else kwargs.get("args", [])
            if cmd and Path(str(cmd[0])).name == "sandbox-exec":
                probe_calls.append([str(c) for c in cmd])
            return orig_run(*args, **kwargs)

        with patch("auto_developer.verifier.subprocess.run", side_effect=_counting_run):
            self.assertEqual(self.verifier._get_sandbox_backend(), "sandbox-exec")
            other_verifier = RedTestVerifier(repo_dir=self.repo_dir)
            self.assertEqual(other_verifier._get_sandbox_backend(), "sandbox-exec")

        self.assertEqual(len(probe_calls), 1, "Real macOS sandbox probe must execute exactly once and cache")


if __name__ == "__main__":
    unittest.main()
