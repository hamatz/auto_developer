# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

---

## [0.10.0] - 2026-11-27

### Highlights

v0.10.0 aims to make Auto Developer usable by people who do not write good specifications themselves, and to let everyone see at a glance what happened overnight. It adds a **Spec Compiler** that turns an EGM design document into reviewed drafts of `SPEC.md`, `PLAN.md`, `teams.json` and a contract, and a **replayable dashboard** that shows the night as a timeline: tickets leave the backlog, teams work, reviews pass or fail, and verified commits pile up. It also closes several gaps found while dogfooding: unrecorded reviews, files lost during quarantine, and fail-fast that depended on the agent following instructions.

### Breaking changes

- **Top-level option abbreviations are no longer accepted** (DX-15). `auto-dev --work ...` must be written `auto-dev --work-dir ...`. Abbreviations of subcommand options are unchanged. This was needed so that `compile --check` does not collide with the top-level `--checker-*` options.
- **One decided review per verification** (GATE-13). `record-review` records at most one review with a decision (APPROVE or REJECT) for the same `verify-step`; a second one is refused with `CHECKER_POLICY_VIOLATION` (exit 4). After a REJECT, run `verify-step` again before reviewing again. Outputs without a decision are kept in the review history and allow one more review.
- **Reviews must follow the verification they belong to** (GATE-13). `review-input` and `record-review` refuse with exit code 3 when the working tree no longer matches the latest verification.
- **Fail-fast is enforced in the low-level path** (GATE-15). `verify-step` and `commit-step` refuse to continue a task that has already failed twice (verification failures and Checker REJECTs together) since it was last committed or quarantined, and point to `rollback-step`.
- **Approved IR files are protected** (GATE-14). `docs/designs/*.ir.json` is treated like `SPEC.md`: an agent that creates, changes or deletes it is stopped. The `verify_local.sh` template created by `auto-dev init` includes the new pattern; update existing copies of the script by hand if you want the same check there.
- **Status snapshot schema** is now `auto-dev-status/2` (adds `timeline[]`, `backlog`, `requirements[]` and `design_irs[]`).

### Added

- **Spec Compiler** (`auto-dev compile`, skill `/nightly-compile`):
  - `--outline <design.md>` maps the headings of an EGM design document (Goals, CUJs, Architectural Decisions, Verification Plan, ...) to the parts of an intermediate representation (IR), with line ranges and SHA-256, ignoring headings inside code blocks.
  - `--check <ir.json>` validates the IR (`auto-dev-ir/1`) and its source anchors, and runs deterministic lints: missing acceptance criteria, contradictions with decisions (`forbids`), overlaps with already implemented requirements, team ownership, unlinked requirements and steps, and open questions.
  - `--emit <ir.json>` writes drafts under `.dev/compile/<name>/`: a SPEC section with source comments, PLAN steps with an "existing test changes" skeleton, `teams.json` and per-team plans, a contract with "decisions for the human", acceptance test outlines, and an impact analysis. It never touches `SPEC.md`, `PLAN.md` or `teams.json` (INV-12).
  - `--impact <ir.json>` lists where each requirement's fingerprints appear: definitions, references and strings in Python (by AST), other text files, tests that mock or count calls, tests that synthesize records, and entry points.
  - `--anchor <path>:<start>-<end>` prints a source anchor for the IR.
  - `/nightly-compile` guides an agent from a design document (or a set of `goal.md` / `architecture.md` / `decision.md` / `todo.md`) to an IR, through the checks and an independent review by a different model family, to the drafts.
- **Status and dashboard**:
  - `auto-dev status [--json]`: a snapshot built only from Auto Developer's own records (INV-11): plan and tasks, events, quarantines, team jobs, audit, requirements, and items that need a human decision.
  - `auto-dev dashboard`: a single offline HTML file that replays the night (backlog, teams with Maker and Checker, integration gate, commit tower with line counts, evidence log, timeline with gap compression, 1×/4×/60× playback). Requirement IDs from plan titles and approved IRs are shown on tickets; `DESIGN_DRIFT` appears when an approved IR no longer matches its design document.
  - `auto-dev dashboard --enable` (or `run` / `teams run --dashboard`) rewrites the dashboard on every event; `--watch` regenerates it periodically. `/nightly-run` offers to enable it.
  - `auto-dev demo --teams`: a two-team demo (one verification failure and retry, integration, RC tag) that ends with a dashboard.
  - `scripts/record_dashboard.py` exports a dashboard replay as an animated GIF (development tool; needs Playwright and Pillow).
- **Events**: `run` / `teams run` now write `verify_step`, `checker_review`, `commit_step` and `rollback_step` events like the low-level commands, plus `task_start`, integration events and `review_input`.
- **Review history**: every `record-review` keeps a copy under `reviews/task_<id>/history/<n>/`, including REJECTs and outputs without a decision.
- **Verification timeout**: `verify-step --timeout SEC`, or a `# auto-dev: verify-timeout=<SEC>` line in the first 20 lines of the verification script (default 300 seconds). Timeouts explain where the limit came from and how to raise it.
- **Quarantine guidance**: after a quarantine, Auto Developer lists new files that are only in the snapshot (with a `tar xzf` command), and files kept as Git objects because they look like secrets (with a `git cat-file -p` command).
- `scripts/check_doc_commands.py` checks the `auto-dev` commands written in the README and the skills against the CLI definition.

### Fixed

- **Quarantine could lose files** (RCV-7): new untracked files whose contents looked like secrets (for example a test with a fake private key) were excluded from both the quarantine branch and the snapshot, and then deleted. They are now written as Git objects before cleaning; if that fails, nothing is deleted.
- `SECRET_FILE_IN_COMMIT` now reads file names with `-z` and treats type changes as changes (AUD-1004, AUD-1005 from the v0.9.0 audit).
- `status` read the audit record from the wrong place and did not use the same `AUDIT_OUTDATED` rule as `verify-bundle`.
- `examples/two-teams/.gitignore` lacked `NIGHTLY_REPORT.md`, which made integration fail after `teams run`.
- Python 3.10 compatibility in a test, and a timing-dependent tamper test that failed when the sandbox probe was not cached.

### Known issues

- Multi-team execution is **experimental**. All teams use the same Maker, Checker and verification; team-specific skills, models and review criteria are planned for v0.11 (`docs/contracts/v0.11-draft.md`).
- `compile --impact` lists candidates only; it does not decide whether a test must change. Files under `docs/` named `test_*.py` are counted as tests (IDEA-032).
- A few timing-dependent tests can fail on heavily loaded machines (IDEA-011).

---

## [0.9.0] - 2026-11-23

### Highlights

v0.9.0 is the first release aimed at people other than the authors. It makes multi-team execution usable from the CLI and the agent skills, makes the Checker's review machine-verifiable, and rewrites the specification and the README so that a new user can get started with `pip install` and `auto-dev demo`. It is a deliberate break in history: the SPEC, LESSONS and README were rewritten, and evidence bundles from earlier versions are not accepted.

### Breaking changes

- **Evidence bundles**: `bundle_manifest.json` now has `format_version: 3`, and each task in a bundle carries copies of its review inputs and output. `verify-bundle` rejects bundles with `format_version` lower than 3, with a message pointing to the version that can verify them (v0.8.0 for older bundles).
- **Review recording**: `auto-dev record-review` now requires `--checker-output`, `--diff-file` and `--spec-file`. The decision is read from the Checker's output file, not from `--decision`. The old form with only `--decision` is rejected with exit code 2.
- **Commit gate**: `auto-dev commit-step` refuses to commit unless a review is recorded for the latest verification, the recorded decision is APPROVE, the Checker's output file is unchanged since it was recorded, the models match, and the current diff matches the reviewed diff (exit code 3 or 4).
- **Model names are required with external commands**: `run` / `teams run` with `--maker-cmd` / `--checker-cmd` but without model names is rejected with exit code 2 (`--mock` is unchanged).
- **Secret files are refused at verification and commit**: `verify-step` (exit 1) and `commit-step` (exit 4) refuse added or modified `.env` / `.env.*` (except `.env.example`, `.env.sample`, `.env.template`), key files (`*.key`, `*.pem`, `*.pkcs12`, `*.pfx`) and SSH key files, with `SECRET_FILE_IN_COMMIT:`. Add them to `.gitignore`.
- **Plan parsing**: tasks are read only from the `## ステップ` section when it exists, and lines inside HTML comments are ignored.
- **Specification and IDs**: `SPEC.md` was rewritten with area-based IDs (`CORE-`, `GATE-`, `TEAM-`, ...). The previous specification and its feature IDs are frozen in `docs/history/`.
- **Tag format**: tags now use `v0.9.0` (previously `v.0.8.0`).

### Added

- **Multi-team execution from the CLI and skills**: `teams init`, `teams prepare`, `teams status`, `teams run`, `teams integrate`, `teams cleanup` and `teams recover`. Each team works in its own Git worktree and branch (`team/<id>`). Integration verifies each team's evidence, merges the teams one by one, verifies again, and creates an `integration/<job_id>` branch and an `rc/<job_id>` tag; main is never changed. Work that touches files outside a team's `owns` is quarantined. Teams with no commits are reported as `NO_CHANGES`. `/nightly-setup` and `/nightly-run` support the team path.
- **Machine-verifiable review**: `auto-dev review-input` writes a canonical diff, and `record-review` binds the decision to the Checker's output, the diff and a verbatim excerpt of `SPEC.md` by SHA-256. The `run` / `teams run` / `demo` paths record the same evidence. Rejected reviews in the `run` path are kept under `reviews/rejected/<job_id>/`.
- **`auto-dev demo`**: a self-contained demonstration in a temporary repository (two successful tasks, a quarantined change to a protected file, `brief`, and a verified bundle). `--keep` keeps the directory.
- **Model families**: `--maker-family`, `--checker-family` and `--auditor-family` declare a model's family explicitly; a declaration that contradicts the built-in detection is refused. Built-in detection now covers `mistral`, `llama`, `qwen` and `deepseek`.
- **Failure logs** start with `Failure Phase:` and `Reason:`.
- **`examples/two-teams/`**, a small two-team project, and **`docs/cli.md`**, a command reference generated from the CLI definition (`scripts/gen_cli_reference.py`).
- **README** rewritten in Japanese (`README.md`) and English (`README.en.md`). The commands in the README are executed by the test suite.

### Changed

- `auto-dev init` and `teams init` append Auto Developer's generated files, common Python caches and `.env` / `.env.*` / `!.env.example` to `.gitignore`, without duplicating existing lines.
- `verify-step` computes the tree SHA with a temporary index and no longer stages files in your real Git index. `commit-step` commits exactly the verified tree.
- `verify-step --checkpoint HEAD` (or any ref) records the resolved commit SHA.
- `auto-dev run` writes the report before creating the bundle.
- Bundles and reports read evidence records only from the location `EvidenceStore` writes to, and include only tasks completed in the target plan; others are listed in `excluded_records`. Records without review evidence are excluded as `NO_REVIEW_EVIDENCE`, and the result is `INTEGRITY_ONLY` instead of `VERIFIED`.
- The canonical diff given to the Checker replaces lines that match secret patterns and omits the contents of `.env*` and key files.
- The `verify_local.sh` template picks Python from `$PYTHON`, then `python3` on `PATH`, and adds `src/` to `PYTHONPATH` when present.

### Fixed

- **macOS**: candidate tests failed to start with `SANDBOX_UNAVAILABLE` when the Python executable was a symbolic link into `/Library/Frameworks` (python.org installer, GitHub Actions `setup-python`).
- **Linux**: `bwrap` failed with `Resource temporarily unavailable` on machines with many threads, because `RLIMIT_NPROC` was computed from the process count. Sandbox start-up failures are now reported as `SANDBOX_UNAVAILABLE` instead of `NO_TESTS_RAN`. The sandbox availability check now actually starts the sandbox, with the same limits as real runs.
- Several issues in the team report and `brief` (base-branch status, model names) and in `teams cleanup`.

### Known issues

- `SECRET_FILE_IN_COMMIT` reads file names without `-z`, so a secret file whose name contains non-ASCII characters may not be detected. It also ignores type changes, so a tracked `.env` symlink replaced by a regular file is not detected (BACKLOG AUD-1004, AUD-1005).
- The independent release audit covered the changes from the start of Phase 70 (`7ea39c0`); the full diff since v0.8.0 was too large for a single audit.
- A few timing-dependent tests can fail on heavily loaded machines.

---

## [0.8.0] - 2026-11-19

### Major Milestone: Autonomous Governance, Multi-LLM Review Enforcement, Outcome Metrics, and Autonomous Reporting (Phases 63 - 65)

Auto Developer v0.8.0 introduces an end-to-end framework for autonomous software governance: structured multi-LLM review enforcement, automated audit dispatching with remediation budgets, quantifiable engineering outcome metrics, and standalone morning brief reporting.

### Added
- **Audit Finding Severity Dispatching & Module Remediation Budget (Stage 7 / Phase 63 / WWW)**:
  - **Severity-Based Triage**: Automatically routes high-severity defects (`P0`/`P1`) to `PLAN.md` tasks while channeling non-critical improvements (`P2`/`INFO`) to `BACKLOG.md`.
  - **Repetitive Failure Circuit Breaker**: Enforces `max_consecutive_cycles` limit per target module to halt runaway feedback loops and escalate persistent failures to human engineers (`ESCALATED`).
  - **Immutable Reproduction Tests**: Strictly protects existing reproduction test files (`AuditStore`) from overwriting during deduplication.
  - **Dedicated Duplicate Suppression (`DUPLICATE`)**: Distinctly tags and reports suppressed duplicate audit findings, eliminating false-positive rejected alerts in the Morning Brief.
- **Cross-Family Maker/Checker Policy Enforcement on `commit-step` (Phase 64 / XXX.1)**:
  - **Strict Multi-Family Enforcement**: Enforces `verify_model_independence(maker_model, checker_model, strict=True)` at the commit gate. Automatically rejects commits with exit code 4 if Maker and Checker share the same LLM family (e.g. Gemini Maker vs Gemini Checker) or use ambiguous family identifiers (`pro`).
  - **Structured Review Records**: Records `checker` provenance (`decision`, `reason`, `model`, `family`, `maker_model`, `maker_family`) directly inside cryptographic Evidence Records.
- **Autonomous Engineering Metrics Foundation (`auto-dev metrics` & `METRICS.md`) (Phases 64 & 65 / XXX & YYY.1)**:
  - **Transactional Event Logging (`MetricsLog`)**: Appends structured events (`verify_step`, `commit_step`, `rollback_step`, `checker_review`, `audit_stage`) to `.git/nightly_evidence/events.jsonl` under strict non-interference guarantees (metrics errors never fail critical development gates).
  - **20 Quantifiable Metrics & Decision Mapping**: Computes development throughput, first-pass verification rate, verify attempts, rework ratio (`rework_tasks_ratio`), out-of-path commits (`out_of_path_commits`), and historical baselines from Git commit history across Phases 50–65.
  - **CLI Metrics & Markdown Reporting**: `auto-dev metrics [--phase N] [--json] [--append-to PATH]` providing phase-level rollups and period-over-period delta comparisons.
- **Standalone Autonomous Report Generation & Morning Brief Recovery (Phase 65 / YYY.2)**:
  - **Standalone Reporting (`auto-dev report`)**: Generates and updates `NIGHTLY_REPORT.md` directly from Git HEAD, `PLAN.md`, evidence records, and `audit_stage.json` without requiring orchestrator `job_state.json`.
  - **Brief & Bundler Integration**: Automatically refreshes `NIGHTLY_REPORT.md` on `auto-dev brief` and `auto-dev bundle` execution, eliminating stale report warnings in bundle verification.
  - **Strict Tree SHA & Commit Ancestry Binding**: Verifies that Evidence Record commits are genuine ancestors of current HEAD before declaring tasks `APPROVE (Verified)`, preventing forged records from achieving verification.
- **Test Suite Expansion**:
  - Expanded test coverage from 471 tests to **560 tests (+89 tests)** across metrics, multi-family checks, standalone reports, and audit budgets.

### Changed
- Renamed `human_commits` to `out_of_path_commits` to accurately measure non-merge commits outside the `commit-step` pipeline.
- Synchronized package version to `0.8.0` across `pyproject.toml`, `plugin.json`, and `auto_developer.__init__`.

---

## [0.7.0] - 2026-11-16

### Major Milestone: Stage 4〜Stage 7 Completion — OS-Level Sandboxing, Multi-LLM Audit Boundary, Live Claude Opus 5.5 Independent Audit, and Evidence Integrity (Phases 19 - 62)

Auto Developer v0.7.0 delivers the full realization of Stages 4, 5, 6, and 7, establishing an industrial-grade multi-agent autonomous engineering platform under strict adversarial, fail-closed, and independent cross-model audit constraints.

### Added
- **Live Cross-Model Independent Repository Audit (Stage 7 / Anthropic Claude Opus 5.5)**:
  - **Live Messages API Integration**: Full production client calling Anthropic API with real provider request ID (`msg_...`) binding and cryptographic execution receipts.
  - **Strict Fail-Closed Architecture**: Non-zero exit on API key absence, HTTP error, or network failure, forbidding silent fall-through to zero findings.
  - **Audit Response Integrity & `stop_reason` Verification**: Detects `max_tokens` cutoffs and malformed outputs, halting safely as `AUDIT_PENDING` rather than falsely reporting zero defects.
  - **Post-Audit Diff Integrity Verification**: Rejects bundles as `AUDIT_OUTDATED` if product source code (`src/**`, `scripts/**`) is modified between audit execution and final release packaging.
- **OS-Level Sandboxing for Red-First Test Execution (`RedTestVerifier`)**:
  - **macOS `sandbox-exec` Default-Deny**: Enforced `(deny file-read*)` default policy combined with explicit read allowlists (`/usr/lib`, `/System`, system Python, isolated worktree) to prevent host secrets (`~/.ssh`, `~/.aws`, `.env`, keychains) from being accessed by candidate tests.
  - **Linux `bwrap` (Bubblewrap) Hardening**: Isolated mount namespace with complete network unsharing (`--unshare-net`), root filesystem isolation, and strict virtual environment boundary checks using `is_relative_to` to restrict host filesystem visibility.
  - **Clean Working Tree & Invariant Snapshot Guards**: Pre- and post-execution snapshot comparison ensuring untracked, ignored, and tracked files in the host repository remain 100% byte-identical.
- **Trivial & Fabricated Red Test Elimination (`TRIVIAL_RED`)**:
  - **Static AST Constant Inspection**: Detects unconditional assertion failures (e.g., `self.assertEqual(1, 2)`) before candidate test adoption.
  - **Dynamic Execution Tracing (`sys.settrace`)**: Verifies that candidate tests actually traverse production source code in stack frames during failure, rejecting detached or synthetic assertions.
  - **AST Causal Dependency & Guard Validation**: Recognizes product calls across factory functions, `self` attribute fixtures, and guarded `try-except` blocks while strictly rejecting dead branches (`if False:`), uninvoked lambdas, and re-raising handlers.
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
  - Expanded test coverage from 125 tests to **471 tests**, maintaining 100% pass rate in `scripts/verify_local.sh`.

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
