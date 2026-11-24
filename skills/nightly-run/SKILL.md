---
name: nightly-run
description: Antigravity Autonomous Pipeline Orchestrator. Executes PLAN.md tasks sequentially (or multi-team worktrees when teams.json exists) overnight with [Implement -> Verify -> Review -> Commit] cycle and strict Fail-Fast rollback.
---

# Nightly Run: Autonomous Pipeline Orchestration

This skill orchestrates overnight autonomous development using the Elephant-Goldfish Model (EGM) standards defined in `SPEC.md` and `docs/designs/autonomous_pipeline_orchestrator.md`. It supports both single-workspace sequential execution and multi-team worktree execution (`teams.json`).

## Mode Selection

Before starting, check whether `teams.json` exists at the workspace root:
- **If `teams.json` does NOT exist**: Follow **Single-Workspace Sequential Execution** below (or invoke `auto-dev run`).
- **If `teams.json` exists**: Follow **Multi-Team Execution Path** below (`auto-dev teams prepare --json` → per-worktree CORE-7 loop → `auto-dev teams integrate --job-id <id>`).
- **External CLI Maker/Checker Option**: If you are launching external CLI commands for Maker and Checker (`--maker-cmd`, `--checker-cmd`, `--maker-model`, `--checker-model`, or `--mock` for testing), you may use `auto-dev teams run` directly instead of running `prepare`, the per-worktree loop, and `integrate` manually:
  ```bash
  auto-dev teams run --maker-cmd "<cmd>" --checker-cmd "<cmd>" --maker-model <maker_model> --checker-model <checker_model>
  ```
  (`teams run` handles `prepare`, concurrent workers with `--worker-timeout` [default 21600s], and `integrate`; if any team fails, it stops without integrating.)
- **Live Dashboard Option (Optional)**: Before starting, you may offer the user a live auto-updating HTML dashboard:
  - Run `auto-dev dashboard --enable` (or pass `--dashboard` to `auto-dev run` / `auto-dev teams run` for that run only).
  - Tell the user the path to open in their browser (default `.dev/dashboard.html`). The page auto-reloads and displays the latest `LIVE` state on every event. To disable persistent auto-updates later, run `auto-dev dashboard --disable`.

---

## Single-Workspace Sequential Execution

When running `/nightly-run` without `teams.json` (or invoking `auto-dev run` / `python3 scripts/nightly_orchestrator.py`), follow the strict EGM pipeline:
1. **Pre-flight Assertion**: Run `auto-dev pre-flight` to verify that the Git working tree is completely clean (`git status --porcelain` must be empty) and fetch `checkpoint` and `next_task`. Refuse to run if uncommitted changes exist.
2. **Sequential Task Loop**: Read `PLAN.md` and process each pending task (`- [ ]`) strictly in order:
   - **Step A (Maker Implementation)**: Spawn a fresh stateless subagent (or execute in an isolated turn) using the **Maker Prompt** from `PROMPTS.md`. Pass only the target task, relevant `SPEC.md` requirements, and `LESSONS.md`.
   - **Step B (External Invariant Check)**: Verify protected files (including untracked files via `git status --porcelain`). If `.github/**`, `SPEC.md`, `scripts/verify_local.sh`, `tests/immunity/**`, or `teams.json` were created or modified, trigger immediate Quarantine rollback.
   - **Step C (Deterministic Verification & Evidence)**: Run `auto-dev verify-step --checkpoint <checkpoint> --task-id <task_id>` (or `python3 scripts/nightly_orchestrator.py verify-step ...`). This checks invariants, executes `scripts/verify_local.sh`, and issues a cryptographic Evidence Token on success. If it fails on the 1st attempt, feed the stderr back to Maker for 1 retry. If it fails a 2nd time, run `auto-dev rollback-step --checkpoint <checkpoint> --task-id <task_id> --reason "<reason>"` to safely quarantine work into a `quarantine/task-<id>-*` branch and halt immediately (Strict Fail-Fast).
   - **Step D (Stateless Checker Review)**:
     1. Generate the canonical diff via `auto-dev review-input --task-id <task_id> --out-dir <review_dir>` (writes `<review_dir>/diff.patch`).
     2. Extract the relevant `SPEC.md` section **verbatim** into `<review_dir>/spec_excerpt.md` (do not summarize or paraphrase).
     3. Spawn a fresh stateless Checker using the **Checker Prompt** from `PROMPTS.md` on a **different model family** from the Maker (e.g. Gemini Maker → Claude Checker; never `inherit`, never an ambiguous name such as `pro`), passing only `<review_dir>/spec_excerpt.md`, `<review_dir>/diff.patch`, and the verification output file path. Wait for the Checker process to finish completely, then read `<review_dir>/checker_output.txt` and verify a single decision (`APPROVE` or `REJECT`) is resolved under the GATE-5 parsing rule (either the entire output is a single JSON object with `"decision"`, or it contains a `DECISION: APPROVE` / `DECISION: REJECT` line — never proceed on a completion notification alone).
     4. Immediately after the review, and **before** any commit, record the outcome (both `APPROVE` and `REJECT`) by passing all three files:
        `auto-dev record-review --task-id <task_id> --checker-output <review_dir>/checker_output.txt --diff-file <review_dir>/diff.patch --spec-file <review_dir>/spec_excerpt.md --maker-model <maker_model> --checker-model <checker_model> --reason "<reason>"`
        On `REJECT`, feed the reasons back to the Maker (counts toward the 2-attempt limit in Step C) instead of committing.
   - **Step E (Evidence-gated Commit & Advance)**: On approval only, commit via
     `auto-dev commit-step --task-id <task_id> --checkpoint <checkpoint> --maker-model <maker_model> --checker-model <checker_model> --checker-reason "<reason>"`.
     Use the real model identifiers actually used for this step. If a model identifier is custom or not recognized by built-in family detection (`gemini`, `claude`, `openai`, `mistral`, `llama`, `qwen`, `deepseek`), explicitly pass `--maker-family <family>` and/or `--checker-family <family>` (non-empty, not `unknown`, and must not conflict with built-in detection; also supported on `record-review`, `run`, and `teams run`). `commit-step` refuses with **exit 4** (`CHECKER_POLICY_VIOLATION`) if the decision is not APPROVE or if the two models are identical, of the same family, of an unrecognized family, or have conflicting explicit families; the Evidence Token is not consumed, so fix the cause (e.g. re-run the review on a different-family Checker) rather than retrying with altered names. Exit 3 means missing or stale evidence. On success the orchestrator consumes the Evidence Token, commits, and marks the task `- [x]` in `PLAN.md`.
3. **Morning Handoff & Evidence Bundle**:
   - Generate or refresh `NIGHTLY_REPORT.md` (Morning Brief) with `auto-dev report`, and include the phase metrics from `auto-dev metrics` (first-pass rate, rework ratio, Checker rejects, audit breakdown).
   - Automatically package the entire run into a sanitized, audit-ready Evidence Bundle via `auto-dev bundle` (saved to `.dev/bundles/bundle_<job_id>.zip`).
   - Output an actionable summary in chat containing:
     - Executive summary and task progress
     - Verified Tree SHA and independent Checker decisions
     - Direct file link to the generated Evidence Bundle ZIP
     - Guidance on third-party verification via `/nightly-verify` or `auto-dev verify-bundle <path>`
     - Quick run/verify commands for the human developer

---

## Multi-Team Execution Path (when `teams.json` exists)

When `teams.json` exists and the agent orchestrates teams directly via subagents, execute the following three phases:

1. **Prepare Team Worktrees**:
   From the main workspace root, run:
   ```bash
   auto-dev teams prepare --json
   ```
   - Validates `teams.json` (`TEAMS_CONFIG_ERROR` / exit 2 if invalid, e.g. non-empty `depends_on` or overlapping `owns`) and asserts a clean Git working tree.
   - Outputs JSON with `job_id` and `teams[]` (each containing `team_id`, `worktree_path`, `branch`, `plan`).
   - If a previous job's worktrees or `team/*` branches remain, `teams prepare` refuses rather than recreating them. Use `auto-dev teams status`, `auto-dev teams recover`, or `auto-dev teams cleanup --job-id <id>` (with `--force` only to discard an unintegrated/failed job) before retrying.

2. **Per-Worktree Sequential Task Loop**:
   For each team's `worktree_path` (either `cd <worktree_path>` or pass `--work-dir <worktree_path>` to every `auto-dev` command):
   - **Pre-flight**: Run `auto-dev pre-flight` inside the worktree. From branch `team/<team_id>` and committed `teams.json`, the CLI automatically resolves `team_id`, `plan`, and `owns`, and returns `checkpoint` and `next_task`. Repeat until `next_task` is `null`.
   - **Maker Implementation**: Spawn a fresh stateless Maker subagent using the **Maker Prompt** from `PROMPTS.md`, passing only the target task from the team's `plan`, relevant `SPEC.md` requirements, `LESSONS.md`, and the team's `owns` boundaries. Do not modify protected files (`.github/**`, `SPEC.md`, `scripts/verify_local.sh`, `tests/immunity/**`, `teams.json`) or files outside `owns`.
   - **Verify & Boundary Check**: Run `auto-dev verify-step --checkpoint <checkpoint> --task-id <task_id>`. It verifies protected files (INV-1), enforces `owns` boundaries (INV-9, exit 11 on violation), runs `scripts/verify_local.sh`, and issues an Evidence Token. On 1st failure, feed the error output back to Maker for 1 retry. On 2nd failure, run `auto-dev rollback-step --checkpoint <checkpoint> --task-id <task_id> --reason "<reason>"` to quarantine into `quarantine/<team_id>-<id>-*` and halt that team immediately (Strict Fail-Fast, INV-4).
   - **Stateless Checker Review & Record**:
     1. Generate the canonical diff via `auto-dev review-input --task-id <task_id> --out-dir <review_dir>` (writes `<review_dir>/diff.patch`).
     2. Extract the relevant `SPEC.md` section **verbatim** into `<review_dir>/spec_excerpt.md`.
     3. Spawn a fresh Checker subagent on a **different model family** from the Maker (INV-6), passing only `<review_dir>/spec_excerpt.md`, `<review_dir>/diff.patch`, and the verification output file path. Wait for the Checker process to finish completely, then read `<review_dir>/checker_output.txt` and verify a single decision (`APPROVE` or `REJECT`) is resolved under the GATE-5 parsing rule (never proceed on a completion notification alone).
     4. Record both `APPROVE` and `REJECT` before committing by passing all three files:
        `auto-dev record-review --task-id <task_id> --checker-output <review_dir>/checker_output.txt --diff-file <review_dir>/diff.patch --spec-file <review_dir>/spec_excerpt.md --maker-model <maker_model> --checker-model <checker_model> --reason "<reason>"`
        On `REJECT`, feed the reasons back to Maker (counts toward the 2-attempt limit).
   - **Evidence-Gated Commit**: On `APPROVE`, commit via:
     `auto-dev commit-step --task-id <task_id> --checkpoint <checkpoint> --maker-model <maker_model> --checker-model <checker_model> --checker-reason "<reason>"`
     (Add `--maker-family <family>` / `--checker-family <family>` when using custom or unrecognized model identifiers.)
   - If any team fails or is quarantined, **stop without calling `teams integrate`**. Leave worktrees intact and report `auto-dev teams status --job-id <job_id>` and `auto-dev report` / `auto-dev brief`.

3. **Integration Gate & Morning Handoff**:
   Once every team has completed all tasks cleanly (`next_task` is `null`), run from the main workspace root:
   ```bash
   auto-dev teams integrate --job-id <job_id>
   ```
   - Verifies that all team plans are marked `[x]` and worktrees are clean, preserves/verifies per-team Evidence Bundles, merges each `team/<team_id>` branch in an integration worktree, checks protected files, re-runs `scripts/verify_local.sh`, and creates an RC tag (`refs/tags/rc/<job_id>`) without updating `main`.
   - If `teams integrate` fails (exit 1), worktrees are preserved; report the failing team and the recovery steps printed by the command.
   - Refresh and inspect the Morning Brief with `auto-dev report` / `auto-dev brief` and `auto-dev metrics`.
   - Present to the developer:
     - Per-team task progress, RC tag (`rc/<job_id>`), and preserved bundle paths (`.dev/integration_jobs/<job_id>/evidence/<team_id>.zip`),
     - The human merge command (`git merge rc/<job_id>`), and
     - Cleanup command after review (`auto-dev teams cleanup --job-id <job_id>`).
