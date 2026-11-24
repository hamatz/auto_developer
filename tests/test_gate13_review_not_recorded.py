"""GATE-13 (Step 5): review_input イベント、status の REVIEW_NOT_RECORDED、スキル手順を検証する。"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main

ROOT_DIR = Path(__file__).resolve().parents[1]
ORCH = ROOT_DIR / "scripts" / "nightly_orchestrator.py"
NIGHTLY_RUN_SKILL = ROOT_DIR / "skills" / "nightly-run" / "SKILL.md"


class TestGate13ReviewNotRecorded(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        for cmd in (
            ["git", "init", "-b", "main"],
            ["git", "config", "user.name", "Tester"],
            ["git", "config", "user.email", "tester@example.com"],
        ):
            subprocess.run(cmd, cwd=self.repo, check=True, capture_output=True)
        self.plan = self.repo / "PLAN.md"
        self.plan.write_text(
            "# PLAN\n\n## 現在の目標\n- Phase 99: test\n\n## ステップ\n- [ ] 1. テストタスク\n",
            encoding="utf-8",
        )
        self.spec_text = "# SPEC\n\n- [ ] **APP-1**: x は 1 である。\n"
        (self.repo / "SPEC.md").write_text(self.spec_text, encoding="utf-8")
        self.ok_sh = self.repo / "ok.sh"
        self.ok_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        self.ok_sh.chmod(0o755)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=self.repo, check=True, capture_output=True)
        self.checkpoint = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout.strip()
        (self.repo / "app.py").write_text("x = 1\n", encoding="utf-8")
        self.io = Path(self._tmp.name) / "checker_io"
        self.io.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _orch(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(ORCH), "--plan", str(self.plan), *args],
            cwd=self.repo,
            capture_output=True,
            text=True,
            check=False,
            timeout=120,
        )

    def _verify(self, task_id: str = "1") -> None:
        res = self._orch(
            "--verify-script", str(self.ok_sh),
            "verify-step", "--checkpoint", self.checkpoint, "--task-id", task_id,
        )
        self.assertEqual(res.returncode, 0, f"verify-step failed: {res.stderr}\n{res.stdout}")

    def _status_json(self) -> dict[str, object]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli_main(["--work-dir", str(self.repo), "status", "--json"])
        self.assertEqual(rc, 0)
        return json.loads(out.getvalue())

    def _read_events(self) -> list[dict[str, object]]:
        events_path = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        if not events_path.is_file():
            return []
        return [
            json.loads(line)
            for line in events_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_review_input_writes_event_and_status_flags_unrecorded_review(self) -> None:
        self._verify()
        out_dir = self.io / "r1"
        res_in = self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir))
        self.assertEqual(res_in.returncode, 0, res_in.stderr)

        diff_path = out_dir / "diff.patch"
        self.assertTrue(diff_path.is_file())
        expected_sha = hashlib.sha256(diff_path.read_bytes()).hexdigest()

        review_input_events = [e for e in self._read_events() if e.get("event") == "review_input"]
        self.assertEqual(len(review_input_events), 1)
        ev = review_input_events[0]
        self.assertEqual(ev.get("task_id"), "1")
        self.assertEqual(ev.get("checkpoint"), self.checkpoint)
        self.assertEqual(ev.get("diff_sha256"), expected_sha)
        self.assertNotIn("evidence_token", ev)

        # record-review 前は REVIEW_NOT_RECORDED が attention[] に入る
        snap_before = self._status_json()
        att_before = snap_before["attention"]
        assert isinstance(att_before, list)
        kinds_before = [item["kind"] for item in att_before]
        self.assertIn("REVIEW_NOT_RECORDED", kinds_before)

        # record-review で記録すると消える
        spec_file = out_dir / "spec_excerpt.md"
        spec_file.write_text(self.spec_text, encoding="utf-8")
        checker_out = out_dir / "checker_output.txt"
        checker_out.write_text("ok\nDECISION: APPROVE\n", encoding="utf-8")
        res_rec = self._orch(
            "record-review", "--task-id", "1",
            "--checker-output", str(checker_out),
            "--diff-file", str(diff_path),
            "--spec-file", str(spec_file),
            "--maker-model", "gemini-4-hydrogen-medium",
            "--checker-model", "claude-sonnet-5-5",
            "--reason", "looks good",
        )
        self.assertEqual(res_rec.returncode, 0, res_rec.stderr)

        snap_after = self._status_json()
        att_after = snap_after["attention"]
        assert isinstance(att_after, list)
        kinds_after = [item["kind"] for item in att_after]
        self.assertNotIn("REVIEW_NOT_RECORDED", kinds_after)

    def test_undecided_review_history_counts_toward_recorded_reviews(self) -> None:
        self._verify()
        out_dir1 = self.io / "r1"
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir1)).returncode, 0)

        spec_file1 = out_dir1 / "spec_excerpt.md"
        spec_file1.write_text(self.spec_text, encoding="utf-8")
        truncated_out = out_dir1 / "checker_output.txt"
        truncated_out.write_text("truncated output without decision line", encoding="utf-8")
        res_undecided = self._orch(
            "record-review", "--task-id", "1",
            "--checker-output", str(truncated_out),
            "--diff-file", str(out_dir1 / "diff.patch"),
            "--spec-file", str(spec_file1),
            "--maker-model", "gemini-4-hydrogen-medium",
            "--checker-model", "claude-sonnet-5-5",
            "--reason", "truncated",
        )
        self.assertEqual(res_undecided.returncode, 4)

        # 判定の決まらない出力も履歴に残るので REVIEW_NOT_RECORDED は消える
        snap1 = self._status_json()
        att1 = snap1["attention"]
        assert isinstance(att1, list)
        self.assertNotIn("REVIEW_NOT_RECORDED", [item["kind"] for item in att1])

        # 同じ Evidence で 2 回目の review-input を呼ぶと、履歴 1 < 入力 2 で再び REVIEW_NOT_RECORDED が出る
        out_dir2 = self.io / "r2"
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir2)).returncode, 0)
        snap2 = self._status_json()
        att2 = snap2["attention"]
        assert isinstance(att2, list)
        self.assertIn("REVIEW_NOT_RECORDED", [item["kind"] for item in att2])

        # 2 回目の出力を記録すれば再び消える
        spec_file2 = out_dir2 / "spec_excerpt.md"
        spec_file2.write_text(self.spec_text, encoding="utf-8")
        ok_out = out_dir2 / "checker_output.txt"
        ok_out.write_text("ok\nDECISION: APPROVE\n", encoding="utf-8")
        res_ok = self._orch(
            "record-review", "--task-id", "1",
            "--checker-output", str(ok_out),
            "--diff-file", str(out_dir2 / "diff.patch"),
            "--spec-file", str(spec_file2),
            "--maker-model", "gemini-4-hydrogen-medium",
            "--checker-model", "claude-sonnet-5-5",
            "--reason", "ok",
        )
        self.assertEqual(res_ok.returncode, 0, res_ok.stderr)
        snap3 = self._status_json()
        att3 = snap3["attention"]
        assert isinstance(att3, list)
        self.assertNotIn("REVIEW_NOT_RECORDED", [item["kind"] for item in att3])

    def test_reverify_resets_attention_for_latest_evidence(self) -> None:
        self._verify()
        out_dir = self.io / "r1"
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir)).returncode, 0)
        att_before = self._status_json()["attention"]
        assert isinstance(att_before, list)
        self.assertIn("REVIEW_NOT_RECORDED", [item["kind"] for item in att_before])

        # 再検証すると最新の Evidence が新しくなり、その Evidence に対する review_input は 0 件になる
        (self.repo / "app.py").write_text("x = 2\n", encoding="utf-8")
        self._verify()
        att_after = self._status_json()["attention"]
        assert isinstance(att_after, list)
        self.assertNotIn("REVIEW_NOT_RECORDED", [item["kind"] for item in att_after])

    def test_review_input_succeeds_even_when_event_write_fails_inv8(self) -> None:
        self._verify()
        blocked = self.repo / ".git" / "nightly_evidence" / "events.jsonl"
        if blocked.is_file():
            blocked.unlink()
        blocked.mkdir(parents=True)
        out_dir = self.io / "inv8"
        res_in = self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir))
        self.assertEqual(res_in.returncode, 0, res_in.stderr)
        self.assertTrue((out_dir / "diff.patch").is_file())
        self.assertIn("METRICS_WARNING", res_in.stderr)

    def test_review_input_does_not_alter_existing_metrics_counts(self) -> None:
        self._verify()
        out_dir = self.io / "m1"
        self.assertEqual(self._orch("review-input", "--task-id", "1", "--out-dir", str(out_dir)).returncode, 0)

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            rc = cli_main(["--work-dir", str(self.repo), "metrics", "--json"])
        self.assertEqual(rc, 0)
        data = json.loads(out.getvalue())
        self.assertEqual(data["metrics"]["checker_rejects"]["value"], 0)
        self.assertEqual(data["metrics"]["tasks_committed"]["value"], 0)

    def test_team_worktree_review_not_recorded_and_cleared(self) -> None:
        example_dir = ROOT_DIR / "examples" / "two-teams"
        with tempfile.TemporaryDirectory() as tmp:
            trial = Path(tmp) / "two-teams"
            shutil.copytree(example_dir, trial)
            for cmd in (
                ["git", "init", "-b", "main"],
                ["git", "config", "user.name", "Test"],
                ["git", "config", "user.email", "test@example.com"],
                ["git", "add", "-A"],
                ["git", "commit", "-m", "init"],
            ):
                subprocess.run(cmd, cwd=trial, check=True, capture_output=True)

            self.assertEqual(cli_main(["--work-dir", str(trial), "teams", "prepare"]), 0)

            wt_core = trial / ".dev" / "worktrees" / "core"
            head_core = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=wt_core, check=True, capture_output=True, text=True
            ).stdout.strip()
            (wt_core / "src" / "calc" / "__init__.py").write_text(
                "def add(a: int, b: int) -> int:\n    return a + b\n",
                encoding="utf-8",
            )
            ok_sh = Path(tmp) / "ok.sh"
            ok_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            ok_sh.chmod(0o755)

            res_v = subprocess.run(
                [
                    sys.executable, str(ORCH),
                    "--work-dir", str(wt_core),
                    "--verify-script", str(ok_sh),
                    "verify-step", "--checkpoint", head_core, "--task-id", "1",
                ],
                cwd=trial, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(res_v.returncode, 0, res_v.stderr)

            io_dir = Path(tmp) / "team_io"
            res_in = subprocess.run(
                [
                    sys.executable, str(ORCH),
                    "--work-dir", str(wt_core),
                    "review-input", "--task-id", "1", "--out-dir", str(io_dir),
                ],
                cwd=trial, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(res_in.returncode, 0, res_in.stderr)

            out_before = io.StringIO()
            with contextlib.redirect_stdout(out_before):
                rc1 = cli_main(["--work-dir", str(trial), "status", "--json"])
            self.assertEqual(rc1, 0)
            snap_before = json.loads(out_before.getvalue())
            review_alerts = [
                item for item in snap_before["attention"]
                if item.get("kind") == "REVIEW_NOT_RECORDED"
            ]
            self.assertEqual(len(review_alerts), 1)
            self.assertEqual(review_alerts[0].get("team_id"), "core")
            self.assertEqual(review_alerts[0].get("task_id"), "1")

            spec_text = (wt_core / "SPEC.md").read_text(encoding="utf-8")
            spec_file = io_dir / "spec_excerpt.md"
            spec_file.write_text(spec_text.strip(), encoding="utf-8")
            checker_out = io_dir / "checker_output.txt"
            checker_out.write_text("ok\nDECISION: APPROVE\n", encoding="utf-8")
            res_rec = subprocess.run(
                [
                    sys.executable, str(ORCH),
                    "--work-dir", str(wt_core),
                    "record-review", "--task-id", "1",
                    "--checker-output", str(checker_out),
                    "--diff-file", str(io_dir / "diff.patch"),
                    "--spec-file", str(spec_file),
                    "--maker-model", "gemini-4-hydrogen-medium",
                    "--checker-model", "claude-sonnet-5-5",
                    "--reason", "looks good",
                ],
                cwd=trial, capture_output=True, text=True, check=False, timeout=120,
            )
            self.assertEqual(res_rec.returncode, 0, res_rec.stderr)

            out_after = io.StringIO()
            with contextlib.redirect_stdout(out_after):
                rc2 = cli_main(["--work-dir", str(trial), "status", "--json"])
            self.assertEqual(rc2, 0)
            snap_after = json.loads(out_after.getvalue())
            self.assertNotIn(
                "REVIEW_NOT_RECORDED",
                [item.get("kind") for item in snap_after["attention"]],
            )

    def test_nightly_run_skill_documents_single_checker_and_mandatory_record_review(self) -> None:
        text = NIGHTLY_RUN_SKILL.read_text(encoding="utf-8")
        sections = re.split(r"^##\s+", text, flags=re.MULTILINE)
        section_map: dict[str, str] = {}
        for sec in sections[1:]:
            heading, _, body = sec.partition("\n")
            section_map[heading.strip()] = body

        single_body = section_map.get("Single-Workspace Sequential Execution", "")
        multi_body = section_map.get("Multi-Team Execution Path (when `teams.json` exists)", "")
        self.assertTrue(single_body, "Single-Workspace Sequential Execution section missing")
        self.assertTrue(multi_body, "Multi-Team Execution Path section missing")

        for name, body in (
            ("Single-Workspace Sequential Execution", single_body),
            ("Multi-Team Execution Path", multi_body),
        ):
            with self.subTest(section=name):
                step3_match = re.search(
                    r"(?ms)^\s*3\.\s+(.*?)(?=^\s*4\.\s+)",
                    body,
                )
                step4_match = re.search(
                    r"(?ms)^\s*4\.\s+(.*?)(?=^\s*-\s+\*\*(?:Step E|Evidence-Gated Commit))",
                    body,
                )
                self.assertIsNotNone(step3_match, f"{name} must contain step 3")
                self.assertIsNotNone(step4_match, f"{name} must contain step 4")
                assert step3_match is not None and step4_match is not None
                step3 = step3_match.group(1)
                step4 = step4_match.group(1)
                step3_low = step3.lower()
                step4_low = step4.lower()

                # 手順 3: 1 回の review-input につき 1 回実行、完了待ち、判定確認は手順 4
                self.assertIn("auto-dev review-input", step3)
                self.assertIn("exactly once", step3_low)
                self.assertIn("finish completely", step3_low)
                self.assertIn("never proceed on a completion notification alone", step3_low)
                self.assertIn("step 4", step3_low)
                self.assertNotIn("verify a single decision", step3_low)
                self.assertNotIn("is resolved", step3_low)

                # 手順 4: すべての出力をまず record-review に渡す
                self.assertIn("auto-dev record-review", step4)
                self.assertIn("every", step4_low)

                # APPROVE / REJECT / undecided (exit 4) / already recorded (exit 4) の分岐
                self.assertIn("`approve` (exit 0)", step4_low)
                self.assertIn("`reject` (exit 0)", step4_low)
                self.assertIn("checker_policy_violation", step4_low)
                self.assertIn("already recorded (exit 4)", step4_low)
                self.assertIn("verify-step", step4_low)

                # undecided 時の再実行手順（Maker に戻さず、試行にも数えず、新しい <review_dir> で再実行）
                undecided_lines = [
                    line for line in step4.splitlines()
                    if "undecided output" in line.lower()
                ]
                self.assertEqual(len(undecided_lines), 1, f"{name} must have an undecided output branch")
                undecided_low = undecided_lines[0].lower()
                self.assertIn("review history", undecided_low)
                self.assertIn("unchanged", undecided_low)
                self.assertRegex(undecided_low, r"do\s+\*{0,2}not\*{0,2}\s+feed")
                self.assertRegex(undecided_low, r"do\s+\*{0,2}not\*{0,2}\s+count")
                self.assertIn("new `<review_dir>`", undecided_low)
                self.assertIn("auto-dev review-input", undecided_low)
                self.assertIn("once more", undecided_low)
                self.assertIn("halt and report", undecided_low)
                self.assertIn("inv-4", undecided_low)

                # Maker に理由を戻す指示は REJECT のみを対象とし、undecided/truncated を含まない
                positive_feed_lines = [
                    line for line in step4.splitlines()
                    if "feed" in line.lower()
                    and "maker" in line.lower()
                    and not re.search(r"do\s+\*{0,2}not\*{0,2}\s+feed", line.lower())
                ]
                self.assertTrue(positive_feed_lines, f"{name} must document feeding REJECT reasons to Maker")
                for line in positive_feed_lines:
                    low = line.lower()
                    self.assertIn("reject", low)
                    self.assertNotIn("undecided", low)
                    self.assertNotIn("truncated", low)


if __name__ == "__main__":
    unittest.main()
