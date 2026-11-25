"""DX-14: scripts/check_doc_commands.py の検証テスト。"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "check_doc_commands.py"


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    return env


def _run_checker(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(_SCRIPT), *args],
        cwd=_REPO_ROOT,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
    )


class TestDX14CheckDocCommands(unittest.TestCase):
    """DX-14: README とスキルの auto-dev コマンドと CLI 定義の整合性検査。"""

    def test_repo_docs_pass(self) -> None:
        self.assertTrue(_SCRIPT.is_file(), "scripts/check_doc_commands.py must exist")
        proc = _run_checker()
        self.assertEqual(
            proc.returncode,
            0,
            f"Expected exit 0 for repo docs, got {proc.returncode}:\n{proc.stdout}\n{proc.stderr}",
        )
        match = re.search(r"OK:\s*(\d+)\s+command\(s\)\s+verified", proc.stdout)
        self.assertIsNotNone(match, f"Expected verified count in stdout:\n{proc.stdout}")
        assert match is not None
        self.assertGreaterEqual(int(match.group(1)), 1)

    def test_unknown_subcommand_in_code_block_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "# Title\n\n```bash\nauto-dev init\nauto-dev no-such-subcommand --flag\n```\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("README.md:5:", combined)
            self.assertIn("no-such-subcommand", combined)

    def test_unknown_nested_subcommand_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            skill_dir = root / "skills" / "demo-skill"
            skill_dir.mkdir(parents=True)
            (root / "README.md").write_text("`auto-dev init`\n", encoding="utf-8")
            (skill_dir / "SKILL.md").write_text(
                "Run `auto-dev teams bogus-action --job-id 1`.\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("skills/demo-skill/SKILL.md:1:", combined)
            self.assertIn("bogus-action", combined)

    def test_unknown_option_in_inline_code_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "Line 1\nUse `auto-dev verify-step --checkpoint abc --nonexistent-opt 1` here.\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("README.md:2:", combined)
            self.assertIn("--nonexistent-opt", combined)

    def test_unknown_option_in_code_block_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "# Header\n\n```bash\nauto-dev init --unknown-in-block\n```\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("README.md:4:", combined)
            self.assertIn("--unknown-in-block", combined)

    def test_option_from_other_subcommand_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "`auto-dev verify-step --checkpoint abc --dry-run`\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("README.md:1:", combined)
            self.assertIn("--dry-run", combined)

    def test_global_option_after_subcommand_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "`auto-dev verify-step --checkpoint abc --work-dir /tmp`\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("README.md:1:", combined)
            self.assertIn("--work-dir", combined)

    def test_env_prefix_and_chained_commands_fail(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "```bash\n"
                "FOO=1 auto-dev bogus-env\n"
                "echo ok || auto-dev bogus-or\n"
                "echo ok; auto-dev bogus-semi\n"
                "echo ok | auto-dev bogus-pipe\n"
                "```\n"
                "Then run `cd repo && auto-dev verify-step --bogus`.\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("README.md:2:", combined)
            self.assertIn("bogus-env", combined)
            self.assertIn("README.md:3:", combined)
            self.assertIn("bogus-or", combined)
            self.assertIn("README.md:4:", combined)
            self.assertIn("bogus-semi", combined)
            self.assertIn("README.md:5:", combined)
            self.assertIn("bogus-pipe", combined)
            self.assertIn("README.md:7:", combined)
            self.assertIn("--bogus", combined)

    def test_tilde_fence_prompt_prefix_and_multiline_continuation_fail_at_start_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "# Demo\n\n"
                "~~~bash\n"
                "$ auto-dev bogus-prompt\n"
                "auto-dev run \\\n"
                "  --bogus-multiline\n"
                "~~~\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("README.md:4:", combined)
            self.assertIn("bogus-prompt", combined)
            self.assertIn("README.md:5:", combined)
            self.assertIn("--bogus-multiline", combined)
            self.assertNotIn("README.md:6:", combined)

    def test_quoted_delimiters_do_not_false_positive(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "```bash\n"
                'auto-dev run --maker-cmd "echo ok; auto-dev bogus"\n'
                "auto-dev run --maker-cmd 'echo ok && auto-dev bogus'\n"
                'auto-dev run --maker-cmd "echo ok || auto-dev bogus"\n'
                "auto-dev run --maker-cmd 'echo ok | auto-dev bogus'\n"
                "```\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(
                proc.returncode,
                0,
                f"Expected exit 0 for quoted delimiters:\n{proc.stdout}\n{proc.stderr}",
            )

    def test_empty_root_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            proc = _run_checker("--root", tmp)
            self.assertEqual(
                proc.returncode,
                1,
                f"Expected exit 1 when no target doc files exist:\n{proc.stdout}\n{proc.stderr}",
            )

    def test_valid_multiline_placeholders_and_comments_pass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text(
                "# Demo\n\n"
                "```bash\n"
                "auto-dev run \\\n"
                '  --maker-cmd "echo {task_id}" \\\n'
                "  --maker-model gemini-3.8-flash \\\n"
                '  --checker-cmd "echo" \\\n'
                "  --checker-model claude-opus-5-5\n"
                "auto-dev teams integrate --job-id <ジョブ ID>  # comment\n"
                "auto-dev teams run --maker-model ...\n"
                "```\n\n"
                "See `auto-dev <コマンド> --help` and `auto-dev --timeout 1200 verify-step ...`.\n",
                encoding="utf-8",
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(
                proc.returncode,
                0,
                f"Expected exit 0:\n{proc.stdout}\n{proc.stderr}",
            )

    def test_unlisted_subcommands_reported_without_failing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("```bash\nauto-dev init\n```\n", encoding="utf-8")
            (root / "README.en.md").write_text("```bash\nauto-dev demo\n```\n", encoding="utf-8")
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 0, f"Expected exit 0:\n{proc.stdout}\n{proc.stderr}")
            self.assertIn("across 2 file(s)", proc.stdout)
            ref_lines = [line for line in proc.stdout.splitlines() if line.startswith("Reference —")]
            self.assertEqual(len(ref_lines), 1, f"Expected one reference line:\n{proc.stdout}")
            ref_line = ref_lines[0]
            _, _, listed_part = ref_line.partition(": ")
            unlisted = {item.strip() for item in listed_part.split(",") if item.strip()}
            self.assertNotIn("init", unlisted)
            self.assertIn("demo", unlisted)
            self.assertIn("status", unlisted)

    def test_readme_en_invalid_command_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "README.md").write_text("`auto-dev init`\n", encoding="utf-8")
            (root / "README.en.md").write_text(
                "# English\n`auto-dev no-such-en-cmd`\n", encoding="utf-8"
            )
            proc = _run_checker("--root", str(root))
            self.assertEqual(proc.returncode, 1, f"Expected exit 1:\n{proc.stdout}\n{proc.stderr}")
            combined = proc.stdout + proc.stderr
            self.assertIn("README.en.md:2:", combined)
            self.assertIn("no-such-en-cmd", combined)


if __name__ == "__main__":
    unittest.main()
