# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.7.0] - 2026-11-14

### Major Milestone: Stage 4〜Stage 7 Completion — OS-Level Sandboxing, Multi-LLM Audit Boundary, and Multi-Team Resilience (Phases 19 - 53)

Auto Developer v0.7.0 delivers the full realization of Stages 4, 5, 6, and 7, establishing an industrial-grade multi-agent autonomous engineering platform under strict adversarial and fail-closed constraints.

### Added
- **OS-Level Sandboxing for Red-First Test Execution (`RedTestVerifier`)**:
  - **macOS `sandbox-exec` Default-Deny**: Enforced `(deny file-read*)` default policy combined with explicit read allowlists (`/usr/lib`, `/System`, system Python, isolated worktree) to prevent host secrets (`~/.ssh`, `~/.aws`, `.env`, keychains) from being accessed by candidate tests.
  - **Linux `bwrap` (Bubblewrap) Hardening**: Isolated mount namespace with complete network unsharing (`--unshare-net`), root filesystem isolation, and strict virtual environment boundary checks using `is_relative_to` to restrict host filesystem visibility.
  - **Clean Working Tree & Invariant Snapshot Guards**: Pre- and post-execution snapshot comparison ensuring untracked, ignored, and tracked files in the host repository remain 100% byte-identical.
- **Trivial & Fabricated Red Test Elimination (`TRIVIAL_RED`)**:
  - **Static AST Constant Inspection**: Detects unconditional assertion failures (e.g., `self.assertEqual(1, 2)`) before candidate test adoption.
  - **Dynamic Execution Tracing (`sys.settrace`)**: Verifies that candidate tests actually traverse production source code in stack frames during failure, rejecting detached or synthetic assertions.
- **Independent Multi-LLM Cross-Family Audit Boundary (`AuditDispatcher` & `AuditStore`)**:
  - **Cross-Family Model Policy Enforcement**: Structurally forbids Maker and Auditor LLMs from sharing the same model family (e.g. Gemini Maker vs Claude Opus / GPT 6.1 Auditor), eliminating cognitive bias and sycophantic approval.
  - **Tamper-Evident Audit Invariant Store**: Structured `AuditFinding` persistence recording all audit findings, human handoff notes, and `REJECTED` invalid findings in the Evidence Store and Bundle.
- **Semantic Conflict Resolution & Multi-Stage Promotion (Stages 4 & 5)**:
  - **Integration Gate (`IntegrationGate`)**: Isolated merge and verification in independent integration worktrees (`int-<job_id>`) prior to main repository promotion.
  - **Atomic Release Candidate (`refs/tags/rc/<job_id>`)**: Deterministic RC tag issuance backed by verified evidence records, with automatic cleanup on validation failure.
- **Process Lifecycle Supervision & Recovery Resilience (Stage 6)**:
  - **PGID Process Tree Supervision**: Reliable group termination preventing orphan or detached sub-processes upon parent orchestrator exit.
  - **Zombie Process Disambiguation**: Deterministic start-time verification (`lstart`) preventing accidental signaling of recycled PIDs.
- **Test Suite Expansion**:
  - Expanded test coverage from 125 tests to **321 tests across 26 test suites**, maintaining 100% pass rate in `scripts/verify_local.sh`.

---

## [0.3.0] - 2026-11-07

### Major Milestone: Stage 1 Multi-Team Foundation & Zero-Loss Evidence Verification (Phases 11 - 18)

Auto Developer v0.3.0 marks the completion of **Stage 1 (Multi-Team Autonomous Development Foundation)**. This release establishes physical Git Worktree isolation for parallel development teams, strict file boundary enforcement, fail-closed unsaved work preservation (Zero-Loss Guarantee), and a tamper-proof third-party verifiable Evidence Bundle architecture.

### Added
- **Multi-Team Worktree Isolation & Lifecycle Management (`WorktreeManager`)**:
  - Isolated Git Worktree provisioning and dedicated feature branches (`feature/{team_id}`) per team.
  - Team ownership boundaries enforcement via `BoundaryGuard`, mechanically preventing out-of-boundary code changes before commit.
  - Non-destructive unmerged branch protection on worktree removal (`purge_unmerged=False`).
- **Zero-Loss Guarantee & Comprehensive Quarantine (`remove_worktree`)**:
  - **Git-Status-Independent Real-File Scanning**: Pre-scans physical worktree files prior to Git status execution to detect uncommitted and ignored assets.
  - **Fail-Closed Secret Preservation (P0)**: Refuses removal of worktrees containing sensitive files (`.env`, private keys) or secret contents, preventing accidental loss or leakage.
  - **Ignored Work File Quarantine (P0)**: Automatically archives non-sensitive Git-ignored files (`local-notes.txt`, etc.) into compressed quarantine snapshots (`tar.gz` + `manifest.json`) with readback SHA-256 verification before removal.
  - **Tracked Clean File Exemption**: Differentiates tracked clean files (e.g. `.env.example`) from uncommitted secrets, preventing false-positive secret blocking on clean worktree removal.
- **Tamper-Proof Evidence Bundle & Third-Party Verifier (`auto-dev bundle` / `auto-dev verify-bundle`)**:
  - Standalone audit package bundling sanitized Git Bundle, Evidence Records, JobState, and execution briefs into an immutable ZIP archive.
  - Isolated clone restoration and deterministic physical verification against real Git commits and Tree objects.
  - Strict Fail-Closed manifest checks rejecting synthetic/zero Tree SHAs, unmanifested ZIP contents, missing task fields (`commit`, `tree_sha`, `spec_sha`, `status`, `checker_decision`), and mismatched evidence tokens.
  - Morning Brief (`NIGHTLY_REPORT.md`) freshness inspection ensuring report HEAD aligns with bundle manifest commit.
- **Deterministic Acceptance & Security Test Suites**:
  - Expanded test coverage from 67 tests to **125 tests (+58 tests)** covering multi-team orchestration, worktree isolation, boundary violations, tamper detection, and fail-closed data preservation.
  - Hardened test payloads against Gitleaks scanning using dynamically combined credential tokens.

### Changed
- Standardized Evidence Record generation to strictly bind physical commit SHA, Tree SHA, and SPEC SHA in a 1-to-1 relationship.
- Refactored `remove_worktree` to eliminate redundant directory traversals, combining pre-scanning, secret detection, and quarantine bundling into a unified safe pipeline.

---

## [0.2.0] - 2026-11-06

### Major Milestone: Trustworthy Autonomous Development & Crash-Resilient Transactional Pipeline (Pilots 01 - 04)

Auto Developer v0.2.0 establishes an industrial-grade foundation for autonomous AI coding. Moving beyond self-reported AI success, it enforces objective verification gates, fail-fast quarantine preservation, and full transactional crash recovery.

### Added
- **Transactional Crash Recovery Engine (`auto-dev recover`)**:
  - `inspect`: Diagnoses inconsistent state after abrupt termination (SIGKILL) by cross-referencing Git HEAD, working tree, Evidence Store, and JobState.
  - `plan`: Generates safe, non-destructive recovery actions prior to execution.
  - `apply`: Safely restores clean checkpoint, archives in-flight WIP work, reconciles commits with state, and guarantees full idempotency (`UNCHANGED` on repeated executions).
  - Validated against 4 distinct real-world crash points (In-flight Maker, Post-verification, Post-commit, Mid-quarantine).
- **Concurrency & Process Locking**:
  - Process-level mutual exclusion using `fcntl.flock(LOCK_EX | LOCK_NB)` across entire recovery execution, eliminating TOCTOU race conditions.
- **Fail-Fast Quarantine & Snapshot Preservation**:
  - Preserves failed tasks in dedicated Git quarantine branches (`quarantine/{task_id}-{timestamp}`) and compressed archives (`snapshot_task_*.tar.gz`).
  - Recursive file traversal indexing all subdirectory artifacts with SHA-256 digests in companion Manifest files (`manifest_task_*.json`).
  - Strict 3-way validation requiring `job_id`, `task_id`, and `checkpoint` match to prevent adoption of stale or cross-job archives.
- **Security & Secret Leakage Prevention**:
  - Enforced `git add -u` on quarantine staging to prevent accidental tracking of untracked secret files (`.env`, private keys).
  - Added `sanitize_staged_changes()` for deep content inspection against API keys and credential patterns, unstaging sensitive changes before quarantine commits.
- **Evidence Binding & Audit Trail**:
  - Cryptographic token generation binding Git Tree SHA and Spec SHA to prevent unverified commits.
  - Full audit records capturing attempt histories, verification output summaries, and independent Checker review metadata.
- **Actionable Morning Brief**:
  - Decision-support executive summary, failure isolation details, quarantine branch references, and copy-pastable manual recovery commands.
- **Packaging & Antigravity Ecosystem**:
  - Standalone CLI `auto-dev` with `run`, `init`, and `recover` subcommands.
  - Antigravity plugin integration (`plugin.json` and bundled skills).

### Changed
- Refactored `JobState` to persist `base_branch` and fine-grained phases (`INIT`, `MAKER`, `VERIFIER`, `CHECKER`, `COMMIT`, `QUARANTINE`).
- Stabilized local verification suite `scripts/verify_local.sh` with 67 comprehensive unit, regression, and acceptance tests.

---

## [0.1.0] - 2026-11-03

### Initial Release: Core Autonomous Pipeline (Pilot 01)
- Autonomous sequential task execution driven by `PLAN.md`.
- Deterministic verification gates enforcing `scripts/verify_local.sh`.
- Protection contracts ensuring immutable status for `.github/`, `SPEC.md`, and acceptance tests.
- Initial Maker and Checker decoupled pipeline orchestration.
