---
name: nightly-run
description: Antigravity Autonomous Pipeline Orchestrator. Executes PLAN.md tasks sequentially overnight with [Implement -> Verify -> Review -> Commit] cycle and strict Fail-Fast rollback.
---

# Nightly Run: Autonomous Pipeline Orchestration

This skill orchestrates overnight autonomous development using the Elephant-Goldfish Model (EGM) standards defined in `SPEC.md` and `docs/designs/autonomous_pipeline_orchestrator.md`. It can be run either directly via the Antigravity skill or via the standalone CLI `auto-dev run`.

## Execution Overview

When the user runs `/nightly-run` (or invokes `auto-dev run` / `python3 scripts/nightly_orchestrator.py`), the process follows the strict EGM pipeline:
1. **Pre-flight Assertion**: Verify that the Git working tree is completely clean (`git status --porcelain` must be empty). Refuse to run if uncommitted changes exist.
2. **Sequential Task Loop**: Read `PLAN.md` and process each pending task (`- [ ]`) strictly in order:
   - **Step A (Maker Implementation)**: Spawn a fresh stateless subagent (or execute in an isolated turn) using the **Maker Prompt** from `PROMPTS.md`. Pass only the target task, relevant `SPEC.md` requirements, and `LESSONS.md`.
   - **Step B (External Invariant Check)**: Verify protected files (including untracked files via `git status --porcelain`). If `.github/**`, `SPEC.md`, `scripts/verify_local.sh`, or immunity tests were created or modified, trigger immediate Quarantine rollback.
   - **Step C (Deterministic Verification & Evidence)**: Run `auto-dev verify-step --checkpoint <checkpoint> --task-id <task_id>` (or `python3 scripts/nightly_orchestrator.py verify-step ...`). This checks invariants, executes `scripts/verify_local.sh`, and issues a cryptographic Evidence Token on success. If it fails on the 1st attempt, feed the stderr back to Maker for 1 retry. If it fails a 2nd time, run `rollback-step` to safely quarantine work into a `quarantine/task-<id>-*` branch and halt immediately (Strict Fail-Fast).
   - **Step D (Stateless Checker Review)**: Spawn a fresh stateless subagent using the **Checker Prompt** from `PROMPTS.md`. Pass only `SPEC.md`, `git diff <checkpoint>`, and the verification output. Checker must return strictly `APPROVE` or `REJECT`.
   - **Step E (Evidence-gated Commit & Advance)**: On approval, commit via `auto-dev commit-step --task-id <task_id> --checkpoint <checkpoint>`. The orchestrator verifies and consumes the Evidence Token, commits the changes, and marks the task as `- [x]` in `PLAN.md`.
3. **Morning Handoff**: Generate `NIGHTLY_REPORT.md` (Morning Brief) at the project root and output an actionable summary with next choices for the human developer. The developer can also run `auto-dev brief` (or `python3 scripts/nightly_orchestrator.py morning-brief`) anytime from the terminal.
