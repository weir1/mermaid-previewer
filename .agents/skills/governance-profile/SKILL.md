---
name: "Governance Profile"
description: "How much ceremony a workspace runs — declaring full/lite, and the two-command closure lite enables (PH26-T01)."
---

# Governance Profile Skill

Load this when a workspace feels too heavy or too light for the work it does: the operator says
closure is costing too many turns, the 2-slot cap is being overridden repeatedly, or someone asks
what `just wrap` / `just land` are.

## The two profiles

| | `full` (default) | `lite` |
|---|---|---|
| work slots per session | 2 | 8 |
| closure | run step by step | `wrap` → self-review → `land` |
| `verify-safe` + test suite | required | **required** |
| pre-push self-review | required | **required** |
| pre-work brief | required | **required** |
| `plan-before-code` for `[complex]` | required | **required** |
| memory bank + handover | required | **required** |

**`workspace_profile.KNOBS` is a closed set — `work_max` and `fast_closure`, nothing else.** There
is deliberately no key for a gate. `lite` means fewer turns; it never means less proof. A test
(`tests/test_workspace_profile.py::ALiteWorkspaceGetsTheTwoDialsAndNoOthers`) fails the day a pack
in `.ai/profiles.yaml` declares a key outside that set, which is why the claim above is checkable
rather than merely written down.

## Declaring one

```bash
just profile                                  # what is active here, and what it costs
just profile-set lite --until 2026-09-15      # declare it, with an expiry
just profile-set full                         # back to the kernel's discipline
```

It writes `profile:` (and `profile_until:`) into `.ai/workspace.yaml`. **Declared, never inferred**
— no heuristic decides a workspace "looks like a product", the same rule `role_registry` applies to
`role_pack:` and for the same reason: a governance weight a script guessed is one nobody agreed to.

**Every unresolved case goes to the STRICTER side.** Absent key → `full`. Unknown name → refused by
name, with the available profiles listed, and `full` — a typo must never inherit the laxer profile.
Expired `profile_until:` → `full`, with the date in the reason. Unparseable date → `full`. A
workspace that declares `lite` but has never received `.ai/profiles.yaml` is told it is **behind on
a deploy**, not that it made a typo — telling someone one deploy behind that they typo'd sends them
to fix the thing that is not wrong.

## The `lite` closure, in two commands

```bash
# 1. Write the session's docs FIRST — activeContext.md, progress.md, AI_CHANGELOG.md,
#    knownIssues.md. "Content before proof": the review hash must cover everything
#    that would be pushed, so a doc written after the gate makes the gate stale.
just wrap "what this session did"          # session-end → archive → codemap → doc-stamps
                                           # → doctor → verify-safe, one pass

# 2. THE GAP. This is yours and nothing collapses it.
just review-diff                           # read it
just self-review pass "what you actually checked"

# 3.
just land "what this session did" "the next step"   # commit → push → close git-push
                                                    # → close docs → close issues → handover
```

**Why the gap survives a "fast" path.** A one-command closure would have to record the review
itself, and a review nobody read certifies nothing — it would convert the strongest gate in the
workspace into a rubber stamp while calling itself an improvement. This is PH23-T02's argument for
splitting `prep` from `ship`, and being in a hurry is not a counter-argument to it. Speed comes
from collapsing the steps a machine can settle. It never comes from collapsing the one step whose
whole value is that a human read something.

**`land` still pushes, and push is `[Destructive/Dependency]`** — ask the operator before running
it, exactly as with `just ship`.

**`WRAP` and `LAND` are built from `PREP` and `SHIP`**, not re-listed, so there is no second
statement of the closure order to drift from `closure_status.STEPS`. `land` adds only what `ship`
structurally cannot run for you: `close docs` and `close issues` write gitignored state, carry no
edge in the DAG, and are therefore the two steps no blocker ever names — which is exactly why
sessions forgot them.

**If `land` fails after the push**, it does not stop. The commit and push are already done and
returning early cannot undo them; one unrecorded closure must not also cost the handover the next
session boots from. It reports what failed and names the commands to finish by hand.

## When to reach for `lite`

Reach for it when the record says the current profile is not being obeyed. The case this was built
from: `@zenithos`, first day, **five `override work-done` records before 08:19**, each naming the
2+3 budget as its reason, beside a prior session that credited seven work tasks and closed none of
the three closures. Every override correct, reasoned and logged — which is the point. A control
whose normal outcome is an authorised override is no longer distinguishable from a control that is
off, and the operator who learns overrides are routine is the same one who has to take
`close git-push --override` seriously.

So the question is never "is this workspace important?" It is **"is the cap being obeyed here?"**
If a session is overriding it every time, either the cap is wrong for the work or the work is
wrong for the session — and `just decisions` tells you which.

## When NOT to

- **Do not reach for it to go faster on a task you are behind on.** The profile changes how many
  tasks you *start*, never how well you do each one (AGENTS.md § QUALITY OVERRIDES BUDGET). A task
  that needs more room gets a handover, not a laxer profile.
- **Do not set `lite` without an expiry unless you mean forever.** The operator's framing was
  *"during initial scaffolding/sprints"*, and a relaxation with no end date is scoped to nothing.
  `profile_until:` makes the OS ask whether the sprint ended, since nothing else will.
- **Do not add a knob to make a gate optional.** If that seems necessary, the gate is wrong and
  should be fixed for everyone — `.ai/profiles.yaml`'s header says the same thing, and the closed-set
  test enforces it.
