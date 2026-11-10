from __future__ import annotations

import sys
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parent.parent / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.models import Task
from auto_developer.runner import CommandRunnerAdapter


class TestRunnerLessonWiring(unittest.TestCase):
    def test_command_runner_delivers_lessons_via_stdin(self) -> None:
        """機能GGG.1: CommandRunnerAdapter が Maker の標準入力に lessons を流し込むこと。"""
        # Maker コマンド: stdin をそのまま読み込んで出力するスクリプト
        adapter = CommandRunnerAdapter(
            maker_cmd_template="python3 -c 'import sys; print(\"STDIN_READ:\" + sys.stdin.read().strip())'",
            timeout_sec=5,
        )
        task = Task(task_id="AUD-BUG-001", title="Fix zero division", line_number=1, is_completed=False, is_failed=False)
        critical_lesson = "CRITICAL_LESSON: Check denominator before division"

        code, out = adapter.run_maker(task, context={"attempt": 1, "lessons": critical_lesson})
        self.assertEqual(code, 0, f"Maker failed: {out}")
        self.assertIn(critical_lesson, out, f"Lessons were not delivered to Maker via stdin! Output:\n{out}")

    def test_command_runner_delivers_lessons_via_env_var(self) -> None:
        """機能GGG.1: CommandRunnerAdapter が環境変数 AUTO_DEV_LESSONS に教訓を設定すること。"""
        adapter = CommandRunnerAdapter(
            maker_cmd_template="python3 -c 'import os; print(\"ENV_LESSON:\" + os.environ.get(\"AUTO_DEV_LESSONS\", \"\"))'",
            timeout_sec=5,
        )
        task = Task(task_id="AUD-BUG-002", title="Handle null pointer", line_number=2, is_completed=False, is_failed=False)
        critical_lesson = "CRITICAL_LESSON: Always null check pointer"

        code, out = adapter.run_maker(task, context={"attempt": 1, "lessons": critical_lesson})
        self.assertEqual(code, 0, f"Maker failed: {out}")
        self.assertIn(critical_lesson, out, f"Lessons were not delivered via AUTO_DEV_LESSONS! Output:\n{out}")

    def test_command_runner_delivers_context_file_placeholder(self) -> None:
        """機能GGG.1: CommandRunnerAdapter が {context_file} プレースホルダーでコンテキストJSONを提供すること。"""
        adapter = CommandRunnerAdapter(
            maker_cmd_template="python3 -c 'import sys, json; data = json.load(open(\"{context_file}\")); print(\"FILE_LESSON:\" + data.get(\"lessons\", \"\"))'",
            timeout_sec=5,
        )
        task = Task(task_id="AUD-BUG-003", title="Refactor ratio", line_number=3, is_completed=False, is_failed=False)
        critical_lesson = "CRITICAL_LESSON: Safe ratio calculation"

        code, out = adapter.run_maker(task, context={"attempt": 1, "lessons": critical_lesson})
        self.assertEqual(code, 0, f"Maker failed: {out}")
        self.assertIn(critical_lesson, out, f"Lessons were not delivered via {{context_file}}! Output:\n{out}")

    def test_command_runner_delivers_context_and_lessons_env_vars(self) -> None:
        """機能HHH.2: CommandRunnerAdapter が AUTO_DEV_CONTEXT_FILE と AUTO_DEV_LESSONS_FILE を環境変数として正式公開すること。"""
        script = (
            "import os, sys, json\n"
            "ctx_path = os.environ.get('AUTO_DEV_CONTEXT_FILE', '')\n"
            "lessons_path = os.environ.get('AUTO_DEV_LESSONS_FILE', '')\n"
            "assert ctx_path and os.path.exists(ctx_path), f'Missing context file: {ctx_path}'\n"
            "assert lessons_path and os.path.exists(lessons_path), f'Missing lessons file: {lessons_path}'\n"
            "with open(ctx_path, encoding='utf-8') as f:\n"
            "    data = json.load(f)\n"
            "assert data.get('task_id') == 'AUD-BUG-004'\n"
            "with open(lessons_path, encoding='utf-8') as f:\n"
            "    text = f.read()\n"
            "assert 'CRITICAL_ENV_LESSON' in text\n"
            "print('ENV_FILES_VERIFIED')\n"
        )
        adapter = CommandRunnerAdapter(
            maker_cmd_template=f"python3 -c \"{script}\"",
            timeout_sec=5,
        )
        task = Task(task_id="AUD-BUG-004", title="Verify env file vars", line_number=4, is_completed=False, is_failed=False)
        code, out = adapter.run_maker(task, context={"attempt": 1, "lessons": "CRITICAL_ENV_LESSON: Check file env vars"})
        self.assertEqual(code, 0, f"Maker failed: {out}")
        self.assertIn("ENV_FILES_VERIFIED", out)


if __name__ == "__main__":
    unittest.main()
