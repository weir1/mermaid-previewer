#!/usr/bin/env python3
"""
protocol_score.py — `just protocol-score`: did each session follow AGENTS.md,
from the record? (PH15-T03)

`just audit` (PH5-T03) counts verdicts — allow/ask/deny/blocked — it never checks
them against the protocol. AGENTS.md imposes roughly a dozen concrete obligations
on every session: run session-start, read the memory bank, append the changelog
after every file change, update the memory bank at close, write a handover,
respect the 2+3 budget, record a self-review before `git push`, triage
`knownIssues.md`, only sync TickTick after asking. Nothing replayed the record
against that checklist — the only report was each session's own `what_changed`
prose in `.ai/session-log/`, which is exactly the kind of claim PH15-T01/T09
exist to distrust.

## Four statuses, never five — nothing is inferred from self-report text
  pass / fail    computed from an artefact: a git diff between two recorded
                 `git_commit`s, a session-log's own `dirty_files` (written by
                 `git status --short` at session-end, not typed by the agent),
                 or a decision-log entry with a specific action/decision.
  vacuous_pass   the obligation had nothing to act on this session (no files
                 changed at all, or no `git push` happened) — correctly
                 satisfied, not "passed" in the sense of having been exercised.
  unobservable   no artefact exists for this claim, for any session — a status
                 poll `decision_log.py` deliberately never logs, or conversation
                 -level approval this scorer never sees.
  excluded       the obligation depends on decision-log's structured schema,
                 and this session started before that schema existed
                 (`decision_log.py`'s own first commit — a git object, not a
                 guessed date).

## Rules this module holds itself to (the same three `audit.py` states)
1. It does not re-implement session grouping — `audit.build_sessions()` is the
   one join. This module only adds an `obligations` dict per session.
2. It reads, it never writes — no decision-log entry, no evidence file.
3. An obligation with no artefact is reported **unobservable**, never silently
   dropped or averaged into a pass rate.

No `--days`: the commit-chain reconstruction (each session's base commit is the
previous session's end commit) needs the FULL session history to attribute file
changes correctly — filtering to a window would sever the chain and misattribute
the first session shown. `--sessions N` limits what is PRINTED, after the full
chain is built.

Usage:
  protocol_score.py                 # every session, newest first
  protocol_score.py --sessions 5    # only the 5 most recent
  protocol_score.py --json          # machine-readable
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import audit                # noqa: E402
import decision_log as dl   # noqa: E402
import gate_check           # noqa: E402

PASS, FAIL, VACUOUS, UNOBSERVABLE, EXCLUDED = (
    "pass", "fail", "vacuous_pass", "unobservable", "excluded")
STATUS_ICON = {PASS: "✅", FAIL: "⛔", VACUOUS: "➖", UNOBSERVABLE: "❓", EXCLUDED: "⏳"}

OBLIGATIONS = (
    "session_start_run", "memory_read_at_start", "changelog_appended",
    "memory_bank_updated", "handover_written", "budget_respected",
    "self_review_before_push", "issues_triaged", "ticktick_approval_before_sync",
)

WORK_DONE_RE = re.compile(r"^work-done (PH\d+-T\d+)$")


# ── artefact readers ─────────────────────────────────────────────────────────
def _parse_ts(value) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", *args], cwd=root,
                                       stderr=subprocess.DEVNULL, text=True)
    except Exception:  # noqa: BLE001
        return ""


def _first_commit_introducing(root: Path, pathspec: str, needle: str) -> datetime | None:
    """Earliest commit whose diff introduces `needle` into `pathspec` — a git
    object marking when a *specific mechanism* started existing, not just when
    the file was born. `-S` is pickaxe search (commits that change the
    needle's occurrence count), which is what distinguishes "decision_log.py
    exists" from "session_budget.py actually calls it for work-done"."""
    out = _git(root, "log", "-S", needle, "--format=%aI", "--", pathspec).strip().splitlines()
    return _parse_ts(out[-1]) if out else None


def mechanism_dates(root: Path) -> dict[str, datetime | None]:
    """One cutoff per decision-log-dependent obligation. These are NOT the
    same date: `decision_log.py` itself shipped 2026-08-01T18:48 (PH6-T13),
    `session_budget.py` started logging `work-done` a few hours later that
    same day (22:17), and the self-review closure rule (`REVIEW_REQUIRED`,
    PH7-T04) landed the next day, 2026-08-02T17:21. Collapsing these into one
    shared cutoff was the first version's bug: it reported two 2026-08-01
    pushes as `fail` for missing a self-review record that no rule required
    yet — reproduced live, then fixed by pinning each obligation to the
    commit that actually introduced ITS mechanism."""
    return {
        "budget_respected": _first_commit_introducing(
            root, "scripts/session_budget.py", "work-done {task}"),
        "self_review_before_push": _first_commit_introducing(
            root, "scripts/session_budget.py", "REVIEW_REQUIRED"),
        "issues_triaged": _first_commit_introducing(
            root, "scripts/session_budget.py", "closure recorded"),
    }


def annotate_commit_chain(sessions: list[dict]) -> None:
    """Each session's base commit = the previous session's last known end
    commit; its own end commit = the latest `git_commit` any of its own logs
    recorded. In place — every diff-based obligation below reuses this."""
    prev = None
    for session in sessions:
        session["_base_commit"] = prev
        for log in session["logs"]:
            commit = log.get("git_commit")
            if commit:
                prev = commit
        session["_end_commit"] = prev


def _dirty_to_path(line: str) -> str:
    """A `git status --short` line → its path. Tolerates untracked ('?? path')
    and rename ('R  old -> new', keeping the new path)."""
    line = line.strip()
    if not line:
        return ""
    rest = line[2:].strip() if len(line) > 2 else line
    if " -> " in rest:
        rest = rest.split(" -> ", 1)[1]
    return rest.strip()


def changed_paths(root: Path, session: dict) -> set[str]:
    """Union of committed changes (base..end diff) and each log's own
    uncommitted `dirty_files` at the moment it was written."""
    paths: set[str] = set()
    base, end = session.get("_base_commit"), session.get("_end_commit")
    if base and end and base != end:
        out = _git(root, "diff", "--name-only", base, end)
        paths.update(line.strip() for line in out.splitlines() if line.strip())
    for log in session["logs"]:
        for line in (log.get("dirty_files") or []):
            path = _dirty_to_path(line)
            if path:
                paths.add(path)
    return paths


def real_changes_exist(paths: set[str]) -> bool:
    """Anything outside `gate_check.BOOKKEEPING` — reusing that tuple rather
    than a second hand-written list is deliberate (PH16-T06 is the one place
    it is kept honest against its own comment)."""
    return any(not p.startswith(gate_check.BOOKKEEPING) for p in paths)


# ── per-obligation checks ────────────────────────────────────────────────────
def check_changelog(paths: set[str]) -> dict:
    if not real_changes_exist(paths):
        return {"status": VACUOUS, "evidence": "no non-bookkeeping file changed this session"}
    if "AI_CHANGELOG.md" in paths:
        return {"status": PASS, "evidence": "AI_CHANGELOG.md changed alongside other files"}
    return {"status": FAIL, "evidence": "files changed but AI_CHANGELOG.md did not"}


def check_memory_bank(paths: set[str]) -> dict:
    if not real_changes_exist(paths):
        return {"status": VACUOUS, "evidence": "no non-bookkeeping file changed this session"}
    if any(p.startswith(".ai/memory-bank/") for p in paths):
        return {"status": PASS, "evidence": "a memory-bank file changed alongside other files"}
    return {"status": FAIL, "evidence": "files changed but no memory-bank file did"}


def check_handover(paths: set[str]) -> dict:
    if not real_changes_exist(paths):
        return {"status": VACUOUS, "evidence": "no non-bookkeeping file changed this session"}
    if ".ai/handover/latest.md" in paths:
        return {"status": PASS, "evidence": ".ai/handover/latest.md changed alongside other files"}
    return {"status": FAIL, "evidence": "files changed but .ai/handover/latest.md did not"}


def check_budget(entries: list[dict]) -> dict:
    tasks = set()
    for entry in entries:
        if entry.get("kind") != "policy" or entry.get("decision") != "allow":
            continue
        m = WORK_DONE_RE.match(entry.get("action") or "")
        if m:
            tasks.add(m.group(1))
    if len(tasks) <= 2:
        return {"status": PASS, "evidence": f"{len(tasks)} distinct task(s) credited: {sorted(tasks)}"}
    return {"status": FAIL,
            "evidence": f"{len(tasks)} distinct task(s) credited (cap is 2): {sorted(tasks)}"}


def check_self_review(entries: list[dict]) -> dict:
    pushes = [e for e in entries if e.get("kind") == "gate" and e.get("decision") == "open"
              and (e.get("action") or "").startswith("git push")]
    if not pushes:
        return {"status": VACUOUS, "evidence": "no successful git-push gate entry this session"}
    reviewed = any(e.get("action") == "close git-push" and e.get("decision") in ("allow", "gate_override")
                   for e in entries)
    if reviewed:
        return {"status": PASS,
                "evidence": f"{len(pushes)} push(es), a close-git-push record is present"}
    return {"status": FAIL,
            "evidence": f"{len(pushes)} push(es), no close-git-push record found"}


def check_issues_triaged(paths: set[str], entries: list[dict]) -> dict:
    """PH16-T07: `session_budget.cmd_close` now logs an `allow` decision-log
    entry (`action="close issues"`) for every successful `close issues`, the
    same shape `close git-push` already got — so this can finally be a real
    check instead of a permanent `unobservable`. Same vacuous/pass/fail shape
    as `check_changelog` et al.: nothing to triage if nothing changed, a
    specific action string required (a `close docs` entry does not count),
    and only `allow` counts (a `deny` means the closure was refused, not
    done — `check_budget`'s same distinction for `work-done`)."""
    if not real_changes_exist(paths):
        return {"status": VACUOUS, "evidence": "no non-bookkeeping file changed this session"}
    if any(e.get("action") == "close issues" and e.get("decision") == "allow" for e in entries):
        return {"status": PASS, "evidence": "a 'close issues' record exists for this session"}
    return {"status": FAIL, "evidence": "files changed but no 'close issues' record exists"}


def _unobservable(reason: str) -> dict:
    return {"status": UNOBSERVABLE, "evidence": reason}


def _excluded_or(session: dict, mechanism_since: datetime | None, compute) -> dict:
    start = session.get("start")
    if mechanism_since and start and start < mechanism_since:
        return {"status": EXCLUDED,
                "evidence": "session started before decision-log's structured schema existed "
                            f"({mechanism_since.isoformat(timespec='seconds')})"}
    return compute()


# ── scoring one session ──────────────────────────────────────────────────────
def score_session(root: Path, session: dict, mechanisms: dict[str, datetime | None]) -> dict:
    paths = changed_paths(root, session)
    entries = session["entries"]
    return {
        "session_start_run": _unobservable(
            "session-start is a status poll, deliberately never logged "
            "(decision_log.py's own design rule — a poll is a query, not a decision)"),
        "memory_read_at_start": _unobservable("reading a file leaves no artefact"),
        "changelog_appended": check_changelog(paths),
        "memory_bank_updated": check_memory_bank(paths),
        "handover_written": check_handover(paths),
        "budget_respected": _excluded_or(
            session, mechanisms["budget_respected"], lambda: check_budget(entries)),
        "self_review_before_push": _excluded_or(
            session, mechanisms["self_review_before_push"], lambda: check_self_review(entries)),
        "issues_triaged": _excluded_or(
            session, mechanisms["issues_triaged"], lambda: check_issues_triaged(paths, entries)),
        "ticktick_approval_before_sync": _unobservable(
            "the approval this checks for happens in conversation, which is not part of "
            "the record this scorer replays"),
    }


def _label(session: dict) -> str:
    if session["start"]:
        return f"{session['start']:%Y-%m-%d %H:%M} UTC"
    return "unknown time"


# ── rendering ────────────────────────────────────────────────────────────────
def render(sessions: list[dict], mechanisms: dict[str, datetime | None], limit: int) -> None:
    print("\n" + "═" * 66)
    print("  📜 PROTOCOL SCORE — did each session follow AGENTS.md, from the record")
    print("═" * 66)
    since_bits = ", ".join(
        f"{name} since {dt.isoformat(timespec='seconds') if dt else 'unknown'}"
        for name, dt in mechanisms.items())
    print(f"\n  {len(sessions)} session(s) · {len(OBLIGATIONS)} obligations each  ·  {since_bits}")

    shown = list(reversed(sessions))[:limit] if limit else list(reversed(sessions))
    totals = {s: 0 for s in (PASS, FAIL, VACUOUS, UNOBSERVABLE, EXCLUDED)}
    failing = []
    for session in shown:
        label = _label(session)
        print(f"\n  ── {label} " + "─" * max(0, 50 - len(label)))
        any_fail = False
        for name in OBLIGATIONS:
            ob = session["_obligations"][name]
            totals[ob["status"]] += 1
            if ob["status"] == FAIL:
                any_fail = True
            evidence = ob["evidence"]
            print(f"    {STATUS_ICON[ob['status']]} {name:<32} "
                  f"{evidence[:90]}{'…' if len(evidence) > 90 else ''}")
        if any_fail:
            failing.append(label)

    print("\n── TOTALS " + "─" * 56)
    print(f"  ✅ pass {totals[PASS]}   ⛔ fail {totals[FAIL]}   ➖ vacuous {totals[VACUOUS]}   "
          f"❓ unobservable {totals[UNOBSERVABLE]}   ⏳ excluded {totals[EXCLUDED]}")

    print("\n── FINDINGS " + "─" * 54)
    if failing:
        for label in failing:
            print(f"  ⚠️  {label}: at least one obligation FAILED — see above")
    else:
        print("  ✅ No session failed an observable, non-excluded obligation.")
    print()


def to_json(sessions: list[dict], mechanisms: dict[str, datetime | None]) -> dict:
    return {
        "mechanism_available_since": {
            name: dt.isoformat() if dt else None for name, dt in mechanisms.items()},
        "obligations": list(OBLIGATIONS),
        "sessions": [{
            "session": session["key"],
            "start": session["start"].isoformat() if session["start"] else None,
            "is_current": session["is_current"],
            "has_decision_data": session["has_anchor"],
            "obligations": session["_obligations"],
        } for session in reversed(sessions)],
    }


def scored_sessions(root: Path) -> tuple[list[dict], dict[str, datetime | None]]:
    """The one entry point everything else (CLI, tests) calls: full join +
    commit chain + per-session obligations, in that order."""
    entries = dl.read_entries(root)
    logs = audit.read_session_logs(root)
    state = audit.read_state(root)
    sessions, _unattributed = audit.build_sessions(entries, logs, state)
    annotate_commit_chain(sessions)
    mechanisms = mechanism_dates(root)
    for session in sessions:
        session["_obligations"] = score_session(root, session, mechanisms)
    return sessions, mechanisms


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Replay the decision log + session logs against AGENTS.md's "
                     "obligations, per session (read-only, never writes).")
    parser.add_argument("--sessions", type=int, default=0, metavar="N",
                        help="show only the N most recent sessions")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    root = dl.ws_root()
    sessions, mechanisms = scored_sessions(root)

    if args.json:
        print(json.dumps(to_json(sessions, mechanisms), indent=2, ensure_ascii=False, default=str))
        return 0
    render(sessions, mechanisms, args.sessions)
    return 0


if __name__ == "__main__":
    sys.exit(main())
