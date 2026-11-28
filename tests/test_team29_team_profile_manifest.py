"""TEAM-29: teams.json のチーム持ち物宣言（skills, maker, checker, review_docs, verify_script）の検証テスト。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.manifest import ManifestValidationError, TeamManifest


def _clean_env() -> dict[str, str]:
    env = dict(os.environ)
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{src_dir}{os.pathsep}{existing}" if existing else src_dir
    return env


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )


def _run_cli(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(cwd), *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=_clean_env(),
    )


def _init_repo_with_teams(repo_dir: Path, teams_data: dict[str, object]) -> None:
    _git(repo_dir, "init", "-b", "main")
    _git(repo_dir, "config", "user.name", "Test")
    _git(repo_dir, "config", "user.email", "test@example.com")
    (repo_dir / "plans").mkdir(parents=True, exist_ok=True)
    (repo_dir / "plans" / "ui.md").write_text("# PLAN\n- [ ] 1. Task\n", encoding="utf-8")
    (repo_dir / "docs").mkdir(parents=True, exist_ok=True)
    (repo_dir / "docs" / "design.md").write_text("# Design\n", encoding="utf-8")
    (repo_dir / "scripts").mkdir(parents=True, exist_ok=True)
    (repo_dir / "scripts" / "verify_ui.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (repo_dir / "teams.json").write_text(json.dumps(teams_data, indent=2), encoding="utf-8")
    _git(repo_dir, "add", "-A")
    _git(repo_dir, "commit", "-m", "init")


class TestTeam29ProfileManifest(unittest.TestCase):
    def test_valid_profile_fields_and_unknown_keys_ignored(self) -> None:
        data = {
            "version": 1,
            "project": "profile-test",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "skills": [{"name": "frontend-design", "version": "1.2.0"}],
                    "maker": {"model": "gemini-2.5-pro"},
                    "checker": {"model": "claude-sonnet-4"},
                    "review_docs": ["docs/design.md"],
                    "verify_script": "scripts/verify_ui.sh",
                    "unknown_extra_key": {"foo": "bar"},
                }
            ],
        }
        manifest = TeamManifest.from_dict(data, allow_depends_on=False)
        ui = manifest.teams[0]
        profile = ui.to_profile_dict()
        self.assertEqual(profile["skills"], [{"name": "frontend-design", "version": "1.2.0"}])
        self.assertEqual(profile["maker"], {"model": "gemini-2.5-pro", "family": "gemini"})
        self.assertEqual(profile["checker"], {"model": "claude-sonnet-4", "family": "claude"})
        self.assertEqual(profile["review_docs"], ["docs/design.md"])
        self.assertEqual(profile["verify_script"], "scripts/verify_ui.sh")

    def test_same_family_maker_and_checker_raises(self) -> None:
        cases = [
            (
                "inferred_same_family",
                {"model": "gemini-2.5-pro"},
                {"model": "gemini-2.5-flash"},
            ),
            (
                "explicit_same_family",
                {"model": "custom-a", "family": "claude"},
                {"model": "custom-b", "family": "claude"},
            ),
            (
                "unknown_family_when_both_declared",
                {"model": "custom-unknown-a"},
                {"model": "claude-sonnet-4"},
            ),
        ]
        for label, maker, checker in cases:
            data = {
                "version": 1,
                "project": "same-family-test",
                "teams": [
                    {
                        "id": "ui",
                        "owns": ["src/ui/**"],
                        "maker": maker,
                        "checker": checker,
                    }
                ],
            }
            with self.subTest(case=label), self.assertRaises(ManifestValidationError) as ctx:
                TeamManifest.from_dict(data, allow_depends_on=False)
            self.assertIn("ui", str(ctx.exception))

    def test_invalid_skills_raises(self) -> None:
        bad_skills_cases: list[object] = [
            "not-a-list",
            ["not-a-dict"],
            [{"name": "", "version": "1.0"}],
            [{"name": "skill-a", "version": ""}],
            [{"name": "skill-a"}],
            [{"version": "1.0"}],
            [{"name": 123, "version": "1.0"}],
        ]
        for bad_skills in bad_skills_cases:
            data = {
                "version": 1,
                "project": "bad-skills",
                "teams": [{"id": "ui", "owns": ["src/ui/**"], "skills": bad_skills}],
            }
            with self.subTest(skills=bad_skills), self.assertRaises(ManifestValidationError) as ctx:
                TeamManifest.from_dict(data, allow_depends_on=False)
            self.assertIn("skills", str(ctx.exception))

    def test_invalid_maker_or_checker_raises(self) -> None:
        bad_model_cases: list[object] = [
            "gemini-2.5-pro",
            {},
            {"model": ""},
            {"model": "   "},
            {"model": 123},
            {"model": "gemini-2.5-pro", "family": ""},
            {"model": "gemini-2.5-pro", "family": "unknown"},
            {"model": "gemini-2.5-pro", "family": "claude"},
        ]
        for bad_model in bad_model_cases:
            for field_name in ("maker", "checker"):
                data = {
                    "version": 1,
                    "project": "bad-model",
                    "teams": [{"id": "ui", "owns": ["src/ui/**"], field_name: bad_model}],
                }
                with self.subTest(field=field_name, val=bad_model), self.assertRaises(ManifestValidationError) as ctx:
                    TeamManifest.from_dict(data, allow_depends_on=False)
                self.assertIn(field_name, str(ctx.exception))

    def test_invalid_review_docs_and_verify_script_paths_raise(self) -> None:
        bad_paths: list[object] = [
            "",
            "   ",
            "/etc/passwd",
            "../secret.md",
            "docs/../secret.md",
            "docs/*.md",
            "docs/design?.md",
            "docs/[a].md",
            "docs/",
            123,
        ]
        for bad_path in bad_paths:
            data_docs = {
                "version": 1,
                "project": "bad-docs",
                "teams": [{"id": "ui", "owns": ["src/ui/**"], "review_docs": [bad_path]}],
            }
            with self.subTest(field="review_docs", path=bad_path), self.assertRaises(ManifestValidationError) as ctx:
                TeamManifest.from_dict(data_docs, allow_depends_on=False)
            self.assertIn("review_docs", str(ctx.exception))

            data_script = {
                "version": 1,
                "project": "bad-script",
                "teams": [{"id": "ui", "owns": ["src/ui/**"], "verify_script": bad_path}],
            }
            with self.subTest(field="verify_script", path=bad_path), self.assertRaises(ManifestValidationError) as ctx2:
                TeamManifest.from_dict(data_script, allow_depends_on=False)
            self.assertIn("verify_script", str(ctx2.exception))

        # review_docs must be a list
        with self.assertRaises(ManifestValidationError) as ctx3:
            TeamManifest.from_dict(
                {
                    "version": 1,
                    "project": "bad-docs-type",
                    "teams": [{"id": "ui", "owns": ["src/ui/**"], "review_docs": "docs/design.md"}],
                },
                allow_depends_on=False,
            )
        self.assertIn("review_docs", str(ctx3.exception))


class TestTeam29CliIntegration(unittest.TestCase):
    def test_cli_prepare_and_status_reject_same_family_with_exit_code_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_repo_with_teams(
                repo,
                {
                    "version": 1,
                    "project": "cli-same-family",
                    "teams": [
                        {
                            "id": "ui",
                            "owns": ["src/ui/**"],
                            "maker": {"model": "gemini-2.5-pro"},
                            "checker": {"model": "gemini-2.5-flash"},
                        }
                    ],
                },
            )
            prep = _run_cli(repo, "teams", "prepare", "--job-id", "job-bad")
            self.assertEqual(prep.returncode, 2, prep.stderr)
            self.assertIn("TEAMS_CONFIG_ERROR", prep.stderr)

            st = _run_cli(repo, "teams", "status", "--json")
            self.assertEqual(st.returncode, 2, st.stderr)
            self.assertIn("TEAMS_CONFIG_ERROR", st.stderr)

    def test_cli_prepare_and_status_json_include_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            repo = Path(tmpdir)
            _init_repo_with_teams(
                repo,
                {
                    "version": 1,
                    "project": "cli-profile",
                    "teams": [
                        {
                            "id": "ui",
                            "owns": ["src/ui/**"],
                            "skills": [{"name": "a11y", "version": "1.0"}],
                            "maker": {"model": "gemini-2.5-pro"},
                            "checker": {"model": "claude-sonnet-4"},
                            "review_docs": ["docs/design.md"],
                            "verify_script": "scripts/verify_ui.sh",
                        }
                    ],
                },
            )
            prep = _run_cli(repo, "teams", "prepare", "--job-id", "job-prof", "--json")
            self.assertEqual(prep.returncode, 0, prep.stderr)
            prep_data = json.loads(prep.stdout)
            ui_prep = prep_data["teams"][0]
            self.assertEqual(
                ui_prep["profile"],
                {
                    "skills": [{"name": "a11y", "version": "1.0"}],
                    "maker": {"model": "gemini-2.5-pro", "family": "gemini"},
                    "checker": {"model": "claude-sonnet-4", "family": "claude"},
                    "review_docs": ["docs/design.md"],
                    "verify_script": "scripts/verify_ui.sh",
                },
            )

            st = _run_cli(repo, "teams", "status", "--job-id", "job-prof", "--json")
            self.assertEqual(st.returncode, 0, st.stderr)
            st_data = json.loads(st.stdout)
            ui_st = st_data["teams"][0]
            self.assertEqual(ui_st["profile"], ui_prep["profile"])


if __name__ == "__main__":
    unittest.main()
