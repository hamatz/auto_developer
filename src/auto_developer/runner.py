from __future__ import annotations

import subprocess
from abc import ABC, abstractmethod
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
        cmd_str = self.maker_cmd_template.format(
            task_id=task.task_id,
            task_title=task.title,
            attempt=context.get("attempt", 1),
            prev_error=context.get("prev_error", ""),
        )
        try:
            proc = subprocess.run(
                ["bash", "-c", cmd_str],
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
        cmd_str = self.checker_cmd_template.format(
            task_id=task.task_id,
            task_title=task.title,
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
            is_approved = "APPROVE" in output.upper() and "REJECT" not in output.upper()
            return is_approved, output.strip()
        except subprocess.TimeoutExpired:
            return False, f"Checker timed out after {self.timeout_sec}s"
        except Exception as e:  # noqa: BLE001
            return False, f"Failed to execute Checker: {e}"
