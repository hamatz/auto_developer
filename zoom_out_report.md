# Goldfish Review Report: Zoom-Out Audit

**Target:** `docs/designs/autonomous_pipeline_orchestrator.md` (`lines 1-245`)
**Document Standard:** `resources/document_standards/zoom-out-standard.md`
**Reviewer:** `egm-zoom-out` (Skeptical Architect)

---

## Reviewer Status

| Reviewer | Status |
| --- | --- |
| `egm-zoom-out` | completed |

---

## Prioritized Findings (Blockers -> Major -> Nits)

### 1. Runtime Boundary Contradiction: External Python Script vs. Internal Antigravity Agent Primitives - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Section 1 & 2 (`Architecture & Module Responsibilities`, lines 53-56, 102-111, 170-176, 184-192)
* **Discrepancy Description**: The design presents an irreconcilable architectural contradiction regarding runtime boundaries. Lines 55 and 184 specify that the orchestrator is an external, standalone Python CLI script (`scripts/nightly_orchestrator.py`). Yet lines 103 and 109 state that this script invokes `Antigravity の invoke_subagent（またはCLIサブプロセス）を呼び出し` to spawn stateless Maker and Checker subagents in isolated context windows. An external Python process running in a local shell has no native access to Antigravity IDE agent primitives or context management without a dedicated, headless CLI or daemon SDK (which is explicitly dismissed in Non-Goals / Alternative 3 as "Antigravity ネイティブのサブエージェントおよびローカルGit/シェルスクリプトで同等の直列ステートマシンが十分軽量に実現可能なため", lines 175-176).
* **Rationale & Recommendation**: The architectural boundary must be clarified before writing implementation plans:
  * **Option A (Antigravity-Native Agent Runner - Recommended):** The orchestrator is an Antigravity agent invoked via `/nightly-run` that manages the execution loop, invokes subagents natively, and uses shell tools for git/verify checks. In this case, building a monolithic 5-module Python orchestrator (`scripts/nightly_orchestrator.py`) is redundant over-engineering.
  * **Option B (Headless External CLI):** The Python script is the driver, but it must invoke a well-defined headless CLI interface or direct LLM API, and references to Antigravity harness primitives like `invoke_subagent` must be replaced with concrete IPC/CLI contracts.

---

### 2. State Pollution on Rollback: `git reset --hard` Fails to Clear Untracked Files and Scratch Artifacts - Severity: Blocker
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Section 2 (`Git & Circuit Breaker`, lines 112-117) & CUJ 2 (lines 44-48)
* **Discrepancy Description**: The rollback mechanism on circuit breaker trip relies exclusively on `git reset --hard <checkpoint>`. In Git, `git reset --hard` only resets tracked files; it leaves all newly created untracked files, scratch scripts, intermediate directories, and compiled artifacts untouched in the working tree. Furthermore, the orchestrator writes failure dumps to `logs/nightly/` and modifies `PLAN.md` within the same working copy. If an "independent" Task 2 proceeds immediately after rolling back Task 1, any stray files created by Maker during Task 1 will pollute Task 2's workspace, typechecker, and git diff.
* **Rationale & Recommendation**:
  * Unconstrained in-place resets in a dirty working copy lead to insidious cascade failures across tasks.
  * Recommendation: At minimum, pair `git reset --hard` with `git clean -fd` to remove untracked files.
  * Architectural Recommendation: Decouple tasks completely by either (a) halting immediately upon the first quarantined task (Fail-Fast), or (b) executing each task inside an isolated scratch branch or ephemeral `git worktree`.

---

### 3. Violation of Core Development Charter: Speculative "Skip & Continue" vs. Strict Fail-Fast - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Overview & Goals (lines 17, 22), Module 1 & 5 (lines 100, 116), CUJ 2 (lines 44-48)
* **Discrepancy Description**: When a task fails twice, the design attempts to quarantine the failure (`[!]`), skip dependent tasks (`[-]`), and continue executing subsequent "independent" tasks overnight. This directly contradicts `AGENTS.md` Rule 5:
  > *"同一の失敗が2回続いたら、修正を止めて状況を PLAN.md に記録する"* (Stop modifying and record status in PLAN.md).
* **Rationale & Recommendation**: In specification-driven engineering, tasks in `PLAN.md` almost never have true orthogonal independence; later tasks assume earlier foundational models, interfaces, and configurations succeeded. Allowing an unattended night agent to guess independence and continue making commits against a partially broken codebase introduces enormous semantic risk for negligible gain.
  * Recommendation: Enforce strict **Fail-Fast** (100% aligned with `AGENTS.md`). When any task fails twice, the orchestrator immediately halts the entire pipeline, generates `NIGHTLY_REPORT.md` with the failure log and clean checkpoint, and yields control to the human developer. This cuts state machine complexity by more than half and eliminates cascade risk entirely.

---

### 4. Premature Generalization (YAGNI): Complex DAG Dependency Resolution in `PLAN.md` - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Open Question 1 (line 34), Technical Plan (lines 98-101, 187)
* **Discrepancy Description**: The proposal contemplates building DAG dependency parsing into `PlanParser`, such as evaluating custom comment tags (`<!-- depends_on: [task-1] -->`) or multi-level section inheritance.
* **Rationale & Recommendation**: Non-Goal line 28 explicitly bans parallel workers and mandates strictly serial execution. `PLAN.md` is already structured linearly by the architect/Elephant in chronological execution order. Building a custom dependency graph resolver into markdown parsing introduces unnecessary fragility, syntax bloat, and maintenance overhead for a single-worker serial loop.
  * Recommendation: Close Open Question 1 decisively with YAGNI: `PLAN.md` tasks are processed strictly sequentially from top to bottom. If any task fails under the Fail-Fast model, execution stops. Reject custom DAG tag parsing.

---

### 5. Duplication of Verification Source of Truth: `verify_local.sh` vs. `.github/workflows/verify.yml` - Severity: Major
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Module 3 (`Deterministic Verifier`, lines 105-107), Safety Invariants (lines 147-148), Implementation (lines 193-199)
* **Discrepancy Description**: The plan creates a new shell script (`scripts/verify_local.sh`) that manually duplicates the verification steps of `.github/workflows/verify.yml`, and then adds custom guardrails to prevent Maker from tampering with `verify_local.sh`.
* **Rationale & Recommendation**: Having two separate definitions of "valid verification" inevitably leads to synchronization drift between local gate checks and CI.
  * Recommendation: Define a single, canonical local entrypoint (e.g., standard npm script `npm run verify` or `make verify` or `act`) that is invoked identically by both `.github/workflows/verify.yml` and the orchestrator, ensuring a single authoritative definition of system invariants.

---

### 6. Ambiguity in Checker Mutation Rights - Severity: Nit
* **Reviewer Lens:** `egm-zoom-out`
* **Affected Scope:** Open Question 2 (lines 35-36)
* **Discrepancy Description**: Open Question 2 questions whether Checker should be permitted to directly remove dead code / perform light refactoring before commit, or remain a binary gatekeeper.
* **Rationale & Recommendation**: In an unmonitored overnight batch run, granting the Checker mutating powers introduces a secondary implementation loop (Checker becomes Maker), requiring secondary verification and risk of divergence.
  * Recommendation: In automated overnight mode, Checker must be strictly read-only and binary (`APPROVE` or `REJECT`). If code changes are required, it must reject back to Maker or halt.

---

## Severity Breakdown
* 🔴 **Blocker**: 2
* 🟡 **Major**: 3
* 🟢 **Nit**: 1
* **Overall Status:** `completed`
