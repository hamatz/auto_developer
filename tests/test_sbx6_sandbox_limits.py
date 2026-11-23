from __future__ import annotations

import os
import resource
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

import auto_developer.verifier as verifier_mod
from auto_developer.verifier import RedTestVerifier


def _reset_cache() -> None:
    reset_fn = getattr(verifier_mod, "_reset_sandbox_backend_cache", None)
    if callable(reset_fn):
        reset_fn()


def _popen_router(
    sandbox_binary: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> Any:
    """bwrap / sandbox-exec の起動だけをモック化し、git 等の実行はそのまま通す Popen ルーター。"""
    orig_popen = subprocess.Popen

    def _router(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args", [])
        if cmd and Path(str(cmd[0])).name == sandbox_binary:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.returncode = returncode
            mock_proc.communicate.return_value = (stdout, stderr)
            return mock_proc
        return orig_popen(*args, **kwargs)

    return _router


class TestSbx6SandboxLimitsAndClassification(unittest.TestCase):
    """SBX-6: スレッド単位 RLIMIT_NPROC、起動確認の資源上限連動・キャッシュ分離、開始目印による失敗分類を検証する。"""

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
        src_pkg = self.repo_dir / "src"
        src_pkg.mkdir(parents=True, exist_ok=True)
        (src_pkg / "app.py").write_text("def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo_dir, check=True, capture_output=True)
        self.verifier = RedTestVerifier(repo_dir=self.repo_dir)
        self.failing_test = (
            "import unittest\n"
            "from app import add\n\n"
            "class T(unittest.TestCase):\n"
            "    def test_repro(self):\n"
            "        self.assertEqual(add(1, 2), 99)\n"
        )

    def tearDown(self) -> None:
        _reset_cache()
        self.tmp_dir.cleanup()

    def test_linux_nproc_limit_counts_user_threads_not_processes(self) -> None:
        """Linux では同一 UID のスレッド総数を数えて余裕を加算し、プロセス数だけで決めないこと。"""
        fake_proc = Path(self.tmp_dir.name) / "fake_proc"
        uid = os.getuid()
        # UID 一致プロセス1: 120 スレッド
        p1 = fake_proc / "101"
        (p1 / "task").mkdir(parents=True)
        (p1 / "status").write_text(f"Name:\tworker1\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nThreads:\t120\n", encoding="utf-8")
        for tid in range(1000, 1120):
            (p1 / "task" / str(tid)).mkdir()
        # UID 一致プロセス2: 200 スレッド
        p2 = fake_proc / "102"
        (p2 / "task").mkdir(parents=True)
        (p2 / "status").write_text(f"Name:\tworker2\nUid:\t{uid}\t{uid}\t{uid}\t{uid}\nThreads:\t200\n", encoding="utf-8")
        for tid in range(2000, 2200):
            (p2 / "task" / str(tid)).mkdir()
        # 別 UID プロセス: 500 スレッド (除外されるべき)
        other_uid = uid + 1
        p3 = fake_proc / "103"
        (p3 / "task").mkdir(parents=True)
        (p3 / "status").write_text(
            f"Name:\tother\nUid:\t{other_uid}\t{other_uid}\t{other_uid}\t{other_uid}\nThreads:\t500\n",
            encoding="utf-8",
        )
        for tid in range(3000, 3500):
            (p3 / "task" / str(tid)).mkdir()

        with (
            patch("auto_developer.verifier.sys.platform", "linux"),
            patch("auto_developer.verifier._LINUX_PROC_DIR", fake_proc),
            patch("auto_developer.verifier.resource.getrlimit", return_value=(1024, 4096)),
        ):
            limits, status = self.verifier._compute_nproc_limit(margin=50)

        self.assertEqual(limits, (370, 4096))
        self.assertIn("applied: limit=370", status)
        self.assertIn("cur_count=320", status)

    def test_linux_nproc_limit_falls_back_to_ps_thread_flag(self) -> None:
        """Linux で /proc が使えない場合は ps -L -u <uid> -o lwp= でスレッド単位のフォールバックをすること。"""
        missing_proc = Path(self.tmp_dir.name) / "missing_proc"
        ps_calls: list[list[str]] = []

        def fake_ps(cmd: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
            ps_calls.append(cmd)
            lwp_lines = "\n".join(str(i) for i in range(1, 251)) + "\n"
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout=lwp_lines, stderr="")

        with (
            patch("auto_developer.verifier.sys.platform", "linux"),
            patch("auto_developer.verifier._LINUX_PROC_DIR", missing_proc),
            patch("auto_developer.verifier.resource.getrlimit", return_value=(1024, 4096)),
            patch("auto_developer.verifier.subprocess.run", side_effect=fake_ps),
        ):
            limits, status = self.verifier._compute_nproc_limit(margin=50)

        self.assertEqual(len(ps_calls), 1)
        self.assertIn("-L", ps_calls[0])
        self.assertIn("lwp=", ps_calls[0])
        self.assertEqual(limits, (300, 4096))
        self.assertIn("applied: limit=300", status)

    def test_probe_with_explicit_none_does_not_recompute_or_apply_nproc_limit_and_uses_timeout_and_cache_isolation(
        self,
    ) -> None:
        """境界値Red 3: nproc_limits=None のとき起動確認で _compute_nproc_limit を再計算せず RLIMIT_NPROC もかけない。timeout_sec 連動・キャッシュ分離も満たす。"""
        probe_calls: list[dict[str, Any]] = []
        rlimit_calls: list[tuple[int, tuple[int, int]]] = []

        def fake_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
            cmd = args[0] if args else kwargs.get("args", [])
            preexec_fn = kwargs.get("preexec_fn")
            probe_timeout = kwargs.get("timeout")
            probe_calls.append({"cmd": list(cmd), "timeout": probe_timeout, "preexec_fn": preexec_fn})
            if callable(preexec_fn):
                preexec_fn()
            # 最初の組 (timeout=12, nproc=None) は失敗、別の組は成功などを返す
            if probe_timeout == 12 and len(probe_calls) == 1:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="bwrap: probe error A")
            if probe_timeout == 25:
                return subprocess.CompletedProcess(args=cmd, returncode=1, stdout="", stderr="bwrap: probe error B")
            return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

        def recording_setrlimit(kind: int, limits: tuple[int, int]) -> None:
            rlimit_calls.append((kind, limits))

        with (
            patch("auto_developer.verifier.sys.platform", "linux"),
            patch(
                "auto_developer.verifier.shutil.which",
                side_effect=lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
            ),
            patch("auto_developer.verifier.subprocess.run", side_effect=fake_run),
            patch("auto_developer.verifier.resource.setrlimit", side_effect=recording_setrlimit),
            patch.object(self.verifier, "_compute_nproc_limit", side_effect=AssertionError("must not recompute")) as mock_compute,
        ):
            # 1) nproc_limits=None, timeout_sec=12: _compute_nproc_limit は呼ばれず、RLIMIT_NPROC も設定されない
            backend_a, detail_a = self.verifier._probe_sandbox_backend(timeout_sec=12, nproc_limits=None)
            self.assertIsNone(backend_a)
            self.assertIn("probe error A", detail_a)
            mock_compute.assert_not_called()
            self.assertEqual(probe_calls[0]["timeout"], 12)
            self.assertTrue(callable(probe_calls[0]["preexec_fn"]))
            self.assertFalse(any(k == resource.RLIMIT_NPROC for k, _ in rlimit_calls))
            self.assertIn((resource.RLIMIT_CPU, (17, 17)), rlimit_calls)

            # 2) キャッシュ分離: 同じ組 (12, None) ならキャッシュされた案内 A を返すが、別の組では使い回さない
            self.assertIn("probe error A", self.verifier._get_sandbox_unavailable_detail(timeout_sec=12, nproc_limits=None))
            detail_other_limits = self.verifier._get_sandbox_unavailable_detail(
                timeout_sec=12, nproc_limits=(300, 4096)
            )
            self.assertNotIn("probe error A", detail_other_limits)

            # 3) 別の値の組 (12, (300, 4096)) はキャッシュせず再実行し、RLIMIT_NPROC を設定する
            rlimit_calls.clear()
            backend_c = self.verifier._get_sandbox_backend(timeout_sec=12, nproc_limits=(300, 4096))
            self.assertEqual(backend_c, "bwrap")
            self.assertEqual(len(probe_calls), 2)
            self.assertIn((resource.RLIMIT_NPROC, (300, 4096)), rlimit_calls)

            # 4) timeout_sec が違う組 (25, None) も別キャッシュになり、それぞれの失敗案内を保持する
            rlimit_calls.clear()
            backend_b = self.verifier._get_sandbox_backend(timeout_sec=25, nproc_limits=None)
            self.assertIsNone(backend_b)
            self.assertEqual(len(probe_calls), 3)
            self.assertEqual(probe_calls[2]["timeout"], 25)
            self.assertIn((resource.RLIMIT_CPU, (30, 30)), rlimit_calls)
            self.assertFalse(any(k == resource.RLIMIT_NPROC for k, _ in rlimit_calls))
            self.assertIn("probe error B", self.verifier._get_sandbox_unavailable_detail(timeout_sec=25, nproc_limits=None))
            self.assertNotIn("probe error A", self.verifier._get_sandbox_unavailable_detail(timeout_sec=25, nproc_limits=None))

    def test_execute_test_passes_timeout_and_none_nproc_to_probe_without_recomputing(self) -> None:
        """_execute_test で _compute_nproc_limit が None を返したとき、起動確認も nproc_limits=None のまま行われること。"""
        probe_args: list[tuple[int, tuple[int, int] | None]] = []

        def fake_probe(
            timeout_sec: int = 30,
            nproc_limits: tuple[int, int] | None | object = None,
        ) -> tuple[str | None, str]:
            probe_args.append((timeout_sec, nproc_limits))  # type: ignore[arg-type]
            return None, "SANDBOX_UNAVAILABLE: simulated"

        with (
            patch.object(self.verifier, "_compute_nproc_limit", return_value=(None, "ps_failed(code=1)")),
            patch.object(self.verifier, "_probe_sandbox_backend", side_effect=fake_probe),
        ):
            res = self.verifier.verify_candidate_test(self.failing_test, timeout_sec=19)

        self.assertEqual(res.reason, "SANDBOX_UNAVAILABLE")
        self.assertEqual(probe_args, [(19, None)])

    def test_missing_started_marker_without_bwrap_prefix_is_sandbox_unavailable(self) -> None:
        """境界値Red 1: 開始の目印がなく、'bwrap:' も出ない失敗（シグナル終了など）が SANDBOX_UNAVAILABLE になること。"""
        router = _popen_router("bwrap", returncode=-11, stdout="", stderr="Segmentation fault (core dumped)")

        with (
            patch("auto_developer.verifier.sys.platform", "linux"),
            patch.object(self.verifier, "_get_sandbox_backend", return_value="bwrap"),
            patch("subprocess.Popen", side_effect=router),
        ):
            res = self.verifier.verify_candidate_test(self.failing_test)

        self.assertFalse(res.is_reproduced)
        self.assertEqual(res.reason, "SANDBOX_UNAVAILABLE")
        self.assertEqual(res.exit_code, -11)
        self.assertIn("Segmentation fault", res.output)
        self.assertIn("AppArmor", f"{res.output}\n{res.reason_detail}")

    def test_missing_started_marker_with_exit_0_or_5_is_still_sandbox_unavailable(self) -> None:
        """開始の目印がない場合は終了コード 0 や 5、'NO TESTS RAN' 文字列であっても SANDBOX_UNAVAILABLE になること。"""
        for code, stdout in [(0, ""), (5, "NO TESTS RAN\n")]:
            router = _popen_router(
                "bwrap",
                returncode=code,
                stdout=stdout,
                stderr="bwrap: Creating new namespace failed: Resource temporarily unavailable",
            )

            with (
                patch("auto_developer.verifier.sys.platform", "linux"),
                patch.object(self.verifier, "_get_sandbox_backend", return_value="bwrap"),
                patch("subprocess.Popen", side_effect=router),
            ):
                res = self.verifier.verify_candidate_test(self.failing_test)

            self.assertFalse(res.is_reproduced)
            self.assertEqual(res.reason, "SANDBOX_UNAVAILABLE")
            self.assertIn("Creating new namespace failed", res.output)
            self.assertIn("bwrap-userns-restrict", f"{res.output}\n{res.reason_detail}")

    def test_started_marker_present_with_bwrap_in_output_is_not_misclassified_as_sandbox_unavailable(self) -> None:
        """境界値Red 2: 開始の目印があり、テスト自身が 'bwrap:' や 'sandbox-exec:' を出力して失敗した場合は通常のテスト失敗（GENUINE_RED）になること。"""
        payload = (
            "===AUTO_DEV_RUNNER_STARTED===\n"
            "bwrap: simulated message printed by test\n"
            "===AUTO_DEV_TEST_RESULT===\n"
            '{"tests_run": 1, "failures": [{"test": "test_repro", "err": "AssertionError: 3 != 99"}], '
            '"errors": [], "executed_product_code": true}'
        )
        router = _popen_router(
            "bwrap",
            returncode=1,
            stdout=payload,
            stderr="bwrap: simulated stderr from test\nsandbox-exec: simulated",
        )

        with (
            patch("auto_developer.verifier.sys.platform", "linux"),
            patch.object(self.verifier, "_get_sandbox_backend", return_value="bwrap"),
            patch("subprocess.Popen", side_effect=router),
        ):
            res = self.verifier.verify_candidate_test(self.failing_test)

        self.assertTrue(res.is_reproduced)
        self.assertEqual(res.reason, "GENUINE_RED")

    def test_started_marker_present_preserves_fallback_no_tests_ran_and_exit_5(self) -> None:
        """開始の目印がある場合、結果マーカー無しのフォールバック（exit code 5 / NO TESTS RAN）が既存通り NO_TESTS_RAN になること。"""
        router = _popen_router("sandbox-exec", returncode=5, stdout="===AUTO_DEV_RUNNER_STARTED===\n", stderr="")

        with (
            patch.object(self.verifier, "_get_sandbox_backend", return_value="sandbox-exec"),
            patch("subprocess.Popen", side_effect=router),
        ):
            res = self.verifier.verify_candidate_test(self.failing_test)

        self.assertFalse(res.is_reproduced)
        self.assertEqual(res.reason, "NO_TESTS_RAN")

    def test_runner_code_emits_started_marker_before_test_loading(self) -> None:
        """RUNNER_CODE はテスト読み込みより前に ===AUTO_DEV_RUNNER_STARTED=== を出力すること。"""
        code_text = RedTestVerifier.RUNNER_CODE
        started_idx = code_text.index("===AUTO_DEV_RUNNER_STARTED===")
        discover_idx = code_text.index("loader.discover")
        self.assertLess(started_idx, discover_idx)


if __name__ == "__main__":
    unittest.main()
