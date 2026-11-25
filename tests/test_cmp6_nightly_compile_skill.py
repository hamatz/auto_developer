from __future__ import annotations

import json
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
PLUGIN_JSON = ROOT_DIR / "plugin.json"
NIGHTLY_COMPILE_SKILL = ROOT_DIR / "skills" / "nightly-compile" / "SKILL.md"
CHECK_DOC_COMMANDS = ROOT_DIR / "scripts" / "check_doc_commands.py"


def _extract_frontmatter(text: str) -> str:
    match = re.match(r"^---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        return ""
    return match.group(1)


class TestCmp6NightlyCompileSkill(unittest.TestCase):
    """CMP-6: /nightly-compile スキルと plugin.json の検証。"""

    def test_plugin_json_suggests_nightly_compile(self) -> None:
        data = json.loads(PLUGIN_JSON.read_text(encoding="utf-8"))
        prompts = data.get("suggestedPrompts", [])
        self.assertIsInstance(prompts, list)
        for existing in (
            "/nightly-run",
            "/nightly-setup",
            "/nightly-bundle",
            "/nightly-verify",
        ):
            self.assertIn(existing, prompts)
        self.assertIn("/nightly-compile", prompts)

    def test_skill_frontmatter_and_inputs(self) -> None:
        self.assertTrue(
            NIGHTLY_COMPILE_SKILL.is_file(),
            f"Missing skill file: {NIGHTLY_COMPILE_SKILL}",
        )
        text = NIGHTLY_COMPILE_SKILL.read_text(encoding="utf-8")
        fm = _extract_frontmatter(text)
        self.assertIn("name: nightly-compile", fm)
        self.assertIn("description:", fm)

        # 入力: EGM 設計書と文書群の組み立て先
        self.assertIn("docs/designs/", text)
        for doc_name in ("goal.md", "architecture.md", "decision.md", "todo.md"):
            self.assertIn(doc_name, text)
        self.assertIn(".dev/compile/", text)
        self.assertIn(".design.md", text)

    def test_skill_workflow_order_and_requirements(self) -> None:
        text = NIGHTLY_COMPILE_SKILL.read_text(encoding="utf-8")

        # 流れの順番: 見出しの確認 -> IR の下書き -> 点検 -> 査読 -> 書き出し
        idx_outline = text.index("auto-dev compile --outline")
        idx_ir = text.index("auto-dev-ir/1")
        idx_check = text.index("auto-dev compile --check")
        idx_review = text.index(".review.txt")
        idx_emit = text.index("auto-dev compile --emit")

        self.assertLess(idx_outline, idx_ir, "--outline must precede IR draft")
        self.assertLess(idx_ir, idx_check, "IR draft must precede --check")
        self.assertLess(idx_check, idx_review, "--check must precede independent review")
        self.assertLess(idx_review, idx_emit, "independent review must precede --emit")

        # IR 下書きの要件
        self.assertIn(".ir.json", text)
        self.assertIn("forbids", text)
        self.assertIn("open_questions", text)

        # 独立した査読の要件
        self.assertIn("DECISION: APPROVE", text)
        self.assertIn("DECISION: REJECT", text)
        for rule_id in ("L-2", "L-3", "L-4"):
            self.assertIn(rule_id, text)

        # 書き出し・引き渡しと INV-12
        self.assertIn("contract.md", text)
        self.assertIn("INV-12", text)
        for protected in ("SPEC.md", "PLAN.md", "teams.json"):
            self.assertIn(protected, text)

    def test_skill_stop_conditions(self) -> None:
        text = NIGHTLY_COMPILE_SKILL.read_text(encoding="utf-8")
        # 1. 足りない節 (exit 1 / 終了コード 1)
        self.assertTrue(
            "exit 1" in text.lower() or "終了コード 1" in text,
            "Must document stopping on --outline exit code 1 (missing sections)",
        )
        # 2. L-9 / open_questions
        self.assertIn("L-9", text)
        # 3. 査読の REJECT 2回
        self.assertTrue(
            "2回" in text or "twice" in text.lower(),
            "Must document stopping after 2 consecutive REJECT reviews",
        )

    def test_check_doc_commands_verifies_nightly_compile_skill(self) -> None:
        proc = subprocess.run(
            [sys.executable, str(CHECK_DOC_COMMANDS)],
            cwd=ROOT_DIR,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            proc.returncode,
            0,
            f"check_doc_commands.py failed:\nstdout={proc.stdout}\nstderr={proc.stderr}",
        )
        self.assertIn("OK:", proc.stdout)


if __name__ == "__main__":
    unittest.main()
