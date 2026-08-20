---
name: "Refactor Safely"
description: "Change structure without changing behaviour — green before, green after, one kind of change per commit."
---

# Refactor-Safely Skill

A refactor changes **structure, not behaviour**. The moment a diff does both, nobody can
review either half: a behaviour change hidden inside a rename is invisible, and a rename
inside a bug fix buries the fix.

## Preconditions — do not start without these

1. **A green suite.** `just test` passes *before* you touch anything. Refactoring on a red
   suite means you cannot tell what you broke.
2. **Coverage of what you are about to move.** If the behaviour has no test, write the
   characterisation test first — assert what the code *does today*, even if that is wrong.
   Fixing the wrongness is a separate, later change.
3. **A reason.** "Cleaner" is not one. Good reasons: the same rule exists in three places and
   they have drifted; a function does two things and only one is under test; the name lies.

## The loop

1. One mechanical transformation at a time — extract, inline, rename, move.
2. `just test` after **each** one. Not at the end. When it goes red you want the last change
   to be the suspect, not one of nine.
3. Commit (or at least checkpoint) between transformations.
4. Never mix in "while I'm here" fixes. Note them and do them after, as their own change.

## Collapsing duplicated rules — the one this repo needs most

The dominant defect class here is **the same rule implemented several times**, drifting until
the copies disagree while their docstrings all claim they agree. Collapsing them is the
highest-value refactor available, and it has its own procedure:

1. Find every copy (grep the *behaviour*, not the function name).
2. **Diff them against each other and write down where they disagree.** They will. The
   disagreements are latent bugs, and one of them is usually the reason you are here.
3. Decide which behaviour is correct — deliberately, and record why.
4. Write the tests for the chosen behaviour **before** collapsing, including one case per
   disagreement found in step 2.
5. Replace every copy with a call to the one implementation. Leave no "just this once"
   local variant; that is how the fork restarts.
6. If a caller genuinely needs different behaviour, that is a *parameter* on the one
   implementation, not a second copy.

Real instance: `evidence-pack.sh`, `gate_check` and `decision_log` each had their own answer
to "which task is in progress?". Two docstrings claimed a shared rule that did not exist, and
the divergence let a session's own summary sentence forge the gate's `task_id`.

## Comment–code parity — pull the Andon cord in the hunk you just touched (PH23-T04)

**After each transformation, before moving to the next one, re-read the comments and
docstrings inside the hunk you just changed and ask whether they still describe the code
below them.** Seconds during construction; forty minutes when `self-review` finds it, which
is where it was found the last three times.

The failure mode is not carelessness, it is ordering: the comment is written *first*,
describing the intent, and then the code is written *second* with a different — usually
better — design. Nothing ever goes back. What survives is a comment that reads as a
specification and is a fossil of a plan that was improved on. Two real instances:

- `doctor.py` said *"fall back to its built-in default and SAY so"*; the code deliberately
  did the opposite — `required, prohibited = [], []`, inventing no list. The better design
  won and the comment kept advertising the worse one.
- `failure_digest.py` opened *"`just verify-safe` runs two independent stages"*. True when
  written. In the kernel and every normally-onboarded workspace it runs **one**, because the
  suite is a pre-commit hook — so a reader following the docstring looked in the wrong file.
  Here the code never moved; the *deployment* did, which is why parity has to be re-checked
  even in a hunk you think you only reformatted.

Three questions per hunk, and they are fast:

1. Does the comment describe what the code **does**, or what someone meant it to do?
2. Does it name a file, function, flag or count that this diff just changed?
3. Would it still be true in a workspace configured differently from this one?

A docstring is the highest-risk case, because it is what the next reader trusts *instead* of
reading the code. When behaviour and comment disagree, fix the comment in the same
transformation — never note it for later. "Later" is what produced all three instances.

## Stop conditions

- **The suite goes red and you do not immediately know why** → revert to the last green
  checkpoint. Do not debug forward through a half-finished refactor.
- **The diff grows past what you can review in one pass** → ship the green slice, note the
  rest, continue in a fresh change. Splitting is expected; a huge unreviewable refactor is
  not.
- **You need to change a test to make it pass** → stop. Either this is not a refactor, or the
  test was pinning the behaviour you just broke. Decide which, explicitly, in the commit
  message.

## Verify

Behaviour-preservation is the claim, so review it as one: in `self-review-diff`, check that
every test change is a *move* or a *rename*, never a loosened assertion.
