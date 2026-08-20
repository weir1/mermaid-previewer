#!/usr/bin/env python3
"""session_lock.py — one writer per workspace (PH16-T22, rule B).

## Why this exists

Three enforcement paths in a God Mode workspace silently assume that exactly one
session writes to it:

  * `task_ledger.active_task()` reads **this** workspace's `activeContext.md`, so
    evidence is bound to whatever task this tree declares — work arriving from
    another session's ledger binds to nothing;
  * `verify-safe` runs the whole suite, so a second writer's red or half-written
    test holds the gate closed for *both* sessions (observed 2026-08-13: 1614
    green → 3 failures → 1 failure across five minutes, with the session reading
    the result touching no line);
  * `self_review` binds its record to the diff's content hash precisely so that
    "review, then quietly add one more thing" is impossible. Under a second
    writer that binding is not merely false, it is **un-satisfiable** — every
    review is voided by the other session's next keystroke, which pressures the
    operator toward an override. A safety property that degrades into an override
    prompt is worse than one that fails loudly.

So the two commands that turn edits into claimed, shipped work — `just commit-all`
and `just work-done` — require this lock first.

## What it does NOT do

It does not stop a second agent writing files, and nothing here should be read as
claiming it does. It converts a silent corruption into a loud refusal at the
moment of commit. That is the whole promise.

## Failing the right way

A lock is the classic deadlock-by-safety-feature: a crashed session could lock the
operator out of his own workspace. Three mechanisms and one rule prevent that.

  * **pid liveness** — the recorded pid is the long-lived *agent* process
    (`CLAUDE_PID`), never the transient `just` subprocess that runs this script.
    An OS-level `flock` can only ever express "this command is running"; ownership
    of a tree spans hours, which is the thing that actually needs to be exclusive.
  * **a staleness window** — a live pid that stopped heartbeating for
    `STALE_SECONDS` no longer holds the tree. Both conditions must hold for a lock
    to be live; either failing makes it stale and it is reclaimed with no human in
    the loop, and the reclaim is recorded.
  * **identity is the writer, not the conversation** (PH16-T23). `/clear`,
    compaction and resume all mint a new `CLAUDE_CODE_SESSION_ID` inside one
    unchanged agent process, so the id is a *proxy* for the writer and the proxy
    rotates on its own. `same_writer()` recognises a holder whose pid, host and
    agent all match the caller's — and whose process is not younger than the lock
    — as the same writer, and `require` **adopts** the lock instead of refusing
    it, preserving `acquired_at` because `commit_scope` partitions the tree at
    that stamp. Observed live 2026-08-14 before this existed: a session refused
    by a lock its own pid held, with `commit-all` and `work-done` offline and
    `--force` as the only exit. Every condition must hold, because the two errors
    are not symmetric — a wrong refusal is visible, a wrong match silently
    reinstates the corruption this module exists to prevent.
  * **a declared end** (PH24-T11). `session_budget` resolves the `SessionStart`
    source, and on a genuinely new session it resets the counter and says so in
    the decision log — while this module went on holding the tree for the full
    staleness window. Observed live 2026-08-17: a `/clear` at 07:11Z, a refusal
    at 08:03Z, and `unlock --force` as the only exit. So the budget now records
    that conclusion and `superseded()` reads it. Strictly *after* `same_writer()`,
    and void the moment the holder heartbeats again — a declaration says a
    session was over, and a later heartbeat is the holder proving otherwise.
  * **`just unlock --force`** — the human escape hatch, logged as the override it
    is. It exists for the crashed holder; if it is being pulled routinely, that is
    a bug in one of the four mechanisms above, not a workflow.
  * **an unidentifiable session degrades OPEN.** Two sessions that both present no
    identity are indistinguishable, so refusing would take the workspace offline
    for a configuration this guard cannot even diagnose. It warns and allows. The
    lock must never become the outage it exists to prevent.

Usage:
    python3 scripts/session_lock.py require [--action "git commit"]
    python3 scripts/session_lock.py status [--json]
    python3 scripts/session_lock.py release
    python3 scripts/session_lock.py unlock --force
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
LOCK_RELPATH = Path(".ai") / "session-lock.json"
# Written by `session_budget.cmd_start`, read here (PH24-T11). Not imported:
# `session_budget` imports THIS module at module scope, on purpose, so the
# dependency can only run one way. The record is the same information travelling
# the same direction without the cycle.
STATE_RELPATH = Path(".ai") / "session-state.json"

# A session heartbeats only when it runs `require` (commit-all / work-done), which
# is a handful of times a session — so this window is deliberately long. It is the
# *second* liveness condition, not the only one: a dead pid is stale immediately,
# whatever the clock says.
STALE_SECONDS = 8 * 3600

# How much later than `acquired_at` a process may have started and still be
# believed to be the acquirer (PH16-T23). It is not zero because `ps -o etime=`
# is whole-second and *truncating*, so a session that acquires the lock in the
# same second it starts can read back as having started a second after it — and
# refusing over rounding would resurrect the outage this fix removes. Two
# minutes is far below any realistic pid-recycling gap: the kernel allocates
# thousands of pids before wrapping, so a recycled holder is minutes-to-hours
# younger than the lock it appears to hold, never one second.
RECYCLE_GRACE_SECONDS = 120

EXIT_OK = 0
EXIT_HELD = 1

# Identity sources, in resolution order. The first one set wins, and the name is
# recorded so `status` can say *how* the session was identified rather than
# leaving the operator to guess.
ID_VARS = ("CLAUDE_CODE_SESSION_ID", "GEMINI_SESSION_ID", "ANTIGRAVITY_SESSION_ID")


def _ws_root() -> Path:
    """Workspace root per the AGENTS.md anti-drift rule: harness project dir,
    then git top-level, then cwd — never the open file."""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root).resolve()
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _stamp(when: datetime | None = None) -> str:
    return (when or _now()).isoformat(timespec="seconds")


def identity() -> dict:
    """Who is asking. `{}` when the session cannot be identified at all.

    The pid is `CLAUDE_PID` — the agent process that outlives every `just`
    subprocess. When there is no such pid the field is 0, which `is_live()`
    reads as "liveness unknown", never as "dead": inventing a death would let one
    session steal a lock it has no evidence is free.
    """
    pid = 0
    raw_pid = os.environ.get("CLAUDE_PID", "").strip()
    if raw_pid.isdigit():
        pid = int(raw_pid)

    for var in ID_VARS:
        value = os.environ.get(var, "").strip()
        if value:
            return {"session": value, "source": var, "pid": pid,
                    "agent": os.environ.get("AI_AGENT", "").strip() or "unknown",
                    "host": socket.gethostname()}

    # Last resort: an agent name plus a pid is weak, but it distinguishes two
    # concurrently running agents, which is the whole question being asked.
    agent = os.environ.get("AI_AGENT", "").strip()
    if agent and pid:
        return {"session": f"{agent}:{pid}", "source": "AI_AGENT+CLAUDE_PID",
                "pid": pid, "agent": agent, "host": socket.gethostname()}
    return {}


def lock_path(root: Path) -> Path:
    return root / LOCK_RELPATH


def read_lock(root: Path) -> dict:
    """The lock as recorded. An unreadable or corrupt lock is `{}` — a lock
    nobody can read cannot be evidence that someone holds this tree."""
    try:
        data = json.loads(lock_path(root).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and data.get("session") else {}
    except Exception:  # noqa: BLE001
        return {}


def pid_alive(pid: int) -> bool:
    """0 means "no pid was recorded" → unknown, and unknown is not dead."""
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # Running, owned by another user. Alive is the honest answer.
        return True
    except Exception:  # noqa: BLE001
        return True


def heartbeat_age(lock: dict) -> float | None:
    """Seconds since the holder last heartbeat; None when unparseable."""
    try:
        beat = datetime.fromisoformat(str(lock.get("heartbeat_at", "")))
        if beat.tzinfo is None:
            beat = beat.replace(tzinfo=timezone.utc)
        return (_now() - beat).total_seconds()
    except Exception:  # noqa: BLE001
        return None


def parse_etime(text: str) -> int | None:
    """`ps -o etime=` → seconds, or None when the output is not that format.

    `[[dd-]hh:]mm:ss`. `etime` and not `etimes` because macOS's `ps` has no
    `etimes` keyword at all (verified: *"ps: etime s: keyword not found"*), and
    this guard runs on both platforms.

    None means **no evidence**, never 0. Zero would read as "started just now"
    and would refuse every adoption on any platform whose `ps` prints something
    unexpected — turning a hardening check into the outage this module exists to
    avoid.
    """
    text = (text or "").strip()
    if not text:
        return None
    days = 0
    if "-" in text:
        head, _, text = text.partition("-")
        if not head.isdigit() or "-" in text:
            return None
        days = int(head)
    parts = text.split(":")
    if not 2 <= len(parts) <= 3 or not all(p.isdigit() for p in parts):
        return None
    nums = [int(p) for p in parts]
    while len(nums) < 3:
        nums.insert(0, 0)
    hours, minutes, seconds = nums
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def process_started_at(pid: int) -> datetime | None:
    """When `pid` started, or None when that cannot be determined.

    Same law as `parse_etime`: None is "no evidence", and no evidence never
    refuses. A missing `ps`, a hung `ps`, an exotic format and a pid that is not
    running all return None — the caller may only ever use a *positive* answer
    to refuse.
    """
    if not pid:
        return None
    try:
        out = subprocess.check_output(["ps", "-p", str(pid), "-o", "etime="],
                                      stderr=subprocess.DEVNULL, text=True, timeout=5)
    except Exception:  # noqa: BLE001
        return None
    elapsed = parse_etime(out)
    if elapsed is None:
        return None
    return _now() - timedelta(seconds=elapsed)


def _parse_stamp(text: str) -> datetime | None:
    try:
        when = datetime.fromisoformat(str(text))
    except Exception:  # noqa: BLE001
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def same_writer(held: dict, me: dict) -> dict:
    """Is the caller the same *writer* as the holder? `{same, adopted, reason}`.

    The lock's unit of ownership is the agent **process**, which is why `pid`
    is recorded from `CLAUDE_PID` rather than from the `just` subprocess. The
    session id is a *proxy* for that writer — and `/clear`, compaction and
    resume all rotate the proxy inside one unchanged process (PH16-T23, observed
    live 2026-08-14 (c): `commit-all` and `work-done` refused a session by a lock
    its own pid held, leaving `unlock --force` as the only exit).

    `adopted` distinguishes "the id also matches" (a plain heartbeat refresh,
    which decides nothing) from "the id rotated under a held lock" (a fact worth
    recording).

    Every condition below must hold, because the two errors are not symmetric: a
    wrong *refusal* is visible and recoverable, while a wrong *match* silently
    reinstates the corruption rule (B) exists to prevent — one session committing
    another's work under its own review hash.
    """
    if not held or not me:
        return {"same": False, "adopted": False, "reason": "no lock, or no identity to compare"}

    if held.get("session") == me.get("session"):
        return {"same": True, "adopted": False, "reason": "same session id"}

    try:
        held_pid = int(held.get("pid") or 0)
    except (TypeError, ValueError):
        held_pid = 0
    try:
        my_pid = int(me.get("pid") or 0)
    except (TypeError, ValueError):
        my_pid = 0

    # 0 is "unknown", and two unknowns are not a match. Reading them as one
    # writer would let any session adopt any lock wherever `CLAUDE_PID` is unset.
    if not held_pid or not my_pid:
        return {"same": False, "adopted": False,
                "reason": "one side reports no pid, so the writer cannot be identified"}
    if held_pid != my_pid:
        return {"same": False, "adopted": False,
                "reason": f"different agent process (holder pid {held_pid}, caller pid {my_pid})"}
    if held.get("host") != me.get("host"):
        return {"same": False, "adopted": False,
                "reason": f"pid {my_pid} on a different host "
                          f"({held.get('host')} vs {me.get('host')})"}
    if held.get("agent") != me.get("agent"):
        return {"same": False, "adopted": False,
                "reason": f"pid {my_pid} but a different agent "
                          f"({held.get('agent')} vs {me.get('agent')})"}

    # The pid-recycling hole: if the holder exited and the OS handed its number
    # to another agent process inside the staleness window, that stranger matches
    # on all three fields above — and would inherit an `acquired_at` from before
    # it existed, widening `commit_scope`'s window over another session's files.
    # A process cannot have acquired a lock that predates it.
    started = process_started_at(my_pid)
    acquired = _parse_stamp(held.get("acquired_at", ""))
    if started and acquired:
        late = (started - acquired).total_seconds()
        if late > RECYCLE_GRACE_SECONDS:
            return {"same": False, "adopted": False,
                    "reason": f"pid {my_pid} started at {_stamp(started)}, "
                              f"{late / 60:.0f}m after the lock was acquired at "
                              f"{held.get('acquired_at')} — the pid was recycled, "
                              f"not the conversation id"}

    return {"same": True, "adopted": True,
            "reason": f"same writer process (pid {my_pid} on {me.get('host')}, "
                      f"agent {me.get('agent')}); the conversation id rotated "
                      f"from {held.get('session')}"}


def _session_state(root: Path) -> dict:
    """`.ai/session-state.json` as recorded, or `{}` — an unreadable state file
    is not evidence that anybody was superseded."""
    try:
        data = json.loads((Path(root) / STATE_RELPATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def superseded(root: Path, held: dict) -> dict:
    """Has the OS already recorded that this holder's session ENDED? `{superseded, reason}`.

    PH24-T11, and the exact mirror of PH16-T40. `session_budget` resolves the
    `SessionStart` source, decides a genuinely new session has begun, resets the
    counter and writes the decision to `.ai/decision-log/` — while this module
    goes on holding the tree for `STALE_SECONDS`. On 2026-08-17 that locked the
    next session out of `commit-all`, `push` and `ship` 52 minutes after a
    `/clear`, and `unlock --force` was the only exit. `/clear` is the normal way
    to start a session under the 2+3 budget, so the escape hatch was being pulled
    on the common path — and a safety valve pulled routinely stops being read as
    one, which is a worse outcome than the delay it causes.

    So the budget writes its conclusion down and this reads it. **No second
    notion of session identity is introduced**: which source strings mean "a new
    session" is decided once, by the module that owns `NEW_SESSION_SOURCES`,
    which is why the record's mere existence is the signal and its `source` field
    is descriptive rather than re-interpreted here.

    Five conditions, all required — the asymmetry `same_writer()` documents
    applies unchanged, because a wrong refusal is visible and a wrong *release*
    silently reinstates the corruption rule (B) exists to prevent:

      1. a declaration exists and parses;
      2. it names **this** holder — its session id, or one of the ids in the
         lineage this tenure has already absorbed (PH16-T23 rotations);
      3. it was made by a **different** conversation id. This is the discriminator
         the incident report named, and it is what stops a session superseding
         itself;
      4. it postdates `acquired_at`. `.ai/session-state.json` outlives sessions,
         so without this an old record would release every future tenure;
      5. the holder has not heartbeat **since** it was declared over. This is the
         safety half and the only one that is not bookkeeping: a declaration says
         a session *was* over, and a later heartbeat is the holder proving
         otherwise by acting. It is what keeps a genuinely concurrent second
         agent refused.

    Honest about the residual: between two heartbeats, a second agent starting
    fresh alongside a live holder records a supersession naming it. That window
    is not opened by this function — `session_budget` already discards the
    holder's credit in exactly that scenario — but it is not closed by it either.
    """
    if not held:
        return {"superseded": False, "reason": "no lock"}
    record = _session_state(root).get("superseded")
    if not isinstance(record, dict) or not record.get("session"):
        return {"superseded": False, "reason": "no session end has been declared"}

    tenure = {str(held.get("session"))} | {str(s) for s in (held.get("lineage") or [])}
    if str(record.get("session")) not in tenure:
        return {"superseded": False,
                "reason": (f"a session end was declared for {record.get('session')}, "
                           f"which is not this tenure")}
    if str(record.get("by") or "") == str(held.get("session")):
        return {"superseded": False,
                "reason": "the declaration names the holder as its own successor"}

    at = _parse_stamp(record.get("at", ""))
    acquired = _parse_stamp(held.get("acquired_at", ""))
    if at is None or acquired is None:
        return {"superseded": False,
                "reason": "cannot compare the declaration with the lock's tenure"}
    if at <= acquired:
        return {"superseded": False,
                "reason": (f"the declaration at {record.get('at')} predates the lock "
                           f"acquired at {held.get('acquired_at')} — an earlier tenure")}

    beat = _parse_stamp(held.get("heartbeat_at", ""))
    if beat is not None and beat > at:
        return {"superseded": False,
                "reason": (f"{held.get('session')} was declared over at {record.get('at')}, "
                           f"but heartbeat at {held.get('heartbeat_at')} — it is still acting")}

    return {"superseded": True,
            "reason": (f"session_budget recorded a new session ({record.get('source') or 'new'}) "
                       f"at {record.get('at')}: {record.get('by')} superseded "
                       f"{record.get('session')}, which has not acted since")}


def liveness(lock: dict) -> dict:
    """{live, reason}. Both conditions must hold; either failing is staleness."""
    if not lock:
        return {"live": False, "reason": "no lock"}
    try:
        pid = int(lock.get("pid") or 0)
    except (TypeError, ValueError):
        # A damaged pid is not a dead one. 0 means "liveness unverifiable", so
        # the heartbeat decides alone — a malformed record must still yield a
        # verdict that names the holder, never a traceback the operator has to
        # read as a refusal.
        pid = 0
    if not pid_alive(pid):
        return {"live": False, "reason": f"holder pid {pid} is not running"}
    age = heartbeat_age(lock)
    if age is None:
        return {"live": False, "reason": "holder heartbeat is unreadable"}
    if age > STALE_SECONDS:
        return {"live": False,
                "reason": f"holder has not heartbeat in {age / 3600:.1f}h "
                          f"(stale after {STALE_SECONDS / 3600:.0f}h)"}
    return {"live": True, "reason": f"pid {pid} alive, heartbeat {age / 60:.0f}m ago"}


def _log(root: Path, kind: str, decision: str, reason: str, action: str) -> None:
    """Record the verdict. Best effort by design rule 1: a logging failure must
    never turn a refusal into an allow, or an allow into a refusal."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import decision_log as dl
        dl.record(kind, decision, "session_lock", root=root,
                  action=action or "session-lock", reason=reason)
    except Exception:  # noqa: BLE001
        pass


def write_lock(root: Path, me: dict, acquired_at: str | None = None,
               lineage: list | None = None) -> dict:
    """`lineage` is every earlier identity that has held **this** tenure.

    It exists because PH16-T23 established that a conversation id rotates inside
    one unchanged process. `write_journal` stamps each entry with the id that was
    current when the write happened, so without this list a session would become
    a stranger to work it did itself ten minutes earlier (PH16-T24).
    """
    entry = {
        "session": me["session"],
        "source": me["source"],
        "pid": me["pid"],
        "agent": me["agent"],
        "host": me["host"],
        "acquired_at": acquired_at or _stamp(),
        "heartbeat_at": _stamp(),
        "v": SCHEMA_VERSION,
    }
    if lineage:
        # De-duplicated and never containing the current holder: the readers ask
        # "which ids held this tenure", and answering twice is answering wrong.
        seen, ordered = set(), []
        for sid in lineage:
            sid = str(sid)
            if sid and sid != entry["session"] and sid not in seen:
                seen.add(sid)
                ordered.append(sid)
        if ordered:
            entry["lineage"] = ordered
    path = lock_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(entry, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)   # atomic: a reader never sees half a lock
    return entry


def require(root: Path, action: str = "") -> int:
    """Acquire, refresh, or reclaim the lock — or refuse. The only enforcement
    entry point; `commit-all` and `work-done` call exactly this."""
    me = identity()
    if not me:
        print("⚠️  session-lock: cannot identify this session "
              "(no CLAUDE_CODE_SESSION_ID / GEMINI_SESSION_ID / "
              "ANTIGRAVITY_SESSION_ID / AI_AGENT+CLAUDE_PID).")
        print("   Two unidentified sessions are indistinguishable, so this guard "
              "cannot enforce one-writer here and DEGRADES OPEN rather than "
              "locking you out of your own workspace.")
        print("   → If a second agent is editing this tree, stop it before committing.")
        return EXIT_OK

    held = read_lock(root)

    if not held:
        write_lock(root, me)
        _log(root, "policy", "acquire", f"session {me['session']} took the workspace lock", action)
        print(f"🔒 session-lock: acquired by {me['session']} (pid {me['pid'] or 'unknown'}).")
        return EXIT_OK

    # Identity before liveness: if this *is* the holder's process, whether that
    # process looks stale is beside the point — it is the caller, and reclaiming
    # would reset a window it has genuinely held since `acquired_at`.
    kin = same_writer(held, me)
    if kin["same"]:
        # `acquired_at` is preserved on BOTH paths, and that is load-bearing
        # rather than tidy: `commit_scope` partitions the tree at this stamp, so
        # resetting it when a conversation id rotates would drop this writer's
        # own earlier work out of its own commit.
        write_lock(root, me, acquired_at=str(held.get("acquired_at") or ""),
                   lineage=list(held.get("lineage") or []) + [held.get("session")])
        if not kin["adopted"]:
            # A pure heartbeat refresh decides nothing and changes no verdict, so
            # it is not decision-logged — the same reason a gate *status poll*
            # is not.
            print(f"🔒 session-lock: held by you ({me['session']}).")
            return EXIT_OK
        _log(root, "policy", "adopt",
             f"{me['session']} adopted the lock held by {held.get('session')} — {kin['reason']}",
             action)
        print(f"🔒 session-lock: adopted by {me['session']} — {kin['reason']}.")
        print(f"   Ownership dates from {held.get('acquired_at')}, unchanged.")
        return EXIT_OK

    state = liveness(held)
    # PH24-T11. Strictly after `same_writer()`, never before it: a `/clear` in
    # one unchanged process must still ADOPT and keep its `acquired_at`, or this
    # session's own earlier work falls out of its own commit (PH16-T23/T24).
    # Only a *different* writer's live lock can be superseded.
    ended = superseded(root, held) if state["live"] else {"superseded": False, "reason": ""}
    if state["live"] and ended["superseded"]:
        write_lock(root, me)
        _log(root, "policy", "supersede",
             f"lock of {held.get('session')} released to {me['session']} — {ended['reason']}",
             action)
        print(f"🔓 session-lock: {held.get('session')} was declared over by "
              f"`session_budget`, so its lock is released — {ended['reason']}.")
        print(f"🔒 session-lock: acquired by {me['session']}.")
        return EXIT_OK

    if state["live"]:
        print(f"🚫 BLOCKED — this workspace is already held by another session.")
        print(f"   Holder : {held.get('session')} (pid {held.get('pid')}, "
              f"host {held.get('host')}, agent {held.get('agent')})")
        print(f"   Since  : {held.get('acquired_at')}   [{state['reason']}]")
        print(f"   You    : {me['session']} (pid {me['pid'] or 'unknown'})")
        # Why this is not read as the same writer under a rotated conversation
        # id. Without it the operator reads two lines that can look identical
        # and has nothing to go on but `--force`.
        print(f"   Distinct because: {kin['reason']}")
        print("")
        print("   A second session writing here defeats three things at once: the")
        print("   evidence↔task binding, the gate (its red test closes yours), and")
        print("   the self-review hash (its next keystroke voids your review).")
        print("   → Finish or close that session, or `just unlock --force` if it is gone.")
        _log(root, "policy", "block",
             f"{me['session']} refused: workspace held by {held.get('session')} ({state['reason']})",
             action)
        return EXIT_HELD

    write_lock(root, me)
    _log(root, "policy", "reclaim",
         f"stale lock of {held.get('session')} reclaimed by {me['session']} — {state['reason']}",
         action)
    print(f"🔓 session-lock: reclaimed a stale lock from {held.get('session')} "
          f"— {state['reason']}.")
    print(f"🔒 session-lock: acquired by {me['session']}.")
    return EXIT_OK


def status(root: Path, as_json: bool = False) -> int:
    """A read. Deliberately not decision-logged — a status poll is a query."""
    held = read_lock(root)
    me = identity()
    state = liveness(held) if held else {"live": False, "reason": "no lock"}
    # `mine` comes from the SAME predicate `require` decides with, because an
    # operator reads this to predict that. Two implementations of "is this mine?"
    # is how a status that says "someone else's" precedes a command that adopts.
    kin = same_writer(held, me) if (held and me) else {"same": False, "adopted": False,
                                                       "reason": "no lock, or no identity"}
    # Same predicate `require` decides with, for the same reason `mine` is:
    # two implementations of "is this lock free?" is how a status that reads
    # BLOCKED precedes a command that reclaims (PH24-T11).
    ended = superseded(root, held) if held else {"superseded": False, "reason": "no lock"}
    if as_json:
        print(json.dumps({"lock": held, "me": me, "liveness": state,
                          "mine": kin["same"], "writer": kin,
                          "superseded": ended}, indent=2))
        return EXIT_OK
    if not held:
        print("🔓 session-lock: free — no session holds this workspace.")
    else:
        mine = " (you)" if kin["same"] else ""
        print(f"🔒 session-lock: held by {held.get('session')}{mine}")
        print(f"   pid {held.get('pid')} · host {held.get('host')} · "
              f"agent {held.get('agent')} · since {held.get('acquired_at')}")
        print(f"   {'LIVE' if state['live'] else 'STALE'} — {state['reason']}")
        if kin["adopted"]:
            print(f"   ADOPTABLE — {kin['reason']}")
        if ended["superseded"] and not kin["same"]:
            print(f"   SUPERSEDED — {ended['reason']}")
    if not me:
        print("   ⚠️  this session is unidentifiable; the guard degrades open here.")
    return EXIT_OK


def release(root: Path, force: bool = False, action: str = "") -> int:
    """Give the lock back. Only the holder may release it — unless `--force`,
    which is an override and is recorded as one."""
    held = read_lock(root)
    if not held:
        print("🔓 session-lock: already free.")
        return EXIT_OK
    me = identity()
    mine = bool(me and held.get("session") == me.get("session"))
    if not mine and not force:
        print(f"🚫 session-lock: held by {held.get('session')}, not by you. "
              f"Use `just unlock --force` if that session is gone.")
        return EXIT_HELD
    try:
        lock_path(root).unlink()
    except FileNotFoundError:
        pass
    if mine and not force:
        _log(root, "policy", "release", f"{held.get('session')} released its own lock", action)
        print("🔓 session-lock: released.")
    else:
        _log(root, "override", "allow",
             f"forced release of the lock held by {held.get('session')} "
             f"({liveness(held)['reason']})", action or "unlock --force")
        print(f"🔓 session-lock: FORCE-released the lock held by {held.get('session')}. "
              f"Recorded in the decision log.")
    return EXIT_OK


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="One writer per workspace (PH16-T22).")
    ap.add_argument("command", choices=["require", "status", "release", "unlock"])
    ap.add_argument("--root", default="", help="workspace root (default: resolved)")
    ap.add_argument("--action", default="", help="label for the decision log")
    ap.add_argument("--force", action="store_true", help="release a lock you do not hold")
    ap.add_argument("--json", action="store_true", help="status as JSON")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve() if args.root else _ws_root()

    if args.command == "require":
        return require(root, args.action)
    if args.command == "status":
        return status(root, args.json)
    # `unlock` is `release` under the name an operator will actually look for; it
    # is not a stronger command. Taking a lock you do not hold still requires the
    # explicit `--force`, so the escape hatch is always a stated intention.
    return release(root, force=args.force,
                   action=args.action or f"{args.command}{' --force' if args.force else ''}")


if __name__ == "__main__":
    sys.exit(main())
