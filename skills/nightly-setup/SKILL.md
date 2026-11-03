---
name: nightly-setup
description: Set up Auto Developer discipline files (PLAN.md, SPEC.md, scripts/verify_local.sh, .gitignore) in the current workspace.
---

# Nightly Setup: Initialize Auto Developer in Workspace

This skill sets up the Elephant-Goldfish Model (EGM) discipline files in the current project so that Auto Developer can autonomously implement, verify, and commit tasks overnight.

## Execution Steps

When the user runs `/nightly-setup`:
1. If the `auto-dev` CLI is installed and available, execute `auto-dev init`.
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
3. Guide the developer on drafting their first task in `PLAN.md` and approving requirements in `SPEC.md`.
