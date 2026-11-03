---
name: nightly-setup
description: Set up Auto Developer discipline files (PLAN.md, SPEC.md, scripts/verify_local.sh, .gitignore) in the current workspace.
---

# Nightly Setup: Initialize Auto Developer in Workspace

This skill sets up the Elephant-Goldfish Model (EGM) discipline files in the current project so that Auto Developer can autonomously implement, verify, and commit tasks overnight.

## Execution Steps

When the user runs `/nightly-setup`:
1. Execute `auto-dev init` (or `python3 -m auto_developer.cli init`) in the current workspace root.
2. Confirm that the following files were prepared:
   - `PLAN.md`: Sequential task tracking list.
   - `SPEC.md`: Product requirements and safety invariants.
   - `scripts/verify_local.sh`: Local deterministic verification gate.
   - `.gitignore`: Configured to ignore execution artifacts (`.dev/`, `logs/`, `NIGHTLY_REPORT.md`).
3. Guide the developer on drafting their first task in `PLAN.md` and approving requirements in `SPEC.md`.
