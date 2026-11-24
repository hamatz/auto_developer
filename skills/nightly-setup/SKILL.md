---
name: nightly-setup
description: Set up Auto Developer discipline files (PLAN.md, SPEC.md, scripts/verify_local.sh, .gitignore) and optional multi-team configuration (teams.json, plans/) in the current workspace.
---

# Nightly Setup: Initialize Auto Developer in Workspace

This skill sets up the Elephant-Goldfish Model (EGM) discipline files in the current project so that Auto Developer can autonomously implement, verify, and commit tasks overnight.

## Execution Steps

When the user runs `/nightly-setup`:
1. If the `auto-dev` CLI is installed and available, execute `auto-dev init` (or `python3 -m auto_developer.cli init`).
2. Otherwise, directly create/update the discipline files in the workspace root:
   - **`scripts/verify_local.sh`**:
     ```bash
     #!/usr/bin/env bash
     set -euo pipefail
     echo "=== Checking project verification checks ==="
     # Add your tests, linters, or typecheckers here (e.g., pytest, npm test, cargo test)
     exit 0
     ```
     Ensure it is executable (`chmod +x scripts/verify_local.sh`).
   - **`PLAN.md`**: Sequential task checklist (`- [ ] 1. First task`).
   - **`SPEC.md`**: Requirements and safety invariants.
   - **`.gitignore`**: Append `.dev/`, `logs/`, and `NIGHTLY_REPORT.md` if not already present.
3. **Ask About Multi-Team Mode**:
   - Ask the user whether they want to use **multi-team execution** (separate per-team Git worktrees, plans, and file ownership boundaries).
   - If the user wants teams, ask for the comma-separated team IDs (for example `core,ui`) and run:
     ```bash
     auto-dev teams init --teams core,ui
     ```
     (Substitute the user's team IDs, or omit `--teams` to use the default `core,ui`. Existing files are never overwritten.)
   - Explain the generated multi-team files:
     - **`teams.json`**: Defines `base.ref`, each team's `id`, `plan` (default `plans/<team_id>.md`), `owns` glob patterns, and `depends_on` (must remain `[]` in v0.9.0; overlapping `owns` or placing a team's `plan` inside another team's `owns` fails validation with `TEAMS_CONFIG_ERROR` / exit 2). Once committed, `teams.json` is treated as a protected file.
     - **`plans/<team_id>.md`**: Per-team sequential task checklist (`- [ ] 1. ...`).
4. Guide the developer on:
   - Drafting tasks in `PLAN.md` (or in each `plans/<team_id>.md` and adjusting `owns` in `teams.json` for multi-team mode),
   - Approving requirements in `SPEC.md`,
   - Optionally enabling the live auto-updating HTML dashboard (`auto-dev dashboard --enable`, or passing `--dashboard` to `auto-dev run` / `auto-dev teams run`; opens `.dev/dashboard.html` in a browser), and
   - Ensuring `scripts/verify_local.sh` runs real project checks (not a no-op; optionally add `# auto-dev: verify-timeout=<SEC>` within the first 20 lines if verification takes longer than the default 300s), then committing the setup files so the Git working tree is clean before `/nightly-run`.
