from __future__ import annotations

import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from collections.abc import Callable
from pathlib import Path
from unittest.mock import patch

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.cli import main


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}{os.pathsep}{existing_pp}" if existing_pp else SRC_DIR
    return env


def _run_cli_subprocess(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", *args],
        cwd=cwd,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


class TestDx6DemoCommand(unittest.TestCase):
    """DX-6: `auto-dev demo [--keep]` の単体・受入テスト。"""

    def test_demo_help_lists_keep_option(self) -> None:
        """`auto-dev demo --help` に `--keep` オプションが表示されること。"""
        stdout_buf = io.StringIO()
        with patch("sys.stdout", stdout_buf), self.assertRaises(SystemExit) as cm:
            main(["demo", "--help"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn("--keep", stdout_buf.getvalue())

    def test_demo_default_cleans_up_temp_dir_and_does_not_touch_cwd(self) -> None:
        """`auto-dev demo`（--keep なし）は一時ディレクトリで完走し、終了時に削除して CWD を汚さないこと。"""
        with tempfile.TemporaryDirectory() as caller_tmp:
            caller_dir = Path(caller_tmp)
            marker_file = caller_dir / "untouched.txt"
            marker_file.write_text("keep me\n", encoding="utf-8")

            proc = _run_cli_subprocess(caller_dir, "demo")
            self.assertEqual(
                proc.returncode,
                0,
                f"demo failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
            )

            out = proc.stdout
            # 各段階の解説と結果が出力されること
            self.assertIn("init", out.lower())
            self.assertIn("quarantine/", out)
            self.assertIn("MORNING BRIEF", out)
            self.assertIn("VERIFIED", out)

            # 一時ディレクトリのパスが出力され、終了後には削除されていること
            m = re.search(r"Demo workspace(?:\s+path)?:\s*(.+)", out)
            self.assertIsNotNone(m, f"Demo workspace path not reported in output:\n{out}")
            assert m is not None
            demo_path = Path(m.group(1).strip())
            self.assertFalse(
                demo_path.exists(),
                f"Temporary demo directory was not deleted without --keep: {demo_path}",
            )

            # 呼び出し元のカレントディレクトリが汚されていないこと
            self.assertEqual(
                sorted(p.name for p in caller_dir.iterdir()),
                ["untouched.txt"],
            )

    def test_demo_keep_preserves_workspace_with_full_artifacts(self) -> None:
        """`auto-dev demo --keep` は一時ディレクトリを残し、2タスク完了・保護ファイル隔離・brief・Bundle検証が揃っていること。"""
        with tempfile.TemporaryDirectory() as caller_tmp:
            caller_dir = Path(caller_tmp)
            kept_dir: Path | None = None
            try:
                proc = _run_cli_subprocess(caller_dir, "demo", "--keep")
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"demo --keep failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}",
                )
                out = proc.stdout

                m = re.search(r"Demo workspace kept at:\s*(.+)", out)
                self.assertIsNotNone(m, f"Kept workspace path missing from output:\n{out}")
                assert m is not None
                kept_dir = Path(m.group(1).strip())
                self.assertTrue(kept_dir.is_dir(), f"Kept directory does not exist: {kept_dir}")

                # 1. 本線はクリーンに復元されており、SPEC.md は改変されていないこと
                status = _git(kept_dir, "status", "--porcelain").stdout.strip()
                self.assertEqual(status, "", f"Kept demo repo working tree is not clean: {status}")

                # 2. PLAN.md はタスク1,2が [x] でタスク3は未完了のまま（本線が正常状態）であること
                plan_text = (kept_dir / "PLAN.md").read_text(encoding="utf-8")
                self.assertRegex(plan_text, r"- \[x\] 1\.")
                self.assertRegex(plan_text, r"- \[x\] 2\.")
                self.assertRegex(plan_text, r"- \[ \] 3\.")

                # 3. 隔離ブランチ quarantine/3-... が存在し、その上で **[FAILED]** と SPEC.md 改変が保全されていること
                branches = _git(kept_dir, "branch", "--list", "quarantine/*").stdout.strip().splitlines()
                q_branches = [b.lstrip("* ").strip() for b in branches if b.strip()]
                self.assertTrue(
                    any(b.startswith("quarantine/3-") for b in q_branches),
                    f"Expected quarantine/3-* branch, got: {q_branches}",
                )
                q_branch = next(b for b in q_branches if b.startswith("quarantine/3-"))
                q_plan = _git(kept_dir, "show", f"{q_branch}:PLAN.md").stdout
                self.assertIn("**[FAILED]**", q_plan)
                q_spec = _git(kept_dir, "show", f"{q_branch}:SPEC.md").stdout
                main_spec = (kept_dir / "SPEC.md").read_text(encoding="utf-8")
                self.assertNotEqual(q_spec, main_spec)

                # 4. 失敗ログ・job_state・レポートが存在すること
                self.assertTrue((kept_dir / "logs" / "nightly" / "task_3_failure.log").is_file())
                state_data = json.loads((kept_dir / ".dev" / "job_state.json").read_text(encoding="utf-8"))
                self.assertEqual(state_data["status"], "FAILED")
                self.assertTrue((kept_dir / "NIGHTLY_REPORT.md").is_file())

                # 5. Bundle が存在し、DX-7 のデモ用モデル名と明示系統のみ記録され、verify-bundle が通ること
                bundles = sorted((kept_dir / ".dev" / "bundles").glob("bundle_*.zip"))
                self.assertTrue(bundles, "Expected at least one bundle ZIP in .dev/bundles/")
                bundle_zip = bundles[-1]

                with zipfile.ZipFile(bundle_zip, "r") as zf:
                    manifest = json.loads(zf.read("bundle_manifest.json").decode("utf-8"))
                    self.assertEqual(manifest.get("format_version"), 3)
                    task_ids = [t.get("task_id") for t in manifest.get("tasks", [])]
                    self.assertEqual(task_ids, ["1", "2"])

                    for tid in ("1", "2"):
                        ev = json.loads(zf.read(f"evidence/task_{tid}.json").decode("utf-8"))
                        checker = ev.get("checker", {})
                        self.assertEqual(checker.get("model"), "auto-dev-demo-checker")
                        self.assertEqual(checker.get("family"), "demo-checker")
                        self.assertEqual(checker.get("family_source"), "explicit")
                        self.assertEqual(checker.get("maker_model"), "auto-dev-demo-maker")
                        self.assertEqual(checker.get("maker_family"), "demo-maker")
                        self.assertEqual(checker.get("maker_family_source"), "explicit")

                vb = _run_cli_subprocess(kept_dir, "--work-dir", str(kept_dir), "verify-bundle", str(bundle_zip))
                self.assertEqual(vb.returncode, 0, f"verify-bundle failed:\n{vb.stdout}\n{vb.stderr}")
                self.assertIn("VERIFIED", vb.stdout)

                # 実在ベンダーのモデル名がレポート・証跡に出力されないこと
                for vendor_token in ("gemini-", "claude-", "gpt-", "deepseek-", "qwen", "llama", "mistral"):
                    self.assertNotIn(vendor_token, out.lower())
            finally:
                if kept_dir is not None and kept_dir.exists():
                    shutil.rmtree(kept_dir, ignore_errors=True)

    def _run_demo_with_mocked_cli(
        self,
        cli_handler: Callable[..., tuple[int, str, str]],
        *,
        argv: list[str] | None = None,
    ) -> tuple[int, list[Path], str, str]:
        from auto_developer import demo as demo_mod

        created_dirs: list[Path] = []
        orig_mkdtemp = tempfile.mkdtemp

        def _track_mkdtemp(*args: object, **kwargs: object) -> str:
            path_str = orig_mkdtemp(*args, **kwargs)  # type: ignore[arg-type]
            prefix = str(kwargs.get("prefix", ""))
            if prefix.startswith("auto-dev-demo-") and not prefix.startswith("auto-dev-demo-agents-"):
                created_dirs.append(Path(path_str))
            return path_str

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with (
            patch("auto_developer.demo.tempfile.mkdtemp", side_effect=_track_mkdtemp),
            patch.object(demo_mod, "_run_demo_cli", side_effect=cli_handler),
            patch("sys.stdout", stdout_buf),
            patch("sys.stderr", stderr_buf),
        ):
            ret = main(argv or ["demo"])
        return ret, created_dirs, stdout_buf.getvalue(), stderr_buf.getvalue()

    def _assert_cleaned_up(self, created_dirs: list[Path]) -> None:
        self.assertTrue(created_dirs, "Expected demo to create a temporary workspace")
        for d in created_dirs:
            self.assertFalse(d.exists(), f"Failed demo run did not clean up temp dir: {d}")

    @staticmethod
    def _complete_initial_steps(repo_dir: Path, cmd: str, run_count: list[int]) -> tuple[int, str, str] | None:
        if cmd == "init":
            (repo_dir / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            return 0, "initialized", ""
        if cmd == "run" and run_count[0] == 0:
            run_count[0] += 1
            plan = (repo_dir / "PLAN.md").read_text(encoding="utf-8")
            plan = plan.replace("- [ ] 1.", "- [x] 1.").replace("- [ ] 2.", "- [x] 2.")
            (repo_dir / "PLAN.md").write_text(plan, encoding="utf-8")
            _git(repo_dir, "add", "PLAN.md")
            _git(repo_dir, "commit", "-m", "feat: complete tasks 1 and 2")
            return 0, "tasks 1-2 completed", ""
        return None

    def test_demo_returns_1_and_cleans_up_on_stage_failure(self) -> None:
        """期待どおりに進まない場合、終了コード 1 を返し、--keep なしなら一時ディレクトリを削除すること。"""
        before_cwd = Path.cwd()
        ret, created_dirs, _, _ = self._run_demo_with_mocked_cli(
            lambda _repo_dir, *_args: (1, "simulated failure", "")
        )
        self.assertEqual(ret, 1)
        self.assertEqual(Path.cwd(), before_cwd)
        self._assert_cleaned_up(created_dirs)

    def test_demo_keep_on_failure_preserves_dir_and_returns_1(self) -> None:
        """失敗時でも `--keep` があれば一時ディレクトリを残してパスを表示し、終了コード 1 を返すこと。"""
        created_dirs: list[Path] = []
        try:
            ret, created_dirs, stdout_text, _ = self._run_demo_with_mocked_cli(
                lambda _repo_dir, *_args: (1, "", "boom"),
                argv=["demo", "--keep"],
            )
            self.assertEqual(ret, 1)
            self.assertEqual(len(created_dirs), 1)
            self.assertTrue(created_dirs[0].is_dir())
            self.assertIn(f"Demo workspace kept at: {created_dirs[0]}", stdout_text)
        finally:
            for d in created_dirs:
                if d.exists():
                    shutil.rmtree(d, ignore_errors=True)

    def test_demo_returns_1_when_quarantine_stage_does_not_reject(self) -> None:
        """隔離段階で保護ファイル改変が検知されず rc=0 になった場合、終了コード 1 で停止し後始末すること。"""
        run_count = [0]

        def _handler(repo_dir: Path, *args: str) -> tuple[int, str, str]:
            cmd = args[0]
            handled = self._complete_initial_steps(repo_dir, cmd, run_count)
            if handled is not None:
                return handled
            if cmd == "run":
                return 0, "unexpected pass on task 3", ""
            return 0, "", ""

        ret, created_dirs, _, stderr_text = self._run_demo_with_mocked_cli(_handler)
        self.assertEqual(ret, 1)
        self.assertIn("fail with exit 1", stderr_text)
        self._assert_cleaned_up(created_dirs)

    def test_demo_returns_1_when_quarantine_branch_is_missing(self) -> None:
        """隔離段階で rc=1 でも quarantine/3-* ブランチが作られなかった場合、終了コード 1 で停止し後始末すること。"""
        run_count = [0]

        def _handler(repo_dir: Path, *args: str) -> tuple[int, str, str]:
            cmd = args[0]
            handled = self._complete_initial_steps(repo_dir, cmd, run_count)
            if handled is not None:
                return handled
            if cmd == "run":
                return 1, "failed without creating quarantine branch", ""
            return 0, "", ""

        ret, created_dirs, _, stderr_text = self._run_demo_with_mocked_cli(_handler)
        self.assertEqual(ret, 1)
        self.assertIn("quarantine/3-*", stderr_text)
        self._assert_cleaned_up(created_dirs)

    def test_demo_returns_1_when_verify_bundle_fails_or_not_verified(self) -> None:
        """verify-bundle が非0終了または VERIFIED を返さない場合、終了コード 1 で停止し後始末すること。"""
        for verify_rc, verify_out in (
            (1, "Status: INVALID"),
            (0, "Status: INTEGRITY_ONLY"),
        ):
            with self.subTest(verify_rc=verify_rc, verify_out=verify_out):
                run_count = [0]

                def _handler(
                    repo_dir: Path,
                    *args: str,
                    _run_count: list[int] = run_count,
                    _verify_rc: int = verify_rc,
                    _verify_out: str = verify_out,
                ) -> tuple[int, str, str]:
                    cmd = args[0]
                    handled = self._complete_initial_steps(repo_dir, cmd, _run_count)
                    if handled is not None:
                        return handled
                    if cmd == "run":
                        _git(repo_dir, "checkout", "-b", "quarantine/3-demo")
                        plan_p = repo_dir / "PLAN.md"
                        plan_p.write_text(
                            plan_p.read_text(encoding="utf-8") + " **[FAILED]**\n",
                            encoding="utf-8",
                        )
                        _git(repo_dir, "add", "PLAN.md")
                        _git(repo_dir, "commit", "-m", "quarantine")
                        _git(repo_dir, "checkout", "main")
                        return 1, "quarantined", ""
                    if cmd == "brief":
                        return 0, "MORNING BRIEF", ""
                    if cmd == "bundle":
                        bundles_dir = repo_dir / ".dev" / "bundles"
                        bundles_dir.mkdir(parents=True, exist_ok=True)
                        bundle_zip = bundles_dir / "bundle_demo.zip"
                        bundle_zip.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
                        return 0, f"BUNDLE CREATED: {bundle_zip}", ""
                    if cmd == "verify-bundle":
                        return _verify_rc, _verify_out, ""
                    return 0, "", ""

                ret, created_dirs, _, _ = self._run_demo_with_mocked_cli(_handler)
                self.assertEqual(ret, 1)
                self._assert_cleaned_up(created_dirs)


if __name__ == "__main__":
    unittest.main()
