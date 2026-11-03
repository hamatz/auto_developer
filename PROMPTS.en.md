# Prompts to start with

Example messages to give the agent at each stage, from "I only have a vague idea in my head" to the first feature being merged. They are written to work with any tool — Antigravity, Claude Code, Codex and others. Replace the parts in `[ ]` with your own content. (The Japanese version, [`PROMPTS.md`](PROMPTS.md), is canonical.)

As a rule, **open a new session for each stage**. The idea behind this kit is not to let an agent implement while remembering the design conversation, and not to let it review while having watched the implementation's trial and error (Chapter 3 of the paper). Results from earlier stages are handed over not through conversation but through files (`docs/designs/`, `SPEC.md`, `PLAN.md`, git history).

| Stage | Who | Session | What remains |
| --- | --- | --- | --- |
| 1. Shape the idea | Design session (Elephant) and the human | May run long | Design document in `docs/designs/` |
| 2. Derive the specification and acceptance tests | Design session and the human | Same as 1 is fine | `SPEC.md`, acceptance tests in `tests/` (Red), `verify.yml` |
| 3. Break the work down | Design session | Same as 1 is fine | `PLAN.md` |
| 4. Implement one step | Implementation agent (Maker) | New for each step | A commit, a checkbox in `PLAN.md` |
| 5. Review the diff | Review agent (Checker) | New every time | A pruning commit, or a send-back |
| 6. Fix a defect | Implementation agent | New for each defect | A regression immunity test, the fix, `LESSONS.md` |

---

## 1. Shape the idea

### With the EGM plugin

Once [Elephant-Goldfish Model](https://github.com/google/elephant-goldfish-model) is installed, call `egm-brainstorm` in a new session and describe the idea as it is (`/egm-brainstorm` in Antigravity, `/egm:egm-brainstorm` in Claude Code, or from the skill picker in Codex).

```text
/egm-brainstorm
What I want to build is still vague. All I know so far is this:
- Whose problem, and what problem: [e.g. people writing self-published books can't easily typeset vertical-writing books]
- The shape I have in mind: [e.g. a writing tool that works just by opening it in a browser]
- What I'm worried about: [e.g. whether the output will be good enough to send to a printer]
Don't jump into design. Keep asking questions and draw out the issues.
I also want to decide together what to build first (the MVP) and what not to build.
```

Then follow the EGM flow.

1. Once the direction is settled, use `egm-draft` to write a draft design document (`design_draft.md`). It pauses after each section, so correct it as you go
2. Use `egm-review` (e.g. `/egm-review design_draft.md`) to have memoryless reviewers inspect the design document
3. Use `egm-resolve` to work through the findings one by one
4. The design document the human approves is placed in `docs/designs/`

### Without EGM

Give the following to a new session. This session becomes the "design session" from here on.

```text
You are my design partner. In this conversation, do not write a single line of code.
What I want to build is still vague. All I know so far is this:
- Whose problem, and what problem: [ ]
- The shape I have in mind: [ ]
- What I'm worried about: [ ]

Ask me at most three questions at a time. From my answers, we will decide together:
1. What to build first (the MVP), and what not to build
2. What a user does, and what must happen for it to count as success
3. What must never break (data, secrets, money and so on)
4. The technology options, and what each one gives up
Once we agree, write it up as a design document in docs/designs/[project-name].md.
```

Once the design document is ready, have **a different, new session** review it.

```text
Read only docs/designs/[project-name].md and review the design document.
Don't guess the author's intent; judge only by what is written.
List, quoting the relevant passages:
- Contradictions
- Places that different people could interpret differently
- Features with no statement of what counts as done
- Missing failure cases (invalid input, lost connections, simultaneous operations and so on)
Fixing them is up to me and the design session.
```

Take the findings back to the design session, fix them, and repeat until the human is satisfied.

---

## 2. Derive the specification and acceptance tests

Give this to the design session (or a new session). The acceptance tests written here become the "yardstick" for everything that follows.

```text
Based on docs/designs/[project-name].md, create the following three things.

1. SPEC.md
   Fill in each section of the template. Leave the design discussion in the design document;
   SPEC.md contains only the invariants and the definition of done that can be turned into acceptance tests.
   Make each core requirement correspond one-to-one with an acceptance test.
2. Acceptance tests under tests/
   At least one per item in SPEC.md. There is no implementation yet, so all of them should fail (Red).
   Actually run them to confirm they fail, and show me the output.
3. .github/workflows/verify.yml
   Replace the type-check, lint and test commands with the ones for the technology stack we chose.

Don't write any implementation code yet.
```

**What the human does** — Read `SPEC.md` and the acceptance tests, and check that they reflect the specification correctly. This is where, in this kit, the human spends the most time reading (Section 1.2 of the paper). Once satisfied, merge the pull request and, after the `verify` workflow has passed once, add `verify` to the ruleset's required status checks. **Only start letting the agent write the implementation once this wall is up.**

---

## 3. Break the work down

```text
Read SPEC.md and break the work needed to satisfy the first core requirement into steps in PLAN.md.
- Each step should be small enough to implement, test and commit within one session
- For each step, write which acceptance tests it turns Green
- Order the steps so that each one depends only on the results of earlier steps
```

---

## 4. Implement one step (Maker)

Open **a new session** for each step. There is no need to hand over the design conversation.

```text
Read AGENTS.md, SPEC.md and PLAN.md.
Implement only the first unfinished step in PLAN.md.
- Before starting, search LESSONS.md for related entries and read them
- Repeat implement → test until that step's acceptance tests are Green
- Don't make tests pass by rewriting them. If you think a test is wrong, stop and write the reason in PLAN.md
- If the same failure happens twice in a row, stop and write the EXPECTED / SAW in PLAN.md
- When done, tick the step in PLAN.md, commit, and open a pull request
```

---

## 5. Review the diff (Checker)

Open **a new session** for each pull request. Do not hand over the implementing session's log.

```text
Review according to "Review (Checker)" in AGENTS.md.
Read only SPEC.md and the following diff: git diff [main]...[branch-name]
- Have features or abstractions not in SPEC.md been added?
- Is there unnecessary code, or any added dependency?
- Is there anything that could break an invariant in SPEC.md?
You may directly fix only by deleting unnecessary code and by renaming or tidying without changing behavior.
If you fix something, rerun the checks, confirm they pass, then commit.
List everything else as findings.
If you think the specification itself is ambiguous or contradictory, report it as a problem with SPEC.md, not with the code.
```

**What the human does** — Confirm that CI passes, read the diffs of tests and the specification and of areas where mistakes do serious damage (authentication, payments, data deletion and so on), then approve.

---

## 6. Fix a defect

When the human finds a defect, give it to **a new session**. The key is to have the agent write a reproduction test before working on the fix.

```text
Please fix the following defect: [what I did, what happened, and what I expected]
Before fixing it:
1. Write a test in tests/ that reproduces this defect, and run it to show that it fails against the current code
2. Only after confirming the failure, fix it, and show that the same test now passes
3. This test stays as a regression immunity test. Don't delete or weaken it
4. Record it in LESSONS.md using the record format (Context / Trap / Rule / Rung / Immunity Test / Proof)
   Under Rung, write which rung you placed the mechanism that prevents this failure on. Aim one rung below a written warning
```

---

## Every so often

Give this to a new session once a month, when the model is updated, and so on.

```text
Read Part 2 of AGENTS.md and LESSONS.md, and list candidates for pruning.
- Norms that can now be enforced mechanically by tests or linters: candidates for deletion from AGENTS.md
- Low-risk guards that may no longer be needed with the current model
  (remove one and reproduce the original failure; delete it if the failure no longer happens. Guards on permissions, secrets and production connections are excluded)
I will decide whether to actually delete them.
```

If you use EGM, also check the drift between design documents and code with `egm-drift-report`.
