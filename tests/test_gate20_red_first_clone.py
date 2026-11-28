"""GATE-20: 一時クローンでの Red-First 確認・REPO_MODIFIED・TRIVIAL_RED 不使用のテスト (Red-First)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.verifier import RedTestVerifier, verify_red_first_tests

REPO_ROOT = Path(__file__).resolve().parent.parent


def _clean_subprocess_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    src_path = str(REPO_ROOT / "src")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{existing_pp}" if existing_pp else src_path
    if extra:
        env.update(extra)
    return env


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo(
    repo: Path,
    *,
    plan_text: str,
    verify_script_text: str = "#!/usr/bin/env bash\nexit 0\n",
) -> str:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Tester")
    _git(repo, "config", "user.email", "tester@example.com")

    (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (repo / "PLAN.md").write_text(plan_text, encoding="utf-8")
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    verify_script = repo / "scripts" / "verify_local.sh"
    verify_script.write_text(verify_script_text, encoding="utf-8")
    verify_script.chmod(0o755)
    (repo / "src" / "app").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "app" / "__init__.py").write_text("", encoding="utf-8")
    (repo / "src" / "app" / "calc.py").write_text(
        "def run_action(cb=None):\n    return 0\n",
        encoding="utf-8",
    )
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "test_existing.py").write_text(
        "import unittest\n"
        "from app.calc import run_action\n\n"
        "class TestExisting(unittest.TestCase):\n"
        "    def test_ok(self):\n"
        "        self.assertEqual(run_action(), 0)\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: initial commit")
    return _git(repo, "rev-parse", "HEAD")


def _run_cli(
    repo: Path,
    *args: str,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo), *args],
        cwd=repo,
        env=_clean_subprocess_env(extra_env),
        capture_output=True,
        text=True,
        check=False,
    )


class TestGate20RedFirstClone(unittest.TestCase):
    """GATE-20 の一時クローン実行・判定・REPO_MODIFIED・記録を検証する。"""

    def test_captured_list_assertion_is_confirmed_not_trivial_red(self) -> None:
        """self.assertTrue(captured) のようにモックが後から中身を足すリストへのアサーションで失敗するテストが NOT_RED にならず CONFIRMED になること。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Invoke callback (Red-First)\n",
            )
            (repo / "src" / "app" / "calc.py").write_text(
                "def run_action(cb=None):\n"
                "    if cb is not None:\n"
                "        cb('called')\n"
                "    return 0\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_captured.py").write_text(
                "import unittest\n"
                "from app.calc import run_action\n\n"
                "class TestCaptured(unittest.TestCase):\n"
                "    def test_callback_appends_to_captured(self):\n"
                "        captured = []\n"
                "        run_action(cb=lambda x: captured.append(x))\n"
                "        self.assertTrue(captured)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_captured.py"])
            self.assertEqual(res["result"], "CONFIRMED")
            self.assertEqual(res["runner"], "clone")
            entry = res["file_results"][0]
            self.assertEqual(entry["result"], "CONFIRMED")
            self.assertEqual(entry["reason"], "GENUINE_RED")
            self.assertEqual(entry["current_result"], "PASSED")

    def test_verify_red_first_tests_does_not_use_sandbox_backend(self) -> None:
        """GATE-20: サンドボックスが使えない環境でも SANDBOX_UNAVAILABLE にならず一時クローンで実行されること。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Invoke callback (Red-First)\n",
            )
            (repo / "src" / "app" / "calc.py").write_text(
                "def run_action(cb=None):\n"
                "    if cb is not None:\n"
                "        cb('called')\n"
                "    return 0\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_captured.py").write_text(
                "import unittest\n"
                "from app.calc import run_action\n\n"
                "class TestCaptured(unittest.TestCase):\n"
                "    def test_callback_appends_to_captured(self):\n"
                "        captured = []\n"
                "        run_action(cb=lambda x: captured.append(x))\n"
                "        self.assertTrue(captured)\n",
                encoding="utf-8",
            )
            with patch.object(RedTestVerifier, "_get_sandbox_backend", return_value=None):
                res = verify_red_first_tests(repo, checkpoint, ["tests/test_captured.py"])
            self.assertEqual(res["result"], "CONFIRMED")
            self.assertEqual(res["runner"], "clone")

    def test_env_and_pythonpath_match_verify_script(self) -> None:
        """環境変数がサニタイズされず引き継がれ、PYTHONPATH の先頭にクローンの src と最上位が置かれること。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Env check (Red-First)\n",
            )
            (repo / "src" / "app" / "calc.py").write_text(
                "import os\n\n"
                "def run_action(cb=None):\n"
                "    return os.environ.get('CUSTOM_SECRET_TOKEN', '')\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_env.py").write_text(
                "import os\n"
                "import sys\n"
                "import unittest\n"
                "from pathlib import Path\n"
                "from app.calc import run_action\n\n"
                "class TestEnv(unittest.TestCase):\n"
                "    def test_env_and_sys_path(self):\n"
                "        self.assertEqual(run_action(), 'secret-val')\n"
                "        cwd = Path.cwd().resolve()\n"
                "        pp = os.environ.get('PYTHONPATH', '').split(os.pathsep)\n"
                "        self.assertEqual(Path(pp[0]).resolve(), (cwd / 'src').resolve())\n"
                "        self.assertEqual(Path(pp[1]).resolve(), cwd)\n",
                encoding="utf-8",
            )
            with patch.dict(os.environ, {"CUSTOM_SECRET_TOKEN": "secret-val"}):
                res = verify_red_first_tests(repo, checkpoint, ["tests/test_env.py"])
            self.assertEqual(res["result"], "CONFIRMED")

    def test_all_skipped_is_not_red_on_checkpoint_and_not_green_on_current(self) -> None:
        """すべてスキップのテストはチェックポイントで NOT_RED、Red 後の今のコードでは NOT_GREEN になること。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Skip check (Red-First)\n",
            )
            (repo / "tests" / "test_all_skip.py").write_text(
                "import unittest\n\n"
                "class TestSkip(unittest.TestCase):\n"
                "    @unittest.skip('always skipped')\n"
                "    def test_skip(self):\n"
                "        self.fail('should not run')\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_all_skip.py"])
            self.assertEqual(res["result"], "NOT_RED")
            self.assertEqual(res["file_results"][0]["result"], "NOT_RED")

            # チェックポイントで失敗し、今のコードですべてスキップの場合は NOT_GREEN
            (repo / "src" / "app" / "calc.py").write_text(
                "SKIP_IT = True\n\ndef run_action(cb=None):\n    return 0\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_cond_skip.py").write_text(
                "import unittest\n"
                "import app.calc as calc\n\n"
                "class TestCondSkip(unittest.TestCase):\n"
                "    def test_cond(self):\n"
                "        if getattr(calc, 'SKIP_IT', False):\n"
                "            self.skipTest('skipped on current')\n"
                "        self.fail('red on checkpoint')\n",
                encoding="utf-8",
            )
            res2 = verify_red_first_tests(repo, checkpoint, ["tests/test_cond_skip.py"])
            self.assertEqual(res2["result"], "NOT_GREEN")
            self.assertEqual(res2["file_results"][0]["result"], "NOT_GREEN")

    def test_repo_modified_detected_and_blocks_enforce(self) -> None:
        """元のリポジトリを変更するテストが REPO_MODIFIED になり、変わったパスが示され、enforce で失敗すること。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Modify repo (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=enforce\ntouch .ran_verify\nexit 0\n",
            )
            target_marker = repo / "tampered_by_test.txt"
            (repo / "tests" / "test_tamper.py").write_text(
                "import unittest\n"
                "from pathlib import Path\n\n"
                "class TestTamper(unittest.TestCase):\n"
                "    def test_write_parent(self):\n"
                f"        Path({str(target_marker)!r}).write_text('oops', encoding='utf-8')\n"
                "        self.fail('red')\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_tamper.py"])
            self.assertEqual(res["result"], "REPO_MODIFIED")
            entry = res["file_results"][0]
            self.assertEqual(entry["result"], "REPO_MODIFIED")
            self.assertIn("tampered_by_test.txt", entry["reason_detail"])

            # マーカーを消してから CLI の enforce でもブロックされることを確かめる
            target_marker.unlink()
            proc = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(proc.returncode, 1, proc.stderr)
            self.assertIn("RED_FIRST_NOT_CONFIRMED:", proc.stderr)
            self.assertIn("REPO_MODIFIED", proc.stderr)
            self.assertFalse((repo / ".ran_verify").exists())

    def test_runner_clone_recorded_in_evidence_events_and_red_first_json(self) -> None:
        """Evidence の red_first、イベント red_first、red_first.json に runner: clone が残ること。"""
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Invoke callback (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=enforce\nexit 0\n",
            )
            (repo / "src" / "app" / "calc.py").write_text(
                "def run_action(cb=None):\n"
                "    if cb is not None:\n"
                "        cb('called')\n"
                "    return 0\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_captured.py").write_text(
                "import unittest\n"
                "from app.calc import run_action\n\n"
                "class TestCaptured(unittest.TestCase):\n"
                "    def test_callback_appends_to_captured(self):\n"
                "        captured = []\n"
                "        run_action(cb=lambda x: captured.append(x))\n"
                "        self.assertTrue(captured)\n",
                encoding="utf-8",
            )
            proc = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(proc.returncode, 0, proc.stderr)

            ev = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertEqual(ev["red_first"]["runner"], "clone")

            events_text = (repo / ".git" / "nightly_evidence" / "events.jsonl").read_text(encoding="utf-8")
            rf_events = [
                json.loads(line)
                for line in events_text.splitlines()
                if line.strip() and json.loads(line).get("event") == "red_first"
            ]
            self.assertTrue(rf_events)
            self.assertEqual(rf_events[-1]["runner"], "clone")

            out_dir = repo / ".dev" / "review_out"
            rev = _run_cli(repo, "review-input", "--task-id", "1", "--out-dir", str(out_dir))
            self.assertEqual(rev.returncode, 0, rev.stderr)
            rf_json = json.loads((out_dir / "red_first.json").read_text(encoding="utf-8"))
            self.assertEqual(rf_json["runner"], "clone")


if __name__ == "__main__":
    unittest.main()
