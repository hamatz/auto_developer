"""TEAM-32: チームの worktree での review-input 出力 (team_profile.json / team_review_docs/) とスキル手順のテスト。"""

from __future__ import annotations

import hashlib
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_team_repo(
    repo: Path,
    *,
    teams_data: dict[str, object],
    review_docs: dict[str, str] | None = None,
    ui_verify_script: str | None = None,
) -> str:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    (repo / "SPEC.md").write_text("# SPEC\n\n## 1. Goal\nTeam 32 test\n", encoding="utf-8")
    (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. Root task\n", encoding="utf-8")
    (repo / "plans").mkdir(parents=True, exist_ok=True)
    (repo / "plans" / "ui.md").write_text("# PLAN (ui)\n\n## ステップ\n- [ ] 1. Build UI\n", encoding="utf-8")
    (repo / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "ui" / "app.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    local_sh = repo / "scripts" / "verify_local.sh"
    local_sh.write_text("#!/usr/bin/env bash\n# auto-dev: red-first=off\nexit 0\n", encoding="utf-8")
    local_sh.chmod(0o755)

    if ui_verify_script is not None:
        ui_sh = repo / "scripts" / "verify_ui.sh"
        ui_sh.write_text(ui_verify_script, encoding="utf-8")
        ui_sh.chmod(0o755)

    for rel_path, content in (review_docs or {}).items():
        doc_path = repo / rel_path
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(content, encoding="utf-8")

    (repo / "teams.json").write_text(
        json.dumps(teams_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return _git(repo, "rev-parse", "HEAD")


class TestTeam32ReviewInputProfile(unittest.TestCase):
    """TEAM-32: チームの worktree の review-input と /nightly-run スキルを検証する。"""

    def test_worktree_review_input_writes_team_profile_and_review_docs(self) -> None:
        doc_design = "# Design System\nUse 8px grid.\n"
        doc_a11y = "# Accessibility\nContrast >= 4.5:1.\n"
        ui_script = "#!/usr/bin/env bash\n# auto-dev: red-first=off\nexit 0\n"

        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                    "skills": [{"name": "frontend-design", "version": "1.2.0"}],
                    "maker": {"model": "gemini-4-hydrogen-medium"},
                    "checker": {"model": "claude-sonnet-5-5"},
                    "review_docs": ["docs/design_system.md", "docs/a11y/rules.md"],
                    "verify_script": "scripts/verify_ui.sh",
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(
                repo,
                teams_data=teams_data,
                review_docs={
                    "docs/design_system.md": doc_design,
                    "docs/a11y/rules.md": doc_a11y,
                },
                ui_verify_script=ui_script,
            )
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            (wt_ui / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            self.assertEqual(
                main(["--work-dir", str(wt_ui), "verify-step", "--checkpoint", chk, "--task-id", "1"]),
                0,
            )

            out_dir = Path(review_tmp) / "review_out"
            self.assertEqual(
                main(["--work-dir", str(wt_ui), "review-input", "--task-id", "1", "--out-dir", str(out_dir)]),
                0,
            )

            profile_path = out_dir / "team_profile.json"
            self.assertTrue(profile_path.is_file(), "team_profile.json must be written in team worktree")
            profile = json.loads(profile_path.read_text(encoding="utf-8"))

            self.assertEqual(profile["team_id"], "ui")
            self.assertEqual(
                profile["skills"],
                {"declared": [{"name": "frontend-design", "version": "1.2.0"}]},
            )
            self.assertEqual(
                profile["maker"],
                {"model": "gemini-4-hydrogen-medium", "family": "gemini"},
            )
            self.assertEqual(
                profile["checker"],
                {"model": "claude-sonnet-5-5", "family": "claude"},
            )
            self.assertEqual(
                profile["review_docs"],
                [
                    {
                        "path": "docs/design_system.md",
                        "sha256": hashlib.sha256(doc_design.encode("utf-8")).hexdigest(),
                    },
                    {
                        "path": "docs/a11y/rules.md",
                        "sha256": hashlib.sha256(doc_a11y.encode("utf-8")).hexdigest(),
                    },
                ],
            )
            self.assertEqual(
                profile["verify_script"],
                {
                    "path": "scripts/verify_ui.sh",
                    "sha256": hashlib.sha256(ui_script.encode("utf-8")).hexdigest(),
                },
            )

            docs_root = out_dir / "team_review_docs"
            self.assertTrue(docs_root.is_dir(), "team_review_docs/ must be created in team worktree")
            copied_design = docs_root / "docs" / "design_system.md"
            copied_a11y = docs_root / "docs" / "a11y" / "rules.md"
            self.assertTrue(copied_design.is_file())
            self.assertTrue(copied_a11y.is_file())
            self.assertEqual(copied_design.read_text(encoding="utf-8"), doc_design)
            self.assertEqual(copied_a11y.read_text(encoding="utf-8"), doc_a11y)

    def test_worktree_without_profile_fields_still_writes_team_profile_and_dir(self) -> None:
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(repo, teams_data=teams_data)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            (wt_ui / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            self.assertEqual(
                main(["--work-dir", str(wt_ui), "verify-step", "--checkpoint", chk, "--task-id", "1"]),
                0,
            )

            out_dir = Path(review_tmp) / "review_out"
            self.assertEqual(
                main(["--work-dir", str(wt_ui), "review-input", "--task-id", "1", "--out-dir", str(out_dir)]),
                0,
            )

            profile = json.loads((out_dir / "team_profile.json").read_text(encoding="utf-8"))
            self.assertEqual(profile["team_id"], "ui")
            self.assertEqual(profile["skills"], {"declared": []})
            self.assertIsNone(profile["maker"])
            self.assertIsNone(profile["checker"])
            self.assertEqual(profile["review_docs"], [])
            self.assertIsNone(profile["verify_script"])
            self.assertTrue((out_dir / "team_review_docs").is_dir())

    def test_single_repo_review_input_does_not_write_team_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")
            (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. Single task\n", encoding="utf-8")
            (repo / "scripts").mkdir(parents=True, exist_ok=True)
            sh = repo / "scripts" / "verify_local.sh"
            sh.write_text("#!/usr/bin/env bash\n# auto-dev: red-first=off\nexit 0\n", encoding="utf-8")
            sh.chmod(0o755)
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "init")
            chk = _git(repo, "rev-parse", "HEAD")

            (repo / "app.py").write_text("A = 1\n", encoding="utf-8")
            self.assertEqual(
                main(["--work-dir", str(repo), "verify-step", "--checkpoint", chk, "--task-id", "1"]),
                0,
            )
            out_dir = Path(review_tmp) / "review_out"
            self.assertEqual(
                main(["--work-dir", str(repo), "review-input", "--task-id", "1", "--out-dir", str(out_dir)]),
                0,
            )
            self.assertFalse((out_dir / "team_profile.json").exists())
            self.assertFalse((out_dir / "team_review_docs").exists())

    def test_worktree_review_input_fails_when_declared_review_doc_missing(self) -> None:
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                    "review_docs": ["docs/missing.md"],
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(repo, teams_data=teams_data)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            (wt_ui / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
            self.assertEqual(
                main(["--work-dir", str(wt_ui), "verify-step", "--checkpoint", chk, "--task-id", "1"]),
                0,
            )
            out_dir = Path(review_tmp) / "review_out"
            rc = main(["--work-dir", str(wt_ui), "review-input", "--task-id", "1", "--out-dir", str(out_dir)])
            self.assertNotEqual(rc, 0)

    def test_nightly_run_skill_documents_team_profile_and_review_docs_for_checker(self) -> None:
        skill_path = Path(__file__).resolve().parent.parent / "skills" / "nightly-run" / "SKILL.md"
        content = skill_path.read_text(encoding="utf-8")
        self.assertIn("team_profile.json", content)
        self.assertIn("team_review_docs/", content)


if __name__ == "__main__":
    unittest.main()
