"""DX-9: verify-step --checkpoint に参照名を渡しても解決した40桁SHAを記録することを検証する。"""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main as cli_main
from auto_developer.evidence import EvidenceStore


class TestDX9CheckpointResolution(unittest.TestCase):
    """DX-9: 参照名 (HEAD 等) を解決した40桁 SHA が Evidence・査読・イベントに記録されること。"""

    def _init_repo(self, repo: Path) -> str:
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True, capture_output=True)
        (repo / "SPEC.md").write_text("# Spec\n", encoding="utf-8")
        (repo / "PLAN.md").write_text("- [ ] 1. dummy task\n", encoding="utf-8")
        (repo / "scripts").mkdir(parents=True, exist_ok=True)
        verify_sh = repo / "scripts" / "verify_local.sh"
        verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        verify_sh.chmod(0o755)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
        ).stdout.strip()

    def test_verify_step_with_head_ref_records_40_char_sha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as rev_tmp:
            repo = Path(tmp)
            head_sha = self._init_repo(repo)
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

            rc = cli_main(["--work-dir", str(repo), "verify-step", "--checkpoint", "HEAD", "--task-id", "1"])
            self.assertEqual(rc, 0)

            ev = EvidenceStore(repo).get_evidence("1")
            assert ev is not None
            self.assertEqual(ev["checkpoint"], head_sha)
            self.assertEqual(len(ev["checkpoint"]), 40)

            # Metrics event also has resolved SHA
            from auto_developer.metrics import MetricsLog

            events_file = MetricsLog(repo).events_path
            self.assertTrue(events_file.exists(), f"Expected {events_file}")
            events = [json.loads(line) for line in events_file.read_text(encoding="utf-8").splitlines() if line.strip()]
            verify_events = [e for e in events if e.get("event") == "verify_step"]
            self.assertTrue(verify_events)
            self.assertEqual(verify_events[-1]["checkpoint"], head_sha)

            # record-review & commit-step also work with HEAD
            review_dir = Path(rev_tmp)
            self.assertEqual(
                cli_main(["--work-dir", str(repo), "review-input", "--task-id", "1", "--out-dir", str(review_dir)]),
                0,
            )
            checker_out = review_dir / "checker_out.txt"
            checker_out.write_text('{"decision": "APPROVE", "reason": "ok"}\n', encoding="utf-8")
            spec_ex = review_dir / "spec.md"
            spec_ex.write_text("# Spec\n", encoding="utf-8")
            self.assertEqual(
                cli_main([
                    "--work-dir", str(repo), "record-review",
                    "--task-id", "1",
                    "--checker-output", str(checker_out),
                    "--diff-file", str(review_dir / "diff.patch"),
                    "--spec-file", str(spec_ex),
                    "--maker-model", "gemini-4-hydrogen-medium",
                    "--checker-model", "claude-sonnet-5-5",
                    "--reason", "ok",
                ]),
                0,
            )

            rev_data = EvidenceStore(repo).get_review_record("1")
            assert rev_data is not None
            self.assertEqual(rev_data.get("checkpoint"), head_sha)

            self.assertEqual(
                cli_main([
                    "--work-dir", str(repo), "commit-step",
                    "--task-id", "1",
                    "--checkpoint", "HEAD",
                    "--maker-model", "gemini-4-hydrogen-medium",
                    "--checker-model", "claude-sonnet-5-5",
                    "--checker-reason", "ok",
                ]),
                0,
            )

    def test_verify_step_rejects_unresolvable_checkpoint_ref(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            self._init_repo(repo)
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                rc = cli_main([
                    "--work-dir", str(repo),
                    "verify-step", "--checkpoint", "nonexistent-ref-xyz", "--task-id", "1",
                ])
            self.assertNotEqual(rc, 0)


if __name__ == "__main__":
    unittest.main()
