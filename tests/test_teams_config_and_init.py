from __future__ import annotations

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.cli import main
from auto_developer.dispatcher import TaskDispatcher
from auto_developer.manifest import ManifestValidationError, TeamManifest


class TestTeamsConfigValidation(unittest.TestCase):
    """TEAM-10: teams.json の読み込みと検証（plan, depends_on, owns 重なり, TEAMS_CONFIG_ERROR）。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def _write_teams_json(self, payload: dict[str, object]) -> Path:
        path = self.repo_dir / "teams.json"
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        return path

    def test_plan_field_default_and_custom(self) -> None:
        from auto_developer.manifest import load_teams_manifest

        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/core/**"]},
                {"id": "ui", "owns": ["src/ui/**"], "plan": "docs/ui_plan.md"},
            ],
        })
        manifest = load_teams_manifest(self.repo_dir)
        by_id = {t.id: t for t in manifest.teams}
        self.assertEqual(by_id["core"].plan, "plans/core.md")
        self.assertEqual(by_id["ui"].plan, "docs/ui_plan.md")

    def test_rejects_invalid_plan_paths_and_duplicate_plan(self) -> None:
        from auto_developer.manifest import load_teams_manifest

        bad_plans: list[object] = ["", "   ", 123, "/abs/plan.md", "../escape.md"]
        for bad_plan in bad_plans:
            self._write_teams_json({
                "version": 1,
                "project": "demo",
                "teams": [{"id": "core", "owns": ["src/core/**"], "plan": bad_plan}],
            })
            with self.subTest(plan=bad_plan), self.assertRaises(ManifestValidationError):
                load_teams_manifest(self.repo_dir)

        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/core/**"], "plan": "plans/shared.md"},
                {"id": "ui", "owns": ["src/ui/**"], "plan": "plans/shared.md"},
            ],
        })
        with self.assertRaises(ManifestValidationError):
            load_teams_manifest(self.repo_dir)

    def test_rejects_non_empty_depends_on(self) -> None:
        from auto_developer.manifest import load_teams_manifest

        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/core/**"], "depends_on": []},
                {"id": "ui", "owns": ["src/ui/**"], "depends_on": ["core"]},
            ],
        })
        with self.assertRaises(ManifestValidationError) as ctx:
            load_teams_manifest(self.repo_dir)
        self.assertIn("depends_on", str(ctx.exception))

    def test_rejects_overlapping_owns_patterns(self) -> None:
        from auto_developer.manifest import load_teams_manifest

        overlapping_cases = [
            (["src/core/**"], ["src/core/**"]),
            (["src/**"], ["src/core/**"]),
            (["src/core/**"], ["src/core/app.py"]),
            (["**"], ["docs/**"]),
            (["src/**/*.py"], ["src/ui/*.py"]),
            (["src/a*"], ["src/*b"]),
            (["src/a?"], ["src/?b"]),
        ]
        for owns_a, owns_b in overlapping_cases:
            self._write_teams_json({
                "version": 1,
                "project": "demo",
                "teams": [
                    {"id": "core", "owns": owns_a},
                    {"id": "ui", "owns": owns_b},
                ],
            })
            with (
                self.subTest(owns_a=owns_a, owns_b=owns_b),
                self.assertRaises(ManifestValidationError),
            ):
                load_teams_manifest(self.repo_dir)

    def test_allows_disjoint_owns_patterns(self) -> None:
        from auto_developer.manifest import load_teams_manifest

        disjoint_cases = [
            (["src/core/**"], ["src/ui/**"]),
            (["a/b/**"], ["a/b2/**"]),
            (["src/core/*"], ["src/core/sub/**"]),
            (["src/*.py"], ["src/*.md"]),
            (["src/a*"], ["src/b*"]),
        ]
        for owns_a, owns_b in disjoint_cases:
            self._write_teams_json({
                "version": 1,
                "project": "demo",
                "teams": [
                    {"id": "core", "owns": owns_a},
                    {"id": "ui", "owns": owns_b},
                ],
            })
            with self.subTest(owns_a=owns_a, owns_b=owns_b):
                manifest = load_teams_manifest(self.repo_dir)
                self.assertEqual(len(manifest.teams), 2)

    def test_rejects_plan_included_in_other_team_owns(self) -> None:
        from auto_developer.manifest import load_teams_manifest

        # Default plan plans/ui.md collides with core's owns=["plans/**"]
        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/core/**", "plans/**"]},
                {"id": "ui", "owns": ["src/ui/**"]},
            ],
        })
        with self.assertRaises(ManifestValidationError):
            load_teams_manifest(self.repo_dir)

        # Custom plan inside another team's owns
        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/core/**"]},
                {"id": "ui", "owns": ["src/ui/**"], "plan": "src/core/PLAN.md"},
            ],
        })
        with self.assertRaises(ManifestValidationError):
            load_teams_manifest(self.repo_dir)

        # Own plan inside own owns is allowed
        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/core/**", "plans/core.md"]},
                {"id": "ui", "owns": ["src/ui/**"]},
            ],
        })
        manifest = load_teams_manifest(self.repo_dir)
        self.assertEqual(len(manifest.teams), 2)

    def test_cli_helper_prints_teams_config_error_and_returns_exit_2(self) -> None:
        from auto_developer.cli import load_teams_manifest_or_error

        # 1. Missing teams.json
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            manifest, code = load_teams_manifest_or_error(self.repo_dir)
        self.assertIsNone(manifest)
        self.assertEqual(code, 2)
        self.assertIn("TEAMS_CONFIG_ERROR:", stderr_buf.getvalue())

        # 2. Non-empty depends_on
        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/core/**"]},
                {"id": "ui", "owns": ["src/ui/**"], "depends_on": ["core"]},
            ],
        })
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            manifest, code = load_teams_manifest_or_error(self.repo_dir)
        self.assertIsNone(manifest)
        self.assertEqual(code, 2)
        self.assertIn("TEAMS_CONFIG_ERROR:", stderr_buf.getvalue())

        # 3. Overlapping owns
        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/**"]},
                {"id": "ui", "owns": ["src/ui/**"]},
            ],
        })
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            manifest, code = load_teams_manifest_or_error(self.repo_dir)
        self.assertIsNone(manifest)
        self.assertEqual(code, 2)
        self.assertIn("TEAMS_CONFIG_ERROR:", stderr_buf.getvalue())

        # 4. Valid teams.json
        self._write_teams_json({
            "version": 1,
            "project": "demo",
            "teams": [
                {"id": "core", "owns": ["src/core/**"]},
                {"id": "ui", "owns": ["src/ui/**"]},
            ],
        })
        stderr_buf = io.StringIO()
        with contextlib.redirect_stderr(stderr_buf):
            manifest, code = load_teams_manifest_or_error(self.repo_dir)
        self.assertIsInstance(manifest, TeamManifest)
        self.assertEqual(code, 0)
        self.assertEqual(stderr_buf.getvalue(), "")


class TestTeamsInitCli(unittest.TestCase):
    """TEAM-12: auto-dev teams init の受入テスト。"""

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.temp_dir.name)

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_teams_init_default_creates_manifest_and_plans(self) -> None:
        from auto_developer.manifest import load_teams_manifest

        stdout_buf = io.StringIO()
        with contextlib.redirect_stdout(stdout_buf):
            exit_code = main(["--work-dir", str(self.repo_dir), "teams", "init"])
        self.assertEqual(exit_code, 0)

        teams_json = self.repo_dir / "teams.json"
        core_plan = self.repo_dir / "plans" / "core.md"
        ui_plan = self.repo_dir / "plans" / "ui.md"
        self.assertTrue(teams_json.is_file())
        self.assertTrue(core_plan.is_file())
        self.assertTrue(ui_plan.is_file())

        manifest = load_teams_manifest(self.repo_dir)
        self.assertEqual([t.id for t in manifest.teams], ["core", "ui"])
        self.assertEqual([t.plan for t in manifest.teams], ["plans/core.md", "plans/ui.md"])

        dispatcher = TaskDispatcher()
        self.assertGreater(len(dispatcher.parse_plan(core_plan)), 0)
        self.assertGreater(len(dispatcher.parse_plan(ui_plan)), 0)

    def test_teams_init_with_custom_teams_flag(self) -> None:
        from auto_developer.manifest import load_teams_manifest

        stdout_buf = io.StringIO()
        with contextlib.redirect_stdout(stdout_buf):
            exit_code = main([
                "--work-dir",
                str(self.repo_dir),
                "teams",
                "init",
                "--teams",
                "api, web, docs",
            ])
        self.assertEqual(exit_code, 0)

        manifest = load_teams_manifest(self.repo_dir)
        self.assertEqual([t.id for t in manifest.teams], ["api", "web", "docs"])
        for tid in ("api", "web", "docs"):
            self.assertTrue((self.repo_dir / "plans" / f"{tid}.md").is_file())

    def test_teams_init_does_not_overwrite_existing_files(self) -> None:
        teams_json = self.repo_dir / "teams.json"
        existing_teams = json.dumps({
            "version": 1,
            "project": "custom",
            "teams": [
                {"id": "core", "owns": ["custom_core/**"]},
                {"id": "ui", "owns": ["custom_ui/**"]},
            ],
        })
        teams_json.write_text(existing_teams, encoding="utf-8")

        core_plan = self.repo_dir / "plans" / "core.md"
        core_plan.parent.mkdir(parents=True, exist_ok=True)
        existing_plan = "# CUSTOM CORE PLAN\n- [ ] 1. Existing task\n"
        core_plan.write_text(existing_plan, encoding="utf-8")

        stdout_buf = io.StringIO()
        with contextlib.redirect_stdout(stdout_buf):
            exit_code = main([
                "--work-dir",
                str(self.repo_dir),
                "teams",
                "init",
                "--teams",
                "core,ui",
            ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(teams_json.read_text(encoding="utf-8"), existing_teams)
        self.assertEqual(core_plan.read_text(encoding="utf-8"), existing_plan)
        self.assertTrue((self.repo_dir / "plans" / "ui.md").is_file())

    def test_teams_init_rejects_invalid_teams_argument(self) -> None:
        for bad_arg in ["", "   ", "InvalidUpper", "core,core"]:
            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with (
                self.subTest(teams=bad_arg),
                contextlib.redirect_stdout(stdout_buf),
                contextlib.redirect_stderr(stderr_buf),
            ):
                exit_code = main([
                    "--work-dir",
                    str(self.repo_dir),
                    "teams",
                    "init",
                    "--teams",
                    bad_arg,
                ])
                self.assertEqual(exit_code, 2)
                self.assertIn("TEAMS_CONFIG_ERROR:", stderr_buf.getvalue())

    def test_teams_init_rejects_invalid_existing_teams_json_without_writing(self) -> None:
        """既存 teams.json に不正な plan パスや検証エラーがある場合、書き込まずに終了コード 2 で拒否する。"""
        with tempfile.TemporaryDirectory() as outer_tmp:
            outer_dir = Path(outer_tmp)
            workspace = outer_dir / "repo"
            workspace.mkdir()
            escaped_target = outer_dir / "escaped.md"

            (workspace / "teams.json").write_text(
                json.dumps({
                    "version": 1,
                    "project": "demo",
                    "teams": [
                        {"id": "core", "owns": ["src/core/**"], "plan": "../escaped.md"},
                    ],
                }),
                encoding="utf-8",
            )

            stdout_buf = io.StringIO()
            stderr_buf = io.StringIO()
            with (
                contextlib.redirect_stdout(stdout_buf),
                contextlib.redirect_stderr(stderr_buf),
            ):
                exit_code = main(["--work-dir", str(workspace), "teams", "init"])

            self.assertEqual(exit_code, 2)
            self.assertIn("TEAMS_CONFIG_ERROR:", stderr_buf.getvalue())
            self.assertFalse(escaped_target.exists())

    def test_teams_init_rejects_mismatched_teams_when_teams_json_exists(self) -> None:
        """既存 teams.json がある場合、存在しないチームを --teams で指定しても孤立 plan を作らず拒否する。"""
        (self.repo_dir / "teams.json").write_text(
            json.dumps({
                "version": 1,
                "project": "custom",
                "teams": [
                    {"id": "core", "owns": ["src/core/**"]},
                    {"id": "ui", "owns": ["src/ui/**"]},
                ],
            }),
            encoding="utf-8",
        )

        stdout_buf = io.StringIO()
        stderr_buf = io.StringIO()
        with (
            contextlib.redirect_stdout(stdout_buf),
            contextlib.redirect_stderr(stderr_buf),
        ):
            exit_code = main([
                "--work-dir",
                str(self.repo_dir),
                "teams",
                "init",
                "--teams",
                "api",
            ])

        self.assertEqual(exit_code, 2)
        self.assertIn("TEAMS_CONFIG_ERROR:", stderr_buf.getvalue())
        self.assertFalse((self.repo_dir / "plans" / "api.md").exists())

    def test_legacy_from_dict_preserves_existing_acceptance_range(self) -> None:
        """allow_depends_on=True（既定・4.8 TEAM 経路）の受理範囲が変わらないこと。"""
        legacy_data = {
            "version": 1,
            "project": "legacy",
            "teams": [
                {"id": "core", "owns": ["src/**"], "depends_on": []},
                {"id": "ui", "owns": ["src/ui/**"], "depends_on": ["core"]},
            ],
        }
        manifest = TeamManifest.from_dict(legacy_data)
        self.assertEqual([t.id for t in manifest.teams], ["core", "ui"])
        with self.assertRaises(ManifestValidationError):
            TeamManifest.from_dict(legacy_data, allow_depends_on=False)

    def test_boundary_exports_public_match_path(self) -> None:
        from auto_developer.boundary import match_path

        self.assertTrue(match_path("src/core/app.py", "src/core/**"))
        self.assertFalse(match_path("src/ui/view.py", "src/core/**"))


if __name__ == "__main__":
    unittest.main()
