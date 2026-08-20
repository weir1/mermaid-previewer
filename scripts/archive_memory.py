#!/usr/bin/env python3
"""
archive_memory.py — `just archive-memory`: keep the hot memory files inside the
context budget by moving closed history to `.ai/memory-bank/archive/` (PH6-T01).

AGENTS.md has said "hot memory files ≤ ~200 lines, archive completed phases to
`.ai/memory-bank/archive/`" since v3.4 and `just doctor` has warned when a file blew
past it — but nothing ever did the archiving, so `archive/` sat empty while
`knownIssues.md` grew to 221 lines. Every session paid for that in context. This is
the missing half: the rule, executed.

## What it moves, and what it will never touch
Each hot file is a chronological log of *closed* work wrapped around *live* state.
Only the closed part is archivable, and only ever by an explicit per-file rule. A file
writes its history in several *shapes* over its life, so each rule is a list of `Kind`s
matched in order (see `RULES`):

  | file              | archivable shapes                                  | keeps |
  |-------------------|----------------------------------------------------|-------|
  | knownIssues.md    | `## Resolved (…)`; `## Active Issues (<date>)` once every issue in it is resolved | newest 3 |
  | progress.md       | `## <date> — …` / `### <date> — …` sections; `- **<date> …:**` bullets | newest 8 |
  | activeContext.md  | the session-history paragraph under every label it uses (`**Prior Session:**`, `**Session before:**`, `**Earlier:**`, `**Earlier still:**`, `**Earlier Session:**`); dated `> ### 🔎 <date>` and `> **<date>` notes | newest 3 |

`## Known Limitations`, an undated `## Active Issues`, a dated section still holding one
open issue, `**Last Session:**`, `> ### ⬇️ THE PRIORITY STACK` (and any other
non-dated blockquote heading), the frontmatter, the quick card and every task list are
structurally excluded — they are live state, not history. A block whose date cannot be
parsed is **never** archived: if the tool can't tell how old something is, it has no
business moving it.

**Why shapes, plural (PH16-T05).** Each of these files quietly changed how it writes
history — `activeContext.md` to `**Session before:**`, `progress.md` to `## <date>`
headings, `knownIssues.md` to resolving issues in place — while `RULES` still matched
the older form. The blocks did not become "held back by `--keep`"; they became
*invisible*, and this tool answered "nothing to archive" for weeks in exactly the words
it uses for a healthy file. `entry_budget.py` is the check that makes that state
impossible to reach silently again.

**Why a shape must also say how it ENDS, and why that is not a heading name
(PH16-T15).** The same drift ran the other way and cost more. The session-history kinds
ended at `^\\*\\*(Tasklist|Pending)` — the headings that followed them until PH16-T09
replaced both with `**Open tasks (working set).**`. A `stop` that can no longer match does
not shorten a block; it *unbounds* it, and this file's end is the live working set. So
`--apply` moved every open-task declaration into the archive while reporting "2 closed
block(s)", `task_ledger.active_task()` — which reads this file and no other to bind
`evidence.json` to the session's task — then returned `""`, and 4 tasks silently went
`Pending` → `''` (`all_tasks()` still counted them, out of `tasks.md`, which is why
nothing noticed). A session entry is a *paragraph*, so it now ends where a paragraph
ends: `Kind(ends_on_blank=True)`. **The direction of failure is the design criterion** —
a shape that ends too early leaves history in the hot file, visible as a line count; one
that ends too late moves live state, invisibly. When in doubt, under-reach.

## The one rule no `Rule` can override
`protected_lines()` refuses to move any line `task_ledger.is_declaration()` recognises out
of a `task_ledger.LEDGER_FILES` file — `activeContext.md` and `tasks.md` — whatever the
kinds say. Fixing the stop pattern above fixes *that* drift and nothing about the next one,
so this is keyed off the ledger's own file list rather than off anything a rule author must
remember. It delegates to `is_declaration` rather than matching `PH\\d+-T\\d+`, because
session prose names task ids in nearly every entry and a bare id match would refuse to
archive any session block ever written. When a block its own shape called *history* is
refused here, that is reported loudly: it means a rule has drifted past the live section.

## Rules this holds itself to
1. **Nothing is deleted, ever.** Blocks are moved verbatim into
   `.ai/memory-bank/archive/<file>.md` and the hot file gains a `[[link]]` pointing at
   them, so the retrieval-first index still reaches the history in one hop. The test
   suite pins losslessness: hot-after + archive-after contains every line of
   hot-before.
2. **Dry run by default.** Rewriting memory is `[Docs Only]` and therefore autonomous,
   but it is still a rewrite — you see the plan before it happens. `--apply` writes.
3. **Count, not calendar, is the default knob.** This workspace runs several sessions a
   day; "older than 30 days" would archive nothing on a file that doubled this week.
   `--keep-days N` adds an age floor on top when you want one.
4. **It reports what it did NOT fix.** If a file is still over budget after archiving,
   that is printed, not glossed over.

Archived files are invisible to `memory_decay.py` / `freshen_memory.py` (both glob
`*.md` non-recursively), so archiving history can never make the memory bank look stale.

Usage:
  archive-memory                 # plan for files over the 200-line budget
  archive-memory --apply         # ...actually move them
  archive-memory --all           # consider every file, not just oversized ones
  archive-memory --keep 5        # keep more recent blocks than the default
  archive-memory --json
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

MEMORY_DIRNAME = Path(".ai") / "memory-bank"
ARCHIVE_DIRNAME = MEMORY_DIRNAME / "archive"
MAX_LINES = 200                       # the budget `just doctor` enforces

#: What a line of hot-memory prose actually costs, measured across the real memory bank
#: 2026-08-12 (PH16-T19): `projectbrief` 37 · `systemPatterns` 59 · `techContext` 81 ·
#: `progress` 84 · `activeContext` 132 · `decisions` 144 — and `knownIssues` **515**, because
#: `note_issue.py` writes one unwrapped line per entry. 150 sits just above the widest healthy
#: file (`decisions.md`, 144), so converting the unit **re-states** the existing 200-line budget
#: rather than tightening it: every file that passes on lines today still passes on chars, and
#: only the one whose density is 3.5× everything else's changes verdict. Calibrated deliberately
#: against the widest healthy file and not the mean — a budget derived from the average would
#: fail honest files for being wordy, which is not the thing being policed.
CHARS_PER_LINE = 150

#: **The budget the context actually charges.** Lines were always a proxy for tokens, and the
#: proxy broke in both directions at once: `knownIssues.md` presented as a mild 1.5× overage
#: (297/200 lines) while costing ~38k tokens — a quarter of the whole ~150k session budget and
#: ~5× what the line count implied — and *inside* that same file a pasted 102-line status table
#: was half the remaining lines but 9% of the remaining chars. Charging by the line therefore
#: understated the file ~5× and, in the same breath, pointed the archiver at its cheapest
#: content. Chars are used rather than tokens because they need no tokeniser and no network:
#: ~4 chars/token holds for this corpus, and a budget that can be computed offline in every one
#: of the 46 governed workspaces is worth more than an exact one that cannot.
MAX_CHARS = MAX_LINES * CHARS_PER_LINE

_DATE_RE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")

#: The two ways a block writes *which entry of that date it is* — anchored immediately
#: after a date, because that is the only position where either shape means an ordinal.
#: `(g)` in `- **2026-08-14 (g) — …` (progress.md, activeContext.md) and `20:25` in
#: `[2026-08-14 20:25 IST]` (knownIssues.md). A single `a`–`z` only: `(PH6-T15)` is a task
#: id, not a rank, and it sits in exactly the same position.
_ORDINAL_LETTER_RE = re.compile(r"\s*\(([a-z])\)")
_ORDINAL_TIME_RE = re.compile(r"\s+(\d{2}:\d{2})\b")

#: Ordinals are `(scheme, value)`. The scheme is carried so that two *conventions* are
#: never silently compared as one scale — see `proves_older`. Absence is a real answer
#: and sorts below every ordinal, which is the shape this repo's history actually has:
#: the first entry of a date carried no letter until the day needed a second.
NO_ORDINAL: tuple[int, str] = (0, "")
_LETTER, _TIME = 1, 2


def over_budget(text: str, max_lines: int = MAX_LINES) -> bool:
    """Is this file over the context budget — on EITHER measure?

    **The one definition of "over budget", so the checker and the tool cannot
    disagree about which files are in scope.** They did, and it produced a
    permanently red `doctor`: `entry_budget.check()` (what `doctor` prints)
    already counted a file over on chars *or* lines, while `main()` selected
    files to consider on lines alone. `knownIssues.md` — 66 lines, 38,643 chars
    — was therefore over budget to the check and invisible to the archiver, so
    `doctor` FAILED with "run `just archive-memory --apply`" and that exact
    command archived nothing, every time.

    PH16-T19 made chars the deciding unit and defined `MAX_CHARS` in this very
    module; this function is the half of that change that never landed. A check
    whose prescribed remedy provably cannot satisfy it is worse than a silent
    one — a control that cries wolf is the one an operator learns to route
    around, and this one also held `just health` at exit 1.

    `max_lines` is a parameter because `--max-lines` is a real flag; the char
    budget is derived from it via `CHARS_PER_LINE` so the two move together
    rather than drifting the moment someone overrides one of them.
    """
    return over_budget_counts(len(text.splitlines()), len(text), max_lines)


def over_budget_counts(lines: int, chars: int, max_lines: int = MAX_LINES) -> bool:
    """`over_budget()` for code that already holds the counts (a plan's `*_after`).

    Same rule, one implementation — the whole point of this pair. Splitting it in
    two here is what let `unfixable()` keep a lines-only guard and silently drop a
    char-over file from the "what I could not fix" report.
    """
    return lines > max_lines or chars > max_lines * CHARS_PER_LINE

POINTER = ("_Older entries were moved to [[{link}]] by `just archive-memory` "
           "(context budget). Nothing was deleted._")


class Kind:
    """One *shape* of block a memory file writes, and how that shape ends.

    A hot file does not write its history in a single form. `activeContext.md` holds
    dated `> ### 🔎` audit notes, `> **<date>` correction notes and `**Session before:**`
    paragraphs; `progress.md` holds both `- **<date>:**` bullets and `## <date>` heading
    sections. They do not end alike — a blockquote note ends at the first line that is
    not a blockquote, a bullet entry ends at the first unindented non-bullet line — so
    `stop` belongs to the shape, not to the file (PH16-T05).

    start          a line that opens a block of this shape
    archivable     True (closed history), False (live state — matched so the shape is
                   *recognised* and can end the block before it, never moved), or a
                   predicate over the block's lines for shapes whose archivability
                   depends on content rather than on the opening line
    stop           a line that ends a block of this shape without opening a new one.
                   Blank lines never match a `stop` — the blockquote shapes need them to
                   pass straight through — so a paragraph shape uses `ends_on_blank`.
    ends_on_blank  this shape is a *paragraph*: it ends at the first blank line, and that
                   blank line belongs to it (PH16-T15). Naming a heading in `stop` is what
                   made this file's session blocks run to EOF when PH16-T09 renamed the
                   heading; a paragraph's end is a property of the paragraph.
    absorbs        lines this shape OWNS, even when another kind's `start` matches them
                   (PH16-T19). A container shape needs this the moment a sub-shape becomes
                   a kind in its own right: `## Resolved (<date>)` is dated by its heading
                   and its entries are not, so cutting the section at its first entry moved
                   the heading and stranded the entries — orphan bullets in the hot file,
                   an empty heading in the archive, and a clean success report over both.
                   Checked before `start`, so it is the narrow escape hatch from "the first
                   matching kind wins", not a second dispatch rule.
    """

    def __init__(self, start, archivable=True, stop=None, ends_on_blank=False, absorbs=None):
        self.start = re.compile(start)
        self.archivable = archivable
        self.stop = re.compile(stop) if stop else None
        self.ends_on_blank = ends_on_blank
        self.absorbs = re.compile(absorbs) if absorbs else None

    def is_archivable(self, lines: list[str]) -> bool:
        if callable(self.archivable):
            return bool(self.archivable(lines))
        return bool(self.archivable)


class Rule:
    """How to cut one memory file into blocks.

    kinds    the block shapes this file writes, in match order — the FIRST whose
             `start` matches wins, so a specific shape must precede a general one
    keep     how many of the newest archivable blocks stay in the hot file
    name     the file this rule is for, stamped from the `RULES` dict below
    """

    def __init__(self, keep, kinds, name=""):
        self.kinds = kinds
        self.keep = keep
        self.name = name

    def kind_for(self, line: str) -> "Kind | None":
        for kind in self.kinds:
            if kind.start.match(line):
                return kind
        return None


def entry_allowance(rule: Rule) -> int:
    """One entry's fair share of the file's line allowance: `MAX_LINES ÷ rule.keep`.

    Derived, never chosen — the file budget divided by how many history entries that file
    is designed to hold hot. `progress.md` / `activeContext.md` / `knownIssues.md` all keep
    3, so the share is 66. A `keep` of 0 means "archive every closed block", so no entry is
    held back and the single remaining entry may use the whole file — that is the allowance,
    not a division by zero.

    **Lives here, in the module that owns both inputs (PH24-T14).** It was
    `entry_budget.entry_budget()`, and `unfixable()` needed the same figure to say *which
    entry* is the bulk — `entry_budget` imports this module, so reaching for it there would
    be a cycle, and re-deriving `MAX_LINES // rule.keep` locally would be a second copy of
    the one thing both files agree on. `entry_budget.entry_budget()` now delegates here, so
    the share `doctor` prints and the share the archiver blames an entry against cannot drift.
    """
    return MAX_LINES if rule.keep <= 0 else MAX_LINES // rule.keep


_ISSUE_BULLET_RE = re.compile(r"^- (.+)$")
_RESOLVED_BULLET_RE = re.compile(r"^- (✅|~~)")

#: Where one `knownIssues.md` entry ends: the first line that is neither a bullet nor indented
#: under one. An entry's sub-bullets and its `test:` ref are indented, so they travel with it —
#: PH7-T06 makes the ref and the issue a single record, and archiving an issue away from the
#: test that proves it fixed would break the thing that check exists to guarantee.
_ENTRY_ENDS = r"^(?![-\s])"


# `all_issues_resolved()` lived here until PH16-T19 and is deliberately deleted rather than
# left importable. It answered "is every issue in this dated section closed?", which was the
# question when the archivable unit for `knownIssues.md` was the *section*; the unit is now the
# entry, and each entry answers for itself by its own marker. A predicate kept past the shape it
# decided is how this file's rules have drifted into lying twice already (PH16-T05, PH16-T15) —
# the next reader would reasonably assume something still consults it. The two regexes below
# outlive it because the marker they read is what the entry kinds are built from.


RULES = {
    "knownIssues.md": Rule(keep=3, kinds=[
        # `## Resolved (2026-07-31 — …)` — history by its heading alone. Nothing in such a
        # section can be open, so it still moves whole: it `absorbs` every line up to the next
        # heading. That was implicit until PH16-T19 (nothing inside it matched a `Kind`, so
        # nothing could cut it) and is now stated, because the entry kinds below would
        # otherwise split the section at its first entry — the heading carries the date, the
        # entries under it do not, so the heading would move and the entries could never
        # follow it. See `AResolvedSectionStillMovesWholeWithItsEntries`.
        Kind(start=r"^## Resolved\b", absorbs=r"^(?!## )"),
        # **The archivable unit here is the ENTRY, not the section (PH16-T19).** It used to be
        # the section: a dated `## Active Issues (<date>)` moved only once *every* issue under
        # it was resolved. Measured against the real file on 2026-08-12 — 45 open, 47 resolved,
        # 14 sections, only 2 of them fully resolved — that meant 12 resolved entries sat hot
        # behind 2 open ones in `2026-08-09` alone, and the archiver's own floor (288 lines) sat
        # *above* the 200-line budget it was reporting against. It asked every session for a
        # target no honest action could reach: the only ways to comply were resolving issues
        # that were not fixed, or deleting history, and this module's Rule 1 forbids the second
        # while `--gap` exists to catch the first.
        #
        # A resolved issue is closed history on its own date; an open one is live and pins only
        # itself. Both end at the first line that is neither a bullet nor indented under one, so
        # an entry's sub-bullets and its `test:` ref travel with it — PH7-T06 makes the ref and
        # the issue one record, and splitting them would archive an issue away from the test
        # that proves it fixed.
        Kind(start=_RESOLVED_BULLET_RE.pattern, stop=_ENTRY_ENDS),
        # The open entry, matched so that it is RECOGNISED — it must end the resolved entry
        # above it rather than be swallowed by it. Listing only the archivable shape is how
        # PH16-T05 and PH16-T15 both happened: a shape no `Kind` matches is not "left alone",
        # it becomes an invisible continuation of its neighbour, and here that neighbour is
        # something this tool is about to move.
        Kind(start=_ISSUE_BULLET_RE.pattern, archivable=False, stop=_ENTRY_ENDS),
        # Every heading — dated or not — is now an anchor for whatever is still open under it,
        # and never moves on its own. `all_issues_resolved` governed the dated form until
        # PH16-T19 and is deliberately gone rather than left wired to a shape it no longer
        # decides: a rule kept past its job is how this file's rules have drifted into lying
        # twice already.
        Kind(start=r"^## ", archivable=False),
    ]),
    # `keep` was 8 when a progress entry was ~10 lines; entries now measure 26-40, so
    # 8 of them is 310 lines against a 200-line file budget — the default policy could
    # no longer satisfy the rule it exists to serve. 4 was the largest keep that fit at
    # the time, measured against the real file (keep 4 → 189 lines, keep 5 → 243).
    #
    # Re-tuned 4 → 3 on 2026-08-16 (PH16-T40's session), by the same measurement and for
    # the same reason: entries have kept growing (the three then-current ones measured
    # 53, 65 and 54 lines and filled 191 of the 200 alone), so a fourth of ANY size broke
    # the budget and `test_progress_md_is_actually_within_the_line_budget` went red on a
    # correctly-written entry. keep 3 → 187 lines, keep 4 → 237. The derived per-entry
    # share is now 200 ÷ 3 ≈ 66. This knob is *expected* to be re-measured, not defended:
    # the alternative was trimming a real entry to nine lines to fit an arithmetic that
    # had already stopped holding, which is the file lying about the session instead.
    "progress.md": Rule(keep=3, kinds=[
        # `## 2026-08-10 (d) — …` / `### 2026-08-11 — …`: the shape this file has
        # actually used since 2026-08-05. `# Progress Log` (one hash) is not a match.
        Kind(start=r"^#{2,3} \d{4}-\d{2}-\d{2}"),
        # `- **2026-07-31 (PH6-T15):**` with indented children; any unindented
        # non-bullet line ends the entry.
        Kind(start=r"^- \*\*\d{4}-\d{2}-\d{2}", stop=r"^(?![-\s])"),
    ]),
    "activeContext.md": Rule(keep=3, kinds=[
        # The session-history paragraph, under every label this file has really used:
        # `**Prior Session:**`, `**Session before:**`, `**Earlier:**`, `**Earlier still:**`,
        # `**Earlier Session:**`. Matched as a *label form* — an "Earlier"/"Prior"/"Session"
        # word plus an optional qualifier — because the three-literal version silently failed
        # on `**Earlier still:**` and `**Earlier Session:**`, which then became invisible
        # continuations of the block above them (PH16-T15: that is why the live run reported
        # 2 moves and made 3). `**Last Session:**` is recognised but never archivable: it is
        # the current state of the workspace.
        #
        # `ends_on_blank`, not a `stop` naming the next heading. These blocks used to stop at
        # `^\*\*(Tasklist|Pending)` — the headings that followed them until PH16-T09 replaced
        # both with `**Open tasks (working set).**`. A stop that cannot match means the block
        # runs to EOF, and this file's EOF is the live working set, so `--apply` moved every
        # open-task declaration into the archive while reporting 2 closed session blocks.
        Kind(start=r"^\*\*(Prior|Earlier)( Session| still)?:\*\*", ends_on_blank=True),
        Kind(start=r"^\*\*Session before:\*\*", ends_on_blank=True),
        Kind(start=r"^\*\*Last Session:\*\*", archivable=False, ends_on_blank=True),
        # Dated blockquote notes: `> ### 🔎 2026-08-10 — …` and `> **2026-08-10 — …`.
        # They end at the first line that is not a blockquote.
        Kind(start=r"^> #{3} 🔎 \d{4}-\d{2}-\d{2}", stop=r"^(?!>)"),
        Kind(start=r"^> \*\*\d{4}-\d{2}-\d{2}", stop=r"^(?!>)"),
        # Any OTHER blockquote heading — `> ### ⬇️ THE PRIORITY STACK` — is live state.
        # Listed last so it catches what the dated forms above did not, and listed at
        # all so it ENDS the dated note before it instead of being swallowed by it.
        Kind(start=r"^> #{3} ", archivable=False, stop=r"^(?!>)"),
        # `**Open tasks (working set).**` (PH16-T09) and its predecessors
        # `**Tasklist (v3.6 …)**` / `**Pending (v3.9 …)**` — the task declarations this
        # file carries. All three are kept: downstream workspaces still carry the older
        # headings, and a shape this rule stops recognising is a shape it can swallow.
        # Declared live EXPLICITLY rather than left as content no rule mentions:
        # `entry_budget` treats unaccounted-for bulk as rule drift, and "this is
        # deliberately live" is a claim the rules should have to make out loud. These are
        # also the most dangerous lines in the memory bank to move — `task_ledger` reads
        # this file FIRST, and 21 task IDs are declared here and nowhere else (measured
        # 2026-08-11), so archiving them would delete them from the OS's own ledger. Since
        # PH16-T15 that is enforced by an invariant as well as stated by this rule.
        Kind(start=r"^\*\*(Open tasks|Tasklist|Pending)", archivable=False),
        Kind(start=r"^\*\*Current Phase:\*\*", archivable=False),
    ]),
}

# Each rule learns the file it is for. `split_blocks` needs the filename to apply the ledger
# invariant, and most callers pass only the rule — so the name travels with the rule rather
# than depending on every call site remembering to supply it (PH16-T15).
for _name, _rule in RULES.items():
    _rule.name = _name


#: Resolved once. `_UNREAD` distinguishes "not looked yet" from a genuine `None` answer, so a
#: deployment with no `task_ledger` is not re-probed per line (PH16-T15 self-review).
_UNREAD = object()
_LEDGER = _UNREAD


def _ledger_reader():
    """`task_ledger`, or None when this deployment has no copy of it.

    A seam, so the missing-module path is testable: the guard below must **refuse** rather
    than disarm itself, which is the opposite of `gate_check`'s tolerant import (there,
    absence means one rule does not fire; here it would mean a destructive rewrite proceeds
    unguarded).

    **Resolved once and remembered, because `_declares_a_task` calls this for every line of
    every ledger file.** The first draft re-ran `sys.path.insert(...)` per call: cutting the
    real `activeContext.md` took `sys.path` from 6 entries to 191. Never a wrong answer — the
    import is idempotent — but the import path is process-global, so every later import in that
    process rescans the duplicates and, in the test suite, the growth outlives the run that
    caused it. A seam called per line has to be cheap to call.
    """
    global _LEDGER
    if _LEDGER is not _UNREAD:
        return _LEDGER
    try:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import task_ledger
        _LEDGER = task_ledger
    except Exception:  # noqa: BLE001
        _LEDGER = None
    return _LEDGER


_ISSUES = _UNREAD


def _issue_reader():
    """`note_issue`, or None when this deployment has no copy of it.

    Same seam, same caching discipline, same reason as `_ledger_reader`: the archiver must not
    re-implement someone else's parse of the memory bank. `knownIssues.md` carries two
    resolution conventions and only `note_issue` knows how to tell them apart — see
    `_merge_superseded`.
    """
    global _ISSUES
    if _ISSUES is not _UNREAD:
        return _ISSUES
    try:
        scripts_dir = str(Path(__file__).resolve().parent)
        if scripts_dir not in sys.path:
            sys.path.insert(0, scripts_dir)
        import note_issue
        _ISSUES = note_issue
    except Exception:  # noqa: BLE001
        _ISSUES = None
    return _ISSUES


def _merge_superseded(name: str, lines: list[str], blocks: list[dict]) -> list[dict]:
    """Rejoin a **prefix-form** resolution to the issue it closes. They are one record.

    `knownIssues.md` resolves an issue two ways. The in-place form rewrites the entry, so the
    marker and the issue are the same line and nothing can separate them. The **prefix** form
    is a separate note written directly above the original `- ⚠️` line, and
    `note_issue._mark_superseded` pairs the two *by strict line adjacency*.

    Making the entry the archivable unit (PH16-T19) made that note independently movable, and
    it moved: caught during this task's own `--apply`, three prefix notes archived away, their
    originals stopped being superseded, and `just issues-gap` went **32 → 35** — three fixed
    issues silently back to "open, no regression test", inflating the number the OS uses to
    audit its own honesty. It survived the obvious check because the open-issue *set* was
    byte-identical on both sides; what moved was resolution STATE, which no count of titles
    can see. Same family as PH16-T15 — the archiver moving live state and reporting success —
    reached by separating two lines instead of by an unbounded block.

    Delegated to `note_issue.parse_entries` rather than re-derived here. The discriminator is
    subtle (the prefix note carries no `[HH:MM IST]` of its own, because it is not the
    original entry) and a second copy of it in this module is precisely the drift that has
    already cost this file twice. No reader → nothing is merged, and the pairs stay put
    because the resolved kind cannot claim what it cannot see.
    """
    ni = _issue_reader()
    if ni is None or Path(name).name != "knownIssues.md" or len(blocks) < 2:
        return blocks
    try:
        entries = ni.parse_entries("\n".join(lines))
    except Exception:  # noqa: BLE001
        return blocks
    # First line of every entry that is closed ONLY by the note directly above it.
    superseded = {e["start"] for e in entries if e.get("superseded")}
    if not superseded:
        return blocks

    # Blocks are contiguous, so a running offset gives each one its first line number.
    starts, offset = [], 0
    for block in blocks:
        starts.append(offset)
        offset += len(block["lines"])

    merged: list[dict] = []
    for block, start in zip(blocks, starts):
        prev = merged[-1] if merged else None
        if (prev is not None and start in superseded
                and _RESOLVED_BULLET_RE.match(prev["lines"][0])):
            prev["lines"].extend(block["lines"])
            continue
        merged.append(block)
    for index, block in enumerate(merged):
        block["index"] = index
    return merged


def _ledger_filenames() -> tuple[Path, ...]:
    tl = _ledger_reader()
    if tl is None:
        # The names, so the guard still knows WHICH files to refuse. Only reached where
        # `task_ledger.py` is absent, and then nothing may leave these files at all.
        return (Path("activeContext.md"), Path("tasks.md"))
    return tuple(Path(Path(rel).name) for rel in tl.LEDGER_FILES)


#: The files `task_ledger` reads to answer "what tasks exist and which one is this session
#: working on?" — `activeContext.md` and `tasks.md`. Derived from `task_ledger.LEDGER_FILES`
#: so that adding a ledger file there arms the guard here automatically (PH16-T15).
LEDGER_FILENAMES = _ledger_filenames()


def _declares_a_task(line: str) -> bool:
    """Is this line a task *declaration* — the thing the OS's ledger reads as state?

    Delegates to `task_ledger.is_declaration`, deliberately. A `PH\\d+-T\\d+` search would
    be wrong in the expensive direction: session-history prose names task ids in nearly
    every entry ("**Earlier:** … PH19-T01 Slices 2+3 shipped"), so a bare id match would
    refuse to archive any session block ever written and pin this file permanently over
    budget. The mention-vs-declaration distinction is exactly what `is_declaration`
    implements, and this repo has already been bitten by a second copy of it drifting
    (`_scan` carried one).

    No ledger reader → every line of a ledger file is treated as a declaration, i.e. the
    file is refused whole. Fail closed: this function's answer decides whether live state
    may be rewritten.
    """
    tl = _ledger_reader()
    if tl is None:
        return True
    return bool(tl.is_declaration(line))


def protected_lines(name: str, lines: list[str]) -> list[str]:
    """The lines in `lines` that may never leave `name`, whatever the rules say.

    **A hard invariant, not a `Rule` field.** PH16-T09 renamed a heading, the session
    `Kind`'s `stop` went stale, one block ran to EOF and `--apply` moved the entire live
    working set into the archive — reporting "2 closed block(s)" while it did it. Fixing
    that `Kind` fixes *that* drift and nothing about the next one, so the refusal is keyed
    off the ledger's own file list rather than off anything a rule author has to remember:
    a `Kind` added tomorrow is covered without being told about this.

    Empty for every file the ledger does not read, so `progress.md` — which names a task id
    in most entries — keeps archiving normally.
    """
    if Path(name).name not in {p.name for p in LEDGER_FILENAMES}:
        return []
    return [line for line in lines if _declares_a_task(line)]


def ws_root() -> Path:
    """Workspace root per AGENTS.md's anti-drift rule — git top-level, never the open file."""
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def block_date(line: str) -> date | None:
    """The date a block is stamped with, or None when it carries none."""
    match = _DATE_RE.search(line)
    if not match:
        return None
    try:
        return date(*(int(part) for part in match.groups()))
    except ValueError:
        return None


def block_ordinal(line: str) -> tuple[int, str]:
    """*Which* entry of its date this block is — read from the text, never from position.

    **The whole point is position-independence (PH16-T30).** `select()` used to break a
    date tie with `-index`, which silently assumes the newest entry sits at the top of the
    file. Half this workspace's dated files are written the other way — `just log-session`
    appends, `AI_CHANGELOG.md` appends, the archive itself is oldest-first — and on a file
    where every block shares one date (this workspace runs 3–7 sessions a day) that
    assumption *is* the entire ranking. It archived the entry written minutes earlier and
    kept three older ones, and reported success.

    An ordinal is anchored to a date occurrence, so a parenthesised task id further along
    the line cannot be mistaken for a rank, and `- ✅ **RESOLVED 2026-08-14 (PH16-T26)**
    [2026-08-14 17:12 IST]` still yields `17:12` from its *second* date. Returns
    `NO_ORDINAL` when the block carries none — an answer the guard below acts on rather
    than papers over.

    **It need not come from the same date `block_date()` read**, and that is deliberate: a
    resolved issue carries its resolution date first and its filing time second
    (`- ✅ **RESOLVED 2026-08-15 (PH16-T30)** [2026-08-14 20:45 IST]`), so requiring one
    anchor would throw away the only sub-day order that file writes. Sound because an
    ordinal is only ever compared against a sibling of the *same* date and the same scheme:
    within such a group the filing time is stable, position-independent and monotone with
    the order the entries were written, which is all `proves_older` asks of it.
    """
    for match in _DATE_RE.finditer(line):
        rest = line[match.end():]
        letter = _ORDINAL_LETTER_RE.match(rest)
        if letter:
            return (_LETTER, letter.group(1))
        clock = _ORDINAL_TIME_RE.match(rest)
        if clock:
            return (_TIME, clock.group(1))
    return NO_ORDINAL


def proves_older(block: tuple[int, str], sibling: tuple[int, str]) -> bool:
    """Does `sibling` PROVE that a block ordinalled `block` is not the newest of its date?

    Stated as proof, not as ordering, because that is the invariant this restores: the
    archiver moves a block only when something can show it is old. Ordering always produces
    an answer; proof is allowed to say no.

    - A sibling with no ordinal proves nothing about anyone.
    - A block with no ordinal is older than any sibling that has one — the house shape
      (the first entry of a date carries no letter until the day needs a second).
    - Two different conventions are not one scale: `20:25` is not "greater than" `(f)`, so
      a mixed date group falls back to withholding rather than to a confident coin toss.
    """
    if sibling == NO_ORDINAL:
        return False
    if block == NO_ORDINAL:
        return True
    if block[0] != sibling[0]:
        return False
    return sibling[1] > block[1]


def split_blocks(lines: list[str], rule: Rule, name: str = "") -> list[dict]:
    """Cut the file into blocks. Everything before the first `start` is the preamble and
    is returned as a non-archivable block, so reassembly is just a concatenation.

    Archivability is settled once a block is complete, not when it opens: a shape like
    `## Active Issues (<date>)` is history or live depending on what is inside it, which
    is unknowable from the opening line alone.

    `name` is the file being cut, and it decides whether the ledger invariant applies. It
    falls back to the name the `RULES` dict already holds, so a caller that has only the
    rule is guarded too — the guard must not be something a call site can forget to ask for.
    """
    blocks: list[dict] = []
    current = {"lines": [], "kind": None, "index": 0}
    for line in lines:
        open_kind = current["kind"]
        if open_kind is not None and open_kind.absorbs and open_kind.absorbs.match(line):
            # A container shape claiming its own children before dispatch can steal them.
            current["lines"].append(line)
            continue
        kind = rule.kind_for(line)
        if kind is not None:
            blocks.append(current)
            current = {"lines": [line], "kind": kind, "index": len(blocks)}
            continue
        if open_kind and open_kind.ends_on_blank and not line.strip():
            # A paragraph shape, ended by the blank line that closes it — and the blank
            # line belongs to this block, so consecutive archived paragraphs stay separated
            # in the archive and the hot file gains no orphan blanks where one was removed.
            current["lines"].append(line)
            blocks.append(current)
            current = {"lines": [], "kind": None, "index": len(blocks)}
            continue
        stop = open_kind.stop if open_kind else None
        if stop and line.strip() and stop.match(line):
            blocks.append(current)                     # this shape ended; live state resumes
            current = {"lines": [line], "kind": None, "index": len(blocks)}
            continue
        current["lines"].append(line)
    blocks.append(current)

    out = [b for b in blocks if b["lines"]]
    name = name or getattr(rule, "name", "")
    # Rejoin records the shapes above split apart, BEFORE archivability is settled — a merged
    # block must be judged as the one record it is, not as the two fragments it was cut into.
    out = _merge_superseded(name, lines, out)
    for block in out:
        kind = block["kind"]
        # What the RULE says, kept separately from the final answer. The gap between the
        # two is the only interesting signal the invariant produces: a block its own shape
        # called history, refused because it holds live state, is rule drift — the
        # PH16-T15 failure — and must be shouted about. A block the rule already called
        # live is just the working set sitting where it belongs, and saying so every run
        # would bury the real signal in noise.
        block["shape_archivable"] = bool(kind) and kind.is_archivable(block["lines"])
        block["date"] = block_date(block["lines"][0]) if kind else None
        block["ordinal"] = block_ordinal(block["lines"][0]) if kind else NO_ORDINAL
        # The invariant, applied last so it overrides every rule that came before it.
        block["protected"] = protected_lines(name, block["lines"])
        block["overridden"] = bool(block["protected"] and block["shape_archivable"])
        block["archivable"] = block["shape_archivable"] and not block["protected"]
    return out


def select(blocks: list[dict], rule: Rule, keep: int,
           keep_days: int) -> tuple[list[dict], list[dict]]:
    """The blocks to move, and the blocks WITHHELD because nothing proves they are old.

    Archivable, dated, beyond the newest `keep`, and — when `keep_days` is set — older than
    that floor. Undated blocks are never selected.

    **Ranking is by (date, sub-day ordinal, position), and position is the last resort
    (PH16-T30).** It used to be the only tie-break, which encoded "the newest entry is at
    the top of the file" as if it were a fact about the format. It is not: the same entry
    written at the bottom — the shape "append a dated entry" naturally produces — ranked as
    the *oldest* of its date and was the first thing archived.

    Ties the ordinals cannot settle are **withheld, not guessed**: a candidate sharing the
    file's newest date with a sibling that no ordinal orders against it stays put and is
    reported.

    **The guard's boundary is the tie, not the date** — deliberately, and it is the
    difference between fixing this bug and freezing the tool. Where dates differ they
    already order the blocks correctly whichever end the file is written from, so the
    ranking is sound and an explicit `--keep 0` is honoured exactly as it always was,
    including for the newest block. The doubt this guard exists for lives entirely inside a
    tied date, which is where `-index` used to be the whole ranking. Within a tied group
    whose ordinals *do* order it, every member but the newest still moves, so a workspace
    writing several entries a date can still archive — the failure mode `RULES`'s own
    comments condemn about the old `keep` policy.
    """
    dated = [b for b in blocks if b["archivable"] and b["date"]]
    ranked = sorted(dated, key=lambda b: (b["date"], b["ordinal"], -b["index"]), reverse=True)
    candidates = ranked[keep:]
    if keep_days:
        floor = datetime.now(timezone.utc).date() - timedelta(days=keep_days)
        candidates = [b for b in candidates if b["date"] < floor]

    newest_date = max((b["date"] for b in dated), default=None)
    tied = [b for b in dated if b["date"] == newest_date]
    moving, withheld = [], []
    for block in candidates:
        unproven = (block["date"] == newest_date and len(tied) > 1 and not any(
            proves_older(block["ordinal"], other["ordinal"]) for other in tied))
        (withheld if unproven else moving).append(block)
    by_position = lambda group: sorted(group, key=lambda b: b["index"])  # noqa: E731
    return by_position(moving), by_position(withheld)


def ordinal_collisions(root: Path | None = None) -> list[dict]:
    """Two dated blocks in one hot memory file that `proves_older` cannot tell apart (PH27-T09).

    `select()` only asks this question about the file's single *newest* date, because
    that is the only tie its own ranking needs to break right now. This asks it of
    **every** date in **every** ruled file — a collision sitting anywhere else today is
    the same defect arriving tomorrow, once enough newer entries land on top of it and
    it becomes the tied group `select()` has to rank.

    Found live, not hypothesised: `task_transition.done()` called twice in one unclosed
    session (2026-08-18) wrote two `## 2026-08-18 — …` headings into `progress.md` with
    no ordinal between them — both `NO_ORDINAL`, `proves_older` withholds order for
    either direction, and which one `select()` would move depended on which end of the
    file it started from (PH27-T08, `tests/test_archive_memory.py
    ::TheArchiverNeverMovesABlockItCannotProveIsOld::
    test_the_real_memory_bank_plans_the_same_from_either_end`). Until PH27-T08 that test
    lived only inside the ~146s full suite; this is the same fact, reachable in well
    under a second.

    **Defines no rule of its own** — `split_blocks()` already computes `date` and
    `ordinal` per block via `block_date()`/`block_ordinal()`, and the candidate filter
    below (`archivable and date`) is copied from `select()`'s own `dated = [...]` line
    so the two functions agree about which blocks are even eligible to collide.
    """
    root = root or ws_root()
    out: list[dict] = []
    for name, rule in RULES.items():
        path = root / MEMORY_DIRNAME / name
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        blocks = split_blocks(lines, rule, name)
        dated = [b for b in blocks if b["archivable"] and b["date"]]
        first_seen: dict[tuple, dict] = {}
        for block in dated:
            key = (block["date"], block["ordinal"])
            prior = first_seen.get(key)
            if prior is None:
                first_seen[key] = block
                continue
            out.append({
                "file": name,
                "date": block["date"].isoformat(),
                "ordinal": block["ordinal"][1] or "NO_ORDINAL",
                "first_line": prior["lines"][0].strip(),
                "second_line": block["lines"][0].strip(),
                "detail": (
                    f"{name}: two blocks dated {block['date'].isoformat()} both carry "
                    f"ordinal {block['ordinal'][1] or 'NO_ORDINAL'} — "
                    f"{prior['lines'][0].strip()[:60]!r} and "
                    f"{block['lines'][0].strip()[:60]!r}. proves_older cannot order "
                    f"them; select() may move either once this becomes the tied group."
                ),
            })
    return out


def _sizes(blocks: list[dict]) -> tuple[int, int]:
    """`(lines, chars)` of a block list — the one place either is counted for a plan.

    `plan_file` and `reachable_states` must measure a file the same way, or the second
    prescribes a `--keep` the first then reports as still over budget. That is the PH24-T14
    defect in miniature, so the arithmetic is shared rather than restated.
    """
    return (sum(len(b["lines"]) for b in blocks),
            sum(len(ln) + 1 for b in blocks for ln in b["lines"]))


def _bulk_entry(kept: list[dict], allowance: int) -> dict | None:
    """The closed entry furthest over its own share, or None if none is over it.

    *Closed* deliberately: `entry_budget` measures per-entry size over archivable blocks
    only, and this must name the same thing `doctor` names or the two disagree about which
    entry is "the big one". Live state has no per-entry share to be over — a file bloated by
    240 open issues has no single entry to blame, and inventing one would be the same
    overreach as prescribing an impossible `--keep`, pointed the other way. Returning None
    is therefore a real answer, and the caller says "what remains is live state" on it.

    **Both measures, ranked by overage rather than by size**, for the reason `MAX_CHARS`
    exists: `knownIssues.md` writes one unwrapped line per entry, so its worst entry is
    barely a line long and thousands of chars wide. Picking the *line*-largest there would
    name the wrong entry while the file was over budget on chars alone. The char share is
    derived from the line share via `CHARS_PER_LINE`, so `--max-lines` still moves both.
    """
    char_allowance = allowance * CHARS_PER_LINE
    over = []
    for block in kept:
        if not block["archivable"]:
            continue
        lines, chars = _sizes([block])
        ratio = max(lines / allowance, chars / char_allowance)
        if ratio > 1:
            over.append({"head": block["lines"][0].strip(), "lines": lines,
                         "chars": chars, "share_lines": allowance,
                         "share_chars": char_allowance})
    if not over:
        return None
    return max(over, key=lambda e: max(e["lines"] / allowance,
                                       e["chars"] / char_allowance))


def reachable_states(blocks: list[dict], rule: Rule, keep: int,
                     keep_days: int) -> list[dict]:
    """What each LOWER `--keep` would actually leave behind, largest keep first.

    **The arithmetic `unfixable()` used to skip (PH24-T14).** Its remedy line was computed
    from `blocks_total` and `keep` alone — a pure count — so it advised `try --keep 2` on a
    345-line `progress.md` where `--keep 2` sheds 47 of the 145 lines needed, and where no
    `--keep` could have cleared the file at all: the bulk was the *newest* entry and `--keep`
    moves the oldest first. Three consecutive sessions read that advice and followed it.

    Nothing is measured here that `select()` did not already know how to compute; the only
    new thing is asking it. Largest keep first, so the first state that clears the budget is
    the *smallest* change that works — advice should not archive more history than it must.

    `keep` values above the caller's are deliberately not simulated: this is consulted only
    when the current `keep` moved nothing, and a higher one moves less than nothing.
    """
    states = []
    for lower in range(keep - 1, -1, -1):
        moving, _withheld = select(blocks, rule, lower, keep_days)
        moving_ids = {b["index"] for b in moving}
        lines, chars = _sizes([b for b in blocks if b["index"] not in moving_ids])
        states.append({"keep": lower, "moving": len(moving),
                       "lines": lines, "chars": chars})
    return states


def plan_file(path: Path, rule: Rule, keep: int, keep_days: int) -> dict:
    lines = path.read_text().splitlines()
    # The real filename wins over the rule's own — `plan_file` is the one caller that
    # knows for certain which file it is reading.
    blocks = split_blocks(lines, rule, path.name)
    moving, withheld = select(blocks, rule, keep, keep_days)
    moving_ids = {b["index"] for b in moving}
    kept = [b for b in blocks if b["index"] not in moving_ids]
    lines_after, chars_after = _sizes(kept)
    return {
        "file": path.name,
        "path": path,
        "lines_before": len(lines),
        "lines_after": lines_after,
        # The unit the context actually charges (PH16-T19). Counted off the same block lists
        # the line figures come from, so the two can never disagree about what is moving.
        "chars_before": sum(len(ln) + 1 for ln in lines),
        "chars_after": chars_after,
        # What a lower `--keep` could reach, and the biggest entry that would still be here
        # (PH24-T14). Carried on the plan rather than recomputed by `unfixable()`, because
        # this is the only place that holds the blocks, the rule and the age floor at once —
        # a remedy computed from anything less is how the impossible advice happened.
        "reachable": reachable_states(blocks, rule, keep, keep_days),
        "entry_allowance": entry_allowance(rule),
        "bulk": _bulk_entry(kept, entry_allowance(rule)),
        "blocks_total": sum(1 for b in blocks if b["archivable"]),
        "moving": moving,
        "kept": kept,
        "undated": [b for b in blocks if b["archivable"] and not b["date"]],
        # Blocks whose own shape called them history and which the invariant refused
        # anyway. Reported, never silent — this list is non-empty only when a `RULES`
        # entry has drifted past the live section, which is the PH16-T15 failure itself.
        "protected": [b for b in blocks if b["overridden"]],
        # Blocks `keep` selected and the newest-entry guard refused (PH16-T30). Same
        # discipline: a refusal the operator is not told about is how this tool reported
        # a clean success while archiving the entry written minutes earlier.
        "withheld": withheld,
    }


def _pointer_line(archive_rel: str) -> str:
    return POINTER.format(link=archive_rel)


def _writable(lines: list[str]) -> str:
    """Render lines as text no pre-commit fixer would want to change (PH16-T11).

    Both halves of a move are written through here — the hot file and the archive — because
    the two used to be normalized differently and drifted: the kept half was `rstrip("\\n")`ed
    and the archived half was appended verbatim. A moved line carrying trailing whitespace
    therefore landed in a *tracked* archive file unchanged, and the next `verify-safe` ran
    pre-commit's `trim trailing whitespace` fixer over it. **pre-commit reports any hook that
    modifies a tracked file as `Failed` regardless of exit code**, so the gate closed with
    `Evidence recorded FAILURE` under a green hook list and 1519 passing tests — and self-healed
    on the re-run, which is what made it read as a flake for as long as it did.

    Whitespace is all that changes. Nothing is reworded, reordered or dropped: the archive's
    stated promise is that it holds the original text, so normalization must not become editing.
    """
    return "\n".join(line.rstrip() for line in lines).rstrip("\n") + "\n"


def apply_plan(plan: dict, root: Path) -> None:
    """Move the selected blocks. Order is preserved on both sides; nothing is rewritten
    beyond appending a pointer, so a diff shows moves and one added line."""
    archive_dir = root / ARCHIVE_DIRNAME
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archive_dir / plan["file"]
    archive_rel = str(ARCHIVE_DIRNAME / plan["file"])

    if not archive_path.exists():
        archive_path.write_text(
            f"# Archived — {plan['file']}\n\n"
            f"> Moved out of `{MEMORY_DIRNAME}/{plan['file']}` by `just archive-memory` to keep the\n"
            f"> hot file inside the ~{MAX_LINES}-line context budget (AGENTS.md § CONTEXT BUDGET).\n"
            f"> Nothing here was edited or deleted — it is the original text, oldest first.\n"
            f"> Open it only when you need history; the hot file holds everything live.\n")

    stamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
    chunk = [f"\n<!-- archived {stamp} -->"]
    for block in plan["moving"]:
        chunk.extend(block["lines"])
    with archive_path.open("a") as fh:
        fh.write(_writable(chunk))

    kept_lines: list[str] = []
    for block in plan["kept"]:
        kept_lines.extend(block["lines"])
    pointer = _pointer_line(archive_rel)
    if not any(archive_rel in line for line in kept_lines):
        kept_lines.extend(["", "---", pointer])
    plan["path"].write_text(_writable(kept_lines))


def unfixable(plan: dict, keep: int, max_lines: int) -> dict | None:
    """An over-budget file this run moved nothing from. Returns the finding, or None.

    Rule 4 of this module says it reports what it did NOT fix, and until PH15-T10 that
    was only true on the has-a-plan path: a file whose closed history all sat inside the
    `keep` window produced no plan at all, dropped out of the report, and the empty-plans
    branch then announced budget compliance it had never measured.

    Scope is deliberately the *no-plan* case only. A file that moved something and is
    still over budget is already reported on the plan path (and carries
    `still_over_budget` in `--json`); reporting it twice would double-count it.
    """
    # Both measures, via the shared rule. A lines-only guard here silently
    # dropped a file that was over on chars alone — reopening from the other side
    # the exact hole this function's docstring says PH15-T10 closed.
    if plan["moving"] or not over_budget_counts(
            plan["lines_after"], plan["chars_after"], max_lines):
        return None
    closed = plan["blocks_total"]
    # Every lower `--keep`, measured — largest first, so `clears` is the smallest change
    # that works. `.get`, because a hand-built plan (the `unfixable()` unit tests pass one)
    # legitimately carries no reachability map; with none, no `--keep` is prescribed at all,
    # which is the safe direction and the only one this function is allowed to guess in.
    reachable = plan.get("reachable") or []
    clears = next((state for state in reachable if not over_budget_counts(
        state["lines"], state["chars"], max_lines)), None)
    floor = reachable[-1] if reachable else None
    # An entry is only "the bulk" against the share its own file's keep policy implies —
    # `entry_allowance`, the same figure `doctor` prints; `_bulk_entry` has already applied
    # it, so None here means "no single entry is over its share" rather than "not checked".
    # Measured whichever branch runs, because it is a measurement; only the no-remedy branch
    # below *names* it, because that is the branch where shortening it is the next action.
    bulk = plan.get("bulk")
    if not closed:
        reason = ("no closed history to move — the live state itself has outgrown the file; "
                  "it needs editing, not archiving")
    elif clears:
        # `--keep 0` is a real setting (archive every closed block), so the suggestion may
        # legitimately be 0 — which is why `--keep` must distinguish "0" from "unset".
        reason = (f"{closed} closed block(s), all inside the newest {keep} that --keep "
                  f"holds back — try `--keep {clears['keep']}`: it moves {clears['moving']} "
                  f"and leaves {clears['lines']} lines / {clears['chars']:,} chars")
    else:
        # The PH24-T14 branch. It used to be unreachable — the count-based suggestion above
        # ran unconditionally, so a file no `--keep` could fix was told to change `--keep`.
        # The wording follows its two siblings: the no-closed-history branch above and the
        # `withheld` note below both already say that the knob is not the answer here.
        reason = (f"{closed} closed block(s), all inside the newest {keep} that --keep holds "
                  f"back — but no `--keep` clears this file")
        if floor:
            reason += (f": moving everything the archiver can see still leaves "
                       f"{floor['lines']} lines / {floor['chars']:,} chars")
        else:
            reason += " and `--keep` is already at 0, its floor — there is nothing lower"
        if bulk:
            # Name the measure the entry is actually over, for the same reason `render()`
            # names the budget the file is actually over: a true finding wearing a false
            # reason is how a tool loses the operator's trust.
            size = (f"{bulk['lines']} lines against a {bulk['share_lines']}-line share"
                    if bulk["lines"] > bulk["share_lines"] else
                    f"{bulk['chars']:,} chars against a {bulk['share_chars']:,}-char share")
            reason += (f". The bulk is one entry — {bulk['head'][:70]} at {size} — so it "
                       f"needs writing shorter, not archiving")
        else:
            reason += ". What remains is live state, so it needs editing, not archiving"
    if plan["protected"]:
        reason += (f"; {len(plan['protected'])} block(s) held back by the ledger invariant — "
                   f"they carry live task declarations")
    if plan["withheld"]:
        # Named for the same reason as the line above it: an over-budget file reported as
        # inexplicably stuck invites the operator to lower `--keep` at a guard that `--keep`
        # cannot move. The remedy here is to write the next entry, not to change a flag.
        reason += (f"; {len(plan['withheld'])} block(s) withheld — they share the file's "
                   f"newest date and nothing in the file orders them against it")
    return {"file": plan["file"], "lines": plan["lines_after"],
            "chars": plan["chars_after"],
            "closed_blocks": closed, "movable": 0, "reason": reason,
            # The numbers the reason was computed from, so `--json` can be checked rather
            # than believed (PH24-T14). `clearing_keep` is None exactly when no `--keep`
            # would clear the file — the state the old reason could not represent.
            "clearing_keep": clears["keep"] if clears else None,
            "floor_lines": floor["lines"] if floor else plan["lines_after"],
            "floor_chars": floor["chars"] if floor else plan["chars_after"],
            "bulk_entry": bulk,
            "protected_blocks": len(plan["protected"]),
            "withheld_blocks": len(plan["withheld"])}


def _render_protected(plan: dict) -> None:
    """Say what the ledger invariant held back, and why. Rule 4 of this module."""
    if not plan["protected"]:
        return
    lines = sum(len(b["protected"]) for b in plan["protected"])
    print(f"    🔒 {len(plan['protected'])} block(s) held back — a history shape matched them, "
          f"but they carry {lines} live task declaration(s), which `task_ledger` reads as state:")
    for block in plan["protected"]:
        print(f"      · {block['lines'][0].strip()[:80]}")
    print("       This means that shape's rule has drifted past the live section — the "
          "PH16-T15 failure.\n       Fix `RULES`; do not lower --keep to work around it.")


def _render_withheld(plan: dict) -> None:
    """Say which blocks the newest-entry guard withheld, and why (PH16-T30).

    The bug this closes did not print a warning — it printed *success*. So the refusal is
    surfaced through the same channel as the ledger invariant's, and worded as what it is:
    not a fault to fix, just the newest entry of the file staying where every next session
    reads it. It stops being withheld the moment a newer one is written.
    """
    if not plan["withheld"]:
        return
    print(f"    🛡️  {len(plan['withheld'])} block(s) withheld — they share the file's newest "
          f"date and no sibling ordinal orders them, so one of them may BE the newest entry:")
    for block in plan["withheld"]:
        print(f"      · {block['lines'][0].strip()[:80]}")
    print("       An entry is ranked by its own sub-day ordinal — `(g)`, `20:25` — never by "
          "where it sits\n       in the file. These will archive normally once a newer entry "
          "exists above or below them.")


def render(plans: list[dict], applied: bool, max_lines: int, considered: int,
           stuck: list[dict] | None = None, max_chars: int | None = None,
           withheld: list[dict] | None = None) -> None:
    # Named `stuck`, not `over_budget`: it holds `unfixable()` findings, and the
    # old name shadowed the module-level `over_budget()` predicate.
    stuck = stuck or []
    # Derived from the line budget the caller passed, so `--max-lines N` still scales both
    # halves together and a caller that predates the char budget keeps working.
    max_chars = max_chars if max_chars is not None else max_lines * CHARS_PER_LINE
    print("\n" + "═" * 62)
    print("  🗄️  ARCHIVE MEMORY — " + ("moved" if applied else "DRY RUN — nothing written"))
    print("═" * 62)
    if not plans:
        if not stuck:
            print(f"\n  ✅ Nothing to archive ({considered} file(s) checked).")
            print(f"     Every hot memory file is within the {max_lines}-line budget.")
            # A run that refused something must not read as a run that found nothing to do
            # (PH16-T30). The budget claim above stays exactly as true as it was; this adds
            # the part the operator would otherwise have to read `--json` to discover.
            for item in withheld or []:
                print(f"     🛡️  {item['file']}: withheld {item['block'][:64]}… — it shares "
                      f"the file's newest date and nothing orders it.")
            print()
            return
        # Name only the budget(s) some listed file actually breaks. Saying
        # "over the 200-line budget" about a 63-line file that is over on chars
        # is a true finding wearing a false reason.
        breaks_lines = any(f["lines"] > max_lines for f in stuck)
        breaks_chars = any(f.get("chars", 0) > max_chars for f in stuck)
        named = " and the ".join(
            ([f"{max_lines}-line budget"] if breaks_lines else [])
            + ([f"{max_chars:,}-char budget"] if breaks_chars else [])
        ) or "context budget"
        print(f"\n  ⚠️  Nothing could be archived, and {len(stuck)} file(s) are still "
              f"over the {named}:")
        for finding in stuck:
            print(f"      · {finding['file']}  {finding['lines']} lines — {finding['reason']}")
        print("\n     This tool only moves *closed history*. It will never touch live state,\n"
              "     so a file bloated by live content cannot be fixed here.\n")
        return

    for plan in plans:
        verb = "moved" if applied else "would move"
        print(f"\n  {plan['file']}  {plan['chars_before']:,} → {plan['chars_after']:,} chars "
              f"(≈{plan['chars_before'] // 4000}k → ≈{plan['chars_after'] // 4000}k tokens) · "
              f"{plan['lines_before']} → {plan['lines_after']} lines")
        print(f"    {verb} {len(plan['moving'])} of {plan['blocks_total']} closed block(s) "
              f"→ {ARCHIVE_DIRNAME}/{plan['file']}")
        for block in plan["moving"]:
            head = block["lines"][0].strip()
            print(f"      · {head[:88]}" + ("…" if len(head) > 88 else ""))
        if plan["undated"]:
            print(f"    ℹ️  {len(plan['undated'])} closed block(s) left in place — no date to "
                  f"rank them by, so they are never moved automatically.")
        _render_protected(plan)
        _render_withheld(plan)
        # Charged in chars (PH16-T19). "Lower --keep" is deliberately no longer suggested: at
        # this point everything the archiver can see has been offered, so `--keep` only trades
        # recent history away for a number, and the residue is live state by definition. Saying
        # so plainly is the difference between a check that can be satisfied honestly and one
        # that nudges toward resolving issues that are not fixed.
        # Name the budget(s) actually exceeded. This used to say "over the
        # {max_chars}-char budget" unconditionally, which was invisibly wrong while
        # only line-over files were ever selected, and plainly wrong the moment they
        # weren't: `progress.md` at 16,197 chars / 260 lines was reported as over a
        # 30,000-char budget it was comfortably inside. A correct verdict with a
        # false reason is how a tool loses the operator's trust.
        exceeded = []
        if plan["chars_after"] > max_chars:
            exceeded.append(f"{max_chars:,}-char budget")
        if plan["lines_after"] > max_lines:
            exceeded.append(f"{max_lines}-line budget")
        if exceeded:
            print(f"    ⚠️  still {plan['chars_after']:,} chars "
                  f"(≈{plan['chars_after'] // 4000}k tokens) / {plan['lines_after']} lines, "
                  f"over the {' and the '.join(exceeded)} — what remains is LIVE state, so "
                  f"this needs editing, not archiving.")

    if not applied:
        print("\n  → Re-run with --apply to move them. Nothing is deleted; the hot file")
        print("    gets a [[link]] to the archive.\n")
    else:
        print("\n  ✅ Done. Run `just doctor` to confirm the budget, then `just freshen-memory`.\n")


def sweep(root: Path, *, apply: bool = False, all_files: bool = False,
          keep: int | None = None, keep_days: int = 0,
          max_lines: int = MAX_LINES) -> dict:
    """Plan every known hot file, optionally move, and report — for a NAMED root.

    Extracted from `main()` by PH25-T03 so something other than a CLI can run this.
    `main()` resolved its own root from `ws_root()` and took no argument, so
    `just prep-close` could only reach this module by shelling out — and would then
    have had to re-parse `render()`'s human-facing output to tell *"nothing was
    archivable"* from *"nothing was recognised"*, which is exactly the distinction
    the closure step exists to preserve. One implementation, two callers.

    `keep=None` means "use each rule's own default", and is NOT the same as
    `keep=0` ("archive every closed block") — the distinction `--keep` has always
    had to make, restated here because the parameter now has a second caller.

    Returns the `--json` shape, plus `raw`: the unflattened plans, because
    `render()` reports per-file protected and withheld blocks that the flattened
    form has already lost.
    """
    plans, stuck, protected, withheld, considered = [], [], [], [], 0
    for name, rule in RULES.items():
        path = root / MEMORY_DIRNAME / name
        if not path.is_file():
            continue
        considered += 1
        # Scope must match `entry_budget`'s, or `doctor` names a remedy that
        # cannot reach the file it names — see `over_budget()`.
        if not all_files and not over_budget(path.read_text(), max_lines):
            continue
        keep_n = rule.keep if keep is None else keep
        plan = plan_file(path, rule, keep_n, keep_days)
        protected.extend({"file": plan["file"], "block": b["lines"][0].strip(),
                          "declarations": len(b["protected"])} for b in plan["protected"])
        withheld.extend({"file": plan["file"], "block": b["lines"][0].strip(),
                         "reason": "shares the file's newest date and no sibling ordinal "
                                   "orders it"} for b in plan["withheld"])
        if plan["moving"]:
            plans.append(plan)
        # Measured whether or not anything moved — a file this run cannot fix is a
        # finding, not a silence. Before PH15-T10 it simply vanished from the report.
        finding = unfixable(plan, keep_n, max_lines)
        if finding and not plan["moving"]:
            stuck.append(finding)

    if apply:
        for plan in plans:
            apply_plan(plan, root)

    return {
        "applied": apply,
        "considered": considered,
        "plans": [{
            "file": p["file"],
            "lines_before": p["lines_before"],
            "lines_after": p["lines_after"],
            "moving": [b["lines"][0].strip() for b in p["moving"]],
            "undated": len(p["undated"]),
            "still_over_budget": p["lines_after"] > max_lines,
            "protected": [b["lines"][0].strip() for b in p["protected"]],
            "withheld": [b["lines"][0].strip() for b in p["withheld"]],
        } for p in plans],
        "over_budget": stuck,
        # Every block the ledger invariant refused, across every file considered — at
        # the top level as well as per-plan, because the refusal can be the reason a
        # file produced no plan at all.
        "protected": protected,
        # Every block the newest-entry guard refused, across every file considered —
        # at the top level as well as per-plan, because a file whose whole selection was
        # withheld produces no plan at all and would otherwise vanish from the report.
        "withheld": withheld,
        "raw": plans,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Move closed history out of the hot memory files (dry run by default).")
    parser.add_argument("--apply", action="store_true", help="actually move (default: dry run)")
    parser.add_argument("--all", action="store_true",
                        help="consider every known file, not only oversized ones")
    # `default=None`, not 0: `--keep 0` means "archive every closed block" and must be
    # distinguishable from "not passed". Under the old `args.keep or rule.keep`, an
    # explicit 0 was falsy and silently fell back to the rule's default — so the one
    # setting that archives everything was the one setting that did nothing.
    parser.add_argument("--keep", type=int, default=None, metavar="N",
                        help="override how many recent blocks stay in each hot file (0 = all)")
    parser.add_argument("--keep-days", type=int, default=0, metavar="N",
                        help="never archive a block newer than N days (default: no age floor)")
    parser.add_argument("--max-lines", type=int, default=MAX_LINES, metavar="N",
                        help=f"the context budget a hot file must fit in (default {MAX_LINES})")
    parser.add_argument("--json", action="store_true", help="machine-readable plan")
    args = parser.parse_args()

    result = sweep(ws_root(), apply=args.apply, all_files=args.all, keep=args.keep,
                   keep_days=args.keep_days, max_lines=args.max_lines)

    if args.json:
        print(json.dumps({k: v for k, v in result.items() if k != "raw"},
                         indent=2, ensure_ascii=False))
        return 0

    render(result["raw"], args.apply, args.max_lines, result["considered"],
           result["over_budget"], withheld=result["withheld"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
