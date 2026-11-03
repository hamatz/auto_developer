# Intermediate Zoom-Out Architectural Synthesis Report

**Target:** `docs/designs/autonomous_pipeline_orchestrator.md` (`lines 1-244`)  
**Document Standard:** `resources/document_standards/zoom-out-standard.md`  
**Reviewer:** `egm-zoom-out` (Skeptical Architect)  
**Evaluated Context:** `zoom_out_report.md`, `comprehension_report.md`, `critic_report.md`  
**Deliverable:** `intermediate_zoom_out_report.md`

---

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-zoom-out` | completed |

---

## Executive Summary & Cross-Cutting Architectural Synthesis

Across the **Comprehension** and **Critic** review stages, the proposed design was analyzed for structural integrity, clarity, and system security. When viewed through the holistic lens of the **Skeptical Architect (Zoom-Out)**, the individual findings coalesce into three critical architectural tension points that must be reconciled before implementation:

1. **The Execution Boundary Dilemma (Agent-Native vs. Headless Python CLI)**:
   The document tries to be two contradictory architectures at once: an external zero-dependency Python script (`scripts/nightly_orchestrator.py`) and an Antigravity agent using native harness primitives (`invoke_subagent`). Furthermore, the Detailed Implementation Plan (lines 184–192) completely omits `AgentRunner` and `CheckerAuditor` from the Python script's scope.
   *Resolution*: The orchestrator should be an **Antigravity-Native Agent** invoked via `/nightly-run`. It orchestrates stateless subagents using native IDE primitives, delegating purely deterministic tasks (Git operations, pre-check diff scanning, and test running) to lightweight helper scripts.

2. **Charter Violation & State Mutation in "Skip & Continue" vs. Fail-Fast**:
   Attempting speculative execution overnight (quarantining Task 1, attempting to parse markdown DAG dependencies, guessing that Task 2 is independent, and skipping dependent tasks) violates `AGENTS.md` Rule 5 (*"同一の失敗が2回続いたら、修正を止めて状況を PLAN.md に記録する"*). Additionally, `git reset --hard` fails to clean untracked files and scratch artifacts created by a failed Maker run, poisoning subsequent tasks.
   *Resolution*: Enforce **Strict Fail-Fast**. On 2 consecutive failures, perform `git reset --hard <checkpoint> && git clean -fd`, dump the diagnostic error log to `logs/nightly/`, mark the failure in `PLAN.md`, generate `NIGHTLY_REPORT.md`, and halt immediately. This eliminates complex DAG parsing, prevents workspace contamination, and halves state machine complexity.

3. **Externalized Guardrails & Canonical Verification**:
   Having `verify_local.sh` check whether it has been tampered with creates a self-bypassing verification vulnerability.
   *Resolution*: Invariants must be enforced externally by an immutable process (Git diff scan against protected paths `.github/**`, `SPEC.md`, `verify_local.sh`, and immunity tests) *before* verification code is executed. Furthermore, local verification should directly invoke the canonical CI commands to prevent configuration drift.

---

## Prioritized Findings (Blockers -> Major -> Nits)

### 1. Runtime Boundary Contradiction: External Standalone Python Script vs. Native Antigravity Subagents - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with `egm-design` Critic #2 and `egm-comprehension` Major #1)
* **Affected Scope:** Lines 55–56, 102–111 (Section 1 & 2) and Lines 184–192 (Section `Detailed Implementation Plan` 1)
* **Discrepancy Description**: Lines 55 and 184 specify that the orchestrator is a zero-dependency standalone Python CLI script (`scripts/nightly_orchestrator.py`). Yet lines 103 and 109 state that this script invokes Antigravity's `invoke_subagent` to spawn stateless Maker and Checker subagents. An external Python process running in a standard shell has no native access to Antigravity IDE agent primitives or subagent contexts. Compounding this, the Detailed Implementation Plan (lines 184–192) omits `AgentRunner` and `CheckerAuditor` entirely from `scripts/nightly_orchestrator.py`.
* **Rationale & Recommendation**: Choose and enforce a coherent architectural runtime model:
  * **Recommended Model (Antigravity-Native Runner)**: The `/nightly-run` skill defines an Antigravity orchestrator persona/agent that loops over `PLAN.md`, spawns Maker/Checker subagents natively via Antigravity primitives, and invokes local CLI helpers (`git`, verification scripts).
  * If an external Python script is retained as the driver, specify the exact headless CLI/IPC bridge (e.g. `antigravity-cli agent run`) and include `AgentRunner` and `CheckerAuditor` in the implementation plan.

---

### 2. Invariant Verification Bypass Vulnerability in `verify_local.sh` - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with `egm-design` Critic #1 and `egm-comprehension` Major #2)
* **Affected Scope:** Lines 105–107, 146–148 (Section 2.3 `Deterministic Verifier` & Section 4 `Safety Invariants`)
* **Discrepancy Description**: Section 4 specifies that `verify_local.sh` detects modifications to protected assets (`.github/`, `verify_local.sh`, `SPEC.md`, immunity tests). However, if the Maker subagent modifies `scripts/verify_local.sh` (e.g., removing the check or hardcoding `exit 0`), the Deterministic Verifier executes the tampered script. An untrusted agent can thus disable its own verification gate from within.
* **Rationale & Recommendation**: Guardrail enforcement must be immutable and external to the script being verified. The orchestrator must run `git diff --name-only <checkpoint>` *prior* to executing any verification script. If any protected paths are touched, the orchestrator must instantly trigger an immediate Quarantine rollback without running Maker's modified code.

---

### 3. State Pollution & Cascade Failure on Rollback: `git reset --hard` Leaves Untracked Files - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with Zoom-Out #2 and `egm-design` Critic #9)
* **Affected Scope:** Lines 40, 44–48 (CUJ 1 & 2), Lines 112–117 (Section 2.5 `Git & Circuit Breaker`), Line 188
* **Discrepancy Description**: The rollback mechanism on circuit breaker trip relies exclusively on `git reset --hard <checkpoint>`. In Git, `git reset --hard` only resets tracked files; it leaves all newly created untracked source files, scratch test scripts, intermediate build directories, and generated artifacts in the working tree. If subsequent tasks execute after Task 1 rolls back, Task 1's orphan files remain on disk, causing type-checking errors, syntax errors, and dirty git diffs. Additionally, no pre-flight check exists to ensure the developer's tree was clean before running.
* **Rationale & Recommendation**:
  * On rollback, execute `git reset --hard <checkpoint> && git clean -fd` to restore a clean tree.
  * In `GitGuard`, add a pre-flight assertion: verify `git status --porcelain` is empty before starting the nightly run; refuse to start if uncommitted daytime changes exist.

---

### 4. Violation of Core Development Charter: Speculative "Skip & Continue" vs. Strict Fail-Fast - Severity: Major
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with Zoom-Out #3, Critic #4, and Comprehension #3)
* **Affected Scope:** Lines 17, 22 (Overview & Goals), Line 34 (Open Question 1), Lines 44–48 (CUJ 2), Lines 98–101 (Section 2.1)
* **Discrepancy Description**: When a task fails twice, the design attempts to quarantine the failure (`[!]`), resolve DAG dependencies in `PLAN.md`, skip dependent tasks (`[-]`), and continue executing subsequent "independent" tasks overnight. This violates `AGENTS.md` Rule 5:
  > *"同一の失敗が2回続いたら、修正を止めて状況を PLAN.md に記録する"* (Stop modifying and record status in PLAN.md).
  Software development tasks in `PLAN.md` are rarely orthogonal. Speculatively guessing task independence overnight introduces severe cascade risk and requires complex DAG comment parsing (`<!-- depends_on -->`) that contradicts Non-Goal line 28 (strictly serial execution).
* **Rationale & Recommendation**: Close Open Question 1 decisively with YAGNI and adopt **Strict Fail-Fast**. When any task fails twice, the orchestrator halts immediately, generates `NIGHTLY_REPORT.md` with the error trace, and stops. This aligns with `AGENTS.md`, eliminates DAG parser complexity, and ensures zero broken state transitions overnight.

---

### 5. Duplication of Verification Source of Truth: `verify_local.sh` vs. `.github/workflows/verify.yml` - Severity: Major
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with Zoom-Out #5 and `AGENTS.md` Part 1)
* **Affected Scope:** Lines 105–107 (Section 2.3), Lines 147–148, Lines 193–199
* **Discrepancy Description**: The plan creates a standalone shell script (`scripts/verify_local.sh`) that manually duplicates the verification steps of `.github/workflows/verify.yml`. Having two independent definitions of the verification gate inevitably leads to synchronization drift between local gate checks and remote CI.
* **Rationale & Recommendation**: Establish a single canonical entrypoint (e.g. `npm run verify` or `make verify` or `pytest && ruff`) that is invoked identically by both `.github/workflows/verify.yml` and the local orchestrator gate, ensuring a single authoritative definition of system invariants.

---

### 6. Missing Data Schema and Exit Taxonomy for `NIGHTLY_REPORT.md` - Severity: Major
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with `egm-structural` Critic #5 and Comprehension Goals)
* **Affected Scope:** Lines 24–25 (Goal 4), Lines 91–93, Lines 118–120 (Section 2.6), Lines 191–192
* **Discrepancy Description**: Goal 4 and Section 2.6 position `NIGHTLY_REPORT.md` as the core handoff artifact for the human developer upon waking. However, the design provides no Markdown template, data schema, or exit status taxonomy (e.g., table of completed commits, failure stack trace links, affected files, required human decisions).
* **Rationale & Recommendation**: Add a dedicated specification subsection to Section 2 or 3 defining the exact schema and Markdown layout of `NIGHTLY_REPORT.md`.

---

### 7. Structural Discrepancies in Architecture Diagrams and Life Cycle Models - Severity: Major
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with `egm-design` Critic #3, `egm-style` Critic #6, and `egm-comprehension` Nit #4 & #5)
* **Affected Scope:** Lines 57–94 (Mermaid Flowchart) vs Lines 98–120 (Section 2 Modules) vs Lines 124–143 (State Diagram)
* **Discrepancy Description**:
  * **Omitted 1st Failure Retry**: The Section 1 flowchart routes `Verifier -->|"Exit Code != 0"| CircuitBreaker --> StateGuard` unconditionally, omitting the 1st failure feedback loop to Maker shown in the state diagram.
  * **Missing Input Artifact**: `PROMPTS.md` is introduced in Section Detailed Plan (line 212) but missing from `Project Inputs` in the architecture diagram.
  * **Divergent Module Naming**: Modules are named inconsistently between the flowchart (`Dispatcher`, `StateGuard`), Section 2 (`PlanManager`, `GitGuard`), and Section Detailed Plan (`PlanParser`, `GitManager`).
  * **Retry Context Semantics**: Unclear whether the 1st retry continues Maker's existing subagent turn with error feedback or spawns a fresh subagent with error logs prepended.
* **Rationale & Recommendation**:
  * Update the Mermaid flowchart to explicitly represent the 1st failure retry branch.
  * Add `PROMPTS.md` to `Project Inputs`.
  * Standardize canonical module names across all diagrams and sections (`TaskDispatcher`, `MakerRunner`, `DeterministicVerifier`, `CheckerAuditor`, `GitGuard`, `MorningReporter`).
  * Clarify that the 1st retry continues the active subagent turn with the compiler/test error output.

---

### 8. Undefined Glossary Terms ("EGM", "免疫テスト") and Non-standard GFM Task Markers - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with `egm-comprehension` Major #2 and `egm-style` Critic #7)
* **Affected Scope:** Line 11, Line 100, Line 116–117, Line 147, Line 244
* **Discrepancy Description**:
  * "EGM" (Elephant-Goldfish Model) and "免疫テスト" (Immunity Tests) are used without definition or path conventions (e.g. `tests/immunity/**`).
  * `[!]` and `[-]` are non-standard GFM task list markers that render as unformatted plain text in standard Markdown renderers.
* **Rationale & Recommendation**:
  * Add a brief glossary or clarify terms in Section Overview / Invariants.
  * Use GFM-compliant conventions (e.g. `- [x]` / `- [ ]` with status labels like `- [ ] Task name **[FAILED]**`).

---

### 9. List Hierarchy and Formatting Inconsistency in Section 2.5 - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out` (cross-cutting with `egm-style` Critic #8)
* **Affected Scope:** Lines 112–117 (Section 2.5)
* **Discrepancy Description**: Section 2.5 abruptly shifts to nested bold-prefix lists (`1回目失敗:`, `2回目失敗:`, `成功時処理:`) which diverges from the concise bullet points in sections 2.1–2.4.
* **Rationale & Recommendation**: Standardize list indentation, bullet levels, and colon punctuation across all subsections of Section 2.

---

## Severity Breakdown

| Severity | Count | Findings |
| --- | :---: | --- |
| 🔴 **Blocker** | 3 | Runtime Boundary Contradiction (#1), Verifier Bypass Vulnerability (#2), State Pollution on Rollback (#3) |
| 🟡 **Major** | 4 | Violation of Charter / Fail-Fast vs Skip (#4), Duplication of CI Source of Truth (#5), Missing Report Schema (#6), Diagram & Life Cycle Discrepancies (#7) |
| 🟢 **Nit** | 2 | Undefined Glossary & GFM Markers (#8), List Formatting Consistency (#9) |
| **Total** | **9** | |
