---
name: "Issue Logging Protocol"
description: "Every bug names the test that proves it fixed — filing, resolving, and waiving knownIssues.md entries (PH7-T06)."
---

# Issue Logging Skill

Load this skill whenever the user says "note this issue" / "log this bug" / "remember this
problem", or when you need to resolve or waive an existing entry in `.ai/memory-bank/knownIssues.md`.

## Filing

1. Run `just note-issue "TITLE" "DESCRIPTION"` (appends to `.ai/memory-bank/knownIssues.md`).
2. Confirm: "Issue logged in knownIssues.md".

```bash
just note-issue "Title" "Desc" "tests/test_x.py::WidgetTest::test_it"   # file, naming the test
just resolve-issue "widget explodes" "tests/test_x.py::WidgetTest::test_it" "PH7-T06"
just resolve-issue "widget explodes" "" "PH7-T06"    # use the ref already on the entry
just issue-waive   "widget explodes" "a filed notice, not a defect" "PH7-T06"
just issues-gap                                       # open issues naming no test
```

## The rules

**`Resolved` is a verdict, not a word you type.** Before this mechanism existed, an issue was
filed as prose and closed by editing the bullet to say `✅ RESOLVED` — nothing ever asked whether
the defect could still happen. `evidence.json` cannot be hand-written; that word could, until now.

- **Every entry carries a `test:` field, including when there is no test** — written as a
  declared `(none yet)`, never as a missing line. A missing line cannot be told from a legacy
  entry; a declared absence can be counted, reported and refused. (Mention vs declaration, again.)
- **`just resolve-issue` refuses** unless the named test **exists and this workspace's own runner
  collects it** — asked of the real `unittest` loader in a subprocess, not guessed from the
  source. Two conditions, because either alone is a lie: the file must be one
  `unittest discover -s tests -p 'test_*.py'` actually reaches, *and* the loader must yield a
  real test for the name. **A refusal writes nothing.**
- **Issues filed before 2026-08-08 are grandfathered** and resolve without friction.
  Retro-flagging ~11 entries nobody can now write a test for turns a real signal into noise
  everyone clicks past. `just issues-gap` reports the gap as a number instead.
- **`just issue-waive` is the only way out**, it demands a written reason, and it lands in
  `.ai/decision-log/` — the same no-silent-bypass rule as the validation gate. Use it for entries
  that are *notices, not defects* (several open ones say so in their own text), and in a
  workspace with no test runner, where the waiver record is itself the useful signal.
- **What this does not bind:** that the named test is any good, or that it would have caught the
  bug. A test asserting `True` satisfies every check here — that is `test-first`'s job. This is
  the ratchet that makes skipping it visible.
- `scripts/note_issue.py` is the **only writer** of `knownIssues.md`, which is why resolution
  lives there rather than in a second script. Never hand-edit a `RESOLVED` marker into the file.
