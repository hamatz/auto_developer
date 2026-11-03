# Implementation Readiness Review Report

**Target:** `docs/designs/autonomous_pipeline_orchestrator.md` (`lines 180-244`)
**Document Standard:** `resources/document_standards/implementation-readiness-standard.md`
**Review Mode:** REVIEW (Zero-Decision Rule Audit)

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-implementation-readiness` | completed |
| `egm-codebase-details` | completed |
| `egm-style` | completed |

---

## Severity Summary

- 🔴 **Blocker**: 4
- 🟡 **Major**: 3
- 🟢 **Nit**: 2

---

## Prioritized Findings (Blockers -> Major -> Nits)

### 1. Missing Class Method Signatures, Types, and Agent Runner Subsystem - Severity: Blocker
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** Lines 184-191 (`#### 1. scripts/nightly_orchestrator.py`)
* **Discrepancy Description**: The detailed implementation plan lists 5 components (`PlanParser`, `GitManager`, `VerifyRunner`, `CircuitBreaker`, `MorningReporter`) in prose bullet points, but provides zero concrete method signatures, input/output types, or data structures. Crucially, it completely omits the agent execution boundary (`AgentRunner` / `CheckerAuditor` described in Technical Plan lines 102-111). An implementer (Maker) has no technical specification on how Antigravity subagents are invoked, how prompts are injected, how timeout/turn limits are monitored, or how structured `APPROVE`/`REJECT` outputs are extracted.
* **Rationale & Recommendation**: Under the Zero-Decision Rule, the Maker must not invent core architectural interfaces. Provide explicit Python `dataclass` definitions (e.g. `Task`, `TaskStatus`, `ExecutionResult`) and concrete class signatures with type hints for all components, including the agent invocation adapter.

### 2. Node.js CI Gate Mismatch and Missing Python Dependency Management - Severity: Blocker
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** Lines 184, 193-199, 231-240 (`scripts/verify_local.sh`, Verification Plan)
* **Discrepancy Description**: The repository's authoritative verification gate (`.github/workflows/verify.yml`) is strictly configured for Node.js 20 / npm (`npm ci`, `npx tsc --noEmit`, `npm run lint`, `npm test`). The plan introduces a Python stack (`scripts/nightly_orchestrator.py`, `pytest`, `mypy`, `ruff`), but does NOT include a Python dependency manifest (`pyproject.toml` or `requirements.txt`) in Target Files, violates the rule in `SPEC.md` line 27 ("新規パッケージ追加時は理由を PLAN.md に記録すること"), and leaves `verify.yml` and `verify_local.sh` out of sync. Furthermore, `.github/` is protected in `AGENTS.md`, meaning the Maker cannot update CI without human code owner authorization.
* **Rationale & Recommendation**: Add `pyproject.toml` (pinning dependencies: `pytest`, `mypy`, `ruff`) to Target Files. Explicitly specify the CI update plan or harmonize `verify_local.sh` with `.github/workflows/verify.yml` so that local checks and CI gate remain 100% equivalent.

### 3. Missing Formal Schemas for `NIGHTLY_REPORT.md`, Failure Logs, and `PLAN.md` - Severity: Blocker
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** Lines 187, 191, 206-211 (`PLAN.md`, `NIGHTLY_REPORT.md`, `logs/nightly/`)
* **Discrepancy Description**: The plan requires `MorningReporter` to generate `NIGHTLY_REPORT.md`, `CircuitBreaker` to dump `logs/nightly/task_<id>_failure.log`, and `PlanParser` to recognize `[x]`, `[!]`, `[-]`. However, no exact Markdown templates, schemas, or regex patterns are defined. Implementers are forced to make ad-hoc formatting decisions regarding report sections, failure log fields, and task status parsing regex.
* **Rationale & Recommendation**: Define the exact Markdown template for `NIGHTLY_REPORT.md` (summary tables, quarantined task section, triage instructions) and the structural schema for failure log files. Specify the exact regex / grammar for parsing and modifying `PLAN.md`.

### 4. Unresolved Architectural Questions Blocking Implementation - Severity: Blocker
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** Lines 32-36 (Open Questions), Lines 187, 212-216
* **Discrepancy Description**: Open Question 1 (Task DAG dependencies via metadata comments vs section-serial order) and Open Question 2 (Checker automated pruning authority vs strict binary approve/reject) were left open in the design. Line 187 assumes "セクション単位の依存関係解決", but fails to specify how cross-section dependencies or failures propagate. Line 215 plans prompt additions for Checker without resolving whether Checker directly commits pruning changes.
* **Rationale & Recommendation**: Close both open questions in the design before handoff: specify the exact dependency syntax and rule for `PLAN.md`, and explicitly constrain Checker to binary `APPROVE`/`REJECT` verdict reporting.

### 5. Missing Verbatim Templates for `PROMPTS.md` and `PLAN.md` Modifications - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** Lines 206-216 (`#### 4. PLAN.md`, `#### 5. PROMPTS.md`)
* **Discrepancy Description**: The plan states that `PROMPTS.md` will be modified to add "無記憶テンプレート" (stateless templates) and `PLAN.md` will add explanations and failure recording format. Leaving the prompt engineering of Maker and Checker to the implementer violates Zero-Decision, as agent reliability hinges directly on prompt wording.
* **Rationale & Recommendation**: Provide the verbatim Markdown text for the Maker and Checker prompt templates directly in the design document so the implementer merely inserts them.

### 6. Unspecified CLI Interface and Exit Codes for `nightly_orchestrator.py` - Severity: Major
* **Reviewer Lens:** `egm-implementation-readiness`
* **Affected Scope:** Lines 184-191, 236-238
* **Discrepancy Description**: The verification plan mentions `python3 scripts/nightly_orchestrator.py --dry-run`, but the Target Files section defines no CLI arguments (e.g. `--plan`, `--repo-dir`, `--max-retries`, `--timeout`), environment variable overrides, or process exit codes (e.g., exit code when some tasks are quarantined vs unrecoverable orchestrator failure).
* **Rationale & Recommendation**: Document the complete CLI interface specification (command arguments, flags, default values, and exit code semantics).

### 7. Non-Deterministic Tooling Specification in Verification Plan - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** Lines 231-233 (`Verification Plan`)
* **Discrepancy Description**: Line 232 specifies `ruff check scripts/ tests/ (または flake8)`. Allowing optional or ambiguous tools in an automated verification plan prevents deterministic gate enforcement and breaks Zero-Decision execution.
* **Rationale & Recommendation**: Remove `(または flake8)` and deterministically standardize on `ruff check scripts/ tests/` and `ruff format --check scripts/ tests/`.

### 8. Inconsistent Component Naming Across Sections - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** Lines 98-119 vs Lines 184-191
* **Discrepancy Description**: Technical Plan section 2 names modules `PlanManager`, `VerifyGate`, `GitGuard`, while Target Files section 1 names them `PlanParser`, `VerifyRunner`, `GitManager`.
* **Rationale & Recommendation**: Standardize on a single set of component names across the architecture diagram, module responsibility breakdown, and target files list.

### 9. Ambiguity in Antigravity Skill Placement - Severity: Nit
* **Reviewer Lens:** `egm-codebase-details`
* **Affected Scope:** Lines 200-205 (`skills/nightly-run/SKILL.md`)
* **Discrepancy Description**: The repository currently lacks a top-level `skills/` directory. The plan should clarify whether project-specific Antigravity skills are loaded from `skills/` or `.gemini/skills/` or if additional Antigravity configuration is required.
* **Rationale & Recommendation**: Document the discovery conventions and setup for repo-level skills in the target project.
