# Comprehension Review Report

**Target:** `docs/designs/autonomous_pipeline_orchestrator.md` (`lines 1-244`)
**Reviewer:** `egm-comprehension` (Naive Reader Persona)

---

### What this proposes
This document proposes an autonomous serial pipeline orchestrator (`scripts/nightly_orchestrator.py` and a `/nightly-run` skill) designed to safely execute development tasks overnight without human supervision. Driven by task lists in `PLAN.md` and specifications in `SPEC.md`, it processes each task through an isolated cycle of implementation by a Maker subagent, local deterministic validation via `scripts/verify_local.sh`, and review by a stateless Checker subagent. If validation or review fails twice consecutively, a circuit breaker reverts the Git working tree to the task's starting checkpoint, logs the failure in `logs/nightly/`, marks the task as failed in `PLAN.md`, and advances to remaining independent tasks, ultimately generating a morning summary in `NIGHTLY_REPORT.md`.

### How the existing system works, per this document
The document describes the current development workflow as governed by guidelines in `AGENTS.md`, design specifications in `SPEC.md`, task checklists in `PLAN.md`, past traps in `LESSONS.md`, and CI validation rules defined in `.github/workflows/verify.yml`. When AI agents attempt to execute multiple tasks autonomously in long sessions or parallel runs, context bloat causes prompt drift, hallucinated requirements, runaway retry loops, test tampering, and cascading workspace breakage. There is currently no automated, fault-isolated local state machine to deterministically enforce step-by-step verification, rollback on repeated failures, and safe handoff for unassisted overnight execution.

---

### Audit Findings

#### 1. Major: Ambiguous Agent Invocation Boundary and Missing Orchestrator Modules
* **Affected Scope**: Section 2 (lines 102–111) and Section `Detailed Implementation Plan` 1 (lines 184–192)
* **Discrepancy Description & Rationale**:
  * In Section 2, the orchestrator architecture defines `AgentRunner` (Stateless Maker Dispatcher) and `CheckerAuditor` (Stateless Checker) as responsible for calling "Antigravity's `invoke_subagent` (or CLI subprocess)".
  * However, in the Detailed Implementation Plan (lines 184–192), `scripts/nightly_orchestrator.py` only implements `PlanParser`, `GitManager`, `VerifyRunner`, `CircuitBreaker`, and `MorningReporter`. `AgentRunner` and `CheckerAuditor` are entirely missing from the implementation scope.
  * A standalone Python CLI script running locally cannot natively invoke Antigravity's in-session subagents (`invoke_subagent`) without an external SDK, daemon, or specific CLI bridge, which conflicts with Non-Goal 30 ("外部サーバーや分散DBを要求せず、Antigravity環境とローカルGit/CLIのみで完結する"). The document leaves it ambiguous whether the loop is driven by the Antigravity agent calling the Python script as a helper, or if Python is the master controller invoking external agent CLIs.

#### 2. Major: Undefined Project-Specific Terminology ("EGM" and "免疫テスト")
* **Affected Scope**: Overview (line 11), Line 147, and Line 244
* **Discrepancy Description & Rationale**:
  * **"EGM"** (lines 11, 244): Referred to as "EGM設計書" and "Drafted with EGM" without expanding the acronym (e.g., Elephant-Goldfish Model) or providing context/links for a new project member.
  * **"免疫テスト" (Immunity Tests)** (line 147): Line 147 specifies an invariant that modifying "免疫テストファイル" immediately triggers quarantine rollback. The document does not define what an immunity test is, what directory or naming convention identifies these test files, or how `verify_local.sh` detects changes to them versus standard unit/acceptance tests.

#### 3. Major: Unresolved Specification for Task Dependency and Independence
* **Affected Scope**: Open Questions (line 34), Section 2 (line 100), CUJ 2 (lines 45–47), and Implementation Plan (line 187)
* **Discrepancy Description & Rationale**:
  * Question 1 (line 34) leaves the task dependency model as an open question (`<!-- depends_on -->` tags vs. section order).
  * However, Section 2 (line 100) and CUJ 2 (line 47) assume dependency resolution is already operational ("前提タスクが失敗している場合はスキップマークを付与して後続へ", "Task 1に依存しないTask 2へ進み"). Line 187 then specifies "セクション単位の依存関係解決".
  * It is unclear how the orchestrator distinguishes whether a subsequent task is independent or dependent when an earlier task fails (e.g., does independence require placing tasks in separate Markdown sections, or will a specific tag syntax be parsed?).

#### 4. Nit: Input Model Inconsistency Regarding `PROMPTS.md`
* **Affected Scope**: Section 1 (Mermaid Diagram, lines 66–70) vs. Detailed Implementation Plan 5 (lines 212–215)
* **Discrepancy Description & Rationale**:
  * The system architecture diagram explicitly lists `SPEC.md`, `PLAN.md`, and `LESSONS.md` under `Project Inputs`, but omits `PROMPTS.md`.
  * In the Detailed Implementation Plan (line 212), `PROMPTS.md` is introduced as a target file to modify with stateless prompt templates for Maker and Checker. It should be reflected in the architecture diagram and inputs description.

#### 5. Nit: Ambiguity on Context State During 1st Retry
* **Affected Scope**: Section 2 (lines 104, 115) and Section 3 (lines 136–137)
* **Discrepancy Description & Rationale**:
  * Line 104 states that Maker runs with past thinking logs severed ("過去タスクの思考ログは遮断する"). Line 115 states that on a first failure, error logs are fed back to allow Maker one retry.
  * It is not stated whether this 1st retry continues the active subagent's conversation turn with the error output, or spawns a fresh subagent with the error log prepended to its prompt.
