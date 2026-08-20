#!/usr/bin/env python3
"""
session_close.py — what finished this session, and how the goals moved (PH9-T06).

The mirror of `session_open.py` (PH9-T04). Operator's own words, `.ai/plan.md`
§6: *"at the end of each session also explain in simple english language what
task has been finish & how we are moving forward with our goals."* Shape from
the same section: *"PH9-T02 done → G2 29% → 43%, ≈3 sessions remaining."*

## Where "before" and "after" come from

`.ai/decision-log/` tags every entry with the session key that was open when it
was written (`decision_log.session_key`), and a `work-done` credit is tagged the
same way — so *this session's credited tasks* is exactly the set of task ids
credited under the current session key, filtered through the one shared
predicate `goal_progress.is_work_credit` (never a third private copy of "was
this a real credit", after `effort_forecast.py` already reused it once).

"After" is `goal_progress.progress()` — the real, current state. "Before" is
`goal_progress.progress_excluding()` with this session's own credits subtracted
back out — not a reconstruction of the ledger's historical text, just this
session's contribution removed. A task this session did not touch keeps its
real status in both views, because nothing about it moved.

## Write-back is a separate step, on purpose

`close_report()` stays pure read, the same discipline `goal_progress.py` and
`effort_forecast.py` follow (PH7-T09: a report that also writes is a hook
re-fire away from corrupting state). `write_back()` is the one function that
touches `.ai/plan.md`, and it is scoped to goals `goal_progress` already says
are `met` that the plan does not yet say are `marked_met` — computed from the
after-state, not limited to only the goals this session's own credits touched,
so a goal that quietly became complete in an earlier, un-closed session is
still caught. That is the "so it cannot drift from reality" half of the DoD.

Usage:
  session_close.py                  # report only (read-only)
  session_close.py --apply          # report, then write back any newly-met goals
  session_close.py --json
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def close_report(root: Path | None = None) -> dict:
    """What this session credited and how the goals moved. Pure read.

    Always `ok: True` once a plan and its goals can be read — a maintenance
    session that credited nothing is a real, reportable outcome, not a
    refusal. Refuses only when the plan machinery, the plan itself, or its
    goals cannot be read at all (mirroring `goal_progress.progress()`).
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "session": "", "credited": [],
          "goal_moves": [], "unmapped_credits": [], "newly_met": [],
          "forecast": None, "plan_status": None, "after": None, "before": None}
    try:
        import decision_log
        import effort_forecast
        import goal_progress
        import plan_workspace
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"cannot read the plan machinery ({exc})."
        return out

    plan = plan_workspace.validate(root=root)
    out["plan_status"] = plan["status"]
    if not plan["exists"]:
        out["reason"] = plan["reason"]
        return out

    after = goal_progress.progress(root)
    out["after"] = after
    if not after["ok"]:
        out["reason"] = after["reason"]
        return out
    out["ok"] = True

    # Goals that are complete but the plan has not yet been told — regardless
    # of whether *this* session is the one that finished them. A goal-progress
    # "met" is always computed, never asserted, so acting on it here is the
    # same trust level `verify-safe` writing `evidence.json` already has.
    plan_goal = {g["id"]: g for g in plan["goals"]}
    out["newly_met"] = [g["id"] for g in after["goals"]
                        if g["met"] and not plan_goal.get(g["id"], {}).get("marked_met")]

    out["forecast"] = effort_forecast.forecast(root)

    session = decision_log.session_key(root)
    out["session"] = session
    if not session:
        out["reason"] = "no active session recorded — nothing credited this run."
        return out

    entries = decision_log.read_entries(root)
    credited_now = sorted({
        e.get("task") for e in entries
        if e.get("session") == session and goal_progress.is_work_credit(e) and e.get("task")
    })
    out["credited"] = credited_now
    if not credited_now:
        out["reason"] = "no task credited this session."
        return out

    before = goal_progress.progress_excluding(root, set(credited_now))
    out["before"] = before
    # Titles for the plain-English rendering (PH9-T17). Read through
    # `task_ledger`, the one resolver `work-done` itself uses — never a second
    # scan of the Markdown, which is how a ledger id and its title drift apart.
    try:
        import task_ledger
        task_titles = {t["task"]: t.get("title", "") for t in task_ledger.all_tasks(root=root)}
    except Exception:  # noqa: BLE001
        task_titles = {}
    task_goal = {t["task"]: g["id"] for g in after["goals"] for t in g["tasks"]}
    before_by_id = {g["id"]: g for g in before["goals"]}
    after_by_id = {g["id"]: g for g in after["goals"]}
    for tid in credited_now:
        gid = task_goal.get(tid)
        if not gid:
            out["unmapped_credits"].append(tid)
            continue
        b, a = before_by_id[gid], after_by_id[gid]
        out["goal_moves"].append({
            "task": tid, "task_title": task_titles.get(tid, ""),
            "goal": gid, "goal_title": a["title"],
            "before_percent": b["percent"], "after_percent": a["percent"],
            "before_done": b["done"], "after_done": a["done"], "total": a["total"],
        })
    out["reason"] = f"{len(credited_now)} task(s) credited this session."
    return out


def write_back(root: Path | None = None, report: dict | None = None) -> dict:
    """Stamp `report["newly_met"]` goals in `.ai/plan.md`. The one write path.

    Kept out of `close_report()` on purpose (PH7-T09) — a caller that only
    wants the report never triggers a write by calling this function too.
    """
    root = root or ws_root()
    if not report or not report.get("ok") or not report.get("newly_met"):
        return {"ok": True, "marked": [], "reason": "nothing to mark."}
    import plan_workspace
    return plan_workspace.mark_goals_met(root, set(report["newly_met"]))


def plain_close_lines(r: dict) -> list[str]:
    """What finished and how it moved the goals, for a human reader (PH9-T17).

    The mirror of `session_open.plain_lines`, and it borrows that module's
    `_headline` rather than growing a second notion of "the readable half of a
    title" — the same collapse `_uncommented()` performed for comment-stripping
    after this repo learned that lesson the expensive way.

    One caveat this cannot fix from here: `session-end` writes to stdout inside
    a command the AI runs, and that output is not reliably shown to the user the
    way a `SessionStart` hook's `systemMessage` is. Until a close-side channel
    exists, the AI relaying this text is what actually delivers it.
    """
    import session_open
    head = session_open._headline

    if not r["ok"]:
        return ["🏁 Could not work out what this session finished "
                f"({r.get('reason', 'unknown')})."]

    out = ["🏁 WHAT THIS SESSION FINISHED"]
    if not r["credited"]:
        out.append("   Nothing was completed and credited this session.")
    for m in r["goal_moves"]:
        out += session_open._wrap(f"• {head(m['task_title'] or m['task'])}", "   ", "     ")
        before = m["before_percent"]
        after = m["after_percent"]
        moved = (f"now {after}% done ({m['after_done']} of {m['total']} pieces)"
                 if after is not None else "progress unchanged")
        if before is not None and after is not None and after != before:
            moved = (f"moved from {before}% to {after}% "
                     f"({m['after_done']} of {m['total']} pieces)")
        out += session_open._wrap(
            f"Moves forward: {head(m['goal_title'])} — {moved}", "     ", "       ")

    if r["unmapped_credits"]:
        out.append("   Also finished, but not linked to any goal yet: "
                   f"{len(r['unmapped_credits'])} item(s).")

    if r["newly_met"]:
        out.append("")
        out.append("🎉 A goal was completed this session:")
        for gid in r["newly_met"]:
            title = next((m["goal_title"] for m in r["goal_moves"] if m["goal"] == gid), gid)
            out += session_open._wrap(f"✅ {head(title)}", "   ", "      ")

    fc = r.get("forecast")
    if fc and fc.get("ok") and fc.get("sessions_remaining") is not None:
        out += ["", f"📊 Roughly {fc['sessions_remaining']} more working session(s) "
                    "to finish what is left."]
    return out


def render_close_plain(r: dict) -> None:
    print("\n".join(plain_close_lines(r)))


def render_close(r: dict) -> None:
    if not r["ok"]:
        print(f"  🛑 Session close report unavailable — {r['reason']}")
        return

    print("  🏁 SESSION CLOSE — what finished, and how the goals moved")
    if not r["credited"]:
        print(f"     {r['reason']}")
    for m in r["goal_moves"]:
        before = f"{m['before_percent']}%" if m["before_percent"] is not None else "—"
        after = f"{m['after_percent']}%" if m["after_percent"] is not None else "—"
        print(f"     {m['task']} done → {m['goal']} {before} → {after} "
              f"({m['after_done']}/{m['total']} done) — {m['goal_title'][:60]}")
    if r["unmapped_credits"]:
        print(f"     credited but unmapped (no Goal:): {', '.join(r['unmapped_credits'])}")

    fc = r.get("forecast")
    if fc and fc["ok"]:
        print(f"     {fc['reason']}")

    if r["newly_met"]:
        print(f"     ✅ goal(s) met: {', '.join(r['newly_met'])} — recorded in .ai/plan.md.")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="What this session credited and how the goals moved.")
    ap.add_argument("--apply", action="store_true",
                    help="also write newly-met goals back to .ai/plan.md (default: report only).")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = ws_root()
    r = close_report(root)
    wb = {"ok": True, "marked": [], "reason": "not applied — pass --apply to write back."}
    if args.apply:
        wb = write_back(root, r)
        r["write_back"] = wb

    if args.json:
        print(json.dumps(r, indent=2, default=str))
    else:
        render_close(r)
        if args.apply and wb["marked"]:
            print(f"     📝 .ai/plan.md updated — {wb['reason']}")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
