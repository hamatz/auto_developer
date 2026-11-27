"""GATE-18: Red-First で今の作業ツリーのコードでの成功も確かめるテスト (Red-First)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.verifier import RedTestVerifier, RedVerificationResult, verify_red_first_tests

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


class TestGate18RedFirstCurrentGreen(unittest.TestCase):
    """GATE-18: チェックポイントと今の作業ツリーの両方で実行する Red-First 確認のテスト。"""

    def test_not_green_when_failing_on_both_checkpoint_and_current_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Broken test (Red-First)\n",
            )
            # 今の作業ツリーでも失敗する（add(1, 2) は 3 だが 999 を期待している）
            (repo / "tests" / "test_broken.py").write_text(
                "import unittest\n"
                "from app.calc import add\n\n"
                "class TestBroken(unittest.TestCase):\n"
                "    def test_broken(self):\n"
                "        self.assertEqual(add(1, 2), 999)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_broken.py"])
            self.assertEqual(res["result"], "NOT_GREEN")
            entry = res["file_results"][0]
            self.assertEqual(entry["result"], "NOT_GREEN")
            self.assertEqual(entry["reason"], "GENUINE_RED")
            self.assertEqual(entry["current_result"], "FAILED")
            self.assertEqual(entry["current_reason"], "GENUINE_RED")

    def test_confirmed_only_when_red_on_checkpoint_and_green_on_current_tree(self) -> None:
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
            entry = res["file_results"][0]
            self.assertEqual(entry["result"], "CONFIRMED")
            self.assertEqual(entry["reason"], "IMPORT_ERROR")
            self.assertEqual(entry["current_result"], "PASSED")
            self.assertEqual(entry["current_reason"], "ALREADY_GREEN")

    def test_not_red_skips_current_tree_execution(self) -> None:
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
                "    def test_ok(self):\n"
                "        self.assertEqual(add(1, 2), 3)\n",
                encoding="utf-8",
            )
            res = verify_red_first_tests(repo, checkpoint, ["tests/test_green.py"])
            self.assertEqual(res["result"], "NOT_RED")
            entry = res["file_results"][0]
            self.assertEqual(entry["result"], "NOT_RED")
            self.assertEqual(entry["current_result"], "SKIPPED")
            self.assertIn("current_reason", entry)

    def test_overall_priority_order(self) -> None:
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
            (repo / "tests" / "test_a_not_red.py").write_text(
                "import unittest\nfrom app.calc import add\n\n"
                "class TestA(unittest.TestCase):\n"
                "    def test_ok(self):\n        self.assertEqual(add(1, 2), 3)\n",
                encoding="utf-8",
            )
            (repo / "tests" / "test_b_not_green.py").write_text(
                "import unittest\nfrom app.calc import add\n\n"
                "class TestB(unittest.TestCase):\n"
                "    def test_fail(self):\n        self.assertEqual(add(1, 2), 999)\n",
                encoding="utf-8",
            )
            # NOT_GREEN > NOT_RED
            res1 = verify_red_first_tests(
                repo, checkpoint, ["tests/test_a_not_red.py", "tests/test_b_not_green.py"]
            )
            self.assertEqual(res1["result"], "NOT_GREEN")

            # CONFIRMED > NOT_GREEN
            (repo / "tests" / "test_c_confirmed.py").write_text(
                "import unittest\nfrom app.calc import sub\n\n"
                "class TestC(unittest.TestCase):\n"
                "    def test_sub(self):\n        self.assertEqual(sub(5, 2), 3)\n",
                encoding="utf-8",
            )
            res2 = verify_red_first_tests(
                repo,
                checkpoint,
                ["tests/test_b_not_green.py", "tests/test_c_confirmed.py"],
            )
            self.assertEqual(res2["result"], "CONFIRMED")

    def test_current_tree_timeout_and_sandbox_unavailable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
            )
            (repo / "tests" / "test_any.py").write_text(
                "import unittest\nfrom app.calc import add\n\n"
                "class TestAny(unittest.TestCase):\n"
                "    def test_any(self):\n        self.assertEqual(add(1, 2), 3)\n",
                encoding="utf-8",
            )
            cp_red = RedVerificationResult(
                is_reproduced=True,
                exit_code=1,
                reason="GENUINE_RED",
                output="",
                reason_detail="red at checkpoint",
            )
            cur_timeout = RedVerificationResult(
                is_reproduced=False,
                exit_code=124,
                reason="TIMEOUT",
                output="",
                reason_detail="timed out",
            )
            cur_sbx = RedVerificationResult(
                is_reproduced=False,
                exit_code=1,
                reason="SANDBOX_UNAVAILABLE",
                output="",
                reason_detail="no sandbox",
            )
            with patch.object(RedTestVerifier, "verify_red_first_test", side_effect=[cp_red, cur_timeout]):
                res_to = verify_red_first_tests(repo, checkpoint, ["tests/test_any.py"])
            self.assertEqual(res_to["result"], "TIMEOUT")
            self.assertEqual(res_to["file_results"][0]["result"], "TIMEOUT")
            self.assertEqual(res_to["file_results"][0]["current_result"], "TIMEOUT")
            self.assertEqual(res_to["file_results"][0]["current_reason"], "TIMEOUT")

            with patch.object(RedTestVerifier, "verify_red_first_test", side_effect=[cp_red, cur_sbx]):
                res_sbx = verify_red_first_tests(repo, checkpoint, ["tests/test_any.py"])
            self.assertEqual(res_sbx["result"], "SANDBOX_UNAVAILABLE")
            self.assertEqual(res_sbx["file_results"][0]["result"], "SANDBOX_UNAVAILABLE")
            self.assertEqual(res_sbx["file_results"][0]["current_result"], "SANDBOX_UNAVAILABLE")
            self.assertEqual(res_sbx["file_results"][0]["current_reason"], "SANDBOX_UNAVAILABLE")

    def test_verify_step_warn_and_enforce_for_not_green_and_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Broken test (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=warn\ntouch .ran_verify\nexit 0\n",
            )
            (repo / "tests" / "test_broken.py").write_text(
                "import unittest\n"
                "from app.calc import add\n\n"
                "class TestBroken(unittest.TestCase):\n"
                "    def test_broken(self):\n"
                "        self.assertEqual(add(1, 2), 999)\n",
                encoding="utf-8",
            )
            proc_warn = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(proc_warn.returncode, 0, proc_warn.stderr)
            self.assertIn("RED_FIRST_WARNING:", proc_warn.stderr)
            self.assertIn("NOT_GREEN", proc_warn.stderr)
            self.assertIn("GENUINE_RED", proc_warn.stderr)
            self.assertIn("FAILED", proc_warn.stderr)
            self.assertTrue((repo / ".ran_verify").exists())

            ev = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertEqual(ev["red_first"]["result"], "NOT_GREEN")
            self.assertEqual(ev["red_first"]["file_results"][0]["current_result"], "FAILED")
            self.assertEqual(ev["red_first"]["file_results"][0]["current_reason"], "GENUINE_RED")

            out_dir = repo / ".dev" / "review_out"
            rev = _run_cli(repo, "review-input", "--task-id", "1", "--out-dir", str(out_dir))
            self.assertEqual(rev.returncode, 0, rev.stderr)
            rf_json = json.loads((out_dir / "red_first.json").read_text(encoding="utf-8"))
            self.assertEqual(rf_json["result"], "NOT_GREEN")
            self.assertEqual(rf_json["file_results"][0]["current_result"], "FAILED")

            # enforce モードでは NOT_GREEN は失敗になる（別リポジトリで初期状態から enforce を設定）
        with tempfile.TemporaryDirectory() as tmp2:
            repo2 = Path(tmp2)
            checkpoint2 = _init_repo(
                repo2,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Broken test (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=enforce\ntouch .ran_verify\nexit 0\n",
            )
            (repo2 / "tests" / "test_broken.py").write_text(
                "import unittest\n"
                "from app.calc import add\n\n"
                "class TestBroken(unittest.TestCase):\n"
                "    def test_broken(self):\n"
                "        self.assertEqual(add(1, 2), 999)\n",
                encoding="utf-8",
            )
            proc_enf = _run_cli(repo2, "verify-step", "--checkpoint", checkpoint2, "--task-id", "1")
            self.assertEqual(proc_enf.returncode, 1, proc_enf.stderr)
            self.assertIn("RED_FIRST_NOT_CONFIRMED:", proc_enf.stderr)
            self.assertIn("NOT_GREEN", proc_enf.stderr)
            self.assertIn("GENUINE_RED", proc_enf.stderr)
            self.assertIn("FAILED", proc_enf.stderr)
            self.assertFalse((repo2 / ".ran_verify").exists())

    def test_nightly_run_skill_documents_not_green(self) -> None:
        skill_text = (REPO_ROOT / "skills" / "nightly-run" / "SKILL.md").read_text(encoding="utf-8")
        self.assertIn("NOT_GREEN", skill_text)


if __name__ == "__main__":
    unittest.main()
