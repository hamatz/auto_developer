"""GATE-17: Red-First のサンドボックス実行・判定・warn/enforce・red_first.json のテスト (Red-First)."""

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


def _clean_subprocess_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    src_path = str(REPO_ROOT / "src")
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_path}{os.pathsep}{existing_pp}" if existing_pp else src_path
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
        "def add(a: int, b: int) -> int:\n    return a + b\n", encoding="utf-8"
    )
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "test_existing.py").write_text(
        "import unittest\n"
        "from app.calc import add\n\n"
        "class TestExisting(unittest.TestCase):\n"
        "    def test_add(self):\n"
        "        self.assertEqual(add(1, 2), 3)\n",
        encoding="utf-8",
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: initial commit")
    return _git(repo, "rev-parse", "HEAD")


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo), *args],
        cwd=repo,
        env=_clean_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


class TestGate17RedFirstExec(unittest.TestCase):
    """GATE-17 のサンドボックス実行・結果分類・warn/enforce・review-input を検証する。"""

    def test_confirmed_by_assertion_failure_against_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Add multiply (Red-First)\n",
            )
            # Working tree has new function, but checkpoint does not
            (repo / "src" / "app" / "calc.py").write_text(
                "def add(a: int, b: int) -> int:\n    return a - b\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_feature.py").write_text(
                "import unittest\n"
                "from pathlib import Path\n"
                "from app.calc import add\n\n"
                "REPO_ROOT = Path(__file__).resolve().parent.parent\n\n"
                "class TestFeature(unittest.TestCase):\n"
                "    def test_add_returns_diff_in_new_spec(self):\n"
                "        self.assertEqual(add(5, 2), 3)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_feature.py"])
            self.assertEqual(res["result"], "CONFIRMED")
            self.assertEqual(res["file_results"][0]["file"], "tests/test_feature.py")
            self.assertEqual(res["file_results"][0]["result"], "CONFIRMED")

    def test_confirmed_by_import_error_of_new_function(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Add sub (Red-First)\n",
            )
            (repo / "src" / "app" / "calc.py").write_text(
                "def add(a: int, b: int) -> int:\n    return a + b\n\n"
                "def sub(a: int, b: int) -> int:\n    return a - b\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_sub.py").write_text(
                "import unittest\n"
                "from app.calc import sub\n\n"
                "class TestSub(unittest.TestCase):\n"
                "    def test_sub(self):\n"
                "        self.assertEqual(sub(5, 2), 3)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_sub.py"])
            self.assertEqual(res["result"], "CONFIRMED")
            self.assertEqual(res["file_results"][0]["result"], "CONFIRMED")
            self.assertEqual(res["file_results"][0]["reason"], "IMPORT_ERROR")

    def test_not_red_when_all_tests_pass_on_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
            )
            (repo / "tests" / "test_green.py").write_text(
                "import unittest\n"
                "from app.calc import add\n\n"
                "class TestGreen(unittest.TestCase):\n"
                "    def test_already_ok(self):\n"
                "        self.assertEqual(add(2, 3), 5)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_green.py"])
            self.assertEqual(res["result"], "NOT_RED")
            self.assertEqual(res["file_results"][0]["result"], "NOT_RED")

    def test_not_red_for_syntax_error_and_trivial_red(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
            )
            (repo / "tests" / "test_syntax.py").write_text(
                "def test_broken(:\n    pass\n", encoding="utf-8"
            )
            (repo / "tests" / "test_trivial.py").write_text(
                "import unittest\n\n"
                "class TestTrivial(unittest.TestCase):\n"
                "    def test_false(self):\n"
                "        self.assertTrue(False)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(
                repo, checkpoint, ["tests/test_syntax.py", "tests/test_trivial.py"]
            )
            self.assertEqual(res["result"], "NOT_RED")
            self.assertEqual(res["file_results"][0]["result"], "NOT_RED")
            self.assertEqual(res["file_results"][0]["reason"], "SYNTAX_ERROR")
            self.assertEqual(res["file_results"][1]["result"], "NOT_RED")
            self.assertEqual(res["file_results"][1]["reason"], "TRIVIAL_RED")

    def test_confirmed_if_any_file_is_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
            )
            (repo / "src" / "app" / "calc.py").write_text(
                "def add(a: int, b: int) -> int:\n    return a + b\n\n"
                "def sub(a: int, b: int) -> int:\n    return a - b\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_a_green.py").write_text(
                "import unittest\n"
                "from app.calc import add\n\n"
                "class TestA(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertEqual(add(1, 1), 2)\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_b_red.py").write_text(
                "import unittest\n"
                "from app.calc import sub\n\n"
                "class TestB(unittest.TestCase):\n"
                "    def test_sub(self):\n"
                "        self.assertEqual(sub(5, 2), 3)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(
                repo, checkpoint, ["tests/test_a_green.py", "tests/test_b_red.py"]
            )
            self.assertEqual(res["result"], "CONFIRMED")

    def test_timeout_is_not_counted_as_confirmed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
            )
            (repo / "tests" / "test_slow.py").write_text(
                "import time\nimport unittest\n\n"
                "class TestSlow(unittest.TestCase):\n"
                "    def test_sleep(self):\n"
                "        time.sleep(5)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_slow.py"], timeout_sec=1)
            self.assertEqual(res["result"], "TIMEOUT")
            self.assertEqual(res["file_results"][0]["result"], "TIMEOUT")

    def test_sandbox_unavailable_returns_sandbox_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
            )
            (repo / "tests" / "test_any.py").write_text(
                "import unittest\n\n"
                "class TestAny(unittest.TestCase):\n"
                "    def test_any(self):\n"
                "        self.assertTrue(True)\n",
                encoding="utf-8",
            )
            with patch.object(RedTestVerifier, "_get_sandbox_backend", return_value=None):
                res = verify_red_first_tests(repo, checkpoint, ["tests/test_any.py"])
            self.assertEqual(res["result"], "SANDBOX_UNAVAILABLE")
            self.assertEqual(res["file_results"][0]["result"], "SANDBOX_UNAVAILABLE")

    def test_verify_step_warn_mode_warns_and_continues(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=warn\ntouch .ran_verify\nexit 0\n",
            )
            (repo / "tests" / "test_green.py").write_text(
                "import unittest\n"
                "from app.calc import add\n\n"
                "class TestGreen(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertEqual(add(1, 2), 3)\n",
                encoding="utf-8",
            )
            proc = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertIn("RED_FIRST_WARNING:", proc.stderr)
            self.assertIn("NOT_RED", proc.stderr)
            self.assertIn("tests/test_green.py", proc.stderr)
            self.assertTrue((repo / ".ran_verify").exists())
            ev = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertEqual(ev["red_first"]["result"], "NOT_RED")

    def test_verify_step_enforce_mode_blocks_non_confirmed_and_counts_inv4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            plan_text = "# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n"
            checkpoint = _init_repo(
                repo,
                plan_text=plan_text,
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=enforce\ntouch .ran_verify\nexit 0\n",
            )
            # 1st attempt: NO_TEST_CHANGES -> blocked with exit 1
            proc1 = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(proc1.returncode, 1, proc1.stderr)
            self.assertIn("RED_FIRST_NOT_CONFIRMED:", proc1.stderr)
            self.assertFalse((repo / ".ran_verify").exists())
            self.assertFalse((repo / ".git" / "nightly_evidence" / "task_1.json").exists())
            self.assertEqual((repo / "PLAN.md").read_text(encoding="utf-8"), plan_text)

            # 2nd attempt: NOT_RED -> blocked with exit 1
            (repo / "tests" / "test_green.py").write_text(
                "import unittest\n"
                "from app.calc import add\n\n"
                "class TestGreen(unittest.TestCase):\n"
                "    def test_ok(self):\n"
                "        self.assertEqual(add(1, 2), 3)\n",
                encoding="utf-8",
            )
            proc2 = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(proc2.returncode, 1, proc2.stderr)
            self.assertIn("RED_FIRST_NOT_CONFIRMED:", proc2.stderr)
            self.assertFalse((repo / ".ran_verify").exists())

            # 3rd attempt: INV-4 fail-fast must trigger
            proc3 = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(proc3.returncode, 1, proc3.stderr)
            self.assertIn("INV4_FAIL_FAST:", proc3.stderr)

    def test_verify_step_enforce_mode_passes_when_confirmed_and_review_input_writes_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Add sub (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=enforce\nexit 0\n",
            )
            (repo / "src" / "app" / "calc.py").write_text(
                "def add(a: int, b: int) -> int:\n    return a + b\n\n"
                "def sub(a: int, b: int) -> int:\n    return a - b\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_sub.py").write_text(
                "import unittest\n"
                "from app.calc import sub\n\n"
                "class TestSub(unittest.TestCase):\n"
                "    def test_sub(self):\n"
                "        self.assertEqual(sub(5, 2), 3)\n",
                encoding="utf-8",
            )
            proc = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ev = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertEqual(ev["red_first"]["result"], "CONFIRMED")
            self.assertEqual(ev["red_first"]["file_results"][0]["file"], "tests/test_sub.py")
            self.assertEqual(ev["red_first"]["file_results"][0]["result"], "CONFIRMED")

            out_dir = repo / ".dev" / "review_out"
            rev_proc = _run_cli(repo, "review-input", "--task-id", "1", "--out-dir", str(out_dir))
            self.assertEqual(rev_proc.returncode, 0, rev_proc.stderr)
            rf_json_path = out_dir / "red_first.json"
            self.assertTrue(rf_json_path.exists())
            rf_data = json.loads(rf_json_path.read_text(encoding="utf-8"))
            self.assertEqual(rf_data["mode"], "enforce")
            self.assertEqual(rf_data["result"], "CONFIRMED")
            self.assertEqual(rf_data["file_results"][0]["file"], "tests/test_sub.py")

    def test_nightly_run_skill_mentions_red_first_json(self) -> None:
        skill_text = (REPO_ROOT / "skills" / "nightly-run" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("red_first.json", skill_text)
        self.assertIn("NOT_RED", skill_text)


if __name__ == "__main__":
    unittest.main()
