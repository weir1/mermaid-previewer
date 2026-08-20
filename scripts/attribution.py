#!/usr/bin/env python3
"""
attribution.py — attribution that admits what it is (PH10-T05, Goal G8).

F-05 (the 2026-07-30 audit): "session budget and DoD are self-reported" — a
free-text claim the engine trusted with nothing checking it. PH7-T02 closed
that for work credit. This closes the same hole for a NEW field before it
opens: `.ai/delegation/*.md`'s `## Attribution` `planned_by:` line is exactly
as free-text as the old DoD claim was, and until this module existed nothing
compared it to anything.

## Corroboration means "checked against an independent record," not "proven"

`.ai/models.yaml`'s own docstring is explicit that the running model cannot
be detected from this harness's environment — no env var, no settings field
(checked live for PH10-T01). So a claim can never be *proven* true. What it
CAN be is checked against a record that was NOT typed for this purpose:
`delegation.scaffold()` now logs an `attribution` decision-log entry the
moment it writes `planned_by:` into the template. `check_contract()`
compares the two. Three outcomes, never a bare pass/fail — the same shape
`conformance.py`/`health.py` already use for "this cannot be honestly
reduced to two states":

  * **corroborated** — the contract's claim matches the logged one.
  * **contradicted** — they disagree. FLAGGED, not silently accepted — the
    entire point of this module.
  * **unverifiable** — nothing was ever logged to check against (a contract
    scaffolded before this shipped, or created by hand outside
    `delegation.scaffold()`). Reported honestly as "cannot tell," never
    nudged toward either of the other two.

## Why `executed_by`/`reviewed_by` are not written back here

Traced through PH10-T04's already-proven threat model before writing any
code: the contract file also carries `## File allowlist`, and
`leash.active_contract()` re-parses it FRESH on every check. Any write path
that needs the contract file exempted from the leash's own allowlist (to
let the write land while a contract is active) would let an executor
hand-widen its own scope on the very next read — PH10-T04 already rejected
this for `reviewed_by`; the same reasoning applies to `executed_by`. Instead
`by_model()` answers "who actually worked on this" from the append-only
decision log — computed from what happened, not typed by the session it is
about.

## `by_model()` — the operator's own reason PH10 exists

`.ai/decision-log/` records every verdict but, until PH10-T05, not WHICH
model reached it. `decision_log.record()` now auto-fills `model` on every
entry (one change point, not one per caller — see that module). This groups
the credited-work subset of the log by that field: `source=="session_budget"`
and `action` starting with `"work-done "` is the exact shape
`session_budget.cmd_work`'s own `_log()` call already writes (confirmed
against this repo's own live log before writing the filter, not assumed).
Entries logged before this field existed report as `unrecorded` — a real,
countable bucket, never guessed into a name.

Usage:
  attribution.py check-contract "PH10-T02"    # is planned_by corroborated?
  attribution.py by-model [--days N]          # work credited, grouped by model
  attribution.py --json   (with any subcommand)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

STATUSES = ("corroborated", "contradicted", "unverifiable")
UNRECORDED = "unrecorded"


def ws_root() -> Path:
    import subprocess
    try:
        t = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                    stderr=subprocess.DEVNULL, text=True).strip()
        if t:
            return Path(t)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


# ────────────────────────────── contract corroboration ────────────────────

def check_contract(task: str, root: Path | None = None) -> dict:
    """Does the contract's static `planned_by:` line match the decision-log
    entry `delegation.scaffold()` wrote at the moment it was set?"""
    root = root or ws_root()
    v = {"task": task, "status": "unverifiable", "claimed": "", "logged": "", "reason": ""}

    import delegation
    path = delegation.contract_path(task, root)
    if not path.is_file():
        v["reason"] = f"no contract at {path} to check."
        return v
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        v["reason"] = f"contract unreadable ({exc})."
        return v

    sections = delegation.plan._sections(text)
    claimed = delegation._line_value(sections.get("Attribution", []), "planned_by:") or ""
    v["claimed"] = claimed

    import decision_log as dl
    logged_entries = [e for e in dl.read_entries(root=root)
                      if e.get("kind") == "attribution" and e.get("task") == task
                      and e.get("action") == "planned_by"]
    if not logged_entries:
        v["reason"] = (f"{task}'s contract claims planned_by={claimed!r}, but no decision-log "
                       "entry corroborates it — scaffolded before this check existed, or "
                       "created by hand. Not accepted as fact, not rejected either.")
        return v

    logged = logged_entries[0].get("model", "")
    v["logged"] = logged
    if not claimed or not logged or claimed != logged:
        v["status"] = "contradicted"
        v["reason"] = (f"{task}'s contract claims planned_by={claimed!r}, but the decision log "
                       f"recorded {logged!r} when the contract was scaffolded — these disagree.")
        return v

    v["status"] = "corroborated"
    v["reason"] = f"{task}'s planned_by={claimed!r} matches the decision log."
    return v


# ─────────────────────────────────── by model ──────────────────────────────

def by_model(root: Path | None = None, days: int | None = None) -> dict:
    """Work-credit decision-log entries, grouped by the model self-reported
    at the moment each was logged."""
    root = root or ws_root()
    import decision_log as dl
    entries = dl.read_entries(root=root, days=days)
    work = [e for e in entries
            if e.get("source") == "session_budget"
            and str(e.get("action", "")).startswith("work-done ")
            and e.get("decision") == "allow"]
    by: dict[str, list[str]] = {}
    for e in work:
        name = e.get("model") or UNRECORDED
        by.setdefault(name, []).append(e.get("task") or "(untitled)")
    return {"total": len(work), "by_model": by}


# ────────────────────────────────────── CLI ──────────────────────────────────

def _print_contract(v: dict) -> None:
    icon = {"corroborated": "✅", "contradicted": "🛑", "unverifiable": "❓"}[v["status"]]
    print(f"  {icon} {v['status'].upper()} — {v['reason']}")


def _print_by_model(v: dict) -> None:
    print(f"  🤖 WORK BY MODEL — {v['total']} credited task(s)")
    for name, tasks in sorted(v["by_model"].items()):
        label = "(none declared)" if name == UNRECORDED else name
        print(f"     • {label}: {len(tasks)} — {', '.join(tasks)}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Attribution corroboration (PH10-T05).")
    sub = ap.add_subparsers(dest="cmd")

    p_check = sub.add_parser("check-contract", help="is planned_by corroborated?")
    p_check.add_argument("task")
    p_check.add_argument("--json", action="store_true")

    p_by = sub.add_parser("by-model", help="work credited, grouped by declared model")
    p_by.add_argument("--days", type=int, default=0, metavar="N")
    p_by.add_argument("--json", action="store_true")

    args = ap.parse_args()
    root = ws_root()

    if args.cmd == "check-contract":
        v = check_contract(args.task, root)
        if args.json:
            print(json.dumps(v, indent=2))
        else:
            _print_contract(v)
        return 0 if v["status"] != "contradicted" else 1

    if args.cmd == "by-model":
        v = by_model(root, days=args.days or None)
        if args.json:
            print(json.dumps(v, indent=2))
        else:
            _print_by_model(v)
        return 0

    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
