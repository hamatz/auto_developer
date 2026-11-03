---
name: nightly-run
description: Antigravity Autonomous Pipeline Orchestrator. Executes PLAN.md tasks sequentially overnight with [Implement -> Verify -> Review -> Commit] cycle and strict Fail-Fast rollback.
---

# Nightly Run: Autonomous Pipeline Orchestration

This skill orchestrates overnight autonomous development using the Elephant-Goldfish Model (EGM) standards defined in `SPEC.md` and `docs/designs/autonomous_pipeline_orchestrator.md`.

## Execution Overview

When the user runs `/nightly-run`, you act as the master orchestrator:
1. **Pre-flight Assertion**: Verify that the Git working tree is completely clean (`git status --porcelain` must be empty). Refuse to run if uncommitted changes exist.
2. **Sequential Task Loop**: Read `PLAN.md` and process each pending task (`- [ ]`) strictly in order:
   - **Step A (Maker Implementation)**: Spawn a fresh stateless subagent (or execute in an isolated turn) using the **Maker Prompt** from `PROMPTS.md`. Pass only the target task, relevant `SPEC.md` requirements, and `LESSONS.md`.
   - **Step B (External Invariant Check)**: Verify `git diff --name-only <checkpoint>`. If `.github/**`, `SPEC.md`, `scripts/verify_local.sh`, or immunity tests were modified, trigger immediate Quarantine rollback.
   - **Step C (Deterministic Verification)**: Run `bash scripts/verify_local.sh`. If it fails on the 1st attempt, feed the stderr back to Maker for 1 retry. If it fails a 2nd time, trigger Circuit Breaker rollback (`git reset --hard <checkpoint> && git clean -fd`), mark the task `- [ ] <id>. <title> **[FAILED]**`, generate `NIGHTLY_REPORT.md`, and halt immediately (Strict Fail-Fast).
   - **Step D (Stateless Checker Review)**: Spawn a fresh stateless subagent using the **Checker Prompt** from `PROMPTS.md`. Pass only `SPEC.md`, `git diff <checkpoint>`, and the verification output. Checker must return strictly `APPROVE` or `REJECT`.
   - **Step E (Commit & Advance)**: On approval, commit the task (`git commit -m "feat: <title> [refs: <task_id>]"`) and mark the task as `- [x]` in `PLAN.md`.
3. **Morning Handoff**: Generate `NIGHTLY_REPORT.md` at the project root and output a summary for the human developer.
