#!/usr/bin/env python3
"""
task_ledger.py — read a task's declaration and its Definition of Done.

Audit finding **F-05: budget + DoD are self-reported.** `just work-done "PH5-T01"`
accepted *any string* — a typo, a task that does not exist, a task nobody worked
on — and incremented the session counter regardless. The DoD convention that
`.ai/docs/tasks.md` calls "the concrete acceptance test that must pass before it
counts as done" was prose nothing ever read.

This module is the read half of the fix: given a task id, find where it is
declared and what its DoD says. `session_budget.py` is the enforcement half.

## Where a task can be declared
Two live files, in priority order — the first that declares the task wins:
  1. `.ai/memory-bank/activeContext.md` — the current-phase operational slice
  2. `.ai/docs/tasks.md`                — the persistent master ledger
Both are searched even when the first has no DoD, so a task listed tersely in
activeContext.md can still inherit the DoD spelled out in tasks.md.

A third, lowest-priority source: `.ai/docs/archive/tasks-PHASE-*.md`, the closed
task blocks `just archive-tasks` moved out of `tasks.md` (PH27-T01). A task is
only ever live in one place, so there is nothing to merge across live vs.
archived — the archive is consulted only once the live ledgers have nothing,
which keeps `goal_progress.py`/`conformance.py` (both call `all_tasks()`) seeing
every task's DoD regardless of whether it has archived away.

## The two DoD shapes actually in use
Both files were hand-written before anything parsed them, so both shapes exist
and both must work — reformatting the ledgers to suit the parser would be the
tail wagging the dog:

    - PH6-T21: Activate the guard fleet-wide. (Pending)
      - DoD: `fleet-status` reports the guard active per workspace.   ← sub-bullet

    - [x] PH5-T01 Real policy_engine.py + policy_check.py preflight
          DoD: verdicts correct for block/approval/autonomous. ✅       ← indented continuation

A DoD may also sit inline on the task line itself. Emphasis markers (`**DoD:**`,
`*DoD:*`) and case variations are tolerated; the leading `- `/`* ` bullet, the
`[x]` checkbox and any trailing `✅` are stripped from the captured text.

## Which goal does this task serve? (PH9-T08)

A task may also declare `Goal: G4`, in exactly the same two positions and with
exactly the same block scoping — so a goal declaration cannot leak from one task
to the next any more than a DoD can. `goal_progress.py` joins these to the goals
in `.ai/plan.md`; a task that declares none is reported *unmapped*, which is a
real answer, not a gap to be filled by guessing.

`all_tasks()` enumerates the ledger, because a completion figure needs a
denominator and this module could previously only answer questions about a task
whose id you already knew.

## What ends a task's block
Any subsequent line at the *same or lower* indentation that declares a different
`PH#-T##`, or a Markdown heading. Continuation lines that are blank or more
deeply indented belong to the task. This is what stops PH6-T21's DoD from being
read as PH6-T03's when they sit adjacent in a list.

Usage:
  task_ledger.py PH7-T02             # human-readable
  task_ledger.py PH7-T02 --json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TASK_RE = re.compile(r"PH\d+-T\d+")
# `DoD:` possibly wrapped in markdown emphasis, at the start of the content.
# The emphasis may close before OR after the colon — `**DoD**:`, `**DoD:**` and
# `*DoD:*` all occur in the live ledgers — so both positions are optional.
DOD_MARKER = r"[*_]{0,2}DoD[*_]{0,2}\s*:\s*[*_]{0,2}\s*"
DOD_RE = re.compile(rf"^{DOD_MARKER}(.+)$", re.I)
# `Goal: G4` — the same shape and the same tolerances as `DoD:` (PH9-T08).
# `Goals:` does not match: the character after "Goal" must be emphasis or the
# colon, so the plan's own `## Goals` heading and any "Goals:" prose are inert.
GOAL_MARKER = r"[*_]{0,2}Goal[*_]{0,2}\s*:\s*[*_]{0,2}\s*"
GOAL_ID_RE = re.compile(r"^(G\d+)\b")
# `test: tests/x.py::Cls::test_name` — a sub-bullet that names a collectible
# regression test. Same field convention as `knownIssues.md` (PH7-T06).
# Indented any amount; leading `- ` optional (some entries drop the dash).
TEST_LINE_RE = re.compile(r"^\s*(?:-\s*)?test:\s*(?P<value>.+)$", re.I)
LEDGER_FILES = (
    Path(".ai") / "memory-bank" / "activeContext.md",
    Path(".ai") / "docs" / "tasks.md",
)
# Closed task blocks `just archive-tasks` moved out of tasks.md (PH27-T01).
# Lowest priority, always searched AFTER LEDGER_FILES: a task is only ever
# live in one place or the other, never both, so there is nothing to merge —
# this is purely "the live ledgers didn't have it, check history next." Same
# task-block Markdown syntax as tasks.md, so `_scan`/`all_tasks`'s existing
# per-line loop reads these files with no second parser.
ARCHIVE_DIR = Path(".ai") / "docs" / "archive"
ARCHIVE_GLOB = "tasks-PHASE-*.md"
STATUS_WORDS = "Pending|In Progress|Complete|Done|Dropped"
# `(Complete)` — and `(Complete 2026-08-01)`, which is the same declaration with the
# date the ledger actually writes. The tail is bounded and may not contain `)`, so a
# whole sentence cannot be swallowed into a status.
STATUS_RE = re.compile(rf"\(({STATUS_WORDS})\b[^)]{{0,40}}\)", re.I)
# `✅ Complete 2026-08-06` — activeContext.md's own completion sigil, and by count its
# dominant one (19 uses against 8 of the parenthesised form).
STATUS_TICK_RE = re.compile(rf"✅\s*[*_]{{0,2}}\s*({STATUS_WORDS})\b", re.I)
# `PH24-T12 — Complete (2026-08-17).` — the THIRD form (PH16-T41), and the inverse of
# form 1: the word bare, the DATE in the parens. Measured on the live activeContext.md
# when this was added, it is what the file actually writes — 8 declarations heard by the
# two rules above, 23 read as no status at all.
#
# **Head-anchored, and that is the entire safety argument.** A bare status word is far
# looser than a parenthesis or a sigil, and matched anywhere on the line it immediately
# forges state: `PH16-T02 — 5/5 slices done 2026-08-09` is a live declaration whose own
# entry says the task is NOT fully met, and an unanchored rule reads `done` and settles
# it. Because this can only match immediately after the declaration's own id, it can
# never describe a different task, so it needs no clause guard — unlike the sigil path.
HEAD_STATUS_RE = re.compile(
    rf"^(?:PH\d+-T\d+)\s*[*_]{{0,2}}\s*[—–-]\s*[*_]{{0,2}}\s*({STATUS_WORDS})\b", re.I)
# A clause boundary, for deciding whether a ✅ belongs to this task or to one the
# sentence merely names. `:` is not one — `PH9-T01: ✅ Complete` is the house form.
_CLAUSE_RE = re.compile(r"[.;]|—")
# A bare `T##` immediately after one of this repo's own multi-id separators —
# `PH25-T01 / T04`, `PH16-T22 · T23` — the shorthand that drops the repeated
# `PH#-` prefix. (PH27-T11)
_SHORT_ID_RE = re.compile(r"(?:/|,|·)\s*T\d+\b")


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _archive_files(root: Path) -> list[Path]:
    """Archived closed-task files, relative to `root`, sorted for determinism.

    Empty when `.ai/docs/archive/` doesn't exist yet (every workspace before
    PH27-T01 runs, and every workspace that has archived nothing) — an absent
    directory is a normal, un-run state, not an error.
    """
    d = root / ARCHIVE_DIR
    if not d.is_dir():
        return []
    return sorted(p.relative_to(root) for p in d.glob(ARCHIVE_GLOB))


def _search_files(root: Path) -> tuple[Path, ...]:
    """The live ledgers, then the archive — priority order for every reader."""
    return (*LEDGER_FILES, *_archive_files(root))


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _strip_bullet(line: str) -> str:
    """Drop a leading heading marker, list marker, and checkbox: '- [x] foo' → 'foo',
    '### PH3-T10 — foo' → 'PH3-T10 — foo'.

    **The heading strip looks like dead code in THIS workspace and is not.** Context's
    own `.ai/docs/tasks.md` declares every task as a `- [x] PH#-T##` bullet, so nothing
    here exercises it — but downstream workspaces (5G_Proxy) declare every task as a
    `### PH#-T##` heading. Without the strip, `content.startswith(task_id)` is false for
    every one of them and `just work-done` can credit nothing in their master ledger.
    Deleting it has broken 5G_Proxy three times (its Sessions 13, 17 and 19). The
    heading-declaration tests in `tests/test_task_ledger.py` exist to fail if it goes again.
    """
    text = line.strip()
    text = re.sub(r"^#{1,6}\s+", "", text)
    text = re.sub(r"^[-*+]\s+", "", text)
    text = re.sub(r"^\[[ x~\-]\]\s*", "", text, flags=re.I)
    # PH16-T39: leading emphasis, the same `[*_]{0,2}` tolerance DOD_MARKER and
    # GOAL_MARKER already carry — and for the same reason: the live ledgers write
    # it that way. `activeContext.md`'s house style is `- **PH22-T08 (In
    # Progress)** — title`, which bound NOTHING for three days because the
    # content started with `**` rather than with the id.
    # Backticks are deliberately NOT stripped: a line quoting the convention in a
    # code span must stay inert, which is the forgery the strictness was added
    # for. Trading one silent failure for another is not a fix.
    text = re.sub(r"^[*_]{1,2}", "", text)
    return text.strip()


def _code_spans(line: str) -> list[tuple[int, int]]:
    """Character ranges covered by `backtick code spans`."""
    return [(m.start(), m.end()) for m in re.finditer(r"`[^`]*`", line)]


def _inline_dod(line: str) -> str:
    """A `DoD:` written on the declaration line itself, or "" if there is none.

    Must ignore `DoD:` inside a code span: PH7-T02's own title is "parses the
    task's `DoD:` and refuses credit…", which is a *mention* of the marker, not a
    declaration — matching it captured the rest of the title as the criterion.
    The DoD text is then taken from the raw line so its own backticks survive.
    """
    spans = _code_spans(line)
    for m in re.finditer(DOD_MARKER, line, re.I):
        if any(start <= m.start() < end for start, end in spans):
            continue
        return _clean_dod(line[m.end():])
    return ""


def _clean_dod(text: str) -> str:
    """Trim the trailing ✅/❌ verification marks and surrounding whitespace.

    The mark records that a *human or a prior session* checked the DoD; it is not
    part of the criterion, and keeping it would let `✅` masquerade as evidence.
    """
    return re.sub(r"[\s✅❌⚠️]+$", "", text).strip()


def _checkbox(line: str) -> bool | None:
    """`- [x] …` → True · `- [ ] …` → False · no checkbox → **None**.

    Absent is deliberately not False. `.ai/docs/tasks.md` declares tasks with
    checkboxes and `activeContext.md` declares them with a `(Complete)` suffix;
    a heading-declared downstream ledger uses neither. Reporting "no checkbox" as
    unticked would invent an open task out of a ledger that never said so, which
    is the same fabrication design law 2 forbids elsewhere in this module.

    `~` and `-` are accepted as marks because `_strip_bullet` already tolerates
    them, but only `x` counts as done — a task someone marked in-progress is not
    finished, and rounding it up is how a ledger starts lying.
    """
    m = re.match(r"^[-*+]\s+\[([ x~\-])\]", line.strip(), flags=re.I)
    if not m:
        return None
    return m.group(1).lower() == "x"


def _goal_value(text: str) -> str:
    """`Goal: G4 — why` → `"G4"`. Anything that is not a goal declaration → "".

    Deliberately strict about the *value*: `Goal: soon` declares nothing, because
    a mapping to a goal that does not exist is worse than no mapping — it would
    quietly remove the task from every total instead of reporting it unmapped.
    """
    m = re.match(rf"^{GOAL_MARKER}", text, re.I)
    if not m:
        return ""
    gm = GOAL_ID_RE.match(text[m.end():].strip())
    return gm.group(1).upper() if gm else ""


def _inline_goal(line: str) -> str:
    """A `Goal:` written on the declaration line itself, or "".

    Ignores code spans for the same reason `_inline_dod` does: a task whose title
    *quotes* the convention (`declare `Goal:` on the task`) is describing the
    rule, not obeying it. That is mention-vs-declaration for the sixth time in
    this repo, and the first time it has been built in before the bug appeared.
    """
    spans = _code_spans(line)
    for m in re.finditer(GOAL_MARKER, line, re.I):
        if any(start <= m.start() < end for start, end in spans):
            continue
        gm = GOAL_ID_RE.match(line[m.end():].strip())
        if gm:
            return gm.group(1).upper()
    return ""


def _block(lines: list[str], i: int, task_id: str) -> list[int]:
    """Indices of the lines belonging to the task declared at `lines[i]`.

    One definition of "inside this task", shared by every field reader. It was
    inlined in the DoD scan before PH9-T08; a second field with a second copy of
    these boundary rules is exactly the drift `active_task()` was created to
    remove. Blank lines and more deeply indented lines belong to the task; a
    heading, a shallower non-task line, or a *different* task id at the same or
    shallower indent ends it.
    """
    base = _indent(lines[i])
    # A heading section has no indent structure of its own — a `### PH#-T##`
    # heading's `- DoD:` bullet sits at indent 0, the same as the heading. The
    # sibling check below assumes nested bullets, so applying it to a heading
    # ends the block on its very first line and every DoD in a heading-shaped
    # ledger reads as absent. The next heading of ANY level is what ends a section.
    is_heading = lines[i].lstrip().startswith("#")
    out: list[int] = []
    for j in range(i + 1, len(lines)):
        nxt = lines[j]
        if not nxt.strip():
            continue
        if nxt.lstrip().startswith("#"):
            break
        if not is_heading and _indent(nxt) <= base:
            other = TASK_RE.search(nxt)
            if other and other.group(0) != task_id:
                break
            if not other:
                break
        out.append(j)
    return out


def _extract(lines: list[str], i: int, task_id: str) -> dict:
    """Build the record for the task declared at `lines[i]`.

    Inline beats sub-bullet for both fields; the first sub-bullet in the block
    wins otherwise. Reading the whole block for `goal` even when `dod` was found
    inline costs one pass and removes the ordering dependency the early `return`
    used to impose.
    """
    line = lines[i]
    found = {
        "task": task_id,
        "line": i + 1,
        "title": _strip_bullet(line),
        "status": declared_status(line),
        "checked": _checkbox(line),
        "dod": "",
        "dod_line": 0,
        "goal": "",
        "goal_line": 0,
        "test_ref": "",   # collectible test named in the DoD block (PH15-T04)
        "complex": is_complex(line),
    }
    block = _block(lines, i, task_id)

    inline = _inline_dod(line)
    if inline:
        found["dod"], found["dod_line"] = inline, i + 1
    else:
        for j in block:
            m = DOD_RE.match(_strip_bullet(lines[j]))
            if m:
                found["dod"], found["dod_line"] = _clean_dod(m.group(1)), j + 1
                break

    goal = _inline_goal(line)
    if goal:
        found["goal"], found["goal_line"] = goal, i + 1
    else:
        for j in block:
            g = _goal_value(_strip_bullet(lines[j]))
            if g:
                found["goal"], found["goal_line"] = g, j + 1
                break

    # Extract `test: <ref>` — first match in the block wins.  A parenthesised
    # value like `(none yet)` or `(waived …)` is not a real ref; skip it.
    for j in block:
        tm = TEST_LINE_RE.match(lines[j])
        if tm:
            val = tm.group("value").strip()
            if val and not val.startswith("("):
                found["test_ref"] = val
            break

    return found


def _scan(text: str, task_id: str) -> dict | None:
    """Find `task_id`'s declaration block in one ledger file."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if task_id not in line:
            continue
        # A task is *declared* by a list item or heading, not by a passing prose
        # mention. Without this, "Last Session: shipped PH5-T03 + PH6-T01" in
        # activeContext.md would register as a declaration with no DoD. The rule
        # itself lives in `is_declaration` — this used to carry a second copy of
        # it, which is how it drifted out of step with `all_tasks`.
        if not (is_declaration(line, allow_heading=True)
                and _strip_bullet(line).startswith(task_id)):
            continue
        return _extract(lines, i, task_id)
    return None


def all_tasks(root: Path | None = None) -> list[dict]:
    """Every task declared in the ledger files, in declaration order.

    The denominator of every completion figure. Until PH9-T08 this module could
    only answer questions about a task whose id you already knew, so "how many
    tasks serve G4?" had no implementation and the number had to be typed.

    Merge semantics match `find_task` exactly — first declaration wins, later
    files fill only the fields it left empty, `[complex]` is OR'd — because two
    functions disagreeing about what a task *is* would surface as a completion
    figure that contradicts the credit check that gates it.
    """
    root = root or ws_root()
    by_id: dict[str, dict] = {}
    order: list[str] = []
    for rel in _search_files(root):
        path = root / rel
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        seen_here: set[str] = set()
        for i, line in enumerate(lines):
            if not is_declaration(line, allow_heading=True):
                continue
            m = TASK_RE.match(_strip_bullet(line))
            if not m or m.group(0) in seen_here:
                continue
            tid = m.group(0)
            seen_here.add(tid)
            hit = _extract(lines, i, tid)
            hit["source"] = str(rel)
            if tid not in by_id:
                by_id[tid] = hit
                order.append(tid)
                continue
            cur = by_id[tid]
            for field, line_field in (("dod", "dod_line"), ("goal", "goal_line")):
                if not cur[field] and hit[field]:
                    cur[field], cur[line_field] = hit[field], hit[line_field]
            if not cur["test_ref"] and hit["test_ref"]:
                cur["test_ref"] = hit["test_ref"]
            if not cur["status"] and hit["status"]:
                cur["status"] = hit["status"]
            if cur["checked"] is None and hit["checked"] is not None:
                cur["checked"] = hit["checked"]
            cur["complex"] = cur["complex"] or hit["complex"]
    return [by_id[tid] for tid in order]


def declarations_in(path: Path) -> dict:
    """`{task_id: record}` for ONE ledger file — the same scan, unmerged.

    `all_tasks()` merges the files with first-declaration-wins, which is exactly
    what hides a disagreement between them: a task whose prose says Complete and
    whose checkbox says open resolves to one blended record with no contradiction
    left in it. `disagreements()` needs to see each file's own answer, so the scan
    is shared and the merge is not.
    """
    out: dict[str, dict] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for i, line in enumerate(lines):
        if not is_declaration(line, allow_heading=True):
            continue
        m = TASK_RE.match(_strip_bullet(line))
        if not m or m.group(0) in out:
            continue
        out[m.group(0)] = _extract(lines, i, m.group(0))
    return out


# The prose statuses that assert a task is finished. `session_end`/`goal_progress`
# keep their own copy for their own question; this one answers "does the prose
# claim done", which is the only claim a checkbox can contradict.
_PROSE_DONE = {"Complete", "Done"}

PROSE_FILE = Path(".ai") / "memory-bank" / "activeContext.md"
LEDGER_FILE = Path(".ai") / "docs" / "tasks.md"


def disagreements(root: Path | None = None) -> list[dict]:
    """Tasks whose prose status and ledger checkbox contradict each other (PH16-T41).

    The defect class this workspace exists to police, found in its own memory bank.
    Session (e) shipped `PH24-T12`, wrote *"— Complete (2026-08-17)."* into
    `activeContext.md` and `AI_CHANGELOG.md`, and ended before `just work-done`;
    `tasks.md` kept `- [ ]`. Three records of one fact, two of them prose,
    disagreeing for a whole session, with nothing detecting it — and session (f)
    filed the issue without fixing its own instance, so it survived two more.

    **The asymmetry is the danger.** Prose is what a human or an AI READS at session
    start, and it said Complete. The checkbox is what the machine reads, and it said
    open. A session booting on the prose skips work the gate still refuses to credit,
    which is the direction that loses work — so both directions are reported:

      * `unticked` — prose says done, `tasks.md` says open.
      * `stale`    — `tasks.md` says done, prose still declares `(In Progress)`, so
                     `active_task()` keeps binding `evidence.json` to finished work.

    **Silence is not a contradiction, deliberately.** Nine live declarations carry no
    status this parser will ever hear — `PH16-T22 · T23 · … — all Complete` declares
    for tasks whose later ids it merely mentions, and hearing it would forge state for
    them. A check that reported those would be noise, and a check that is noise gets
    turned off. Only an explicit contradiction is reported; the checkbox remains the
    machine's authority everywhere else.

    Scope is measured, not assumed: `progress.md` yields zero declarations — its prose
    is correctly refused by `is_declaration` — so this compares `activeContext.md`
    against `tasks.md` and says so rather than implying wider coverage.
    """
    root = root or ws_root()
    prose = declarations_in(root / PROSE_FILE)
    ledger = declarations_in(root / LEDGER_FILE)
    out: list[dict] = []
    for tid, rec in prose.items():
        led = ledger.get(tid)
        if led is None or led["checked"] is None:
            continue  # the ledger makes no claim; there is nothing to contradict
        status = (rec.get("status") or "").title()
        if status in _PROSE_DONE and led["checked"] is False:
            out.append({
                "task": tid, "kind": "unticked", "prose": status,
                "prose_line": rec["line"], "ledger_line": led["line"],
                "detail": (f"{tid} is declared '{status}' in {PROSE_FILE} line {rec['line']}, "
                           f"but {LEDGER_FILE} line {led['line']} still has `- [ ]`. A session "
                           f"booting on the prose skips it; the gate still refuses to credit it."),
            })
        elif status == "In Progress" and led["checked"] is True:
            out.append({
                "task": tid, "kind": "stale", "prose": status,
                "prose_line": rec["line"], "ledger_line": led["line"],
                "detail": (f"{tid} is ticked in {LEDGER_FILE} line {led['line']} while "
                           f"{PROSE_FILE} line {rec['line']} still declares it '(In Progress)' — "
                           f"so `active_task()` keeps binding evidence.json to finished work."),
            })
    return out


COMPLEX_RE = re.compile(r"\[\s*complex\s*\]", re.I)
# The *marker zone*: what may sit between the task id and the marker on a
# declaration line — a separator (`:` `—` `-`), emphasis, and whitespace. Nothing
# else. A marker further right than this is prose, whatever it is wrapped in.
_MARKER_ZONE_RE = re.compile(r"^[\s:—–\-*_]*`?\s*\[\s*complex\s*\]", re.I)

#: A backticked `[complex]`, wherever it sits. Rule 3 pairs it with the tail test
#: below — the marker alone proves nothing, its POSITION relative to the end of
#: the line is what distinguishes a declaration from a sentence.
_BACKTICKED_COMPLEX_RE = re.compile(r"`\s*\[\s*complex\s*\]\s*`")

#: What may follow a trailing marker: whitespace, emphasis, and other backticked
#: tokens (a `(Status)`, another marker) — and nothing else. One word of prose
#: after the marker makes it a mention, which is exactly the line this must hold.
_TRAILING_RUN_RE = re.compile(r"^[\s*_]*(?:`[^`]*`[\s*_]*)*$")


def is_complex(line: str) -> bool:
    """Does this declaration carry the `[complex]` marker? (PH7-T03, PH15-T06)

    Three ways to declare it, and all three are needed, because the fleet does not
    write its ledgers the way this kernel writes its own:

    1. A **bare** marker anywhere on the declaration line.
    2. A marker in the **marker zone** — immediately after the task id, past only
       a separator and emphasis — *even inside a code span*. **This kernel's style:**
       ``- [ ] PH10-T14 `[complex]` **title**``
    3. A backticked marker in a **trailing marker run** — from the marker to end of
       line there is nothing but whitespace, emphasis, and other backticked tokens.
       **The style every other workspace writes:**
       ``## PH1-T16 — Build the thing `(Pending)` `[complex]``` (PH16-T42)

    A marker inside a `code span` anywhere else is a *mention* and does not count
    — the third time this repo has needed that distinction, after `DoD:` in
    `_inline_dod()` and `(In Progress)` in `active_task()`. The live case is
    PH7-T03's own title, "required for `[complex]` tasks", which describes the
    rule; subjecting a task to a requirement for quoting it is the same forgery
    in a new coat.

    **Rule 2 exists because rule 1 alone made the ratchet inert (PH15-T06).** The
    code-span rejection was written against prose quoting the rule, but the
    ledger's own house style declares the marker as ``PH10-T01 `[complex]` …`` —
    so 16 of the 34 tasks carrying a marker, including every `[complex]` task in
    PHASE 10, 11, 12, 13 and 14, resolved False and `just work-done`'s exit-7
    plan refusal could never fire for them. Position is what separates the two:
    prose quoting the rule never puts the quote in the slot right after the id,
    so widening by position rescues the declarations without reopening the hole
    — the mention cases stay mentions, asserted in both directions by test.

    **Rule 3 exists because rule 2 rescued only this repo (PH16-T42).** PH15-T06
    widened to the style the KERNEL writes, which is precisely how the style
    everyone else writes came to be left out. Filed by `@zenithos` and reproduced
    on contact: ``## PH1-T16 — title `(Pending)` `[complex]``` resolved False, so
    `verify_work_claim()` skipped BOTH the plan-exists refusal and the
    named-collectible-test requirement — silently — and `work-done` credited the
    task as though each had passed. A gate that is off and reports success.

    What separates rule 3 from a mention is what FOLLOWS the marker: a declaration
    ends in annotations, a mention continues into a sentence. ``` `[complex]` tasks
    generally`` is followed by prose and stays False.

    **The false-positive direction is accepted deliberately.** A line that merely
    ENDS in the quoted marker now reads as a declaration. That is the safe side of
    an asymmetry, not an oversight: a false positive demands a plan for a task that
    did not need one, which is friction its author can see and answer; a false
    negative turns the ratchet off and reports success, which is invisible by
    construction and is the bug this rule was added to end. Rule 3 still requires
    the line to begin with a task id, so ordinary prose cannot trip it.
    """
    spans = _code_spans(line)
    for m in COMPLEX_RE.finditer(line):
        if not any(start <= m.start() < end for start, end in spans):
            return True
    content = _strip_bullet(line)
    tid = TASK_RE.match(content)
    if not tid:
        return False
    rest = content[tid.end():]
    if _MARKER_ZONE_RE.match(rest):
        return True
    # Rule 3 — the trailing marker run. Everything from the marker to end of line
    # is whitespace, emphasis, or another backticked token, so the marker is a
    # trailing annotation rather than a word inside a sentence. A mention is
    # followed by prose, and prose is neither.
    for m in _BACKTICKED_COMPLEX_RE.finditer(rest):
        if _TRAILING_RUN_RE.match(rest[m.end():]):
            return True
    return False


def declared_status(line: str) -> str:
    """The status this declaration line *declares*, or "" for none. (PH15-T09)

    Two accepted forms, because the ledger writes both:

    1. **Parenthesised** — `(Pending)`, `(Complete 2026-08-01)`. The parens are the
       declaration; a bounded, `)`-free tail lets the date in without letting a
       sentence in.
    2. **Sigilled** — `✅ Complete 2026-08-06`. `activeContext.md`'s dominant form.

    Neither counts inside a `code span`, and a sigilled status is rejected when the
    same clause already names a *different* task — "blocked until PH9-T05 is ✅
    Complete" describes another task's state and must not settle this one's.

    **Why this exists.** `STATUS_RE` used to require the closing paren immediately
    after the word, and nothing read the sigil at all. So 31 tasks that had really
    shipped — every completed task in PHASE 6, 7 and 9 — declared their completion
    in a form the parser did not recognise, resolved to *no status*, and fell
    through the gap between `ledger_audit` (open tasks) and `conformance` (done
    tasks). Both tools are right to ignore a task claiming nothing; the defect was
    that these tasks were claiming something and were not heard. Every completion
    figure, goal percentage and effort forecast the OS reported was computed on a
    denominator missing a third of the finished work.

    The eighth instance of the mention-vs-declaration class, in its third shape:
    not a mention read as a declaration (forged state), nor a declaration read as a
    mention (PH15-T06's inert marker), but a *declaration form the parser had never
    been taught*, so a true claim registered as silence.
    """
    spans = _code_spans(line)

    def outside(m):
        return not any(s <= m.start() < e for s, e in spans)

    for m in STATUS_RE.finditer(line):
        if outside(m):
            return m.group(1).title()

    content = _strip_bullet(line)
    own = TASK_RE.match(content)
    own_id = own.group(0) if own else ""
    for m in STATUS_TICK_RE.finditer(content):
        if any(s <= m.start() < e for s, e in _code_spans(content)):
            continue
        parts = _CLAUSE_RE.split(content[:m.start()])
        clause = parts[-1] if parts else ""
        if any(t != own_id for t in TASK_RE.findall(clause)):
            continue  # the sentence is talking about a different task
        return m.group(1).title()

    # Form 3 (PH16-T41): `PH24-T12 — Complete (2026-08-17).` Last, so it can only ever
    # answer where the two older forms found nothing — this widens what is *heard*
    # and must never change what they already decided.
    head = HEAD_STATUS_RE.match(content)
    if head:
        return head.group(1).title()
    return ""


def _names_multiple_ids(content: str) -> bool:
    """True when `content` names more than one task id before its own status
    marker — `PH25-T01 / T04 / T05 / T06 (Pending)`, `PH16-T22 · T23 — all
    Complete`. (PH27-T11)

    Scoped to the FIRST clause (up to `.`, `;` or `—`, the same boundary
    `declared_status` already uses to keep a ✅ from binding a task the
    sentence merely names) — a bullet is free to mention other ids in its
    prose body; the shape this refuses is naming them before its own status
    even starts. Two forms count: a second full `PH#-T##` id, or the
    shorthand that drops the repeated `PH#-` prefix (`_SHORT_ID_RE`).
    """
    clause = _CLAUSE_RE.split(content, maxsplit=1)[0]
    if len(TASK_RE.findall(clause)) > 1:
        return True
    return bool(_SHORT_ID_RE.search(clause))


def is_declaration(line: str, *, allow_heading: bool = False) -> bool:
    """Is this line a task *declaration* rather than prose mentioning a task?

    A declaration is a list bullet (optionally checkboxed) whose content starts
    with the task id. Everything else — a summary paragraph, a `DoD:` sub-bullet
    quoting an id — is a mention.

    `allow_heading` additionally admits `### PH#-T##`, which is how downstream
    workspaces' `.ai/docs/tasks.md` declares every task. It is opt-in rather than
    the default because the two ledgers do not share a shape: in `activeContext.md`
    a `#` line is a *section title*, so `active_task` must keep refusing headings
    or a title would claim the session. The readers of the master ledger — `_scan`
    and `all_tasks` — pass True, and they must pass the *same* value: when they
    disagreed downstream, `all_tasks` counted 6 of 5G_Proxy's 36 tasks while
    `find_task` could credit none of them, so every goal %, completion figure and
    effort forecast was computed on a denominator missing most of the work.

    A bullet naming more than one task id before its own status marker —
    `PH25-T01 / T04 / T05 / T06 (Pending)` — is excluded entirely rather than
    silently owned by the first id it names (PH27-T11). `TASK_RE.match()` used
    to see only the leading token, so the whole multi-id sentence became that
    first id's title, and `just task-start` on it refused with "already
    declared" over a bullet that had declared nothing.
    """
    # The marker must be a REAL list marker — followed by whitespace. `*` is both
    # a bullet and an emphasis character, so a `startswith` test cannot tell
    # `* PH1-T01 …` (a bullet) from `**PH16-T36:** two guards passed …` (a bold
    # prose paragraph). That distinction did not matter until PH16-T39 taught
    # `_strip_bullet` to drop leading emphasis: without this line, every bold
    # paragraph opening with a task id became a declaration — re-opening the
    # mention-vs-declaration forgery from the other end. Caught in PH16-T39's own
    # self-review against the live `activeContext.md`, which has three such
    # paragraphs, and not by the suite, which had no test for the shape.
    marker = r"^\s*([-*+]|#{1,6})\s" if allow_heading else r"^\s*[-*+]\s"
    if not re.match(marker, line):
        return False
    content = _strip_bullet(line)
    if not TASK_RE.match(content):
        return False
    return not _names_multiple_ids(content)


#: A status marker that is the whole point of its line — trailing emphasis,
#: punctuation and sigils allowed, a following sentence not. `(In Progress)`
#: mid-sentence is prose *about* the marker; at the end of a line it was meant
#: to be the marker. See `_orphan_markers`.
ORPHAN_MARKER_RE = re.compile(r"\(In Progress\)[\s*_`.,;:✅]*$", re.I)


def _orphan_markers(lines: list[str]) -> list[dict]:
    """`(In Progress)` written where nothing will ever read it (PH16-T32).

    `active_task()` needs ONE line to be both a declaration and to carry the
    marker. Write it across two —

        - PH16-T30 `[complex]`: the archiver proves a block is old first.
          (In Progress)

    — and the declaration is invisible: the session runs bound to `task_id=""`,
    every `verify-safe` records a *maintenance run*, and the first thing to
    notice is `just work-done` refusing the claim, after the verification runs
    that would have proven the work are already spent. That happened on
    2026-08-15 (a) and cost a whole session's binding.

    This is the mirror of the bug `active_task()` was written against: that one
    let prose FORGE a declaration, this one makes a real declaration INVISIBLE.
    Detecting it is safe; *accepting* it is not — reading continuation lines for
    the marker would re-open the forgery — so `active_task()` still refuses to
    bind and this function only names what it found.

    Three conditions, each killing a class of false positive, because a detector
    that cries wolf in 46 workspaces' `verify-safe` output gets ignored:

      1. the line is inside a task declaration's block, by `_block()` — the same
         boundary definition every other field reader uses, not a second copy;
      2. the declaration itself carries NO status of any kind — a task that
         already says what it is cannot have lost its marker;
      3. the marker ENDS the line. `- DoD: PH2-T02 must report (In Progress)
         correctly.` is a sentence about the rule, and it is in the live suite.
    """
    out: list[dict] = []
    for i, line in enumerate(lines):
        if not is_declaration(line):
            continue
        m = TASK_RE.match(_strip_bullet(line))
        if not m:
            continue
        if declared_status(line):
            continue  # (In Progress) here too — a bound task, or an answered one
        for j in _block(lines, i, m.group(0)):
            text = lines[j].strip()
            if ORPHAN_MARKER_RE.search(text):
                out.append({"line": j + 1, "task": m.group(0), "kind": "continuation",
                            "text": text, "declared_line": i + 1})

    # PH16-T39, half (b). The pass above can only see a marker *below* a
    # declaration it already accepted — so when the declaration itself fails to
    # register, nothing reports it and the session learns at credit time that it
    # was bound to nothing. That is what made this defect cost hours twice
    # (2026-08-15 PH16-T36, 2026-08-16 PH22-T08) rather than minutes.
    #
    # Emphasis tolerance closes the shape that was actually hit; this closes the
    # SILENCE, so a fourth shape is named instead of discovered. It only ever
    # warns — binding here would re-open the forgery `active_task()` exists to
    # refuse — and it carries the same end-of-line condition as the rule above,
    # because a detector that cries wolf in 44 workspaces gets ignored.
    seen = {o["line"] for o in out}
    for i, line in enumerate(lines):
        text = line.strip()
        if i + 1 in seen or is_declaration(line):
            continue
        if not ORPHAN_MARKER_RE.search(text):
            continue
        m = TASK_RE.search(text)
        if m:
            out.append({"line": i + 1, "task": m.group(0), "kind": "unbound",
                        "text": text, "declared_line": 0})
    return out


def active_task_report(root: Path | None = None) -> dict:
    """Which task is bound, and what looks like a broken declaration.

    **The one implementation of this rule.** `gate_check`, `decision_log` and
    `evidence-pack.sh` all need "which task is the session working on?" and each
    had its own copy. Every copy read *any line containing "(In Progress)"* and
    took the first task id on it — so a session summary describing the workflow
    ("declare `(In Progress)` … then claim") matched, and the narrative about the
    rule forged an answer for the rule. Empty is the honest answer, and it means
    "maintenance run, covering no specific task".

    Returns `{"task": str, "orphans": [...], "source": str}`. The orphans are
    computed even when a task IS bound: a second declaration that lost its marker
    is still a task nothing will credit.
    """
    root = root or ws_root()
    ac = root / ".ai" / "memory-bank" / "activeContext.md"
    report = {"task": "", "orphans": [], "source": str(ac)}
    try:
        lines = ac.read_text(errors="replace").splitlines()
    except OSError:
        return report
    for line in lines:
        # PH27-T15 — `declared_status()`, not a bare `"(In Progress)" in line` substring
        # check: this ledger's own dated house style, `(In Progress 2026-08-17 i)`, is a
        # real declaration `STATUS_RE` already reads (the same form `(Complete 2026-08-01)`
        # uses throughout `tasks.md`), and the substring check missed it — a correctly
        # declared task bound to no task, `task_id=none`, and a wasted full-suite re-run
        # to discover nothing was wrong. `task_transition.py` already keys this exact check
        # on `declared_status(...).startswith("In Progress")`; reusing it here means the
        # writer and the reader of the marker can never teach it two different shapes.
        if declared_status(line).startswith("In Progress") and is_declaration(line):
            m = TASK_RE.search(_strip_bullet(line))
            if m:
                report["task"] = m.group(0)
                break
    report["orphans"] = _orphan_markers(lines)
    return report


def active_task(root: Path | None = None) -> str:
    """The task declared `(In Progress)` in activeContext.md, or "" for none.

    A thin wrapper over `active_task_report()` so the binding every gate trusts
    and the warning about a broken binding can never disagree about the file.
    """
    return active_task_report(root)["task"]


def orphan_warning(report: dict) -> list[str]:
    """Operator-facing lines for a report's orphans; empty when there are none.

    One renderer, because this has to read the same in `verify-safe`'s output and
    in the session briefing — the two places a session can still act on it.
    """
    out: list[str] = []
    for o in report["orphans"]:
        if o.get("kind") == "unbound":
            out.append(f"⚠️  line {o['line']} says `(In Progress)` and names {o['task']}, "
                       f"but the line is not a task declaration — so NOTHING is bound to it.")
            out.append(f"   A declaration STARTS with the id: "
                       f"`- {o['task']} — <title> (In Progress)`. "
                       f"Emphasis is fine (`- **{o['task']} (In Progress)** — …`); "
                       f"an id inside a code span is deliberately inert.")
        else:
            out.append(f"⚠️  {o['task']} declares `(In Progress)` on line {o['line']}, "
                       f"which is a continuation of its declaration on line {o['declared_line']} "
                       f"— so NOTHING is bound to it.")
            out.append(f"   Move the marker onto the declaration line itself: "
                       f"`- {o['task']} …: <title> (In Progress)`.")
        if not report["task"]:
            out.append("   Until then this session's evidence records a maintenance run "
                       "(task_id=none) and `just work-done` will refuse the claim.")
    return out


def find_task(task_id: str, root: Path | None = None) -> dict:
    """Locate a task across the ledger files.

    Returns `{"found": bool, "dod": str, "source": str, ...}`. A task declared in
    activeContext.md without a DoD still picks up tasks.md's DoD — the master
    ledger is where DoDs are written out in full.
    """
    root = root or ws_root()
    result = {"task": task_id, "found": False, "dod": "", "source": "",
              "title": "", "status": "", "line": 0, "complex": False,
              "goal": "", "goal_line": 0, "test_ref": "", "searched": []}
    for rel in _search_files(root):
        path = root / rel
        result["searched"].append(str(rel))
        if not path.is_file():
            continue
        try:
            hit = _scan(path.read_text(encoding="utf-8", errors="replace"), task_id)
        except OSError:
            continue
        if not hit:
            continue
        # OR'd across ledgers on purpose: marking a task `[complex]` in either
        # file is a claim about the work, and the stricter claim should win —
        # the alternative silently drops the marker when the two disagree.
        complex_so_far = result["complex"] or hit["complex"]
        if not result["found"]:
            result.update(hit)
            result["found"] = True
            result["source"] = str(rel)
        if not result["dod"] and hit["dod"]:
            result["dod"] = hit["dod"]
            result["source"] = str(rel)
        # Same first-non-empty rule as the DoD: a task listed tersely in
        # activeContext.md inherits the goal spelled out in tasks.md.
        if not result["goal"] and hit["goal"]:
            result["goal"] = hit["goal"]
            result["goal_line"] = hit["goal_line"]
        # Same first-non-empty rule again, for the `test:` field (PH15-T04).
        if not result["test_ref"] and hit["test_ref"]:
            result["test_ref"] = hit["test_ref"]
        result["complex"] = complex_so_far
        # Stop only when nothing further can be learned. Breaking on the DoD
        # alone loses the `test:` field for every task whose terse
        # activeContext.md entry carries a DoD while tasks.md carries the test —
        # which is the shape of every PH15 task, and would make `work-done`
        # refuse a `[complex]` task that does name its test.
        if result["dod"] and result["test_ref"]:
            break
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description="Read a task's declaration + DoD from the ledger.")
    ap.add_argument("task", nargs="?", help="task id, e.g. PH7-T02")
    ap.add_argument("--active", action="store_true",
                    help="print the task this session's evidence binds to (empty = maintenance "
                         "run). Warnings go to stderr so stdout stays parseable.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # `--active` is the shell entry point: `evidence-pack.sh` captures stdout for
    # `task_id` and lets stderr through to the operator's terminal, which is how
    # one implementation of the rule serves both languages (PH16-T32).
    if args.active:
        report = active_task_report()
        for line in orphan_warning(report):
            print(line, file=sys.stderr)
        if args.json:
            print(json.dumps(report, indent=2))
        elif report["task"]:
            print(report["task"])
        return 0

    if not args.task:
        ap.error("a task id is required (or use --active)")
    if not TASK_RE.fullmatch(args.task.strip()):
        print(f"❌ {args.task!r} is not a task id (expected PH#-T##).", file=sys.stderr)
        return 2

    info = find_task(args.task.strip())
    if args.json:
        print(json.dumps(info, indent=2))
    elif not info["found"]:
        print(f"❌ {args.task} is not declared in {' or '.join(info['searched'])}.")
    else:
        print(f"✅ {args.task} — declared in {info['source']}:{info['line']}"
              + (f" ({info['status']})" if info["status"] else ""))
        print(f"   {info['title']}")
        print(f"   DoD: {info['dod']}" if info["dod"] else "   ⚠️  no DoD line declared")
    return 0 if info["found"] else 1


if __name__ == "__main__":
    sys.exit(main())
