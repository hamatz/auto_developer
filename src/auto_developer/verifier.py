from __future__ import annotations

from pathlib import Path
import subprocess


class DeterministicVerifier:
    """ローカル検証スクリプトの実行と判定を担当。"""

    def run_verification(
        self, script_path: Path, timeout_sec: int = 300, cwd: Path | None = None
    ) -> tuple[int, str]:
        """検証スクリプトを実行し、(exit_code, output) を返す。"""
        if not script_path.exists():
            return 1, f"Verification script not found: {script_path}"

        working_dir = cwd or script_path.parent
        try:
            proc = subprocess.run(
                ["bash", str(script_path)],
                cwd=working_dir,
                capture_output=True,
                text=True,
                timeout=timeout_sec,
            )
            output = proc.stdout + "\n" + proc.stderr
            return proc.returncode, output.strip()
        except subprocess.TimeoutExpired as e:
            return 124, f"Verification timed out after {timeout_sec}s: {e}"
        except Exception as e:
            return 1, f"Failed to execute verification script: {e}"
