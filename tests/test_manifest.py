import json
import sys
import tempfile
import unittest
from pathlib import Path

src_dir = str(Path(__file__).resolve().parents[1] / "src")
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)

from auto_developer.manifest import ManifestValidationError, TeamManifest


class TestTeamManifest(unittest.TestCase):
    def test_from_dict_valid(self):
        data = {
            "version": 1,
            "project": "auto-developer-dogfood",
            "base": {
                "ref": "main",
                "require_clean": True,
            },
            "contracts": {
                "path": "contracts/",
                "freeze_before_execution": True,
            },
            "teams": [
                {
                    "id": "core",
                    "owns": ["src/auto_developer/core/**"],
                    "depends_on": [],
                    "max_parallel_tasks": 1,
                },
                {
                    "id": "cli",
                    "owns": ["src/auto_developer/cli/**"],
                    "depends_on": ["core"],
                    "max_parallel_tasks": 1,
                },
                {
                    "id": "docs",
                    "owns": ["docs/**"],
                    "depends_on": [],
                    "max_parallel_tasks": 1,
                },
            ],
            "integration": {
                "strategy": "merge-sequential",
                "require_team_evidence": True,
                "reverify_after_merge": True,
                "update_main": False,
            },
        }
        manifest = TeamManifest.from_dict(data)
        self.assertEqual(manifest.version, 1)
        self.assertEqual(manifest.project, "auto-developer-dogfood")
        self.assertEqual(len(manifest.teams), 3)

        # トポロジカルソート確認: core と docs は先行可能、cli は core の後
        stages = manifest.get_execution_stages()
        self.assertEqual(len(stages), 2)
        self.assertIn("core", stages[0])
        self.assertIn("docs", stages[0])
        self.assertEqual(stages[1], ["cli"])

    def test_missing_required_fields_raises(self):
        with self.assertRaises(ManifestValidationError):
            TeamManifest.from_dict({"project": "no-version"})

    def test_duplicate_team_id_raises(self):
        data = {
            "version": 1,
            "project": "test",
            "teams": [
                {"id": "team1", "owns": ["a/**"]},
                {"id": "team1", "owns": ["b/**"]},
            ],
        }
        with self.assertRaises(ManifestValidationError):
            TeamManifest.from_dict(data)

    def test_unknown_dependency_raises(self):
        data = {
            "version": 1,
            "project": "test",
            "teams": [
                {"id": "team1", "owns": ["a/**"], "depends_on": ["non_existent_team"]},
            ],
        }
        with self.assertRaises(ManifestValidationError):
            TeamManifest.from_dict(data)

    def test_circular_dependency_raises(self):
        data = {
            "version": 1,
            "project": "test",
            "teams": [
                {"id": "team-a", "owns": ["a/**"], "depends_on": ["team-b"]},
                {"id": "team-b", "owns": ["b/**"], "depends_on": ["team-a"]},
            ],
        }
        with self.assertRaises(ManifestValidationError):
            TeamManifest.from_dict(data)

    def test_invalid_team_id_format_raises(self):
        invalid_ids = ["TeamA", "team/core", "team@core", "-invalid", "1team", "", "   "]
        for t_id in invalid_ids:
            data = {
                "version": 1,
                "project": "test",
                "teams": [{"id": t_id, "owns": ["a/**"]}],
            }
            with self.subTest(team_id=t_id), self.assertRaises(ManifestValidationError):
                TeamManifest.from_dict(data)

    def test_empty_or_invalid_owns_raises(self):
        cases = [
            [],  # 空リスト
            [""],  # 空文字列
            [123],  # 非文字列
        ]
        for owns in cases:
            data = {
                "version": 1,
                "project": "test",
                "teams": [{"id": "team-a", "owns": owns}],
            }
            with self.subTest(owns=owns), self.assertRaises(ManifestValidationError):
                TeamManifest.from_dict(data)

    def test_invalid_max_parallel_tasks_raises(self):
        for val in [0, -1, "1", None]:
            data = {
                "version": 1,
                "project": "test",
                "teams": [{"id": "team-a", "owns": ["a/**"], "max_parallel_tasks": val}],
            }
            with self.subTest(max_parallel_tasks=val), self.assertRaises(ManifestValidationError):
                TeamManifest.from_dict(data)

    def test_boolean_type_safety_raises(self):
        # 文字列 "false" や "true" は厳格に拒否するべき
        data = {
            "version": 1,
            "project": "test",
            "base": {"ref": "main", "require_clean": "false"},
            "teams": [{"id": "team-a", "owns": ["a/**"]}],
        }
        with self.assertRaises(ManifestValidationError):
            TeamManifest.from_dict(data)

    def test_load_from_json_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            file_path = Path(tmpdir) / "teams.json"
            file_path.write_text(
                json.dumps({
                    "version": 1,
                    "project": "test-file",
                    "teams": [{"id": "t1", "owns": ["t1/**"]}],
                }),
                encoding="utf-8",
            )
            manifest = TeamManifest.load(file_path)
            self.assertEqual(manifest.project, "test-file")
            self.assertEqual(manifest.teams[0].id, "t1")


if __name__ == "__main__":
    unittest.main()
