from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, cast

from auto_developer.audit import resolve_model_family_safe, verify_model_independence
from auto_developer.boundary import BoundaryGuard
from auto_developer.bundler import EvidenceBundler
from auto_developer.demo import run_demo
from auto_developer.dispatcher import TaskDispatcher
from auto_developer.evidence import EvidenceStore, compute_canonical_diff
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
    format_teams_recover_text,
    format_teams_run_text,
    format_teams_status_text,
    get_multi_team_report_summary,
    get_teams_status,
    integrate_teams_job,
    prepare_teams_job,
    recover_teams_job,
    resolve_active_team_job_id,
    run_teams_job,
)
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter
from auto_developer.runner import (
    CommandRunnerAdapter,
    MockRunnerAdapter,
    RunnerAdapter,
    parse_checker_decision,
)
from auto_developer.verifier import (
    RED_FIRST_FILE_TIMEOUT_SEC,
    DeterministicVerifier,
    find_changed_test_files,
    is_red_first_task,
    resolve_red_first_mode,
    resolve_verify_timeout,
    verify_red_first_tests,
)

VERSION = "0.10.0"

DEFAULT_VERIFY_SH = """#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/2] Checking protected file invariants (tracked & untracked) ==="
CHECKPOINT="${1:-HEAD}"

if git rev-parse --verify "$CHECKPOINT" >/dev/null 2>&1; then
  PROTECTED_DIFF=$(git diff --name-only "$CHECKPOINT" -- .github/ SPEC.md scripts/verify_local.sh tests/immunity/ teams.json ':(glob)docs/designs/*.ir.json' 2>/dev/null || true)
  if [ -n "$PROTECTED_DIFF" ]; then
    echo "ERROR: Protected files were modified without authorization (git diff):"
    echo "$PROTECTED_DIFF"
    exit 1
  fi
fi

DIRTY_PROTECTED=$(git status --porcelain -uall | awk '{print $2}' | grep -E '^(\\.github/|SPEC\\.md|scripts/verify_local\\.sh|tests/immunity/|teams\\.json$|docs/designs/[^/]+\\.ir\\.json$)' || true)
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

if [ -z "${PYTHON:-}" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON="python3"
  else
    echo "ERROR: python3 not found to execute tests"
    exit 1
  fi
fi

if [ -d "src" ]; then
  if [ -n "${PYTHONPATH:-}" ]; then
    export PYTHONPATH="$(pwd)/src:${PYTHONPATH}"
  else
    export PYTHONPATH="$(pwd)/src"
  fi
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

DEFAULT_GITIGNORE_ENTRIES: tuple[str, ...] = (
    ".dev/",
    "logs/",
    "NIGHTLY_REPORT.md",
    "__pycache__/",
    "*.py[cod]",
    ".pytest_cache/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".env",
    ".env.*",
    "!.env.example",
)


def _ensure_gitignore_entries(target_dir: Path) -> None:
    """`.gitignore` に Auto Developer と Python の生成物を重複なく追記する。"""
    gitignore = target_dir / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text(encoding="utf-8")
        existing_lines = {line.strip() for line in content.splitlines()}
        to_add = [entry for entry in DEFAULT_GITIGNORE_ENTRIES if entry not in existing_lines]
        if to_add:
            prefix = ""
            if content:
                prefix = content if content.endswith("\n") else content + "\n"
                if not prefix.endswith("\n\n"):
                    prefix += "\n"
            updated = prefix + "# Auto Developer artifacts\n" + "\n".join(to_add) + "\n"
            gitignore.write_text(updated, encoding="utf-8")
            print(f"  ✓ Added {to_add} to .gitignore")
        else:
            print("  - .gitignore already contains Auto Developer artifacts, skipping")
    else:
        gitignore.write_text("\n".join(DEFAULT_GITIGNORE_ENTRIES) + "\n", encoding="utf-8")
        print("  ✓ Created .gitignore with Auto Developer artifacts")


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
    _ensure_gitignore_entries(target_dir)

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


def _compute_canonical_diff(work_dir: Path, checkpoint: str) -> bytes:
    """GATE-5: compute_canonical_diff への委譲。"""
    return compute_canonical_diff(work_dir, checkpoint)


def _verify_evidence_matches_working_tree(work_dir: Path, evidence: dict[str, Any]) -> bool:
    """GATE-13: Evidence の Tree SHA が現在の作業ツリーと一致するか検証する。"""
    expected_tree_sha = str(evidence.get("tree_sha", "")).strip()
    if not expected_tree_sha:
        print(
            "EVIDENCE_ERROR: Verification evidence has no tree_sha. Run `auto-dev verify-step` first.",
            file=sys.stderr,
        )
        return False
    try:
        current_tree_sha = GitGuard(repo_dir=work_dir).get_working_tree_sha()
    except (subprocess.CalledProcessError, OSError) as e:
        print(
            f"EVIDENCE_ERROR: Failed to compute working tree SHA: {e}. Run `auto-dev verify-step` first.",
            file=sys.stderr,
        )
        return False
    if current_tree_sha != expected_tree_sha:
        print(
            "EVIDENCE_ERROR: Working tree has changed since verification. Run `auto-dev verify-step` first.",
            file=sys.stderr,
        )
        return False
    return True


def _format_inv4_fail_fast_message(
    task_id: str,
    checkpoint: str,
    fail_counts: dict[str, int],
) -> str:
    """GATE-15: INV-4 Fail-Fast 停止メッセージを生成する。"""
    return (
        f"INV4_FAIL_FAST: Task {task_id} has {fail_counts['total']} failures "
        f"(verify_failures={fail_counts['verify_failures']}, "
        f"checker_rejects={fail_counts['checker_rejects']}) since last commit/rollback. "
        f"Run `auto-dev rollback-step --checkpoint {checkpoint} --task-id {task_id} "
        f'--reason "INV-4 fail-fast"` to quarantine and halt.'
    )


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


def _refresh_team_report_best_effort(
    reporter: MorningReporter,
    report_path: Path,
    work_dir: Path,
    plan_path: Path,
    *,
    force_create: bool,
) -> None:
    """マルチチーム実行後の NIGHTLY_REPORT.md を更新する（非致命）。"""
    import subprocess as _sp

    should_refresh = force_create or report_path.exists()
    if not should_refresh:
        chk_ign = _sp.run(
            ["git", "check-ignore", "-q", str(report_path)],
            cwd=work_dir,
            capture_output=True,
            check=False,
        )
        should_refresh = chk_ign.returncode == 0
    if not should_refresh:
        return
    try:
        reporter.generate_standalone_report(
            report_path=report_path,
            repo_dir=work_dir,
            plan_path=plan_path,
        )
    except Exception as e:  # noqa: BLE001
        print(f"REPORT_WARNING: Failed to refresh report: {e}", file=sys.stderr)


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


def _detect_current_git_branch(target_dir: Path) -> str | None:
    """現在の Git ブランチ名を返す（コミット前の unborn HEAD を含む）。取得できない場合は None。"""
    try:
        proc = subprocess.run(
            ["git", "symbolic-ref", "--short", "HEAD"],
            cwd=target_dir,
            capture_output=True,
            text=True,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    branch = proc.stdout.strip()
    return branch or None


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
        detected_branch = _detect_current_git_branch(target_dir)
        base_ref = detected_branch if detected_branch is not None else "main"
        ids_to_use = list(team_ids) if team_ids is not None else ["core", "ui"]
        manifest_payload = {
            "version": 1,
            "project": target_dir.name or "auto-developer-project",
            "base": {
                "ref": base_ref,
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

        if detected_branch is None:
            print(
                "  ! Warning: Could not determine current git branch "
                "(detached HEAD or non-git directory); defaulting base.ref to 'main'.",
                file=sys.stderr,
            )

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

    _ensure_gitignore_entries(target_dir)
    return 0


_DEFAULT_MOCK_MAKER_MODEL = "gemini-3.8-flash"
_DEFAULT_MOCK_CHECKER_MODEL = "claude-opus-5.5"
_DEFAULT_MOCK_AUDITOR_MODEL = "claude-opus-5.5"


def _normalize_optional_model(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        return stripped if stripped else None
    return None


def _resolve_cli_run_models(
    *,
    is_mock: bool,
    maker_cmd: str,
    checker_cmd: str,
    raw_maker_model: object,
    raw_checker_model: object,
    enable_audit: bool = False,
    auditor_cmd: str = "",
    raw_auditor_model: object = None,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Resolve CLI model identifiers and reject omitted models when external commands are given (DX-2)."""
    maker_model = _normalize_optional_model(raw_maker_model)
    checker_model = _normalize_optional_model(raw_checker_model)
    auditor_model = _normalize_optional_model(raw_auditor_model)
    if is_mock:
        return (
            maker_model or _DEFAULT_MOCK_MAKER_MODEL,
            checker_model or _DEFAULT_MOCK_CHECKER_MODEL,
            auditor_model or _DEFAULT_MOCK_AUDITOR_MODEL,
            None,
        )

    missing_models: list[str] = []
    if maker_cmd and not maker_model:
        missing_models.append("--maker-model")
    if checker_cmd and not checker_model:
        missing_models.append("--checker-model")
    if enable_audit and auditor_cmd and not auditor_model:
        missing_models.append("--auditor-model")
    if missing_models:
        flags = ", ".join(missing_models)
        err = (
            f"CONFIG ERROR: In production mode, model identifiers must be specified when external commands are configured ({flags}).\n"
            f"Please specify {flags}, or use --mock to run with simulated mock agents."
        )
        return None, None, None, err
    return maker_model, checker_model, auditor_model, None


def _validate_explicit_cli_families(
    *items: tuple[str, str | None, str | None],
) -> str | None:
    """Validate explicit --*-family CLI options against empty/unknown values and built-in detection."""
    for flag, model_name, raw_family in items:
        if raw_family is None:
            continue
        _, _, err = resolve_model_family_safe(model_name or "", raw_family)
        if err is not None:
            return f"PRE-FLIGHT MODEL POLICY ERROR ({flag}): {err}"
    return None


def build_parser() -> argparse.ArgumentParser:
    """CLI の ArgumentParser を構築して返す。"""
    # トップレベルの --checker-* と、サブコマンドの --check が前方一致でぶつかるため
    # （省略形はドキュメントにない暗黙の機能であり、影響がないことを確認済み）
    parser = argparse.ArgumentParser(
        prog="auto-dev",
        description="Auto Developer: Trustworthy Autonomous Pipeline Orchestrator",
        allow_abbrev=False,
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
        "--timeout", type=int, default=None, help="Max verification timeout in seconds (default: 300)"
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
        "--maker-model", type=str, default=None, help="Model identifier for Maker"
    )
    parser.add_argument(
        "--checker-model", type=str, default=None, help="Model identifier for Checker"
    )
    parser.add_argument(
        "--maker-family", type=str, default=None, help="Explicit model family for Maker"
    )
    parser.add_argument(
        "--checker-family", type=str, default=None, help="Explicit model family for Checker"
    )
    parser.add_argument(
        "--enable-audit", action="store_true", help="Enable independent repository audit stage on completion"
    )
    parser.add_argument(
        "--auditor-cmd", type=str, default="", help="Command template to execute independent auditor"
    )
    parser.add_argument(
        "--auditor-model", type=str, default=None, help="Model identifier for Independent Auditor"
    )
    parser.add_argument(
        "--auditor-family", type=str, default=None, help="Explicit model family for Independent Auditor"
    )
    parser.add_argument(
        "--auditor-fallback-models", type=str, default="", help="Comma-separated fallback models for independent auditor"
    )
    parser.add_argument(
        "--audit-baseline", type=str, default=None, help="Baseline commit hash for independent audit comparison"
    )
    parser.add_argument(
        "--audit-paths", type=str, default=None, help="Comma-separated git pathspecs to restrict independent audit diff"
    )
    parser.add_argument(
        "--audit-max-diff-bytes",
        type=int,
        default=200000,
        help="Maximum git diff bytes per independent audit request (default: 200000)",
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand actions")

    # Subcommand: init
    subparsers.add_parser("init", help="Initialize Auto Developer template files in the project")

    # Subcommand: demo
    demo_parser = subparsers.add_parser(
        "demo", help="Run a self-contained demo in a temporary Git repository"
    )
    demo_parser.add_argument(
        "--keep",
        action="store_true",
        help="Keep the temporary demo directory after completion and print its path",
    )
    demo_parser.add_argument(
        "--teams",
        action="store_true",
        help="Run a 2-team concurrent demo with retry, RC tag integration, and dashboard output",
    )

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
    run_parser.add_argument("--dry-run", action="store_true", default=argparse.SUPPRESS, help="Simulate execution without modifying git or state")
    run_parser.add_argument("--timeout", type=int, default=argparse.SUPPRESS, help="Max verification timeout in seconds")
    run_parser.add_argument("--resume", action="store_true", default=argparse.SUPPRESS, help="Resume interrupted job from state file")
    run_parser.add_argument("--mock", action="store_true", default=argparse.SUPPRESS, help="Run with simulated mock agents (for testing only)")
    run_parser.add_argument("--maker-cmd", type=str, default=argparse.SUPPRESS, help="Command template to execute Maker")
    run_parser.add_argument("--checker-cmd", type=str, default=argparse.SUPPRESS, help="Command template to execute Checker")
    run_parser.add_argument("--maker-model", type=str, default=argparse.SUPPRESS, help="Model identifier for Maker")
    run_parser.add_argument("--checker-model", type=str, default=argparse.SUPPRESS, help="Model identifier for Checker")
    run_parser.add_argument("--maker-family", type=str, default=argparse.SUPPRESS, help="Explicit model family for Maker")
    run_parser.add_argument("--checker-family", type=str, default=argparse.SUPPRESS, help="Explicit model family for Checker")
    run_parser.add_argument("--enable-audit", action="store_true", default=argparse.SUPPRESS, help="Enable independent repository audit stage on completion")
    run_parser.add_argument("--auditor-cmd", type=str, default=argparse.SUPPRESS, help="Command template to execute independent auditor")
    run_parser.add_argument("--auditor-model", type=str, default=argparse.SUPPRESS, help="Model identifier for Independent Auditor")
    run_parser.add_argument("--auditor-family", type=str, default=argparse.SUPPRESS, help="Explicit model family for Independent Auditor")
    run_parser.add_argument("--auditor-fallback-models", type=str, default=argparse.SUPPRESS, help="Comma-separated fallback models for independent auditor")
    run_parser.add_argument("--audit-baseline", type=str, default=argparse.SUPPRESS, help="Baseline commit hash for independent audit comparison")
    run_parser.add_argument("--audit-paths", type=str, default=argparse.SUPPRESS, help="Comma-separated git pathspecs to restrict independent audit diff")
    run_parser.add_argument(
        "--audit-max-diff-bytes",
        type=int,
        default=argparse.SUPPRESS,
        help="Maximum git diff bytes per independent audit request (default: 200000)",
    )
    run_parser.add_argument(
        "--dashboard",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Auto-update HTML dashboard on each event during this run",
    )

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
    verify_parser.add_argument(
        "--timeout",
        type=int,
        default=argparse.SUPPRESS,
        help="Max verification timeout in seconds",
    )

    # Subcommand: commit-step
    commit_parser = subparsers.add_parser("commit-step", help="Commit current task and mark [x] in PLAN.md")
    commit_parser.add_argument("--task-id", type=str, required=True, help="Task ID to commit")
    commit_parser.add_argument("--checkpoint", type=str, default=None, help="Optional checkpoint commit hash to verify")
    commit_parser.add_argument("--maker-model", type=str, required=True, help="Model identifier for Maker")
    commit_parser.add_argument("--checker-model", type=str, required=True, help="Model identifier for Checker")
    commit_parser.add_argument("--maker-family", type=str, default=None, help="Explicit model family for Maker")
    commit_parser.add_argument("--checker-family", type=str, default=None, help="Explicit model family for Checker")
    commit_parser.add_argument(
        "--checker-decision",
        type=str,
        default=None,
        help="Optional Checker review decision (verified against recorded review)",
    )
    commit_parser.add_argument("--checker-reason", type=str, default=None, help="Checker review justification")
    commit_parser.add_argument("--checker-json", type=str, default=None, help="Checker review structured JSON string")

    # Subcommand: rollback-step
    rollback_parser = subparsers.add_parser("rollback-step", help="Rollback working tree and mark task as failed")
    rollback_parser.add_argument("--checkpoint", type=str, required=True, help="Checkpoint commit hash")
    rollback_parser.add_argument("--task-id", type=str, required=True, help="Task ID to mark failed")
    rollback_parser.add_argument("--reason", type=str, default="Verification failed", help="Failure reason")

    # Subcommand: review-input (GATE-5)
    review_input_parser = subparsers.add_parser(
        "review-input", help="Write canonical diff for Checker review without modifying git index"
    )
    review_input_parser.add_argument("--task-id", type=str, required=True, help="Task ID")
    review_input_parser.add_argument("--out-dir", type=Path, required=True, help="Output directory")

    # Subcommand: record-review
    review_parser = subparsers.add_parser("record-review", help="Record Checker review outcome from output file")
    review_parser.add_argument("--task-id", type=str, required=True, help="Task ID")
    review_parser.add_argument("--checker-output", type=Path, default=None, help="Path to Checker output file")
    review_parser.add_argument("--diff-file", type=Path, default=None, help="Path to canonical diff file")
    review_parser.add_argument("--spec-file", type=Path, default=None, help="Path to SPEC excerpt file")
    review_parser.add_argument(
        "--decision",
        type=str,
        default=None,
        help="Optional declared decision (verified against --checker-output)",
    )
    review_parser.add_argument("--checker-model", type=str, required=True, help="Checker model")
    review_parser.add_argument("--maker-model", type=str, required=True, help="Maker model")
    review_parser.add_argument("--maker-family", type=str, default=None, help="Explicit model family for Maker")
    review_parser.add_argument("--checker-family", type=str, default=None, help="Explicit model family for Checker")
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

    # Subcommand: status
    status_parser = subparsers.add_parser("status", help="Show repository and task status snapshot")
    status_parser.add_argument("--json", action="store_true", help="Output status snapshot in JSON format")

    # Subcommand: dashboard
    dashboard_parser = subparsers.add_parser("dashboard", help="Generate self-contained HTML status dashboard")
    dashboard_parser.add_argument(
        "--out", type=Path, default=None, help="Output HTML file path (default: .dev/dashboard.html)"
    )
    dashboard_parser.add_argument(
        "--watch",
        nargs="?",
        const=5,
        default=None,
        type=int,
        metavar="SECONDS",
        help="Regenerate dashboard HTML every N seconds (default: 5) until Ctrl-C",
    )
    dashboard_parser.add_argument(
        "--enable",
        action="store_true",
        help="Enable automatic dashboard regeneration on STAT-4 events (.dev/dashboard.json)",
    )
    dashboard_parser.add_argument(
        "--disable",
        action="store_true",
        help="Disable automatic dashboard regeneration (.dev/dashboard.json)",
    )

    # Subcommand: compile
    compile_parser = subparsers.add_parser(
        "compile",
        help="Validate auto-dev-ir/1 JSON, extract design outline, or emit drafts under .dev/compile/ (INV-12)",
    )
    compile_parser.add_argument(
        "--check",
        type=Path,
        default=None,
        metavar="IR_JSON",
        help="Validate an auto-dev-ir/1 JSON file",
    )
    compile_parser.add_argument(
        "--outline",
        type=Path,
        default=None,
        metavar="DESIGN_MD",
        help="Extract heading outline from a design markdown file",
    )
    compile_parser.add_argument(
        "--emit",
        type=Path,
        default=None,
        metavar="IR_JSON",
        help="Emit draft artifacts under .dev/compile/ from an auto-dev-ir/1 JSON file",
    )
    compile_parser.add_argument(
        "--anchor",
        type=str,
        default=None,
        metavar="PATH:START-END",
        help="Compute CMP-1 source anchor {path, lines, sha256} for a file line range",
    )
    compile_parser.add_argument(
        "--impact",
        type=Path,
        default=None,
        metavar="IR_JSON",
        help="Analyze potential repository impact from an auto-dev-ir/1 JSON file (INV-12)",
    )
    compile_parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        metavar="DIR",
        help="Output directory under .dev/compile/ for --emit (default: .dev/compile/<name>/)",
    )
    compile_parser.add_argument(
        "--lang",
        choices=["ja", "en"],
        default="ja",
        help="Language for emitted draft headings and boilerplate (default: ja)",
    )
    compile_parser.add_argument(
        "--force",
        action="store_true",
        help="Overwrite existing generated files in --out-dir",
    )
    compile_parser.add_argument(
        "--spec",
        type=Path,
        default=argparse.SUPPRESS,
        metavar="SPEC_MD",
        help="Path to SPEC.md for L-6 check (defaults to <work-dir>/SPEC.md if present)",
    )
    compile_parser.add_argument(
        "--json",
        action="store_true",
        help="Output compile result in JSON format",
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
    teams_run_parser = teams_subparsers.add_parser(
        "run", help="Prepare worktrees, execute all team workers concurrently, and integrate"
    )
    teams_run_parser.add_argument(
        "--job-id", type=str, default=None, help="Optional job ID"
    )
    teams_run_parser.add_argument(
        "--worker-timeout",
        type=float,
        default=21600.0,
        help="Per-worker execution timeout in seconds (default: 21600)",
    )
    teams_run_parser.add_argument(
        "--mock",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Run workers with simulated mock agents (for testing only)",
    )
    teams_run_parser.add_argument(
        "--maker-cmd",
        type=str,
        default=argparse.SUPPRESS,
        help="Command template to execute Maker in each team worker",
    )
    teams_run_parser.add_argument(
        "--checker-cmd",
        type=str,
        default=argparse.SUPPRESS,
        help="Command template to execute Checker in each team worker",
    )
    teams_run_parser.add_argument(
        "--maker-model",
        type=str,
        default=argparse.SUPPRESS,
        help="Model identifier for Maker",
    )
    teams_run_parser.add_argument(
        "--checker-model",
        type=str,
        default=argparse.SUPPRESS,
        help="Model identifier for Checker",
    )
    teams_run_parser.add_argument(
        "--maker-family",
        type=str,
        default=argparse.SUPPRESS,
        help="Explicit model family for Maker",
    )
    teams_run_parser.add_argument(
        "--checker-family",
        type=str,
        default=argparse.SUPPRESS,
        help="Explicit model family for Checker",
    )
    teams_run_parser.add_argument(
        "--timeout",
        type=int,
        default=argparse.SUPPRESS,
        help="Max verification timeout in seconds for each worker and integration gate",
    )
    teams_run_parser.add_argument(
        "--verify-script",
        type=Path,
        default=argparse.SUPPRESS,
        help="Path to verification script",
    )
    teams_run_parser.add_argument(
        "--spec",
        type=Path,
        default=argparse.SUPPRESS,
        help="Path to SPEC.md",
    )
    teams_run_parser.add_argument(
        "--json", action="store_true", help="Output run result in JSON format"
    )
    teams_run_parser.add_argument(
        "--dashboard",
        action="store_true",
        default=argparse.SUPPRESS,
        help="Auto-update HTML dashboard on each event during this run",
    )
    teams_recover_parser = teams_subparsers.add_parser(
        "recover", help="Recover interrupted multi-team job (orphaned workers/bundles/integration)"
    )
    teams_recover_parser.add_argument(
        "--job-id", type=str, default=None, help="Optional job ID (defaults to latest job)"
    )
    teams_recover_parser.add_argument(
        "--verify-script",
        type=Path,
        default=argparse.SUPPRESS,
        help="Path to verification script",
    )
    teams_recover_parser.add_argument(
        "--json", action="store_true", help="Output recovery result in JSON format"
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI エントリーポイント。"""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.subcommand == "demo":
        return run_demo(keep=bool(args.keep), teams=bool(args.teams))

    if args.subcommand == "compile":
        from auto_developer.compiler import (
            anchor_source_range,
            check_ir_file,
            emit_ir_drafts,
            format_anchor_text,
            format_outline_text,
            outline_design_file,
        )
        from auto_developer.impact import analyze_ir_impact, format_impact_text

        selected_modes = [
            name
            for name, val in (
                ("--check", args.check),
                ("--outline", args.outline),
                ("--emit", args.emit),
                ("--anchor", args.anchor),
                ("--impact", args.impact),
            )
            if val is not None
        ]
        if len(selected_modes) > 1:
            print(
                f"ERROR: {' and '.join(selected_modes)} cannot be used together.",
                file=sys.stderr,
            )
            return 2
        if len(selected_modes) == 0:
            print(
                "ERROR: --check <IR_JSON>, --outline <DESIGN_MD>, --emit <IR_JSON>, --anchor <PATH:START-END>, or --impact <IR_JSON> is required.",
                file=sys.stderr,
            )
            return 2

        work_dir = args.work_dir.resolve()
        if args.impact is not None:
            ir_path = args.impact if args.impact.is_absolute() else (work_dir / args.impact)
            exit_code, payload, issues, error_msg = analyze_ir_impact(ir_path, work_dir=work_dir)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif error_msg is not None:
                print(f"ERROR: {error_msg}", file=sys.stderr)
            elif exit_code != 0:
                for issue in issues:
                    print(issue.format_line())
            else:
                for line in format_impact_text(payload):
                    print(line)
            return exit_code

        if args.anchor is not None:
            exit_code, payload, error_msg = anchor_source_range(args.anchor, work_dir=work_dir)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif error_msg is not None:
                print(f"ERROR: {error_msg}", file=sys.stderr)
            else:
                print(format_anchor_text(payload))
            return exit_code

        if args.outline is not None:
            doc_path = args.outline if args.outline.is_absolute() else (work_dir / args.outline)
            exit_code, payload, error_msg = outline_design_file(doc_path, work_dir=work_dir)
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif error_msg is not None:
                print(f"ERROR: {error_msg}", file=sys.stderr)
            else:
                for line in format_outline_text(payload):
                    print(line)
            return exit_code

        spec_arg: Path | None = getattr(args, "spec", None)
        resolved_spec: Path | None = None
        if spec_arg is not None:
            resolved_spec = spec_arg if spec_arg.is_absolute() else (work_dir / spec_arg)

        if args.emit is not None:
            ir_path = args.emit if args.emit.is_absolute() else (work_dir / args.emit)
            exit_code, payload, issues, error_msg = emit_ir_drafts(
                ir_path,
                work_dir=work_dir,
                out_dir=args.out_dir,
                spec_path=resolved_spec,
                lang=args.lang,
                force=bool(args.force),
            )
            if args.json:
                print(json.dumps(payload, ensure_ascii=False, indent=2))
            elif error_msg is not None:
                print(f"ERROR: {error_msg}", file=sys.stderr)
            elif exit_code != 0:
                for issue in issues:
                    print(issue.format_line())
            else:
                for written in payload.get("files", []):
                    print(written)
            return exit_code

        ir_path = args.check if args.check.is_absolute() else (work_dir / args.check)
        exit_code, payload, issues = check_ir_file(ir_path, work_dir=work_dir, spec_path=resolved_spec)
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            if issues:
                for issue in issues:
                    print(issue.format_line())
            else:
                print(f"OK: {ir_path}")
        return exit_code

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
    if args.subcommand == "status":
        from auto_developer.status import collect_status_snapshot, format_status_text

        snapshot = collect_status_snapshot(work_dir, plan_path=args.plan)
        if args.json:
            print(json.dumps(snapshot, ensure_ascii=False, indent=2))
        else:
            print(format_status_text(snapshot))
        return 0

    if args.subcommand == "dashboard":
        from auto_developer.dashboard import (
            DEFAULT_DASHBOARD_REFRESH_SECONDS,
            resolve_dashboard_output_path,
            resolve_main_repo_dir,
            save_dashboard_config,
            watch_dashboard,
            write_dashboard,
        )

        if args.enable and args.disable:
            print("ERROR: --enable and --disable cannot be used together.", file=sys.stderr)
            return 2
        if args.watch is not None and (args.enable or args.disable):
            print("ERROR: --watch cannot be combined with --enable or --disable.", file=sys.stderr)
            return 2
        if args.enable:
            cfg_path = save_dashboard_config(work_dir, enabled=True, out_path=args.out)
            main_repo = resolve_main_repo_dir(work_dir)
            resolved_out = (
                (work_dir.resolve() / args.out).resolve()
                if args.out is not None and not args.out.is_absolute()
                else (args.out.resolve() if args.out is not None else resolve_dashboard_output_path(main_repo))
            )
            print(f"DASHBOARD AUTO-UPDATE ENABLED: {cfg_path}")
            try:
                out_file = write_dashboard(
                    main_repo,
                    out_path=resolved_out,
                    plan_path=args.plan,
                    refresh_seconds=DEFAULT_DASHBOARD_REFRESH_SECONDS,
                )
                print(f"DASHBOARD WRITTEN: {out_file}")
            except Exception as e:  # noqa: BLE001
                print(f"DASHBOARD_WARNING: Failed to write initial dashboard: {e}", file=sys.stderr)
            return 0
        if args.disable:
            cfg_path = save_dashboard_config(work_dir, enabled=False)
            print(f"DASHBOARD AUTO-UPDATE DISABLED: {cfg_path}")
            return 0
        if args.watch is not None:
            if args.watch < 1:
                print("ERROR: --watch interval must be >= 1 second.", file=sys.stderr)
                return 2
            return watch_dashboard(
                work_dir,
                out_path=args.out,
                plan_path=args.plan,
                interval_sec=args.watch,
            )
        out_file = write_dashboard(work_dir, out_path=args.out, plan_path=args.plan)
        print(f"DASHBOARD WRITTEN: {out_file}")
        return 0

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
                if int_code != 2:
                    _refresh_team_report_best_effort(
                        reporter, report_path, work_dir, plan_path, force_create=False
                    )
                print(int_err, file=sys.stderr)
                return int_code
            _refresh_team_report_best_effort(
                reporter, report_path, work_dir, plan_path, force_create=True
            )
            if args.json:
                print(json.dumps(int_payload, ensure_ascii=False, indent=2))
            else:
                print(format_teams_integrate_text(int_payload))
            return 0
        if args.teams_subcommand == "run":
            teams_is_mock = bool(getattr(args, "mock", False))
            teams_dashboard = bool(getattr(args, "dashboard", False))
            teams_maker_cmd = getattr(args, "maker_cmd", "")
            teams_checker_cmd = getattr(args, "checker_cmd", "")
            teams_maker_family = getattr(args, "maker_family", None)
            teams_checker_family = getattr(args, "checker_family", None)
            if not teams_is_mock and (not teams_maker_cmd or not teams_checker_cmd):
                run_payload, run_code, run_err = run_teams_job(
                    work_dir,
                    job_id=args.job_id,
                    maker_cmd=teams_maker_cmd,
                    checker_cmd=teams_checker_cmd,
                    maker_model=None,
                    checker_model=None,
                    maker_family=teams_maker_family,
                    checker_family=teams_checker_family,
                    is_mock=False,
                    worker_timeout=args.worker_timeout,
                    verify_timeout=args.timeout,
                    verify_script=args.verify_script,
                    spec_path=args.spec,
                    dashboard=teams_dashboard,
                )
            else:
                teams_maker_model, teams_checker_model, _, teams_model_err = _resolve_cli_run_models(
                    is_mock=teams_is_mock,
                    maker_cmd=teams_maker_cmd,
                    checker_cmd=teams_checker_cmd,
                    raw_maker_model=getattr(args, "maker_model", None),
                    raw_checker_model=getattr(args, "checker_model", None),
                )
                if teams_model_err is not None:
                    print(teams_model_err, file=sys.stderr)
                    return 2
                teams_fam_err = _validate_explicit_cli_families(
                    ("--maker-family", teams_maker_model, teams_maker_family),
                    ("--checker-family", teams_checker_model, teams_checker_family),
                )
                if teams_fam_err is not None:
                    print(teams_fam_err, file=sys.stderr)
                    return 2
                run_payload, run_code, run_err = run_teams_job(
                    work_dir,
                    job_id=args.job_id,
                    maker_cmd=teams_maker_cmd,
                    checker_cmd=teams_checker_cmd,
                    maker_model=teams_maker_model,
                    checker_model=teams_checker_model,
                    maker_family=teams_maker_family,
                    checker_family=teams_checker_family,
                    is_mock=teams_is_mock,
                    worker_timeout=args.worker_timeout,
                    verify_timeout=args.timeout,
                    verify_script=args.verify_script,
                    spec_path=args.spec,
                    dashboard=teams_dashboard,
                )
            if run_code != 2:
                _refresh_team_report_best_effort(
                    reporter,
                    report_path,
                    work_dir,
                    plan_path,
                    force_create=run_payload is not None,
                )
            if run_payload is None:
                print(run_err, file=sys.stderr)
                return run_code
            if args.json:
                print(json.dumps(run_payload, ensure_ascii=False, indent=2))
            else:
                print(format_teams_run_text(run_payload))
            return 0
        if args.teams_subcommand == "recover":
            rec_payload, rec_code, rec_err = recover_teams_job(
                work_dir,
                job_id=args.job_id,
                verify_script=args.verify_script,
            )
            if rec_payload is not None:
                _refresh_team_report_best_effort(
                    reporter,
                    report_path,
                    work_dir,
                    plan_path,
                    force_create=rec_code == 0,
                )
                if args.json:
                    print(json.dumps(rec_payload, ensure_ascii=False, indent=2))
                else:
                    print(format_teams_recover_text(rec_payload))
                return rec_code
            print(rec_err, file=sys.stderr)
            return rec_code
        for action in parser._actions:
            if isinstance(action, argparse._SubParsersAction) and "teams" in action.choices:
                action.choices["teams"].print_help()
                break
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
        if next_task is not None:
            try:
                metrics = MetricsLog(
                    repo_dir=work_dir,
                    plan_path=effective_plan_path,
                    team_id=team_ctx.team_id if team_ctx else None,
                )
                pf_phase = metrics.get_current_phase()
                pf_attempt = metrics.count_verify_attempts(next_task.task_id, checkpoint, phase=pf_phase) + 1
                metrics.record_event(
                    "task_start",
                    task_id=next_task.task_id,
                    checkpoint=checkpoint,
                    attempt=pf_attempt,
                    phase=pf_phase,
                )
            except Exception as e:  # noqa: BLE001
                print(f"METRICS_WARNING: Failed to record task_start event: {e}", file=sys.stderr)
        print(json.dumps(result, ensure_ascii=False))
        return 0

    elif args.subcommand == "verify-step":
        raw_chk = args.checkpoint or "HEAD"
        rev_proc = subprocess.run(
            ["git", "rev-parse", "--verify", f"{raw_chk}^{{commit}}"],
            cwd=work_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if rev_proc.returncode != 0 or not rev_proc.stdout.strip():
            print(
                f"ERROR: Cannot resolve checkpoint ref '{raw_chk}' to a commit SHA.",
                file=sys.stderr,
            )
            return 1
        args.checkpoint = rev_proc.stdout.strip()
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref=args.checkpoint or "HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        metrics = MetricsLog(
            repo_dir=work_dir,
            plan_path=effective_plan_path,
            team_id=team_ctx.team_id if team_ctx else None,
        )
        start_time = time.time()
        phase = metrics.get_current_phase()
        tasks = dispatcher.parse_plan(effective_plan_path)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        if not target:
            print(f"ERROR: Task {args.task_id} not found in {effective_plan_path}", file=sys.stderr)
            return 1

        try:
            verify_timeout, verify_timeout_source = resolve_verify_timeout(args.timeout, verify_script)
            red_first_mode = resolve_red_first_mode(verify_script)
        except ValueError as timeout_err:
            print(f"CONFIG ERROR: {timeout_err}", file=sys.stderr)
            return 2

        fail_counts = metrics.count_task_failures_since_reset(
            args.task_id,
            phase=phase,
            team_id=team_ctx.team_id if team_ctx else None,
        )
        if fail_counts["total"] >= 2:
            print(
                _format_inv4_fail_fast_message(args.task_id, args.checkpoint, fail_counts),
                file=sys.stderr,
            )
            attempt = metrics.count_verify_attempts(args.task_id, args.checkpoint, phase=phase) + 1
            metrics.record_event(
                "verify_step",
                task_id=args.task_id,
                checkpoint=args.checkpoint,
                result="FAIL_FAST_STOPPED",
                exit_code=1,
                duration_sec=round(time.time() - start_time, 2),
                attempt=attempt,
                phase=phase,
            )
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

        red_first_payload: dict[str, Any] | None = None
        if red_first_mode != "off":
            if is_red_first_task(target.title):
                try:
                    target_test_files = find_changed_test_files(work_dir, args.checkpoint)
                except RuntimeError as rf_err:
                    effective_plan_path.write_text(original_plan_content, encoding="utf-8")
                    print(f"RED_FIRST_ERROR: {rf_err}", file=sys.stderr)
                    return 2
                if not target_test_files:
                    rf_result = "NO_TEST_CHANGES"
                    rf_file_results: list[dict[str, Any]] = []
                else:
                    rf_check = verify_red_first_tests(
                        work_dir,
                        args.checkpoint,
                        target_test_files,
                        timeout_sec=RED_FIRST_FILE_TIMEOUT_SEC,
                    )
                    rf_result = str(rf_check["result"])
                    rf_file_results = list(rf_check["file_results"])
                red_first_payload = {
                    "mode": red_first_mode,
                    "result": rf_result,
                    "test_files": target_test_files,
                    "file_results": rf_file_results,
                }
                rf_attempt = metrics.count_verify_attempts(args.task_id, args.checkpoint, phase=phase) + 1
                metrics.record_event(
                    "red_first",
                    task_id=args.task_id,
                    checkpoint=args.checkpoint,
                    mode=red_first_mode,
                    result=rf_result,
                    test_files=target_test_files,
                    file_results=rf_file_results,
                    attempt=rf_attempt,
                    phase=phase,
                )
                if rf_result != "CONFIRMED":
                    rf_summary = (
                        ", ".join(
                            f"{r['file']}={r['result']}(checkpoint={r['reason']}, current={r.get('current_result', 'SKIPPED')}:{r.get('current_reason', 'SKIPPED')})"
                            for r in rf_file_results
                        )
                        if rf_file_results
                        else "no changed test files"
                    )
                    if red_first_mode == "warn":
                        print(
                            f"RED_FIRST_WARNING: result={rf_result} files={target_test_files} ({rf_summary})",
                            file=sys.stderr,
                        )
                    elif red_first_mode == "enforce":
                        effective_plan_path.write_text(original_plan_content, encoding="utf-8")
                        print(
                            f"RED_FIRST_NOT_CONFIRMED: result={rf_result} files={target_test_files} ({rf_summary})",
                            file=sys.stderr,
                        )
                        duration_sec = round(time.time() - start_time, 2)
                        attempt = metrics.count_verify_attempts(args.task_id, args.checkpoint, phase=phase) + 1
                        metrics.record_event(
                            "verify_step",
                            task_id=args.task_id,
                            checkpoint=args.checkpoint,
                            result="RED_FIRST_NOT_CONFIRMED",
                            exit_code=1,
                            duration_sec=duration_sec,
                            attempt=attempt,
                            phase=phase,
                        )
                        return 1
            else:
                red_first_payload = {
                    "mode": red_first_mode,
                    "result": "SKIPPED",
                    "test_files": [],
                    "file_results": [],
                }

        code, out = verifier.run_verification(
            verify_script,
            timeout_sec=verify_timeout,
            cwd=work_dir,
            timeout_source=verify_timeout_source,
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
        if team_ctx is not None:
            post_boundary_res = BoundaryGuard(
                repo_dir=work_dir,
                owns_patterns=team_ctx.owns_patterns,
                base_checkpoint=args.checkpoint,
            ).inspect_changes()
            if not post_boundary_res.allowed:
                effective_plan_path.write_text(original_plan_content, encoding="utf-8")
                evidence_store.clear_evidence(task_id=args.task_id)
                violated_list = "\n".join(f"  - {f}" for f in post_boundary_res.violations)
                print(
                    f"BOUNDARY VIOLATION: Unauthorized modifications outside team '{team_ctx.team_id}' owns "
                    f"detected after verification:\n{violated_list}\n"
                    "検証で生成されたファイルなら .gitignore に追加してください。",
                    file=sys.stderr,
                )
                metrics.record_event(
                    "verify_step",
                    task_id=args.task_id,
                    checkpoint=args.checkpoint,
                    result="BOUNDARY_VIOLATION",
                    exit_code=1,
                    duration_sec=duration_sec,
                    attempt=attempt,
                    phase=phase,
                )
                return 1
        tree_sha = git_guard.get_working_tree_sha()
        secret_files = git_guard.find_secret_files_in_tree(args.checkpoint, tree_sha)
        if secret_files:
            effective_plan_path.write_text(original_plan_content, encoding="utf-8")
            print(
                f"SECRET_FILE_IN_COMMIT: {', '.join(secret_files)} — remove or add to .gitignore before verifying.",
                file=sys.stderr,
            )
            metrics.record_event(
                "verify_step",
                task_id=args.task_id,
                checkpoint=args.checkpoint,
                result="FAILED",
                exit_code=1,
                duration_sec=duration_sec,
                attempt=attempt,
                phase=phase,
            )
            return 1
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
            red_first=red_first_payload,
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
        raw_chk = args.checkpoint or "HEAD"
        rev_proc = subprocess.run(
            ["git", "rev-parse", "--verify", f"{raw_chk}^{{commit}}"],
            cwd=work_dir,
            check=False,
            capture_output=True,
            text=True,
        )
        if rev_proc.returncode != 0 or not rev_proc.stdout.strip():
            print(
                f"ERROR: Cannot resolve checkpoint ref '{raw_chk}' to a commit SHA.",
                file=sys.stderr,
            )
            return 1
        chk_ref = rev_proc.stdout.strip()
        args.checkpoint = chk_ref
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref=chk_ref,
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        metrics = MetricsLog(
            repo_dir=work_dir,
            plan_path=effective_plan_path,
            team_id=team_ctx.team_id if team_ctx else None,
        )
        tasks = dispatcher.parse_plan(effective_plan_path)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        if not target:
            print(f"ERROR: Task {args.task_id} not found in {effective_plan_path}", file=sys.stderr)
            return 1

        phase = metrics.get_current_phase()
        fail_counts = metrics.count_task_failures_since_reset(
            args.task_id,
            phase=phase,
            team_id=team_ctx.team_id if team_ctx else None,
        )
        if fail_counts["total"] >= 2:
            print(
                _format_inv4_fail_fast_message(args.task_id, chk_ref, fail_counts),
                file=sys.stderr,
            )
            return 4

        # Checker Policy 検査 (機能XXX.1 / AGENTS.md Rule 6 / DX-7 / GATE-6)
        declared_decision = (
            args.checker_decision.strip().upper() if args.checker_decision is not None else None
        )
        maker_fam, maker_fam_source, _ = resolve_model_family_safe(args.maker_model, args.maker_family)
        checker_fam, checker_fam_source, _ = resolve_model_family_safe(
            args.checker_model, args.checker_family
        )
        commit_event_base: dict[str, Any] = {
            "task_id": args.task_id,
            "checker_decision": declared_decision or "",
            "checker_model": args.checker_model,
            "checker_family": checker_fam,
            "checker_family_source": checker_fam_source,
            "maker_model": args.maker_model,
            "maker_family": maker_fam,
            "maker_family_source": maker_fam_source,
        }

        if declared_decision is not None and declared_decision != "APPROVE":
            print(
                f"CHECKER_POLICY_VIOLATION: Checker review decision must be APPROVE (got '{args.checker_decision}')",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                **commit_event_base,
            )
            return 4

        is_independent, violation_msg = verify_model_independence(
            args.maker_model,
            args.checker_model,
            strict=True,
            maker_family=args.maker_family,
            auditor_family=args.checker_family,
        )
        if not is_independent:
            print(f"CHECKER_POLICY_VIOLATION: {violation_msg}", file=sys.stderr)
            metrics.record_event(
                "commit_step",
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                **commit_event_base,
            )
            return 4

        # GATE-6: Evidence 消費前に、最新の検証に対応する査読記録・出力ファイル・差分・モデルを照合
        commit_evidence = evidence_store.get_evidence(args.task_id)
        if commit_evidence is None:
            print(
                f"EVIDENCE ERROR: No valid verification evidence found for task {args.task_id}.",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="EVIDENCE_ERROR",
                commit=None,
                **commit_event_base,
            )
            return 3

        review_record = evidence_store.get_review_record(args.task_id)
        if review_record is None or str(review_record.get("evidence_token", "")) != str(
            commit_evidence.get("token", "")
        ):
            print(
                f"CHECKER_POLICY_VIOLATION: No recorded review matching the latest verification evidence for task {args.task_id}",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                **commit_event_base,
            )
            return 4

        recorded_decision = str(review_record.get("decision", "")).strip().upper()
        commit_event_base["checker_decision"] = recorded_decision
        if recorded_decision != "APPROVE":
            print(
                f"CHECKER_POLICY_VIOLATION: Recorded review decision is '{recorded_decision}', must be APPROVE",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                **commit_event_base,
            )
            return 4

        if declared_decision is not None and declared_decision != recorded_decision:
            print(
                f"CHECKER_POLICY_VIOLATION: Declared --checker-decision '{declared_decision}' does not match recorded '{recorded_decision}'",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                **commit_event_base,
            )
            return 4

        recorded_out_path = Path(str(review_record.get("checker_output_path", "")))
        if not recorded_out_path.is_file():
            print(
                f"CHECKER_POLICY_VIOLATION: Recorded checker output file missing: {recorded_out_path}",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                **commit_event_base,
            )
            return 4
        actual_out_sha256 = hashlib.sha256(recorded_out_path.read_bytes()).hexdigest()
        if actual_out_sha256 != str(review_record.get("checker_output_sha256", "")):
            print(
                "CHECKER_POLICY_VIOLATION: Checker output file was modified after record-review",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                **commit_event_base,
            )
            return 4

        if (
            str(review_record.get("maker_model", "")) != args.maker_model
            or str(review_record.get("checker_model", "")) != args.checker_model
            or str(review_record.get("maker_family", "")) != maker_fam
            or str(review_record.get("checker_family", "")) != checker_fam
        ):
            print(
                "CHECKER_POLICY_VIOLATION: Maker/Checker model or family does not match recorded review",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="CHECKER_POLICY_VIOLATION",
                commit=None,
                **commit_event_base,
            )
            return 4

        if team_ctx is not None:
            boundary_res = BoundaryGuard(
                repo_dir=work_dir,
                owns_patterns=team_ctx.owns_patterns,
                base_checkpoint=chk_ref,
            ).inspect_changes()
            if not boundary_res.allowed:
                evidence_store.clear_evidence(task_id=args.task_id)
                print(
                    f"BOUNDARY ERROR: Unauthorized files detected at commit gate for team '{team_ctx.team_id}': "
                    f"{boundary_res.violations}\n"
                    f"Evidence has been invalidated. Run `auto-dev rollback-step --checkpoint {chk_ref} "
                    f"--task-id {args.task_id}` to quarantine the changes and restore the worktree.",
                    file=sys.stderr,
                )
                metrics.record_event(
                    "commit_step",
                    result="BOUNDARY_VIOLATION",
                    commit=None,
                    **commit_event_base,
                )
                return 3

        current_tree_sha = git_guard.get_working_tree_sha()
        secret_files = git_guard.find_secret_files_in_tree(chk_ref, current_tree_sha)
        if secret_files:
            print(
                f"SECRET_FILE_IN_COMMIT: {', '.join(secret_files)} — remove or add to .gitignore before committing.",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="SECRET_FILE_IN_COMMIT",
                commit=None,
                **commit_event_base,
            )
            return 4

        try:
            current_diff_bytes = _compute_canonical_diff(
                work_dir, str(commit_evidence.get("checkpoint", chk_ref))
            )
            current_diff_sha256 = hashlib.sha256(current_diff_bytes).hexdigest()
        except subprocess.CalledProcessError:
            current_diff_sha256 = ""
        if current_diff_sha256 != str(review_record.get("diff_sha256", "")):
            print(
                "EVIDENCE ERROR: Current canonical diff does not match recorded review diff.",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="EVIDENCE_ERROR",
                commit=None,
                **commit_event_base,
            )
            return 3

        decision = recorded_decision

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
                result="EVIDENCE_ERROR",
                commit=None,
                **commit_event_base,
            )
            return 3
        commit_hash = git_guard.commit_task(target, tree_sha=current_tree_sha)
        committed_tree = git_guard.get_commit_tree_sha("HEAD")
        if committed_tree != current_tree_sha:
            print(
                f"FATAL INVARIANT BREACH: Committed tree {committed_tree} does not match verified tree {current_tree_sha}!",
                file=sys.stderr,
            )
            metrics.record_event(
                "commit_step",
                result="EVIDENCE_ERROR",
                commit=commit_hash,
                **commit_event_base,
            )
            return 3

        # Checker 査読メタデータの抽出 (機能Q / 機能XXX.1 / DX-7 / GATE-6)
        checker_info: dict[str, Any] = {
            "decision": decision,
            "reason": args.checker_reason or "",
            "model": args.checker_model,
            "family": checker_fam,
            "family_source": checker_fam_source,
            "maker_model": args.maker_model,
            "maker_family": maker_fam,
            "maker_family_source": maker_fam_source,
            "review": {
                "output_sha256": review_record["checker_output_sha256"],
                "diff_sha256": review_record["diff_sha256"],
                "spec_sha256": review_record["spec_sha256"],
                "recorded_at": review_record["recorded_at"],
                "decision_source": "checker_output",
            },
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
            result="COMMITTED",
            commit=commit_hash,
            **commit_event_base,
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
        metrics = MetricsLog(
            repo_dir=work_dir,
            plan_path=effective_plan_path,
            team_id=team_ctx.team_id if team_ctx else None,
        )
        tasks = dispatcher.parse_plan(effective_plan_path)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        task_id = target.task_id if target else args.task_id
        evidence_store.clear_evidence(task_id=task_id)
        if target:
            dispatcher.mark_task_status(effective_plan_path, target, status="failed")
        rollback_job_id = ""
        if team_ctx:
            rollback_job_id = resolve_active_team_job_id(work_dir, team_id=team_ctx.team_id) or ""
        if not rollback_job_id and state_file.is_file():
            try:
                rollback_job_id = JobState.load(state_file).job_id
            except (OSError, json.JSONDecodeError, ValueError):
                rollback_job_id = ""
        from auto_developer.guard import QuarantineObjectError, format_snapshot_only_files_lines

        try:
            quarantine_branch = git_guard.quarantine_and_restore(
                args.checkpoint,
                task_id=task_id,
                job_id=rollback_job_id,
                team_id=team_ctx.team_id if team_ctx else "",
            )
        except QuarantineObjectError as exc:
            if target:
                log_path = work_dir / "logs" / "nightly" / f"task_{target.task_id}_failure.log"
                base_reason = args.reason or "Verification failed"
                reporter.write_failure_log(
                    log_path,
                    {
                        "task_id": target.task_id,
                        "title": target.title,
                        "checkpoint": args.checkpoint,
                        "quarantined_branch": "",
                        "snapshot_path": None,
                        "snapshot_only_files": [],
                        "snapshot_only_error": str(exc),
                        "failure_phase": "ROLLBACK",
                        "reason": f"{base_reason} ({exc})",
                        "attempt_1_log": str(exc),
                    },
                )
            print(f"ROLLBACK FAILED: {exc}", file=sys.stderr)
            return 1
        snapshot_path = git_guard.last_quarantine_snapshot
        snapshot_only_files = list(git_guard.last_snapshot_only_files)
        snapshot_only_error = git_guard.last_snapshot_only_error
        git_object_files = list(git_guard.last_git_object_files)
        if target:
            log_path = work_dir / "logs" / "nightly" / f"task_{target.task_id}_failure.log"
            reporter.write_failure_log(
                log_path,
                {
                    "task_id": target.task_id,
                    "title": target.title,
                    "checkpoint": args.checkpoint,
                    "quarantined_branch": quarantine_branch,
                    "snapshot_path": str(snapshot_path) if snapshot_path else None,
                    "snapshot_only_files": snapshot_only_files,
                    "snapshot_only_error": snapshot_only_error,
                    "git_object_files": git_object_files,
                    "failure_phase": "ROLLBACK",
                    "reason": args.reason or "Verification failed",
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
        for snap_line in format_snapshot_only_files_lines(
            snapshot_path,
            snapshot_only_files,
            repo_dir=work_dir,
            error_reason=snapshot_only_error,
            git_object_files=git_object_files,
        ):
            print(snap_line)
        return 0

    elif args.subcommand == "review-input":
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref="HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        evidence_store = EvidenceStore(repo_dir=work_dir)
        review_evidence = evidence_store.get_evidence(args.task_id)
        if review_evidence is None:
            print(f"EVIDENCE_ERROR: No valid verification evidence for task {args.task_id}", file=sys.stderr)
            return 3
        checkpoint = str(review_evidence.get("checkpoint", "")).strip()
        if not checkpoint:
            print(f"EVIDENCE_ERROR: Evidence for task {args.task_id} has no checkpoint", file=sys.stderr)
            return 3
        if not _verify_evidence_matches_working_tree(work_dir, review_evidence):
            return 3
        try:
            diff_bytes = _compute_canonical_diff(work_dir, checkpoint)
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to compute canonical diff from {checkpoint}: {e}", file=sys.stderr)
            return 3
        out_dir: Path = args.out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        diff_out = out_dir / "diff.patch"
        diff_out.write_bytes(diff_bytes)

        red_first_data = review_evidence.get("red_first")
        if not isinstance(red_first_data, dict):
            red_first_data = {
                "mode": "off",
                "result": "OFF",
                "test_files": [],
                "file_results": [],
            }
        red_first_out = out_dir / "red_first.json"
        red_first_out.write_text(json.dumps(red_first_data, ensure_ascii=False, indent=2), encoding="utf-8")

        diff_sha256 = hashlib.sha256(diff_bytes).hexdigest()
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        metrics = MetricsLog(
            repo_dir=work_dir,
            plan_path=effective_plan_path,
            team_id=team_ctx.team_id if team_ctx else None,
        )
        metrics.record_event(
            "review_input",
            task_id=args.task_id,
            checkpoint=checkpoint,
            diff_sha256=diff_sha256,
            evidence_issued_at=review_evidence.get("issued_at"),
        )

        print(f"REVIEW_INPUT_WRITTEN: {diff_out}")
        return 0

    elif args.subcommand == "record-review":
        if args.checker_output is None or args.diff_file is None or args.spec_file is None:
            print(
                "ERROR: Legacy record-review without --checker-output/--diff-file/--spec-file is no longer supported.\n"
                "Usage: auto-dev record-review --task-id N --checker-output FILE --diff-file FILE "
                "--spec-file FILE --maker-model M --checker-model M [--maker-family F] "
                "[--checker-family F] [--decision D] [--reason R]",
                file=sys.stderr,
            )
            return 2

        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref="HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        maker_fam, maker_fam_source, maker_fam_err = resolve_model_family_safe(
            args.maker_model, args.maker_family
        )
        if maker_fam_err is not None:
            print(f"PRE-FLIGHT MODEL POLICY ERROR (--maker-family): {maker_fam_err}", file=sys.stderr)
            return 2
        checker_fam, checker_fam_source, checker_fam_err = resolve_model_family_safe(
            args.checker_model, args.checker_family
        )
        if checker_fam_err is not None:
            print(f"PRE-FLIGHT MODEL POLICY ERROR (--checker-family): {checker_fam_err}", file=sys.stderr)
            return 2

        evidence_store = EvidenceStore(repo_dir=work_dir)
        target_evidence = evidence_store.get_evidence(args.task_id)
        if target_evidence is None:
            print(f"EVIDENCE_ERROR: No valid verification evidence for task {args.task_id}", file=sys.stderr)
            return 3
        if not _verify_evidence_matches_working_tree(work_dir, target_evidence):
            return 3
        if evidence_store.has_decided_review_for_evidence(args.task_id, target_evidence):
            print(
                f"CHECKER_POLICY_VIOLATION: Review already recorded for task {args.task_id} "
                "on the current verification evidence; re-run verify-step before recording another review",
                file=sys.stderr,
            )
            return 4

        checker_output_path: Path = args.checker_output
        if not checker_output_path.is_file():
            print(
                f"CHECKER_POLICY_VIOLATION: Checker output file not found: {checker_output_path}",
                file=sys.stderr,
            )
            return 4
        try:
            output_text = checker_output_path.read_text(encoding="utf-8")
        except OSError as e:
            print(f"CHECKER_POLICY_VIOLATION: Failed to read checker output: {e}", file=sys.stderr)
            return 4

        dec = parse_checker_decision(output_text)
        if dec is not None and args.decision is not None:
            declared = args.decision.strip().upper()
            if declared != dec:
                print(
                    f"CHECKER_POLICY_VIOLATION: Declared --decision '{declared}' does not match checker output '{dec}'",
                    file=sys.stderr,
                )
                return 4

        diff_file: Path = args.diff_file
        if not diff_file.is_file():
            print(f"EVIDENCE_ERROR: Diff file not found: {diff_file}", file=sys.stderr)
            return 3
        checkpoint = str(target_evidence.get("checkpoint", "")).strip()
        try:
            expected_diff = _compute_canonical_diff(work_dir, checkpoint)
        except subprocess.CalledProcessError as e:
            print(f"EVIDENCE_ERROR: Failed to compute canonical diff: {e}", file=sys.stderr)
            return 3
        if diff_file.read_bytes() != expected_diff:
            print("EVIDENCE_ERROR: --diff-file does not match current canonical diff", file=sys.stderr)
            return 3

        spec_excerpt_file: Path = args.spec_file
        if not spec_excerpt_file.is_file():
            print(f"CHECKER_POLICY_VIOLATION: Spec file not found: {spec_excerpt_file}", file=sys.stderr)
            return 4
        spec_excerpt = spec_excerpt_file.read_text(encoding="utf-8")
        full_spec = spec_path.read_text(encoding="utf-8") if spec_path.exists() else ""
        if not spec_excerpt.strip() or spec_excerpt not in full_spec:
            print(
                "CHECKER_POLICY_VIOLATION: --spec-file must be a non-empty contiguous excerpt of SPEC.md",
                file=sys.stderr,
            )
            return 4

        if dec is None:
            evidence_store.record_review(
                task_id=args.task_id,
                evidence=target_evidence,
                decision="",
                checker_output_path=checker_output_path,
                diff_path=diff_file,
                spec_path=spec_excerpt_file,
                maker_model=args.maker_model,
                maker_family=maker_fam,
                maker_family_source=maker_fam_source,
                checker_model=args.checker_model,
                checker_family=checker_fam,
                checker_family_source=checker_fam_source,
                reason=args.reason or "",
            )
            print(
                "CHECKER_POLICY_VIOLATION: Checker output must contain exactly one DECISION line (APPROVE or REJECT)",
                file=sys.stderr,
            )
            return 4

        evidence_store.record_review(
            task_id=args.task_id,
            evidence=target_evidence,
            decision=dec,
            checker_output_path=checker_output_path,
            diff_path=diff_file,
            spec_path=spec_excerpt_file,
            maker_model=args.maker_model,
            maker_family=maker_fam,
            maker_family_source=maker_fam_source,
            checker_model=args.checker_model,
            checker_family=checker_fam,
            checker_family_source=checker_fam_source,
            reason=args.reason or "",
        )

        metrics = MetricsLog(
            repo_dir=work_dir,
            plan_path=effective_plan_path,
            team_id=team_ctx.team_id if team_ctx else None,
        )
        metrics.record_event(
            "checker_review",
            task_id=args.task_id,
            decision=dec,
            checker_model=args.checker_model,
            checker_family=checker_fam,
            checker_family_source=checker_fam_source,
            maker_model=args.maker_model,
            maker_family=maker_fam,
            maker_family_source=maker_fam_source,
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
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref="HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        out_p = args.out or report_path
        gen_p = reporter.generate_standalone_report(
            report_path=out_p,
            repo_dir=work_dir,
            plan_path=effective_plan_path,
        )
        print(f"REPORT GENERATED: {gen_p}")
        return 0

    elif args.subcommand in ("brief", "morning-brief"):
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref="HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        mt_summary: dict[str, Any] | None = None
        try:
            mt_summary = get_multi_team_report_summary(work_dir)
        except Exception as e:  # noqa: BLE001
            print(f"REPORT_WARNING: Failed to summarize multi-team job: {e}", file=sys.stderr)

        if mt_summary is not None:
            try:
                gen_p = reporter.generate_standalone_report(
                    report_path=report_path,
                    repo_dir=work_dir,
                    plan_path=effective_plan_path,
                )
                if gen_p.exists():
                    print(gen_p.read_text(encoding="utf-8"))
                    return 0
            except Exception as e:  # noqa: BLE001
                print(f"REPORT_WARNING: Failed to generate standalone report: {e}", file=sys.stderr)

        if state_file.exists():
            from auto_developer.evidence import resolve_evidence_records_dir

            state = JobState.load(state_file)
            total_tasks = len(state.completed_tasks) + (1 if state.quarantined_task else 0)
            if total_tasks == 0 and effective_plan_path.is_file():
                try:
                    total_tasks = len(TaskDispatcher().parse_plan(effective_plan_path))
                except OSError:
                    total_tasks = 0
            summary: dict[str, Any] = {
                "status": state.status,
                "total_tasks": total_tasks,
                "succeeded_count": len(state.completed_tasks),
                "quarantined_count": 1 if state.quarantined_task else 0,
                "checkpoint": state.checkpoint_hash,
                "completed_tasks": state.completed_tasks,
                "quarantined_task": state.quarantined_task,
            }
            if mt_summary is not None:
                summary["multi_team"] = mt_summary
            else:
                for cand_audit in [
                    resolve_evidence_records_dir(work_dir) / "audit_stage.json",
                    work_dir / ".dev" / "evidence" / "records" / "audit_stage.json",
                    work_dir / ".git" / "nightly_evidence" / "audit_stage.json",
                    work_dir / ".dev" / "audit_stage.json",
                ]:
                    if cand_audit.is_file():
                        try:
                            summary["audit_stage"] = json.loads(cand_audit.read_text(encoding="utf-8"))
                            break
                        except (json.JSONDecodeError, OSError):
                            pass
            try:
                reporter.write_report(
                    report_path,
                    summary,
                    repo_dir=work_dir,
                    plan_path=effective_plan_path,
                )
            except Exception as e:  # noqa: BLE001
                print(f"REPORT_WARNING: Failed to write report from job_state: {e}", file=sys.stderr)
            print(reporter.format_console_brief(summary))
            return 0
        else:
            try:
                gen_p = reporter.generate_standalone_report(
                    report_path=report_path,
                    repo_dir=work_dir,
                    plan_path=effective_plan_path,
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
        team_ctx, ctx_err = _resolve_cli_team_context(
            work_dir,
            ref="HEAD",
            explicit_plan=args.plan,
        )
        if ctx_err != 0:
            return ctx_err
        effective_plan_path = team_ctx.plan_path if team_ctx else plan_path
        try:
            reporter.generate_standalone_report(
                report_path=report_path,
                repo_dir=work_dir,
                plan_path=effective_plan_path,
            )
        except Exception as e:  # noqa: BLE001
            print(f"REPORT_WARNING: Failed to refresh report before bundle: {e}", file=sys.stderr)

        bundler = EvidenceBundler(repo_dir=work_dir, plan_path=effective_plan_path)
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

    is_mock = bool(getattr(args, "mock", False))
    maker_cmd = getattr(args, "maker_cmd", "")
    checker_cmd = getattr(args, "checker_cmd", "")
    enable_audit = bool(getattr(args, "enable_audit", False))
    auditor_cmd = getattr(args, "auditor_cmd", "")
    auditor_fallback_str = getattr(args, "auditor_fallback_models", "")
    fallback_models = (
        [m.strip() for m in auditor_fallback_str.split(",") if m.strip()]
        if auditor_fallback_str
        else []
    )
    audit_baseline = getattr(args, "audit_baseline", None)
    raw_audit_paths = getattr(args, "audit_paths", None)
    audit_paths = (
        [p.strip() for p in raw_audit_paths.split(",") if p.strip()]
        if raw_audit_paths
        else None
    )
    audit_max_diff_bytes = int(getattr(args, "audit_max_diff_bytes", 200000))
    if audit_max_diff_bytes <= 0:
        print("CONFIG ERROR: --audit-max-diff-bytes must be a positive integer", file=sys.stderr)
        return 2
    dry_run = bool(getattr(args, "dry_run", False))
    resume = bool(getattr(args, "resume", False))
    try:
        timeout, timeout_source = resolve_verify_timeout(getattr(args, "timeout", None), verify_script)
    except ValueError as timeout_err:
        print(f"CONFIG ERROR: {timeout_err}", file=sys.stderr)
        return 2

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

    maker_model, checker_model, auditor_model, model_err = _resolve_cli_run_models(
        is_mock=is_mock,
        maker_cmd=maker_cmd,
        checker_cmd=checker_cmd,
        raw_maker_model=getattr(args, "maker_model", None),
        raw_checker_model=getattr(args, "checker_model", None),
        enable_audit=enable_audit,
        auditor_cmd=auditor_cmd,
        raw_auditor_model=getattr(args, "auditor_model", None),
    )
    if model_err is not None:
        print(model_err, file=sys.stderr)
        return 2

    maker_family = getattr(args, "maker_family", None)
    checker_family = getattr(args, "checker_family", None)
    auditor_family = getattr(args, "auditor_family", None)
    fam_err = _validate_explicit_cli_families(
        ("--maker-family", maker_model, maker_family),
        ("--checker-family", checker_model, checker_family),
        ("--auditor-family", auditor_model, auditor_family),
    )
    if fam_err is not None:
        print(fam_err, file=sys.stderr)
        return 2

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
        timeout_source=timeout_source,
        resume=resume,
        team_id=resolved_team_id,
        owns_patterns=resolved_owns,
        maker_model=maker_model,
        checker_model=checker_model,
        maker_family=maker_family,
        checker_family=checker_family,
        enable_audit_stage=enable_audit,
        auditor_cmd_template=auditor_cmd,
        auditor_model=auditor_model,
        auditor_family=auditor_family,
        fallback_models=fallback_models,
        audit_baseline_commit=audit_baseline,
        audit_paths=audit_paths,
        audit_max_diff_bytes=audit_max_diff_bytes,
    )
    use_dashboard = bool(getattr(args, "dashboard", False)) and not dry_run
    prev_dash_env = os.environ.get("AUTO_DEV_DASHBOARD")
    if use_dashboard:
        from auto_developer.metrics import try_update_dashboard

        os.environ["AUTO_DEV_DASHBOARD"] = "1"
        try_update_dashboard(work_dir, plan_path=effective_run_plan_path)
    try:
        return orchestrator.run()
    finally:
        if use_dashboard:
            if prev_dash_env is None:
                os.environ.pop("AUTO_DEV_DASHBOARD", None)
            else:
                os.environ["AUTO_DEV_DASHBOARD"] = prev_dash_env


if __name__ == "__main__":
    sys.exit(main())
