#!/usr/bin/env python3
"""
audit.py — `just audit`: what did this OS actually decide, session by session? (PH5-T03)

The decision log (PH6-T13) answers "what verdicts were reached?" as a flat stream.
The session log (`session_end.py`) answers "what did that session change?" as a set of
disconnected files. Neither answers the question the audit exists for: **for each
session, how much ran autonomously, what did the OS stop, and what came out of it?**
This joins the two and renders that.

## The join (and why it needs one)
The two logs do not share a key, and pretending they do would silently mis-attribute:

  * a decision entry carries `session` = the session's **start** timestamp, copied from
    `.ai/session-state.json` at the moment the verdict was reached;
  * a session-log file carries `session_id` = the moment `just session-end` **wrote** it.

So a session log is attributed to the session whose start is the **latest start at or
before it was written** — i.e. the session that was open when it was written. Logs
older than every recorded session start (everything before decision logging existed on
2026-08-01) become their own rows, explicitly marked as having no decision data rather
than being folded into the nearest session and inflating its numbers.

## Rules this file holds itself to
1. **It does not re-implement the counting.** Every allow/ask/deny/open/blocked figure
   comes from `decision_log.summarize()` applied to a session's slice of entries. One
   counter, one definition of "autonomy rate" — a second copy here would drift from
   `just decisions` the first time either changed.
2. **It reads, it never writes.** No decision-log entry, no evidence, no state. An audit
   that mutated what it measures would be the same class of bug F-16 was. Like a gate
   *status* poll, running it is a query, not a decision, so it is deliberately absent
   from the log it prints.
3. **Missing data is reported as missing.** Pre-schema lines, sessions with no log, logs
   with no decisions — each is shown for what it is. The audit's whole value is that its
   gaps are visible instead of averaged away.

Usage:
  audit.py                      # every session, newest first
  audit.py --days 7             # only the last 7 days of logs
  audit.py --sessions 3         # only the 3 most recent sessions
  audit.py --json               # machine-readable (same numbers, no formatting)
"""

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import decision_log as dl  # noqa: E402

SESSION_LOG_DIRNAME = Path(".ai") / "session-log"
STATE_FILENAME = Path(".ai") / "session-state.json"
# `2026-08-01_131823.json` — the fallback when a log predates the `written_at` field.
_FILENAME_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})_(\d{2})(\d{2})(\d{2})")


# ── Loading ──────────────────────────────────────────────────────────────────
def _parse_ts(value) -> datetime | None:
    """Parse an ISO-8601 stamp, assuming UTC when it carries no offset."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _log_timestamp(log: dict, path: Path) -> datetime | None:
    """When this session log was written. `written_at` is the 2026-07 schema; the older
    2026-05 schema has neither field, so the filename is the last resort."""
    for field in ("written_at", "session_id"):
        stamp = _parse_ts(log.get(field))
        if stamp:
            return stamp
    match = _FILENAME_TS.match(path.name)
    if match:
        date, hour, minute, second = match.groups()
        return _parse_ts(f"{date}T{hour}:{minute}:{second}+00:00")
    return None


def read_session_logs(root: Path, days: int | None = None) -> list[dict]:
    """Session-end records, oldest first, both schemas tolerated. `_ts`/`_file` added."""
    directory = root / SESSION_LOG_DIRNAME
    if not directory.is_dir():
        return []
    cutoff = None
    if days:
        cutoff = datetime.now(timezone.utc) - timedelta(days=days - 1)
        cutoff = cutoff.replace(hour=0, minute=0, second=0, microsecond=0)
    logs: list[dict] = []
    for path in sorted(directory.glob("*.json")):
        try:
            log = json.loads(path.read_text(errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue          # a corrupt log must not hide every other session
        if not isinstance(log, dict):
            continue
        stamp = _log_timestamp(log, path)
        if cutoff and stamp and stamp < cutoff:
            continue
        log["_ts"] = stamp
        log["_file"] = path.name
        logs.append(log)
    logs.sort(key=lambda entry: entry["_ts"] or datetime.min.replace(tzinfo=timezone.utc))
    return logs


def read_state(root: Path) -> dict:
    """`.ai/session-state.json` — the live budget for the session that is still open."""
    try:
        state = json.loads((root / STATE_FILENAME).read_text())
        return state if isinstance(state, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


# ── The join ─────────────────────────────────────────────────────────────────
def build_sessions(entries: list[dict], logs: list[dict], state: dict) -> list[dict]:
    """Group decisions and session logs into one row per session, oldest first.

    Anchors are session *starts*: every distinct `session` value in the decision log,
    plus the currently open one from session-state. Each log is attributed to the last
    anchor at or before it was written; a log older than every anchor becomes its own
    anchor-less row so it is never mis-attributed to a session it did not belong to.
    """
    grouped: dict[str, list[dict]] = {}
    unattributed: list[dict] = []
    for entry in entries:
        key = entry.get("session") or ""
        if key:
            grouped.setdefault(key, []).append(entry)
        else:
            unattributed.append(entry)      # legacy / pre-session-state lines

    current_key = str(state.get("session_start") or "")
    if current_key:
        grouped.setdefault(current_key, [])

    anchors = sorted(
        ((key, _parse_ts(key)) for key in grouped),
        key=lambda pair: pair[1] or datetime.min.replace(tzinfo=timezone.utc),
    )
    sessions = [{
        "key": key,
        "start": start,
        "entries": grouped[key],
        "logs": [],
        "is_current": key == current_key,
        "has_anchor": True,
    } for key, start in anchors]

    # Scan only the anchored sessions. Orphan rows are collected separately and merged
    # at the end — appending them into the list being scanned would make the first
    # orphan an anchor and let it swallow every log written after it.
    orphans: list[dict] = []
    for log in logs:
        stamp = log.get("_ts")
        owner = None
        if stamp:
            # Sessions are sorted by start, so the last anchor at or before `stamp`
            # is the session that was open when the log was written.
            for session in sessions:
                if session["start"] and session["start"] <= stamp:
                    owner = session
        if owner is not None:
            owner["logs"].append(log)
        else:
            orphans.append({
                "key": "", "start": stamp, "entries": [], "logs": [log],
                "is_current": False, "has_anchor": False,
            })
    sessions.extend(orphans)

    sessions.sort(key=lambda s: s["start"] or datetime.min.replace(tzinfo=timezone.utc))
    for session in sessions:
        session["summary"] = dl.summarize(session["entries"])   # rule 1: one counter
    return sessions, unattributed


def task_throughput(session: dict) -> dict:
    """PH6-T20: per-session task throughput, from the session's own session-end log(s).

    Three honest states, never a bare 0 — `tasks_completed` defaults to `[]` in
    `session_end.py` whether a session truly finished nothing or `just session-end`
    was simply called without `--tasks-completed` (the overwhelmingly common case:
    18 of the first 29 recorded sessions), so an empty list is reported as
    *unrecorded*, not measured as zero. Deduped in case the same task id appears in
    more than one log attached to a session (a rare but possible double session-end)."""
    logs = session["logs"]
    if not logs:
        return {"status": "no_log", "count": None, "tasks": [],
                "basis": "no session-end log recorded for this session"}
    tasks: list[str] = []
    for log in logs:
        for task in (log.get("tasks_completed") or []):
            if task not in tasks:
                tasks.append(task)
    if not tasks:
        return {"status": "unrecorded", "count": None, "tasks": [],
                "basis": f"{len(logs)} session-end log(s) recorded, none named a completed task"}
    return {"status": "measured", "count": len(tasks), "tasks": tasks,
            "basis": f"{len(logs)} session-end log(s)"}


def notable(entries: list[dict]) -> list[dict]:
    """The verdicts an audit exists to surface: what was denied, blocked, or overridden."""
    return [e for e in entries
            if e.get("decision") in ("deny", "blocked", "gate_override")]


def findings(sessions: list[dict]) -> list[str]:
    """Protocol problems visible in the logs themselves — not a re-count, a read of them."""
    out: list[str] = []
    for session in sessions:
        label = _label(session)
        summary = session["summary"]
        if summary["overrides"]:
            out.append(f"{label}: {summary['overrides']} gate override(s) — every one needs a "
                       f"written reason in .ai/decision-log/")
        if session["has_anchor"] and not session["logs"] and not session["is_current"]:
            out.append(f"{label}: ended without a session log — `just session-end` was "
                       f"never run, so its outcome was never recorded")
        for log in session["logs"]:
            if log.get("tasks_failed"):
                out.append(f"{label}: failed task(s) {', '.join(log['tasks_failed'])}")
            if log.get("policy_violations"):
                out.append(f"{label}: {log['policy_violations']} policy violation(s) "
                           f"self-reported in {log['_file']}")
            if log.get("spec_drift_detected"):
                out.append(f"{label}: spec drift flagged in {log['_file']}")
            state = log.get("workspace_state_at_start")
            if state and state != "VERIFIED":
                out.append(f"{label}: started in state {state}")
    return out


# ── Rendering ────────────────────────────────────────────────────────────────
def _label(session: dict) -> str:
    if session["start"]:
        return f"{session['start']:%Y-%m-%d %H:%M} UTC"
    return "unknown time"


def _rate(summary: dict) -> str:
    return "no data" if summary["autonomy_rate"] is None else f"{summary['autonomy_rate'] * 100:.1f}%"


def _gate_rate(summary: dict) -> str:
    return "no data" if summary["gate_pass_rate"] is None else f"{summary['gate_pass_rate'] * 100:.1f}%"


def _render_session(session: dict, state: dict) -> None:
    marker = "▶" if session["is_current"] else " "
    suffix = "  (current session)" if session["is_current"] else \
             ("  (no decision data — predates the decision log)" if not session["has_anchor"] else "")
    print(f"\n{marker} {_label(session)}{suffix}")

    summary = session["summary"]
    if not summary["total"]:
        if session["has_anchor"]:
            print("    decisions  no data (0 decision-log entries this session)")
    else:
        policy = summary["policy"]
        if sum(policy.values()):
            print(f"    decisions  ✅ {policy['allow']}  🟡 {policy['ask']}  ⛔ {policy['deny']}"
                  f"   ·  autonomy {_rate(summary)}")
        else:
            print("    decisions  no data (0 policy verdicts this session)")
        gate = summary["gate"]
        if gate["open"] or gate["blocked"]:
            print(f"    gate       ✅ open {gate['open']}  🛑 blocked {gate['blocked']}"
                  f"   ·  pass rate {_gate_rate(summary)}"
                  + (f"  ⚠️  override {summary['overrides']}" if summary["overrides"] else ""))
        else:
            # PH6-T20: a rate needs a denominator — 0 gate verdicts is a known count,
            # never rendered as a fabricated "0%" pass rate.
            print("    gate       no data (0 validation-gate verdicts this session)"
                  + (f"  ⚠️  override {summary['overrides']}" if summary["overrides"] else ""))

    if not (session["is_current"] and not session["logs"]):
        # The current, still-open session prints its own "still open" line below —
        # a second "no data" line there would just repeat it.
        tp = task_throughput(session)
        if tp["status"] == "measured":
            print(f"    task throughput  {tp['count']} completed — {', '.join(tp['tasks'])}"
                  f"  (basis: {tp['basis']})")
        else:
            print(f"    task throughput  no data ({tp['basis']})")

    if session["is_current"]:
        work = state.get("work") or []
        closure = sorted(set(state.get("closure") or []))
        print(f"    budget     work {len(work)}/2 · closure {len(closure)}/3"
              + (f"   [{', '.join(work + closure)}]" if work or closure else ""))

    for log in session["logs"]:
        # tasks_completed is rendered once above (task_throughput), not per-log here.
        changed = (log.get("what_changed") or "").strip()
        if changed and changed != "No summary provided":
            print(f"    outcome    {changed[:160] + ('…' if len(changed) > 160 else '')}")
        commit = log.get("git_commit")
        dirty = log.get("dirty_files_count")
        if commit or dirty is not None:
            print(f"    git        {commit or 'unknown'}"
                  + (f"  ·  {dirty} uncommitted file(s) at close" if dirty else "  ·  clean"))

    if session["is_current"] and not session["logs"]:
        print("    outcome    still open — no session log yet")

    for entry in notable(session["entries"]):
        icon = {"deny": "⛔", "blocked": "🛑", "gate_override": "⚠️"}[entry["decision"]]
        what = entry.get("path") or entry.get("action") or entry.get("tool") or "?"
        why = entry.get("reason") or entry.get("rule") or ""
        print(f"    {icon} {entry['decision']:<13} {what}" + (f"  — {why[:90]}" if why else ""))


def render(sessions: list[dict], unattributed: list[dict], overall: dict,
           state: dict, days: int, limit: int) -> None:
    print("\n" + "═" * 66)
    print("  🔍 GOD MODE AUDIT — what the OS decided, session by session")
    print("═" * 66)

    shown = list(reversed(sessions))[:limit] if limit else list(reversed(sessions))
    scope = f"last {days}d" if days else "all time"
    print(f"\n  Scope: {scope}  ·  {overall['total']} decision entr"
          f"{'y' if overall['total'] == 1 else 'ies'}  ·  {len(sessions)} session(s)"
          + (f"  ·  showing {len(shown)}" if len(shown) != len(sessions) else ""))

    print("\n── OVERALL " + "─" * 55)
    policy = overall["policy"]
    gate = overall["gate"]
    print(f"  Policy    ✅ allow {policy['allow']}   🟡 ask {policy['ask']}   ⛔ deny {policy['deny']}")
    print(f"  Gate      ✅ open {gate['open']}   🛑 blocked {gate['blocked']}")
    print(f"  Override  ⚠️  {overall['overrides']}"
          + (f"   ·  pre-schema lines not counted: {overall['legacy']}" if overall["legacy"] else ""))
    print(f"  Autonomy rate: {_rate(overall)}   (share of guarded tool calls that ran untouched)")
    print(f"  Gate pass rate: {_gate_rate(overall)}   "
          f"(share of validation-gate checks that opened clean)")

    print("\n── SESSIONS (newest first) " + "─" * 39)
    if not shown:
        print("\n  📭 Nothing logged yet. The log fills as the policy hook and the gate run.")
    for session in shown:
        _render_session(session, state)

    if unattributed:
        print(f"\n  ℹ️  {len(unattributed)} entr{'y' if len(unattributed) == 1 else 'ies'} carry no "
              f"session key (written before session-state existed) — counted in OVERALL, "
              f"shown in no session.")

    problems = findings(shown)
    print("\n── FINDINGS " + "─" * 54)
    if problems:
        for problem in problems:
            print(f"  ⚠️  {problem}")
    else:
        print("  ✅ No overrides, no failed tasks, no session closed without a log.")
    print()


def to_json(sessions: list[dict], unattributed: list[dict], overall: dict, state: dict) -> dict:
    return {
        "overall": overall,
        "unattributed_entries": len(unattributed),
        "current_session": str(state.get("session_start") or ""),
        "budget": {"work": state.get("work") or [], "closure": state.get("closure") or []},
        "sessions": [{
            "session": session["key"],
            "start": session["start"].isoformat() if session["start"] else None,
            "is_current": session["is_current"],
            "has_decision_data": session["has_anchor"],
            "summary": session["summary"],
            "task_throughput": task_throughput(session),
            "notable": notable(session["entries"]),
            "logs": [{k: v for k, v in log.items() if k != "_ts"} for log in session["logs"]],
        } for session in reversed(sessions)],
        "findings": findings(sessions),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Per-session audit of the decision log + session logs (read-only).")
    parser.add_argument("--days", type=int, default=0, metavar="N",
                        help="limit to the last N days of logs")
    parser.add_argument("--sessions", type=int, default=0, metavar="N",
                        help="show only the N most recent sessions")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument("--by-model", action="store_true",
                        help="work credited, grouped by the self-reported model (PH10-T05)")
    args = parser.parse_args()

    root = dl.ws_root()

    if args.by_model:
        # PH10-T05: the grouping lives in attribution.py (rule 1 — this
        # module does not re-implement the counting), audit.py only renders.
        import attribution
        v = attribution.by_model(root, days=args.days or None)
        if args.json:
            print(json.dumps(v, indent=2))
        else:
            attribution._print_by_model(v)
        return 0

    entries = dl.read_entries(root, days=args.days or None)
    logs = read_session_logs(root, days=args.days or None)
    state = read_state(root)

    sessions, unattributed = build_sessions(entries, logs, state)
    overall = dl.summarize(entries)          # rule 1: the same counter `just decisions` uses

    if args.json:
        print(json.dumps(to_json(sessions, unattributed, overall, state),
                         indent=2, ensure_ascii=False, default=str))
        return 0

    render(sessions, unattributed, overall, state, args.days, args.sessions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
