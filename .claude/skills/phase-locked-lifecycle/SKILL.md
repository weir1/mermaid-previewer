---
name: "Phase-Locked Lifecycle"
description: "The four phases of a session and what may NOT happen inside each — the ordering rules no script can enforce."
---

# Phase-Locked Lifecycle Skill

`closure_status.STEPS` declares which closure operations depend on which, and
`closure_pipeline` runs them in that order. That machinery covers the *steps*. It does not
cover the **arbitrary writes** a session makes between them — and every ordering failure
this workspace has paid for was one of those.

This skill names the four phases and, for each, states what may not happen inside it. It is
the judgement half; `just closure-status` is the mechanical half. **When they disagree, the
board is right and this file is stale — fix this file.**

## Why this is a skill and not a paragraph in `AGENTS.md`

The phase that produced it argues in its own briefing that adding prose to the boot file is
the mechanism that failed, and PH23-T05 spent a slot *removing* eleven lines from it. A skill
costs nothing until it is loaded, and it is loaded at the moment it applies.

## The four phases

Names come from `closure_status.PHASES`, which is the authority. A test reads that dict and
fails if a phase declared there is missing here — this file cannot drift from the graph
silently.

| # | Phase | What it produces |
|---|---|---|
| 1 | **SPECIFICATION** | the task is declared, agreed, planned, and its DoD names a real test |
| 2 | **CONSTRUCTION** | the work itself — the code, the tests, the docs |
| 3 | **HARMONIZATION** | derived artefacts caught up with the source (codemap, doc stamps) |
| 4 | **ATTESTATION & CLOSURE** | the proofs (gate, credit, review), then the irreversible acts |

**Phase 2 has no closure step, and that is the point.** It is the only phase whose output is
not an artefact some other phase derives from, which is exactly why nothing checks it and why
its two prohibitions below are the ones that keep being broken.

---

## PHASE 1 — SPECIFICATION

**Produces:** `task-declared` · `brief` · `plan` (`[complex]`) · `ledger-test` (`[complex]`).

### ❌ Do not start writing code before the brief is accepted.
`just work-done` refuses without one (exit 8) and refuses a `[complex]` task with no plan
(exit 7). A brief written afterwards is stamped `post-hoc` with the file count — the record
cannot conceal lateness, only report it.

### ❌ Do not leave the DoD's `test:` field until credit time.
**Found the hard way (PH23-T02).** `work-done` refused because the DoD named no test; naming
it edited `.ai/docs/tasks.md`, a **tracked** file, which made ninety-second-old evidence
STALE — the audit's own root cause reproduced inside its fix. The cure was not an exemption;
the ledger is real content. The cure was a phase boundary: `ledger-test` is a **phase-1**
step, and `prep()` refuses up front on any phase-1 debt. Name the test when you write it.

---

## PHASE 2 — CONSTRUCTION

**Produces:** the work. **Nothing closes it, so both rules here rest on you.**

### ❌ Do not transition a task's state before it is credited.
`[ ]` → `[x]` in `.ai/docs/tasks.md`, `(In Progress)` → `Complete` in `activeContext.md`:
both are **tracked writes**, so doing them before `just work-done` closes the gate that
`work-done` needs. Order is: credit, *then* transition.

And **both ledgers move together.** `test_ledger_consolidation` caught them disagreeing —
`tasks.md` saying done while `activeContext.md` still declared the task open leaves
`active_task()` bound to finished work, so the next task's evidence binds to the wrong id.

**The consequence, which costs a cycle if you don't plan for it** (found by dogfooding this
skill on the session that wrote it): the transition is itself a tracked write, so performing
it after `work-done` stales the gate `ship` needs, forcing a second `prep-close`. The precise
rule is therefore narrower than "credit, then transition" — it is **never transition between
the gate and the credit**. Batch the transition with the session's other content writes and
accept one more `prep-close`, or leave the last task's transition to the docs closure of the
same pass. What you must not do is flip it after `prep-close` and before `just work-done`,
where it closes the very gate the credit is about to ask for.

### ❌ Do not write a tracked file after the self-review is recorded.
`self_review._staged_diff` stages the **whole tree** into a temporary index, so the review
hash covers *everything that would be pushed* — memory-bank files and `AI_CHANGELOG.md`
included, deliberately (Q3: exempting bookkeeping would let unreviewed content reach origin).
Any write after the verdict voids it and forces a re-review.

This is a **construction-time** rule, not a review-time one. By the time you are reading the
diff it is already too late to learn it. Practical consequence: **the session's docs are
written before `just prep-close`, not after `just ship`.** The `close docs` / `close issues`
*records* are a different thing — they write only gitignored state
(`.ai/session-state.json`, `.ai/decision-log/`), so they void nothing and may follow `ship`.

---

## PHASE 3 — HARMONIZATION

**Produces:** `codemap` · `doc-stamps` (and advisory `doctor`).

### ❌ Do not regenerate a derived artefact after the gate or the review.
Evidence freshness is measured against the newest tracked working-tree file, so regenerating
either artefact after phase 4 begins makes the gate stale and voids the review. This is what
voided the observed session's review twice — once by a codemap regeneration, once by
doc-stamps. Neither changed a line of reviewed logic; both were correct operations in the
wrong phase.

### ❌ Do not hand-edit a derived artefact. Ever.
`.ai/codemap.md`, `evidence.json`, doc-stamp frontmatter, `.ai/decision-log/` — every one is
derived, and every one has exactly one writer. A hand-kept derived value is right on the day
it is written and a lie with a schedule thereafter. `just codemap` · `just doc-stamps
--apply` · `just verify-safe` · `scripts/decision_log.py`.

---

## PHASE 4 — ATTESTATION & CLOSURE

**Produces:** `gate` → `work-done` → `self-review` → `commit` → `push` → `close-git-push`,
then the two peers `close-docs` and `close-issues`.

### ❌ Do not run the steps by hand.
`just prep-close` settles everything a machine can, `just ship` does everything after the
verdict, and `ship` **refuses** over any unsettled input — asked of `closure_status.blockers()`
rather than a second hand-kept list. The gap between them, where you read the diff, is the
one piece of friction doing work: automating the review would make the strongest gate here a
rubber stamp.

### ❌ Do not conflate "happens after" with "is invalidated by".
**Found by the board on the session that built it.** `close-docs` was given a dependency on
`close-git-push` to encode the documented order, and `invalidations()` duly reported a
recorded docs closure as condemned by a pending push. The report was correct given the edge;
**the edge was wrong.** Redoing a push does not undo the fact that the memory bank was
updated.

An edge in that graph means *"redoing this dependency voids this step"* — nothing else.
Conventional order is expressed by **position** in `STEPS`, which is what `next_action()`
walks. If you are tempted to add an edge to make the board print steps in a nicer order,
you are about to reintroduce the exact imprecision the graph exists to remove.

### ❌ Do not push without approval.
The gate is mechanical; approval is not. `ship` enforces the first and cannot supply the
second.

---

## The one-line test before any write

> **"Which phase is this write in, and which phase am I in?"**

A write that belongs to an earlier phase than the one you are in invalidates something. If
you cannot answer, run **`just closure-status`** — it reports every step's state, the ONE
next action, and any attestation already condemned by an unsettled input. That uncertainty,
unresolved, is what cost the observed session four `git commit --amend` cycles.
