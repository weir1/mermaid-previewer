#!/usr/bin/env python3
"""The closure pipeline — the DAG run in one pass, instead of by hand (PH23-T02).

## Why a recipe and not a paragraph

`doc/CLOSURE_AUDIT_BRIEFING.md` records a session that completed its work task
correctly and then spent longer closing than building: four consecutive
`git commit --amend` cycles, a self-review voided twice, doc-stamps drifting after
the commit meant to ship them. Every gate involved was correct. What was missing
was any *enforcement* of which gate's output is another gate's input.

That ordering lived in `AGENTS.md` as prose, and the session that produced the
evidence was following it — it stated the correct order in its own terminal output
at minute 55 and violated it at minute 65. Compliance with a multi-step ordering
rule decays as context fills; a longer paragraph makes that worse, not better. So
the order is executed here rather than recalled.

## The split, and why it is where it is

Two recipes, not one, with the self-review between them:

    prep  →  [ a human reads the diff and records a verdict ]  →  ship

`prep` runs everything a machine can settle: codemap, doc-stamps, doctor, gate.
`ship` runs everything after the verdict: commit, push, close.

The gap is load-bearing. A single end-to-end recipe would have to record the review
itself, and a review nobody read certifies nothing — it would convert the strongest
gate in this workspace into a rubber stamp. The friction that remains is the one
piece of friction that is doing work.

## The order is asked, never re-listed

`PREP` and `SHIP` name step ids from `closure_status.STEPS` and nothing else, and
`ship`'s preconditions come from `closure_status.blockers()`. A test asserts the
two sequences agree with the declared graph, so this module cannot drift into being
a second, disagreeing statement of the closure order — which is precisely the defect
class the phase exists to end.

## Idempotent by construction

Every step delegates to a command that is already idempotent: `codemap` regenerates
from source, `doc_stamp.apply` derives from git and skips what is already right,
`verify-safe` rewrites evidence wholesale. Running `prep` twice is safe and cheap,
which matters because "did I already run this?" at 100k tokens is exactly the
uncertainty that produced the redundant work in the first place.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import closure_status as cs  # noqa: E402

# Step ids from `closure_status.STEPS`. Membership and order are both pinned by
# `tests/test_closure_pipeline.py` against the declared graph.
PREP = ("archive", "codemap", "doc-stamps", "doctor", "gate")
SHIP = ("commit", "push", "close-git-push")

# The step the two halves are split around. Named rather than inlined so the
# test that checks the split can state what it is checking.
REVIEW = "self-review"

# ── The `lite` profile's two-command closure (PH26-T01) ─────────────────────
#
# Same DAG, fewer turns. `WRAP` prepends the docs pass that AGENTS.md requires
# BEFORE `prep-close` ("content before proof") to the whole of `PREP`; `LAND` is
# the whole of `SHIP` plus the two closure records `ship` deliberately cannot run
# for you, plus the handover. Both are built from `PREP`/`SHIP` rather than
# re-listed, so there is no second statement of the closure order to drift — the
# defect PHASE 23 exists to end.
#
# **`REVIEW` is in neither, and that is the entire design.** `wrap` stops before
# it, `land` starts after it. A one-command closure would have to record the
# review itself, and a review nobody read certifies nothing: it would convert the
# strongest gate in this workspace into a rubber stamp while claiming to be
# faster. Speed comes from collapsing the steps a machine can settle. It never
# comes from collapsing the one step whose whole value is that a human read
# something.
#
# `session-end` and `handover` are real work but are not closure-DAG steps —
# nothing downstream is gated on them, so `closure_status.STEPS` does not declare
# them. They are listed here and exempted from the graph-membership test by name,
# rather than being quietly skipped by it.
NON_GRAPH_STEPS = ("session-end", "handover")

WRAP = ("session-end",) + PREP
LAND = SHIP + ("close-docs", "close-issues", "handover")


def ws_root() -> Path:
    return cs.ws_root()


def _run(root: Path, *args: str) -> tuple[int, str]:
    """Run a command in the workspace, streaming nothing, returning what happened."""
    p = subprocess.run(args, cwd=root, capture_output=True, text=True)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


# ── The step actions ─────────────────────────────────────────────────────────
#
# Each returns (ok, one-line detail). They call the same entry points the operator
# would call by hand; none of them re-implements a check.

def _do_archive(root: Path) -> tuple[bool, str]:
    """`archive-memory --apply` — the step that had no caller (PH25-T03).

    `archive_memory.py` has existed since PH6-T01 and nothing automated ever ran it,
    so the ≤200-line rule was enforced by an agent remembering a command. Two
    consecutive sessions had `prep-close` go red on the hot-file budget and cured it
    by hand-typing exactly this. That is the failure the archiver's own docstring
    records one level up — *"so nothing ever did the archiving"* — repeating at the
    next level, and the tool was never the gap.

    It runs first in `PREP` because it rewrites tracked `.ai/memory-bank/` files that
    every later step derives from. `sweep()` is called rather than the recipe, so what
    the pipeline moves is by construction what `just archive-memory` moves — and so
    "moved nothing" stays distinguishable from "recognised nothing" without parsing a
    render function. Nothing is ever deleted: blocks move to the archive and the hot
    file keeps a `[[link]]`.
    """
    import archive_memory
    out = archive_memory.sweep(root, apply=True)
    moved = [b for p in out.get("plans", []) for b in p.get("moving", [])]
    stuck = out.get("over_budget") or []
    if not moved:
        # Said in the words of a no-op, not of work — `prep-close` is documented as
        # safe to run twice, and a second run that re-reported the first run's blocks
        # would make the board describe work this session did not do.
        detail = "nothing to move — no closed history is waiting"
    else:
        files = ", ".join(sorted({p["file"] for p in out["plans"]}))
        detail = f"{len(moved)} block(s) archived from {files}"
    if stuck:
        # Reported, never fatal: a file that is over budget with nothing archivable is
        # bloated by LIVE state, which this tool must not touch. It is `doctor`'s warn.
        detail += (f" · {len(stuck)} file(s) still over budget with nothing movable "
                   f"({', '.join(s['file'] for s in stuck)}) — live state, not history")
    return True, detail


def _do_codemap(root: Path) -> tuple[bool, str]:
    import codemap
    before = codemap.check(root)
    if not before.get("stale") and before.get("exists"):
        return True, "already matches the tree"
    codemap.write(root)
    return True, before.get("reason", "regenerated")


def _do_stamps(root: Path) -> tuple[bool, str]:
    import doc_stamp
    changed = doc_stamp.apply(root)
    if not changed:
        return True, "already current"
    head = ", ".join(changed[:3]) + (f" (+{len(changed) - 3} more)"
                                     if len(changed) > 3 else "")
    return True, f"{len(changed)} stamped: {head}"


def _do_doctor(root: Path) -> tuple[bool, str]:
    """Advisory — it reports, it never stops the pipeline.

    Its own validation-gate check reads red until the gate step below runs, so a
    blocking doctor here would stall the pipeline on a condition the pipeline
    itself resolves two lines later. (F6 in the briefing recommended exactly this.)
    """
    import doctor
    checks = doctor.run_checks(root)
    n_fail, n_warn = doctor.counts(checks)
    return True, f"{len(checks)} checks · {n_fail} fail · {n_warn} warn (advisory)"


def _do_gate(root: Path) -> tuple[bool, str]:
    """`just verify-safe` — the recipe, not a reimplementation of it.

    It runs pre-commit, the suite, and `evidence-pack.sh`; calling the recipe keeps
    one implementation of "what verification means" rather than a second one here
    that could pass while the real one fails.
    """
    rc, out = _run(root, "just", "verify-safe")
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
    return rc == 0, tail[0][:200]


def _do_commit(root: Path, message: str = "", allow_partial: bool = False,
               everything: bool = False) -> tuple:
    """`commit-all`, plus the exit code and output `ship` needs to offer a choice.

    Returns a 3-tuple on failure so `ship` can tell a *refusal with a decision
    behind it* (`commit_scope` exits 5 and 6) from a commit that simply broke.
    The other actions return 2-tuples and are untouched; `_outcome()` normalises.

    `--all` is passed only when asked for (PH24-T12). Passing it unconditionally
    was fix (a): one line, would have unblocked three stuck sessions, and makes
    "stage every change in the tree" the pipeline's default — retiring a guard
    that has caught real omissions three times.
    """
    args = ["just", "commit-all", message or "[AI] chore: sync workspace state"]
    if everything:
        args.append("--all")
    elif allow_partial:
        args.append("--allow-partial")
    rc, out = _run(root, *args)
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-3:]
    detail = " / ".join(t.strip()[:120] for t in tail)
    if rc == 0:
        return True, detail
    return False, detail, {"code": rc, "output": out}


def _do_push(root: Path) -> tuple[bool, str]:
    rc, out = _run(root, "just", "push")
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
    return rc == 0, tail[0][:200]


def _do_close(root: Path) -> tuple[bool, str]:
    rc, out = _run(root, "just", "close", "git-push")
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
    return rc == 0, tail[0][:200]


def _do_session_end(root: Path, summary: str = "") -> tuple[bool, str]:
    """`session-end` — freshen memory, write the session log, rotate the changelog.

    First in `WRAP` because AGENTS.md's "content before proof" rule puts every
    tracked-file write BEFORE `prep-close`: the review hash has to cover everything
    that would be pushed, and a doc written after the gate makes the gate stale.
    Running it here is what lets `wrap` be one turn instead of two.

    It does NOT write `activeContext.md` / `progress.md` / `AI_CHANGELOG.md` for
    you — those are the session's own account of what it did, and a machine that
    generated them would be generating the record the OS reads back as truth.
    """
    args = ["just", "session-end"]
    if summary:
        args += ["--summary", summary]
    rc, out = _run(root, *args)
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
    return rc == 0, tail[0][:200]


def _closer(kind: str):
    """`close docs` / `close issues` — the peers `ship` cannot run for you.

    They write only gitignored state, so they void no review and are safe after the
    push; that is exactly why they carry no edge in the DAG and no blocker will ever
    name them. Which is also why they were the two steps a session forgot: nothing
    downstream complains. `land` runs them because it is the last command.
    """
    def _do(root: Path) -> tuple[bool, str]:
        rc, out = _run(root, "just", "close", kind)
        tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
        return rc == 0, tail[0][:200]
    return _do


def _do_handover(root: Path, next_step: str = "") -> tuple[bool, str]:
    """`handover` — the one file the next session boots from."""
    rc, out = _run(root, "just", "handover", next_step or "pick up from closure-status")
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-1:] or [""]
    return rc == 0, tail[0][:200]


ACTIONS = {
    "session-end": _do_session_end,
    "close-docs": _closer("docs"),
    "close-issues": _closer("issues"),
    "handover": _do_handover,
    "archive": _do_archive,
    "codemap": _do_codemap,
    "doc-stamps": _do_stamps,
    "doctor": _do_doctor,
    "gate": _do_gate,
    "commit": _do_commit,
    "push": _do_push,
    "close-git-push": _do_close,
}


# ── The two passes ───────────────────────────────────────────────────────────

def prep(root: Path | str | None = None, actions: dict | None = None,
         states: dict | None = None) -> dict:
    """Phase 3 → the gate, in DAG order, one pass. Stops at the first hard failure.

    **Phase 1 is checked before anything runs.** Every phase-1 debt — an unagreed
    brief, an unwritten plan, a `[complex]` task whose DoD names no test — is paid
    by editing a *tracked* file, and the gate's freshness is measured against
    tracked files. So a debt discovered after `verify-safe` costs a second full
    verification of work that was already verified. Found while dogfooding this
    very pipeline: the ledger named no test, `work-done` refused, naming it made
    the minutes-old evidence STALE. That is the audit's own root cause reproduced
    inside the fix for it, and the cure is a phase boundary, not an exemption.
    """
    root = Path(root or ws_root())
    actions = actions or ACTIONS
    states = states if states is not None else cs.assess(root)

    owed = [s for s in cs.blockers(states, "gate") if cs.BY_ID[s].phase == 1]
    if owed:
        return {"ok": False, "steps": [], "stopped_at": owed[0], "blockers": owed,
                "reason": ("phase 1 is unsettled, and paying it later would make this "
                           "run's evidence stale — settle first: "
                           + ", ".join(f"{s} ({states.get(s)})" for s in owed))}
    done = []
    for sid in PREP:
        ok, detail = actions[sid](root)
        done.append({"step": sid, "ok": ok, "detail": detail})
        if not ok:
            return {"ok": False, "steps": done, "stopped_at": sid,
                    "reason": f"{sid} failed — {detail}"}
    states = cs.assess(root)
    left = cs.blockers(states, REVIEW)
    return {"ok": not left, "steps": done, "stopped_at": None,
            "blockers": left,
            "reason": ("the tree is harmonized and attested — read the diff and "
                       "record a verdict"
                       if not left else
                       f"still unsettled before a review can bind: {', '.join(left)}")}


# `commit_scope`'s exit codes for the two refusals that have a decision behind
# them, and the marker its `report_exclusions()` prints each left-behind path
# with. Named here rather than matched inline so a change to either side breaks a
# test with a sentence, instead of silently degrading this report to nothing.
COMMIT_PARTIAL = 5
COMMIT_ANNOUNCED = 6
EXCLUSION_MARKER = "✗ "


def _outcome(result) -> tuple:
    """`(ok, detail, info)` from an action returning either 2 or 3 values."""
    ok, detail, *rest = result
    return ok, detail, (rest[0] if rest else {})


def _excluded_paths(output: str) -> list:
    """The paths `commit-all` said it was leaving behind."""
    found = []
    for line in output.splitlines():
        if EXCLUSION_MARKER in line:
            rel = line.split(EXCLUSION_MARKER, 1)[1].strip()
            if rel and rel not in found:
                found.append(rel)
    return found


def _commit_refusal(message: str, info: dict) -> dict | None:
    """Turn a commit refusal that has a choice behind it into that choice.

    PH24-T12. The refusal text was already correct — *"stage the rest, or re-run
    with --all"* — and the pipeline could not obey it, because `--all` was not a
    `ship` flag. So for three sessions running, the documented closure path ended
    in a hand-typed `just commit-all "msg" --all`, which is what PH23-T05 existed
    to eliminate. Both exits are named here as `just ship` commands, i.e. as
    things the operator can actually run.

    Exit 6 is offered a *different* pair, deliberately: it fires precisely when
    `--allow-partial` would commit docs announcing absent code, so offering
    `--allow-partial` there would point at the catastrophe just prevented.
    """
    code = info.get("code")
    if code not in (COMMIT_PARTIAL, COMMIT_ANNOUNCED):
        return None
    left = _excluded_paths(info.get("output", ""))
    named = ", ".join(left[:6]) + (f" (+{len(left) - 6} more)" if len(left) > 6 else "")
    quoted = message.replace('"', r'\"')
    if code == COMMIT_ANNOUNCED:
        reason = (
            "refusing to ship — staged documents announce code this commit does not "
            f"contain{f' ({named})' if left else ''}. `--allow-partial` is not a way "
            "out of this one; it is the case it fires on. Include the code:\n"
            f'     git add -- <the paths above>   ·  then  just ship "{quoted}"\n'
            f'     just ship "{quoted}" --all      take the whole tree, knowingly')
    else:
        reason = (
            f"refusing to ship — this commit would be PARTIAL: {len(left) or 'some'} "
            f"changed path(s) are not attributed to this session"
            f"{f' ({named})' if left else ''}. That is usually an inherited tree — "
            "`write_journal` only records what THIS session wrote. Two ways on, both "
            "deliberate:\n"
            f'     just ship "{quoted}" --all             take everything in the tree\n'
            f'     just ship "{quoted}" --allow-partial   leave them out, deliberately')
    return {"excluded": left, "reason": reason}


def ship(root: Path | str | None = None, message: str = "",
         allow_partial: bool = False, actions: dict | None = None,
         states: dict | None = None, everything: bool = False) -> dict:
    """Commit, push, close — but only over a settled review and an open gate.

    The precondition is `blockers(states, "commit")`, i.e. asked of the graph. That
    is what makes this a brake rather than a convenience: the failure the audit
    recorded was not "the agent forgot a command", it was "the agent ran a command
    whose input had already been invalidated", and only the graph knows that.
    """
    root = Path(root or ws_root())
    actions = actions or ACTIONS
    states = states if states is not None else cs.assess(root)

    left = cs.blockers(states, "commit")
    if left:
        return {"ok": False, "steps": [], "refused": left,
                "reason": ("refusing to ship — these must be settled first: "
                           + ", ".join(f"{s} ({states.get(s)})" for s in left))}
    condemned = cs.invalidations(states)
    if condemned:
        return {"ok": False, "steps": [], "refused": [i["step"] for i in condemned],
                "reason": "refusing to ship — " + " · ".join(i["why"] for i in condemned)}

    done = []
    for sid in SHIP:
        fn = actions[sid]
        ok, detail, info = _outcome(
            fn(root, message, allow_partial, everything) if sid == "commit" else fn(root))
        done.append({"step": sid, "ok": ok, "detail": detail})
        if not ok:
            stopped = {"ok": False, "steps": done, "stopped_at": sid,
                       "reason": f"{sid} failed — {detail}"}
            # A refusal the operator can answer is reported as the answer, not as
            # a failure they must translate into a different command (PH24-T12).
            choice = _commit_refusal(message, info) if sid == "commit" else None
            if choice:
                stopped.update(choice)
            return stopped
    return {"ok": True, "steps": done, "refused": [],
            "reason": "shipped — committed, pushed, git-push closure recorded"}


# ── The `lite` profile's fast path (PH26-T01) ────────────────────────────────

def _profile_refusal(root: Path, recipe: str) -> dict | None:
    """`None` when this workspace declared a profile with `fast_closure`.

    The refusal names the profile it is running and the way on, because a refusal
    that says only "not available here" makes the operator go looking for which
    switch they missed — and that search is the cost this task exists to remove.
    """
    try:
        import workspace_profile
        r = workspace_profile.resolve(root)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "steps": [],
                "reason": f"cannot resolve this workspace's profile ({exc}) — "
                          f"refusing to run `{recipe}` rather than guess."}
    if r["fast_closure"]:
        return None
    return {"ok": False, "steps": [], "profile": r["id"],
            "reason": (
                f"`just {recipe}` is the `lite` profile's closure path, and this "
                f"workspace runs `{r['id']}`. Run the pipeline step by step instead — "
                "`just closure-status` prints the one next action. To change it "
                "deliberately: `just profile-set lite --until YYYY-MM-DD`. A profile "
                "is declared, never inferred, and it moves how many commands closure "
                "costs — never which gates fire.")}


def wrap(root: Path | str | None = None, summary: str = "",
         actions: dict | None = None, states: dict | None = None) -> dict:
    """`session-end` → the whole of `PREP`, in one pass, ending at the review.

    The `lite` half of PH23-T02's split. Every step it runs is a step `prep` already
    ran, in the same DAG order, with the same phase-1 precondition — the saving is
    turns, not checks. It stops exactly where `prep` stops: at the gap where a human
    reads the diff.
    """
    root = Path(root or ws_root())
    refused = _profile_refusal(root, "wrap")
    if refused:
        return refused
    actions = actions or ACTIONS
    ok, detail = actions["session-end"](root, summary)
    if not ok:
        return {"ok": False, "steps": [{"step": "session-end", "ok": False,
                                        "detail": detail}],
                "stopped_at": "session-end",
                "reason": f"session-end failed — {detail}"}
    out = prep(root, actions=actions, states=states)
    out["steps"] = [{"step": "session-end", "ok": True, "detail": detail}] + out["steps"]
    return out


def land(root: Path | str | None = None, message: str = "", next_step: str = "",
         allow_partial: bool = False, actions: dict | None = None,
         states: dict | None = None, everything: bool = False) -> dict:
    """`SHIP`, then the two closure records `ship` cannot run, then the handover.

    Every refusal `ship` makes, `land` makes — it delegates rather than
    re-implementing the precondition, so a voided review or a stale gate stops this
    before the commit exactly as it stops `ship`. What comes after the push are the
    two steps that write only gitignored state, which is why they are safe here and
    why nothing else ever reminded anyone to run them.
    """
    root = Path(root or ws_root())
    refused = _profile_refusal(root, "land")
    if refused:
        return refused
    actions = actions or ACTIONS
    out = ship(root, message, allow_partial=allow_partial, actions=actions,
               states=states, everything=everything)
    if not out["ok"]:
        return out

    for sid in LAND[len(SHIP):]:
        fn = actions[sid]
        ok, detail = fn(root, next_step) if sid == "handover" else fn(root)
        out["steps"].append({"step": sid, "ok": ok, "detail": detail})
        if not ok:
            # Deliberately not fatal to what already happened: the commit and push
            # are done and cannot be undone by returning early. Report the failure
            # and keep going, so one unrecorded closure does not also cost the
            # handover the next session boots from.
            out["ok"] = False
            out["reason"] = (f"shipped, but {sid} failed — {detail}. The commit and "
                             f"push stand; run `just close docs` / `just close issues` "
                             f"/ `just handover` by hand for whatever is missing.")
    if out["ok"]:
        out["reason"] = ("landed — committed, pushed, all 3 closures recorded, "
                         "handover written. Start a fresh session.")
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def _render(result: dict, title: str) -> int:
    print("═" * 62)
    print(f"  {title}")
    print("═" * 62)
    for s in result.get("steps", []):
        print(f"  {'✅' if s['ok'] else '❌'} {s['step']} — {s['detail']}")
    print("  " + "─" * 58)
    if result.get("excluded"):
        # Named in full, and last-but-one: the refusal's two options are only
        # answerable by someone who can see what is being left out.
        print(f"  ⚠️  NOT in this commit — {len(result['excluded'])} changed path(s):")
        for rel in result["excluded"]:
            print(f"       ✗ {rel}")
    print(f"  {'✅' if result['ok'] else '❌'} {result['reason']}")
    if result.get("ok") and title.startswith("PREP"):
        print("     ➤ just review-diff")
        print('     ➤ just self-review pass "what you actually checked"')
        print('     ➤ just ship "the commit message"')
    if result.get("ok") and title.startswith("WRAP"):
        # The gap, spelled out. `wrap` deliberately does not review for you, so the
        # next two commands are the operator's, not the pipeline's.
        print("     ➤ just review-diff        ← read it. This is the step nothing")
        print("                                  else in the pipeline can do for you.")
        print('     ➤ just self-review pass "what you actually checked"')
        print('     ➤ just land "the commit message" "the next step"')
    print("═" * 62)
    return 0 if result["ok"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Run the closure DAG in one pass.")
    ap.add_argument("phase", choices=["prep", "ship", "wrap", "land"])
    ap.add_argument("message", nargs="?", default="",
                    help="commit message (ship/land) · session summary (wrap)")
    ap.add_argument("--next", dest="next_step", default="",
                    help="land: the next step, written into the handover")
    ap.add_argument("--allow-partial", action="store_true",
                    help="ship: permit a commit that excludes attributed paths")
    ap.add_argument("--all", dest="everything", action="store_true",
                    help="ship: stage every change in the tree, including any this "
                         "session did not make. Offered by the partial refusal; never "
                         "the default.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", default="")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else ws_root()

    if args.phase == "prep":
        result = prep(root)
        title = "PREP-CLOSE — harmonize, then attest"
    elif args.phase == "wrap":
        if not args.message:
            print('❌ wrap needs a session summary: just wrap "what this session did"')
            return 2
        result = wrap(root, args.message)
        title = "WRAP — session-end, harmonize, attest"
    elif args.phase == "land":
        if not args.message:
            print('❌ land needs a commit message: just land "what this session did" "next"')
            return 2
        result = land(root, args.message, next_step=args.next_step,
                      allow_partial=args.allow_partial, everything=args.everything)
        title = "LAND — commit · push · close ×3 · handover"
    else:
        if not args.message:
            print("❌ ship needs a commit message: just ship \"what this session did\"")
            return 2
        result = ship(root, args.message, allow_partial=args.allow_partial,
                      everything=args.everything)
        title = "SHIP — commit · push · close"

    if args.json:
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    return _render(result, title)


if __name__ == "__main__":
    raise SystemExit(main())
