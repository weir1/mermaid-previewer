#!/usr/bin/env python3
"""
health.py — is this workspace healthy, right now? (PH16-T01)  Run: `just health`

The session briefing had nine sections and none of them answered this. It
reported OS drift, memory freshness, the gate, the changelog, Telegram,
TickTick, tokens and the budget — everything except whether the code works. The
session that filed this task opened green while `doctor` was reporting 2 FAILs
and three tests were red, and found out twenty minutes later by accident.

The validation gate is not this check and cannot be made into it. It answers
*may I push* — is there a fresh `passed` evidence record — and it carries no
failure count for anything. Health answers *does this work*.

## Two verdicts, gathered two different ways, and the reason is cost

  * **doctor runs live.** Measured 0.26s on the kernel. It is a real check of
    the tree as it stands, and it is delegated to `doctor.run_checks()` — this
    module counts the levels it returns and re-implements no check.

  * **the test suite is READ, never run.** Measured 20.4s for 964 tests, about
    100x the opening's 0.19s baseline, in every session in ~40 workspaces. So
    `run_tests.py` leaves a record and this reads it.

## A record is only a verdict about the tree it was taken on

Which is the whole difficulty, because a stale record read as fresh is the exact
bug this module exists to fix, wearing a different hat. So the record is checked
against `gate_check._newest_change()` — the same "newest mtime among tracked and
untracked-not-ignored files" the gate already uses, so the opening and the gate
can never disagree about what moved — and any record older than that renders
**not run since X changed**, never green.

## Nothing here is allowed to be green by default

Every unknown resolves to `not_run`: a missing record, an unreadable one, a
record with no timestamp, a doctor that raised, a workspace with no runner at
all. `run_tests.py` already refuses to call an absent suite a pass; this keeps
that refusal intact rather than quietly rounding it up.

Usage:
    python3 scripts/health.py             # the technical rendering (AI channel)
    python3 scripts/health.py --plain     # plain English (human channel)
    python3 scripts/health.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

# Statuses that are allowed to read as green. Everything else — including every
# flavour of "we could not tell" — is not. Kept as an explicit allowlist so a
# new status added later is un-green until someone decides otherwise.
GREEN_DOCTOR = {"ok", "warn"}      # doctor itself calls WARNs "healthy with warnings"
GREEN_TESTS = {"pass"}


def ws_root() -> Path:
    import gate_check
    return gate_check.ws_root()


def _parse_ts(value: str):
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (ValueError, AttributeError, TypeError):
        return None


# ── the doctor verdict ─────────────────────────────────────────────────────

def doctor_verdict(root: Path) -> dict:
    """FAIL/WARN counts from `doctor.run_checks()`. Never raises.

    A doctor that cannot run is `not_run` — reported as a check that did not
    happen, never as one that passed. This is the branch that made the incident
    possible in the first place: an unasked question rendering as a good answer.
    """
    v = {"status": "not_run", "fails": None, "warns": None, "total": None,
         "failed": [], "detail": ""}
    try:
        import doctor
        checks = doctor.run_checks(root)
    except Exception as exc:  # noqa: BLE001
        v["detail"] = f"doctor could not run ({exc})"
        return v

    fails, warns = doctor.counts(checks)
    v.update(status="fail" if fails else ("warn" if warns else "ok"),
             fails=fails, warns=warns, total=len(checks),
             failed=[label for level, label, _ in checks if level == "FAIL"])
    v["detail"] = f"{len(checks)} checks · {fails} fail · {warns} warn"
    return v


# ── the test-suite verdict ─────────────────────────────────────────────────

def tests_verdict(root: Path) -> dict:
    """The last recorded run, and whether it still describes this tree.

    `status` is one of:
      pass · fail   — a record newer than everything in the tree
      stale         — a real record, but the tree moved after it was taken
      none          — the workspace has no test runner (never a pass)
      not_run       — no record, an unreadable one, or one with no timestamp
    """
    v = {"status": "not_run", "ran": None, "failures": None, "recorded_at": "",
         "changed": "", "changed_at": "", "detail": ""}
    try:
        import run_tests
        rec = run_tests.read_record(root)
    except Exception as exc:  # noqa: BLE001
        v["detail"] = f"the test record could not be read ({exc})"
        return v

    if not rec:
        v["detail"] = ("no test run has been recorded — run `just test` or "
                       "`just verify-safe`")
        return v

    ts = _parse_ts(rec.get("recorded_at", ""))
    if ts is None:
        # A record that cannot say *when* is not a verdict about any particular
        # tree, so it cannot be checked for staleness and must not be trusted.
        v["detail"] = "the test record carries no readable timestamp"
        return v

    v["recorded_at"] = rec.get("recorded_at", "")
    v["ran"] = rec.get("ran")
    failures = rec.get("failures")
    errors = rec.get("errors")
    if failures is not None or errors is not None:
        v["failures"] = (failures or 0) + (errors or 0)

    if rec.get("status") == "none":
        v["status"] = "none"
        v["detail"] = rec.get("basis") or "this workspace has no test runner"
        return v

    # Freshness — borrowed from the gate so the two can never disagree.
    #
    # `_newest_change` returns `0.0` for "I could not tell" as well as for an
    # empty tree, and it takes that path whenever `git ls-files` fails — which
    # includes the eight fleet workspaces that are not git repos at all. Reading
    # that as "nothing has changed" would let a record from any point in the past
    # render green forever, which is this task's own bug in the one module
    # written against it. So an unmeasurable tree is NOT RUN, never a pass.
    try:
        import gate_check
        newest, where = gate_check._newest_change(root)
        skew = gate_check.CLOCK_SKEW_S
    except Exception as exc:  # noqa: BLE001
        newest, where, skew = None, f"freshness unavailable ({exc})", 2.0

    if not newest:
        v["detail"] = (where or "the tree's newest change could not be measured") + \
            " — this record cannot be shown to still describe the code"
        return v   # status stays not_run

    if newest > ts.timestamp() + skew:
        v["status"] = "stale"
        v["changed"] = where
        v["changed_at"] = datetime.fromtimestamp(
            newest, timezone.utc).isoformat(timespec="seconds")
        v["detail"] = (f"`{where}` changed after this record was written — it is "
                       "not a verdict on the code as it stands now")
        return v

    v["status"] = "pass" if rec.get("status") == "pass" else "fail"
    n = "an unknown number of" if v["ran"] is None else str(v["ran"])
    v["detail"] = f"{n} test(s), {'unknown' if v['failures'] is None else v['failures']} failing"
    return v


# ── the whole verdict ──────────────────────────────────────────────────────

def check(root: Path | str | None = None) -> dict:
    """Both verdicts plus the overall one. Pure read; writes nothing."""
    root = Path(root) if root is not None else ws_root()
    d = doctor_verdict(root)
    t = tests_verdict(root)

    # A known failure outranks an unknown: "2 checks are failing" is more useful
    # than "we are not sure", even when both are true.
    if d["status"] == "fail" or t["status"] == "fail":
        verdict = "unhealthy"
    elif d["status"] in GREEN_DOCTOR and t["status"] in GREEN_TESTS:
        verdict = "healthy"
    else:
        verdict = "unknown"
    return {"doctor": d, "tests": t, "verdict": verdict,
            "green": verdict == "healthy"}


# ── channel 1: the AI reading the briefing ─────────────────────────────────

_ICON = {"healthy": "✅", "unhealthy": "❌", "unknown": "⚠️ "}


def lines(h: dict) -> list[str]:
    """The technical rendering: counts, statuses, paths."""
    d, t = h["doctor"], h["tests"]

    out = []
    if d["status"] == "not_run":
        out.append(f"  ⚠️  doctor: NOT RUN — {d['detail']}")
    else:
        icon = {"ok": "✅", "warn": "⚠️ ", "fail": "❌"}[d["status"]]
        line = f"  {icon} doctor: {d['fails']} fail · {d['warns']} warn ({d['total']} checks)"
        if d["failed"]:
            line += " — " + "; ".join(d["failed"])
        out.append(line)

    if t["status"] in ("pass", "fail"):
        icon = "✅" if t["status"] == "pass" else "❌"
        out.append(f"  {icon} tests: {t['status'].upper()} — {t['detail']} "
                   f"(recorded {t['recorded_at']})")
    elif t["status"] == "stale":
        out.append(f"  ⚠️  tests: NOT RUN since {t['detail']} "
                   f"(last record {t['recorded_at']})")
    elif t["status"] == "none":
        out.append(f"  ⚠️  tests: NONE — {t['detail']}")
    else:
        out.append(f"  ⚠️  tests: NOT RUN — {t['detail']}")

    if h["verdict"] != "healthy":
        fixes = []
        if d["status"] == "fail" or d["status"] == "not_run":
            fixes.append("`just doctor`")
        if t["status"] != "pass":
            fixes.append("`just verify-safe`")
        out.append("  ⚡ ACTION: run " + " then ".join(fixes) + " before trusting this tree.")
    return out


def render(h: dict) -> None:
    print("\n".join(lines(h)))


# ── channel 2: the person reading the terminal ─────────────────────────────
#
# The PH9-T17 rule: two audiences, two renderings, never one string doing both.
# This one carries no level names, no exit codes, no filenames-as-jargon — the
# same facts said the way he would say them.

READABLE_WIDTH = 119  # tests/test_health.py asserts every rendered line is < 120 chars


def _wrap(line: str, width: int = READABLE_WIDTH) -> list[str]:
    """Wrap one rendered line to the readability budget, for ANY input — a template with
    a variable substitution (a changed file's path, a doctor check's own label) is
    unbounded, so the renderer must hold the budget regardless of what filled it in."""
    if len(line) <= width:
        return [line]
    stripped = line.lstrip(" ")
    indent = line[: len(line) - len(stripped)]
    cont_indent = indent + "  "
    return textwrap.wrap(stripped, width=width, initial_indent=indent,
                          subsequent_indent=cont_indent, break_on_hyphens=False) or [line]


#: A per-task file under `.ai/`: the *whole* basename is a ledger task id. Keyed to that
#: convention and not to a directory list (PH24-T15) — `plans/` was enumerated here and the
#: leak came back through `prework/` the moment the convention spread. The directory is
#: captured so the sentence can still say what the file was; one it has never heard of gets
#: the generic phrase below rather than a leaked id.
_TASK_FILE_RE = re.compile(r"(?:^|/)\.ai/([^/]+)/PH\d+-T\d+\.[A-Za-z0-9]+$")

#: What each per-task file IS, in his words — one entry per directory that really holds
#: task-id-named files today (`.ai/plans`, `.ai/prework`, `.ai/delegation`; `.ai/reviews`
#: names its records by timestamp and hash, so it is deliberately absent rather than
#: guessed at). A directory absent from this map is still redacted — the fallback is
#: deliberately vague *and* still true, which is the one place vagueness is right here.
_TASK_FILE_PHRASES = {
    "plans": "the written plan for a task",
    "prework": "the pre-work brief for a task",
    "delegation": "the delegation contract for a task",
}
_TASK_FILE_FALLBACK = "a file the OS keeps for one task"


def _in_plain_english(path: str) -> str:
    """A changed file, named the way the operator would name it. (PH16-T28, PH24-T15)

    The human channel bans ledger task ids — they are a token he has to decode —
    and a plan file's *name is one*: `.ai/plans/PH16-T28.md`. This line rendered
    the path verbatim, so any session whose newest change was its own plan leaked
    an id into the plain-English message, and `test_health`'s whole-message check
    went red for it.

    That is not a rare accident: `plan-before-code` tells every `[complex]` task
    to write the plan before the first edit, so the session most likely to open
    on a stale-test verdict is precisely the one whose newest file is a plan. It
    surfaced as a suite failure that reproduced on some runs and not others,
    which is how a state-dependent leak looks from the outside.

    **PH24-T15 — and it is the same bug, not a new one.** The fix above matched
    `.ai/plans/` alone, the directory in front of it, and the paragraph above
    *predicted* the recurrence without generalising to it. On 2026-08-17 (i)
    pre-commit's own end-of-files fixer touched `.ai/prework/PH25-T03.md`, that
    became the tree's newest change, and this line leaked `PH25-T03` — so the
    suite failed, `evidence.json` recorded FAILURE and **the validation gate
    closed**, blocking commit, push and tt-sync on a workspace with nothing wrong
    with it. By then the convention had spread to 26 briefs and a delegation
    contract, and the unfixed case had become the *likelier* one: a brief is
    mandatory for every task, a plan only for `[complex]` ones. So the rule is now
    keyed to the naming convention, and an unknown directory is covered by
    construction rather than by whoever remembers to add it.

    Only per-task files are translated, and only when the whole basename is the id:
    `doc/report-PH24-T15.md` is a document with a name and is still printed exactly,
    as is `scripts/gate_check.py` — telling him which script changed is the useful
    behaviour, and blanket vagueness would satisfy the vocabulary rule while
    destroying the point of the sentence. `lines()` (the engineer's channel) keeps
    the raw path either way.
    """
    match = _TASK_FILE_RE.search(path or "")
    if not match:
        return path
    return _TASK_FILE_PHRASES.get(match.group(1), _TASK_FILE_FALLBACK)


def plain_lines(h: dict) -> list[str]:
    """The health verdict as a person would want to read it."""
    d, t = h["doctor"], h["tests"]
    out = ["🩺 IS THIS WORKSPACE HEALTHY?"]

    if d["status"] == "not_run":
        out.append("   The self-checks could not be run, so nothing here says the setup is sound.")
    elif d["status"] == "fail":
        n = d["fails"]
        out.append(f"   No — {n} of its {d['total']} self-checks {'is' if n == 1 else 'are'} "
                   "failing right now:")
        out += [f"     • {label}" for label in d["failed"]]
    elif d["status"] == "warn":
        out.append(f"   The setup is sound — all {d['total']} self-checks pass, "
                   f"{d['warns']} with a note.")
    else:
        out.append(f"   The setup is sound — all {d['total']} self-checks pass.")

    if t["status"] == "pass":
        n = "an unknown number of" if t["ran"] is None else str(t["ran"])
        out.append(f"   The tests were last run after the most recent change and all "
                   f"{n} of them passed.")
    elif t["status"] == "fail":
        f = t["failures"]
        out.append(f"   The tests are failing — {'some' if f is None else f} of "
                   f"{'them' if t['ran'] is None else t['ran']} did not pass.")
    elif t["status"] == "stale":
        out.append(f"   The tests have not been run since {_in_plain_english(t['changed'])} "
                   "was changed, so nothing here proves the code still works.")
    elif t["status"] == "none":
        out.append("   There are no tests in this workspace, so nothing can prove the "
                   "code works.")
    else:
        out.append("   The tests have not been run, so nothing here proves the code works.")

    if h["verdict"] != "healthy":
        out.append("   To find out:  just doctor   ·   just verify-safe")
    return [wrapped for raw in out for wrapped in _wrap(raw)]


def render_plain(h: dict) -> None:
    print("\n".join(plain_lines(h)))


def main() -> int:
    ap = argparse.ArgumentParser(description="Is this workspace healthy right now?")
    ap.add_argument("--plain", action="store_true",
                    help="plain English for a human reader")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default=None, help="workspace root (default: git top-level)")
    args = ap.parse_args()

    h = check(args.root)
    if args.json:
        print(json.dumps(h, indent=2))
    elif args.plain:
        render_plain(h)
    else:
        print("\n" + "═" * 54)
        print("  🩺 WORKSPACE HEALTH")
        print("═" * 54)
        render(h)
        print()
    return 0 if h["verdict"] == "healthy" else 1


if __name__ == "__main__":
    sys.exit(main())
