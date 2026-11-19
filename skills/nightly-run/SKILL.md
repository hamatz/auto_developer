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
   - **Step D (Stateless Checker Review)**: Spawn a fresh stateless subagent using the **Checker Prompt** from `PROMPTS.md`. Pass only `SPEC.md`, `git diff <checkpoint>`, and the verification output (write them to files and pass the paths; do not summarize). The Checker **must run on a different model family from the Maker** (e.g. Gemini Maker → Claude Checker; never `inherit`, never an ambiguous name such as `pro`). The Checker's final line must be exactly `DECISION: APPROVE` or `DECISION: REJECT`.
     Immediately after the review, and **before** any commit, record the outcome (both APPROVE and REJECT) for metrics:
     `auto-dev record-review --task-id <task_id> --decision <APPROVE|REJECT> --maker-model <maker_model> --checker-model <checker_model> --reason "<reason>"`
     On REJECT, feed the reasons back to the Maker (counts toward the 2-attempt limit in Step C) instead of committing.
   - **Step E (Evidence-gated Commit & Advance)**: On approval only, commit via
     `auto-dev commit-step --task-id <task_id> --checkpoint <checkpoint> --maker-model <maker_model> --checker-model <checker_model> --checker-decision APPROVE --checker-reason "<reason>"`.
     Use the real model identifiers actually used for this step. `commit-step` refuses with **exit 4** (`CHECKER_POLICY_VIOLATION`) if the decision is not APPROVE or if the two models are identical, of the same family, or of an unrecognized family; the Evidence Token is not consumed, so fix the cause (e.g. re-run the review on a different-family Checker) rather than retrying with altered names. Exit 3 means missing or stale evidence. On success the orchestrator consumes the Evidence Token, commits, and marks the task `- [x]` in `PLAN.md`.
3. **Morning Handoff & Evidence Bundle**:
   - Generate or refresh `NIGHTLY_REPORT.md` (Morning Brief) with `auto-dev report`, and include the phase metrics from `auto-dev metrics` (first-pass rate, rework ratio, Checker rejects, audit breakdown).
   - Automatically package the entire run into a sanitized, audit-ready Evidence Bundle via `auto-dev bundle` (saved to `.dev/bundles/bundle_<job_id>.zip`).
   - Output an actionable summary in chat containing:
     - Executive summary and task progress
     - Verified Tree SHA and independent Checker decisions
     - Direct file link to the generated Evidence Bundle ZIP
     - Guidance on third-party verification via `/nightly-verify` or `auto-dev verify-bundle <path>`
     - Quick run/verify commands for the human developer
