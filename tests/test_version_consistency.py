import json
import re
import unittest
from pathlib import Path

import auto_developer
from auto_developer.cli import VERSION


class TestVersionConsistency(unittest.TestCase):
    def setUp(self):
        self.root_dir = Path(__file__).resolve().parents[1]

    def test_package_versions_match_0_10_0(self):
        """すべてのバージョン表記が 0.10.0 に統一されていること (REL-6)。"""
        expected_version = "0.10.0"

        # 1. auto_developer.__version__
        self.assertEqual(auto_developer.__version__, expected_version)

        # 2. cli.VERSION
        self.assertEqual(VERSION, expected_version)

        # 3. pyproject.toml
        pyproject_text = (self.root_dir / "pyproject.toml").read_text(encoding="utf-8")
        m = re.search(r'^version\s*=\s*["\']([^"\']+)["\']', pyproject_text, re.MULTILINE)
        self.assertIsNotNone(m, "version not found in pyproject.toml")
        assert m is not None
        self.assertEqual(m.group(1), expected_version)

        # 4. plugin.json
        plugin_data = json.loads((self.root_dir / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(plugin_data.get("version"), expected_version)

    def test_release_documents_exist_for_0_10_0(self):
        """CHANGELOG.md に [0.10.0] 節があり、docs/releases/v0.10.0.md が存在すること (REL-6)。"""
        # CHANGELOG.md
        changelog_text = (self.root_dir / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("## [0.10.0] - ", changelog_text)

        # docs/releases/v0.10.0.md
        rel_note = self.root_dir / "docs" / "releases" / "v0.10.0.md"
        self.assertTrue(rel_note.is_file(), "docs/releases/v0.10.0.md must exist")
        rel_text = rel_note.read_text(encoding="utf-8")
        self.assertIn("v0.10.0", rel_text)


if __name__ == "__main__":
    unittest.main()
