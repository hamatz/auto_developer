---
name: nightly-verify
description: Independently audit and verify an Evidence Bundle ZIP for tamper-detection, Tree SHA binding, SPEC immutability, and Checker approvals.
---

# Nightly Verify: Independent Audit of Evidence Bundles

This skill allows Antigravity to act as an independent third-party auditor, verifying that an Evidence Bundle has not been tampered with and was genuinely approved through the Auto Developer pipeline.

## When to Use

- When the user asks to "verify bundle", "audit zip", "check deliverables", or triggers `/nightly-verify <path>`.
- When reviewing pull requests, contractor handoffs, or autonomous overnight run deliverables.

## Execution Steps

1. **Locate Target Bundle**:
   Identify the bundle ZIP from user arguments, uploaded file path, or the latest bundle in `.dev/bundles/`.

2. **Run Cryptographic Verification**:
   Execute the verification engine:
   ```bash
   auto-dev verify-bundle <path-to-bundle.zip> --json
   ```
   *(Or `python3 -m auto_developer.cli verify-bundle <path-to-bundle.zip> --json`)*

3. **Perform Four-Point Audit**:
   - **Checksum Integrity**: Ensure every file inside the archive matches its `bundle_manifest.json` SHA-256 hash. If any file was modified, report `TAMPERED`.
   - **Evidence Binding**: Verify that task Git Tree SHAs match the cryptographic Evidence Tokens.
   - **Independent Checker Review**: Confirm that stateless Checker subagents explicitly returned `APPROVE`. Reject any `UNVERIFIED` or `REJECTED` tasks.
   - **Specification Immutability**: Verify that `SPEC.md` has not been modified between verification and commit.

4. **Format Audit Report**:
   Present an authoritative audit summary in chat:
   - **Overall Status**: `PASSED (Verified & Tamper-Free)` or `FAILED (Integrity Breach / Unverified)`
   - **Job Metadata**: Job ID, HEAD Commit SHA, total verified files
   - **Task Breakdown Table**: Task ID, Title, Status, Tree SHA, Checker Decision
   - **Discrepancies / Warnings**: Detailed list of any errors, missing records, or hash mismatches
