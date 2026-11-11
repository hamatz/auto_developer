from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from auto_developer.models import Task


class RunnerAdapter(ABC):
    """AI エージェントセッション（Maker / Checker）の実行を抽象化するアダプター基底クラス。"""

    @abstractmethod
    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        """Maker セッションを起動してタスクを実装させ、(exit_code, output) を返す。"""

    @abstractmethod
    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        """Checker セッションを起動して独立査読を行わせ、(is_approved, explanation) を返す。"""


class MockRunnerAdapter(RunnerAdapter):
    """テストおよびドライラン用の決定論的 RunnerAdapter。"""

    def __init__(
        self,
        maker_exit_code: int = 0,
        maker_output: str = "Mock Maker executed successfully",
        checker_approve: bool = True,
        checker_reason: str = "Mock Checker approved",
    ) -> None:
        self.maker_exit_code = maker_exit_code
        self.maker_output = maker_output
        self.checker_approve = checker_approve
        self.checker_reason = checker_reason
        self.maker_calls: list[tuple[Task, dict[str, Any]]] = []
        self.checker_calls: list[tuple[Task, str, str]] = []

    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        self.maker_calls.append((task, context))
        return self.maker_exit_code, self.maker_output

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        self.checker_calls.append((task, diff, spec_content))
        return self.checker_approve, self.checker_reason


class CommandRunnerAdapter(RunnerAdapter):
    """外部CLIコマンドやスクリプトを呼び出してエージェントを実行するアダプター。"""

    def __init__(
        self,
        maker_cmd_template: str = "",
        checker_cmd_template: str = "",
        timeout_sec: int = 600,
    ) -> None:
        self.maker_cmd_template = maker_cmd_template
        self.checker_cmd_template = checker_cmd_template
        self.timeout_sec = timeout_sec

    def run_maker(self, task: Task, context: dict[str, Any]) -> tuple[int, str]:
        if not self.maker_cmd_template:
            return 1, "ERROR: No maker_cmd_template configured (Fail-Closed)"

        import json
        import os
        import tempfile

        lessons_text = str(context.get("lessons", "") or "")

        # 一時コンテキストファイルと教訓ファイルを作成
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp_path = Path(tmp_dir)
            context_file = tmp_path / f"context_task_{task.task_id}.json"
            lessons_file = tmp_path / f"lessons_task_{task.task_id}.txt"

            context_data = dict(context)
            context_data["task_id"] = str(task.task_id)
            context_data["task_title"] = task.title
            context_file.write_text(
                json.dumps(context_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            lessons_file.write_text(lessons_text, encoding="utf-8")

            cmd_str = (
                self.maker_cmd_template
                .replace("{task_id}", str(task.task_id))
                .replace("{task_title}", task.title)
                .replace("{attempt}", str(context.get("attempt", 1)))
                .replace("{prev_error}", str(context.get("prev_error", "")))
                .replace("{context_file}", str(context_file))
                .replace("{lessons_file}", str(lessons_file))
            )

            env = os.environ.copy()
            env["AUTO_DEV_LESSONS"] = lessons_text
            env["AUTO_DEV_TASK_ID"] = str(task.task_id)
            env["AUTO_DEV_TASK_TITLE"] = task.title
            env["AUTO_DEV_ATTEMPT"] = str(context.get("attempt", 1))
            env["AUTO_DEV_CONTEXT_FILE"] = str(context_file.resolve())
            env["AUTO_DEV_LESSONS_FILE"] = str(lessons_file.resolve())

            try:
                proc = subprocess.run(
                    ["bash", "-c", cmd_str],
                    input=lessons_text,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=self.timeout_sec,
                )
                return proc.returncode, proc.stdout + "\n" + proc.stderr
            except subprocess.TimeoutExpired:
                return 124, f"Maker execution timed out after {self.timeout_sec}s"
            except Exception as e:  # noqa: BLE001
                return 1, f"Failed to execute Maker: {e}"

    def run_checker(self, task: Task, diff: str, spec_content: str) -> tuple[bool, str]:
        if not self.checker_cmd_template:
            return False, "ERROR: No checker_cmd_template configured (Fail-Closed)"
        cmd_str = (
            self.checker_cmd_template
            .replace("{task_id}", str(task.task_id))
            .replace("{task_title}", task.title)
        )
        try:
            proc = subprocess.run(
                ["bash", "-c", cmd_str],
                input=f"--- DIFF ---\n{diff}\n--- SPEC ---\n{spec_content}",
                capture_output=True,
                text=True,
                check=False,
                timeout=self.timeout_sec,
            )
            output = proc.stdout + "\n" + proc.stderr
            if proc.returncode != 0:
                return False, f"Checker process failed with exit {proc.returncode}:\n{output}"

            # 1. 構造化 JSON パースを試行
            import json
            import re
            json_match = re.search(r"\{.*\}", output, re.DOTALL)
            if json_match:
                try:
                    data = json.loads(json_match.group(0))
                    if isinstance(data, dict) and "decision" in data:
                        dec = str(data["decision"]).strip().upper()
                        findings = data.get("findings", [])
                        findings_str = f" | Findings: {findings}" if findings else ""
                        return dec == "APPROVE", f"Structured JSON: {dec}{findings_str}"
                except json.JSONDecodeError:
                    pass

            # 2. テキスト判定 (厳格な否定語検知)
            up = output.upper()
            has_rejection = any(w in up for w in ("REJECT", "NOT APPROVED", "DISAPPROVED", "DECLINE"))
            is_approved = ("APPROVE" in up or "PASS" in up) and not has_rejection
            return is_approved, output.strip()
        except subprocess.TimeoutExpired:
            return False, f"Checker timed out after {self.timeout_sec}s"
        except Exception as e:  # noqa: BLE001
            return False, f"Failed to execute Checker: {e}"
