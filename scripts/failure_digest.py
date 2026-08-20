#!/usr/bin/env python3
"""
failure_digest.py — a closed gate names what closed it (PH16-T35, Goal G13).

`just verify-safe` runs two independent stages — `pre-commit run --all-files` and
`run_tests.py` — and folded both into a single `$EXIT`. `evidence-pack.sh` then wrote

    "message": "Validation step failed (exit 1) — gate blocked."

and nothing else. Measured 2026-08-15: the six workspaces whose gate was closed on
2026-08-14 (`@anti`, `@debridioalt`, `@iphonesideload`, `@jobscraper`, `@leanmuscle`,
`@skillsforherwebsite`) carry byte-equivalent evidence — `status=failed`, `exit_code=1`,
that one sentence. From the kernel there were six red lights and not one reason, which is
why the standing handover said each needs its own session: not to fix it, to *find out*.

This module turns each stage's captured output into the names of the checks that failed.

**The two stages are not always two runs.** `verify-safe` runs `run_tests.py` separately
only where the suite is *not* already a pre-commit hook — and onboarding installs it as
one, so in the kernel and in every normally-onboarded workspace the suite executes inside
`godmode-tests` and there is no tests log at all. PH24-T08 therefore scans the pre-commit
log with both patterns; without that, `failed_checks` named the hook and never the test
inside it, which is the only fact worth recording.

## Three rules it is built around

**Never the raw output.** `evidence.json` is tracked and committed in 44 workspaces.
Captured tool output carries absolute paths, environment and — for a failing secrets hook —
the secret itself. Only extracted check *names* leave here, capped at `MAX_CHECKS`, and the
cap is *stated* in `note` when it bites, because a silently truncated list reads as a
complete one.

**Absent is unknown, never inferred.** Evidence written before this module has no cause in
it. The honest reading is "this pack did not record one" — `gate_check.failure_cause`
renders exactly that. Synthesising a plausible cause is the `None`-is-not-`set()` mistake
PH16-T24 already paid for.

**This never decides whether the gate opens.** `status` and `exit_code` are computed as they
always were; the digest is additive metadata. A digest that crashes, is missing, or finds
nothing must leave a failing run failing — a broken diagnostic that turns red into green is
strictly worse than the problem it was written for.

Usage:
  failure_digest.py --precommit <log> --tests <log>     # JSON on stdout
"""

import argparse
import json
import re
import sys
from pathlib import Path

#: Enough to diagnose, few enough that the record stays a record. When it bites, `note`
#: says so — the point of a cap is defeated by hiding that it applied.
MAX_CHECKS = 12

#: PH24-T13 — a reason is one sentence, not a payload. Long enough for the assertion
#: messages this repo actually writes (the specimen that motivated the task is 104 chars),
#: short enough that `evidence.json` stays a record of a run rather than a copy of it.
MAX_REASON = 240

#: Appended when `MAX_REASON` bites. Same rule as `note` one field over: a silently
#: truncated value reads as a complete one, so the truncation is *in* the value.
TRUNCATION_MARK = " … (truncated)"

#: `pre-commit`'s own line shape: the hook's human name, dot leader, verdict.
#: `Passed`/`Skipped` share it, so the verdict is matched rather than assumed.
_PRECOMMIT_RE = re.compile(r"^(.+?)\.{3,}(Failed|Error)\s*$")

#: `unittest`'s failure header. The dotted path IS the check name — it is what a session
#: re-runs — so nothing else on the line is kept.
_UNITTEST_RE = re.compile(r"^(?:FAIL|ERROR):\s+\S+\s+\(([^)\s]+)")

#: PH24-T13 — the line `run_tests._render` prints under each failure header on a quiet
#: run, carrying the exception line the test itself produced. Anchored at column 0 for
#: exactly the reason the header is: a cosmetic indent silently starves this parser.
#: A log written by a workspace still running the older `run_tests.py` has none of these,
#: which is why an absent reason degrades to PH24-T08's behaviour instead of failing.
_REASON_RE = re.compile(r"^REASON:\s*(.+?)\s*$")

#: A POSIX **absolute** path, replaced by its basename before a reason is recorded.
#: Relative paths (`tests/test_x.py`, `.ai/memory-bank/progress.md`) are deliberately kept:
#: they are diagnostic, they carry no identity, and they are what these messages are usually
#: *about*, whereas `/Users/<name>/…` carries the operator's name.
#:
#: The lookbehind is the whole correctness of this pattern, and it was added in this task's
#: own self-review after the first cut mangled `.ai/memory-bank/progress.md` into
#: `.aiprogress.md` — it matched the `/memory-bank/progress.md` run and glued the basename
#: onto the orphaned head. Silent, and the wreckage still looks like a path. A leading `/`
#: preceded by a path character is a *separator inside* a relative path, never the root.
#: This is the only place the redaction rule exists — see `_sanitize_reason`.
_ABS_PATH_RE = re.compile(r"(?<![\w.\-/])/(?:[^/\s]+/)+[^/\s]*")

#: The stages, in the order `verify-safe` runs them. Order is the whole reason
#: `failed_stage` can be a single name: the first one to fail is the one that closed the
#: gate, and a session fixing it will reach the second on its own.
STAGES = ("pre-commit", "tests")


def _read(source: str | Path | None) -> str:
    """A log path, its contents, or "" — a log that cannot be read is not a crash.

    The caller is a shell script running inside a failing pipeline. Raising here would
    replace a diagnosable failure with an undiagnosable one.
    """
    if source is None:
        return ""
    if isinstance(source, Path):
        try:
            return source.read_text(errors="replace")
        except OSError:
            return ""
    return source


def _dedup(names: list[str]) -> list[str]:
    """Order-preserving de-duplication across two patterns over one log."""
    out: list[str] = []
    for n in names:
        if n not in out:
            out.append(n)
    return out


def _sanitize_reason(raw: str) -> str:
    """One safe line, or "" — the ONLY implementation of the reason redaction rule.

    Deliberately here and not in `run_tests.py`, which produces the text. The screen and
    both logs are not the risk: `verify-safe`'s stage logs live in a `mktemp -d` removed on
    exit and `.ai/last-test-run.log` is gitignored, and both already hold the full traceback
    with absolute paths in it. The risk this module's docstring names is specific and it is
    this file's own output — `evidence.json` is *tracked and committed in 46 workspaces*.
    So the rule lives at that boundary, once, applied by the field's only writer, which also
    means the digest does not have to trust what the runner printed. A second copy of it in
    the producer is the defect class this repo keeps paying for: one rule, several drifted
    implementations.
    """
    text = " ".join((raw or "").split())          # collapse to a single line
    if not text:
        return ""
    text = _ABS_PATH_RE.sub(lambda m: m.group(0).rsplit("/", 1)[-1] or "<path>", text)
    if len(text) > MAX_REASON:
        text = text[:MAX_REASON].rstrip() + TRUNCATION_MARK
    return text


def _reasons(text: str) -> dict[str, str]:
    """`{unittest id: why it failed}`, paired positionally with the id above it.

    **Never keyed by a hook name, and that is a safety property rather than a tidiness
    one.** A failing secrets hook prints the secret it found; `ruff` prints source lines.
    Assertion messages are written by us and are ours to copy — arbitrary hook output is
    not. So a `REASON:` line binds to the last *unittest id* seen, and a hook verdict
    clears that binding: without the reset, a reason printed under a later hook would
    attach to a test id from an earlier one.

    First reason wins per id, so a repeated marker cannot overwrite a recorded one.
    """
    out: dict[str, str] = {}
    current: str | None = None
    for line in text.splitlines():
        line = line.rstrip()
        matched_id = _UNITTEST_RE.match(line)
        if matched_id:
            current = matched_id.group(1).strip()
            continue
        # Reasons are tested BEFORE hook verdicts: `_PRECOMMIT_RE` matches anything ending
        # in `…Failed`, so an assertion message that happens to end that way would be read
        # as a hook line and silently discard its own reason. A real hook line can never
        # begin with `REASON: `, so this order costs nothing and closes that.
        matched_reason = _REASON_RE.match(line)
        if matched_reason:
            if current and current not in out:
                reason = _sanitize_reason(matched_reason.group(1))
                if reason:
                    out[current] = reason
            continue
        if _PRECOMMIT_RE.match(line):
            current = None                        # a hook verdict ends the block above it
            continue
    return out


def _checks(text: str, pattern: re.Pattern) -> list[str]:
    """Failed check names, in order, de-duplicated. Never the lines around them."""
    out: list[str] = []
    for line in text.splitlines():
        match = pattern.match(line.rstrip())
        if match:
            name = match.group(1).strip()
            if name and name not in out:
                out.append(name)
    return out


def digest(precommit: str | Path | None = None,
           tests: str | Path | None = None) -> dict:
    """`{failed_stage, failed_checks, stages, note}` for a failing run.

    `failed_stage` is the FIRST stage with failures, because that is the one that closed
    the gate. `stages` keeps every stage's names, since a session fixing one wants to see
    the other rather than discover it on the next run.

    Unparseable output yields an empty `failed_checks` with the stage still named: parser
    drift then degrades *visibly* to today's behaviour instead of reporting something wrong.
    """
    pre_text, tests_text = _read(precommit), _read(tests)

    # PH24-T08 — the pre-commit log is scanned with BOTH patterns. Onboarding
    # installs the suite as the `godmode-tests` hook, and `verify-safe` runs a
    # separate tests stage only where it is *not* one — so in the kernel and in
    # every normally-onboarded workspace there is no `tests.log` at all and the
    # unittest ids are printed into this log. Pointing `_UNITTEST_RE` only at the
    # other file is why `failed_checks` named the hook and never the test inside
    # it. The hook name stays first: that is what pre-commit itself reported, and
    # the ids are what it reported *about*.
    found = {
        "pre-commit": _dedup(_checks(pre_text, _PRECOMMIT_RE)
                             + _checks(pre_text, _UNITTEST_RE)),
        "tests": _checks(tests_text, _UNITTEST_RE),
    }
    seen = {"pre-commit": bool(pre_text.strip()), "tests": bool(tests_text.strip())}

    stage = next((s for s in STAGES if found[s]), None)
    if stage is None:
        # Output was captured but nothing matched, or nothing was captured at all. Both
        # are "we could not name it", which is what the reader must be told.
        stage = next((s for s in STAGES if seen[s]), "unknown")

    checks = found.get(stage, [])
    note = ""
    if len(checks) > MAX_CHECKS:
        note = (f"truncated — {len(checks)} checks failed, the first {MAX_CHECKS} are "
                f"listed; re-run the stage for the full list")
        checks = checks[:MAX_CHECKS]

    # PH24-T13 — the reason travels with the name. Filtered to `checks` *after* the cap,
    # so a reason can never describe a check the record does not list, and `MAX_CHECKS`
    # bounds this map for free. An empty map is the honest reading of a log with no
    # `REASON:` lines in it (any workspace still on the older `run_tests.py`): the reason
    # was not recorded, which is not the same as the failure having none.
    reasons = {name: why
               for name, why in _reasons(pre_text if stage == "pre-commit"
                                         else tests_text).items()
               if name in checks}
    return {
        "failed_stage": stage,
        "failed_checks": checks,
        "failed_reasons": reasons,
        "stages": {s: found[s][:MAX_CHECKS] for s in STAGES if found[s] or seen[s]},
        "note": note,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Name the checks that closed the gate, for evidence.json.")
    parser.add_argument("--precommit", default="", help="path to the pre-commit stage log")
    parser.add_argument("--tests", default="", help="path to the test stage log")
    args = parser.parse_args()

    # Never a non-zero exit and never a traceback: the caller is a shell script inside an
    # already-failing pipeline, and a crash here would erase the diagnosis it came for.
    try:
        out = digest(Path(args.precommit) if args.precommit else None,
                     Path(args.tests) if args.tests else None)
    except Exception as exc:  # noqa: BLE001
        out = {"failed_stage": "unknown", "failed_checks": [], "failed_reasons": {},
               "stages": {},
               "note": f"digest failed ({exc.__class__.__name__}) — cause not recorded"}
    print(json.dumps(out, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
