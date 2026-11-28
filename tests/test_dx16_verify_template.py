"""DX-16: auto-dev init が生成する scripts/verify_local.sh 雛形の受け入れテスト。"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import DEFAULT_VERIFY_SH, main
from auto_developer.verifier import resolve_red_first_mode, resolve_verify_timeout


def _clean_env() -> dict[str, str]:
    env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")
    }
    env["PYTHON"] = sys.executable
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


class TestDx16VerifyTemplate(unittest.TestCase):
    """DX-16: 検証スクリプト雛形の設定行とプロジェクト固有の規則セクション。"""

    def test_first_20_lines_contain_default_directives_with_comments(self) -> None:
        head_lines = DEFAULT_VERIFY_SH.splitlines()[:20]
        head_text = "\n".join(head_lines)

        self.assertIn("# auto-dev: verify-timeout=300", head_lines)
        self.assertIn("# auto-dev: red-first=warn", head_lines)
        self.assertIn("300", head_text)
        self.assertIn("warn", head_text)

        # 注釈が各設定行の近くにあること（shebang・set -euo pipefail・2つの設定行以外に注釈行がある）
        comment_lines = [
            line for line in head_lines if line.strip().startswith("#") and not line.startswith("#!")
        ]
        self.assertGreaterEqual(len(comment_lines), 4)

    def test_verifier_resolves_template_directives(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            rc = main(["--work-dir", str(repo), "init"])
            self.assertEqual(rc, 0)
            verify_sh = repo / "scripts" / "verify_local.sh"
            self.assertTrue(verify_sh.exists())

            timeout_sec, timeout_src = resolve_verify_timeout(
                cli_timeout=None, script_path=verify_sh
            )
            self.assertEqual(timeout_sec, 300)
            self.assertIn("verify_local.sh", timeout_src)

            rf_mode = resolve_red_first_mode(script_path=verify_sh)
            self.assertEqual(rf_mode, "warn")

    def test_section_numbering_and_project_rules_examples(self) -> None:
        self.assertNotIn("[1/2]", DEFAULT_VERIFY_SH)
        self.assertNotIn("[2/2]", DEFAULT_VERIFY_SH)

        idx1 = DEFAULT_VERIFY_SH.index("[1/3]")
        idx2 = DEFAULT_VERIFY_SH.index("[2/3]")
        idx3 = DEFAULT_VERIFY_SH.index("[3/3]")
        self.assertLess(idx1, idx2)
        self.assertLess(idx2, idx3)

        rules_section = DEFAULT_VERIFY_SH[idx2:idx3]
        # 例1: 使ってはいけない書き方（eval(）
        self.assertIn("eval(", rules_section)
        self.assertIn("src/", rules_section)
        # 例2: 層をまたぐ読み込み（domain -> ui）
        self.assertIn("domain", rules_section)
        self.assertIn("ui", rules_section)

    def test_init_does_not_overwrite_existing_verify_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            verify_sh = repo / "scripts" / "verify_local.sh"
            verify_sh.parent.mkdir(parents=True, exist_ok=True)
            custom = "#!/usr/bin/env bash\n# custom\nexit 0\n"
            verify_sh.write_text(custom, encoding="utf-8")

            rc = main(["--work-dir", str(repo), "init"])
            self.assertEqual(rc, 0)
            self.assertEqual(verify_sh.read_text(encoding="utf-8"), custom)

    def test_template_bash_syntax_and_sample_project_execution(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.name", "DX16 Test")
            _git(repo, "config", "user.email", "dx16@example.com")

            rc = main(["--work-dir", str(repo), "init"])
            self.assertEqual(rc, 0)

            verify_sh = repo / "scripts" / "verify_local.sh"
            syntax_res = subprocess.run(
                ["bash", "-n", str(verify_sh)],
                cwd=repo,
                env=_clean_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                syntax_res.returncode,
                0,
                f"bash -n failed: {syntax_res.stdout}\n{syntax_res.stderr}",
            )

            # 見本プロジェクト（テスト1件 + 例に該当するコードがあっても既定では失敗しないこと）
            (repo / "tests").mkdir(exist_ok=True)
            (repo / "tests" / "test_sample.py").write_text(
                "import unittest\n\n\nclass SampleTest(unittest.TestCase):\n    def test_ok(self) -> None:\n        self.assertEqual(1 + 1, 2)\n",
                encoding="utf-8",
            )
            (repo / "src" / "domain").mkdir(parents=True, exist_ok=True)
            (repo / "src" / "domain" / "bad.py").write_text(
                "from ui import screen\n\ndef f():\n    return eval('1')\n",
                encoding="utf-8",
            )
            _git(repo, "add", "-A")
            _git(repo, "commit", "-m", "init sample project")

            run_res = subprocess.run(
                ["bash", str(verify_sh)],
                cwd=repo,
                env=_clean_env(),
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(
                run_res.returncode,
                0,
                f"verify_local.sh failed:\nstdout={run_res.stdout}\nstderr={run_res.stderr}",
            )
            self.assertIn("[1/3]", run_res.stdout)
            self.assertIn("[2/3]", run_res.stdout)
            self.assertIn("[3/3]", run_res.stdout)


if __name__ == "__main__":
    unittest.main()
