# Goldfish Annotated Review Report

**Target:** `docs/designs/autonomous_pipeline_orchestrator.md` (`lines 1-244`)
**Status:** resolved (All 9 PROPOSE FIX items applied)

## Prioritized Findings & AI Suggestions

### 1. Missing Class Method Signatures, Types, and Agent Runner Subsystem [`egm-implementation-readiness`] - Severity: High
* **Affected Scope:** [docs/designs/autonomous_pipeline_orchestrator.md#L184-L191](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L184-L191) (Section `Detailed Implementation Plan` 1)
* **Discrepancy Description:** The implementation plan lists 5 components in prose without concrete method signatures, input/output types, or data structures. Crucially, the agent execution boundary (`AgentRunner` and `CheckerAuditor`) is omitted from the implementation scope.
* **Rationale:** Violates the Zero-Decision Rule. Implementers must not invent data models or agent invocation boundaries.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Define explicit Python `dataclass` models (`Task`, `TaskStatus`, `ExecutionResult`, `OrchestratorConfig`) and class interfaces with method signatures for all components, clarifying that `/nightly-run` coordinates the pipeline while helper modules handle deterministic operations.
* **Proposed Edit:**
  * Location: `Detailed Implementation Plan` 1
  * Change: Expand `scripts/nightly_orchestrator.py` with full type annotations, dataclasses, class interfaces, and method signatures.

---

### 2. Node.js CI Gate Mismatch and Missing Python Dependency Management [`egm-codebase-details`] - Severity: High
* **Affected Scope:** [docs/designs/autonomous_pipeline_orchestrator.md#L184-L199](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L184-L199) (Target Files & Verification Plan)
* **Discrepancy Description:** The repository CI (`.github/workflows/verify.yml`) is strictly configured for Node.js 20 / npm. The plan introduces Python (`pytest`, `mypy`, `ruff`) without adding a dependency manifest (`pyproject.toml`) or reconciling CI.
* **Rationale:** Violates `SPEC.md` line 27 and creates synchronization drift between local verification and GitHub Actions CI.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Add `pyproject.toml` to Target Files (pinning `pytest>=8.0.0`, `mypy>=1.9.0`, `ruff>=0.3.0`). Clarify in `verify_local.sh` that both Node.js checks and Python checks run locally, and document the companion PR required for human approval on `.github/workflows/verify.yml`.
* **Proposed Edit:**
  * Location: Target Files to Create/Modify
  * Change: Add item for `pyproject.toml` with pinned tool configurations; harmonize `verify_local.sh` and Verification Plan.

---

### 3. Missing Formal Schemas for `NIGHTLY_REPORT.md`, Failure Logs, and `PLAN.md` [`egm-implementation-readiness`] - Severity: High
* **Affected Scope:** [docs/designs/autonomous_pipeline_orchestrator.md#L187-L191](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L187-L191) (Section `Detailed Implementation Plan` 1, 4)
* **Discrepancy Description:** No exact Markdown templates, schemas, or regex patterns are defined for `NIGHTLY_REPORT.md`, failure log dumps (`logs/nightly/`), or `PLAN.md` status modifications.
* **Rationale:** Forces the implementer to make ad-hoc formatting decisions on core operational deliverables.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Provide the exact Markdown layout for `NIGHTLY_REPORT.md` (summary KPI table, quarantined task details, next steps), structural schema for failure logs, and regex patterns for `PLAN.md` parsing.
* **Proposed Edit:**
  * Location: Technical Plan Section 3 & Detailed Implementation Plan
  * Change: Add formal Markdown schemas and templates.

---

### 4. Unresolved Architectural Questions Blocking Implementation [`egm-implementation-readiness`] - Severity: High
* **Affected Scope:** [docs/designs/autonomous_pipeline_orchestrator.md#L32-L36](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L32-L36) (Open Questions)
* **Discrepancy Description:** Open Question 1 (Task DAG dependencies) and Open Question 2 (Checker pruning rights) remain unresolved in the design.
* **Rationale:** Implementer cannot proceed while core dependency and mutation rights are open.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Close Question 1 with Strict Fail-Fast (sequential top-to-bottom processing in `PLAN.md`; halt immediately on 2nd failure; YAGNI on custom DAG tags). Close Question 2 by strictly defining Checker as read-only and binary (`APPROVE` or `REJECT`).
* **Proposed Edit:**
  * Location: Section `Open Questions` & Section 2
  * Change: Replace Open Questions with settled decisions and move rejected options to Alternatives Considered.

---

### 5. Invariant Verification Bypass & Rollback State Pollution [`egm-zoom-out` & `egm-design`] - Severity: High
* **Affected Scope:** [docs/designs/autonomous_pipeline_orchestrator.md#L112-L117](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L112-L117), [L146-L148](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L146-L148)
* **Discrepancy Description:** `verify_local.sh` self-inspecting for changes allows tampered scripts to exit 0. `git reset --hard` leaves untracked files on rollback. Missing pre-flight dirty working tree check.
* **Rationale:** A tampered script can bypass CI; untracked orphan files pollute subsequent task executions and git diffs.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Externalize invariant pre-check (`git diff --name-only <checkpoint>` runs before executing verify script). On rollback, run `git reset --hard <checkpoint> && git clean -fd`. Add pre-flight dirty tree assertion (`git status --porcelain`).
* **Proposed Edit:**
  * Location: Section 2.5 `GitGuard` & Section 4 `Safety Invariants`
  * Change: Add pre-flight clean check, external diff scan for protected files, and `git clean -fd` to rollback.

---

### 6. Missing Verbatim Templates for `PROMPTS.md` and `PLAN.md` Modifications [`egm-implementation-readiness`] - Severity: Medium
* **Affected Scope:** [docs/designs/autonomous_pipeline_orchestrator.md#L206-L216](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L206-L216) (Target Files 4, 5)
* **Discrepancy Description:** States that `PROMPTS.md` and `PLAN.md` will be updated with prompt templates and failure formats, but omits the verbatim markdown.
* **Rationale:** Prompt phrasing directly dictates AI behavior and must not be left to implementer discretion.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Embed the exact Markdown prompts for Maker and Checker, and the exact status schema for `PLAN.md`, directly in the document.
* **Proposed Edit:**
  * Location: Target Files 4 & 5
  * Change: Insert the complete verbatim prompt text and `PLAN.md` specification.

---

### 7. Unspecified CLI Interface and Exit Codes for `nightly_orchestrator.py` [`egm-implementation-readiness`] - Severity: Medium
* **Affected Scope:** [docs/designs/autonomous_pipeline_orchestrator.md#L184-L191](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L184-L191)
* **Discrepancy Description:** CLI arguments (`--plan`, `--dry-run`, `--max-retries`, `--timeout`) and process exit codes are undefined.
* **Rationale:** Prevents deterministic integration and unit testing.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Specify `argparse` options, defaults, help text, and exit code semantics (0: success, 1: task failed/halted, 2: pre-flight check failed).
* **Proposed Edit:**
  * Location: Target Files 1
  * Change: Add CLI arguments and exit code specification table.

---

### 8. Structural & Diagram Inconsistencies [`egm-structural` & `egm-style`] - Severity: Medium
* **Affected Scope:** [docs/designs/autonomous_pipeline_orchestrator.md#L57-L94](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L57-L94), [L231-L233](file:///Users/hamatsumakoto/Documents/develop/auto_developer/docs/designs/autonomous_pipeline_orchestrator.md#L231-L233)
* **Discrepancy Description:** Architecture flowchart omits 1st failure retry branch; `PROMPTS.md` missing from diagram inputs; Verification Plan has ambiguous `(または flake8)`.
* **Rationale:** Visual and text models must match.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Update Mermaid flowchart to include retry branch and `PROMPTS.md`; standardize verification tool on `ruff`.
* **Proposed Edit:**
  * Location: Section 1 Mermaid diagram & Verification Plan
  * Change: Update diagram and lock verification tool to `ruff`.

---

### 9. Component Naming & Glossary Standardization [`egm-style` & `egm-codebase-details`] - Severity: Low
* **Affected Scope:** Lines 98–119, Lines 184–191, Line 11, Line 147
* **Discrepancy Description:** Inconsistent component naming (`PlanManager` vs `PlanParser`, `VerifyGate` vs `VerifyRunner`); undefined glossary terms ("EGM", "免疫テスト").
* **Rationale:** Cleanliness and readability.
* **AI Suggestion:** PROPOSE FIX
* **Justification:** Standardize canonical names: `TaskDispatcher`, `MakerRunner`, `DeterministicVerifier`, `CheckerAuditor`, `GitGuard`, `MorningReporter`. Define terms in Overview/Invariants.
* **Proposed Edit:**
  * Location: Across all sections
  * Change: Standardize module names and add brief definitions for EGM and immunity tests.
