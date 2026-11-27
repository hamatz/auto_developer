"""GATE-16: Red-First の設定、対象タスク、対象テストファイル、記録のテスト (Red-First)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.verifier import (
    find_changed_test_files,
    is_red_first_task,
    resolve_red_first_mode,
)

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
    (repo / "tests" / "immunity").mkdir(parents=True, exist_ok=True)
    (repo / "tests" / "immunity" / "test_keep.py").write_text(
        "def test_keep():\n    assert True\n", encoding="utf-8"
    )
    (repo / "tests" / "test_existing.py").write_text(
        "def test_existing():\n    assert True\n", encoding="utf-8"
    )
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "chore: initial commit")
    return _git(repo, "rev-parse", "HEAD")


def _run_verify_step(repo: Path, checkpoint: str, task_id: str = "1") -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "auto_developer.cli",
            "--work-dir",
            str(repo),
            "verify-step",
            "--checkpoint",
            checkpoint,
            "--task-id",
            task_id,
        ],
        cwd=repo,
        env=_clean_subprocess_env(),
        capture_output=True,
        text=True,
        check=False,
    )


class TestGate16RedFirstConfig(unittest.TestCase):
    """GATE-16 の設定解決・対象タスク判定・対象テストファイル抽出・記録を検証する。"""

    def test_resolve_red_first_mode_defaults_to_warn(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "verify_local.sh"
            script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            self.assertEqual(resolve_red_first_mode(script), "warn")
            self.assertEqual(resolve_red_first_mode(Path(tmp) / "missing.sh"), "warn")
            self.assertEqual(resolve_red_first_mode(None), "warn")

    def test_resolve_red_first_mode_reads_first_20_lines_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "verify_local.sh"
            padding = "\n".join("# comment" for _ in range(18))
            script.write_text(
                f"#!/usr/bin/env bash\n{padding}\n# auto-dev: red-first=enforce\nexit 0\n",
                encoding="utf-8",
            )
            self.assertEqual(script.read_text(encoding="utf-8").splitlines()[19], "# auto-dev: red-first=enforce")
            self.assertEqual(resolve_red_first_mode(script), "enforce")

            padding_20 = "\n".join("# comment" for _ in range(19))
            script.write_text(
                f"#!/usr/bin/env bash\n{padding_20}\n# auto-dev: red-first=off\nexit 0\n",
                encoding="utf-8",
            )
            self.assertEqual(script.read_text(encoding="utf-8").splitlines()[20], "# auto-dev: red-first=off")
            self.assertEqual(resolve_red_first_mode(script), "warn")

    def test_resolve_red_first_mode_rejects_invalid_values(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "verify_local.sh"
            for bad in ("invalid", "true", "WARN", ""):
                script.write_text(
                    f"#!/usr/bin/env bash\n# auto-dev: red-first={bad}\nexit 0\n",
                    encoding="utf-8",
                )
                with self.assertRaises(ValueError):
                    resolve_red_first_mode(script)

    def test_is_red_first_task(self) -> None:
        self.assertTrue(is_red_first_task("Add GATE-16 (Red-First)"))
        self.assertTrue(is_red_first_task("Fix bug (Red: repro)"))
        self.assertFalse(is_red_first_task("Update documentation"))

    def test_find_changed_test_files_filters_and_excludes_immunity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
            )
            (repo / "tests" / "test_existing.py").write_text(
                "def test_existing():\n    assert 1 == 1\n", encoding="utf-8"
            )
            (repo / "tests" / "test_added.py").write_text(
                "def test_added():\n    assert True\n", encoding="utf-8"
            )
            (repo / "tests" / "sub").mkdir(parents=True, exist_ok=True)
            (repo / "tests" / "sub" / "feature_test.py").write_text(
                "def test_sub():\n    assert True\n", encoding="utf-8"
            )
            (repo / "tests" / "test_日本語.py").write_text(
                "def test_jp():\n    assert True\n", encoding="utf-8"
            )
            (repo / "tests" / "helper.py").write_text("X = 1\n", encoding="utf-8")
            (repo / "tests" / "immunity" / "test_new_imm.py").write_text(
                "def test_imm():\n    assert True\n", encoding="utf-8"
            )
            (repo / "src_file.py").write_text("Y = 2\n", encoding="utf-8")

            found = find_changed_test_files(repo, checkpoint)
            self.assertEqual(
                found,
                [
                    "tests/sub/feature_test.py",
                    "tests/test_added.py",
                    "tests/test_existing.py",
                    "tests/test_日本語.py",
                ],
            )

    def test_verify_step_invalid_red_first_directive_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=bogus\nexit 0\n",
            )
            proc = _run_verify_step(repo, checkpoint)
            self.assertEqual(proc.returncode, 2, proc.stderr)
            self.assertIn("CONFIG ERROR", proc.stderr)

    def test_verify_step_off_mode_skips_inspection_and_recording(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=off\nexit 0\n",
            )
            proc = _run_verify_step(repo, checkpoint)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ev = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertNotIn("red_first", ev)
            events_path = repo / ".git" / "nightly_evidence" / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertFalse(any(e.get("event") == "red_first" for e in events))

    def test_verify_step_non_target_task_records_skipped_in_evidence_only(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Regular task\n",
            )
            proc = _run_verify_step(repo, checkpoint)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ev = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertEqual(ev["red_first"]["result"], "SKIPPED")
            self.assertEqual(ev["red_first"]["mode"], "warn")
            events_path = repo / ".git" / "nightly_evidence" / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            self.assertFalse(any(e.get("event") == "red_first" for e in events))

    def test_verify_step_target_task_no_test_changes_records_event_and_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Implement feature (Red: repro)\n",
            )
            proc = _run_verify_step(repo, checkpoint)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ev = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertEqual(
                ev["red_first"],
                {"mode": "warn", "result": "NO_TEST_CHANGES", "test_files": [], "file_results": []},
            )
            events_path = repo / ".git" / "nightly_evidence" / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            rf_events = [e for e in events if e.get("event") == "red_first"]
            self.assertEqual(len(rf_events), 1)
            self.assertEqual(rf_events[0]["task_id"], "1")
            self.assertEqual(rf_events[0]["checkpoint"], checkpoint)
            self.assertEqual(rf_events[0]["mode"], "warn")
            self.assertEqual(rf_events[0]["result"], "NO_TEST_CHANGES")
            self.assertEqual(rf_events[0]["test_files"], [])

    def test_verify_step_target_task_with_changed_test_records_files_and_inv8(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Implement feature (Red-First)\n",
                verify_script_text="#!/usr/bin/env bash\n# auto-dev: red-first=enforce\nexit 0\n",
            )
            (repo / "tests" / "test_new.py").write_text(
                "import unittest\n"
                "from non_existent_feature import fn\n\n"
                "class TestNew(unittest.TestCase):\n"
                "    def test_new(self):\n"
                "        self.assertTrue(fn())\n",
                encoding="utf-8",
            )
            # INV-8: block events.jsonl by creating a directory at its path
            (repo / ".git" / "nightly_evidence" / "events.jsonl").mkdir(parents=True, exist_ok=True)
            proc = _run_verify_step(repo, checkpoint)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            ev = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertEqual(ev["red_first"]["mode"], "enforce")
            self.assertEqual(ev["red_first"]["result"], "CONFIRMED")
            self.assertEqual(ev["red_first"]["test_files"], ["tests/test_new.py"])

    def test_find_changed_test_files_raises_on_invalid_checkpoint(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_repo(
                repo,
                plan_text="# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n",
            )
            with self.assertRaises(RuntimeError):
                find_changed_test_files(repo, "nonexistent-ref-000000")

    def test_verify_step_handles_find_changed_test_files_runtime_error(self) -> None:
        from auto_developer import cli

        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            plan_text = "# PLAN.md\n\n## ステップ\n- [ ] 1. Task (Red-First)\n"
            checkpoint = _init_repo(repo, plan_text=plan_text)
            with (
                patch.object(cli, "find_changed_test_files", side_effect=RuntimeError("git diff failed")),
                patch.object(
                    sys,
                    "argv",
                    [
                        "auto-dev",
                        "--work-dir",
                        str(repo),
                        "verify-step",
                        "--checkpoint",
                        checkpoint,
                        "--task-id",
                        "1",
                    ],
                ),
            ):
                import io
                from contextlib import redirect_stderr

                stderr_buf = io.StringIO()
                with redirect_stderr(stderr_buf):
                    rc = cli.main()
            self.assertEqual(rc, 2)
            self.assertIn("RED_FIRST_ERROR:", stderr_buf.getvalue())
            self.assertEqual((repo / "PLAN.md").read_text(encoding="utf-8"), plan_text)


if __name__ == "__main__":
    unittest.main()
