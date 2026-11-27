from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from auto_developer.audit import (
    AuditConfig,
    IndependentAuditRunner,
    pack_audit_diff_chunks,
    split_git_diff_by_file,
    verify_audit_chunks_match_full_diff,
)
from auto_developer.orchestrator import NightlyOrchestrator


class TestAudit8ChunkCompleteness(unittest.TestCase):
    """AUDIT-8: 分割した監査の欠落を防ぐ（-z 読み取りと組の差分の一致検査）。"""

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
        (self.repo_dir / "src" / "a.py").write_text("A = 1\n", encoding="utf-8")
        (self.repo_dir / "src" / "日本語テスト.py").write_text("JP = 1\n", encoding="utf-8")
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

    def _make_auditor_script(self, script_path: Path, handler_body: str) -> str:
        script_path.write_text(
            "import json, sys\n"
            "prompt = sys.stdin.read()\n"
            f"{handler_body}\n",
            encoding="utf-8",
        )
        return f"{sys.executable} {script_path}"

    def test_japanese_filename_is_included_in_chunked_audit(self) -> None:
        """日本語ファイル名の変更が分割監査の入力から抜けないこと。"""
        (self.repo_dir / "src" / "a.py").write_text("A = '" + ("x" * 160) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "日本語テスト.py").write_text("JP = '" + ("y" * 160) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("modify ascii and japanese files")

        prompts_dir = self.repo_dir / "prompts_jp"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        script = self.repo_dir / "auditor_jp.py"
        cmd = self._make_auditor_script(
            script,
            "import pathlib\n"
            f"pdir = pathlib.Path({str(prompts_dir)!r})\n"
            "idx = len(list(pdir.glob('prompt_*.txt'))) + 1\n"
            "(pdir / f'prompt_{idx}.txt').write_text(prompt, encoding='utf-8')\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
            "'provider_request_id': f'msg_jp_{idx}', 'usage': {'input_tokens': 100, 'output_tokens': 20}, 'findings': []}))",
        )

        # 1ファイル分より少し大きい上限にして2組に分割させる
        diff_a = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", "src/a.py"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        max_bytes = len(diff_a.encode("utf-8")) + 10

        config = AuditConfig(maker_model="gemini-3.8-flash", auditor_model="claude-opus-5.5", baseline_commit=self.baseline_sha)
        runner = IndependentAuditRunner(
            auditor_cmd_template=cmd,
            model_name="claude-opus-5.5",
            config=config,
            baseline_commit=self.baseline_sha,
            audit_max_diff_bytes=max_bytes,
        )
        findings = runner.audit_repository(repo_dir=self.repo_dir, target_commit=target_sha, baseline_commit=self.baseline_sha)
        self.assertEqual(findings, [])

        prompt_files = sorted(prompts_dir.glob("prompt_*.txt"))
        self.assertEqual(len(prompt_files), 2)
        all_prompts = "\n".join(p.read_text(encoding="utf-8") for p in prompt_files)
        self.assertIn("y" * 160, all_prompts)
        self.assertIn("x" * 160, all_prompts)

        meta = runner.last_run_metadata or {}
        chunk_files = [f for c in meta.get("chunks", []) for f in c.get("files", [])]
        self.assertEqual(sorted(chunk_files), ["src/a.py", "src/日本語テスト.py"])

    def test_missing_file_in_chunks_stops_before_calling_auditor_and_returns_audit_pending(self) -> None:
        """組の差分をつなげたものが全体と一致しない場合、監査役を呼ばず AUDIT_PENDING になること。"""
        (self.repo_dir / "src" / "a.py").write_text("A = '" + ("x" * 160) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "日本語テスト.py").write_text("JP = '" + ("y" * 160) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("two files for mismatch test")

        calls_log = self.repo_dir / "calls_mismatch.jsonl"
        script = self.repo_dir / "auditor_mismatch.py"
        cmd = self._make_auditor_script(
            script,
            f"with open({str(calls_log)!r}, 'a', encoding='utf-8') as f:\n"
            "    f.write('called\\n')\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
            "'provider_request_id': 'msg_should_not_run', 'usage': {'input_tokens': 10, 'output_tokens': 5}, 'findings': []}))",
        )

        diff_a = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", "src/a.py"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        max_bytes = len(diff_a.encode("utf-8")) + 10

        def _drop_second_file(file_diffs: list[tuple[str, str]], max_diff_bytes: int) -> list[dict]:
            chunks = pack_audit_diff_chunks(file_diffs, max_diff_bytes)
            # 2番目の組を落として欠落を発生させる
            return chunks[:1]

        orch = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            enable_audit_stage=True,
            auditor_cmd_template=cmd,
            auditor_model="claude-opus-5.5",
            maker_model="gemini-3.8-flash",
            audit_max_diff_bytes=max_bytes,
        )
        with patch("auto_developer.audit.pack_audit_diff_chunks", side_effect=_drop_second_file):
            res = orch.run_audit_stage(baseline_commit=self.baseline_sha, target_commit=target_sha)

        self.assertEqual(res["status"], "AUDIT_PENDING")
        self.assertIn("src/日本語テスト.py", str(res.get("error", "")))
        self.assertFalse(calls_log.exists(), "Auditor must NOT be called when chunk diff check fails")

    def test_duplicate_file_across_chunks_returns_audit_pending(self) -> None:
        """同じファイルが複数の組に重複して入った場合も AUDIT_PENDING になること。"""
        (self.repo_dir / "src" / "a.py").write_text("A = '" + ("x" * 160) + "'\n", encoding="utf-8")
        (self.repo_dir / "src" / "b.py").write_text("B = '" + ("y" * 160) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("two files for duplicate test")

        calls_log = self.repo_dir / "calls_dup.jsonl"
        script = self.repo_dir / "auditor_dup.py"
        cmd = self._make_auditor_script(
            script,
            f"with open({str(calls_log)!r}, 'a', encoding='utf-8') as f:\n"
            "    f.write('called\\n')\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
            "'provider_request_id': 'msg_should_not_run', 'usage': {'input_tokens': 10, 'output_tokens': 5}, 'findings': []}))",
        )

        diff_a = subprocess.run(
            ["git", "diff", self.baseline_sha, target_sha, "--", "src/a.py"],
            cwd=self.repo_dir,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        max_bytes = len(diff_a.encode("utf-8")) + 10

        def _duplicate_first_file(file_diffs: list[tuple[str, str]], max_diff_bytes: int) -> list[dict]:
            chunks = pack_audit_diff_chunks(file_diffs, max_diff_bytes)
            # 1組目を複製して重複させる
            return [chunks[0], chunks[0], chunks[1]]

        orch = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            enable_audit_stage=True,
            auditor_cmd_template=cmd,
            auditor_model="claude-opus-5.5",
            maker_model="gemini-3.8-flash",
            audit_max_diff_bytes=max_bytes,
        )
        with patch("auto_developer.audit.pack_audit_diff_chunks", side_effect=_duplicate_first_file):
            res = orch.run_audit_stage(baseline_commit=self.baseline_sha, target_commit=target_sha)

        self.assertEqual(res["status"], "AUDIT_PENDING")
        self.assertIn("src/a.py", str(res.get("error", "")))
        self.assertFalse(calls_log.exists(), "Auditor must NOT be called when chunk diff has duplicates")

    def test_verify_audit_chunks_match_full_diff_detects_content_mismatch_and_extra_file(self) -> None:
        """ファイルごとの内容の食い違いや余分なファイル、空白入りファイル名を正しく判定すること。"""
        diff_a = "diff --git a/src/my file.py b/src/my file.py\nindex 111..222 100644\n--- a/src/my file.py\n+++ b/src/my file.py\n@@ -1 +1 @@\n-old\n+new\n"
        diff_b = "diff --git a/src/日本語.py b/src/日本語.py\nindex 333..444 100644\n--- a/src/日本語.py\n+++ b/src/日本語.py\n@@ -1 +1 @@\n-1\n+2\n"
        full_diff = diff_a + diff_b

        parsed = split_git_diff_by_file(full_diff)
        self.assertEqual([p for p, _ in parsed], ["src/my file.py", "src/日本語.py"])

        # 完全一致
        self.assertEqual(
            verify_audit_chunks_match_full_diff(full_diff, [{"diff": diff_a}, {"diff": diff_b}]),
            [],
        )

        # 内容の食い違い
        diff_b_altered = diff_b.replace("+2\n", "+3\n")
        self.assertEqual(
            verify_audit_chunks_match_full_diff(full_diff, [{"diff": diff_a}, {"diff": diff_b_altered}]),
            ["src/日本語.py"],
        )

        # 余分なファイル
        diff_extra = "diff --git a/src/extra.py b/src/extra.py\nnew file mode 100644\n--- /dev/null\n+++ b/src/extra.py\n@@ -0,0 +1 @@\n+x\n"
        self.assertEqual(
            verify_audit_chunks_match_full_diff(full_diff, [{"diff": diff_a}, {"diff": diff_b}, {"diff": diff_extra}]),
            ["src/extra.py"],
        )

    def test_rename_delete_space_quote_and_japanese_paths_in_chunked_audit(self) -> None:
        """リネーム・削除・空白・引用符・日本語を含むパスが分割監査で欠落せず一致検査を通ること。"""
        # setUp の baseline とは別に、リネーム検出が効くよう複数行のファイルを用意する
        (self.repo_dir / "src" / "rename_old.py").write_text(
            "".join(f"R_{i} = {i}\n" for i in range(20)),
            encoding="utf-8",
        )
        (self.repo_dir / "src" / "delete_me.py").write_text("DEL = 'delete_marker'\n", encoding="utf-8")
        self.baseline_sha = self._commit_all("add files for rename and delete")

        # 1. リネーム (git mv)
        subprocess.run(
            ["git", "mv", "src/rename_old.py", "src/rename_new.py"],
            cwd=self.repo_dir,
            check=True,
        )
        # 2. 削除 (git rm)
        subprocess.run(["git", "rm", "src/delete_me.py"], cwd=self.repo_dir, check=True, capture_output=True)
        # 3. 空白入りパス
        (self.repo_dir / "src" / "my space.py").write_text("SPACE = '" + ("s" * 120) + "'\n", encoding="utf-8")
        # 4. 引用符 (") 入りパス
        (self.repo_dir / "src" / 'q"uote.py').write_text("QUOTE = '" + ("q" * 120) + "'\n", encoding="utf-8")
        # 5. 日本語パス
        (self.repo_dir / "src" / "日本語テスト.py").write_text("JP = '" + ("j" * 120) + "'\n", encoding="utf-8")
        target_sha = self._commit_all("rename, delete, space, quote, japanese")

        prompts_dir = self.repo_dir / "prompts_edge"
        prompts_dir.mkdir(parents=True, exist_ok=True)
        script = self.repo_dir / "auditor_edge.py"
        cmd = self._make_auditor_script(
            script,
            "import pathlib\n"
            f"pdir = pathlib.Path({str(prompts_dir)!r})\n"
            "idx = len(list(pdir.glob('prompt_*.txt'))) + 1\n"
            "(pdir / f'prompt_{idx}.txt').write_text(prompt, encoding='utf-8')\n"
            "print(json.dumps({'mode': 'LIVE', 'status': 'COMPLETED', 'stop_reason': 'end_turn', "
            "'provider_request_id': f'msg_edge_{idx}', 'usage': {'input_tokens': 100, 'output_tokens': 20}, 'findings': []}))",
        )

        # 複数組に確実に分割される小さな上限
        orch = NightlyOrchestrator(
            repo_dir=self.repo_dir,
            enable_audit_stage=True,
            auditor_cmd_template=cmd,
            auditor_model="claude-opus-5.5",
            maker_model="gemini-3.8-flash",
            audit_max_diff_bytes=300,
        )
        res = orch.run_audit_stage(baseline_commit=self.baseline_sha, target_commit=target_sha)
        self.assertEqual(res["status"], "SUCCESS", msg=f"Unexpected failure: {res.get('error')}")

        prompt_files = sorted(prompts_dir.glob("prompt_*.txt"))
        self.assertGreaterEqual(len(prompt_files), 3)
        all_prompts = "\n".join(p.read_text(encoding="utf-8") for p in prompt_files)
        self.assertIn("delete_marker", all_prompts)
        self.assertIn("R_0 = 0", all_prompts)
        self.assertIn("s" * 120, all_prompts)
        self.assertIn("q" * 120, all_prompts)
        self.assertIn("j" * 120, all_prompts)

        chunk_files = sorted(f for c in res.get("chunks", []) for f in c.get("files", []))
        self.assertEqual(
            chunk_files,
            sorted([
                "src/delete_me.py",
                "src/my space.py",
                'src/q"uote.py',
                "src/rename_new.py",
                "src/rename_old.py",
                "src/日本語テスト.py",
            ]),
        )


if __name__ == "__main__":
    unittest.main()
