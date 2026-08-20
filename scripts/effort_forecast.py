#!/usr/bin/env python3
"""
effort_forecast.py — how many more sessions this workspace's plan needs, with its basis.

`.ai/plan.md` §7, in the operator's own words: *"I want to know where I stand
and how much is left... how much effort remains to finish the goal — so I can
judge how much energy to spend, instead of building endlessly with no sense of
the finish line."* PH9-T08 answers "how much is done" as a count. It cannot
answer this: a percentage does not convert to "you can stop soon" without a
rate, and there has never been a rate anywhere in this repo.

## Where the rate comes from

`.ai/decision-log/*.jsonl` already tags every entry with the `session` that
was open when it was written, and `audit.build_sessions()` already groups
entries into one row per session. **Velocity is the mean count of credited
`work-done` entries per session, over the last `window` completed sessions** —
completed meaning *anchored* (it has real decision-log entries, distinguishing
it from a session-log row with none) and *not the one still open*, which is
unfinished and would understate itself every time this is run mid-session,
i.e. most of the time.

`is_work_credit()` — the one predicate for "was this entry a real, granted
credit" — is imported from `goal_progress.py` rather than re-filtered here a
second way; the two disagreeing about what counts is exactly the drift
`active_task()` was built to prevent across three older copies.

## Refuses, never guesses (design law 2)

- **Too little history.** Below `MIN_SESSIONS` completed sessions, a mean is
  noise wearing a decimal point — refused, not reported with false confidence.
- **Zero velocity.** 0 credited tasks/session over the window could mean the
  plan is finished (checked separately, via `goal_progress`) or that the
  workspace is stalled; reporting "0 sessions remaining" in the second case
  would be a lie in the encouraging direction, so this refuses instead.
- **No plan, or no goals.** Delegates straight to `goal_progress.progress()`'s
  own refusal — not re-implemented.

## Never pooled across workspaces (design law 1)

`session_velocity`/`forecast` take exactly one `root` and read only that
workspace's own `.ai/decision-log/` and `.ai/session-log/`. There is no
fleet-wide variant here; a fleet view (PH9-T12) is a roll-up of each
workspace's own self-report, never a recomputation over several at once.

Usage:
  effort_forecast.py                 # this workspace's forecast
  effort_forecast.py --json
  effort_forecast.py --window 5      # use the last 5 completed sessions instead of 10
"""

import argparse
import json
import statistics
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Below this many completed sessions, a mean is not a rate — it's a guess with
# a decimal point. Three is the smallest sample where "the last one was an
# outlier" is even a distinguishable question.
MIN_SESSIONS = 3
DEFAULT_WINDOW = 10


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _completed_sessions(root: Path) -> list[dict]:
    """Anchored sessions, oldest first, excluding the one still open.

    Delegates entirely to `audit.build_sessions()` — the one place this repo
    joins the decision log to session boundaries — rather than re-deriving
    session grouping here a second time.
    """
    import decision_log
    import audit
    entries = decision_log.read_entries(root=root)
    logs = audit.read_session_logs(root)
    state = audit.read_state(root)
    sessions, _unattributed = audit.build_sessions(entries, logs, state)
    return [s for s in sessions if s.get("has_anchor") and not s.get("is_current")]


def session_velocity(root: Path | None = None, window: int = DEFAULT_WINDOW) -> dict:
    """Credited `work-done` entries per completed session, over the last `window`.

    Pure read. Refuses (does not compute a mean) below `MIN_SESSIONS`.
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "tasks_per_session": None, "spread": None,
          "window": 0, "counts": []}
    try:
        import goal_progress
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"goal_progress.py unavailable ({exc})."
        return out

    try:
        completed = _completed_sessions(root)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"cannot read session history ({exc})."
        return out

    used = completed[-window:] if window else completed
    if len(used) < MIN_SESSIONS:
        out["reason"] = (f"only {len(used)} completed session(s) recorded — need at least "
                         f"{MIN_SESSIONS} to compute a rate worth trusting.")
        out["window"] = len(used)
        return out

    counts = [sum(1 for e in s["entries"] if goal_progress.is_work_credit(e)) for s in used]
    mean = statistics.mean(counts)
    spread = round(statistics.pstdev(counts)) if len(counts) > 1 else 0
    out.update(ok=True, tasks_per_session=round(mean, 1), spread=spread,
               window=len(used), counts=counts,
               reason=f"{round(mean, 1)} tasks/session over {len(used)} session(s), ±{spread}")
    return out


def forecast(root: Path | None = None, window: int = DEFAULT_WINDOW) -> dict:
    """Sessions remaining = open mapped tasks (PH9-T08) / velocity. Refuses, never guesses.

    Pure read — nothing here writes to any file. Kept side-effect free for the
    same reason `goal_progress.progress()` is: PH7-T09 is this repo's standing
    lesson about a read path that turns out to write.
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "sessions_remaining": None, "open_tasks": None,
          "basis": "", "velocity": None, "progress": None}

    v = session_velocity(root, window=window)
    out["velocity"] = v
    if not v["ok"]:
        out["reason"] = f"cannot forecast — {v['reason']}"
        return out

    try:
        import goal_progress
        prog = goal_progress.progress(root)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"goal_progress.py unavailable ({exc})."
        return out
    out["progress"] = prog
    if not prog["ok"]:
        out["reason"] = f"cannot forecast — {prog['reason']}"
        return out

    open_tasks = prog["totals"]["open"]
    out["open_tasks"] = open_tasks
    if open_tasks == 0:
        out.update(ok=True, sessions_remaining=0,
                   reason="no open mapped tasks — nothing left to forecast.")
        return out

    if v["tasks_per_session"] <= 0:
        out["reason"] = (f"cannot forecast — 0 tasks/session over the last {v['window']} "
                         "session(s); the workspace may be stalled rather than finished.")
        return out

    remaining, basis = _cost(open_tasks, v)
    out.update(ok=True, sessions_remaining=remaining, basis=basis,
               reason=f"≈{remaining} session(s) ({basis})")
    return out


def _cost(n_tasks: int, v: dict) -> tuple[int, str]:
    """`ceil(n_tasks / velocity)` + its basis string. The one place this division
    happens — shared by `forecast()` (real open-task count) and `price_tag()`
    (a hypothetical count), so the two can never independently drift on what is
    otherwise the identical arithmetic run twice.
    """
    import math
    remaining = math.ceil(n_tasks / v["tasks_per_session"])
    basis = f"{v['tasks_per_session']} tasks/session over {v['window']} sessions, ±{v['spread']}"
    return remaining, basis


def price_tag(n_tasks: int = 1, root: Path | None = None, window: int = DEFAULT_WINDOW) -> dict:
    """Sessions cost of `n_tasks` more ledger tasks, at this workspace's own measured rate. (PH9-T10)

    `.ai/plan.md` §7/G5: *"nothing gets silently added to the scope... priced in
    sessions."* This is that price — the same `_cost()` division `forecast()`
    already runs for "open tasks remaining" (PH9-T08's denominator), just
    applied to a hypothetical `n_tasks` instead of `goal_progress.progress()`'s
    real count. Refuses under the identical two conditions `forecast()` does:
    too little session history, or exactly 0 tasks/session over the window (the
    workspace may be stalled, not finished) — a price built on a rate that
    isn't trustworthy would be worse than no price at all.

    This is the primitive PH9-T05 (off-plan request notices — named additional
    work, not yet built) is expected to call directly with `n_tasks=1` for a
    single ad-hoc request. Kept a stable, independently pinned signature for
    exactly that reason, so T05 has something fixed to build against.
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "n_tasks": n_tasks, "sessions": None, "basis": ""}

    v = session_velocity(root, window=window)
    if not v["ok"]:
        out["reason"] = f"cannot price — {v['reason']}"
        return out
    if v["tasks_per_session"] <= 0:
        out["reason"] = (f"cannot price — 0 tasks/session over the last {v['window']} "
                         "session(s); the workspace may be stalled rather than finished.")
        return out

    sessions, basis = _cost(n_tasks, v)
    out.update(ok=True, sessions=sessions, basis=basis,
               reason=f"≈{sessions} session(s), assuming {n_tasks} task(s) at {basis}")
    return out


def priced_proposals(root: Path | None = None, window: int = DEFAULT_WINDOW) -> dict:
    """Every *open* proposal in `.ai/plan.md`, each priced at `n_tasks=1`. (PH9-T10)

    `n_tasks=1` is the honest floor — pricing a proposal by guessing how many
    tasks the eventual feature will actually take would be a specific-looking
    number built on nothing measured, exactly what design law 2 forbids. Every
    price is labelled with that assumption, never shown as a bare integer.

    Reads `plan_workspace.validate()['proposals']` rather than re-parsing
    `.ai/plan.md` a second way — that module keeps sole ownership of the plan's
    text, the same reason `goal_progress.py` reads its `['goals']` instead of
    parsing goals itself.

    Zero unresolved proposals is `ok=True` with an empty list: "nothing is
    pending" is a real, immediate answer that must not wait on session history
    existing. A refusal only fires once there is something to actually price.
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "proposals": []}

    try:
        import plan_workspace
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"plan_workspace.py unavailable ({exc})."
        return out

    v = plan_workspace.validate(root=root)
    if not v["exists"]:
        out["reason"] = v["reason"]
        return out

    open_proposals = [p for p in v["proposals"] if p["status"] == "open"]
    if not open_proposals:
        out.update(ok=True, reason="no unresolved proposals.")
        return out

    price = price_tag(1, root, window=window)
    if not price["ok"]:
        out["reason"] = price["reason"]
        return out

    out.update(ok=True, proposals=[dict(p, price=price) for p in open_proposals],
               reason=f"{len(open_proposals)} unresolved proposal(s), each {price['reason']}")
    return out


def render(f: dict) -> None:
    if not f["ok"]:
        print(f"🛑 Effort forecast unavailable — {f['reason']}")
        return
    if f["sessions_remaining"] == 0:
        print(f"  🏁 {f['reason']}")
        return
    print(f"  ⏳ REMAINING EFFORT — {f['reason']}")
    print(f"     {f['open_tasks']} open mapped task(s) ÷ {f['basis']}")


def render_price(pp: dict) -> None:
    if not pp["ok"]:
        print(f"🛑 Scope pricing unavailable — {pp['reason']}")
        return
    if not pp["proposals"]:
        print(f"  💲 SCOPE PRICE — {pp['reason']}")
        return
    print(f"  💲 SCOPE PRICE — {pp['reason']}")
    for p in pp["proposals"]:
        print(f"     {p['id']} — {p['text'][:64]}")
        print(f"          {p['price']['reason']}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="How many more sessions this workspace's plan needs, with its basis.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--window", type=int, default=DEFAULT_WINDOW,
                    help=f"completed sessions to average over (default {DEFAULT_WINDOW}).")
    ap.add_argument("--price", action="store_true",
                    help="price every unresolved .ai/plan.md proposal in sessions, "
                         "instead of forecasting the whole plan.")
    args = ap.parse_args()

    if args.price:
        pp = priced_proposals(ws_root(), window=args.window)
        if args.json:
            print(json.dumps(pp, indent=2, default=str))
        else:
            render_price(pp)
        return 0 if pp["ok"] else 1

    f = forecast(ws_root(), window=args.window)
    if args.json:
        print(json.dumps(f, indent=2, default=str))
    else:
        render(f)
    return 0 if f["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
