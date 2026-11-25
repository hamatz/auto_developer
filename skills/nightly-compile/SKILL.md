---
name: nightly-compile
description: Compile EGM design documents (or document bundles) into reviewed IR and draft SPEC/PLAN/contract/acceptance files under .dev/compile/ without modifying SPEC.md, PLAN.md, or teams.json.
---

# Nightly Compile: Design Document to Draft SPEC & PLAN

This skill guides the agent through compiling an Elephant-Goldfish Model (EGM) design document (or a bundle of project documents) into a checked, independently reviewed Intermediate Representation (IR) and emitting draft files under `.dev/compile/` for human review and adoption (**INV-12**).

## Execution Steps

When the user runs `/nightly-compile`:

1. **Input Preparation**:
   - Accept either a single EGM design document (e.g., `docs/designs/*.md`) or a document bundle (`goal.md`, `architecture.md`, `decision.md`, `todo.md`, etc.).
   - If given a document bundle, assemble it into a single design document at `.dev/compile/<name>.design.md` following the `v0.10-draft` §5.2 mapping:
     - `goal.md` → Overview / Goals / Non-Goals
     - `architecture.md` → Technical Plan
     - `decision.md` → Architectural Decisions / Alternatives Considered
     - `todo.md` → Detailed Implementation Plan
   - If required sections (such as CUJs / acceptance criteria or Verification Plan) are missing, **do not guess or fabricate them** — stop and ask the human.

2. **Outline Check**:
   Run:
   ```bash
   auto-dev compile --outline <design_md> --json
   ```
   - If `auto-dev compile --outline` exits with **exit code 1** (終了コード 1, missing required sections), **stop and ask the human** to provide or clarify the missing sections before continuing.

3. **Draft IR (`auto-dev-ir/1`)**:
   Write the IR JSON (`"schema": "auto-dev-ir/1"`) to `.dev/compile/<name>.ir.json`:
   - Write `requirements`, `acceptance`, and `decisions` following the design document's exact wording; do **not** summarize or paraphrase in a way that changes meaning.
   - Attach `source` locations (`path`, `lines`, `sha256`) to every element, using the line ranges reported by `--outline`.
   - Record operations forbidden by decisions in `forbids`.
   - Record any unclear or undecided points in `open_questions` instead of guessing.

4. **Deterministic Check**:
   Run:
   ```bash
   auto-dev compile --check .dev/compile/<name>.ir.json --json
   ```
   (Pass `--spec <path>` if `SPEC.md` is not at the workspace root.)
   - Fix any reported `ERROR`s and re-run `--check` until 0 `ERROR`s remain.
   - Never delete requirements or `source` locations just to eliminate an `ERROR`.
   - If `L-9` (`open_questions` present) causes an `ERROR`, **stop and ask the human** to resolve the open questions before proceeding.

5. **Independent Review**:
   - Spawn a fresh reviewer on a **different model family** from the Maker that drafted the IR.
   - Pass **only**: the design document (or `.dev/compile/<name>.design.md`), `.dev/compile/<name>.ir.json`, and the `--check` output. (Do **not** skip this step when assembling from a document bundle.)
   - Instruct the reviewer to verify:
     - All Goals, CUJs, and Decisions from the design document are present in the IR.
     - Requirement statements preserve the design document's meaning without distortion.
     - `L-2`: Conditional branches ("A or B" / 「A または B」) specify handling for each case.
     - `L-3`: No requirement or decision both requires and forbids the same target.
     - `L-4`: No undefined terms are used outside the Glossary.
   - Save the reviewer's output to `.dev/compile/<name>.review.txt`, ending with `DECISION: APPROVE` or `DECISION: REJECT`.
   - If `DECISION: REJECT`, revise `.dev/compile/<name>.ir.json` and restart from **Step 4 (`auto-dev compile --check`)**.
   - If `DECISION: REJECT` occurs **2 consecutive times (2回連続)**, **stop and hand off to the human** with `.dev/compile/<name>.review.txt`.

6. **Emit Drafts & Human Handoff (INV-12)**:
   Once approved, emit the draft artifacts under `.dev/compile/`:
   ```bash
   auto-dev compile --emit .dev/compile/<name>.ir.json --lang ja
   ```
   (Use `--lang en` for English headings/boilerplate, `--out-dir .dev/compile/<dir>` to choose a subdirectory under `.dev/compile/`, and `--force` only when intentionally overwriting previously emitted draft files.)
   - Present to the human:
     - The generated directory (default `.dev/compile/<name>/`) and `contract.md` path.
     - Every item under **"人間が判断すること"** (all `WARN`s, cross-team steps, and manual impact analysis).
   - **INV-12**: Never modify `SPEC.md`, `PLAN.md`, `teams.json`, or `docs/contracts/` directly. The human reviews the drafts, copies them into `SPEC.md`, `PLAN.md`, `teams.json`, or `docs/contracts/`, and commits them.
