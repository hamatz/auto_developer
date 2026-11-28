"""TEAM-31: チームの worktree の verify-step でチームの verify_script を使い、Evidence に記録するテスト。"""

from __future__ import annotations

import hashlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr
from pathlib import Path

from auto_developer.cli import main


def _clean_env() -> dict[str, str]:
    return {
        k: v
        for k, v in os.environ.items()
        if k not in ("PYTHONDONTWRITEBYTECODE", "PYTHONPYCACHEPREFIX")
    }


def _git(repo_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo_dir,
        capture_output=True,
        text=True,
        check=True,
        env=_clean_env(),
    )


def _init_multi_team_repo(
    repo_dir: Path,
    *,
    local_script: str = "#!/usr/bin/env bash\nexit 0\n",
    ui_script: str = "#!/usr/bin/env bash\nexit 0\n",
    ui_plan_text: str = "# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI task\n",
    core_plan_text: str = "# PLAN (core)\n\n## ステップ\n- [ ] 1. Core task\n",
) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test User")

    (repo_dir / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Plan\n\n## ステップ\n- [ ] 1. Main task\n", encoding="utf-8")
    (repo_dir / "plans").mkdir(parents=True, exist_ok=True)
    (repo_dir / "plans" / "ui.md").write_text(ui_plan_text, encoding="utf-8")
    (repo_dir / "plans" / "core.md").write_text(core_plan_text, encoding="utf-8")

    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    local_sh = repo_dir / "scripts" / "verify_local.sh"
    local_sh.write_text(local_script, encoding="utf-8")
    local_sh.chmod(0o755)

    ui_sh = repo_dir / "scripts" / "verify_ui.sh"
    ui_sh.write_text(ui_script, encoding="utf-8")
    ui_sh.chmod(0o755)

    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui" / "app.py").write_text("X = 1\n", encoding="utf-8")
    (repo_dir / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "core" / "mod.py").write_text("Y = 1\n", encoding="utf-8")

    teams_data = {
        "version": 1,
        "project": "demo",
        "teams": [
            {
                "id": "ui",
                "plan": "plans/ui.md",
                "owns": ["src/ui/**"],
                "verify_script": "scripts/verify_ui.sh",
            },
            {
                "id": "core",
                "plan": "plans/core.md",
                "owns": ["src/core/**"],
            },
        ],
    }
    (repo_dir / "teams.json").write_text(
        json.dumps(teams_data, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "init")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


class TestTeam31TeamVerifyScript(unittest.TestCase):
    """TEAM-31: チームの worktree の verify-step はチームの verify_script を使う。"""

    def test_worktree_verify_step_uses_team_verify_script_and_fails_when_it_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_multi_team_repo(
                repo,
                local_script="#!/usr/bin/env bash\necho LOCAL_OK\nexit 0\n",
                ui_script="#!/usr/bin/env bash\necho UI_VERIFY_FAILED >&2\nexit 1\n",
            )
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            (wt_ui / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            err_buf = io.StringIO()
            with redirect_stderr(err_buf):
                rc = main(["--work-dir", str(wt_ui), "verify-step", "--checkpoint", chk, "--task-id", "1"])

            self.assertEqual(rc, 1, err_buf.getvalue())
            self.assertIn("UI_VERIFY_FAILED", err_buf.getvalue())

    def test_worktree_verify_step_reads_red_first_and_timeout_from_team_verify_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_multi_team_repo(
                repo,
                local_script="#!/usr/bin/env bash\n# auto-dev: verify-timeout=60\n# auto-dev: red-first=off\nexit 0\n",
                ui_script="#!/usr/bin/env bash\n# auto-dev: verify-timeout=1\n# auto-dev: red-first=enforce\nsleep 3\nexit 0\n",
                ui_plan_text="# PLAN (ui)\n\n## ステップ\n- [ ] 1. UI feature (Red: failing test)\n",
            )
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            # 1. red-first=enforce が verify_ui.sh から読まれる（テスト変更なしのため RED_FIRST_NOT_CONFIRMED）
            (wt_ui / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            err_buf = io.StringIO()
            with redirect_stderr(err_buf):
                rc_rf = main(["--work-dir", str(wt_ui), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(rc_rf, 1, err_buf.getvalue())
            self.assertIn("RED_FIRST_NOT_CONFIRMED", err_buf.getvalue())

        with tempfile.TemporaryDirectory() as tmp2:
            base2 = Path(tmp2)
            repo2 = base2 / "repo"
            repo2.mkdir()
            chk2 = _init_multi_team_repo(
                repo2,
                local_script="#!/usr/bin/env bash\n# auto-dev: verify-timeout=60\n# auto-dev: red-first=off\nexit 0\n",
                ui_script="#!/usr/bin/env bash\n# auto-dev: verify-timeout=1\n# auto-dev: red-first=off\nsleep 3\nexit 0\n",
            )
            wt_ui2 = base2 / "wt_ui"
            _git(repo2, "worktree", "add", "-b", "team/ui", str(wt_ui2), "main")

            # 2. verify-timeout=1 が verify_ui.sh から読まれる（sleep 3 でタイムアウト exit 124）
            (wt_ui2 / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            err_buf2 = io.StringIO()
            with redirect_stderr(err_buf2):
                rc_to = main(["--work-dir", str(wt_ui2), "verify-step", "--checkpoint", chk2, "--task-id", "1"])
            self.assertEqual(rc_to, 124, err_buf2.getvalue())
            self.assertIn("verify_ui.sh", err_buf2.getvalue())

    def test_worktree_verify_step_records_verify_script_path_and_sha256_in_evidence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            ui_body = "#!/usr/bin/env bash\necho UI_OK\nexit 0\n"
            chk = _init_multi_team_repo(repo, ui_script=ui_body)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            (wt_ui / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            rc = main(["--work-dir", str(wt_ui), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(rc, 0)

            ev_path = wt_ui / ".dev" / "evidence" / "task_1.json"
            self.assertTrue(ev_path.exists())
            ev = json.loads(ev_path.read_text(encoding="utf-8"))
            ver = ev["details"]["verification"]
            expected_sha = hashlib.sha256(ui_body.encode("utf-8")).hexdigest()
            self.assertEqual(ver["verify_script"], "scripts/verify_ui.sh")
            self.assertEqual(ver["verify_script_sha256"], expected_sha)

            # commit-step 後の永続レコードにも引き継がれる
            out_dir = Path(review_tmp)
            self.assertEqual(
                main(["--work-dir", str(wt_ui), "review-input", "--task-id", "1", "--out-dir", str(out_dir)]),
                0,
            )
            checker_out = out_dir / "checker.txt"
            checker_out.write_text("DECISION: APPROVE\n", encoding="utf-8")
            self.assertEqual(
                main(
                    [
                        "--work-dir",
                        str(wt_ui),
                        "record-review",
                        "--task-id",
                        "1",
                        "--checker-output",
                        str(checker_out),
                        "--diff-file",
                        str(out_dir / "diff.patch"),
                        "--spec-file",
                        str(wt_ui / "SPEC.md"),
                        "--maker-model",
                        "gemini-4-hydrogen-medium",
                        "--checker-model",
                        "claude-sonnet-5-5",
                        "--reason",
                        "ok",
                    ]
                ),
                0,
            )
            self.assertEqual(
                main(
                    [
                        "--work-dir",
                        str(wt_ui),
                        "commit-step",
                        "--task-id",
                        "1",
                        "--checkpoint",
                        chk,
                        "--maker-model",
                        "gemini-4-hydrogen-medium",
                        "--checker-model",
                        "claude-sonnet-5-5",
                        "--checker-reason",
                        "ok",
                    ]
                ),
                0,
            )
            rec = json.loads(ev_path.read_text(encoding="utf-8"))
            self.assertEqual(rec["status"], "COMPLETED")
            self.assertEqual(rec["verification"]["verify_script"], "scripts/verify_ui.sh")
            self.assertEqual(rec["verification"]["verify_script_sha256"], expected_sha)

    def test_worktree_without_verify_script_and_main_repo_use_verify_local(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            local_body = "#!/usr/bin/env bash\necho LOCAL_OK\nexit 0\n"
            chk = _init_multi_team_repo(
                repo,
                local_script=local_body,
                ui_script="#!/usr/bin/env bash\nexit 1\n",
            )
            wt_core = base / "wt_core"
            _git(repo, "worktree", "add", "-b", "team/core", str(wt_core), "main")

            (wt_core / "src" / "core" / "mod.py").write_text("Y = 2\n", encoding="utf-8")
            rc_core = main(["--work-dir", str(wt_core), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(rc_core, 0)

            ev_core = json.loads((wt_core / ".dev" / "evidence" / "task_1.json").read_text(encoding="utf-8"))
            expected_local_sha = hashlib.sha256(local_body.encode("utf-8")).hexdigest()
            self.assertEqual(ev_core["details"]["verification"]["verify_script"], "scripts/verify_local.sh")
            self.assertEqual(ev_core["details"]["verification"]["verify_script_sha256"], expected_local_sha)

            # メインリポジトリでも scripts/verify_local.sh が使われ、Evidence に記録される
            (repo / "src" / "core" / "mod.py").write_text("Y = 3\n", encoding="utf-8")
            rc_main = main(["--work-dir", str(repo), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(rc_main, 0)

            ev_main = json.loads((repo / ".git" / "nightly_evidence" / "task_1.json").read_text(encoding="utf-8"))
            self.assertEqual(ev_main["details"]["verification"]["verify_script"], "scripts/verify_local.sh")
            self.assertEqual(ev_main["details"]["verification"]["verify_script_sha256"], expected_local_sha)


if __name__ == "__main__":
    unittest.main()
