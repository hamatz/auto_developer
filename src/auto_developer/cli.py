from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, cast

from auto_developer.audit import detect_model_family, verify_model_independence
from auto_developer.boundary import BoundaryGuard
from auto_developer.bundler import EvidenceBundler
from auto_developer.dispatcher import TaskDispatcher
from auto_developer.evidence import EvidenceStore
from auto_developer.guard import GitGuard
from auto_developer.manifest import (
    ManifestValidationError,
    TeamContext,
    TeamManifest,
    load_teams_manifest,
    resolve_team_context,
)
from auto_developer.metrics import MetricsLog
from auto_developer.models import JobState
from auto_developer.multi_team import (
    cleanup_teams_job,
    format_teams_integrate_text,
    format_teams_status_text,
    get_teams_status,
    integrate_teams_job,
    prepare_teams_job,
)
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter
from auto_developer.runner import CommandRunnerAdapter, MockRunnerAdapter, RunnerAdapter
from auto_developer.verifier import DeterministicVerifier

VERSION = "0.8.0"

DEFAULT_VERIFY_SH = """#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/2] Checking protected file invariants (tracked & untracked) ==="
CHECKPOINT="${1:-HEAD}"

if git rev-parse --verify "$CHECKPOINT" >/dev/null 2>&1; then
  PROTECTED_DIFF=$(git diff --name-only "$CHECKPOINT" -- .github/ SPEC.md scripts/verify_local.sh tests/immunity/ teams.json 2>/dev/null || true)
  if [ -n "$PROTECTED_DIFF" ]; then
    echo "ERROR: Protected files were modified without authorization (git diff):"
    echo "$PROTECTED_DIFF"
    exit 1
  fi
fi

DIRTY_PROTECTED=$(git status --porcelain | awk '{print $2}' | grep -E '^(\\.github/|SPEC\\.md|scripts/verify_local\\.sh|tests/immunity/|teams\\.json$)' || true)
if [ -n "$DIRTY_PROTECTED" ]; then
  echo "ERROR: Protected files were created or modified (git status):"
  echo "$DIRTY_PROTECTED"
  exit 1
fi

echo "=== [2/2] Running Project Tests ==="
# プロジェクトのテストコマンドに置き換えてください (例: pytest, npm test, cargo test 等)
if [ ! -d "tests" ] || [ -z "$(find tests -name 'test_*.py' 2>/dev/null)" ]; then
  echo "ERROR: No tests configured. A valid test suite (e.g. tests/test_*.py) is required to enforce verification gates."
  exit 1
fi

if [ -x "/usr/bin/python3" ]; then
  PYTHON="/usr/bin/python3"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="python3"
else
  echo "ERROR: python3 not found to execute tests"
  exit 1
fi

"$PYTHON" -m unittest discover -s tests -p "test_*.py"

echo "=== All verification checks passed! (Exit 0) ==="
"""

DEFAULT_PLAN_MD = """# PLAN.md

<!--
Auto Developer が順次実行するタスクリスト。
- [ ] 1. タスク名  (未実行)
- [x] 1. タスク名  (完了)
- [ ] 1. タスク名 **[FAILED]** (失敗・隔離)
-->

## 現在の目標
- 新機能の実装と検証

## ステップ
- [ ] 1. 初期モジュールの実装
- [ ] 2. 単体テストの追加と受入検証
"""

DEFAULT_SPEC_MD = """# Product Specification & Invariant Contracts (SPEC.md)

## 1. 概要 (Overview)
- プロジェクト概要および開発目的をここに記載します。

## 2. コア受入要件 (Core Requirements)
- [ ] 要件1: 
- [ ] 要件2: 

## 3. 厳格な不変条件 (System Invariants)
1. 保護アセット不変: `.github/**`, `SPEC.md`, `scripts/verify_local.sh`, 免疫テストの無断改変を物理遮断する。
2. 失敗時の隔離保存: タスク失敗時は成果を破棄せず quarantine ブランチに退避し、本線を直前の正常状態へ復元する。
"""

DEFAULT_TEST_PY = """import unittest


class SmokeTest(unittest.TestCase):
    def test_smoke(self):
        self.assertTrue(True)
"""

DEFAULT_IMMUNITY_TEST_PY = """# tests/immunity/ - 人間が定義・承認した受入仕様テスト（免疫テスト）
# Maker（実装AI）による変更・削除・勝手な追加は機械的に遮断・隔離されます。
import unittest


class AcceptanceImmunityTest(unittest.TestCase):
    def test_acceptance_contract(self):
        \"\"\"SPEC.md に定義されたコア仕様の受入条件を記述してください。\"\"\"
        self.assertTrue(True)
"""


def init_project(target_dir: Path) -> int:
    """指定ディレクトリに Auto Developer 規律ファイル群を生成する。"""
    print(f"Initializing Auto Developer configuration in: {target_dir}")

    # 1. scripts/verify_local.sh
    verify_script = target_dir / "scripts" / "verify_local.sh"
    if not verify_script.exists():
        verify_script.parent.mkdir(parents=True, exist_ok=True)
        verify_script.write_text(DEFAULT_VERIFY_SH, encoding="utf-8")
        verify_script.chmod(0o755)
        print("  ✓ Created scripts/verify_local.sh")
    else:
        print("  - scripts/verify_local.sh already exists, skipping")

    # 2. PLAN.md
    plan_file = target_dir / "PLAN.md"
    if not plan_file.exists():
        plan_file.write_text(DEFAULT_PLAN_MD, encoding="utf-8")
        print("  ✓ Created PLAN.md")
    else:
        print("  - PLAN.md already exists, skipping")

    # 3. SPEC.md
    spec_file = target_dir / "SPEC.md"
    if not spec_file.exists():
        spec_file.write_text(DEFAULT_SPEC_MD, encoding="utf-8")
        print("  ✓ Created SPEC.md")
    else:
        print("  - SPEC.md already exists, skipping")

    # 4. .gitignore
    gitignore = target_dir / ".gitignore"
    needed_ignores = [".dev/", "logs/", "NIGHTLY_REPORT.md"]
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        to_add = [ig for ig in needed_ignores if ig not in content]
        if to_add:
            content += "\n# Auto Developer artifacts\n" + "\n".join(to_add) + "\n"
            gitignore.write_text(content, encoding="utf-8")
            print(f"  ✓ Added {to_add} to .gitignore")
        else:
            print("  - .gitignore already contains Auto Developer artifacts, skipping")
    else:
        gitignore.write_text("\n".join(needed_ignores) + "\n", encoding="utf-8")
        print("  ✓ Created .gitignore with Auto Developer artifacts")

    # 5. tests/test_smoke.py (初期検証用テスト)
    smoke_test = target_dir / "tests" / "test_smoke.py"
    if not (target_dir / "tests").exists():
        smoke_test.parent.mkdir(parents=True, exist_ok=True)
        smoke_test.write_text(DEFAULT_TEST_PY, encoding="utf-8")
        print("  ✓ Created tests/test_smoke.py (Initial verification test)")

    # 6. tests/immunity/test_acceptance_sample.py (保護された受入・免疫テスト / 機能O)
    immunity_dir = target_dir / "tests" / "immunity"
    immunity_sample = immunity_dir / "test_acceptance_sample.py"
    if not immunity_sample.exists():
        immunity_dir.mkdir(parents=True, exist_ok=True)
        immunity_sample.write_text(DEFAULT_IMMUNITY_TEST_PY, encoding="utf-8")
        print("  ✓ Created tests/immunity/test_acceptance_sample.py (Protected Acceptance Test)")
    else:
        print("  - tests/immunity/test_acceptance_sample.py already exists, skipping")

    print("\nInitialization complete! You can now run `auto-dev run` or `auto-dev --dry-run`.")
    return 0


def load_teams_manifest_or_error(
    work_dir: Path,
    *,
    manifest_path: Path | None = None,
) -> tuple[TeamManifest | None, int]:
    """teams.json を読み込み、検証失敗時は TEAMS_CONFIG_ERROR: を出して終了コード 2 を返す。"""
    try:
        manifest = load_teams_manifest(work_dir, manifest_path=manifest_path)
    except (FileNotFoundError, ManifestValidationError, OSError) as err:
        print(f"TEAMS_CONFIG_ERROR: {err}", file=sys.stderr)
        return None, 2
    return manifest, 0


def _parse_owns_arg(raw_owns: list[str] | None) -> list[str] | None:
    if raw_owns is None:
        return None
    patterns: list[str] = []
    for item in raw_owns:
        for part in item.split(","):
            cleaned = part.strip()
            if cleaned:
                patterns.append(cleaned)
    return patterns


def _resolve_cli_team_context(
    work_dir: Path,
    *,
    ref: str = "HEAD",
    explicit_team_id: str | None = None,
    explicit_owns: list[str] | None = None,
    explicit_plan: Path | None = None,
    allow_explicit_without_manifest: bool = False,
) -> tuple[TeamContext | None, int]:
    try:
        ctx = resolve_team_context(
            work_dir,
            ref=ref,
            explicit_team_id=explicit_team_id,
            explicit_owns=explicit_owns,
            explicit_plan=explicit_plan,
            allow_explicit_without_manifest=allow_explicit_without_manifest,
        )
    except ManifestValidationError as err:
        print(f"TEAMS_CONFIG_ERROR: {err}", file=sys.stderr)
        return None, 2
    return ctx, 0


def _build_team_plan_template(team_id: str) -> str:
    return (
        f"# PLAN ({team_id})\n\n"
        "<!--\n"
        f"チーム '{team_id}' のタスクリスト。\n"
        "- [ ] 1. タスク名  (未実行)\n"
        "- [x] 1. タスク名  (完了)\n"
        "- [ ] 1. タスク名 **[FAILED]** (失敗・隔離)\n"
        "-->\n\n"
        "## 現在の目標\n"
        f"- チーム `{team_id}` の実装と検証\n\n"
        "## ステップ\n"
        "- [ ] 1. 初期モジュールの実装\n"
        "- [ ] 2. 単体テストの追加と検証\n"
    )


def init_teams_project(target_dir: Path, team_ids: list[str] | None = None) -> int:
    """teams.json の雛形と各チームの plans/<team_id>.md を作成する（既存ファイルは上書きしない）。"""
    print(f"Initializing multi-team configuration in: {target_dir}")
    teams_json_path = target_dir / "teams.json"

    if teams_json_path.exists():
        manifest, err_code = load_teams_manifest_or_error(target_dir, manifest_path=teams_json_path)
        if manifest is None:
            return err_code
        print("  - teams.json already exists, skipping")
        if team_ids is not None:
            if len(team_ids) != len(set(team_ids)):
                print("TEAMS_CONFIG_ERROR: Duplicate team ID in --teams.", file=sys.stderr)
                return 2
            existing_ids = {t.id for t in manifest.teams}
            unknown = [tid for tid in team_ids if tid not in existing_ids]
            if unknown:
                print(
                    f"TEAMS_CONFIG_ERROR: teams.json already exists without teams {unknown}.",
                    file=sys.stderr,
                )
                return 2
            selected = set(team_ids)
            team_plans = [(team.id, team.plan) for team in manifest.teams if team.id in selected]
        else:
            team_plans = [(team.id, team.plan) for team in manifest.teams]
    else:
        ids_to_use = list(team_ids) if team_ids is not None else ["core", "ui"]
        manifest_payload = {
            "version": 1,
            "project": target_dir.name or "auto-developer-project",
            "base": {
                "ref": "main",
                "require_clean": True,
            },
            "teams": [
                {
                    "id": tid,
                    "plan": f"plans/{tid}.md",
                    "owns": [f"src/{tid}/**"],
                    "depends_on": [],
                }
                for tid in ids_to_use
            ],
            "integration": {
                "strategy": "merge-sequential",
                "require_team_evidence": True,
                "reverify_after_merge": True,
                "update_main": False,
            },
        }
        try:
            validated = TeamManifest.from_dict(manifest_payload, allow_depends_on=False)
        except ManifestValidationError as err:
            print(f"TEAMS_CONFIG_ERROR: {err}", file=sys.stderr)
            return 2

        teams_json_path.write_text(
            json.dumps(manifest_payload, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        print("  ✓ Created teams.json")
        team_plans = [(team.id, team.plan) for team in validated.teams]

    for tid, plan_rel in team_plans:
        plan_file = target_dir / plan_rel
        if not plan_file.exists():
            plan_file.parent.mkdir(parents=True, exist_ok=True)
            plan_file.write_text(_build_team_plan_template(tid), encoding="utf-8")
            print(f"  ✓ Created {plan_rel}")
        else:
            print(f"  - {plan_rel} already exists, skipping")

    return 0


def main(argv: list[str] | None = None) -> int:
    """CLI エントリーポイント。"""
    parser = argparse.ArgumentParser(
        prog="auto-dev",
        description="Auto Developer: Trustworthy Autonomous Pipeline Orchestrator",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument(
        "--work-dir", type=Path, default=Path.cwd(), help="Target workspace root directory"
    )
    parser.add_argument("--plan", type=Path, default=None, help="Path to PLAN.md")
    parser.add_argument("--spec", type=Path, default=None, help="Path to SPEC.md")
    parser.add_argument(
        "--verify-script",
        type=Path,
        default=None,
        help="Path to verification script",
    )
    parser.add_argument(
        "--report", type=Path, default=None, help="Path to NIGHTLY_REPORT.md"
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=None,
        help="Path to persistent job state file",
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Simulate execution without modifying git or state"
    )
    parser.add_argument(
        "--timeout", type=int, default=300, help="Max verification timeout in seconds"
    )
    parser.add_argument(
        "--resume", action="store_true", help="Resume interrupted job from state file"
    )
    parser.add_argument(
        "--mock", action="store_true", help="Run with simulated mock agents (for testing only)"
    )
    parser.add_argument(
        "--maker-cmd", type=str, default="", help="Command template to execute Maker"
    )
    parser.add_argument(
        "--checker-cmd", type=str, default="", help="Command template to execute Checker"
    )
    parser.add_argument(
        "--maker-model", type=str, default="gemini-3.8-flash", help="Model identifier for Maker"
    )
    parser.add_argument(
        "--checker-model", type=str, default="claude-opus-5.5", help="Model identifier for Checker"
    )
    parser.add_argument(
        "--enable-audit", action="store_true", help="Enable independent repository audit stage on completion"
    )
    parser.add_argument(
        "--auditor-cmd", type=str, default="", help="Command template to execute independent auditor"
    )
    parser.add_argument(
        "--auditor-model", type=str, default="claude-opus-5.5", help="Model identifier for Independent Auditor"
    )
    parser.add_argument(
        "--auditor-fallback-models", type=str, default="", help="Comma-separated fallback models for independent auditor"
    )
    parser.add_argument(
        "--audit-baseline", type=str, default=None, help="Baseline commit hash for independent audit comparison"
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand actions")

    # Subcommand: init
    subparsers.add_parser("init", help="Initialize Auto Developer template files in the project")

    # Subcommand: run
    run_parser = subparsers.add_parser("run", help="Run autonomous sequential execution loop")
    run_parser.add_argument("--plan", type=Path, default=argparse.SUPPRESS, help="Path to PLAN.md")
    run_parser.add_argument("--team-id", type=str, default=None, help="Explicit team ID for team execution")
    run_parser.add_argument(
        "--owns",
        action="append",
        default=None,
        help="Explicit ownership glob pattern(s) for team execution (repeatable or comma-separated)",
    )
    run_parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying git or state")
    run_parser.add_argument("--timeout", type=int, default=None, help="Max verification timeout in seconds")
    run_parser.add_argument("--resume", action="store_true", help="Resume interrupted job from state file")
    run_parser.add_argument("--mock", action="store_true", help="Run with simulated mock agents (for testing only)")
    run_parser.add_argument("--maker-cmd", type=str, default="", help="Command template to execute Maker")
    run_parser.add_argument("--checker-cmd", type=str, default="", help="Command template to execute Checker")
    run_parser.add_argument("--maker-model", type=str, default="gemini-3.8-flash", help="Model identifier for Maker")
    run_parser.add_argument("--checker-model", type=str, default="claude-opus-5.5", help="Model identifier for Checker")
    run_parser.add_argument("--enable-audit", action="store_true", help="Enable independent repository audit stage on completion")
    run_parser.add_argument("--auditor-cmd", type=str, default="", help="Command template to execute independent auditor")
    run_parser.add_argument("--auditor-model", type=str, default="claude-opus-5.5", help="Model identifier for Independent Auditor")
    run_parser.add_argument("--auditor-fallback-models", type=str, default="", help="Comma-separated fallback models for independent auditor")
    run_parser.add_argument("--audit-baseline", type=str, default=None, help="Baseline commit hash for independent audit comparison")

    # Subcommand: brief
    subparsers.add_parser("brief", help="Display morning brief of recent execution")
    subparsers.add_parser("morning-brief", help="Alias for brief")

    # Subcommand: report
    report_parser = subparsers.add_parser("report", help="Generate or refresh NIGHTLY_REPORT.md from git, PLAN.md, and evidence")
    report_parser.add_argument("--out", type=Path, default=None, help="Output markdown path")

    # Subcommand: pre-flight
    subparsers.add_parser("pre-flight", help="Check clean working tree and print next pending task")

    # Subcommand: verify-step
    verify_parser = subparsers.add_parser("verify-step", help="Check protected files and run verify script")
    verify_parser.add_argument("--checkpoint", type=str, required=True, help="Checkpoint commit hash")
    verify_parser.add_argument("--task-id", type=str, default="default", help="Task ID being verified")

    # Subcommand: commit-step
    commit_parser = subparsers.add_parser("commit-step", help="Commit current task and mark [x] in PLAN.md")
    commit_parser.add_argument("--task-id", type=str, required=True, help="Task ID to commit")
    commit_parser.add_argument("--checkpoint", type=str, default=None, help="Optional checkpoint commit hash to verify")
    commit_parser.add_argument("--maker-model", type=str, required=True, help="Model identifier for Maker")
    commit_parser.add_argument("--checker-model", type=str, required=True, help="Model identifier for Checker")
    commit_parser.add_argument("--checker-decision", type=str, required=True, help="Checker review decision (e.g. APPROVE)")
    commit_parser.add_argument("--checker-reason", type=str, default=None, help="Checker review justification")
    commit_parser.add_argument("--checker-json", type=str, default=None, help="Checker review structured JSON string")

    # Subcommand: rollback-step
    rollback_parser = subparsers.add_parser("rollback-step", help="Rollback working tree and mark task as failed")
    rollback_parser.add_argument("--checkpoint", type=str, required=True, help="Checkpoint commit hash")
    rollback_parser.add_argument("--task-id", type=str, required=True, help="Task ID to mark failed")
    rollback_parser.add_argument("--reason", type=str, default="Verification failed", help="Failure reason")

    # Subcommand: record-review
    review_parser = subparsers.add_parser("record-review", help="Record Checker review outcome to metrics log")
    review_parser.add_argument("--task-id", type=str, required=True, help="Task ID")
    review_parser.add_argument(
        "--decision",
        type=str,
        required=True,
        help="Review decision (APPROVE or REJECT)",
    )
    review_parser.add_argument("--checker-model", type=str, required=True, help="Checker model")
    review_parser.add_argument("--maker-model", type=str, required=True, help="Maker model")
    review_parser.add_argument("--reason", type=str, default="", help="Review reason")

    # Subcommand: metrics (XXX.4)
    metrics_parser = subparsers.add_parser("metrics", help="Aggregate metrics for a phase and compare with previous phase")
    metrics_parser.add_argument("--phase", type=str, default=None, help="Target phase (e.g. 64 or 'Phase 64')")
    metrics_parser.add_argument("--json", action="store_true", help="Output metrics as JSON")
    metrics_parser.add_argument("--append-to", type=Path, default=None, help="Append phase section to file")

    # Subcommand: recover
    recover_parser = subparsers.add_parser("recover", help="Crash recovery and consistency tools")
    recover_parser.add_argument(
        "action",
        choices=["inspect", "plan", "apply"],
        default="inspect",
        nargs="?",
        help="Recovery action: inspect (diagnose), plan (show plan), apply (execute recovery)",
    )
    recover_parser.add_argument(
        "--json",
        action="store_true",
        help="Output diagnosis or plan in JSON format",
    )

    # Subcommand: bundle
    bundle_parser = subparsers.add_parser("bundle", help="Package results and audit evidence into a sanitized bundle")
    bundle_parser.add_argument("--out", type=Path, default=None, help="Output ZIP path")
    bundle_parser.add_argument("--job-id", type=str, default=None, help="Optional job ID")

    # Subcommand: verify-bundle
    verify_bundle_parser = subparsers.add_parser("verify-bundle", help="Verify integrity and evidence of an exported bundle")
    verify_bundle_parser.add_argument("bundle_path", type=Path, help="Path to bundle ZIP file")
    verify_bundle_parser.add_argument(
        "--json",
        action="store_true",
        help="Output verification result in JSON format",
    )

    # Subcommand: teams
    teams_parser = subparsers.add_parser("teams", help="Multi-team execution commands")
    teams_subparsers = teams_parser.add_subparsers(
        dest="teams_subcommand", help="Multi-team subcommand actions"
    )
    teams_init_parser = teams_subparsers.add_parser(
        "init", help="Initialize teams.json and per-team plan files"
    )
    teams_init_parser.add_argument(
        "--teams",
        type=str,
        default=None,
        help="Comma-separated team IDs (default: core,ui)",
    )
    teams_prepare_parser = teams_subparsers.add_parser(
        "prepare", help="Validate teams.json/clean tree and create per-team worktrees"
    )
    teams_prepare_parser.add_argument(
        "--job-id", type=str, default=None, help="Optional job ID"
    )
    teams_prepare_parser.add_argument(
        "--json", action="store_true", help="Output prepared job info in JSON format"
    )
    teams_status_parser = teams_subparsers.add_parser(
        "status", help="Show per-team worktree, branch, task progress, worker, and quarantine status"
    )
    teams_status_parser.add_argument(
        "--job-id", type=str, default=None, help="Optional job ID (defaults to latest job)"
    )
    teams_status_parser.add_argument(
        "--json", action="store_true", help="Output team status in JSON format"
    )
    teams_cleanup_parser = teams_subparsers.add_parser(
        "cleanup", help="Remove worktrees and team branches for a finished job"
    )
    teams_cleanup_parser.add_argument(
        "--job-id", type=str, required=True, help="Job ID to clean up"
    )
    teams_cleanup_parser.add_argument(
        "--force",
        action="store_true",
        help="Force cleanup even if integration did not succeed or worktrees have uncommitted changes",
    )
    teams_integrate_parser = teams_subparsers.add_parser(
        "integrate", help="Verify team completion/bundles and run IntegrationGate"
    )
    teams_integrate_parser.add_argument(
        "--job-id", type=str, required=True, help="Job ID to integrate"
    )
    teams_integrate_parser.add_argument(
        "--json", action="store_true", help="Output integration result in JSON format"
    )

    args = parser.parse_args(argv)

    work_dir = args.work_dir.resolve()
    plan_path = args.plan or (work_dir / "PLAN.md")
    spec_path = args.spec or (work_dir / "SPEC.md")
    verify_script = args.verify_script or (work_dir / "scripts" / "verify_local.sh")
    report_path = args.report or (work_dir / "NIGHTLY_REPORT.md")
    state_file = args.state_file or (work_dir / ".dev" / "job_state.json")

    git_guard = GitGuard(repo_dir=work_dir)
    dispatcher = TaskDispatcher()
    verifier = DeterministicVerifier()
    reporter = MorningReporter()
    evidence_store = EvidenceStore(repo_dir=work_dir)

    # Subcommand: init
    if args.subcommand == "init":
        return init_project(work_dir)

    # Subcommand: teams
    if args.subcommand == "teams":
        if args.teams_subcommand == "init":
            if args.teams is not None:
                raw_ids = [s.strip() for s in args.teams.split(",") if s.strip()]
                if not raw_ids:
                    print(
                        "TEAMS_CONFIG_ERROR: --teams must specify at least one team ID.",
                        file=sys.stderr,
                    )
                    return 2
                return init_teams_project(work_dir, team_ids=raw_ids)
            return init_teams_project(work_dir, team_ids=None)
        if args.teams_subcommand == "prepare":
            prep_payload, prep_code, prep_err = prepare_teams_job(
                work_dir, job_id=args.job_id
            )
            if prep_payload is None:
                print(prep_err, file=sys.stderr)
                return prep_code
            if args.json:
                print(json.dumps(prep_payload, ensure_ascii=False, indent=2))
            else:
                print(f"Job ID: {prep_payload['job_id']}")
                print(
                    f"Base Ref: {prep_payload['base_ref']} ({str(prep_payload['base_commit'])[:7]})"
                )
                print("Worktrees prepared:")
                for t in prep_payload["teams"]:
                    print(
                        f"  - {t['team_id']}: {t['worktree_path']} (branch: {t['branch']})"
                    )
            return 0
        if args.teams_subcommand == "status":
            status_payload, status_code, status_err = get_teams_status(
                work_dir, job_id=args.job_id
            )
            if status_payload is None:
                print(status_err, file=sys.stderr)
                return status_code
            if args.json:
                print(json.dumps(status_payload, ensure_ascii=False, indent=2))
            else:
                print(format_teams_status_text(status_payload))
            return 0
        if args.teams_subcommand == "cleanup":
            cln_payload, cln_code, cln_err = cleanup_teams_job(
                work_dir, job_id=args.job_id, force=args.force
            )
            if cln_payload is None:
                print(cln_err, file=sys.stderr)
                return cln_code
            print(
                f"Cleaned up job '{cln_payload['job_id']}': "
                f"removed {len(cln_payload['removed_worktrees'])} worktree(s), "
                f"{len(cln_payload['removed_branches'])} branch(es)."
            )
            q_map = cln_payload.get("quarantine_branches") or {}
            if q_map:
                for q_tid, q_branch in q_map.items():
                    print(f"  Quarantined '{q_tid}' work to branch: {q_branch}")
            return 0
        if args.teams_subcommand == "integrate":
            int_payload, int_code, int_err = integrate_teams_job(
                work_dir,
                job_id=args.job_id,
                verify_script=args.verify_script,
                timeout_sec=args.timeout,
            )
            if int_payload is None:
                print(int_err, file=sys.stderr)
                return int_code
            if args.json:
                print(json.dumps(int_payload, ensure_ascii=False, indent=2))
            else:
                print(format_teams_integrate_text(int_payload))
            return 0
        teams_parser.print_help()
        return 2

    # Subcommand: pre-flight
    if args.subcommand == "pre-flight":
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref="HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        try:
            git_guard.assert_clean_working_tree()
            tasks = dispatcher.parse_plan(effective_plan_path)
        except (RuntimeError, FileNotFoundError) as e:
            print(f"PRE-FLIGHT ERROR: {e}", file=sys.stderr)
            return 2
        next_task = dispatcher.get_next_pending_task(tasks)
        checkpoint = git_guard.get_current_checkpoint()
        result: dict[str, Any] = {
            "clean": True,
            "checkpoint": checkpoint,
            "next_task": {
                "id": next_task.task_id,
                "title": next_task.title,
            }
            if next_task
            else None,
        }
        if team_ctx is not None:
            result["team_id"] = team_ctx.team_id
            result["plan"] = team_ctx.plan_rel_path
            result["owns"] = team_ctx.owns_patterns
        print(json.dumps(result, ensure_ascii=False))
        return 0

    elif args.subcommand == "verify-step":
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref=args.checkpoint or "HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        metrics = MetricsLog(repo_dir=work_dir, plan_path=effective_plan_path)
        start_time = time.time()
        phase = metrics.get_current_phase()
        tasks = dispatcher.parse_plan(effective_plan_path)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        if not target:
            print(f"ERROR: Task {args.task_id} not found in {effective_plan_path}", file=sys.stderr)
            return 1

        original_plan_content = effective_plan_path.read_text(encoding="utf-8")
        # 検証対象の最終ツリーに PLAN.md の完了反映を含める
        dispatcher.mark_task_status(effective_plan_path, target, status="completed")

        if not git_guard.check_protected_files_untouched(args.checkpoint):
            effective_plan_path.write_text(original_plan_content, encoding="utf-8")
            print(f"CIRCUIT BREAKER: Protected file modified since {args.checkpoint}", file=sys.stderr)
            attempt = metrics.count_verify_attempts(args.task_id, args.checkpoint, phase=phase) + 1
            metrics.record_event(
                "verify_step",
                task_id=args.task_id,
                checkpoint=args.checkpoint,
                result="CIRCUIT_BREAKER",
                exit_code=10,
                duration_sec=round(time.time() - start_time, 2),
                attempt=attempt,
                phase=phase,
            )
            return 10

        if team_ctx is not None:
            boundary_res = BoundaryGuard(
                repo_dir=work_dir,
                owns_patterns=team_ctx.owns_patterns,
                base_checkpoint=args.checkpoint,
            ).inspect_changes()
            if not boundary_res.allowed:
                effective_plan_path.write_text(original_plan_content, encoding="utf-8")
                print(
                    f"BOUNDARY VIOLATION: Unauthorized modifications outside team '{team_ctx.team_id}' owns: "
                    f"{boundary_res.violations}",
                    file=sys.stderr,
                )
                attempt = metrics.count_verify_attempts(args.task_id, args.checkpoint, phase=phase) + 1
                metrics.record_event(
                    "verify_step",
                    task_id=args.task_id,
                    checkpoint=args.checkpoint,
                    result="BOUNDARY_VIOLATION",
                    exit_code=11,
                    duration_sec=round(time.time() - start_time, 2),
                    attempt=attempt,
                    phase=phase,
                )
                return 11

        code, out = verifier.run_verification(
            verify_script, timeout_sec=args.timeout, cwd=work_dir
        )
        duration_sec = round(time.time() - start_time, 2)
        attempt = metrics.count_verify_attempts(args.task_id, args.checkpoint, phase=phase) + 1
        if code != 0:
            effective_plan_path.write_text(original_plan_content, encoding="utf-8")
            print(f"VERIFICATION FAILED (Exit {code}):\n{out}", file=sys.stderr)
            metrics.record_event(
                "verify_step",
                task_id=args.task_id,
                checkpoint=args.checkpoint,
                result="FAILED",
                exit_code=code,
                duration_sec=duration_sec,
                attempt=attempt,
                phase=phase,
            )
            return code
        tree_sha = git_guard.get_working_tree_sha()
        spec_sha = git_guard.get_spec_sha(spec_path)
        out_lines = [line.strip() for line in out.strip().splitlines() if line.strip()]
        evidence = evidence_store.issue_evidence(
            task_id=args.task_id,
            checkpoint=args.checkpoint,
            tree_sha=tree_sha,
            spec_sha=spec_sha,
            details={
                "verification": {
                    "exit_code": code,
                    "output_summary": out_lines[-10:] if out_lines else [],
                }
            },
        )
        metrics.record_event(
            "verify_step",
            task_id=args.task_id,
            checkpoint=args.checkpoint,
            result="PASSED",
            exit_code=0,
            duration_sec=duration_sec,
            attempt=attempt,
            phase=phase,
        )
        print(f"VERIFICATION PASSED (Evidence Token: {evidence['token']})")
        return 0

    elif args.subcommand == "commit-step":
        chk_ref = args.checkpoint or "HEAD"
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref=chk_ref,
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        metrics = MetricsLog(repo_dir=work_dir, plan_path=effective_plan_path)
        tasks = dispatcher.parse_plan(effective_plan_path)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        if not target:
            print(f"ERROR: Task {args.task_id} not found in {effective_plan_path}", file=sys.stderr)
            return 1

        # Checker Policy 検査 (機能XXX.1 / AGENTS.md Rule 6)
        decision = (args.checker_decision or "").strip().upper()
        maker_fam = detect_model_family(args.maker_model)
        checker_fam = detect_model_family(args.checker_model)

        if decision != "APPROVE":
            print(
                f"CHECKER_POLICY_VIOLATION: Checker review decision must be APPROVE (got '{args.checker_decision}')",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                task_id=args.task_id,
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                checker_decision=decision,
                checker_model=args.checker_model,
                checker_family=checker_fam,
                maker_model=args.maker_model,
                maker_family=maker_fam,
            )
            return 4

        is_independent, violation_msg = verify_model_independence(
            args.maker_model, args.checker_model, strict=True
        )
        if not is_independent:
            print(f"CHECKER_POLICY_VIOLATION: {violation_msg}", file=sys.stderr)
            metrics.record_event(
                "commit_step",
                task_id=args.task_id,
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                checker_decision=decision,
                checker_model=args.checker_model,
                checker_family=checker_fam,
                maker_model=args.maker_model,
                maker_family=maker_fam,
            )
            return 4

        if team_ctx is not None:
            boundary_res = BoundaryGuard(
                repo_dir=work_dir,
                owns_patterns=team_ctx.owns_patterns,
                base_checkpoint=chk_ref,
            ).inspect_changes()
            if not boundary_res.allowed:
                print(
                    f"BOUNDARY ERROR: Unauthorized files detected at commit gate for team '{team_ctx.team_id}': "
                    f"{boundary_res.violations}",
                    file=sys.stderr,
                )
                metrics.record_event(
                    "commit_step",
                    task_id=args.task_id,
                    result="BOUNDARY_VIOLATION",
                    commit=None,
                    checker_decision=decision,
                    checker_model=args.checker_model,
                    checker_family=checker_fam,
                    maker_model=args.maker_model,
                    maker_family=maker_fam,
                )
                return 3

        current_tree_sha = git_guard.get_working_tree_sha()
        current_spec_sha = git_guard.get_spec_sha(spec_path)
        if not evidence_store.verify_and_consume(
            task_id=args.task_id,
            checkpoint=args.checkpoint,
            expected_tree_sha=current_tree_sha,
            expected_spec_sha=current_spec_sha,
        ):
            print(
                f"EVIDENCE ERROR: No valid verification evidence found for task {args.task_id} matching current tree/spec. "
                "Evidence missing or working tree/spec was tampered after verify-step.",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                task_id=args.task_id,
                result="EVIDENCE_ERROR",
                commit=None,
                checker_decision=decision,
                checker_model=args.checker_model,
                checker_family=checker_fam,
                maker_model=args.maker_model,
                maker_family=maker_fam,
            )
            return 3
        commit_hash = git_guard.commit_task(target)
        committed_tree = git_guard.get_commit_tree_sha("HEAD")
        if committed_tree != current_tree_sha:
            print(
                f"FATAL INVARIANT BREACH: Committed tree {committed_tree} does not match verified tree {current_tree_sha}!",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                task_id=args.task_id,
                result="EVIDENCE_ERROR",
                commit=commit_hash,
                checker_decision=decision,
                checker_model=args.checker_model,
                checker_family=checker_fam,
                maker_model=args.maker_model,
                maker_family=maker_fam,
            )
            return 3

        # Checker 査読メタデータの抽出 (機能Q / 機能XXX.1)
        checker_info: dict[str, Any] = {
            "decision": decision,
            "reason": args.checker_reason or "",
            "model": args.checker_model,
            "family": checker_fam,
            "maker_model": args.maker_model,
            "maker_family": maker_fam,
        }
        if args.checker_json:
            try:
                extra = cast(dict[str, Any], json.loads(args.checker_json))
                if isinstance(extra, dict):
                    for k, v in extra.items():
                        if k not in checker_info:
                            checker_info[k] = v
            except (json.JSONDecodeError, ValueError):
                checker_info["raw"] = args.checker_json

        evidence_store.archive_record(
            task_id=args.task_id,
            status="COMPLETED",
            commit_info={
                "hash": commit_hash,
                "sha": commit_hash,
                "commit_sha": commit_hash,
                "tree_sha": committed_tree,
                "tree_verified": True,
            },
            checker_info=checker_info,
        )
        metrics.record_event(
            "commit_step",
            task_id=args.task_id,
            result="COMMITTED",
            commit=commit_hash,
            checker_decision=decision,
            checker_model=args.checker_model,
            checker_family=checker_fam,
            maker_model=args.maker_model,
            maker_family=maker_fam,
        )
        print(f"COMMITTED: {commit_hash}")
        return 0

    elif args.subcommand == "rollback-step":
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref=args.checkpoint or "HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        metrics = MetricsLog(repo_dir=work_dir, plan_path=effective_plan_path)
        tasks = dispatcher.parse_plan(effective_plan_path)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        task_id = target.task_id if target else args.task_id
        evidence_store.clear_evidence(task_id=task_id)
        if target:
            dispatcher.mark_task_status(effective_plan_path, target, status="failed")
        quarantine_branch = git_guard.quarantine_and_restore(
            args.checkpoint,
            task_id=task_id,
            team_id=team_ctx.team_id if team_ctx else "",
        )
        if target:
            log_path = work_dir / "logs" / "nightly" / f"task_{target.task_id}_failure.log"
            reporter.write_failure_log(
                log_path,
                {
                    "task_id": target.task_id,
                    "title": target.title,
                    "checkpoint": args.checkpoint,
                    "quarantined_branch": quarantine_branch,
                    "attempt_1_log": args.reason,
                },
            )
        metrics.record_event(
            "rollback_step",
            task_id=task_id,
            checkpoint=args.checkpoint,
            quarantine_branch=quarantine_branch or None,
            reason=args.reason or "",
        )
        print(f"ROLLED BACK TO {args.checkpoint} (Work quarantined to: {quarantine_branch or 'none'})")
        return 0

    elif args.subcommand == "record-review":
        dec = (args.decision or "").strip().upper()
        if dec not in ("APPROVE", "REJECT"):
            print(f"ERROR: --decision must be APPROVE or REJECT (got '{args.decision}')", file=sys.stderr)
            return 2
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref="HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        maker_fam = detect_model_family(args.maker_model)
        checker_fam = detect_model_family(args.checker_model)
        metrics = MetricsLog(repo_dir=work_dir, plan_path=effective_plan_path)
        metrics.record_event(
            "checker_review",
            task_id=args.task_id,
            decision=dec,
            checker_model=args.checker_model,
            checker_family=checker_fam,
            maker_model=args.maker_model,
            maker_family=maker_fam,
            reason=args.reason or "",
        )
        return 0

    elif args.subcommand == "metrics":
        metrics = MetricsLog(repo_dir=work_dir, plan_path=plan_path)
        target_phase = args.phase or metrics.get_current_phase()
        result = metrics.compute_phase_metrics(target_phase)
        if getattr(args, "append_to", None):
            target_file: Path = args.append_to
            target_phase_name = str(result.get("phase", ""))
            m_num = re.search(r"\d+", target_phase_name)
            if m_num:
                p_num_str = re.escape(m_num.group(0))
                section_pattern = re.compile(rf"^##\s+Phase\s+{p_num_str}\b", re.MULTILINE)
                err_label = f"## Phase {m_num.group(0)}"
            else:
                escaped_name = re.escape(target_phase_name)
                section_pattern = re.compile(rf"^##\s+{escaped_name}\b", re.MULTILINE)
                err_label = f"## {target_phase_name}"

            if target_file.exists():
                existing_text = target_file.read_text(encoding="utf-8")
                if section_pattern.search(existing_text):
                    print(
                        f"ERROR: Section '{err_label}' already exists in {target_file}",
                        file=sys.stderr,
                    )
                    return 1
            report_text = metrics.format_metrics_report(result)
            with open(target_file, "a", encoding="utf-8") as f:
                f.write(f"\n{report_text}\n")
            return 0
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(metrics.format_metrics_report(result))
        return 0

    elif args.subcommand == "report":
        out_p = args.out or report_path
        gen_p = reporter.generate_standalone_report(
            report_path=out_p,
            repo_dir=work_dir,
            plan_path=plan_path,
        )
        print(f"REPORT GENERATED: {gen_p}")
        return 0

    elif args.subcommand in ("brief", "morning-brief"):
        if state_file.exists():
            state = JobState.load(state_file)
            summary = {
                "status": state.status,
                "total_tasks": len(state.completed_tasks) + (1 if state.quarantined_task else 0),
                "succeeded_count": len(state.completed_tasks),
                "quarantined_count": 1 if state.quarantined_task else 0,
                "checkpoint": state.checkpoint_hash,
                "completed_tasks": state.completed_tasks,
                "quarantined_task": state.quarantined_task,
            }
            try:
                reporter.write_report(report_path, summary, repo_dir=work_dir)
            except Exception as e:  # noqa: BLE001
                print(f"REPORT_WARNING: Failed to write report from job_state: {e}", file=sys.stderr)
            print(reporter.format_console_brief(summary))
            return 0
        else:
            try:
                gen_p = reporter.generate_standalone_report(
                    report_path=report_path,
                    repo_dir=work_dir,
                    plan_path=plan_path,
                )
                if gen_p.exists():
                    print(gen_p.read_text(encoding="utf-8"))
                    return 0
            except Exception as e:  # noqa: BLE001
                print(f"REPORT_WARNING: Failed to generate standalone report: {e}", file=sys.stderr)

            if report_path.exists():
                print(report_path.read_text(encoding="utf-8"))
                return 0
            else:
                print("No execution state or report found. Have you run the orchestrator yet?", file=sys.stderr)
                return 1

    elif args.subcommand == "recover":
        from auto_developer.recovery import RecoveryEngine
        engine = RecoveryEngine(
            repo_dir=work_dir,
            state_path=state_file,
            evidence_store=evidence_store,
            git_guard=git_guard,
        )
        as_json = getattr(args, "json", False)

        if args.action == "inspect":
            report = engine.inspect()
            if as_json:
                print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
            else:
                print("=== AUTO DEVELOPER / CRASH RECOVERY INSPECTION ===")
                print(f" Diagnosis:          {report.diagnosis.value}")
                print(f" Can Auto-Recover:   {report.can_auto_recover}")
                print(f" Current Branch:     {report.current_branch} (base: {report.base_branch})")
                print(f" Current HEAD:       {report.head_commit[:7]}")
                print(f" Checkpoint:         {report.checkpoint_commit[:7]}")
                print(f" Working Tree Clean: {report.tree_clean}")
                if report.uncommitted_files:
                    print(f" Uncommitted Files:  {', '.join(report.uncommitted_files)}")
                print(f" Explanation:        {report.explanation}")
                print(f" Recommended Action: {report.recommended_action}")
            return 0 if report.diagnosis.value == "HEALTHY" else 1

        elif args.action == "plan":
            plan = engine.plan()
            if as_json:
                print(json.dumps(plan.to_dict(), ensure_ascii=False, indent=2))
            else:
                print("=== AUTO DEVELOPER / RECOVERY PLAN ===")
                print(f" Diagnosis:        {plan.diagnosis.value}")
                print(f" Can Auto-Recover: {plan.can_auto_recover}")
                print(f" Explanation:      {plan.explanation}")
                print(" Actions to Apply:")
                if plan.actions:
                    for i, act in enumerate(plan.actions, 1):
                        print(f"  {i}. [{act.action_type}] {act.description}")
                else:
                    print("  (None - System is healthy)")
            return 0

        elif args.action == "apply":
            rec_result = engine.apply()
            if as_json:
                print(json.dumps(rec_result.to_dict(), ensure_ascii=False, indent=2))
            else:
                print("=== AUTO DEVELOPER / RECOVERY APPLY ===")
                print(f" Success:         {rec_result.success}")
                print(f" Resulting State: {rec_result.resulting_state}")
                print(f" Message:         {rec_result.message}")
                if rec_result.applied_actions:
                    print(" Applied Actions:")
                    for act_msg in rec_result.applied_actions:
                        print(f"  ✓ {act_msg}")
            return 0 if rec_result.success else 1

    elif args.subcommand == "bundle":
        try:
            reporter.generate_standalone_report(
                report_path=report_path,
                repo_dir=work_dir,
                plan_path=plan_path,
            )
        except Exception as e:  # noqa: BLE001
            print(f"REPORT_WARNING: Failed to refresh report before bundle: {e}", file=sys.stderr)

        bundler = EvidenceBundler(repo_dir=work_dir)
        out_path = bundler.create_bundle(dest_path=args.out, job_id=args.job_id)
        print(f"BUNDLE CREATED: {out_path}")
        return 0

    elif args.subcommand == "verify-bundle":
        bundler = EvidenceBundler(repo_dir=work_dir)
        res = bundler.verify_bundle(args.bundle_path)
        if getattr(args, "json", False):
            print(json.dumps(res.to_dict(), ensure_ascii=False, indent=2))
        else:
            print("=== AUTO DEVELOPER / BUNDLE VERIFICATION ===")
            print(f" Status:         {res.status}")
            print(f" Job ID:         {res.job_id or 'Unknown'}")
            print(f" Head Commit:    {res.head_commit or 'Unknown'}")
            print(f" Verified Files: {len(res.file_hashes)}")
            print(f" Tasks Audited:  {len(res.tasks)}")
            if getattr(res, "audit_stage_verified", False):
                info = getattr(res, "audit_stage_info", {}) or {}
                meta = info.get("audit_metadata", {}) or {}
                prov = meta.get("provider", "independent")
                m_name = meta.get("auditor_model", "N/A")
                print(f" Audit Stage:    VERIFIED (Provider: {prov}, Model: {m_name})")
            if res.errors:
                print(" Errors:")
                for err in res.errors:
                    print(f"  ✗ {err}")
            if res.warnings:
                print(" Warnings:")
                for warn in res.warnings:
                    print(f"  ⚠ {warn}")
        if res.status == "VERIFIED":
            return 0
        elif res.status == "INTEGRITY_ONLY":
            return 2
        else:
            return 1

    # Default Autonomous Loop ("run" or no subcommand):
    explicit_team_id = getattr(args, "team_id", None)
    explicit_owns = _parse_owns_arg(getattr(args, "owns", None))
    explicit_plan = getattr(args, "plan", None)
    team_ctx, ctx_err = _resolve_cli_team_context(
        work_dir,
        ref="HEAD",
        explicit_team_id=explicit_team_id,
        explicit_owns=explicit_owns,
        explicit_plan=explicit_plan,
        allow_explicit_without_manifest=True,
    )
    if ctx_err != 0:
        return ctx_err

    effective_run_plan_path = team_ctx.plan_path if team_ctx else plan_path
    resolved_team_id = (team_ctx.team_id or None) if team_ctx else None
    resolved_owns = (team_ctx.owns_patterns or None) if team_ctx else None

    is_mock = getattr(args, "mock", False)
    maker_cmd = getattr(args, "maker_cmd", "")
    checker_cmd = getattr(args, "checker_cmd", "")
    maker_model = getattr(args, "maker_model", "gemini-3.8-flash")
    checker_model = getattr(args, "checker_model", "claude-opus-5.5")
    enable_audit = getattr(args, "enable_audit", False)
    auditor_cmd = getattr(args, "auditor_cmd", "")
    auditor_model = getattr(args, "auditor_model", "claude-opus-5.5")
    auditor_fallback_str = getattr(args, "auditor_fallback_models", "")
    fallback_models = (
        [m.strip() for m in auditor_fallback_str.split(",") if m.strip()]
        if auditor_fallback_str
        else []
    )
    audit_baseline = getattr(args, "audit_baseline", None)
    dry_run = getattr(args, "dry_run", False)
    resume = getattr(args, "resume", False)
    timeout = getattr(args, "timeout", None) or 300

    if enable_audit and not is_mock and not auditor_cmd:
        print(
            "CONFIG ERROR: In production mode, when independent audit stage is enabled (--enable-audit), --auditor-cmd must be configured.\n"
            "Use --mock if you want to execute with simulated mock agents for testing.",
            file=sys.stderr,
        )
        return 2

    if is_mock:
        runner: RunnerAdapter = MockRunnerAdapter()
    else:
        if not maker_cmd or not checker_cmd:
            print(
                "CONFIG ERROR: In production mode, both Maker and Checker commands must be configured (--maker-cmd and --checker-cmd).\n"
                "Use --mock if you want to execute with simulated mock agents for testing.",
                file=sys.stderr,
            )
            return 2
        runner = CommandRunnerAdapter(
            maker_cmd_template=maker_cmd,
            checker_cmd_template=checker_cmd,
            timeout_sec=timeout,
            cwd=work_dir,
        )

    orchestrator = NightlyOrchestrator(
        repo_dir=work_dir,
        plan_path=effective_run_plan_path,
        spec_path=spec_path,
        verify_script=verify_script,
        report_path=report_path,
        state_path=state_file,
        runner_adapter=runner,
        dry_run=dry_run,
        timeout_sec=timeout,
        resume=resume,
        team_id=resolved_team_id,
        owns_patterns=resolved_owns,
        maker_model=maker_model,
        checker_model=checker_model,
        enable_audit_stage=enable_audit,
        auditor_cmd_template=auditor_cmd,
        auditor_model=auditor_model,
        fallback_models=fallback_models,
        audit_baseline_commit=audit_baseline,
    )
    return orchestrator.run()


if __name__ == "__main__":
    sys.exit(main())
