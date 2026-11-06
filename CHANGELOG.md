# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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
