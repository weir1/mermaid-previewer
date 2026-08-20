---
name: "Pre-Work Brief"
description: "No blind work — discuss the task in plain English and record the operator's own words before starting it."
---

# Pre-Work Brief Skill

The gate governs whether an act is **safe**. `off-plan` governs whether the scope **grew**.
Neither asks whether the operator *understood and agreed* — so a session can be perfectly
safe, perfectly on-plan, and still spend its budget on work he would have trimmed.

This is the procedure for holding that discussion well. The ratchet that makes skipping it
visible is at the bottom.

## Why this exists (the measurement, not the theory)

Taken 2026-08-15, in this operator's own fleet:

- `zenith-core`'s `UPWORK_HUNT_KIT.md` — a complete Upwork profile, headline and stack — was
  finished 2026-07-20 and untouched for 26 days. Its own `MISSION_LOG.md` (2026-07-15) reads
  *"Under Law 7 (External Consequence) the hunt starts immediately."* Income over that period:
  zero.
- `@jobscraper`'s last product commit aged to 15 days while every file touched there that week
  was `.ai/` kernel state. July: 118 commits. August: 10, all kernel sync.

**Both workspaces were following their plans correctly the entire time.** Alignment was never
the failure. An unheld conversation was.

Note also what those two workspaces already contained: `Directive 8 — External Outcomes Over
Internal Complexity` and a 90-Day Engineering Rule, both correct, both written down, both being
violated by the workspace that wrote them. **A correct rule, written as prose, has failed twice
here.** That is why this is a command that refuses and not another paragraph.

## Holding the discussion well

Eight fields. Two of them carry the weight; the rest are the context that makes those two
answerable.

**Explain before you ask (PH22-T09).** `--accept` refuses while any of *Why this task, now ·
What · Why it matters · Cheaper alternative* is unwritten — and the refusal means shown to
**him**, not merely written to the file. He cannot justify a task he has not been shown; on
2026-08-16 he was asked to, against a page of untouched scaffold, and stopped it. The lookup
fields (Goal · North-Star stage · Price) deliberately do not block the conversation.

| Field | What a good answer looks like |
|---|---|
| **Why this task, now** | Why THIS one and not another open task — what it unblocks, what deferring it costs. Every other field describes the task; this one describes the **choice**. |
| **What** | Plain English, no jargon, no file paths. If he would have to ask "what does that mean", rewrite it. |
| **Why it matters** | The business lens — what it saves, unlocks or earns. *"Nothing yet, this is groundwork"* is a legitimate answer and a far better one than a stretch. |
| **Goal** | A real id from `.ai/plan.md`. An unknown id is **refused**, not warned about. If nothing fits, the honest move is `just off-plan "<request>"` first. |
| **North-Star stage** | Which ladder rung this feeds. *"None — infrastructure"* is legitimate; inventing a link to stage 1 is not. |
| **Price** | Sessions, with the basis. `just forecast` computes it. |
| **Cheaper alternative** | **The AI's half.** See below. |
| **Operator's justification** | **His own words.** See below. |

### The cheaper alternative — play the accountability partner

This field is required because the counter-argument is the part an agent skips when it is
eager to start. Name the smaller version that gets most of the value, and *argue for it*.

A warning with no argument in it is noise, and noise gets tuned out — after which the
mechanism protects nothing. So:

- **Do not** write "we could do less." Name the specific smaller thing and what it gives up.
- **Do** cite evidence when you have it. The strongest challenge in the founding discussion was
  a git log showing a finished asset untouched for 26 days — not an opinion.
- **Concede when he answers well.** If he supplies real business context you did not have,
  say so plainly and record it. The resolution rule is his: *"solid justification from both
  side."* Winning is not the goal; an examined decision is.
- Being right is not enough to proceed. He decides.

### The operator's justification — the anti-forgery half

**The AI must not be able to hold this discussion with itself.** A brief with this field empty
is refused, and the task cannot be credited.

- Ask with `AskUserQuestion` (Claude) or the Antigravity equivalent — labelled options,
  recommended first. **Never** end a turn with an idle prose question.
- Record **his words**, not your summary of them. `--accept` appends them verbatim with a
  timestamp.
- If he pushes back on your cheaper alternative, that pushback *is* the justification. Record
  it and proceed with what he chose.

## What this can and cannot prove

**It cannot prove a human spoke.** It proves a brief exists, names a real goal, has both halves
filled, and whether it predated the code (`base_head` → `pre-work` / `post-hoc`). An agent
determined to forge all of that can.

The self-review gate (PH7-T04) has the identical limit and says so. Overclaiming here would
make this mechanism an instance of the defect class it exists to police — a claim nothing
verifies, the most common bug in this repo. What it buys is that **forging the discussion
becomes a deliberate act rather than the default path.**

It also cannot force the brief to come first: nothing can observe "the first edit of a task."
So it records when it was written instead. A brief written after the code says `post-hoc` with
the file count, every time. It cannot prevent lateness; it must not be able to conceal it.

## The ratchet

```bash
just brief "PH22-T01"                        # scaffold .ai/prework/PH22-T01.md
just brief "PH22-T01" --accept "his words"   # record HIS justification
just brief "PH22-T01" --check                # validate; exit 1 if one-sided
just brief --ratio                           # governance vs product, with its basis
```

- **`just work-done` refuses a task with no valid brief** — exit 8, counter untouched, checked
  before the plan gate because that is the real order of events: he agrees, then the plan is
  written, then the code.
- **`session-start` names** a declared task with no brief, so the omission surfaces when it is
  still cheap rather than at credit time (PH16-T32's lesson).
- **An unfilled scaffold is not a brief.** Its guidance lives in HTML comments, so an untouched
  scaffold fails validation by construction — the same trick as the plan gate.
- **Every task, not only `[complex]` ones.** The 26 days this rule was written against
  contained no complex task at all.
- `--override "reason"` on `work-done` exists, demands a written reason, and lands in the
  decision log. No silent bypass.

## The governance ratio

`just brief --ratio` reports how much of recent work went to the OS rather than to what the
workspace exists to produce, **classified by what the diff touched — not by which workspace it
ran in.** That distinction is the whole point: `@jobscraper` is an income workspace, and a
per-workspace classifier would have scored its kernel-only week as outcome work and reported
the exact opposite of the truth.

In the kernel itself, `scripts/`, `tests/` and `doc/` **are** the product, so only `.ai/`,
`.agents/`, `.claude/` and the protocol files count there. Without that carve-out the kernel
scores ~99% every session, and a number that cannot vary tells you nothing.

Lead the brief with it when it is high. It is the number he asked for.

---

# The version contract (PH22-T02)

The brief governs one task. The contract governs the **rung** those tasks add up to — which
working features belong to `v0.1.0`, agreed before any of them is built.

```bash
just versions             # the current rung: features, which are proved, what blocks a bump
just versions --check     # exit 1 on an unreadable or unproven contract
just versions --scaffold  # write the template — then AGREE it before building
```

## Every feature is written twice

The checkbox bullet is the **plain-English half** — what it *does*. The `tech:` sub-bullet is
what it *is*. That order is deliberate: the readable line is what the parser keys on, so it
cannot be the half that gets dropped in a hurry.

```markdown
- [ ] Scrape one keyword query and write a clean list of job titles and links
  - tech: `scripts/scrape.py` → `data/jobs.json`, selector with API fallback
  - verify: test tests/test_scrape.py::Query::test_writes_titles_and_links
```

His requirement, verbatim: *"Plain english is needed in every workspaace with tech language
because half the time i dont even know what ai is building or what its doing."* A feature list
written only in technical language is not a contract he agreed to — it is one he was shown.

**What no parser can catch:** a bullet and a `tech:` line that are *both* jargon. Validation
rejects the unambiguous cases (a bullet that is only a code span, or has no prose word at all)
and nothing more. Writing the readable half honestly is your job, not the checker's.

## Nothing is verified by assertion

There is no `verified: true` to set. Three forms, all evaluated when read:

| Form | Checked by |
|---|---|
| `verify: test <ref>` | `run_tests.collects()` — the real runner, not a regex |
| `verify: artefact <path>` | the file is stat'd |
| `verify: attest <date> "<what>" expires <N>d` | compared against today; **expires** |

The attestation is the weakest and knowingly so: for non-software rungs — 20 proposals sent, a
document delivered — his word is the only evidence there is. It ages out so stale proof cannot
hold a rung open forever. `--check` reports `stale` with the age rather than quietly counting it.

## Absence is honest; a hollow contract is not

**No `.ai/versions.md` is not an error.** No workspace has one yet, and writing rungs on his
behalf destroys the only thing the file is for — that he agreed to them first. A file that
exists and declares a rung with no features **is** an error: that is agreement-shaped and empty.

`version_plan.py` refuses to stamp a bump while the current rung holds an unverified feature,
and names it. The kernel is excluded — `.ai/os_version.json` + `phase_ledger` already own its
ladder, and a second one would give the repo two answers to "what version is it".
