#!/usr/bin/env python3
"""
archive_tasks.py — `just archive-tasks`: move CLOSED task blocks out of the master
ledger (`.ai/docs/tasks.md`) into per-phase archive files (PH27-T01).

## Why this is a sibling script, not a new `archive_memory.py` Kind
`archive_memory.protected_lines()` refuses to move any line `task_ledger.is_declaration()`
recognises out of a `task_ledger.LEDGER_FILES` file — `activeContext.md` and `tasks.md` —
*whatever the Kinds say*. That guard exists because of a real incident (PH16-T15): a date-block
archiver taught a new shape once already moved live `Pending` task declarations off the ledger
invisibly, and `all_tasks()` kept counting them out of a file that no longer had them. `tasks.md`'s
archivable unit isn't a dated section anyway — it's a CLOSED `PH#-T##` task block — so bending
`archive_memory`'s date-shaped `Kind`/`Rule` machinery to fit would be a second, incompatible
notion of "the archivable unit" bolted onto code that already earned its caution the hard way.

## What moves
A block is archivable when `task_ledger._checkbox()` on its declaration line is `True` — this
repo's own existing definition of "closed" (the same one `all_tasks()`/`find_task()` already use),
not a second one invented here. Everything else — open blocks, phase headings, intro prose, and
`## HISTORY` — never moves.

## Where it goes, and what stays behind
`.ai/docs/archive/tasks-PHASE-<N>.md`, one file per phase, holding the moved blocks verbatim in
the SAME task-block Markdown syntax `tasks.md` already uses — so `task_ledger`'s existing parser
reads the archive unmodified (`task_ledger._archive_files()` adds these as a third, lowest-priority
source for `find_task()`/`all_tasks()`). A phase heading and its intro prose are never removed,
even once every task under it has archived away — 12 phases currently have zero open tasks and
still could not "move as a unit" usefully (PH27-T01's own investigation), so removing headings
buys little and risks corrupting the boundary between phases. Each phase that lost blocks gets one
trailer line, kept up to date across repeated runs:

    > N closed task(s) archived → [[.ai/docs/archive/tasks-PHASE-<N>.md]]

## Safety
Dry run by default; `--apply` writes. Idempotent — a second run finds nothing left to move and
reports that. A task block not under any `## PHASE N` heading (the preamble, or anything after
`## HISTORY`) is left alone and reported as skipped rather than guessed at.

Usage:
  archive_tasks.py            # dry run — report what WOULD move
  archive_tasks.py --apply    # actually move it
  archive_tasks.py --json     # machine-readable report (either mode)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_ledger as tl  # noqa: E402

TASKS_REL = Path(".ai") / "docs" / "tasks.md"
# `\d+(?:\.\d+)?` — this ledger has fractional phases (`## PHASE 4.6 — …`, a
# sub-phase inserted after PHASE 4 shipped). A bare `\d+\b` matches the "4" in
# "4.6" and `\b` is satisfied at the "." (a non-word char), so an earlier draft
# of this regex silently merged PHASE 4 and PHASE 4.6 into one archive file —
# caught by comparing the heading list against `plan["phases"]` on the real
# file before this ever ran with `--apply`.
PHASE_RE = re.compile(r"^##\s+PHASE\s+(\d+(?:\.\d+)?)\b")
ANY_H2_RE = re.compile(r"^##\s+\S")
TRAILER_RE = re.compile(r"^>\s*(\d+)\s+closed task\(s\) archived\s*→\s*\[\[(.+?)\]\]\s*$")


def _phase_sort_key(label: str) -> tuple[int, ...]:
    return tuple(int(p) for p in label.split("."))


def _trailer(count: int, archive_rel: str) -> str:
    return f"> {count} closed task(s) archived → [[{archive_rel}]]"


def _sections(lines: list[str]) -> list[tuple[str | None, int, int]]:
    """Split `lines` into `(phase_label_or_marker, start, end)` half-open ranges.

    `phase_label_or_marker` is the phase number as a STRING (`"4"`, `"4.6"`,
    `"27"` — never converted to `int`, since a fractional phase would raise)
    for a `## PHASE N` heading, the literal `"HISTORY"` for `## HISTORY`, any
    other heading's own text for an unrelated `## ` heading (`## BACKLOG`,
    `## P0 — …` — none of these are phases and none are ever archived into),
    and `None` for the preamble before the first `## ` heading. `end` is
    exclusive. Only string labels matching `PHASE_RE` are ever treated as an
    archivable phase by the caller — everything else is reported and skipped.
    """
    heads = [i for i, ln in enumerate(lines) if ANY_H2_RE.match(ln)]
    bounds = heads + [len(lines)]
    out: list[tuple[str | None, int, int]] = []
    if heads and heads[0] > 0:
        out.append((None, 0, heads[0]))
    elif not heads:
        return [(None, 0, len(lines))]
    for k, start in enumerate(heads):
        end = bounds[k + 1]
        m = PHASE_RE.match(lines[start])
        if m:
            label = m.group(1)
        elif lines[start].strip().upper().startswith("## HISTORY"):
            label = "HISTORY"
        else:
            label = lines[start].strip()
        out.append((label, start, end))
    return out


_PHASE_LABEL_RE = re.compile(r"^\d+(?:\.\d+)?$")


def _is_phase_label(label: str | None) -> bool:
    return label is not None and bool(_PHASE_LABEL_RE.match(label))


def _closed_blocks(lines: list[str], start: int, end: int) -> list[tuple[str, list[int]]]:
    """`[(task_id, [line indices, declaration first]), ...]` for CLOSED blocks
    declared inside `lines[start:end]`, in file order. Reuses `task_ledger`'s own
    declaration/block/checkbox rules — the exact ones `all_tasks()` uses — rather
    than a second definition of "what counts as a closed task".
    """
    out: list[tuple[str, list[int]]] = []
    i = start
    while i < end:
        line = lines[i]
        if tl.is_declaration(line, allow_heading=True):
            m = tl.TASK_RE.match(tl._strip_bullet(line))  # noqa: SLF001
            if m and tl._checkbox(line) is True:          # noqa: SLF001
                block = tl._block(lines, i, m.group(0))    # noqa: SLF001
                block = [j for j in block if j < end]
                out.append((m.group(0), [i, *block]))
        i += 1
    return out


def build_plan(root: Path | None = None) -> dict:
    """Compute what WOULD move, without writing anything.

    Returns a dict with `phases`: {phase_label: {"blocks": [(id, [line idx...])],
    "existing_trailer": int|None (line index of a trailer already there)}} and
    `skipped`: closed blocks found outside any numbered phase (never touched).
    `phase_label` is a string (`"4"`, `"4.6"`, `"27"`) — see `_sections`.
    """
    root = root or tl.ws_root()
    path = root / TASKS_REL
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    phases: dict[str, dict] = {}
    skipped: list[str] = []
    for label, start, end in _sections(lines):
        blocks = _closed_blocks(lines, start, end)
        if not blocks:
            continue
        if not _is_phase_label(label):
            skipped.extend(tid for tid, _ in blocks)
            continue
        trailer_idx = next(
            (i for i in range(start, end) if TRAILER_RE.match(lines[i])), None)
        phases[label] = {"blocks": blocks, "existing_trailer": trailer_idx,
                          "section": (start, end)}
    return {"lines": lines, "phases": phases, "skipped": skipped, "path": path}


def _block_text(lines: list[str], idxs: list[int]) -> str:
    return "\n".join(lines[i] for i in idxs)


def _archive_rel(phase: str) -> Path:
    return Path(".ai") / "docs" / "archive" / f"tasks-PHASE-{phase}.md"


def apply_plan(plan: dict, root: Path) -> dict:
    """Write the archive files and the rewritten `tasks.md`. Returns byte/line counts."""
    lines: list[str] = list(plan["lines"])
    moved_lines = 0
    moved_bytes = 0
    to_delete: set[int] = set()

    for phase in sorted(plan["phases"], key=_phase_sort_key):
        info = plan["phases"][phase]
        blocks = info["blocks"]
        if not blocks:
            continue
        archive_rel = _archive_rel(phase)
        archive_path = root / archive_rel
        archive_path.parent.mkdir(parents=True, exist_ok=True)
        chunks = [_block_text(lines, idxs) for _, idxs in blocks]
        moved_lines += sum(len(idxs) for _, idxs in blocks)
        moved_bytes += sum(len(c.encode("utf-8")) + 1 for c in chunks)
        header = (
            f"# Archive — PHASE {phase} closed tasks\n\n"
            f"> Moved from `.ai/docs/tasks.md` by `just archive-tasks --apply` "
            f"(PH27-T01). Verbatim; nothing here is ever edited by hand.\n\n"
        )
        existing = archive_path.read_text(encoding="utf-8") if archive_path.is_file() else header
        if not existing.endswith("\n\n"):
            existing = existing.rstrip("\n") + "\n\n"
        archive_path.write_text(existing + "\n\n".join(chunks) + "\n", encoding="utf-8")

        for _, idxs in blocks:
            to_delete.update(idxs)

        start, end = info["section"]
        prior_count = 0
        if info["existing_trailer"] is not None:
            m = TRAILER_RE.match(lines[info["existing_trailer"]])
            prior_count = int(m.group(1))
            to_delete.add(info["existing_trailer"])
        total = prior_count + len(blocks)
        # Insert just before the first surviving task declaration in the
        # section, or at the end of the section if none survive.
        insert_at = end
        for j in range(start, end):
            if j in to_delete:
                continue
            if tl.is_declaration(lines[j], allow_heading=True):
                insert_at = j
                break
        info["trailer_text"] = _trailer(total, str(archive_rel))
        info["insert_at"] = insert_at

    out: list[str] = []
    inserts: dict[int, str] = {info["insert_at"]: info["trailer_text"]
                                for info in plan["phases"].values() if info["blocks"]}
    for i, line in enumerate(lines):
        if i in inserts:
            out.append(inserts[i])
        if i in to_delete:
            continue
        out.append(line)
    # An insertion point that fell exactly at `end` (== len(lines) for the
    # last section) is never visited by the loop above.
    if len(lines) in inserts:
        out.append(inserts[len(lines)])

    plan["path"].write_text("\n".join(out) + "\n", encoding="utf-8")
    return {"moved_lines": moved_lines, "moved_bytes": moved_bytes,
            "phases_touched": sorted(plan["phases"])}


def render(plan: dict, applied: bool, result: dict | None) -> str:
    total_blocks = sum(len(info["blocks"]) for info in plan["phases"].values())
    total_lines = sum(len(idxs) for info in plan["phases"].values()
                       for _, idxs in info["blocks"])
    lines_out = []
    if not total_blocks:
        lines_out.append("✅ Nothing to archive — no closed task block sits in a live phase.")
    elif applied:
        r = result or {}
        lines_out.append(
            f"✅ Archived {total_blocks} closed task block(s), "
            f"{r.get('moved_lines', total_lines)} line(s) / "
            f"{r.get('moved_bytes', 0):,} byte(s), across "
            f"{len(r.get('phases_touched', []))} phase file(s).")
        for phase in sorted(plan["phases"], key=_phase_sort_key):
            info = plan["phases"][phase]
            lines_out.append(f"   PHASE {phase}: {len(info['blocks'])} block(s) → "
                              f"{_archive_rel(phase)}")
    else:
        lines_out.append(
            f"📋 {total_blocks} closed task block(s) / {total_lines} line(s) "
            f"WOULD move across {len(plan['phases'])} phase(s):")
        for phase in sorted(plan["phases"], key=_phase_sort_key):
            info = plan["phases"][phase]
            ids = ", ".join(tid for tid, _ in info["blocks"])
            lines_out.append(f"   PHASE {phase}: {ids}")
        lines_out.append("\n  → Re-run with --apply to move them. Nothing is deleted; the")
        lines_out.append("    live ledger keeps a one-line pointer to each phase's archive.")
    if plan["skipped"]:
        lines_out.append(
            f"⚠️  {len(plan['skipped'])} closed block(s) sit outside any numbered phase "
            f"(preamble/HISTORY) and were left alone: {', '.join(plan['skipped'])}")
    return "\n".join(lines_out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="actually move (default: dry run)")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args()

    root = tl.ws_root()
    plan = build_plan(root)
    result = apply_plan(plan, root) if args.apply else None

    if args.json:
        payload = {
            "applied": args.apply,
            "phases": {p: [tid for tid, _ in i["blocks"]] for p, i in plan["phases"].items()},
            "skipped": plan["skipped"],
            "result": result,
        }
        print(json.dumps(payload, indent=2))
    else:
        print(render(plan, args.apply, result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
