#!/usr/bin/env python3
"""task_transition.py — the three ledger files change together, or not at all (PH27-T02).

## Why this exists, measured rather than felt

A task's state lives in three files that must agree: `.ai/docs/tasks.md` owns the
checkbox, `.ai/memory-bank/activeContext.md` owns the `(In Progress)` declaration
that `task_ledger.active_task()` — and therefore `evidence.json` — binds to, and
`.ai/memory-bank/progress.md` owns the history. Nothing performed that change.
Sessions performed it by hand, with Python heredocs and string slicing.

  * **Terminal 67746, 2026-08-18.** Stage 1 of a two-task session — heredoc
    slicing, a title desynced between two of the files, and the four-test cascade
    that followed — cost **101.4k of ~116k tokens and 33 of 51 minutes**, fifteen
    of them in one diagnostic loop.
  * **The session that built this** opened with `activeContext.md` declaring
    `PH16-T42 (In Progress)` a full session after PH16-T42 shipped in `bd3b5d9`.
    Nobody wrote the wrong thing. Somebody failed to write the right thing, in one
    of three places, by hand.

The invariants were never missing — PH16-T09 (a completed task leaves the working
set), PH16-T30/T32 (markers on the declaration line), PH16-T41 (prose and checkbox
agree), PH16-T42 (`[complex]` is detected). They all fire *after* the fact, inside
a 146-second suite. This module is what makes the change correctly the first time.

## The title is not copied — it is never authored twice

`start()` reads the title, `[complex]` marker, DoD, Goal and test out of
`tasks.md` and writes the declaration from them. Two records cannot disagree when
only one of them was ever written by a human. That is the whole of the PH16-T41
defence, and it is structural rather than checked.

## Atomic, and refusing beats guessing

Every new file text is computed in memory and validated before any write. A
refusal writes nothing and names the invariant that stopped it. If a write raises
part-way through, the files already written are restored — a half-applied state
change is the one outcome worse than not having the tool, because by then the
operator has stopped reading.

Where the working set has no existing declaration to anchor against, `start()`
**refuses** rather than inserting at a guessed offset. A wrong offset puts the
declaration somewhere `active_task()` may not read, which is the failure this
module exists to prevent, arriving by a new route.

## It defines no rule of its own

`is_declaration`, `declared_status` and `is_complex` come from `task_ledger`; the
per-entry line budget comes from `archive_memory.RULES` via `entry_budget`. A
second copy of any of them would drift, and the drift would reach 38 workspaces as
`work-done` refusing a task that is fine. `tests/test_task_transition.py` asserts
the delegation by scanning this source, so it cannot decay into good intentions.

## What this does NOT do

It does not touch `verify-safe`, the validation gate, the pre-work brief or the
`plan-before-code` requirement. It removes typing, never a check. `work-done`
stays the credit gate and stays separate: the command that certifies the work must
not also be the command that edits the ledger the certification is checked against.

It does not letter a session's `progress.md` heading (PH27-T08). `done()` will
join a same-day heading that carries no ordinal, but it never writes `(a)`, `(b)`,
… itself — that stays the session-close convention it already is. This means two
genuinely different, back-to-back unclosed sessions on the same day can still land
under one heading if neither ever got lettered in between; that is a smaller,
separate problem from the one this closes (two colliding *undated-relative-to-each-
other* headings that broke the archiver's ordering), and it is never worse than
today's behaviour — it merges instead of colliding.
"""

from __future__ import annotations

import argparse
import datetime
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import archive_memory  # noqa: E402
import entry_budget  # noqa: E402
import task_ledger  # noqa: E402

LEDGER = Path(".ai") / "docs" / "tasks.md"
ACTIVE = Path(".ai") / "memory-bank" / "activeContext.md"
PROGRESS = Path(".ai") / "memory-bank" / "progress.md"

IN_PROGRESS = "(In Progress)"
SHIPPED_PREFIX = "**Shipped and moved out of working set"


def ws_root(root: Path | str | None = None) -> Path:
    """The AGENTS.md anti-drift rule — `task_ledger`'s resolver, not a second one."""
    return Path(root) if root is not None else task_ledger.ws_root()


def _today() -> str:
    """IST, because every date this workspace writes in prose is IST."""
    tz = datetime.timezone(datetime.timedelta(hours=5, minutes=30))
    return datetime.datetime.now(tz).strftime("%Y-%m-%d")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _write_text(path: Path, text: str) -> None:
    """Atomic single-file write. Patched by the atomicity test to force a failure
    part-way through a transition, so it is a named seam rather than an inline
    `open()`."""
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tt-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _commit(writes: dict[Path, str]) -> None:
    """Apply every computed text, or restore what was there before.

    Not a transaction — the filesystem does not offer one across three files — but
    it closes the window that matters: a raise on file 2 of 3 leaves the tree as it
    was, instead of leaving a ledger that disagrees with itself in a new way.
    """
    originals = {p: _read(p) for p in writes}
    written: list[Path] = []
    try:
        for path, text in writes.items():
            _write_text(path, text)
            written.append(path)
    except BaseException:
        for path in written:
            try:
                _write_text(path, originals[path])
            except OSError:
                pass
        raise


def _refuse(reason: str) -> dict:
    return {"ok": False, "reason": reason, "wrote": []}


# ── Reading the current state ───────────────────────────────────────────────

def _declared(root: Path) -> dict:
    """`{task_id: record}` from `activeContext.md` — the working set as declared."""
    return task_ledger.declarations_in(root / ACTIVE)


def _in_progress(root: Path) -> list[str]:
    """Every id currently declaring `(In Progress)` in the working set.

    A list, not an Optional: two of them is a real state this repo has been in, and
    the caller needs to name both rather than silently see the first.
    """
    out = []
    for line in _read(root / ACTIVE).splitlines():
        if not task_ledger.is_declaration(line, allow_heading=True):
            continue
        if task_ledger.declared_status(line).startswith("In Progress"):
            m = task_ledger.TASK_RE.match(task_ledger._strip_bullet(line))
            if m:
                out.append(m.group(0))
    return out


def _anchor(lines: list[str]) -> int | None:
    """Index of the first existing declaration — where a new one is inserted.

    `None` means the working set has no anchor, and the caller refuses. Guessing an
    offset would put the declaration where `active_task()` may not read it.
    """
    for i, line in enumerate(lines):
        if task_ledger.is_declaration(line, allow_heading=False):
            return i
    return None


def _block(lines: list[str], start: int) -> int:
    """End index (exclusive) of the declaration block beginning at `start`.

    The block is its bullet plus the indented continuation lines beneath it — the
    same shape `task_ledger._block` walks, applied to removal.
    """
    end = start + 1
    while end < len(lines):
        nxt = lines[end]
        if not nxt.strip():
            # A blank line ends the block only if what follows is not a continuation.
            after = end + 1
            if after < len(lines) and lines[after].startswith(("  ", "\t")):
                end = after
                continue
            break
        if not nxt.startswith(("  ", "\t")):
            break
        end += 1
    return end


# ── start ───────────────────────────────────────────────────────────────────

def start(task_id: str, root: Path | str | None = None) -> dict:
    """Declare `task_id` in progress, with its details read from the ledger."""
    root = ws_root(root)
    ledger_path, active_path = root / LEDGER, root / ACTIVE
    if not ledger_path.is_file() or not active_path.is_file():
        return _refuse(f"missing ledger file(s) under {root} — nothing was written")

    ledger = task_ledger.declarations_in(ledger_path)
    rec = ledger.get(task_id)
    if rec is None:
        return _refuse(
            f"{task_id} is not declared in {LEDGER}. A task is started from the "
            f"master ledger, so add it there first — a declaration invented here "
            f"would be a task with no DoD and no goal.")
    if rec.get("checked") is True:
        return _refuse(
            f"{task_id} is already ticked `- [x]` in {LEDGER}. Re-declaring a "
            f"completed task puts it back in the working set (PH16-T09).")

    already = _declared(root)
    if task_id in already:
        return _refuse(f"{task_id} is already declared in {ACTIVE} — nothing to do.")
    open_now = _in_progress(root)
    if open_now:
        return _refuse(
            f"{', '.join(open_now)} is already (In Progress). `active_task()` reads "
            f"{ACTIVE} and answers with the first declaration it meets, so a second "
            f"one binds evidence.json to whichever it happens to find. Finish or "
            f"retire it first: just task-done {open_now[0]} \"<summary>\"")

    lines = _read(active_path).splitlines()
    at = _anchor(lines)
    if at is None:
        return _refuse(
            f"{ACTIVE} holds no existing task declaration to anchor the working "
            f"set to, and this refuses to guess a line number — an offset that is "
            f"wrong puts the declaration where active_task() will not read it. "
            f"Add the first declaration by hand, then this command maintains it.")

    # The marker goes in the MARKER ZONE — immediately after the id, before the
    # status — because that is rule 2 of `task_ledger.is_complex`, and it is this
    # kernel's house style. Written after the status it satisfies neither rule 2
    # (wrong slot) nor rule 3 (a title follows, so it is not a trailing run), and
    # `plan-before-code` would be silently off for the task. That is PH16-T42
    # arriving from the writing side instead of the parsing side.
    marker = " `[complex]`" if rec.get("complex") else ""
    title = (rec.get("title") or "").strip()
    head = f"- {task_id}{marker} {IN_PROGRESS} — {title}"

    # Round-trip the line we are about to write through the same predicates that
    # will read it back. Emitting a declaration the ledger cannot parse is the
    # failure this module exists to prevent, so it is checked here rather than
    # discovered in a 146-second suite.
    if not task_ledger.is_declaration(head):
        return _refuse(f"refusing to write a line task_ledger.is_declaration() "
                       f"does not recognise: {head!r}")
    if bool(rec.get("complex")) != task_ledger.is_complex(head):
        return _refuse(f"refusing to write a line whose `[complex]` marker "
                       f"task_ledger.is_complex() reads differently from the "
                       f"ledger's: {head!r}")
    if not task_ledger.declared_status(head).startswith("In Progress"):
        return _refuse(f"refusing to write a line task_ledger.declared_status() "
                       f"does not read as In Progress: {head!r}")

    block = [head]
    for label, key in (("DoD", "dod"), ("test", "test_ref"), ("Goal", "goal")):
        val = (rec.get(key) or "").strip()
        if val:
            block.append(f"  {label}: {val}")
    block.append("")

    new_active = "\n".join(lines[:at] + block + lines[at:]) + "\n"
    _commit({active_path: new_active})
    return {"ok": True, "task": task_id, "title": title,
            "complex": bool(rec.get("complex")), "wrote": [str(ACTIVE)],
            "reason": f"{task_id} declared in progress — title read from {LEDGER}, "
                      f"not retyped."}


# ── done ────────────────────────────────────────────────────────────────────

def _tick(text: str, task_id: str, date: str) -> str:
    """`- [ ] PH#-T##` → `- [x] …`, with `(Complete <date>)` appended once."""
    out = []
    for line in text.splitlines():
        stripped = line.lstrip()
        if stripped.startswith("- [ ]") and task_id in line.split("—")[0]:
            line = line.replace("- [ ]", "- [x]", 1)
            if "(Complete" not in line:
                line = f"{line.rstrip()} (Complete {date})"
        out.append(line)
    return "\n".join(out) + "\n"


def _retire(text: str, task_id: str, date: str) -> str:
    """Remove the declaration block and name the id on the shipped line."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not task_ledger.is_declaration(line, allow_heading=True):
            continue
        m = task_ledger.TASK_RE.match(task_ledger._strip_bullet(line))
        if m and m.group(0) == task_id:
            del lines[i:_block(lines, i)]
            break

    for i, line in enumerate(lines):
        if line.startswith(SHIPPED_PREFIX):
            if task_id not in line:
                # HEAD of the list, immediately after `:**` — never end-of-line.
                # Found by dogfooding this on the real file: the live shipped line
                # runs on into a sentence that wraps across several lines, so an
                # end-of-line append produced `…both landed in · PH27-T06` and
                # corrupted the prose. The ids always follow `:**`; the tail may be
                # anything. This also puts the newest id first, which is the order
                # the rest of the file already reads in.
                head, sep, tail = line.partition(":**")
                lines[i] = (f"{head}{sep} {task_id} ·{tail}" if sep
                            else f"{line.rstrip()} · {task_id}")
            break
    else:
        at = _anchor(lines)
        ins = at if at is not None else len(lines)
        lines[ins:ins] = [f"{SHIPPED_PREFIX} ({date}):** {task_id}", ""]
    return "\n".join(lines) + "\n"


def _entry(task_id: str, summary: str, date: str, budget: int) -> list[str]:
    """A `## <date> — <summary>` block trimmed to its share of progress.md.

    The share is `entry_budget.entry_budget(archive_memory.RULES["progress.md"])`
    — the file's own allowance divided by how many entries it keeps hot. Read, not
    written down here: a literal would be a second source for one number, and the
    tripwire that cost the baseline session a second 2.5-minute suite run was
    exactly this budget being invisible until the tests ran.
    """
    body = [ln.rstrip() for ln in summary.strip().splitlines() if ln.strip()]
    head = f"## {date} — {body[0][:110]}"
    lines = [head, "", f"**Work: {task_id}.**"] + body[1:]
    # The trailing blank separating this entry from the next one is part of the
    # entry, so it is counted here. Trimming to `budget` and then appending would
    # emit `budget + 1` and reopen the tripwire this exists to close.
    if len(lines) + 1 > budget:
        lines = lines[:budget - 2] + ["_(trimmed to the per-entry budget — full "
                                      "detail in AI_CHANGELOG.md)_"]
    return lines + [""]


def _paragraph(task_id: str, summary: str, budget: int) -> list[str]:
    """A standalone `**Work: <id>.**` paragraph, sized like `_entry()`'s first one.

    The shape a task gets when it JOINS a heading already open for today instead
    of starting a new one (PH27-T08's fix) — unlike `_entry()`, there is no
    heading to hand the summary's first line to, so the whole summary lands here.
    """
    body = [ln.rstrip() for ln in summary.strip().splitlines() if ln.strip()]
    lines = [f"**Work: {task_id}.**"] + body
    if len(lines) + 1 > budget:
        lines = lines[:max(budget - 2, 1)] + ["_(trimmed to the per-entry budget — full "
                                              "detail in AI_CHANGELOG.md)_"]
    return lines + [""]


def done(task_id: str, summary: str, root: Path | str | None = None) -> dict:
    """Tick the ledger, retire the declaration, record the history — atomically.

    **A second same-day call joins the heading already open, rather than
    duplicating it (PH27-T08).** Live defect this closes: two `done()` calls in
    one unclosed session each opened a fresh `## <date> — …` heading, and
    `archive_memory.proves_older` cannot order two same-date blocks that both
    carry no ordinal — it withholds rather than guesses, so the archiver's plan
    became order-dependent (caught by
    `test_the_real_memory_bank_plans_the_same_from_either_end` against the real
    file). The guard is the block's ORDINAL, not just its date: a closed
    session's heading has already earned a letter by the time another session's
    work lands on the same date, so only an ordinal-less block is treated as
    "mine to extend." This never invents a letter — lettering stays the
    session-close convention it already is.
    """
    root = ws_root(root)
    paths = {"ledger": root / LEDGER, "active": root / ACTIVE,
             "progress": root / PROGRESS}
    missing = [str(p) for p in paths.values() if not p.is_file()]
    if missing:
        return _refuse(f"missing ledger file(s): {', '.join(missing)} — nothing "
                       f"was written")
    if not summary.strip():
        return _refuse(f"{task_id} needs a summary — a history entry with no "
                       f"content is a green tick over nothing.")

    if task_id not in _in_progress(root):
        return _refuse(
            f"{task_id} is not declared {IN_PROGRESS} in {ACTIVE}, so there is no "
            f"transition to make. Start it first: just task-start {task_id}")

    date = _today()
    budget = entry_budget.entry_budget(archive_memory.RULES["progress.md"])

    # Every text computed before any write — a refusal found on the third file
    # cannot have already changed the first.
    new_ledger = _tick(_read(paths["ledger"]), task_id, date)
    new_active = _retire(_read(paths["active"]), task_id, date)

    prog = _read(paths["progress"]).splitlines()
    at = next((i for i, ln in enumerate(prog) if ln.startswith("## ")), len(prog))
    joins_open_heading = (
        at < len(prog)
        and archive_memory.block_date(prog[at]) == datetime.date.fromisoformat(date)
        and archive_memory.block_ordinal(prog[at]) == archive_memory.NO_ORDINAL)
    if joins_open_heading:
        end = next((i for i in range(at + 1, len(prog)) if prog[i].startswith("## ")),
                   len(prog))
        remaining = max(budget - (end - at), 3)
        new_progress = "\n".join(
            prog[:end] + _paragraph(task_id, summary, remaining) + prog[end:]) + "\n"
    else:
        new_progress = "\n".join(
            prog[:at] + _entry(task_id, summary, date, budget) + prog[at:]) + "\n"

    _commit({paths["ledger"]: new_ledger, paths["active"]: new_active,
             paths["progress"]: new_progress})
    return {"ok": True, "task": task_id, "date": date,
            "wrote": [str(LEDGER), str(ACTIVE), str(PROGRESS)],
            "reason": f"{task_id} ticked, retired from the working set, and "
                      f"recorded in {PROGRESS}."}


# ── CLI ─────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("start", help="declare a task in progress")
    s.add_argument("task_id")
    d = sub.add_parser("done", help="tick, retire and record a finished task")
    d.add_argument("task_id")
    d.add_argument("summary")
    for p in (s, d):
        p.add_argument("--root", default=None)
    args = ap.parse_args(argv)

    out = (start(args.task_id, root=args.root) if args.cmd == "start"
           else done(args.task_id, args.summary, root=args.root))
    if out["ok"]:
        print(f"  ✅ {out['reason']}")
        for w in out["wrote"]:
            print(f"     • {w}")
        return 0
    print(f"  ❌ {out['reason']}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
