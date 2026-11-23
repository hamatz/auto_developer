from __future__ import annotations

import io
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer import demo as demo_mod
from auto_developer.cli import main


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


class TestDx8Demo(unittest.TestCase):
    def test_demo_checker_script_approves_spec_diff_without_custom_rejection(self) -> None:
        payload = "--- DIFF ---\n+++ b/SPEC.md\n+tamper\n--- SPEC ---\n# SPEC\n"
        proc = subprocess.run(
            [sys.executable, "-c", demo_mod._DEMO_CHECKER_SCRIPT],
            input=payload,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        data = json.loads(proc.stdout)
        self.assertEqual(data["decision"], "APPROVE")

    def _run_mocked_demo(
        self,
        *,
        run3_rc: int = 1,
        create_quarantine: bool = True,
        failure_phase: str = "PROTECTED_FILES",
        verify_rc: int = 0,
        verify_json: dict[str, object] | None = None,
    ) -> int:
        run_count = [0]

        def _handler(repo_dir: Path, *args: str) -> tuple[int, str, str]:
            cmd = args[0]
            if cmd == "init":
                (repo_dir / ".gitignore").write_text(".dev/\nlogs/\n", encoding="utf-8")
                (repo_dir / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
                (repo_dir / "PLAN.md").write_text("# PLAN\n- [ ] 1. A\n- [ ] 2. B\n- [ ] 3. C\n", encoding="utf-8")
                _git(repo_dir, "add", ".")
                _git(repo_dir, "commit", "-m", "init")
                return 0, "", ""
            if cmd == "run":
                run_count[0] += 1
                if run_count[0] == 1:
                    (repo_dir / "PLAN.md").write_text(
                        "# PLAN\n- [x] 1. A\n- [x] 2. B\n- [ ] 3. C\n",
                        encoding="utf-8",
                    )
                    _git(repo_dir, "add", "PLAN.md")
                    _git(repo_dir, "commit", "-m", "complete 1 and 2")
                    return 0, "", ""
                if run_count[0] == 2:
                    if create_quarantine:
                        _git(repo_dir, "checkout", "-b", "quarantine/3-demo")
                        p = repo_dir / "PLAN.md"
                        p.write_text(p.read_text(encoding="utf-8") + " **[FAILED]**\n", encoding="utf-8")
                        _git(repo_dir, "add", "PLAN.md")
                        _git(repo_dir, "commit", "-m", "quarantine")
                        _git(repo_dir, "checkout", "main")
                    log_p = repo_dir / "logs" / "nightly" / "task_3_failure.log"
                    log_p.parent.mkdir(parents=True, exist_ok=True)
                    log_p.write_text(
                        f"Failure Phase: {failure_phase}\nReason: test\n",
                        encoding="utf-8",
                    )
                    return run3_rc, "run3", ""
                return 0, "", ""
            if cmd == "brief":
                return 0, "MORNING BRIEF", ""
            if cmd == "bundle":
                bdir = repo_dir / ".dev" / "bundles"
                bdir.mkdir(parents=True, exist_ok=True)
                bzip = bdir / "bundle_demo.zip"
                bzip.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
                return 0, f"BUNDLE CREATED: {bzip}", ""
            if cmd == "verify-bundle":
                payload = verify_json if verify_json is not None else {"status": "VERIFIED"}
                return verify_rc, json.dumps(payload), ""
            return 0, "", ""

        err_buf = io.StringIO()
        with (
            patch.object(demo_mod, "_run_demo_cli", side_effect=_handler),
            patch("sys.stdout", io.StringIO()),
            patch("sys.stderr", err_buf),
        ):
            rc = main(["demo"])
        self._last_err = err_buf.getvalue()
        return rc

    def test_demo_fails_at_each_stage_when_conditions_not_met(self) -> None:
        # Baseline passes
        self.assertEqual(self._run_mocked_demo(), 0, getattr(self, "_last_err", ""))
        # 1. quarantine stage exit != 1
        self.assertEqual(self._run_mocked_demo(run3_rc=0), 1)
        # 2. quarantine branch missing
        self.assertEqual(self._run_mocked_demo(create_quarantine=False), 1)
        # 3. wrong Failure Phase (e.g. CHECKER_REJECT instead of PROTECTED_FILES)
        self.assertEqual(self._run_mocked_demo(failure_phase="CHECKER_REJECT"), 1)
        # 4. verify-bundle --json status == "UNVERIFIED" (substring "VERIFIED" must NOT match!)
        self.assertEqual(self._run_mocked_demo(verify_rc=0, verify_json={"status": "UNVERIFIED"}), 1)


if __name__ == "__main__":
    unittest.main()
