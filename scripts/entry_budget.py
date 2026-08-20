#!/usr/bin/env python3
"""entry_budget.py — the *entry* is the unit that needs a budget, not just the file
(PH16-T05).

AGENTS.md § CONTEXT BUDGET holds every hot memory file to 200 lines, and `doctor` has
warned when one blew past it since v3.4. That warning names a file and a number, which
is enough to know something is wrong and not enough to do anything about it — so it sat
true for weeks and became furniture. Measured 2026-08-11: `progress.md` held **7 entries
across 1118 lines** (~160 lines each). A single session's entry consumed ~80% of the
whole file's allowance without ever tripping a rule of its own, because there was no
rule of its own.

## Two things this module reports

**1. Oversized entries, by name.** The per-entry budget is `MAX_LINES // rule.keep` —
the file's own allowance divided by how many history entries that file is designed to
hold hot. Both numbers already exist in `archive_memory` and are already pinned by
tests; nothing new is invented here that could drift from them. `progress.md` (keep 8)
→ 25 lines an entry, `activeContext.md` / `knownIssues.md` (keep 3) → 66.

**2. Why a file is over budget — which is a different question from whether it is.**
`archive_memory` only moves what `RULES` can *see*. When a memory file's writing
convention drifts away from its rule, the blocks do not become "held back by `--keep`";
they become invisible, and the archiver answers `nothing to archive` — the same sentence
it prints when a file is genuinely healthy. That is what happened here: `activeContext.md`
had been writing `**Session before:**` blocks (14 of them) against a rule that only knew
`**Prior Session:**` / `**Earlier:**`; `progress.md` had moved to `## <date>` heading
sections against a rule that only knew `- **<date>:**` bullets; `knownIssues.md` had been
marking issues `✅ RESOLVED` inline inside `## Active Issues (<date>)` sections against a
rule that only knew `## Resolved`. All three reported ~1 closed block and moved nothing,
for weeks, while the boot set cost 45% of the session context budget.

An over-budget file therefore gets a *cause*, not just a number, and the three causes
have different owners:

    blind       over budget, and the archiver can move NOTHING — not at any `--keep`.
                The tool cannot see the file's shape. The RULE is broken. → FAIL
    unarchived  over budget while still holding history the archiver would move right
                now. Nobody ran it. → FAIL, and `just archive-memory --apply` fixes it
    (neither)   over budget on live state alone, with the archiver at its floor. The
                file genuinely holds more live state than the budget allows and needs
                a human editing decision. → warn, with the measured floor

Only the first two are the OS lying to itself, and only those two are things a tool can
fix. Merging them into one "over budget" signal is what let this sit for weeks.

**One splitter, not two.** Entries are cut by `archive_memory.split_blocks` under
`archive_memory.RULES` — imported, never re-implemented. A second parser of "what an
entry is" would be a fresh copy of exactly the drift this module exists to detect: fix a
rule in `archive_memory` and this check follows it in the same edit, by construction.

Usage:
  entry_budget.py            # this workspace's per-entry verdict
  entry_budget.py --json
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import archive_memory as am  # noqa: E402

# How many oversized entries to name. Enough to act on, not so many the message
# becomes a second copy of the file — the same cap `protocol_budget` uses.
TOP_N_ENTRIES = 5


def ws_root() -> Path:
    return am.ws_root()


def entry_budget(rule: "am.Rule") -> int:
    """One entry's fair share of the file's line allowance.

    Derived, never chosen: the file budget `archive_memory.MAX_LINES` divided by
    `rule.keep`, the number of history entries that file is designed to keep hot.

    **The derivation moved to `archive_memory.entry_allowance()` (PH24-T14)** and this is
    now one line of delegation, kept because `doctor`, `render()` and the tests all call it
    by this name. It moved because `unfixable()` needs the same share to say which entry is
    the bulk of an over-budget file, and `archive_memory` cannot import this module without
    a cycle — so the choice was one owner or two copies of `MAX_LINES // keep`, and two
    copies of a threshold is the drift both these modules exist to detect.
    """
    return am.entry_allowance(rule)


def measure(path: Path, rule: "am.Rule", max_lines: int) -> dict:
    """Per-file verdict. Pure read — nothing here writes or plans a write."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    blocks = am.split_blocks(lines, rule)
    budget = entry_budget(rule)
    char_budget = max_lines * am.CHARS_PER_LINE
    # A file's rule declares whether it can EVER hold live state, by whether any of its
    # `Kind`s is ever non-archivable (`archivable=False`, or a callable — the content-
    # dependent form used nowhere today, treated as "might be live" rather than guessed
    # at). `progress.md`'s two kinds are both plain history; `activeContext.md` and
    # `knownIssues.md` each declare at least one kind — `**Last Session:**`, an open issue
    # bullet — that is never archivable. Derived from the rule the archiver already reads,
    # not a second per-file table that could disagree with it.
    pure_history = not any(callable(k.archivable) or k.archivable is False
                            for k in rule.kinds)

    entries, offset, unexplained = [], 1, 0
    for block in blocks:
        size = len(block["lines"])
        if block["archivable"]:
            entries.append({
                "file": path.name,
                "head": block["lines"][0].strip(),
                "line": offset,
                "lines": size,
                "over_by": max(0, size - budget),
            })
        # A block with no `kind` is content no rule in RULES accounts for — neither
        # as history nor as declared-live. The file's header is legitimately in this
        # bucket; anything past `MAX_LINES` of it is the rules having lost the file.
        if block["kind"] is None:
            unexplained += size
        offset += size

    # **Chars decide, lines are reported (PH16-T19).** The budget is charged in the unit the
    # context actually pays; the line number stays in the payload beside it, because it is the
    # number every previous `doctor` run printed and someone reading a changed verdict has to
    # be able to see which measure moved. A file over on EITHER is over budget: the line rule
    # is not being relaxed, only joined by the one that catches what it missed.
    # Asked of `archive_memory`, never re-implemented here. These two modules
    # held private copies of this rule and drifted: the checker counted chars,
    # the archiver's file filter counted lines, and `doctor` spent its days
    # FAILING with a remedy that could not reach the file — see
    # `archive_memory.over_budget()`.
    over_budget = am.over_budget(text, max_lines)
    # What `just archive-memory --apply` would actually leave behind, at this file's
    # own `keep` policy...
    archived = am.plan_file(path, rule, keep=rule.keep, keep_days=0)["lines_after"]
    # ...and the absolute floor: everything the archiver can see, moved. What remains
    # at `--keep 0` is live state by definition — the tool has no further move.
    floor = am.plan_file(path, rule, keep=0, keep_days=0)["lines_after"]

    # `blind` / `unarchived` computed once, as locals, so `unexcused` below can name
    # "neither of the other two" without re-deriving either expression (the drift PH27-T03
    # itself was filed to close: one rule, one implementation).
    blind = over_budget and unexplained > max_lines
    unarchived = over_budget and archived < len(lines)

    return {
        "file": path.name,
        "lines": len(lines),
        "budget": max_lines,
        "chars": len(text),
        "char_budget": char_budget,
        "over_by_chars": max(0, len(text) - char_budget),
        # ~4 chars/token over this corpus. Stated as an estimate because it is one — the
        # same basis `just tokens` reports against, and the same ±15% honesty.
        "est_tokens": len(text) // 4,
        "entry_budget": budget,
        "keep": rule.keep,
        "entries": len(entries),
        "over_by": max(0, len(lines) - max_lines),
        "oversized": [e for e in entries if e["over_by"] > 0],
        "archived_lines": archived,
        "floor_lines": floor,
        "unexplained_lines": unexplained,
        # Two different findings, deliberately not merged into one (PH16-T05):
        #
        # `blind` — over budget, and more than a whole file's allowance of it is
        # content NO rule accounts for: not history, and not declared-live either.
        # That is drift: the file changed how it writes and `RULES` did not follow,
        # so the archiver skips it while reporting the same cheerful sentence it
        # prints for a healthy file. The *rule* is broken, and that is a FAIL.
        #
        # Measured as unaccounted-for lines rather than as "the archiver moved
        # nothing", which was the first definition tried here and was too weak to
        # catch the real defect: pre-fix, `activeContext.md` moved 2 lines out of
        # 666 — not zero, so a "moved nothing" test stayed green through exactly
        # the state it existed to catch. Verified by mutation, not by reasoning.
        "blind": blind,
        # `unarchived` — over budget while still holding history `archive-memory`
        # would move right now. Nobody ran it. Always fixable by running it, so it
        # is a debt, not a defect — but it may never be the reason a file is fat.
        "unarchived": unarchived,
        # `pure_history` — this file's rule declares no shape as ever live, so an
        # entry sitting at the archiver's floor is never "genuinely live state a
        # human must judge" — see the comment above where this is derived.
        "pure_history": pure_history,
        # `unexcused` — over budget, at the archiver's floor (neither `blind` nor
        # `unarchived` — there is nothing left the tool itself can move), on a file
        # with no live-state excuse (PH27-T03). Distinct from the module's existing
        # third case ("over budget on live state alone" — a real human editing
        # decision, and a legitimate `warn`): here the *rule* says nothing in this
        # file is ever live, so the overage has to be a block the archiver can see
        # but can never select — e.g. a dated-shaped heading whose date fails to
        # parse (`block_date()` returns `None`; `archive_memory.select()`'s own
        # rule is "undated blocks are never selected") — not a judgement call.
        "unexcused": over_budget and pure_history and not blind and not unarchived,
    }


def check(root: Path | None = None, max_lines: int | None = None) -> dict:
    """Verdict over every hot memory file `archive_memory` has a rule for.

    A file with no rule is not measured here — inventing an entry shape for it would be
    the guess this module refuses to make. `ok` is false when any file is over budget or
    any entry is over its share; `blind` is reported separately because it is the one
    state that means the *tool* is broken rather than the file being big.
    """
    root = root or ws_root()
    max_lines = am.MAX_LINES if max_lines is None else max_lines
    files = []
    for name, rule in am.RULES.items():
        path = root / am.MEMORY_DIRNAME / name
        if not path.is_file():
            continue
        files.append(measure(path, rule, max_lines))

    oversized = sorted((e for f in files for e in f["oversized"]),
                       key=lambda e: -e["lines"])
    blind = [f for f in files if f["blind"]]
    unarchived = [f for f in files if f["unarchived"]]
    unexcused = [f for f in files if f["unexcused"]]
    return {
        "ok": not blind and not unarchived and not oversized
              and all(f["over_by"] == 0 and f["over_by_chars"] == 0 for f in files),
        "budget": max_lines,
        "char_budget": max_lines * am.CHARS_PER_LINE,
        "files": files,
        "blind": [f["file"] for f in blind],
        "unarchived": [f["file"] for f in unarchived],
        # `unexcused` ⊆ files already counted "over budget" by `ok` above (PH27-T03) —
        # named separately here for the same reason `blind`/`unarchived` are: a reader
        # needs to know WHICH of the three causes applies, not only that one does.
        "unexcused": [f["file"] for f in unexcused],
        "oversized_entries": oversized,
    }


def render(c: dict) -> None:
    print("\n" + "═" * 62)
    print("  📏 ENTRY BUDGET — is any single memory entry eating the file?")
    print("═" * 62)
    for f in c["files"]:
        bad = f["blind"] or f["unarchived"] or f["unexcused"]
        over = f["over_by"] or f["over_by_chars"]
        mark = "❌" if bad else ("⚠️ " if over or f["oversized"] else "✅")
        # Chars first, because chars are what is charged; the line number follows it rather
        # than leading, so the measure that decides the verdict is the one read first.
        print(f"\n  {mark} {f['file']}  {f['chars']:,} / {f['char_budget']:,} chars "
              f"(≈{f['est_tokens'] // 1000}k tokens) · {f['lines']} / {f['budget']} lines · "
              f"{f['entries']} entr(ies) · {f['entry_budget']} lines each "
              f"(= {f['budget']} ÷ keep {f['keep']})")
        if f["over_by_chars"] and not f["over_by"]:
            # The state the line budget could not see at all, and the reason the unit changed.
            print(f"       ⚠️  inside the line budget and {f['over_by_chars']:,} chars OVER "
                  f"the real one — {f['chars'] // max(f['lines'], 1)} chars/line against the "
                  f"{am.CHARS_PER_LINE} this budget assumes.")
        for e in f["oversized"][:TOP_N_ENTRIES]:
            print(f"       {e['lines']:>4} ln  (+{e['over_by']:<4}) line {e['line']:<5} "
                  f"{e['head'][:70]}")
        extra = len(f["oversized"]) - TOP_N_ENTRIES
        if extra > 0:
            print(f"       … and {extra} more over the {f['entry_budget']}-line share")
        if f["blind"]:
            print("       ❌ archive-memory can move NOTHING from this file — not even at "
                  "`--keep 0`.")
            print("          Its shape has drifted from archive_memory.RULES. Widen the rule.")
        elif f["unarchived"]:
            print(f"       ❌ {f['lines'] - f['archived_lines']} line(s) of closed history are "
                  f"still sitting here → `just archive-memory --apply` leaves "
                  f"{f['archived_lines']}.")
        elif f["unexcused"]:
            # PH27-T03 — this file's rule declares no live state, so the archiver being at
            # its floor and still over budget is never a human editing call the way it is
            # for `activeContext.md`/`knownIssues.md` below; it means a block the archiver
            # can see but can never select (commonly an undated heading).
            print(f"       ❌ over budget at the archiver's floor ({f['floor_lines']} "
                  f"lines), and this file's rule declares no live state — every block here "
                  f"is history; one of them is sitting unmovable. Check for an entry whose "
                  f"date heading the archiver cannot parse.")
        elif over:
            print(f"       ⚠️  over budget on LIVE state alone (archiver's floor is "
                  f"{f['floor_lines']} lines) — this one needs editing, not archiving.")
    if c["ok"]:
        print("\n  ✅ Every hot file and every entry is inside its budget.\n")
    else:
        print("\n  → `just archive-memory` moves closed history; an oversized *entry* "
              "needs writing shorter.\n")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Per-entry line budget for the hot memory files.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-lines", type=int, default=None, metavar="N",
                    help=f"file budget to measure against (default {am.MAX_LINES})")
    args = ap.parse_args()
    c = check(ws_root(), args.max_lines)
    if args.json:
        print(json.dumps(c, indent=2, ensure_ascii=False))
    else:
        render(c)
    return 0 if c["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
