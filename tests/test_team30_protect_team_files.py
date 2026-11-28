from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main
from auto_developer.guard import GitGuard


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


def _init_repo_with_teams(
    repo_dir: Path,
    *,
    teams_content: str | None = None,
    create_declared_files: bool = True,
) -> str:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.email", "test@example.com")
    _git(repo_dir, "config", "user.name", "Test User")
    (repo_dir / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
    (repo_dir / "PLAN.md").write_text("# Plan\n- [ ] 1. Task\n", encoding="utf-8")
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    verify_sh = repo_dir / "scripts" / "verify_local.sh"
    verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    verify_sh.chmod(0o755)
    (repo_dir / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo_dir / "src" / "ui" / "app.py").write_text("X = 1\n", encoding="utf-8")

    if teams_content is not None:
        (repo_dir / "teams.json").write_text(teams_content, encoding="utf-8")
        if create_declared_files:
            (repo_dir / "docs").mkdir(parents=True, exist_ok=True)
            (repo_dir / "docs" / "ui_review.md").write_text("# UI Review\n", encoding="utf-8")
            (repo_dir / "scripts" / "verify_ui.sh").write_text(
                "#!/usr/bin/env bash\nexit 0\n", encoding="utf-8"
            )
            (repo_dir / "scripts" / "verify_ui.sh").chmod(0o755)

    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "init")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def _default_teams_json() -> str:
    data = {
        "version": 1,
        "project": "demo",
        "teams": [
            {
                "id": "ui",
                "plan": "PLAN.md",
                "owns": ["src/ui/**"],
                "review_docs": ["docs/ui_review.md", "docs/日本語.md"],
                "verify_script": "scripts/verify_ui.sh",
            }
        ],
    }
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


class TestTeam30ProtectTeamFiles(unittest.TestCase):
    """TEAM-30: チームの review_docs と verify_script を保護ファイルとして扱う。"""

    def test_guard_rejects_modify_delete_and_untracked_add_of_team_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_repo_with_teams(repo, teams_content=_default_teams_json())
            guard = GitGuard(repo_dir=repo)

            # 1. review_docs の変更を検知
            review_doc = repo / "docs" / "ui_review.md"
            review_doc.write_text("# Tampered\n", encoding="utf-8")
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertFalse(guard.check_protected_files_untouched(chk, allow_immunity_additions=True))
            self.assertIn("docs/ui_review.md", guard.get_modified_protected_files(chk))

            # 2. review_docs の削除を検知
            _git(repo, "checkout", "--", "docs/ui_review.md")
            review_doc.unlink()
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertFalse(guard.check_protected_files_untouched(chk, allow_immunity_additions=True))
            self.assertIn("docs/ui_review.md", guard.get_modified_protected_files(chk))

            # 3. チェックポイント時点で未作成の review_docs の新規追加（未追跡・非ASCIIパス含む）を検知
            _git(repo, "checkout", "--", "docs/ui_review.md")
            new_doc = repo / "docs" / "日本語.md"
            new_doc.write_text("# New\n", encoding="utf-8")
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertFalse(guard.check_protected_files_untouched(chk, allow_immunity_additions=True))
            self.assertIn("docs/日本語.md", guard.get_modified_protected_files(chk))
            new_doc.unlink()

            # 4. verify_script の変更を検知
            v_ui = repo / "scripts" / "verify_ui.sh"
            v_ui.write_text("#!/usr/bin/env bash\nexit 1\n", encoding="utf-8")
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertFalse(guard.check_protected_files_untouched(chk, allow_immunity_additions=True))
            self.assertIn("scripts/verify_ui.sh", guard.get_modified_protected_files(chk))
            _git(repo, "checkout", "--", "scripts/verify_ui.sh")

            # 5. 通常のファイル変更は許可
            (repo / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            self.assertTrue(guard.check_protected_files_untouched(chk))
            self.assertEqual(guard.get_modified_protected_files(chk), [])

    def test_guard_fallback_when_teams_json_missing_or_unreadable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_repo_with_teams(repo, teams_content="{invalid json")
            guard = GitGuard(repo_dir=repo)

            # 壊れた teams.json でもクラッシュせず既存の保護検査が動く
            (repo / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            self.assertTrue(guard.check_protected_files_untouched(chk))
            self.assertEqual(guard.get_modified_protected_files(chk), [])

            (repo / "SPEC.md").write_text("# Modified\n", encoding="utf-8")
            self.assertFalse(guard.check_protected_files_untouched(chk))
            self.assertIn("SPEC.md", guard.get_modified_protected_files(chk))

    def test_verify_step_rejects_modified_team_review_doc_and_verify_script(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            chk = _init_repo_with_teams(repo, teams_content=_default_teams_json())

            (repo / "docs" / "ui_review.md").write_text("# Tampered\n", encoding="utf-8")
            rc = main(["--work-dir", str(repo), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(rc, 10)

            _git(repo, "checkout", "--", "docs/ui_review.md")
            (repo / "scripts" / "verify_ui.sh").write_text("#!/usr/bin/env bash\nexit 0\n# x\n", encoding="utf-8")
            rc2 = main(["--work-dir", str(repo), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(rc2, 10)

    def test_commit_step_rejects_modified_team_review_doc_with_circuit_breaker(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            repo = Path(tmp)
            chk = _init_repo_with_teams(repo, teams_content=_default_teams_json())

            # 正常に verify-step と record-review を通す
            (repo / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            v_rc = main(["--work-dir", str(repo), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(v_rc, 0)

            diff_dir = Path(review_tmp)
            ri_rc = main(
                [
                    "--work-dir",
                    str(repo),
                    "review-input",
                    "--task-id",
                    "1",
                    "--out-dir",
                    str(diff_dir),
                ]
            )
            self.assertEqual(ri_rc, 0)

            checker_out = diff_dir / "checker.txt"
            checker_out.write_text("DECISION: APPROVE\n", encoding="utf-8")
            rr_rc = main(
                [
                    "--work-dir",
                    str(repo),
                    "record-review",
                    "--task-id",
                    "1",
                    "--checker-output",
                    str(checker_out),
                    "--diff-file",
                    str(diff_dir / "diff.patch"),
                    "--spec-file",
                    str(repo / "SPEC.md"),
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                    "--reason",
                    "ok",
                ]
            )
            self.assertEqual(rr_rc, 0)

            # record-review 後に review_docs を改ざんして commit-step を呼ぶ -> exit 10 (CIRCUIT BREAKER)
            (repo / "docs" / "ui_review.md").write_text("# Tampered after review\n", encoding="utf-8")
            proc = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "auto_developer.cli",
                    "--work-dir",
                    str(repo),
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
                ],
                cwd=repo,
                capture_output=True,
                text=True,
                check=False,
                env=_clean_env(),
            )
            self.assertEqual(proc.returncode, 10, proc.stdout + proc.stderr)
            self.assertIn("CIRCUIT BREAKER", proc.stderr)
            self.assertEqual(_git(repo, "rev-parse", "HEAD").stdout.strip(), chk)


if __name__ == "__main__":
    unittest.main()
