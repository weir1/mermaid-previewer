---
name: "Debug to Root Cause"
description: "Reproduce → isolate → find the cause → pin it with a test → fix. Never patch the symptom."
---

# Debug-Root-Cause Skill

The rule: **never change code you have not first watched misbehave.** A fix applied to a bug
you cannot reproduce is a guess, and a guess that makes the symptom disappear is worse than
the bug — it removes the evidence while leaving the cause.

## The sequence

### 1. Reproduce
Get a command that fails **every time**, and write it down. If it is intermittent, find what
makes it deterministic (ordering, clock, a file that exists on one machine) — that hunt
usually *is* the bug.

If you cannot reproduce it, you are not debugging yet. Stop and gather: exact command, exact
output, exact state. `just decisions --tail 20` and `.ai/session-log/` record what the OS
actually did, as opposed to what a summary says it did.

### 2. Isolate
Cut the failing surface in half until one component is provably responsible. Prefer
bisecting over reading: `git stash`, `git bisect`, commenting out the second half of a
pipeline, calling the function directly with the failing input.

Do not read the whole file hoping to spot it. That is how a plausible-but-innocent line gets
"fixed".

### 3. Name the cause
Write one sentence: **"X happens because Y."** If the sentence needs "somehow" or "maybe",
you are still at the symptom.

Push past the first plausible answer. The recurring shape in this codebase is that the
visible bug and the real cause are one level apart:
- the gate reported the wrong `task_id` → *cause:* three private copies of "which task is in
  progress?", two claiming in their docstrings to share a rule they did not;
- a dotfile rule silently did nothing → *cause:* `.lstrip("./")` strips *characters*, not a
  prefix;
- "no changes to review" → *cause:* a failed `git diff` returns "" exactly like a clean tree.

**Ask: is this one instance, or one of N?** If the cause is a duplicated rule, a wrong
assumption about an API, or a conflation of two states, the same bug is almost certainly
elsewhere. Grep for the pattern before fixing the instance.

### 4. Pin it with a test
Write the test that **fails now**, for this reason. Run it, watch it fail, and check the
failure message describes the actual bug. This is the step that converts a fix into a
guarantee — see `test-first`.

### 5. Fix the cause
While iterating, `just test-fast` re-runs only the tests reachable from the file you are
changing — fast enough to watch red-then-green on every edit instead of every few. It has
false negatives by construction and is **never a gate**: once the pinned test is green,
re-run the test, then `just test` (the whole suite), then check the neighbours you found in
step 3.

### 6. Record it
`just note-issue "title" "what, how found, why it happened, what still needs doing"` for
anything not fully closed. Findings that live only in a session summary do not survive.

## Anti-patterns

- **Symptom patching** — a `try/except` around the failure, a retry, a special case for the
  input that broke. Each of these hides the cause and adds a second bug.
- **Fixing on inspection** — "this looks wrong" without a reproduction. If it is wrong, you
  can demonstrate it; if you cannot, you are changing working code on a hunch.
- **Stopping at the first thing that makes it green.** Green after a change you do not
  understand means the bug moved.
- **Trusting a narrative over the record.** A memory file saying something was fixed is a
  claim; the code, the test, and the decision log are the evidence.
