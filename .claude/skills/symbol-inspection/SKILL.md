---
name: "Symbol Inspection"
description: "Read one function or class with just sym instead of the whole file — exact line numbers, driven by ast."
---

# Symbol Inspection Skill

`just sym <file> <symbol>` returns one function or class's signature, docstring and body,
with exact 1-indexed line numbers — driven by Python's `ast` module, so it cannot be fooled
by a name that only appears in a comment or a string. Measured on a real file in this repo:
slicing `task_transition.py`'s `done()` cost ~1,050 tokens against ~5,206 for the whole file
— an 80% reduction, on a file this repo's own scripts have plenty of.

## When to reach for it

- You need to see **one** function or method in a file you have not read yet, and the file is
  large enough that reading it whole would cost real context — this repo's own scripts
  routinely run past 500 lines.
- You are about to make a precision edit to one symbol and want its exact line range first,
  rather than re-deriving it from a full `Read`.
- Late in a session, when context is tightest — this is exactly when this OS's own memory
  bank has recorded its worst decisions, and exactly when a whole-file read is most expensive.

## When NOT to reach for it

- **The file is small.** A 60-line script costs almost nothing to `Read` whole; slicing it
  saves nothing and adds a round trip.
- **You need surrounding context** — imports, sibling functions it calls, module-level
  constants it depends on. This tool answers "what does this one symbol say", not "how does
  this file fit together." Read the whole file when the question is structural.
- **You are about to edit**, not just read. This tool is read-only and does the honest half of
  the job: it tells you where a symbol starts and ends. It does not write anything — use `Edit`
  as normal once you know the range.
- **Non-Python files.** `ast` only parses Python; there is no equivalent here for Markdown,
  YAML or shell.

## Usage

```bash
just sym scripts/task_transition.py done          # a top-level function
just sym scripts/task_transition.py Outer.run      # a name shared by two scopes — see below
just sym scripts/task_transition.py done --json    # machine-readable
```

## Disambiguation

If a name is defined in more than one scope (two different classes each with a `run` method,
say), `just sym` **refuses** rather than guessing — a wrong guess would hand back the wrong
body while looking authoritative, which is worse than the whole-file read this tool exists to
avoid. The refusal lists every real match's dotted qualname (`Base.run`, `Outer.run`,
`Outer.Inner.run`); re-ask with the one you meant.

## The honest limit

This is a reading tool and only a reading tool — see PH25-T01's own DoD: "it helps reading,
and does nothing for the editing that follows." And per every skill in this pack: a tool no
model knows about saves nothing, which is why this file exists rather than leaving the
capability to be rediscovered by accident.
