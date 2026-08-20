#!/usr/bin/env python3
"""
session_open.py — what this workspace is for, its standing, and why (PH9-T04).

The operator's own words, `.ai/plan.md` §4: *"after reading handover & relevant
docs, ai will explain user in plain simple english what tasks he is going to do
this session, why, what & what impact it will be — go over planned docs & goals."*
PH9-T08 (goal-linked progress) and PH9-T09 (effort forecast) can both already
answer "how are we doing", but neither is ever shown — `session_start.py` runs
eight sections and none of them says why the session exists. This module leads
the briefing with that.

## What "why" and "impact" resolve to

- **Why** = the goal a queued task declares, resolved to that goal's title via
  `.ai/plan.md`. A task declaring no `Goal:` is reported **unmapped** — the same
  answer `goal_progress.py` gives, never silently dropped (the DoD says so
  explicitly, because omitting it would look like every task has a home).
- **Impact** = concrete artefacts pulled from the task's own `DoD:` text —
  backtick spans that look like a file, a script, or a `just` command. This is
  the only already-written, structured source of "what will this touch" in the
  repo; DoDs are written naming exactly these things because PH7-T02 made DoD
  the thing credit is checked against. A DoD with no such span reports the
  refusal, not an invented guess — "so it can be wrong detectably" means impact
  is either a real, quoted artefact or explicitly absent.

## What "this session" means at the point session-start fires

Nothing has been decided yet — the AI has not picked a task. So:
  - a task already `(In Progress)` is reported as exactly that;
  - otherwise the first ≤2 `(Pending)` tasks in ledger order are reported as
    **candidates**, matching the 2-work-slot session budget — never claimed as
    a decision that has already been made.

Pure read throughout, same as `goal_progress.py` / `effort_forecast.py` (PH7-T09
is this repo's standing lesson about a read path that turns out to write).

Usage:
  session_open.py                 # short opening (~5 lines)
  session_open.py --full          # also render full goal progress + forecast
  session_open.py --json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

MAX_QUEUED = 2
# A backtick span worth calling "impact": a path (has a slash or a known
# extension) or a `just` invocation. Plain backticked words ("PH9-T04",
# "session-start") are not artefacts — they name the task or a step, not a
# checkable thing that changed.
_ARTEFACT_RE = re.compile(r"[./]|\.(py|md|json|ya?ml|sh)$|^just\b", re.I)


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _artefacts(dod: str) -> list[str]:
    """Concrete, checkable things named in a DoD's backtick spans, or none."""
    return [span for span in re.findall(r"`([^`]+)`", dod or "") if _ARTEFACT_RE.search(span)]


def opening(root: Path | None = None, max_queued: int = MAX_QUEUED) -> dict:
    """What this workspace is for, its standing, and why/impact for what's queued.

    Refuses (via `ok: False`) only when the plan machinery cannot be read at
    all or no plan exists yet — mirroring `goal_progress.progress()`'s own
    refusal so a caller never has to learn a second shape of "unavailable".
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "purpose": "", "essence": None, "plan_status": None,
          "progress": None, "forecast": None, "queued": [], "retired": None, "done": False}
    try:
        import effort_forecast
        import goal_progress
        import plan_workspace
        import task_ledger
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"cannot read the plan machinery ({exc})."
        return out

    # A retired workspace's opening IS the retirement notice — checked before
    # anything else, because there is nothing left to plan around (PH9-T11).
    retired = plan_workspace.retirement(root=root)
    if retired["retired"]:
        out.update(ok=True, retired=retired)
        return out

    plan = plan_workspace.validate(root=root)
    out["plan_status"] = plan["status"]
    if not plan["exists"]:
        out["reason"] = plan["reason"]
        return out

    out["ok"] = True
    out["purpose"] = plan_workspace.purpose_line(root=root)
    out["essence"] = plan_workspace.essence(root=root)
    out["progress"] = goal_progress.progress(root)
    out["forecast"] = effort_forecast.forecast(root)

    # Every declared goal met -> nothing left that the plan asked for, even if
    # a stray goal-less task remains in the ledger (PH9-T11's done-signal).
    if goal_progress.all_met(out["progress"]):
        out["done"] = True
        return out

    goal_titles = {g["id"]: g["title"] for g in plan["goals"]}
    tasks = {t["task"]: t for t in task_ledger.all_tasks(root=root)}
    active = task_ledger.active_task(root=root)

    if active and active in tasks:
        queued, label = [tasks[active]], "in_progress"
    else:
        queued = [t for t in tasks.values() if t["status"] == "Pending"][:max_queued]
        label = "candidate"

    for t in queued:
        gid = t.get("goal") or ""
        out["queued"].append({
            "task": t["task"], "title": t["title"], "label": label,
            "goal": gid or None,
            "goal_title": goal_titles.get(gid) if gid else None,
            "unmapped": not gid,
            "dangling": bool(gid) and gid not in goal_titles,
            "impact": _artefacts(t.get("dod", "")),
        })
    return out


# ── plain English for the human channel (PH9-T17) ───────────────────────────
#
# `render_open` below is the TECHNICAL rendering: task ids, goal ids, percent
# mapped. It is what the AI reads and what `just standing` prints, and it stays
# exactly as it was. This section is the second rendering, for the one field a
# person actually sees in the transcript (`systemMessage`, PH9-T16).
#
# The operator asked for "the complete goal of the workspace ... in simple
# English non-tech language". Two things follow, and both are enforced by tests
# rather than left to whoever edits this next:
#   · the goals are NAMED, not counted — "11 goal(s)" answers nothing;
#   · no ledger ids, no `Goal:`, no `DoD`, no "unmapped". Those are this repo's
#     vocabulary, not his. If he wants them, `just standing` is one command.
#
# A goal's title is written as "<headline> — <how you will know it is done>".
# The headline is the plain-English half by construction, so that is what this
# renders; the acceptance criteria after the dash are the technical half.

_ID_PREFIX_RE = re.compile(r"^\s*PH\d+-T\d+\s*[:—-]?\s*", re.I)
#: A ledger id **wherever it appears**, not only where it prefixes the title (PH16-T20).
#: `_ID_PREFIX_RE` is anchored to `^`, so it stripped the id in front of a ledger title and
#: left every other one standing — and "Deploy the PH16-T17 refusals to the fleet" is how a
#: deploy task is naturally written, since a deploy task names what it deploys. That title
#: reddened both real-workspace guards at once, minutes later inside `verify-safe`, reporting
#: the leaked token rather than the title that wrote it.
#:
#: A run like `PH16-T11/T12/T13` or `PH16-T11 + PH16-T12` is ONE reference written short, so
#: the continuation forms are part of the match: stripping the first id and leaving `/T12/T13`
#: standing would trade a leak for a gash. Trailing punctuation and spacing come with it, and
#: the caller collapses what is left — a strip that leaves `Deploy  to the fleet` has only
#: moved the tell from a machine token to a double space.
_ID_ANYWHERE_RE = re.compile(
    r"\bPH\d+-T\d+(?:\s*[/+,&]\s*(?:PH\d+-)?T\d+)*\b\s*[:—–-]?\s*", re.I)
_MARKER_RE = re.compile(r"^\s*\[[a-z-]+\]\s*", re.I)
_MD_RE = re.compile(r"\*\*|__|`")
_ANNOTATION_RE = re.compile(r"\s*(?:✅|⚠️|🏁|❌)")
# A ledger status marker is bookkeeping, never part of what the task is.
_STATUS_RE = re.compile(
    r"\s*\((?:Complete|Completed|Pending|In Progress|Blocked|Deferred|Ongoing|Resolved)\)",
    re.I)
_DASH_RE = re.compile(r"\s+[—–]\s+")
# A sentence end, not any period. `.ai/codemap.md exists` and `Node.js apps`
# both contain `.` + text and must not be treated as the end of the headline —
# so the period has to follow a word and precede either a capitalised next
# sentence or the end of the string.
_SENTENCE_END_RE = re.compile(r"(?<=[A-Za-z0-9)\]])\.(?:\s+(?=[A-Z])|$)")


def _headline(text: str) -> str:
    """The plain-English half of a title, and nothing else.

    Real ledger titles are far messier than a fixture suggests — they carry the
    task id, a `[complex]` marker, markdown, and several sentences of rationale
    aimed at a future maintainer. Only the first clause is written for a reader.
    So: strip the markers, then cut at whichever comes first, the em-dash that
    separates a goal's headline from its acceptance criteria or the end of the
    first sentence.

    Status annotations (`✅ **met** …`, `⚠️ **un-met** …`) go too — a stamp is a
    fact *about* a goal rather than part of it, and an un-met note in particular
    cites the very task ids that must never reach this channel.
    """
    # Markup first: the ledger writes the marker as `` `[complex]` ``, so a
    # marker strip that runs before the backticks come off silently misses it.
    s = _STATUS_RE.sub("", _MD_RE.sub("", (text or "").strip()))
    for _ in range(3):  # the id and the marker appear in either order
        s = _MARKER_RE.sub("", _ID_PREFIX_RE.sub("", s))

    cut = len(s)
    for rx, offset in ((_DASH_RE, 0), (_ANNOTATION_RE, 0), (_SENTENCE_END_RE, 1)):
        m = rx.search(s)
        if m:
            cut = min(cut, m.start() + offset)
    s = s[:cut].strip().rstrip(".").strip()

    # Ids left INSIDE the surviving clause go last, AFTER the cut (PH16-T20). Stripping them
    # first breaks the cut: `_SENTENCE_END_RE` needs a capital to mark the next sentence, and
    # removing the id from "…not just the AI. PH9-T04 rendered it…" leaves a lowercase `r`
    # there, so the headline ran on and swallowed the sentence it was supposed to stop before.
    # Whitespace is collapsed after the removal — a strip that leaves a double space has only
    # changed which artefact tells the reader a machine wrote this.
    return re.sub(r"\s{2,}", " ", _ID_ANYWHERE_RE.sub("", s)).strip().rstrip(".").strip()


_EMPHASIS_RE = re.compile(r"(?<!\w)\*(?=\S)|(?<=\S)\*(?!\w)")
WRAP_WIDTH = 96


def _plain_purpose(purpose: str) -> str:
    return _EMPHASIS_RE.sub("", _MD_RE.sub("", (purpose or "").strip()))


def _wrap(text: str, indent: str, hanging: str | None = None) -> list[str]:
    """Wrap to a readable column. A wall of text is not plain English."""
    import textwrap
    lines = textwrap.wrap(text, width=WRAP_WIDTH, initial_indent=indent,
                          subsequent_indent=hanging if hanging is not None else indent)
    return lines or [indent + text]


def plain_lines(o: dict) -> list[str]:
    """The opening as a person would want to read it. Never ledger vocabulary."""
    if o.get("retired"):
        r = o["retired"]
        return ["🏁 This workspace is finished and closed"
                + (f" ({r['reason']})" if r.get("reason") else ".")]
    if not o["ok"]:
        return ["📋 This workspace has no plan yet, so there is nothing to report about "
                "its goals.", "   You can write one with:  just plan-workspace"]

    out = ["📋 WHAT THIS WORKSPACE IS ABOUT"]
    out += _essence_lines(o)

    prog = o["progress"]
    if prog and prog["ok"]:
        goals = prog["goals"]
        met = sum(1 for g in goals if g["met"])
        t = prog["totals"]
        out += ["", "📊 WHERE WE ARE RIGHT NOW",
                f"   {met} of {len(goals)} goals finished."]
        if t["percent"] is not None:
            out.append(f"   {t['done']} of {t['mapped']} pieces of planned work are "
                       f"finished — about {t['percent']}%.")
        # Named, not enumerated. He rejected the checkbox column and then asked
        # for "which tasks completed which remain" — so the same facts run as
        # two prose sentences instead of eleven rows.
        done_titles = [_headline(g["title"]) for g in goals if g["met"]]
        left_titles = [_headline(g["title"]) for g in goals if not g["met"]]
        if done_titles:
            out += _wrap("Already done: " + "; ".join(done_titles) + ".", "   ", "      ")
        if left_titles:
            out += _wrap("Still to go: " + "; ".join(left_titles) + ".", "   ", "      ")
        fc = o["forecast"]
        # `sessions_remaining`, not `sessions`: PH9-T17 read a key
        # `effort_forecast.forecast()` has never returned, so this line — the
        # "how much effort is left" he asked for by name — silently never
        # printed. A `.get()` guard degrades to nothing, which is why no test
        # and no reader caught it. Pinned in `TheForecastLineActuallyPrints`.
        if fc and fc["ok"] and fc.get("sessions_remaining") is not None:
            out.append(f"   At the recent pace, roughly {fc['sessions_remaining']} more "
                       f"working session(s) to finish what is left.")

    out += ["", "🔧 WHAT THIS SESSION IS PICKING UP"]
    if o.get("done"):
        out.append("   Everything planned is done. Nothing is queued.")
    elif not o["queued"]:
        out.append("   Nothing is queued right now.")
    else:
        decided = o["queued"][0]["label"] == "in_progress"
        out.append("   Already underway:" if decided
                   else "   Not decided yet — these are the next candidates:")
        for q in o["queued"]:
            out += _wrap(f"• {_headline(q['title'])}", "   ", "     ")
            out += _achievement_lines(q, prog)

    out += ["", "   (Every goal, with how far along it is:  just standing --full)"]
    return out


def _two_sentences(text: str) -> str:
    """"one or two liner essence" — his cap, applied at render time only.

    Trims for the human channel; the plan keeps whatever was written and
    `just standing` still prints it whole, so nothing is lost, only shortened.
    Reuses `_SENTENCE_END_RE` (which already knows a filename's dot is not a
    full stop) rather than splitting on ".".
    """
    text = (text or "").strip()
    ends = [m.start() + 1 for m in _SENTENCE_END_RE.finditer(text)]
    return text if len(ends) < 2 else text[:ends[1]].strip()


def _essence_lines(o: dict) -> list[str]:
    """What it is, why it exists, how it helps — or which of those is unwritten.

    Never composes any of the three. They are the user's statement of intent and
    the OS's job is to carry it, not to author it; an unwritten one is reported
    as unwritten, with the file to write it in.
    """
    e = o.get("essence") or {}
    out = _wrap(_two_sentences(_plain_purpose(e.get("what") or o.get("purpose", "")))
                or "What this workspace is has not been written down yet.", "   ")
    if e.get("why"):
        out += _wrap(f"Why it exists: {_plain_purpose(e['why'])}", "   ", "      ")
    if e.get("helps"):
        out += _wrap(f"How it helps: {_plain_purpose(e['helps'])}", "   ", "      ")

    absent = [f'"{name}"' for name, key in (("Why it exists", "why"), ("How it helps", "helps"))
              if not e.get(key)]
    if absent:
        verb = "is" if len(absent) == 1 else "are"
        out += _wrap(f"{' and '.join(absent)} {verb} not written down yet — add "
                     f'{"it" if len(absent) == 1 else "them"} to .ai/plan.md under '
                     f'"What I want".', "   ", "      ")
    return out


def _achievement_lines(q: dict, prog: dict | None) -> list[str]:
    """"after finishing what it acheives for our main goal" — his words.

    `Helps with: <goal>` (PH9-T17) named the goal and stopped there, which does
    not answer what finishing the task achieves. Where the goal stands is the
    missing half, and it is read from `goal_progress` rather than restated here,
    so the number in the opening and the number in `just standing` cannot drift.
    """
    if q["unmapped"] or q["dangling"]:
        return ["     Not linked to any goal yet."]

    line = f"When this is done: {_headline(q['goal_title'])}"
    g = next((x for x in (prog or {}).get("goals", []) if x["id"] == q["goal"]), None)
    if g and g.get("percent") is not None:
        line += (f" — that goal is {g['percent']}% there so far "
                 f"({g['done']} of {g['total']} pieces).")
    return _wrap(line, "     ", "       ")


def render_plain(o: dict) -> None:
    print("\n".join(plain_lines(o)))


def _why(q: dict) -> str:
    if q["unmapped"]:
        return "unmapped — declares no Goal:"
    if q["dangling"]:
        return f"{q['goal']} — dangling, no such goal in .ai/plan.md"
    return f"{q['goal']} — {q['goal_title']}"


def render_open(o: dict, full: bool = False) -> None:
    if o.get("retired"):
        r = o["retired"]
        when = f" {r['at']}" if r["at"] else ""
        print(f"  🏁 RETIRED{when} — {r['reason'] or 'no reason recorded'}")
        print("     This workspace is marked done — nothing queued, nothing to plan for.")
        return

    if not o["ok"]:
        print(f"  🛑 Workspace intent unavailable — {o['reason']}")
        return

    print(f"  📍 {o['purpose'] or '(no purpose line written in .ai/plan.md yet)'}")

    prog, fc = o["progress"], o["forecast"]
    bits = []
    if prog and prog["ok"]:
        t = prog["totals"]
        pct = f" ({t['percent']}%)" if t["percent"] is not None else ""
        bits.append(f"{len(prog['goals'])} goal(s) · {t['done']}/{t['mapped']} mapped tasks done{pct}")
    if fc and fc["ok"]:
        bits.append(fc["reason"])
    if bits:
        print(f"     Standing: {' · '.join(bits)}")

    if o.get("done"):
        print("     🎉 DONE — every declared goal is met. Nothing queued this session.")
    elif not o["queued"]:
        print("     Nothing (In Progress) and nothing (Pending) — nothing queued this session.")
    else:
        for q in o["queued"]:
            tag = "In progress" if q["label"] == "in_progress" else "Next candidate"
            print(f"     {tag}: {q['task']} — {q['title'][:70]}")
            print(f"       Why: {_why(q)}")
            if q["impact"]:
                print(f"       Impact: {', '.join(q['impact'])}")
            else:
                print("       Impact: (no concrete artefact named in this task's DoD)")

    # PH22-T02: what the CURRENT version rung still owes, in the operator's own
    # half of the contract. Silent where no `.ai/versions.md` exists (every
    # workspace today) and in the kernel, which keeps its own ladder — a briefing
    # that nags about a file this task deliberately does not write on his behalf
    # would be noise from the day it shipped.
    try:
        import versions as versions_mod
        cv = versions_mod.validate()
        if cv["exists"] and not cv["kernel"]:
            rung = versions_mod.current_rung()
            if rung:
                left = [f for f in rung["features"] if not f["verified"]]
                done = len(rung["features"]) - len(left)
                print(f"     Version rung {rung['name']}: {done}/{len(rung['features'])} "
                      f"feature(s) verified")
                for f in left[:3]:
                    print(f"       ⬜ {f['plain']}")
                if len(left) > 3:
                    print(f"       …and {len(left) - 3} more")
    except Exception:  # noqa: BLE001 — a briefing must never break the session
        pass

    print("     Full standing → `just standing`")

    if full:
        print()
        import effort_forecast
        import goal_progress
        goal_progress.render(prog, show_unmapped=True)
        print()
        effort_forecast.render(fc)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="What this workspace is for, its standing, and why/impact for what's queued.")
    ap.add_argument("--full", action="store_true",
                    help="also render full goal progress + effort forecast.")
    ap.add_argument("--plain", action="store_true",
                    help="plain English for a human reader: goals named, no ledger ids.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    o = opening(ws_root())
    if args.json:
        print(json.dumps(o, indent=2, default=str))
    elif args.plain:
        render_plain(o)
    else:
        render_open(o, full=args.full)
    return 0 if o["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
