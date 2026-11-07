---
name: nightly-bundle
description: Package current development work, Git history, Evidence Records, and Checker logs into a sanitized, audit-ready Evidence Bundle ZIP.
---

# Nightly Bundle: Export Audit-Ready Evidence Package

This skill packages the current workspace into an exportable, tamper-evident **Evidence Bundle** that can be handed off to clients, code owners, or third-party auditors.

## When to Use

- When the human developer asks to "create a bundle", "package results", "prepare deliverables", or triggers `/nightly-bundle`.
- Before sharing development results with external reviewers to prove independent AI verification.

## Execution Steps

1. **Generate Evidence Bundle**:
   Execute the bundling command in the workspace:
   ```bash
   auto-dev bundle
   ```
   *(If `auto-dev` CLI is not installed globally, run `python3 -m auto_developer.cli bundle` or `python3 scripts/nightly_orchestrator.py bundle`)*

2. **Verify Bundle Contents**:
   The command creates a sanitized ZIP archive under `.dev/bundles/bundle_<job_id>.zip` containing:
   - `bundle_manifest.json`: Cryptographic SHA-256 checksums of all bundled files and task metadata.
   - `git_bundle.bundle`: Full Git commit history.
   - `source_snapshot.tar`: Clean filesystem snapshot of the HEAD tree.
   - `evidence/task_*.json`: Tamper-evident verification records (Tree SHA, SPEC SHA, and Checker logs).
   - `job_state.json`: State machine transition and attempt history.
   - `logs/nightly/`: Execution logs.
   - `NIGHTLY_REPORT.md`: Formatted Morning Brief.
   - *Sanitization*: Secret files (`.env*`, `*.pem`, `*.key`) and OS noise (`__MACOSX`, `.DS_Store`) are automatically excluded.

3. **Present to Developer**:
   Output the result with:
   - The absolute file path to the generated ZIP.
   - The number of included tasks and commit hash.
   - The verification command for third parties:
     ```bash
     auto-dev verify-bundle <path-to-bundle.zip>
     ```
     or via Antigravity skill: `/nightly-verify <path-to-bundle.zip>`
