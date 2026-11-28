from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from auto_developer.audit import AuditConfig, IndependentAuditRunner


class TestAudit10LiteralPathspec(unittest.TestCase):
    """AUDIT-10: 分割監査の組ごとの差分作成でファイルパスを :(literal) として扱う。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.repo_dir = Path(self.tmp.name)
        subprocess.run(["git", "init", "-b", "main"], cwd=self.repo_dir, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test Runner"], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=self.repo_dir, check=True)

        (self.repo_dir / "SPEC.md").write_text("# Spec\nSPEC_MARKER\n", encoding="utf-8")
        (self.repo_dir / "PLAN.md").write_text("# Plan\n- [x] 1. Done\n", encoding="utf-8")
        (self.repo_dir / "BACKLOG.md").write_text("# Backlog\n", encoding="utf-8")
        (self.repo_dir / "src").mkdir(parents=True, exist_ok=True)
        (self.repo_dir / "src" / "a*.py").write_text("A_STAR = 1\n", encoding="utf-8")
        (self.repo_dir / "src" / "ab.py").write_text("AB = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", "baseline"], cwd=self.repo_dir, check=True, capture_output=True)
        self.baseline_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _commit_all(self, msg: str) -> str:
        subprocess.run(["git", "add", "."], cwd=self.repo_dir, check=True)
        subprocess.run(["git", "commit", "-m", msg], cwd=self.repo_dir, check=True, capture_output=True)
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo_dir, capture_output=True, text=True, check=True
        ).stdout.strip()

    def _make_auditor_script(self, script_path: Path, prompts_dir: Path) -> str:
        prompts_dir.mkdir(parents=True, exist_ok=True)
        script_path.write_text(
            "import json, pathlib, sys\n"
            "prompt = sys.stdin.read()\n"
            f"pdir = pathlib.Path({str(prompts_dir)!r})\n"
            "idx = len(list(pdir.glob('prompt_*.txt'))) + 1\n"
            "(pdir / f'prompt_{idx}.txt').write_text(prompt, encoding='utf-8')\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
            "'provider_request_id': f'msg_{idx}', 'usage': {'input_tokens': 100, 'output_tokens': 20}, 'findings': []}))\n",
            encoding="utf-8",
        )
        return f"{sys.executable} {script_path}"

    def test_glob_meta_filename_does_not_cause_chunk_mismatch(self) -> None:
        """`a*.py` と `ab.py` が両方変更されても、分割監査の欠落・重複検査で食い違いにならないこと。"""
        (self.repo_dir / "src" / "a*.py").write_text("A_STAR = '" + ("x" * 160) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "ab.py").write_text("AB = '" + ("y" * 160) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("modify wildcard-named and matching files")

        prompts_dir = self.repo_dir / "prompts_literal"
        cmd = self._make_auditor_script(self.repo_dir / "auditor_literal.py", prompts_dir)

        diff_ab = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", ":(literal)src/ab.py"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        max_bytes = len(diff_ab.encode("utf-8")) + 10

        config = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="claude-opus-5-5",
            baseline_commit=self.baseline_sha,
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd,
            model_name="claude-opus-5-5",
            config=config,
            baseline_commit=self.baseline_sha,
            audit_max_diff_bytes=max_bytes,
        )
        findings = runner.audit_repository(
            repo_dir=self.repo_dir,
            target_commit=target_sha,
            baseline_commit=self.baseline_sha,
            spec_path=self.repo_dir / "SPEC.md",
        )
        self.assertEqual(findings, [])
        meta = runner.last_run_metadata or {}
        self.assertEqual(meta.get("status"), "COMPLETED")
        self.assertEqual(meta.get("total_chunks"), 2)

        prompts = [p.read_text(encoding="utf-8") for p in sorted(prompts_dir.glob("prompt_*.txt"))]
        self.assertEqual(len(prompts), 2)
        # 各ファイルはちょうど1つの組にだけ含まれる
        self.assertEqual(sum("diff --git a/src/a*.py b/src/a*.py" in p for p in prompts), 1)
        self.assertEqual(sum("diff --git a/src/ab.py b/src/ab.py" in p for p in prompts), 1)

    def test_audit_paths_scope_still_interprets_glob_pathspec(self) -> None:
        """範囲（--audit-paths）の pathspec は従来どおり glob として解釈されること。"""
        (self.repo_dir / "src" / "a*.py").write_text("A_STAR = '" + ("x" * 160) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "ab.py").write_text("AB = '" + ("y" * 160) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "other.txt").write_text("OTHER = '" + ("z" * 160) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("modify py and txt files")

        prompts_dir = self.repo_dir / "prompts_scope"
        cmd = self._make_auditor_script(self.repo_dir / "auditor_scope.py", prompts_dir)

        diff_ab = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", ":(literal)src/ab.py"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        max_bytes = len(diff_ab.encode("utf-8")) + 10

        config = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="claude-opus-5-5",
            baseline_commit=self.baseline_sha,
        )
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd,
            model_name="claude-opus-5-5",
            config=config,
            baseline_commit=self.baseline_sha,
            audit_paths=["src/*.py"],
            audit_max_diff_bytes=max_bytes,
        )
        findings = runner.audit_repository(
            repo_dir=self.repo_dir,
            target_commit=target_sha,
            baseline_commit=self.baseline_sha,
            spec_path=self.repo_dir / "SPEC.md",
        )
        self.assertEqual(findings, [])
        meta = runner.last_run_metadata or {}
        self.assertEqual(meta.get("status"), "COMPLETED")
        self.assertEqual(meta.get("diff_files_count"), 2)

        prompts = [p.read_text(encoding="utf-8") for p in sorted(prompts_dir.glob("prompt_*.txt"))]
        self.assertEqual(len(prompts), 2)
        self.assertTrue(all("other.txt" not in p for p in prompts))


if __name__ == "__main__":
    unittest.main()
