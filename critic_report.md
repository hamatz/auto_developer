# Goldfish Review Report: Critic Review

**Target:** `docs/designs/autonomous_pipeline_orchestrator.md` (`lines 1-179`)
**Document Standard:** `resources/document_standards/feature-spec-standard.md`
**Mode:** REVIEW

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-design` | completed |
| `egm-structural` | completed |
| `egm-style` | completed |

## Severity Breakdown
- 🔴 **Blocker**: 2
- 🟡 **Major**: 4
- 🟢 **Nit**: 3
- **Total Findings**: 9

---

## Prioritized Findings (Blockers -> Major -> Nits)

### 1. Invariant Verification Bypass Vulnerability in `verify_local.sh` - Severity: Blocker
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** Lines 105–107, 146–148 (Section 2.3 `Deterministic Verifier` & Section 4 `Safety Invariants`)
* **Discrepancy Description**: Section 4 specifies that `verify_local.sh` detects modifications to protected assets (`.github/`, `verify_local.sh`, `SPEC.md`). However, if the Maker subagent alters `scripts/verify_local.sh` (e.g. neutering the checks or forcing `exit 0`), the Deterministic Verifier will execute the tampered script. An untrusted agent change can thus disable its own verification gate from within.
* **Rationale & Recommendation**: The orchestrator engine host (`scripts/nightly_orchestrator.py` or runtime orchestrator) must inspect `git diff --name-only <checkpoint>` for protected files *before* invoking `verify_local.sh`. Guardrail checks must be enforced externally by an immutable process, not delegated to the script being verified.

---

### 2. Ambiguous IPC & Execution Boundary Between Standalone Python CLI and Antigravity Subagent Runtime - Severity: Blocker
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** Lines 55–56, 102–104 (Section 1 `システム全体構成` & Section 2.2 `Stateless Maker Dispatcher`)
* **Discrepancy Description**: Line 55 defines the orchestrator as an external, zero-dependency standalone Python script (`scripts/nightly_orchestrator.py`), whereas Line 103 states that `AgentRunner` calls Antigravity's `invoke_subagent` to spawn stateless Maker/Checker agents. A standalone Python script running outside Antigravity has no native access to Antigravity IDE subagent APIs without an IPC socket, RPC bridge, or Antigravity CLI daemon.
* **Rationale & Recommendation**: Explicitly delineate the execution boundary. Specify whether (A) an Antigravity agent runs `/nightly-run` as a skill coordinating subagents and calling Python/shell helper scripts, or (B) `scripts/nightly_orchestrator.py` connects to Antigravity via a defined CLI command (e.g. `antigravity-cli agent run --context ...`).

---

### 3. Architecture Flowchart Omission of the 1st Failure Retry Loop - Severity: Major
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** Lines 57–94 (Section 1 Mermaid Architecture Diagram)
* **Discrepancy Description**: The Section 1 flowchart routes `Verifier -->|"Exit Code != 0"| CircuitBreaker -->|"git reset --hard"| StateGuard` unconditionally. It entirely omits the retry loop on 1st failure back to `MakerAgent`. In contrast, Section 2.5 (Lines 114–115) and Section 3 State Diagram (Lines 136–137) define a 2-strike policy where a first failure feeds back logs to Maker for one retry attempt.
* **Rationale & Recommendation**: Update the Mermaid flowchart in Section 1 to show the conditional branch: `Exit Code != 0` / `Checker Rejected` -> `Attempt < 2 ? -> Feedback log to MakerAgent : Attempt >= 2 -> Circuit Breaker (reset --hard)`.

---

### 4. Circular Dependency Between CUJ 2 and Open Question 1 - Severity: Major
* **Reviewer Lens:** `egm-structural`
* **Affected Scope:** Line 34 (Section `Open Questions`) vs Lines 44–48 (`CUJ 2`) & Lines 98–101 (`PlanManager`)
* **Discrepancy Description**: CUJ 2 relies on skipping dependent tasks while continuing with independent subsequent tasks ("Task 1に依存しないTask 2へ進み、Task 2は正常に実装..."). However, Open Question 1 explicitly states that the format and semantics for expressing dependencies in `PLAN.md` (DAG comment metadata vs linear section sequence) remain unresolved. CUJ 2 cannot be verified or implemented until this dependency grammar is settled.
* **Rationale & Recommendation**: Resolve Open Question 1 within the technical plan. Adopt a concrete convention (e.g. metadata tag `<!-- depends_on: [task_id] -->` or structured markdown nesting) as the baseline specification, and move the alternatives to `Alternatives Considered`.

---

### 5. Missing Data Model and Schema Specification for `NIGHTLY_REPORT.md` - Severity: Major
* **Reviewer Lens:** `egm-structural`
* **Affected Scope:** Lines 24–25 (Goal 4), Lines 91–93, Lines 118–120 (Section 2.6 `Morning Reporter`)
* **Discrepancy Description**: Goal 4 lists the Morning Report as a key deliverable for human developer hand-off, yet Section 2.6 provides no markdown template, field schema, or exit status taxonomy (e.g., summary table of succeeded/quarantined/skipped tasks, links to error logs, list of manual interventions needed).
* **Rationale & Recommendation**: Add a dedicated subsection to Section 2 or 3 defining the exact structure and template of `NIGHTLY_REPORT.md` so that implementation and verification tests have an unambiguous target contract.

---

### 6. Architectural Component Naming Inconsistencies Across Sections - Severity: Major
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** Lines 58–64 (Flowchart nodes) vs Lines 98–120 (Section 2 Module list)
* **Discrepancy Description**: The document uses conflicting names for the core modules:
  - Flowchart: `Dispatcher`, `StateGuard`, `CircuitBreaker`, `Reporter`, `MakerAgent`, `Verifier`, `CheckerAgent`.
  - Section 2: `PlanManager`, `AgentRunner`, `VerifyGate`, `CheckerAuditor`, `GitGuard` (merged with Circuit Breaker), `Reporter`.
  - In Section 1, `StateGuard` and `CircuitBreaker` are separate nodes, whereas in Section 2.5 they are merged under `Git & Circuit Breaker (GitGuard)`.
* **Rationale & Recommendation**: Standardize on canonical module names across all diagrams and descriptions (e.g., `TaskDispatcher`, `MakerRunner`, `DeterministicVerifier`, `CheckerAuditor`, `GitGuard`, `MorningReporter`).

---

### 7. Non-standard GFM Task List State Markers - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** Lines 100, 116–117 (Section 2.1 & Section 2.5)
* **Discrepancy Description**: The specification uses `[!]` (quarantined/failure) and `[-]` (skipped) as task checkboxes in `PLAN.md`. Standard GitHub Flavored Markdown (GFM) only renders `- [ ]` and `- [x]` as checkbox inputs; custom characters inside brackets render as plain text or break list parsing.
* **Rationale & Recommendation**: Clarify that these are custom state tokens parsed by `PlanParser`, or adopt GFM-compliant conventions (e.g. `- [ ] ~~Task title~~ [QUARANTINED]` or `- [x]` with status tags).

---

### 8. List Punctuation and Hierarchy Inconsistency in Section 2.5 - Severity: Nit
* **Reviewer Lens:** `egm-style`
* **Affected Scope:** Lines 112–117 (Section 2.5 `Git & Circuit Breaker`)
* **Discrepancy Description**: Section 2.5 abruptly switches into nested sub-bullet formatting with mixed colon styles (`1回目失敗:`, `2回目失敗:`, `成功時処理:`) that contrast with the concise bullet style used in sections 2.1–2.4.
* **Rationale & Recommendation**: Align the nested list styling and punctuation of Section 2.5 with sections 2.1–2.4 for visual and editorial consistency.

---

### 9. Undefined Pre-condition Handling for Dirty Working Trees - Severity: Nit
* **Reviewer Lens:** `egm-design`
* **Affected Scope:** Line 40 (CUJ 1 Given condition), Line 113 (Section 2.5)
* **Discrepancy Description**: CUJ 1 assumes a clean Git tree at startup, but Section 2.5 does not specify what happens if the user launches `/nightly-run` with unstaged or uncommitted daytime work. A subsequent `git reset --hard` during a task quarantine would destroy the developer's uncommitted work.
* **Rationale & Recommendation**: Mandate a pre-flight dirty-tree check in `GitGuard`: if `git status --porcelain` is non-empty, refuse to start and prompt the user to commit or stash uncommitted changes.
