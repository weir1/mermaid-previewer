---
name: "Plan Before Code"
description: "Write the approach down before writing the code — problem, approach, files, risks, rollback, DoD."
---

# Plan-Before-Code Skill

For anything non-trivial, the plan is the artifact that makes review possible. Code written
without one is a set of decisions nobody — including you, next session — can reconstruct.

## When this is required

- The task touches **more than ~3 files**, or any enforcement/gate/policy path.
- The task is marked `[complex]` in the ledger.
- You are about to change something the OS relies on to tell the truth about itself
  (evidence, the decision log, the budget counters, the policy engine).

Skip it for a typo fix or a one-line doc edit. Writing a plan for those is theatre.

## The plan

Write `.ai/plans/PH#-T##.md` **before the first edit**:

```markdown
# PH#-T## — <title>

## Problem
What is actually wrong today. Include the observed behaviour, not just the desired one.
If you cannot state the problem without describing the solution, you do not have it yet.

## Approach
The design, and the one or two alternatives you rejected — with why. The rejected
options are the most useful part of this file in six weeks, so `--check` REQUIRES
at least one line here that *starts* with the word "Rejected":

Rejected: a sidecar JSON file — two records to keep true, and he reads the Markdown.

## Files to touch
Each path + what changes there. Anything not on this list is scope creep; add it here
deliberately or leave it out.

## Risks
What breaks if this is wrong. What it would look like in production. What is
irreversible.

## Rollback
The exact way back. "Revert the commit" only counts if nothing external was mutated.

## DoD
The concrete acceptance test. It must be something that can *fail*.
```

## The rules that make it worth doing

1. **The DoD comes before the code**, and it must be falsifiable. "Works correctly" is not a
   DoD; "`close git-push` exits non-zero when no review covers the diff" is. `just work-done`
   parses this line — a task with no stated acceptance test cannot be shown to have met it.
2. **A rejected alternative is declared, not mentioned** (PH15-T07). `--check` fails a plan
   whose Approach starts no line with "Rejected", and a paragraph merely containing the word
   does not satisfy it. This is not pedantry: when this bug was filed, its own description
   over-counted two plans by reading a mid-paragraph rejection as a declared one. Measured
   the day it was enforced, 5 of 25 plans declared none — including the one that prompted
   the task. Three house styles all pass: `**Rejected alternative 1 — …**`, `**Rejected:** …`,
   and `Rejected alternatives:` over a bullet list.
3. **The file list is a budget.** When you find yourself editing a file that is not on it,
   stop and decide: is this required by the task, or is it a separate task? Add it with a
   reason or leave it.
4. **Update the plan when the design changes.** A plan that contradicts the shipped code is
   worse than none — the next session will trust it.
5. **Discovery beats guessing.** If you cannot write the approach, you do not understand the
   code yet. Read it first (`.ai/memory-bank/INDEX.md` → the authoritative file; do not grep
   the repo to rediscover where things live).

## After the work

The plan is a review aid, not a trophy: check the shipped diff against the file list and the
DoD as the first pass of `self-review-diff`. A file in the diff that is not in the plan is a
finding until it is explained.

## The enforcement ratchet (this workspace)

Writing the plan is craft; the ratchet below is what makes skipping it visible.

```bash
just plan "PH7-T03"            # scaffolds .ai/plans/PH7-T03.md — never overwrites
just plan "PH7-T03" --check    # validate; exit 1 if missing or still a scaffold
```

- **`just work-done` refuses a `[complex]` task with no written plan** (exit 7, counter
  untouched). The check runs *before* the gate, because the plan is supposed to precede the
  code — you should not have to run the pipeline to be told you never wrote one.
- **A scaffold is not a plan.** The template's guidance sits in HTML comments, so an untouched
  scaffold has six headings and no content and fails validation by construction. Every section
  must carry real text.
- **The marker must be a marker.** `` `[complex]` `` inside a code span is a *mention* — a task
  that quotes this rule is not subject to it.
- What this does **not** bind: that the plan is any good — that's everything above this section.
  This is only the ratchet that makes skipping it visible.
- `--override "reason"` exists, demands a written reason, and lands in the decision log — the
  same no-silent-bypass rule as the validation gate.
