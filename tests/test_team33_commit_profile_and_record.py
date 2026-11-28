"""TEAM-33: チームの worktree での commit-step モデル一致確認と team_profile 記録のテスト。"""

from __future__ import annotations

import contextlib
import hashlib
import io
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from auto_developer.cli import main
from auto_developer.evidence import EvidenceStore


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_team_repo(
    repo: Path,
    *,
    teams_data: dict[str, object],
    review_docs: dict[str, str] | None = None,
    ui_verify_script: str | None = None,
) -> str:
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test User")

    (repo / "SPEC.md").write_text("# SPEC\n\n## 1. Goal\nTeam 33 test\n", encoding="utf-8")
    (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. Root task\n", encoding="utf-8")
    (repo / "plans").mkdir(parents=True, exist_ok=True)
    (repo / "plans" / "ui.md").write_text("# PLAN (ui)\n\n## ステップ\n- [ ] 1. Build UI\n", encoding="utf-8")
    (repo / "src" / "ui").mkdir(parents=True, exist_ok=True)
    (repo / "src" / "ui" / "app.py").write_text("X = 1\n", encoding="utf-8")
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    local_sh = repo / "scripts" / "verify_local.sh"
    local_sh.write_text("#!/usr/bin/env bash\n# auto-dev: red-first=off\nexit 0\n", encoding="utf-8")
    local_sh.chmod(0o755)

    if ui_verify_script is not None:
        ui_sh = repo / "scripts" / "verify_ui.sh"
        ui_sh.write_text(ui_verify_script, encoding="utf-8")
        ui_sh.chmod(0o755)

    for rel_path, content in (review_docs or {}).items():
        doc_path = repo / rel_path
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(content, encoding="utf-8")

    (repo / "teams.json").write_text(
        json.dumps(teams_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    return _git(repo, "rev-parse", "HEAD")


def _run_cli(argv: list[str]) -> tuple[int, str, str]:
    out_buf = io.StringIO()
    err_buf = io.StringIO()
    with contextlib.redirect_stdout(out_buf), contextlib.redirect_stderr(err_buf):
        rc = main(argv)
    return rc, out_buf.getvalue(), err_buf.getvalue()


def _prepare_verified_and_reviewed_worktree(
    wt_dir: Path,
    review_dir: Path,
    *,
    chk: str,
    maker_model: str,
    checker_model: str,
) -> None:
    (wt_dir / "src" / "ui" / "app.py").write_text("X = 2\n", encoding="utf-8")
    rc, out, err = _run_cli(["--work-dir", str(wt_dir), "verify-step", "--checkpoint", chk, "--task-id", "1"])
    assert rc == 0, f"verify-step failed: {out}\n{err}"

    rc, out, err = _run_cli(
        ["--work-dir", str(wt_dir), "review-input", "--task-id", "1", "--out-dir", str(review_dir)]
    )
    assert rc == 0, f"review-input failed: {out}\n{err}"

    checker_out = review_dir / "checker.txt"
    checker_out.write_text("Looks good.\nDECISION: APPROVE\n", encoding="utf-8")

    rc, out, err = _run_cli(
        [
            "--work-dir",
            str(wt_dir),
            "record-review",
            "--task-id",
            "1",
            "--checker-output",
            str(checker_out),
            "--diff-file",
            str(review_dir / "diff.patch"),
            "--spec-file",
            str(wt_dir / "SPEC.md"),
            "--maker-model",
            maker_model,
            "--checker-model",
            checker_model,
            "--reason",
            "ok",
        ]
    )
    assert rc == 0, f"record-review failed: {out}\n{err}"


class TestTeam33CommitProfileAndRecord(unittest.TestCase):
    """TEAM-33: commit-step のモデル一致確認と Evidence / 査読記録の team_profile 記録。"""

    def test_maker_model_mismatch_rejects_with_exit_4_and_preserves_state(self) -> None:
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                    "maker": {"model": "gemini-4-hydrogen-medium"},
                    "checker": {"model": "claude-sonnet-5-5"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(repo, teams_data=teams_data)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            _prepare_verified_and_reviewed_worktree(
                wt_ui,
                Path(review_tmp),
                chk=chk,
                maker_model="gpt-5-codex",
                checker_model="claude-sonnet-5-5",
            )

            head_before = _git(wt_ui, "rev-parse", "HEAD")
            plan_before = (wt_ui / "plans" / "ui.md").read_text(encoding="utf-8")

            rc, _out, err = _run_cli(
                [
                    "--work-dir",
                    str(wt_ui),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "gpt-5-codex",
                    "--checker-model",
                    "claude-sonnet-5-5",
                    "--checker-reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 4, f"Expected exit 4 on maker mismatch, got {rc}: {err}")
            self.assertIn("TEAM_PROFILE_MISMATCH:", err)
            self.assertIn("gemini-4-hydrogen-medium", err)
            self.assertIn("gpt-5-codex", err)

            self.assertEqual(_git(wt_ui, "rev-parse", "HEAD"), head_before)
            self.assertEqual((wt_ui / "plans" / "ui.md").read_text(encoding="utf-8"), plan_before)
            self.assertIsNotNone(EvidenceStore(wt_ui).get_evidence("1"))

    def test_checker_model_mismatch_rejects_with_exit_4_and_preserves_state(self) -> None:
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                    "maker": {"model": "gemini-4-hydrogen-medium"},
                    "checker": {"model": "claude-sonnet-5-5"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(repo, teams_data=teams_data)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            _prepare_verified_and_reviewed_worktree(
                wt_ui,
                Path(review_tmp),
                chk=chk,
                maker_model="gemini-4-hydrogen-medium",
                checker_model="claude-opus-5-5",
            )

            head_before = _git(wt_ui, "rev-parse", "HEAD")
            plan_before = (wt_ui / "plans" / "ui.md").read_text(encoding="utf-8")

            rc, _out, err = _run_cli(
                [
                    "--work-dir",
                    str(wt_ui),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-opus-5-5",
                    "--checker-reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 4, f"Expected exit 4 on checker mismatch, got {rc}: {err}")
            self.assertIn("TEAM_PROFILE_MISMATCH:", err)
            self.assertIn("claude-sonnet-5-5", err)
            self.assertIn("claude-opus-5-5", err)

            self.assertEqual(_git(wt_ui, "rev-parse", "HEAD"), head_before)
            self.assertEqual((wt_ui / "plans" / "ui.md").read_text(encoding="utf-8"), plan_before)
            self.assertIsNotNone(EvidenceStore(wt_ui).get_evidence("1"))

    def test_matching_models_commit_and_record_team_profile_in_evidence_and_review(self) -> None:
        doc_design = "# Design\nGrid 8px\n"
        ui_script = "#!/usr/bin/env bash\n# auto-dev: red-first=off\nexit 0\n"
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                    "skills": [{"name": "frontend-design", "version": "1.2.0"}],
                    "maker": {"model": "gemini-4-hydrogen-medium"},
                    "checker": {"model": "claude-sonnet-5-5"},
                    "review_docs": ["docs/design.md"],
                    "verify_script": "scripts/verify_ui.sh",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(
                repo,
                teams_data=teams_data,
                review_docs={"docs/design.md": doc_design},
                ui_verify_script=ui_script,
            )
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            review_dir = Path(review_tmp)
            _prepare_verified_and_reviewed_worktree(
                wt_ui,
                review_dir,
                chk=chk,
                maker_model="gemini-4-hydrogen-medium",
                checker_model="claude-sonnet-5-5",
            )

            expected_profile = json.loads((review_dir / "team_profile.json").read_text(encoding="utf-8"))

            rc, out, err = _run_cli(
                [
                    "--work-dir",
                    str(wt_ui),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                    "--checker-reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 0, f"commit-step failed: {out}\n{err}")

            store = EvidenceStore(wt_ui)
            ev_record = store.get_record("1")
            self.assertIsNotNone(ev_record)
            assert ev_record is not None
            self.assertEqual(ev_record.get("team_profile"), expected_profile)
            self.assertEqual(
                ev_record["team_profile"]["skills"],
                {"declared": [{"name": "frontend-design", "version": "1.2.0"}]},
            )
            self.assertEqual(
                ev_record["team_profile"]["verify_script"]["sha256"],
                hashlib.sha256(ui_script.encode("utf-8")).hexdigest(),
            )

            rev_record = store.get_review_record("1")
            self.assertIsNotNone(rev_record)
            assert rev_record is not None
            self.assertEqual(rev_record.get("team_profile"), expected_profile)

    def test_worktree_without_declared_models_commits_normally(self) -> None:
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(repo, teams_data=teams_data)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            _prepare_verified_and_reviewed_worktree(
                wt_ui,
                Path(review_tmp),
                chk=chk,
                maker_model="gpt-5-codex",
                checker_model="claude-sonnet-5-5",
            )
            rc, out, err = _run_cli(
                [
                    "--work-dir",
                    str(wt_ui),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "gpt-5-codex",
                    "--checker-model",
                    "claude-sonnet-5-5",
                    "--checker-reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 0, f"commit-step failed: {out}\n{err}")

            store = EvidenceStore(wt_ui)
            ev_record = store.get_record("1")
            rev_record = store.get_review_record("1")
            self.assertIsNotNone(ev_record)
            self.assertIsNotNone(rev_record)
            assert ev_record is not None and rev_record is not None
            self.assertIn("team_profile", ev_record)
            self.assertIn("team_profile", rev_record)
            self.assertIsNone(ev_record["team_profile"]["maker"])
            self.assertIsNone(ev_record["team_profile"]["checker"])

    def test_single_repo_outside_worktree_does_not_record_team_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            repo = Path(tmp) / "repo"
            repo.mkdir()
            _git(repo, "init", "-b", "main")
            _git(repo, "config", "user.email", "test@example.com")
            _git(repo, "config", "user.name", "Test User")
            (repo / "SPEC.md").write_text("# SPEC\n", encoding="utf-8")
            (repo / "PLAN.md").write_text("# PLAN\n- [ ] 1. Single task\n", encoding="utf-8")
            (repo / "scripts").mkdir(parents=True, exist_ok=True)
            sh = repo / "scripts" / "verify_local.sh"
            sh.write_text("#!/usr/bin/env bash\n# auto-dev: red-first=off\nexit 0\n", encoding="utf-8")
            sh.chmod(0o755)
            _git(repo, "add", ".")
            _git(repo, "commit", "-m", "init")
            chk = _git(repo, "rev-parse", "HEAD")

            (repo / "app.py").write_text("A = 1\n", encoding="utf-8")
            rc, out, err = _run_cli(["--work-dir", str(repo), "verify-step", "--checkpoint", chk, "--task-id", "1"])
            self.assertEqual(rc, 0, f"{out}\n{err}")

            review_dir = Path(review_tmp)
            rc, out, err = _run_cli(
                ["--work-dir", str(repo), "review-input", "--task-id", "1", "--out-dir", str(review_dir)]
            )
            self.assertEqual(rc, 0, f"{out}\n{err}")

            checker_out = review_dir / "checker.txt"
            checker_out.write_text("DECISION: APPROVE\n", encoding="utf-8")
            rc, out, err = _run_cli(
                [
                    "--work-dir",
                    str(repo),
                    "record-review",
                    "--task-id",
                    "1",
                    "--checker-output",
                    str(checker_out),
                    "--diff-file",
                    str(review_dir / "diff.patch"),
                    "--spec-file",
                    str(repo / "SPEC.md"),
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                    "--reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 0, f"{out}\n{err}")

            rc, out, err = _run_cli(
                [
                    "--work-dir",
                    str(repo),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                    "--checker-reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 0, f"{out}\n{err}")

            store = EvidenceStore(repo)
            ev_record = store.get_record("1")
            rev_record = store.get_review_record("1")
            self.assertIsNotNone(ev_record)
            self.assertIsNotNone(rev_record)
            assert ev_record is not None and rev_record is not None
            self.assertNotIn("team_profile", ev_record)
            self.assertNotIn("team_profile", rev_record)

    def test_maker_matching_checker_family_still_reports_team_profile_mismatch(self) -> None:
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                    "maker": {"model": "gemini-4-hydrogen-medium"},
                    "checker": {"model": "claude-sonnet-5-5"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(repo, teams_data=teams_data)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            _prepare_verified_and_reviewed_worktree(
                wt_ui,
                Path(review_tmp),
                chk=chk,
                maker_model="claude-sonnet-5-5",
                checker_model="claude-sonnet-5-5",
            )
            rc, _out, err = _run_cli(
                [
                    "--work-dir",
                    str(wt_ui),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "claude-sonnet-5-5",
                    "--checker-model",
                    "claude-sonnet-5-5",
                    "--checker-reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 4)
            self.assertIn("TEAM_PROFILE_MISMATCH:", err)

    def test_review_record_model_mismatch_reports_team_profile_mismatch(self) -> None:
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                    "maker": {"model": "gemini-4-hydrogen-medium"},
                    "checker": {"model": "claude-sonnet-5-5"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(repo, teams_data=teams_data)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            _prepare_verified_and_reviewed_worktree(
                wt_ui,
                Path(review_tmp),
                chk=chk,
                maker_model="gpt-5-codex",
                checker_model="claude-sonnet-5-5",
            )
            rc, _out, err = _run_cli(
                [
                    "--work-dir",
                    str(wt_ui),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-sonnet-5-5",
                    "--checker-reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 4)
            self.assertIn("TEAM_PROFILE_MISMATCH:", err)
            self.assertIn("review_record", err)

    def test_only_maker_declared_allows_any_independent_checker(self) -> None:
        teams_data = {
            "version": 1,
            "project": "demo",
            "teams": [
                {
                    "id": "ui",
                    "owns": ["src/ui/**"],
                    "plan": "plans/ui.md",
                    "maker": {"model": "gemini-4-hydrogen-medium"},
                }
            ],
        }
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as review_tmp:
            base = Path(tmp)
            repo = base / "repo"
            repo.mkdir()
            chk = _init_team_repo(repo, teams_data=teams_data)
            wt_ui = base / "wt_ui"
            _git(repo, "worktree", "add", "-b", "team/ui", str(wt_ui), "main")

            _prepare_verified_and_reviewed_worktree(
                wt_ui,
                Path(review_tmp),
                chk=chk,
                maker_model="gemini-4-hydrogen-medium",
                checker_model="claude-opus-5-5",
            )
            rc, out, err = _run_cli(
                [
                    "--work-dir",
                    str(wt_ui),
                    "commit-step",
                    "--task-id",
                    "1",
                    "--checkpoint",
                    chk,
                    "--maker-model",
                    "gemini-4-hydrogen-medium",
                    "--checker-model",
                    "claude-opus-5-5",
                    "--checker-reason",
                    "ok",
                ]
            )
            self.assertEqual(rc, 0, f"commit-step failed: {out}\n{err}")
            ev_record = EvidenceStore(wt_ui).get_record("1")
            assert ev_record is not None
            self.assertIsNone(ev_record["team_profile"]["checker"])


if __name__ == "__main__":
    unittest.main()
