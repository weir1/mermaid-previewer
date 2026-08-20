#!/usr/bin/env python3
"""
goal_progress.py — how far along is each goal, computed rather than claimed.

`.ai/plan.md` has held the goals since PH9-T01 and the ledgers have held the
tasks since v3.3. **Nothing joined them.** So "how much of G4 is done?" — the
operator's actual question, the one behind *"instead of endless building"* — could
only ever be answered by an AI typing a number into a summary paragraph.

This module is that join: plan goals × ledger tasks × the credit record.

## Where the number comes from

Completion is a **count over a task set**. There is no percentage field anywhere
in this repo to read, and none is written — `progress()` derives every figure
from the tasks that resolve to a goal, so a hand-written completion figure has
nowhere to be injected. That is the structural half of design law 2 ("measured,
never asserted").

## Two meanings of "done", reported separately

A task is **credited** when `.ai/decision-log/` holds a `work-done <id>` allow
for it. That is a real credential: `session_budget.verify_work_claim` demands a
declared DoD, a written plan when the task is `[complex]`, and an open validation
gate whose evidence names that task. Nobody can type it.

A task is **asserted** when its ledger line says `(Complete)` and no such credit
exists. That is someone's word.

Both count toward done, and the split is always printed. Counting only credits
would be honest about provenance and wrong about reality — `work-done` did not
enforce anything until 2026-08-01, so every task from PH4–PH6 would read as
outstanding and G1 would report 0%. Counting only ledger status is the assertion
the whole phase exists to remove. Reporting both, labelled, is the only version
where the number is useful *and* its trustworthiness is visible: a goal sitting
at "8 done (1 credited · 7 asserted)" is telling you exactly how much to believe
it.

## Three answers that are not a percentage

- **unmapped** — the task declares no `Goal:`. A real answer, not a gap to fill
  by guessing. Most of this kernel's history predates the goals and honestly
  serves none of them.
- **dangling** — the task declares `Goal: G9` and the plan stops at G7. Reported
  against the task; it never creates a goal, because a typo that invents one
  would silently remove the task from every total.
- **no tasks** — a declared goal that nothing serves has no completion. It
  reports "no tasks declared", not 0% and not 100%; both would be inventions.

Usage:
  goal_progress.py                 # per-goal standing
  goal_progress.py --json
  goal_progress.py --unmapped      # list every task that maps to no goal
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Default idle threshold (days) before an open goal is named "stalled" (PH9-T12).
# Long enough that a normal multi-day gap between sessions doesn't false-positive;
# short enough to actually catch a goal quietly dropped.
STALL_DAYS = 14

# Ledger statuses that assert a task is finished.
DONE_STATUSES = {"Complete", "Done"}
# Decision-log decisions that record work credit actually being granted.
CREDIT_DECISIONS = {"allow", "gate_override"}


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def is_work_credit(e: dict) -> bool:
    """Is this decision-log entry a real, granted `work-done` credit?

    The one test for "was this task actually credited", shared by `credited_tasks()`
    here and by `effort_forecast.py`'s per-session velocity count (PH9-T09) — a
    second private copy of this filter is exactly how two counters start
    disagreeing, the drift `active_task()` exists to prevent.

    Overrides count. `work-done --override` is a *logged, reasoned* decision that
    the exception was genuine, which is a different thing from an unverified
    claim; excluding it would make the honest exception indistinguishable from
    never having done the work, and would push people toward not logging it.
    """
    return (e.get("source") == "session_budget"
            and str(e.get("action", "")).startswith("work-done")
            and e.get("decision") in CREDIT_DECISIONS)


def credited_tasks(root: Path | None = None) -> set[str]:
    """Task ids that were granted work credit, from `.ai/decision-log/`.

    Read through `decision_log.read_entries` rather than re-parsing the JSONL.
    `just audit` already summarises this log, and a second private reader is how
    two counters start disagreeing — the drift `active_task()` exists to prevent.

    A read failure returns the empty set, which under-reports credit rather than
    inventing it — the safe direction for a number whose whole purpose is to be
    trustworthy.
    """
    try:
        import decision_log
        entries = decision_log.read_entries(root=root or ws_root())
    except Exception:  # noqa: BLE001
        return set()
    out = set()
    for e in entries:
        if not is_work_credit(e):
            continue
        task = str(e.get("task") or "").strip()
        if task:
            out.add(task)
    return out


def _blank_goal(gid: str, title: str) -> dict:
    return {"id": gid, "title": title, "tasks": [], "total": 0, "done": 0,
            "credited": 0, "asserted": 0, "open": 0, "percent": None, "met": False}


def progress(root: Path | None = None) -> dict:
    """Join the plan's goals to the ledger's tasks. Pure read — writes nothing.

    Kept side-effect free because `session-start` (PH9-T04) renders it on every
    session. PH7-T09 is the standing lesson in this repo about read paths that
    write.
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "goals": [], "unmapped": [], "dangling": [],
           "totals": {"tasks": 0, "mapped": 0, "done": 0, "credited": 0,
                      "asserted": 0, "open": 0, "percent": None},
           "plan_status": None, "basis": ""}

    try:
        import plan_workspace
        import task_ledger
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"cannot read the plan or the ledger ({exc})."
        return out

    plan = plan_workspace.validate(root=root)
    out["plan_status"] = plan["status"]
    if not plan["exists"]:
        out["reason"] = plan["reason"]
        return out
    if not plan["goals"]:
        out["reason"] = ("the plan declares no goals — nothing to measure progress "
                         "against. Add `- G1 — <title> — <how you know it is done>`.")
        return out

    goals = {g["id"]: _blank_goal(g["id"], g["title"]) for g in plan["goals"]}
    tasks = task_ledger.all_tasks(root=root)
    credited = credited_tasks(root)

    for t in tasks:
        out["totals"]["tasks"] += 1
        gid = t.get("goal") or ""
        if not gid:
            out["unmapped"].append({"task": t["task"], "title": t["title"],
                                    "status": t["status"], "source": t.get("source", "")})
            continue
        if gid not in goals:
            out["dangling"].append({"task": t["task"], "goal": gid,
                                    "source": t.get("source", "")})
            continue

        is_credited = t["task"] in credited
        is_asserted = (not is_credited) and t["status"] in DONE_STATUSES
        g = goals[gid]
        g["tasks"].append({"task": t["task"], "status": t["status"],
                           "credited": is_credited, "done": is_credited or is_asserted})
        g["total"] += 1
        if is_credited:
            g["credited"] += 1
        if is_asserted:
            g["asserted"] += 1
        out["totals"]["mapped"] += 1

    for g in goals.values():
        g["done"] = g["credited"] + g["asserted"]
        g["open"] = g["total"] - g["done"]
        # A goal nothing serves has no completion. 0% would claim work is
        # outstanding and 100% would claim it is finished; both are inventions.
        g["percent"] = round(100 * g["done"] / g["total"]) if g["total"] else None
        g["met"] = g["total"] > 0 and g["done"] == g["total"]

    out["goals"] = [goals[g["id"]] for g in plan["goals"]]
    for key in ("done", "credited", "asserted", "open"):
        out["totals"][key] = sum(g[key] for g in out["goals"])
    mapped = out["totals"]["mapped"]
    out["totals"]["percent"] = round(100 * out["totals"]["done"] / mapped) if mapped else None
    out["basis"] = (
        f"{mapped} task(s) mapped to {len(out['goals'])} goal(s) across the ledger; "
        f"{out['totals']['credited']} credited in .ai/decision-log/, "
        f"{out['totals']['asserted']} asserted by a ledger `(Complete)`; "
        f"{len(out['unmapped'])} task(s) declare no goal.")
    out["ok"] = True
    out["reason"] = out["basis"]
    return out


def all_met(p: dict) -> bool:
    """Does `progress()`'s own output show every declared goal met? (PH9-T11)

    The done-signal `session_open.opening()` branches on: "nothing left that
    the plan asked for." Takes the already-computed dict, not a `root` —
    every caller that needs this already has a fresh `progress()` in hand, and
    a second `root` parameter would invite a second, possibly-stale read of
    the same thing rather than reusing what was just computed.

    False whenever there is nothing to be "all met" about: `progress()`
    refused (no plan, no goals) or the plan declares goals but none of them
    have any tasks mapped yet (`total == 0` -> `met` is already False by
    `progress()`'s own construction, so a freshly-scaffolded plan can never
    read as done).
    """
    return bool(p.get("ok")) and bool(p.get("goals")) and all(g["met"] for g in p["goals"])


def progress_excluding(root: Path | None = None, exclude_tasks: set[str] | None = None) -> dict:
    """`progress()`, with `exclude_tasks` forced back to "not done". (PH9-T06)

    The "before" half of a session-close comparison: *"what would the goal
    numbers say if this session's own credited work hadn't happened yet"*. Built
    on top of `progress()`'s own already-computed per-task list rather than
    re-deriving from the ledger and decision log a second way — the exact drift
    `active_task()` exists to prevent, applied to a new field.

    Deliberately not a reconstruction of the ledger's historical text: a task
    *not* in `exclude_tasks` keeps its real, current status untouched, because
    this session did not touch it and nothing about it should move. Excluding a
    task forces both its `credited` and `done` flags false regardless of whether
    the ledger already reads `(Complete)` for it — the point is to subtract
    *this session's* contribution, not to guess what the file looked like this
    morning.

    Pure read; an empty `exclude_tasks` returns `progress()` unchanged.
    """
    p = progress(root)
    if not p["ok"] or not exclude_tasks:
        return p
    import copy
    q = copy.deepcopy(p)
    for g in q["goals"]:
        for t in g["tasks"]:
            if t["task"] in exclude_tasks:
                t["credited"] = False
                t["done"] = False
        g["credited"] = sum(1 for t in g["tasks"] if t["credited"])
        g["done"] = sum(1 for t in g["tasks"] if t["done"])
        g["asserted"] = g["done"] - g["credited"]
        g["open"] = g["total"] - g["done"]
        g["percent"] = round(100 * g["done"] / g["total"]) if g["total"] else None
        g["met"] = g["total"] > 0 and g["done"] == g["total"]
    for key in ("done", "credited", "asserted", "open"):
        q["totals"][key] = sum(g[key] for g in q["goals"])
    mapped = q["totals"]["mapped"]
    q["totals"]["percent"] = round(100 * q["totals"]["done"] / mapped) if mapped else None
    return q


def _credit_times(root: Path) -> dict[str, str]:
    """task id -> latest work-done credit timestamp, from `.ai/decision-log/`.

    Same read path as `credited_tasks()` (`decision_log.read_entries`, the one
    filter `is_work_credit()`), just keeping the timestamp instead of throwing
    it away. A read failure returns empty — under-reports timing rather than
    inventing it, the same safe direction `credited_tasks()` already takes.
    """
    try:
        import decision_log
        entries = decision_log.read_entries(root=root)
    except Exception:  # noqa: BLE001
        return {}
    out: dict[str, str] = {}
    for e in entries:
        if not is_work_credit(e):
            continue
        task = str(e.get("task") or "").strip()
        ts = e.get("ts")
        if task and ts and (task not in out or str(ts) > out[task]):
            out[task] = str(ts)
    return out


def _parse_ts(raw: str):
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def stalled_goals(root: Path | None = None, threshold_days: int = STALL_DAYS) -> dict:
    """Which open goals have gone quiet, and for how long. (PH9-T12)

    Only **open** goals with at least one mapped task are candidates — a met
    goal is done, not stalled, and a goal with zero mapped tasks already has
    its own honest answer ("no tasks declared") rather than an idle clock.

    Idle time is measured from the latest credited `work-done` for a task
    mapped to that goal. A goal never credited falls back to the plan's own
    `agreed_at` — it has been sitting since the plan was written. Where
    neither exists, this refuses to estimate (design law 2) rather than
    reporting a number with nothing behind it.

    Pure read, same contract as `progress()` (PH7-T09 is this repo's standing
    lesson about a read path that turns out to write).
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "threshold_days": threshold_days, "goals": []}

    p = progress(root)
    if not p["ok"]:
        out["reason"] = p["reason"]
        return out

    try:
        import plan_workspace
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"plan_workspace.py unavailable ({exc})."
        return out
    agreed_at = plan_workspace.validate(root=root).get("agreed_at")

    times = _credit_times(root)
    now = datetime.now(timezone.utc)

    for g in p["goals"]:
        if g["met"] or g["total"] == 0:
            continue
        credited_ts = [times[t["task"]] for t in g["tasks"]
                       if t["credited"] and t["task"] in times]
        last = max(credited_ts) if credited_ts else agreed_at
        entry = {"id": g["id"], "title": g["title"], "last_touched": last,
                 "idle_days": None, "stalled": False, "basis": ""}

        dt = _parse_ts(last) if last else None
        if dt is None:
            entry["basis"] = ("no credited work and no plan agreed_at — cannot compute "
                              "idle time" if not last else f"unparseable timestamp ({last!r})")
        else:
            idle = round((now - dt).total_seconds() / 86400)
            entry.update(idle_days=idle, stalled=idle >= threshold_days,
                        basis=(f"last credited work {idle}d ago" if credited_ts
                               else f"plan agreed {idle}d ago, no credited work yet"))
        out["goals"].append(entry)

    out["ok"] = True
    stalled = [g for g in out["goals"] if g["stalled"]]
    out["reason"] = (f"{len(stalled)} of {len(out['goals'])} open goal(s) stalled "
                     f"(≥{threshold_days}d idle)." if out["goals"]
                     else "no open goals to check — every goal is either met or has no tasks yet.")
    return out


def render_stalled(s: dict) -> None:
    if not s["ok"]:
        print(f"🛑 Stalled-goal check unavailable — {s['reason']}")
        return
    print(f"  🐌 STALLED GOALS — idle ≥{s['threshold_days']}d")
    stalled = [g for g in s["goals"] if g["stalled"]]
    if not stalled:
        print(f"     {s['reason']}")
        return
    for g in stalled:
        print(f"     ⚠️  {g['id']} — idle {g['idle_days']}d — {g['title'][:60]}")
        print(f"          {g['basis']}")


def _bar(percent: int | None, width: int = 20) -> str:
    if percent is None:
        return "·" * width
    filled = round(width * percent / 100)
    return "█" * filled + "░" * (width - filled)


def render(p: dict, show_unmapped: bool = False) -> None:
    if not p["ok"]:
        print(f"🛑 Goal progress unavailable — {p['reason']}")
        return

    print("  🎯 GOAL PROGRESS — computed from the ledger, not asserted")
    for g in p["goals"]:
        if g["total"] == 0:
            print(f"  {g['id']} ·  no tasks declared  · {g['title'][:60]}")
            continue
        mark = "✅" if g["met"] else "  "
        print(f"  {mark}{g['id']} {_bar(g['percent'])} {g['percent']:>3}%  "
              f"{g['done']}/{g['total']} done "
              f"({g['credited']} credited · {g['asserted']} asserted)")
        print(f"       {g['title'][:76]}")

    t = p["totals"]
    if t["percent"] is not None:
        print(f"\n     Overall: {t['done']}/{t['mapped']} mapped tasks done ({t['percent']}%) "
              f"— {t['credited']} credited · {t['asserted']} asserted")
    if p["dangling"]:
        print("\n  ⚠️  Tasks naming a goal that does not exist:")
        for d in p["dangling"]:
            print(f"       {d['task']} → {d['goal']} ({d['source']})")
    if p["unmapped"]:
        print(f"\n     {len(p['unmapped'])} task(s) declare no goal "
              f"— `--unmapped` to list them.")
        if show_unmapped:
            for u in p["unmapped"]:
                status = u["status"] or "—"
                print(f"       {u['task']:<10} {status:<9} {u['title'][:60]}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Per-goal completion, computed from the ledger and the credit record.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--unmapped", action="store_true",
                    help="list every task that declares no goal.")
    ap.add_argument("--stalled", action="store_true",
                    help="report open goals idle past the threshold instead of full progress.")
    ap.add_argument("--threshold", type=int, default=STALL_DAYS,
                    help=f"idle days before an open goal counts as stalled (default {STALL_DAYS}).")
    args = ap.parse_args()

    if args.stalled:
        s = stalled_goals(ws_root(), threshold_days=args.threshold)
        if args.json:
            print(json.dumps(s, indent=2, default=str))
        else:
            render_stalled(s)
        return 0 if s["ok"] else 1

    p = progress(ws_root())
    if args.json:
        print(json.dumps(p, indent=2))
    else:
        render(p, show_unmapped=args.unmapped)
    return 0 if p["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
