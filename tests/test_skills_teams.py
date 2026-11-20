from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT_DIR / "src"
NIGHTLY_SETUP_SKILL = ROOT_DIR / "skills" / "nightly-setup" / "SKILL.md"
NIGHTLY_RUN_SKILL = ROOT_DIR / "skills" / "nightly-run" / "SKILL.md"


def _extract_frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return ""
    return match.group(1)


class TestSkillsTeamsDocumentation(unittest.TestCase):
    """TEAM-18: /nightly-setup と /nightly-run スキルのマルチチーム手順検証。"""

    def test_nightly_setup_prompts_for_teams_and_documents_teams_init(self) -> None:
        text = NIGHTLY_SETUP_SKILL.read_text(encoding="utf-8")
        fm = _extract_frontmatter(text)
        self.assertIn("name: nightly-setup", fm)
        self.assertIn("description:", fm)

        # 既存単体初期化の維持
        self.assertIn("auto-dev init", text)
        self.assertIn("scripts/verify_local.sh", text)
        self.assertIn("PLAN.md", text)
        self.assertIn("SPEC.md", text)
        self.assertIn(".gitignore", text)

        # TEAM-18: チームを使うか尋ねる指示と teams init の案内
        lower = text.lower()
        self.assertTrue(
            ("team" in lower or "チーム" in text) and ("ask" in lower or "尋ね" in text or "確認" in text),
            "nightly-setup must instruct asking the user whether to use teams",
        )
        self.assertIn("auto-dev teams init", text)
        self.assertIn("--teams", text)
        self.assertIn("teams.json", text)
        self.assertIn("plans/", text)

    def test_nightly_run_documents_team_path_prepare_loop_integrate_and_teams_run(self) -> None:
        text = NIGHTLY_RUN_SKILL.read_text(encoding="utf-8")
        fm = _extract_frontmatter(text)
        self.assertIn("name: nightly-run", fm)
        self.assertIn("description:", fm)

        # 既存の単一リポジトリ直列ループの維持
        for cmd in (
            "pre-flight",
            "verify-step",
            "record-review",
            "commit-step",
            "rollback-step",
            "auto-dev report",
            "auto-dev metrics",
            "auto-dev bundle",
            "auto-dev verify-bundle",
        ):
            self.assertIn(cmd, text, f"Missing existing command '{cmd}' in nightly-run SKILL.md")

        # TEAM-18: teams.json 分岐、teams prepare --json、各 worktree でのループ、teams integrate --job-id
        self.assertIn("teams.json", text)
        self.assertIn("auto-dev teams prepare --json", text)
        self.assertIn("auto-dev teams integrate --job-id", text)
        self.assertIn("worktree", text.lower())

        # 順序検証: prepare -> integrate の順でチーム経路が書かれていること
        prep_idx = text.index("auto-dev teams prepare --json")
        integ_idx = text.index("auto-dev teams integrate --job-id")
        self.assertLess(prep_idx, integ_idx, "teams prepare must precede teams integrate")

        # チーム文脈・不変条件 (INV-1, INV-4, INV-6, INV-9) の言及
        self.assertIn("owns", text)
        self.assertIn("quarantine", text.lower())

        # TEAM-18: 外部 CLI で Maker / Checker を起動する場合は teams run を使ってよいと案内
        self.assertIn("auto-dev teams run", text)
        self.assertIn("--maker-cmd", text)
        self.assertIn("--checker-cmd", text)

        # 既存の準備済みジョブが残っている場合の回復・片付けコマンド案内
        self.assertIn("teams status", text)
        self.assertIn("teams cleanup", text)
        self.assertIn("teams recover", text)

    def test_documented_cli_subcommands_exist_in_cli(self) -> None:
        """スキルに記載した auto-dev サブコマンドが実際の CLI に存在すること。"""
        env = {"PYTHONPATH": str(SRC_DIR)}
        for subcmd_args in (
            ["init", "--help"],
            ["teams", "init", "--help"],
            ["teams", "prepare", "--help"],
            ["teams", "integrate", "--help"],
            ["teams", "run", "--help"],
            ["teams", "status", "--help"],
            ["teams", "cleanup", "--help"],
            ["teams", "recover", "--help"],
            ["pre-flight", "--help"],
            ["verify-step", "--help"],
            ["record-review", "--help"],
            ["commit-step", "--help"],
            ["rollback-step", "--help"],
        ):
            with self.subTest(subcommand=subcmd_args):
                proc = subprocess.run(
                    [sys.executable, "-m", "auto_developer.cli", *subcmd_args],
                    cwd=ROOT_DIR,
                    env=env,
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(
                    proc.returncode,
                    0,
                    f"CLI help failed for {subcmd_args}: {proc.stderr}",
                )


if __name__ == "__main__":
    unittest.main()
