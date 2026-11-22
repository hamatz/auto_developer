from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SRC_DIR = str(Path(__file__).resolve().parents[1] / "src")
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from auto_developer.audit import (
    AuditConfig,
    IndependentAuditRunner,
    detect_model_family,
    verify_model_independence,
)
from auto_developer.orchestrator import NightlyOrchestrator


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env.pop("PYTHONDONTWRITEBYTECODE", None)
    env.pop("PYTHONPYCACHEPREFIX", None)
    existing_pp = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = f"{SRC_DIR}:{existing_pp}" if existing_pp else SRC_DIR
    return env


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )


def _run_cli(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "auto_developer.cli", "--work-dir", str(repo), *args],
        cwd=repo,
        env=_clean_env(),
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )


def _init_single_repo(repo: Path) -> str:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "TestUser")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / ".gitignore").write_text(
        ".dev/\nlogs/\nNIGHTLY_REPORT.md\n__pycache__/\n*.py[cod]\n",
        encoding="utf-8",
    )
    (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
    (repo / "PLAN.md").write_text(
        "# PLAN\n\n## 現在の目標\nPhase 69\n\n## ステップ\n- [ ] 1. Implement feature\n",
        encoding="utf-8",
    )
    scripts_dir = repo / "scripts"
    scripts_dir.mkdir(parents=True, exist_ok=True)
    verify_sh = scripts_dir / "verify_local.sh"
    verify_sh.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
    verify_sh.chmod(0o755)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")
    return _git(repo, "rev-parse", "HEAD").stdout.strip()


def _init_teams_repo(repo: Path) -> None:
    _init_single_repo(repo)
    (repo / "src" / "core").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "core" / "__init__.py").write_text("CORE = 1\n", encoding="utf-8")
    (repo / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "ui" / "__init__.py").write_text("UI = 1\n", encoding="utf-8")
    (repo / "plans").mkdir(parents=True, exist_ok=True)
    (repo / "plans" / "core.md").write_text("## ステップ\n- [ ] 1. Core task\n", encoding="utf-8")
    (repo / "plans" / "ui.md").write_text("## ステップ\n- [ ] 1. UI task\n", encoding="utf-8")
    teams_json = {
        "version": 1,
        "project": "sample",
        "base": {"ref": "main"},
        "teams": [
            {"id": "core", "plan": "plans/core.md", "owns": ["src/core/**"], "depends_on": []},
            {"id": "ui", "plan": "plans/ui.md", "owns": ["src/ui/**"], "depends_on": []},
        ],
    }
    (repo / "teams.json").write_text(json.dumps(teams_json, indent=2) + "\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init teams")


class TestDx7ModelFamilyUnit(unittest.TestCase):
    """DX-7: 組み込み系統の追加、明示系統、食い違い・不正値の拒否の単体テスト。"""

    def test_detect_model_family_includes_new_oss_families(self) -> None:
        self.assertEqual(detect_model_family("mistral-large-latest"), "mistral")
        self.assertEqual(detect_model_family("open-mistral-7b"), "mistral")
        self.assertEqual(detect_model_family("llama-3.3-70b-instruct"), "llama")
        self.assertEqual(detect_model_family("meta-llama/Llama-3.1-8B"), "llama")
        self.assertEqual(detect_model_family("qwen2.5-coder-32b"), "qwen")
        self.assertEqual(detect_model_family("Qwen/Qwen3-32B"), "qwen")
        self.assertEqual(detect_model_family("deepseek-r1"), "deepseek")
        self.assertEqual(detect_model_family("deepseek-chat"), "deepseek")
        # 未知の識別子は引き続き unknown のまま
        self.assertEqual(detect_model_family("pro"), "unknown")
        self.assertEqual(detect_model_family("totally-unknown"), "unknown")
        self.assertEqual(detect_model_family("my-local-model"), "unknown")

    def test_verify_model_independence_supports_explicit_families(self) -> None:
        ok, _ = verify_model_independence(
            "my-local-a",
            "my-local-b",
            maker_family="llama",
            auditor_family="qwen",
        )
        self.assertTrue(ok)

        # カスタム系統名 (例: DX-6 デモ用の demo-maker / demo-checker)
        ok_demo, _ = verify_model_independence(
            "auto-dev-demo-maker",
            "auto-dev-demo-checker",
            maker_family="demo-maker",
            auditor_family="demo-checker",
        )
        self.assertTrue(ok_demo)

        # 明示同士の同一系統は拒否
        ok_same, msg_same = verify_model_independence(
            "my-local-a",
            "my-local-b",
            maker_family="llama",
            auditor_family="llama",
        )
        self.assertFalse(ok_same)
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", msg_same)

        # 明示系統と組み込み推定の同一系統も拒否
        ok_cross_same, msg_cross_same = verify_model_independence(
            "my-local-a",
            "llama-3.3-70b",
            maker_family="llama",
        )
        self.assertFalse(ok_cross_same)
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", msg_cross_same)

    def test_verify_model_independence_rejects_invalid_and_conflicting_explicit_families(self) -> None:
        # 空文字・空白・unknown は拒否
        for bad in ("", "   ", "unknown", "UNKNOWN"):
            with self.subTest(bad_family=bad):
                ok, reason = verify_model_independence(
                    "my-local-a",
                    "claude-opus-5.5",
                    maker_family=bad,
                )
                self.assertFalse(ok)
                self.assertIn("INDEPENDENT_AUDIT_VIOLATION", reason)

                ok2, reason2 = verify_model_independence(
                    "gemini-3.8-flash",
                    "my-local-b",
                    auditor_family=bad,
                )
                self.assertFalse(ok2)
                self.assertIn("INDEPENDENT_AUDIT_VIOLATION", reason2)

        # 組み込み判定と食い違う明示は拒否 (偽装防止)
        ok_mismatch, msg_mismatch = verify_model_independence(
            "gemini-3.8-flash",
            "claude-opus-5.5",
            maker_family="claude",
        )
        self.assertFalse(ok_mismatch)
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", msg_mismatch)

        ok_mismatch2, msg_mismatch2 = verify_model_independence(
            "gemini-3.8-flash",
            "claude-opus-5.5",
            auditor_family="openai",
        )
        self.assertFalse(ok_mismatch2)
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", msg_mismatch2)

        # 組み込み判定と一致する明示は受理
        ok_match, _ = verify_model_independence(
            "gemini-3.8-flash",
            "claude-opus-5.5",
            maker_family="gemini",
            auditor_family="claude",
        )
        self.assertTrue(ok_match)

    def test_audit_config_and_runner_support_explicit_families_and_record_source(self) -> None:
        cfg_ok = AuditConfig(
            maker_model="my-local-maker",
            auditor_model="my-local-auditor",
            maker_family="llama",
            auditor_family="deepseek",
        )
        self.assertTrue(cfg_ok.validate())

        cfg_mismatch = AuditConfig(
            maker_model="gemini-3.8-flash",
            auditor_model="claude-opus-5.5",
            maker_family="qwen",
        )
        with self.assertRaises(ValueError) as ctx:
            cfg_mismatch.validate()
        self.assertIn("INDEPENDENT_AUDIT_VIOLATION", str(ctx.exception))

        runner = IndependentAuditRunner(
            auditor_cmd_template="echo '[]'",
            model_name="my-local-auditor",
            config=cfg_ok,
        )
        findings = runner.audit(spec_content="# SPEC", git_diff="diff --git a/b")
        self.assertEqual(findings, [])
        meta = runner.last_run_metadata or {}
        self.assertEqual(meta.get("maker_family"), "llama")
        self.assertEqual(meta.get("maker_family_source"), "explicit")
        self.assertEqual(meta.get("auditor_family"), "deepseek")
        self.assertEqual(meta.get("auditor_family_source"), "explicit")


class TestDx7CliAndE2E(unittest.TestCase):
    """DX-7: commit-step / record-review / run / teams run の明示系統・拒否・family_source 記録。"""

    def test_commit_step_explicit_and_inferred_family_and_policy_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_single_repo(repo)
            (repo / "app.py").write_text("x = 1\n", encoding="utf-8")

            v_res = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(v_res.returncode, 0, v_res.stderr)

            # 1. 不正/食い違いの明示系統は終了コード 4 で拒否し、Evidence と HEAD を守る
            for bad_args in (
                ["--maker-model", "gemini-3.8-flash", "--maker-family", "claude", "--checker-model", "gpt-4o"],
                ["--maker-model", "my-local-a", "--maker-family", "unknown", "--checker-model", "claude-opus-5.5"],
                ["--maker-model", "my-local-a", "--maker-family", "  ", "--checker-model", "claude-opus-5.5"],
                ["--maker-model", "my-local-a", "--maker-family", "llama", "--checker-model", "llama-3.3-70b"],
            ):
                with self.subTest(bad_args=bad_args):
                    res_bad = _run_cli(
                        repo,
                        "commit-step",
                        "--task-id",
                        "1",
                        "--checkpoint",
                        checkpoint,
                        "--checker-decision",
                        "APPROVE",
                        "--checker-reason",
                        "dx7 rejection check",
                        *bad_args,
                    )
                    self.assertEqual(res_bad.returncode, 4, f"stdout={res_bad.stdout}\nstderr={res_bad.stderr}")
                    self.assertIn("CHECKER_POLICY_VIOLATION", res_bad.stderr)
                    self.assertEqual(_git(repo, "rev-parse", "HEAD").stdout.strip(), checkpoint)

            # 2. 片方明示 (unknown モデル + llama)、片方推定 (deepseek-r1) でコミット成功
            res_ok = _run_cli(
                repo,
                "commit-step",
                "--task-id",
                "1",
                "--checkpoint",
                checkpoint,
                "--maker-model",
                "my-local-model",
                "--maker-family",
                "llama",
                "--checker-model",
                "deepseek-r1",
                "--checker-decision",
                "APPROVE",
                "--checker-reason",
                "ok",
            )
            self.assertEqual(res_ok.returncode, 0, f"stdout={res_ok.stdout}\nstderr={res_ok.stderr}")
            self.assertIn("COMMITTED:", res_ok.stdout)

            # Evidence Record に family と family_source が記録されていること
            rec_path = repo / ".git" / "nightly_evidence" / "records" / "task_1.json"
            rec = json.loads(rec_path.read_text(encoding="utf-8"))
            checker = rec["checker"]
            self.assertEqual(checker["maker_family"], "llama")
            self.assertEqual(checker["maker_family_source"], "explicit")
            self.assertEqual(checker["family"], "deepseek")
            self.assertEqual(checker["family_source"], "inferred")

            # events.jsonl の commit_step イベントにも記録されていること
            events_path = repo / ".git" / "nightly_evidence" / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            committed_events = [e for e in events if e.get("event") == "commit_step" and e.get("result") == "COMMITTED"]
            self.assertEqual(len(committed_events), 1)
            self.assertEqual(committed_events[0]["maker_family"], "llama")
            self.assertEqual(committed_events[0]["maker_family_source"], "explicit")
            self.assertEqual(committed_events[0]["checker_family"], "deepseek")
            self.assertEqual(committed_events[0]["checker_family_source"], "inferred")

    def test_record_review_records_family_source_and_rejects_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_single_repo(repo)

            # 食い違いと unknown は終了コード 2 で拒否
            res_bad = _run_cli(
                repo,
                "record-review",
                "--task-id",
                "1",
                "--decision",
                "APPROVE",
                "--maker-model",
                "gemini-3.8-flash",
                "--maker-family",
                "openai",
                "--checker-model",
                "claude-opus-5.5",
            )
            self.assertEqual(res_bad.returncode, 2, res_bad.stderr)

            res_unknown = _run_cli(
                repo,
                "record-review",
                "--task-id",
                "1",
                "--decision",
                "APPROVE",
                "--maker-model",
                "my-local-a",
                "--maker-family",
                "unknown",
                "--checker-model",
                "claude-opus-5.5",
            )
            self.assertEqual(res_unknown.returncode, 2, res_unknown.stderr)

            # 正常記録（明示 + 推定）
            res_ok = _run_cli(
                repo,
                "record-review",
                "--task-id",
                "1",
                "--decision",
                "REJECT",
                "--maker-model",
                "my-local-a",
                "--maker-family",
                "mistral",
                "--checker-model",
                "qwen2.5-coder",
                "--reason",
                "needs test",
            )
            self.assertEqual(res_ok.returncode, 0, res_ok.stderr)

            events_path = repo / ".git" / "nightly_evidence" / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            review_events = [e for e in events if e.get("event") == "checker_review"]
            self.assertEqual(len(review_events), 1)
            self.assertEqual(review_events[0]["maker_family"], "mistral")
            self.assertEqual(review_events[0]["maker_family_source"], "explicit")
            self.assertEqual(review_events[0]["checker_family"], "qwen")
            self.assertEqual(review_events[0]["checker_family_source"], "inferred")

    def test_cli_run_with_explicit_families_and_audit_stage(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_single_repo(repo)

            # 不正な明示系統は終了コード 2 で拒否
            res_mismatch = _run_cli(
                repo,
                "run",
                "--maker-cmd",
                "true",
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
                "--maker-model",
                "gemini-3.8-flash",
                "--maker-family",
                "claude",
                "--checker-model",
                "claude-opus-5.5",
            )
            self.assertEqual(res_mismatch.returncode, 2, res_mismatch.stderr)

            # カスタムモデル + 明示系統で run + --enable-audit が最後まで完走すること
            maker_cmd = (
                f"{sys.executable} -c \"from pathlib import Path; Path('feature.py').write_text('OK = True\\\\n')\""
            )
            checker_cmd = "printf 'DECISION: APPROVE\\n'"
            auditor_cmd = "printf '[]\\n'"
            res_ok = _run_cli(
                repo,
                "run",
                "--maker-cmd",
                maker_cmd,
                "--checker-cmd",
                checker_cmd,
                "--maker-model",
                "local-maker-v1",
                "--maker-family",
                "llama",
                "--checker-model",
                "local-checker-v1",
                "--checker-family",
                "mistral",
                "--enable-audit",
                "--auditor-cmd",
                auditor_cmd,
                "--auditor-model",
                "local-auditor-v1",
                "--auditor-family",
                "deepseek",
            )
            self.assertEqual(res_ok.returncode, 0, f"stdout={res_ok.stdout}\nstderr={res_ok.stderr}")

            rec = json.loads(
                (repo / ".git" / "nightly_evidence" / "records" / "task_1.json").read_text(encoding="utf-8")
            )
            self.assertEqual(rec["checker"]["family"], "mistral")
            self.assertEqual(rec["checker"]["family_source"], "explicit")
            self.assertEqual(rec["checker"]["maker_family"], "llama")
            self.assertEqual(rec["checker"]["maker_family_source"], "explicit")

            audit_rec = json.loads(
                (repo / ".git" / "nightly_evidence" / "records" / "audit_stage.json").read_text(encoding="utf-8")
            )
            meta = audit_rec.get("audit_metadata") or {}
            self.assertEqual(meta.get("auditor_family"), "deepseek")
            self.assertEqual(meta.get("auditor_family_source"), "explicit")
            self.assertEqual(meta.get("maker_family"), "llama")
            self.assertEqual(meta.get("maker_family_source"), "explicit")

    def test_cli_teams_run_with_explicit_families_and_mismatch_rejection(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_teams_repo(repo)

            # 食い違いは prepare 前に終了コード 2 で拒否
            res_bad = _run_cli(
                repo,
                "teams",
                "run",
                "--job-id",
                "job-dx7-bad",
                "--maker-cmd",
                "true",
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
                "--maker-model",
                "gemini-3.8-flash",
                "--maker-family",
                "qwen",
                "--checker-model",
                "claude-opus-5.5",
            )
            self.assertEqual(res_bad.returncode, 2, res_bad.stderr)
            self.assertFalse((repo / ".dev" / "worktrees" / "core").exists())

            # カスタムモデル + 明示系統で teams run が成功し RC タグが作成されること
            maker_cmd = (
                f"{sys.executable} -c \""
                "from pathlib import Path; "
                "p = Path('src/core/mod.py') if Path('plans/core.md').exists() and Path('src/core').exists() else None; "
                "import subprocess; "
                "br = subprocess.check_output(['git', 'rev-parse', '--abbrev-ref', 'HEAD'], text=True).strip(); "
                "tid = br.split('/')[-1]; "
                "tfile = Path('src') / tid / 'feat.py'; "
                "tfile.parent.mkdir(parents=True, exist_ok=True); "
                "tfile.write_text('VAL = 42\\n', encoding='utf-8')"
                "\""
            )
            res_ok = _run_cli(
                repo,
                "teams",
                "run",
                "--job-id",
                "job-dx7-ok",
                "--maker-cmd",
                maker_cmd,
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
                "--maker-model",
                "custom-local-maker",
                "--maker-family",
                "demo-maker",
                "--checker-model",
                "custom-local-checker",
                "--checker-family",
                "demo-checker",
            )
            self.assertEqual(res_ok.returncode, 0, f"stdout={res_ok.stdout}\nstderr={res_ok.stderr}")

            job_data = json.loads(
                (repo / ".dev" / "integration_jobs" / "job-dx7-ok" / "job.json").read_text(encoding="utf-8")
            )
            self.assertEqual(job_data.get("maker_family"), "demo-maker")
            self.assertEqual(job_data.get("maker_family_source"), "explicit")
            self.assertEqual(job_data.get("checker_family"), "demo-checker")
            self.assertEqual(job_data.get("checker_family_source"), "explicit")

            core_rec = json.loads(
                (repo / ".dev" / "worktrees" / "core" / ".dev" / "evidence" / "task_1.json").read_text(encoding="utf-8")
            )
            self.assertEqual(core_rec["checker"]["family"], "demo-checker")
            self.assertEqual(core_rec["checker"]["family_source"], "explicit")
            self.assertEqual(core_rec["checker"]["maker_family"], "demo-maker")
            self.assertEqual(core_rec["checker"]["maker_family_source"], "explicit")

    def test_orchestrator_direct_rejects_invalid_explicit_family(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_single_repo(repo)
            orch = NightlyOrchestrator(
                repo_dir=repo,
                maker_model="gemini-3.8-flash",
                checker_model="claude-opus-5.5",
                maker_family="llama",
            )
            self.assertEqual(orch.run(), 2)

    def test_commit_step_inferred_family_source_and_top_level_flags_and_wrapper(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            checkpoint = _init_single_repo(repo)
            (repo / "app.py").write_text("y = 2\n", encoding="utf-8")

            v_res = _run_cli(repo, "verify-step", "--checkpoint", checkpoint, "--task-id", "1")
            self.assertEqual(v_res.returncode, 0, v_res.stderr)

            # 系統未指定のときは inferred が記録され、checker_json で上書きできないこと
            wrapper = Path(__file__).resolve().parents[1] / "scripts" / "nightly_orchestrator.py"
            res_commit = subprocess.run(
                [
                    sys.executable,
                    str(wrapper),
                    "--work-dir",
                    str(repo),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    checkpoint,
                    "--maker-model",
                    "gemini-3.8-flash",
                    "--checker-model",
                    "claude-opus-5.5",
                    "--checker-decision",
                    "APPROVE",
                    "--checker-json",
                    json.dumps({"family": "fake", "family_source": "explicit", "maker_family_source": "fake"}),
                ],
                cwd=repo,
                env=_clean_env(),
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(res_commit.returncode, 0, res_commit.stderr)

            rec = json.loads(
                (repo / ".git" / "nightly_evidence" / "records" / "task_1.json").read_text(encoding="utf-8")
            )
            self.assertEqual(rec["checker"]["family"], "claude")
            self.assertEqual(rec["checker"]["family_source"], "inferred")
            self.assertEqual(rec["checker"]["maker_family"], "gemini")
            self.assertEqual(rec["checker"]["maker_family_source"], "inferred")

            events_path = repo / ".git" / "nightly_evidence" / "events.jsonl"
            events = [json.loads(line) for line in events_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            committed = [e for e in events if e.get("event") == "commit_step" and e.get("result") == "COMMITTED"]
            self.assertEqual(len(committed), 1)
            self.assertEqual(committed[0]["checker_family_source"], "inferred")
            self.assertEqual(committed[0]["maker_family_source"], "inferred")

    def test_top_level_family_flags_work_before_run_subcommand(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp)
            _init_single_repo(repo)
            maker_cmd = (
                f"{sys.executable} -c \"from pathlib import Path; Path('top.py').write_text('Z = 1\\\\n')\""
            )
            res = _run_cli(
                repo,
                "--maker-cmd",
                maker_cmd,
                "--checker-cmd",
                "printf 'DECISION: APPROVE\\n'",
                "--maker-model",
                "custom-maker",
                "--maker-family",
                "llama",
                "--checker-model",
                "custom-checker",
                "--checker-family",
                "qwen",
                "run",
            )
            self.assertEqual(res.returncode, 0, f"stdout={res.stdout}\nstderr={res.stderr}")
            rec = json.loads(
                (repo / ".git" / "nightly_evidence" / "records" / "task_1.json").read_text(encoding="utf-8")
            )
            self.assertEqual(rec["checker"]["family"], "qwen")
            self.assertEqual(rec["checker"]["family_source"], "explicit")
            self.assertEqual(rec["checker"]["maker_family"], "llama")
            self.assertEqual(rec["checker"]["maker_family_source"], "explicit")


if __name__ == "__main__":
    unittest.main()
