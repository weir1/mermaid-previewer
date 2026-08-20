#!/usr/bin/env python3
"""
session_budget.py — enforce the per-session work budget: 2 work tasks + 3 closure.

Why: keeps a single AI session short and high-signal so it stays well under the
context limit, and reminds you to stop and start a FRESH session instead of
letting one session sprawl (which burns context + usage and degrades quality).

Budget:
  • 2 WORK tasks   — real tasks from .ai/docs/tasks.md / activeContext.md
  • 3 CLOSURE tasks — git-push · docs (memory/changelog update) · issues (triage)

## Work credit is EARNED, not claimed (PH7-T02, audit finding F-05)

`work` used to accept any string and increment the counter — a typo, a task that
does not exist, or a task nobody worked on all counted. The DoD convention that
`.ai/docs/tasks.md` calls "the concrete acceptance test that must pass before it
counts as done" was prose nothing ever read, so the budget measured *assertions*.

Three conditions must now hold before the counter moves:

  1. the task is **declared** in a ledger (`activeContext.md` / `tasks.md`) —
     see `task_ledger.py`;
  2. that declaration carries a **`DoD:` line** — a task with no stated
     acceptance test cannot be shown to have met it;
  3. the **validation gate is open AND its evidence names this task**.

Condition 3 is deliberately stricter than `just gate`. The gate tolerates an
empty `task_id` (a maintenance run legitimately covers no task), but "evidence
that names no task" is not proof *this* task was validated — accepting it would
reopen F-05 through the back door. So the evidence must carry the task id, which
means declaring the task `(In Progress)` in activeContext.md *before* running
`just verify-safe`. That ordering is the point: say what you are working on,
validate it, then claim it.

`--override "reason"` exists for the honest exception (a docs-only task with no
runnable gate). It demands a written reason and records an `override` entry in
`.ai/decision-log/` — the same no-silent-bypass rule the validation gate follows.

## `close git-push` requires a self-review (PH7-T04)

The one closure step with a real external side effect had no requirement to have
*looked* at what was going out. It now refuses unless `.ai/reviews/` holds a
review whose recorded diff hash equals the session diff's — see
`self_review.py`, which explains why the record is bound to content rather than
to a promise. Same override rule: a written reason, logged, never silent.

`start` stamps `head_at_start` so the review covers the session's whole diff and
not merely what is still unpushed.

## `start` will not silently discard earned credit (PH7-T09)

`start` used to reset unconditionally. That was harmless while the counter was
self-reported, but PH7-T02 made it a *credential* and PH7-T04 made `head_at_start`
the base of the self-review diff — so a reset fired mid-session destroys a credit
that can only be restored through a logged override, and moves the review base
forward, which lets `close git-push` be satisfied by a review of **less than what
is actually pushed**. The counter loss is loud; the narrowed review is invisible.

It happened three times (knownIssues.md), the last one unprompted: Claude Code
fires `SessionStart` on resume and compaction as well as on a genuinely new
session, and the hook is registered with no matcher, so a live session re-entry
runs the same reset. `start` cannot *observe* whether a session is new — so it
now asks, and refuses to guess destructively:

  • `--source resume|compact` → this session already existed → **preserve**.
  • `--source startup|clear`  → a genuinely new context → reset.
  • no/unknown source + a non-empty counter → **preserve** (the hand-run preview
    case). Unknown must not fall through to the destructive branch; that fallback
    shape is audit finding F-16.
  • an empty counter → reset freely. Nothing to protect, so no friction.
  • `--force` → always reset. `just budget-reset --force` is the deliberate one.

The asymmetry is the design: wrongly preserving costs one `--force`; wrongly
resetting destroys a credential and narrows a security review. A reset that *does*
discard a non-empty counter is recorded in `.ai/decision-log/` — recovery already
required a logged override, and the destruction was the half with no record.

Exit codes: 0 credited · 1 usage · 3 budget spent (`check`) · 4 ledger/DoD
refusal · 5 gate refusal · 6 unreviewed diff.

Usage:
  session_budget.py status                 # show remaining budget
  session_budget.py start [--source X]     # reset the counter for a NEW session
  session_budget.py start --force          # reset even if credit was earned
  session_budget.py work "PH5-T01"         # claim a completed work task (enforced)
  session_budget.py work "PH5-T01" --override "docs-only, no runnable gate"
  session_budget.py close git-push|docs|issues
  session_budget.py close git-push --override "reason"
  session_budget.py check                  # exit 3 if the WORK budget is spent

State: .ai/session-state.json  (gitignored — per session, per machine)
"""

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
# PH16-T40. The budget asks the lock what a session IS rather than keeping a
# second opinion — see writer_continuity(). Imported at module scope on purpose:
# a lazily-imported guard that quietly fails to load is a guard that is off.
import session_lock  # noqa: E402

#: `full`'s cap, and the only number this module states itself. Every caller reads
#: `work_max()` instead, because PH26-T01 made the cap a per-workspace declaration
#: and two live copies of one number is this workspace's own recurring defect.
WORK_MAX = 2
CLOSURE_TYPES = ["git-push", "docs", "issues"]
EXIT_LEDGER = 4
EXIT_GATE = 5
EXIT_UNREVIEWED = 6
EXIT_PLAN = 7
# PH22-T01. Distinct from EXIT_PLAN because "you never discussed this with him"
# and "you never wrote the plan" are different failures with different cures, and
# a caller that collapses them cannot tell the operator which one happened.
EXIT_BRIEF = 8
# Closure steps that must not be recorded until the session's diff was reviewed.
REVIEW_REQUIRED = {"git-push"}
# SessionStart sources (PH7-T09). A source outside BOTH sets is "unknown", which
# is deliberately *not* the same as "new" — see the module docstring.
NEW_SESSION_SOURCES = {"startup", "clear"}
CONTINUATION_SOURCES = {"resume", "compact"}


def _ws_root() -> Path:
    try:
        t = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                    stderr=subprocess.DEVNULL, text=True).strip()
        if t:
            return Path(t)
    except Exception:
        pass
    return Path(".").resolve()


STATE = _ws_root() / ".ai" / "session-state.json"


def work_max(root: Path | None = None) -> int:
    """How many work slots this workspace gets — asked of its profile (PH26-T01).

    `full` answers 2, which is what every workspace ran before profiles existed and
    what 45 of 46 still run. `lite` answers more, for a product workspace whose
    sessions are eight ten-minute scaffolding tasks rather than one rewrite of an
    enforcement path.

    Deliberately a lookup and not a constant read: the profile is declared in
    `.ai/workspace.yaml`, so the cap is a property of the workspace this process is
    standing in, not of this file. A missing/broken `workspace_profile` falls back to
    `WORK_MAX` rather than raising — losing the budget display is a worse failure
    than running the default cap, and the fallback is the STRICT direction.
    """
    try:
        import workspace_profile
        return workspace_profile.work_max(root or _ws_root())
    except Exception:  # noqa: BLE001
        return WORK_MAX


def _head(root: Path | None = None) -> str:
    """The commit the session starts from — the base of `close git-push`'s review."""
    try:
        return subprocess.check_output(
            ["git", "-C", str(root or _ws_root()), "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return ""


def new_state(root: Path | None = None) -> dict:
    return {"session_start": datetime.now(timezone.utc).isoformat(),
            "head_at_start": _head(root), "work": [], "closure": []}


def load_raw() -> dict | None:
    """The stored state, or None if there is none / it is unreadable.

    `load()` cannot answer "was there a prior session?" — it manufactures a fresh
    state when the file is missing, which is right for readers and wrong for the
    one caller that must decide whether anything is at stake.
    """
    if not STATE.exists():
        return None
    try:
        s = json.loads(STATE.read_text())
    except Exception:  # noqa: BLE001
        return None
    return s if isinstance(s, dict) else None


def load() -> dict:
    s = load_raw()
    return s if s is not None else new_state()


def is_empty(state: dict | None) -> bool:
    """Has this session earned nothing yet? Then a reset costs nothing."""
    return not state or not (state.get("work") or state.get("closure"))


def is_fully_closed(state: dict | None) -> bool:
    """Has this session recorded ALL 3 closures (git-push, docs, issues)?

    A fully-closed record has nothing left for `should_reset` to protect: the
    work credit, the diff review and the closure verdicts are already durably
    written to git + `.ai/decision-log/`, independent of this file. Discarding
    THIS file's copy of that record loses nothing — unlike a mid-flight session
    (spent its work slots, closure not yet finished), where `--force` really
    would erase credit that exists nowhere else yet.

    Used only to change what the tool SAYS (`render`, `cmd_start`'s preserve
    branch) — `should_reset` itself is untouched, so `--force` is still
    required either way. See knownIssues.md: an ambiguous SessionStart source
    (Claude Code reporting "resume"/no source for what a human experienced as
    a new session) made a fully-closed record look identical to a live refusal.
    """
    return bool(state) and set(state.get("closure", [])) >= set(CLOSURE_TYPES)


def writer_continuity(state: dict | None, lock: dict | None = None,
                      me: dict | None = None, root: Path | None = None) -> dict:
    """Does a live lock prove the same *writer* earned `state`? {continuous, reason}.

    PH16-T40. The harness rotates its conversation id mid-session and the next
    `SessionStart` reports `source=startup` — the one value PH7-T09 deliberately
    trusts as a new context. On 2026-08-16 that discarded PH22-T09's credit twenty
    minutes after it was earned.

    The fact needed to tell a rotation from a restart already exists one module
    away, and `session_lock` prints it in its own output: it identifies a session
    by the agent **process**, and `same_writer()` calls a rotated id under one
    unchanged pid continuity (PH16-T23). Rather than invent a second notion of
    session identity — which is the defect, two subsystems disagreeing about what
    a session *is* — this asks the one that is already right.

    Three conditions, all required, because a wrong "continuous" is the direction
    that hurts: it would leave a counter that never clears, blocking work instead
    of over-permitting it.
      * the lock is **live** (holder running, heartbeat inside the window),
      * `same_writer()` matches this process against the holder,
      * `acquired_at` **predates** the counter it is protecting — a lock taken
        after the credit describes a later tenure, and PH16-T38's own bug was
        exactly `acquired_at` landing after the work.
    """
    if state is None or is_empty(state):
        return {"continuous": False, "reason": "nothing earned to protect"}
    lock = session_lock.read_lock(root or _ws_root()) if lock is None else lock
    if not lock:
        return {"continuous": False, "reason": "no lock — nothing claims this tree"}

    live = session_lock.liveness(lock)
    if not live["live"]:
        return {"continuous": False, "reason": f"lock is not live ({live['reason']})"}

    me = session_lock.identity() if me is None else me
    match = session_lock.same_writer(lock, me)
    if not match["same"]:
        return {"continuous": False, "reason": f"a different writer holds the lock ({match['reason']})"}

    acquired = _parse_iso(lock.get("acquired_at"))
    began = _parse_iso(state.get("session_start"))
    if acquired is None or began is None:
        return {"continuous": False,
                "reason": "cannot compare the lock's tenure with the counter's start"}
    if acquired > began:
        return {"continuous": False,
                "reason": (f"the lock was acquired at {lock.get('acquired_at')}, after the "
                           f"counter began at {state.get('session_start')} — a later tenure")}

    return {"continuous": True,
            "reason": (f"the same writer process (pid {lock.get('pid')}) has held this tree "
                       f"since {lock.get('acquired_at')} — {match['reason']}")}


def _parse_iso(text) -> datetime | None:
    try:
        stamp = datetime.fromisoformat(str(text))
    except (TypeError, ValueError):
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


def should_reset(state: dict | None, source: str = "", force: bool = False,
                 continuity: dict | None = None) -> dict:
    """Should `start` discard `state`? Returns {reset, reason} (PH7-T09).

    Pure: takes the state rather than reading it, so the decision can be tested
    without a state file and the caller stays responsible for the side effect.
    `continuity` keeps that property intact for PH16-T40 — the lock is read by
    `writer_continuity()` and its verdict passed in. Omitted, this behaves exactly
    as it did before, which is what stops every existing caller changing meaning.
    """
    src = (source or "").strip().lower()
    if force:
        return {"reset": True, "reason": "--force"}
    if state is None:
        return {"reset": True, "reason": "no prior session state"}
    if is_empty(state):
        return {"reset": True, "reason": "prior counter is empty — nothing to preserve"}
    if src in CONTINUATION_SOURCES:
        return {"reset": False,
                "reason": f"SessionStart source={src} — this session already existed"}
    if continuity and continuity.get("continuous"):
        # PH16-T40. Deliberately ahead of NEW_SESSION_SOURCES and behind `--force`:
        # a rotation reports `startup`, so this must outrank the source string —
        # but the operator's explicit reset must still outrank everything.
        return {"reset": False,
                "reason": (f"SessionStart source={src}, but the same writer still holds the "
                           f"lock — {continuity['reason']}")}
    if src in NEW_SESSION_SOURCES:
        return {"reset": True, "reason": f"SessionStart source={src} — a new session"}
    # Unknown source + earned credit: the ambiguous case that bit three times.
    # Preserving is recoverable with one --force; resetting is not.
    return {"reset": False,
            "reason": (f"no recognised session source ({src or 'none given'}) and credit "
                       "has been earned — refusing to discard it without --force")}


def save(s: dict) -> None:
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=2))


def render(s: dict) -> None:
    w, c = len(s["work"]), len(set(s["closure"]))
    cap = work_max()
    print(f"  🎯 Session budget: WORK {w}/{cap} · CLOSURE {c}/3")
    if cap != WORK_MAX:
        # Named, never silent. A raised cap the operator cannot see is a raised cap
        # nobody agreed to — and the profile is the only thing that can raise it.
        try:
            import workspace_profile
            print(f"     profile: {workspace_profile.label()}")
        except Exception:  # noqa: BLE001
            pass
    if s["work"]:
        print(f"     work done: {', '.join(s['work'])}")
    if s["closure"]:
        print(f"     closure done: {', '.join(sorted(set(s['closure'])))}")
    remaining = [t for t in CLOSURE_TYPES if t not in s["closure"]]
    if w >= cap and is_fully_closed(s):
        # Nothing left to protect — see is_fully_closed()'s docstring. A future
        # SessionStart re-entering this same record (ambiguous or continuation
        # source) is NOT a "budget over, stop" situation; it is a closed
        # session's record sitting around after its work already shipped.
        print("  ✅ CLOSED session — all 3 closures recorded (git-push · docs · issues).")
        print("     Nothing here is at risk: work + review are already committed to git")
        print("     and `.ai/decision-log/`. Picking this up as a new working session?")
        print("     `just budget-reset --force` is SAFE here — it discards nothing that")
        print("     isn't already durably recorded elsewhere.")
    elif w >= cap:
        print("  🛑 WORK BUDGET REACHED. Do NOT start another task.")
        if remaining:
            print(f"     → Finish closure: {', '.join(remaining)}")
        print("     → Run `just handover \"<next step>\"`, then START A NEW SESSION.")
    else:
        print(f"     → {cap - w} work slot(s) left this session.")


def _log(decision: str, task: str, reason: str, kind: str = "policy",
         root: Path | None = None, action: str = "", **fields) -> None:
    """Record the credit verdict in `.ai/decision-log/`. Best effort: a logging
    failure must never turn a refusal into a credit, or vice versa.

    `**fields` (PH22-T05) so a caller can attach a machine-readable verdict
    instead of hiding it inside `reason`. Reading state back out of prose is the
    mention-vs-declaration bug class, and it has cost this workspace three
    incidents; a field that a reader can key on cannot be forged by a sentence
    that merely mentions the word.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import decision_log as dl
        dl.record(kind, decision, "session_budget", root=root or _ws_root(),
                  action=action or f"work-done {task}", reason=reason, task=task,
                  **fields)
    except Exception:  # noqa: BLE001
        pass


def classify_discarded(state: dict | None) -> dict:
    """What is this reset throwing away, and is it worth saying out loud?

    Three verdicts, and the two quiet ones carry as much of the design as the
    loud one — a warning that fires on every reset is noise within a week:

      • `untouched` — nothing was earned; the reset costs nothing.
      • `clean`     — work AND all three closures. The credit, the reviewed diff
                      and the handover are already durable in git and
                      `.ai/decision-log/`; this file's copy is redundant.
      • `unclosed`  — work credited, closure incomplete. This is the 2026-08-15
                      case, twice: PH22-T01 and PH22-T02 credited, zero closure
                      steps, 2,759 lines uncommitted. Here the record exists
                      NOWHERE else yet, so discarding it silently destroys the
                      only trace that the work is unfinished.

    Pure function of the state — no clock, no git, no I/O — so the classification
    can be asserted directly, and the renderer's job stays rendering.
    """
    work = list((state or {}).get("work") or [])
    closure = list((state or {}).get("closure") or [])
    out = {"verdict": "untouched", "warn": False, "work": work,
           "closure": closure,
           "missing": [k for k in CLOSURE_TYPES if k not in closure]}
    if not work:
        # Closure without work is odd, but nothing was earned and then dropped —
        # and warning about it would train the operator to ignore the warning.
        return out
    if not out["missing"]:
        out["verdict"] = "clean"
        return out
    out["verdict"] = "unclosed"
    out["warn"] = True
    return out


def supersession(source: str, root: Path | None = None,
                 lock: dict | None = None, me: dict | None = None) -> dict:
    """What this reset means for whoever holds the workspace lock (PH24-T11).

    Returns the record to store, or `{}` when this reset supersedes nobody.

    The mirror of `writer_continuity()` above. There, the budget asked the lock
    "is this really a new session?" so it would stop discarding a continuing
    one's credit. Here it *answers* — because the reverse question, "has the
    session I am holding the tree for ended?", is one the lock has no way to ask.
    On 2026-08-17 a `/clear` reset this counter, the reset was written to the
    decision log, and `session_lock` went on holding the tree for another 52
    minutes with an 8-hour reclaim window; `unlock --force` was the only exit.

    Written only on a **recognised new-session source**, so the meaning of a
    source string is decided exactly once — here, by the module that owns
    `NEW_SESSION_SOURCES`. `session_lock` reads the record's existence, never
    re-derives it. An empty return is the honest "this reset proves nothing":
    an unrecognised source, no lock, an unidentifiable caller, or — the case
    that matters — `same_writer()` recognising the holder as this very process,
    which is a rotation and must keep adopting (PH16-T23).
    """
    src = (source or "").strip().lower()
    if src not in NEW_SESSION_SOURCES:
        return {}
    root = root or _ws_root()
    lock = session_lock.read_lock(root) if lock is None else lock
    me = session_lock.identity() if me is None else me
    if not lock or not me or not me.get("session"):
        return {}
    if session_lock.same_writer(lock, me)["same"]:
        return {}
    return {"session": lock.get("session"), "by": me.get("session"),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "source": src}


def new_state_after(prior: dict | None, root: Path | None = None) -> dict:
    """A fresh session that remembers what the last one failed to finish.

    Only an `unclosed` verdict leaves residue. That is deliberate: the anti-nag
    property is a function of what is STORED, not of what a renderer later
    chooses to show. A future briefing that forgets to filter still cannot nag
    about a session that closed properly, because there is nothing there.
    """
    s = new_state(root)
    v = classify_discarded(prior)
    if v["warn"]:
        s["prior"] = {"verdict": v["verdict"], "work": v["work"],
                      "missing": v["missing"],
                      "session_start": (prior or {}).get("session_start", "")}
    return s


def verify_work_claim(task: str, root: Path | None = None) -> dict:
    """Can `task` be credited? Returns {ok, code, reason, dod, gate}.

    Pure check — never touches the counter, so the caller decides what to do and
    the tests can assert the verdict without mutating session state.
    """
    root = root or _ws_root()
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    v = {"ok": False, "code": EXIT_LEDGER, "reason": "", "dod": "", "gate": {}}

    try:
        import task_ledger
    except Exception as exc:  # noqa: BLE001
        v["reason"] = f"task_ledger.py unavailable ({exc}) — cannot verify the claim."
        return v

    info = task_ledger.find_task(task, root=root)
    if not info["found"]:
        v["reason"] = (f"{task} is not declared in {' or '.join(info['searched'])}. "
                       "Work credit requires a real task, not a string.")
        return v
    if not info["dod"]:
        v["reason"] = (f"{task} is declared in {info['source']}:{info['line']} but has no "
                       "`DoD:` line. A task with no stated acceptance test cannot be "
                       "shown to have met it — add one, then re-claim.")
        return v
    v["dod"] = info["dod"]

    # EVERY task owes a pre-work brief (PH22-T01, Goal G20) — not only `[complex]`
    # ones. Checked first of all the documentation gates because that is the real
    # order of events: he agrees, then the plan is written, then the code. A task
    # nobody discussed should be told so before it is told anything else.
    #
    # Deliberately wider than the plan gate below. The operator asked for "every
    # session every task", twice, and the evidence backs the wider rule: the 26
    # days of `[AI] chore: sync workspace state` this task was written against
    # contained no complex task at all, so a `[complex]`-only check would have
    # watched the entire failure go past.
    try:
        import prework
        bv = prework.validate(task, root=root)
    except Exception as exc:  # noqa: BLE001
        v["code"] = EXIT_BRIEF
        v["reason"] = f"prework.py unavailable ({exc}) — cannot verify the brief."
        return v
    if not bv["ok"]:
        v["code"] = EXIT_BRIEF
        v["reason"] = (f"{task} has no valid pre-work brief — {bv['reason']} "
                       f'Or `just work-done "{task}" --override "reason"` if the '
                       "exception is genuine; that lands in the decision log.")
        return v
    v["brief_timing"] = bv["timing"]

    # A `[complex]` task owes a plan (PH7-T03). Checked before the gate because
    # the plan is meant to precede the *code*, so failing here should not require
    # having run the pipeline first — and because "you never wrote the plan" is a
    # more useful message than "the gate is stale".
    if info.get("complex"):
        try:
            import plan as plan_mod
            pv = plan_mod.validate(task, root=root)
        except Exception as exc:  # noqa: BLE001
            v["code"] = EXIT_PLAN
            v["reason"] = f"plan.py unavailable ({exc}) — cannot verify the plan."
            return v
        if not pv["ok"]:
            v["code"] = EXIT_PLAN
            v["reason"] = (f"{task} is marked `[complex]` but has no written plan — "
                           f"{pv['reason']}")
            return v

    # A `[complex]` task also owes a named, collectible test in its DoD (PH15-T04).
    # Same exit code as the plan refusal — both are "owe more documentation".
    # Grandfathered tasks (completed before this rule existed) are not blocked
    # here; `just conformance` reports them as a gap figure instead.
    if info.get("complex"):
        if not info.get("test_ref"):
            v["code"] = EXIT_PLAN
            v["reason"] = (
                f"{task} is marked `[complex]` but its DoD names no collectible test. "
                "Add `      test: tests/x.py::Class::test_name` as a sub-bullet under the "
                f"DoD entry, or `just work-done \"{task}\" --override \"reason\"` if the "
                "exception is genuine.")
            return v
        # Verify the named test is actually collectible.
        #
        # `collects()` answers with `collected` + `verified` — it has no `found`
        # key, and reading one made this refusal fire for EVERY complex task,
        # including tasks naming a test the runner loads happily. `collected` is
        # the gate (PH7-T06's contract, `note_issue.py:411`); `verified` is False
        # for a non-unittest runner that cannot be asked what it collects without
        # running it, which is a caveat to carry, not grounds to refuse — else a
        # JS workspace could never credit a `[complex]` task at all.
        try:
            import run_tests as rt_mod
            cr = rt_mod.collects(info["test_ref"], root=root)
        except Exception as exc:  # noqa: BLE001
            cr = {"collected": False, "verified": False, "ref": info["test_ref"],
                  "reason": f"run_tests unavailable ({exc})"}
        if not cr.get("collected"):
            v["code"] = EXIT_PLAN
            v["reason"] = (
                f"{task} names `test: {info['test_ref']}` but that test is not "
                f"collectible: {cr.get('reason', 'unknown error')}. Fix or rename the "
                "test, or use --override with a written reason.")
            return v
        if not cr.get("verified"):
            v["test_caveat"] = cr.get("reason", "")

    try:
        import gate_check
        gate = gate_check.check(root, require_task=task)
    except Exception as exc:  # noqa: BLE001
        v["code"] = EXIT_GATE
        v["reason"] = f"gate_check.py unavailable ({exc}) — cannot prove the DoD was validated."
        return v
    v["gate"] = gate

    if not gate["open"]:
        v["code"] = EXIT_GATE
        v["reason"] = f"validation gate is BLOCKED — {gate['reason']}"
        return v
    covered = (gate.get("checks", {}) or {}).get("task_id", "")
    if covered != task:
        v["code"] = EXIT_GATE
        v["reason"] = (
            f"the gate is open but its evidence covers {covered or 'no task'!r}, not {task!r}. "
            f"Mark {task} `(In Progress)` in activeContext.md, run `just verify-safe`, "
            "then claim the credit.")
        return v

    v["ok"] = True
    v["code"] = 0
    v["reason"] = f"DoD declared in {info['source']} and covered by a passing {gate['checks']['pipeline']} run."
    return v


def cmd_work(s: dict, args: list[str], root: Path | None = None) -> int:
    root = root or _ws_root()
    task = args[1] if len(args) > 1 else ""
    override = ""
    if "--override" in args:
        i = args.index("--override")
        override = args[i + 1].strip() if len(args) > i + 1 else ""
    if not task:
        print("usage: session_budget.py work \"PH#-T##\" [--override \"reason\"]")
        return 1

    if override:
        s["work"].append(task)
        save(s)
        _log("gate_override", task, f"work credit overridden: {override}", kind="override", root=root)
        print(f"  ⚠️  OVERRIDE — {task} credited without a verified DoD.")
        print(f"     Reason: {override}")
        print("     Recorded in .ai/decision-log/ — this is visible in `just audit`.")
        render(s)
        return 0
    if "--override" in args:
        print("❌ --override requires a written reason: --override \"why\"", file=sys.stderr)
        return 1

    v = verify_work_claim(task, root=root)
    if not v["ok"]:
        _log("deny", task, v["reason"], root=root)
        print(f"🛑 WORK CREDIT REFUSED — {task}", file=sys.stderr)
        print(f"   {v['reason']}", file=sys.stderr)
        print("   The counter was NOT incremented.", file=sys.stderr)
        # EXIT_PLAN covers two different debts (PH7-T03's plan, PH15-T04's named
        # test), so the remedy has to be chosen from the reason. Printing "run
        # `just plan`" at someone whose plan is fine and whose test is missing
        # sends them to fix the one thing that is not wrong.
        if v["code"] == EXIT_PLAN:
            if "collectible test" in v["reason"] or "not collectible" in v["reason"]:
                print("   → name the regression test in the DoD block: "
                      "`      test: tests/test_x.py::Class::test_name`", file=sys.stderr)
            else:
                print(f"   → `just plan \"{task}\"` scaffolds it; fill every section.",
                      file=sys.stderr)
        print("   → Fix the cause, or `just work-done \"%s\" --override \"reason\"` "
              "if the exception is genuine." % task, file=sys.stderr)
        return v["code"]

    s["work"].append(task)
    save(s)
    _log("allow", task, v["reason"], root=root)
    print(f"  ✅ {task} credited — DoD verified against an open gate.")
    print(f"     DoD: {v['dod']}")
    # A runner that cannot be asked what it collects (anything but unittest) lets
    # the credit through on the file existing. Say so — crediting on a weaker
    # check than the operator assumes is exactly the silence this OS exists to
    # remove, and PH7-T06 prints the same caveat when it resolves an issue.
    if v.get("test_caveat"):
        print(f"     ⚠️  collection NOT verified — {v['test_caveat']}")
    render(s)
    return 0


def verify_closure(kind: str, root: Path | None = None) -> dict:
    """Can `kind` be recorded as done? Pure check, like `verify_work_claim`.

    Only `git-push` carries a precondition today: the session's diff must have a
    recorded review covering it. A missing `self_review.py` **refuses** rather
    than waving the closure through — an unverifiable precondition is a closed
    one, the same rule the validation gate follows for unverified evidence.

    PH10-T04: when the active task carries a complete delegation contract
    (`leash.active_contract()` — the one existing definition of "delegated",
    reused rather than re-derived), a SECOND precondition applies: a
    reviewer-tier verdict bound to the same diff (`delegate_review.check()`).
    A `fail` verdict, a stale verdict, or none at all refuses closure the
    same way a missing self-review does. An ordinary (non-delegated) session
    is unaffected — `leash.active_contract()` returns None for it, the exact
    no-op discipline PH10-T03 already established.
    """
    root = root or _ws_root()
    v = {"ok": True, "code": 0, "reason": ""}
    if kind not in REVIEW_REQUIRED:
        return v
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    try:
        import self_review
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": EXIT_UNREVIEWED,
                "reason": f"self_review.py unavailable ({exc}) — cannot show the diff was reviewed."}
    r = self_review.check(root)
    if not r["ok"]:
        return {"ok": False, "code": EXIT_UNREVIEWED, "reason": r["reason"]}

    try:
        import leash
        contract = leash.active_contract(root)
    except Exception:  # noqa: BLE001
        contract = None
    if not contract:
        return {"ok": True, "code": 0, "reason": r["reason"]}

    try:
        import delegate_review
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "code": EXIT_UNREVIEWED,
                "reason": (f"delegate_review.py unavailable ({exc}) — cannot show "
                           f"{contract['task']} was reviewer-approved.")}
    dr = delegate_review.check(contract["task"], root)
    if not dr["ok"]:
        return {"ok": False, "code": EXIT_UNREVIEWED, "reason": dr["reason"]}
    return {"ok": True, "code": 0, "reason": dr["reason"]}


def cmd_close(s: dict, args: list[str], root: Path | None = None) -> int:
    root = root or _ws_root()
    kind = args[1] if len(args) > 1 else ""
    override = ""
    if "--override" in args:
        i = args.index("--override")
        override = args[i + 1].strip() if len(args) > i + 1 else ""
        if not override:
            print("❌ --override requires a written reason: --override \"why\"", file=sys.stderr)
            return 1
    if kind not in CLOSURE_TYPES:
        print(f"usage: session_budget.py close {'|'.join(CLOSURE_TYPES)} [--override \"reason\"]")
        return 1
    if override and kind not in REVIEW_REQUIRED:
        # Silently accepting it would imply something was bypassed and logged.
        print(f"❌ `close {kind}` has no precondition to override — "
              f"only {'/'.join(sorted(REVIEW_REQUIRED))} does. Drop the reason.",
              file=sys.stderr)
        return 1

    if override and kind in REVIEW_REQUIRED:
        _log("gate_override", "", f"closure {kind} overridden: {override}",
             kind="override", root=root, action=f"close {kind}")
        print(f"  ⚠️  OVERRIDE — {kind} recorded without a verified self-review.")
        print(f"     Reason: {override}")
        print("     Recorded in .ai/decision-log/ — this is visible in `just audit`.")
    else:
        v = verify_closure(kind, root=root)
        if not v["ok"]:
            _log("deny", "", v["reason"], root=root, action=f"close {kind}")
            print(f"🛑 CLOSURE REFUSED — {kind}", file=sys.stderr)
            print(f"   {v['reason']}", file=sys.stderr)
            print("   It was NOT recorded.", file=sys.stderr)
            print("   → `just self-review-status` to see the diff, then "
                  "`just self-review pass \"what you checked\"`.", file=sys.stderr)
            return v["code"]
        # Every successful closure is logged now, not only git-push's (PH16-T07)
        # — `docs`/`issues` used to update session-state.json (gitignored,
        # overwritten by the next `start`) and nothing else, so `protocol_score.py`
        # had no artefact for "issues triaged at close" and reported it
        # unobservable forever. docs/issues carry no precondition and no `reason`
        # from `verify_closure`, so they get a stated fallback instead of an
        # empty one.
        reason = v["reason"] or f"{kind} closure recorded"
        _log("allow", "", reason, root=root, action=f"close {kind}")
        if kind in REVIEW_REQUIRED:
            print(f"  ✅ {kind} — {reason}")

    s["closure"].append(kind)
    save(s)
    render(s)
    return 0


def cmd_start(args: list[str]) -> int:
    """Begin a session — or decline to, when this is a re-entry into a live one."""
    source, force = "", "--force" in args
    if "--source" in args:
        i = args.index("--source")
        if i + 1 < len(args):
            source = args[i + 1]

    existing = load_raw()
    # PH16-T40: read the lock before deciding. The incident happened *here* — the
    # predicate was asked a question it had no way to answer.
    verdict = should_reset(existing, source, force,
                           continuity=writer_continuity(existing))

    if not verdict["reset"]:
        print(f"  ♻️  Session budget preserved — {verdict['reason']}.")
        render(existing)
        if is_fully_closed(existing):
            print("     ℹ️  New working session? `just budget-reset --force` is SAFE — this")
            print("     record is already fully closed (git-push · docs · issues), so nothing")
            print("     unrecorded is discarded.")
        else:
            print("     → Genuinely a new session? `just budget-reset --force` "
                  "(discards unclosed credit).")
        return 0

    lost = classify_discarded(existing)
    if not is_empty(existing):
        # Destroying earned credit is a decision, not a housekeeping detail.
        # Recovery has always been logged; now so is the loss.
        discarded = ", ".join(existing.get("work", []) + existing.get("closure", [])) or "—"
        _log("reset", ", ".join(existing.get("work", [])) or "",
             f"session budget reset ({verdict['reason']}) — discarded: {discarded}",
             kind="budget", action="budget-reset", verdict=lost["verdict"],
             missing_closure=",".join(lost["missing"]))

    # PH22-T05: carry the loss forward. Logging it and then writing a blank state
    # is what made the 2026-08-15 incident invisible — the fact was recorded in a
    # file nobody reads at session start, and the state the briefing DOES read was
    # wiped in the same breath.
    # PH24-T11: the conclusion "the previous session is over" is written where
    # `session_lock` can read it. Recorded, not acted on — releasing the lock
    # from here would put the decision in the subsystem that does not own it and
    # would destroy the very evidence that explains the next session's verdict.
    fresh = new_state_after(existing)
    ends = supersession(source)
    if ends:
        fresh["superseded"] = ends
        print(f"  🔓 The workspace lock is held by {ends['session']}, whose session this "
              f"start replaces — recorded, so `just commit-all` need not be forced.")
    save(fresh)
    # PH26-T03: asked, never stated. A banner that names a cap the counter did not
    # resolve is the failure `@zenithos` reported — and the sentence is the half an
    # agent obeys.
    print(f"  🎯 Session budget reset: {work_max()} work + {len(CLOSURE_TYPES)} closure.")
    if lost["warn"]:
        print(f"  ⚠️  The session just discarded credited {', '.join(lost['work'])} "
              f"and never ran: {', '.join(lost['missing'])}.")
    return 0


def main() -> int:
    args = sys.argv[1:]
    cmd = args[0] if args else "status"
    s = load()

    if cmd == "start":
        return cmd_start(args[1:])
    if cmd == "work":
        return cmd_work(s, args)
    if cmd == "close":
        return cmd_close(s, args)
    if cmd == "check":
        return 3 if len(s["work"]) >= work_max() else 0
    # default: status
    render(s)
    return 0


if __name__ == "__main__":
    sys.exit(main())
