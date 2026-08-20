---
name: "Self-Review a Diff"
description: "How to actually review the session's own diff before pushing it — the craft half of the PH7-T04 gate."
---

# Self-Review Skill

`just close git-push` refuses without a recorded review of this session's diff. That
mechanism proves a review was recorded against this exact content. **It cannot prove the
review was any good.** This skill is that half.

Use it whenever you reach the `git-push` closure task, and any time a diff is about to
leave the workspace.

**Run it *after* `just verify-safe`, not before.** `verify-safe` rewrites `evidence.json`,
which is tracked, so it changes the diff and voids any review recorded before it. Committing
is safe — the diff is measured from the session's base commit, not from `HEAD`.

**What counts as "the diff":** commits since the session started **+** the working tree **+**
untracked files (`commit-all` runs `git add -A`, so they ship too). A `fail` verdict does not
satisfy the `git-push` closure task — fix, then review again.

## 1. Read the actual diff — not your memory of writing it

```bash
just self-review-status     # base, size, and whether anything already covers it
just review-diff            # the exact text under review
```

Read it as **incoming code from someone else**. The failure mode this exists to prevent is
reviewing your intentions instead of your output — you remember what you meant to write,
so your eyes slide over what you wrote.

If the diff is large, review it in passes rather than skimming once.

## 2. The passes

Go through these in order. Each is a different question; running them together is how
things get missed.

1. **Scope** — is anything in this diff that the task did not ask for? Debug prints, a
   stray rename, a file you touched while exploring. Remove it. An unexplained hunk is a
   review finding, not a bonus.
2. **Correctness at the boundaries** — for each new branch: what happens on empty, missing,
   malformed, and "the tool failed"? The recurring bug class in this workspace is
   **an error path that produces the same value as the success path** (a failed `git diff`
   returning "" and reading as "no changes"; `--verify` echoing a SHA that names nothing).
3. **Does it actually bind?** — for anything claiming to enforce, gate, check or verify:
   write down the input that *should* be refused, then confirm the code refuses it. Every
   forgery found in this repo survived because nobody did that one step.
4. **Blast radius** — new external side effects? New writes outside the workspace root?
   Anything that touches `.git/`, credentials, or another workspace?
5. **Tests** — is there a test that fails without this change? For a bug fix, does it
   reproduce the original bug? See `test-first`.
6. **The record** — memory bank, `AI_CHANGELOG.md`, and any doc the change makes wrong.
   A stale doc is a defect with a delayed fuse.

## 3. Record the verdict

```bash
just self-review pass "read every hunk; checked the refusal paths and the empty-input cases"
just self-review pass-with-findings "…" "medium: token_budget divisor untested above 1MB"
just self-review fail "…" "high: close() records the closure before the check runs"
```

- The note must say **what you checked**, not that you checked. "Reviewed" is the
  unfalsifiable claim the gate exists to stop.
- `pass-with-findings` is for findings you are **deliberately** accepting or deferring —
  log them in `knownIssues.md` too, or they evaporate.
- `fail` does not satisfy the closure. Fix, which changes the diff, then review again.

**The record is bound to the diff's content hash.** Any edit after recording voids it.
That is deliberate: it makes "review, then quietly add one more thing" impossible.

## 4. Findings you cannot fix now

Do not silently downgrade them to make the closure pass. Either fix it, or record
`pass-with-findings` **and** `just note-issue "…" "…"` so it survives the session. An
override (`just close git-push "reason"`) is logged to `.ai/decision-log/` and shows up in
`just audit` — use it honestly or not at all.
