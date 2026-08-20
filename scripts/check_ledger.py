#!/usr/bin/env python3
"""check_ledger.py — sub-second ledger consistency, before the 146s suite (PH27-T04).

`task_ledger` already knows every shape of ledger disagreement this workspace
checks for — `disagreements()` for a prose/checkbox contradiction (PH16-T41) and
`active_task_report()` (which itself calls `_orphan_markers()`) plus
`orphan_warning()` for a declared `(In Progress)` marker nothing will ever bind
to (PH16-T32/T39). All four run in well under a second on the files they already
read. The only place any of them actually ran was buried inside the ~146s full
test suite (`tests/test_task_ledger.py`), so learning "does the ledger still
agree with itself" cost two minutes even for a doc-only credit — paid twice in
one evening by the session that filed this task (PH25-T04's own crediting).

## It defines no rule of its own

This module parses nothing and matches no regex. Every predicate is a direct
call into `task_ledger`'s existing public functions; `tests/test_check_ledger.py`
asserts that by scanning this file's own source, the same pattern
`task_transition.py` / `tests/test_task_transition.py` already established for
the same reason — a second implementation of a ledger rule drifts, and the
drift ships to every workspace this kernel deploys to.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_ledger as tl  # noqa: E402


def check(root: Path | None = None) -> dict:
    """Everything `task_ledger` already knows how to disagree about, in one pass.

    Returns `{"ok": bool, "disagreements": [...], "orphans": [...], "lines": [...]}`
    — `disagreements` and `orphans` are `task_ledger`'s own records, unmodified;
    `lines` is `orphan_warning()`'s rendered text, so the CLI and any other
    caller read the same words `verify-safe`'s output already uses.
    """
    root = root or tl.ws_root()
    disagreements = tl.disagreements(root)
    report = tl.active_task_report(root)
    orphans = report["orphans"]
    return {
        "ok": not disagreements and not orphans,
        "disagreements": disagreements,
        "orphans": orphans,
        "lines": tl.orphan_warning(report),
    }


def render(c: dict) -> None:
    if c["ok"]:
        print("✅ ledger consistent — activeContext.md and tasks.md agree, no orphaned markers.")
        return
    for b in c["disagreements"]:
        print(f"❌ {b['task']} line {b['prose_line']} vs {b['ledger_line']} "
              f"({b['kind']}): {b['detail']}")
    for line in c["lines"]:
        print(line)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sub-second ledger consistency check (PH27-T04) — no rule of "
                    "its own, a CLI surface over task_ledger's existing functions.")
    ap.add_argument("--json", action="store_true", help="machine-readable report")
    args = ap.parse_args()
    c = check()
    if args.json:
        print(json.dumps(c, indent=2, ensure_ascii=False))
    else:
        render(c)
    return 0 if c["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
