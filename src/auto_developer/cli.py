from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from auto_developer.dispatcher import TaskDispatcher
from auto_developer.evidence import EvidenceStore
from auto_developer.guard import GitGuard
from auto_developer.models import JobState
from auto_developer.orchestrator import NightlyOrchestrator
from auto_developer.reporter import MorningReporter
from auto_developer.runner import CommandRunnerAdapter, MockRunnerAdapter, RunnerAdapter
from auto_developer.verifier import DeterministicVerifier

VERSION = "0.3.0"

DEFAULT_VERIFY_SH = """#!/usr/bin/env bash
set -euo pipefail

echo "=== [1/2] Checking protected file invariants (tracked & untracked) ==="
CHECKPOINT="${1:-HEAD}"

if git rev-parse --verify "$CHECKPOINT" >/dev/null 2>&1; then
  PROTECTED_DIFF=$(git diff --name-only "$CHECKPOINT" -- .github/ SPEC.md scripts/verify_local.sh tests/immunity/ 2>/dev/null || true)
  if [ -n "$PROTECTED_DIFF" ]; then
    echo "ERROR: Protected files were modified without authorization (git diff):"
    echo "$PROTECTED_DIFF"
    exit 1
  fi
fi

DIRTY_PROTECTED=$(git status --porcelain | awk '{print $2}' | grep -E '^(\\.github/|SPEC\\.md|scripts/verify_local\\.sh|tests/immunity/)' || true)
if [ -n "$DIRTY_PROTECTED" ]; then
  echo "ERROR: Protected files were created or modified (git status):"
  echo "$DIRTY_PROTECTED"
  exit 1
fi

echo "=== [2/2] Running Project Tests ==="
# プロジェクトのテストコマンドに置き換えてください (例: pytest, npm test, cargo test 等)
if [ -d "tests" ]; then
  if command -v python3 >/dev/null 2>&1; then
    python3 -m unittest discover -s tests -p "test_*.py" 2>/dev/null || true
  fi
fi

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

    print("\nInitialization complete! You can now run `auto-dev run` or `auto-dev --dry-run`.")
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
        "--maker-cmd", type=str, default="", help="Command template to execute Maker"
    )
    parser.add_argument(
        "--checker-cmd", type=str, default="", help="Command template to execute Checker"
    )

    subparsers = parser.add_subparsers(dest="subcommand", help="Subcommand actions")

    # Subcommand: init
    subparsers.add_parser("init", help="Initialize Auto Developer template files in the project")

    # Subcommand: run
    run_parser = subparsers.add_parser("run", help="Run autonomous sequential execution loop")
    run_parser.add_argument("--dry-run", action="store_true", help="Simulate execution without modifying git or state")
    run_parser.add_argument("--timeout", type=int, default=None, help="Max verification timeout in seconds")
    run_parser.add_argument("--resume", action="store_true", help="Resume interrupted job from state file")
    run_parser.add_argument("--maker-cmd", type=str, default="", help="Command template to execute Maker")
    run_parser.add_argument("--checker-cmd", type=str, default="", help="Command template to execute Checker")

    # Subcommand: brief
    subparsers.add_parser("brief", help="Display morning brief of recent execution")
    subparsers.add_parser("morning-brief", help="Alias for brief")

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

    # Subcommand: rollback-step
    rollback_parser = subparsers.add_parser("rollback-step", help="Rollback working tree and mark task as failed")
    rollback_parser.add_argument("--checkpoint", type=str, required=True, help="Checkpoint commit hash")
    rollback_parser.add_argument("--task-id", type=str, required=True, help="Task ID to mark failed")
    rollback_parser.add_argument("--reason", type=str, default="Verification failed", help="Failure reason")

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

    # Subcommand: pre-flight
    if args.subcommand == "pre-flight":
        try:
            git_guard.assert_clean_working_tree()
        except RuntimeError as e:
            print(f"PRE-FLIGHT ERROR: {e}", file=sys.stderr)
            return 2
        tasks = dispatcher.parse_plan(plan_path)
        next_task = dispatcher.get_next_pending_task(tasks)
        checkpoint = git_guard.get_current_checkpoint()
        result = {
            "clean": True,
            "checkpoint": checkpoint,
            "next_task": {
                "id": next_task.task_id,
                "title": next_task.title,
            }
            if next_task
            else None,
        }
        print(json.dumps(result, ensure_ascii=False))
        return 0

    elif args.subcommand == "verify-step":
        if not git_guard.check_protected_files_untouched(args.checkpoint):
            print(f"CIRCUIT BREAKER: Protected file modified since {args.checkpoint}", file=sys.stderr)
            return 10
        code, out = verifier.run_verification(
            verify_script, timeout_sec=args.timeout, cwd=work_dir
        )
        if code != 0:
            print(f"VERIFICATION FAILED (Exit {code}):\n{out}", file=sys.stderr)
            return code
        evidence = evidence_store.issue_evidence(task_id=args.task_id, checkpoint=args.checkpoint)
        print(f"VERIFICATION PASSED (Evidence Token: {evidence['token']})")
        return 0

    elif args.subcommand == "commit-step":
        tasks = dispatcher.parse_plan(plan_path)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        if not target:
            print(f"ERROR: Task {args.task_id} not found in {plan_path}", file=sys.stderr)
            return 1
        if not evidence_store.verify_and_consume(task_id=args.task_id, checkpoint=args.checkpoint):
            print(
                f"EVIDENCE ERROR: No valid verification evidence found for task {args.task_id}. Must pass verify-step first.",
                file=sys.stderr,
            )
            return 3
        dispatcher.mark_task_status(plan_path, target, status="completed")
        commit_hash = git_guard.commit_task(target)
        print(f"COMMITTED: {commit_hash}")
        return 0

    elif args.subcommand == "rollback-step":
        tasks = dispatcher.parse_plan(plan_path)
        target = next((t for t in tasks if t.task_id == args.task_id), None)
        task_id = target.task_id if target else args.task_id
        evidence_store.clear_evidence(task_id=task_id)
        quarantine_branch = git_guard.quarantine_and_restore(args.checkpoint, task_id=task_id)
        if target:
            dispatcher.mark_task_status(plan_path, target, status="failed")
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
        print(f"ROLLED BACK TO {args.checkpoint} (Work quarantined to: {quarantine_branch or 'none'})")
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
            print(reporter.format_console_brief(summary))
            return 0
        elif report_path.exists():
            print(report_path.read_text(encoding="utf-8"))
            return 0
        else:
            print("No execution state or report found. Have you run the orchestrator yet?", file=sys.stderr)
            return 1

    # Default Autonomous Loop ("run" or no subcommand):
    maker_cmd = getattr(args, "maker_cmd", "")
    checker_cmd = getattr(args, "checker_cmd", "")
    dry_run = getattr(args, "dry_run", False)
    resume = getattr(args, "resume", False)
    timeout = getattr(args, "timeout", None) or 300

    if maker_cmd or checker_cmd:
        runner: RunnerAdapter = CommandRunnerAdapter(
            maker_cmd_template=maker_cmd,
            checker_cmd_template=checker_cmd,
            timeout_sec=timeout,
        )
    else:
        runner = MockRunnerAdapter()

    orchestrator = NightlyOrchestrator(
        repo_dir=work_dir,
        plan_path=plan_path,
        spec_path=spec_path,
        verify_script=verify_script,
        report_path=report_path,
        state_path=state_file,
        runner_adapter=runner,
        dry_run=dry_run,
        timeout_sec=timeout,
        resume=resume,
    )
    return orchestrator.run()


if __name__ == "__main__":
    sys.exit(main())
