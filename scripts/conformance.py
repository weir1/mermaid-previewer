#!/usr/bin/env python3
"""`just conformance` — does a task the ledger calls DONE still hold? (PH15-T01)

`ledger_audit.py` asks whether a **pending** task has quietly become true. Its first
act is `if not is_open(t): continue`, so the moment a task is ticked it leaves that
audit's field of view permanently. This module is the mirror, and it exists because
the unguarded direction is the one that decays: **a done task that has quietly
stopped being true.**

Delete `scripts/off_plan.py` tomorrow and PH9-T05 still reads `- [x]`; `goal_progress`
still counts it toward G5, `effort_forecast` still prices the remaining work as if it
were finished, and `just standing` still reports that goal met. Nothing in the repo
disagrees with a stale tick. When it opened, this kernel had **73 done tasks and no
mechanism had ever re-read one.**

## The distinction the whole module is built on

*Verified* and *not disproved* are different claims, and collapsing them is the defect.
So there are four buckets and only the first is a pass:

  * `verified`     — every artefact its DoD names still resolves, and every test it
                     names is still collected by this workspace's own runner
  * `broken`       — something it named is gone. Reported **with the path**, exit 1
  * `unverifiable` — it has a DoD, but that DoD names nothing checkable
  * `no_dod`       — no `DoD:` line at all

`unverifiable` and `no_dod` are never scored as passing and never quietly excluded from
the denominator. The headline therefore carries **two** figures — verified out of
*checkable*, and checkable out of *all done* — because either alone is a lie. That is
design law 2 (state the basis or refuse) applied to the OS's account of itself.

## Rules this file holds itself to

1. **It does not re-implement the parser.** Spans, artefact shapes and existence come
   from `ledger_audit`; test collectibility comes from `run_tests.collects()`, which
   already owns that contract for `just resolve-issue`. A second answer to "does this
   artefact exist" would drift from the first the week either changed.
2. **It reads, it never writes.** No ledger edit, no evidence, no state, no decision-log
   entry — pinned by a source-scan test, not by intention. A verifier able to edit the
   ledger could fake its own result, which is precisely the trust problem it exists to
   remove. Like a gate *status* poll, running it is a query, not a decision.
3. **It fails only on disproof.** `unverifiable` does not fail the run. 42 of this
   kernel's own done tasks land there on day one, and an exit code that is red from the
   moment it ships is an exit code everyone learns to ignore — the same reasoning that
   made the codemap check a WARN for 38 workspaces rather than a FAIL.

**The known false positive, stated rather than hidden:** existence is not correctness.
A file emptied to a stub still resolves, so its task still reads verified. That is the
same bargain `ledger_audit` strikes, and why every row carries the artefacts it matched
instead of a bare verdict — a wrong call is arguable rather than silent. Closing it
properly needs a DoD that names its own test, which is PH15-T04, not this.

Usage:
  conformance.py                # every done task, broken first
  conformance.py --no-tests     # skip runner probes (fast; artefacts only)
  conformance.py --all          # list the unverifiable ones individually
  conformance.py --json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger_audit as la  # noqa: E402
import run_tests  # noqa: E402
import task_ledger as tl  # noqa: E402


def artefacts_of(dod: str) -> list[dict]:
    """Delegated verbatim — one definition of what a DoD names."""
    return la.artefacts(dod)


def test_refs(dod: str) -> list[str]:
    """The test references a DoD *declares*, in `path::Class::test` form.

    The `::` is the declaration. A DoD saying "add tests" or naming `just test`
    mentions testing without naming a test, and a mention is not a declaration —
    the same rule `plan.py` applies to `[complex]` and `plan_workspace.py` applies
    to a goal. A bare `tests/test_x.py` is already a *path* artefact, handled by
    `artefacts_of`, so it is deliberately not duplicated here.
    """
    out: list[str] = []
    for span in la.spans(dod):
        if "::" in span and not span.startswith("just "):
            if span not in out:
                out.append(span)
    return out


def is_done(task: dict) -> bool:
    """Exactly the complement of `ledger_audit.is_open` — one definition, inverted.

    A task carrying neither a checkbox nor a status is claiming nothing, and
    nothing is not a completion any more than it was an opening. It belongs to
    neither tool, and is counted by neither.
    """
    if la.is_open(task):
        return False
    return task.get("status") in la.DONE_STATUSES or task.get("checked") is True


def audit(root: Path | None = None, check_tests: bool = True) -> dict:
    """Pure read. Returns the four buckets plus the two denominators."""
    root = root or tl.ws_root()
    recipes = la._recipes(root)
    verified: list[dict] = []
    broken: list[dict] = []
    unverifiable: list[dict] = []
    no_dod: list[dict] = []
    done = unclaimed = 0

    for t in tl.all_tasks(root=root):
        if not is_done(t):
            # Neither tool examines a row claiming nothing, by design — but the
            # gap between "done" and "not open" must be a stated number rather
            # than a silent difference between two counts.
            if not la.is_open(t):
                unclaimed += 1
            continue
        done += 1
        row = {"task": t["task"], "title": t["title"][:80], "source": t.get("source", "")}

        if not t.get("dod"):
            no_dod.append(row)
            continue

        arts = artefacts_of(t["dod"])
        refs = test_refs(t["dod"]) if check_tests else []
        if not arts and not refs:
            unverifiable.append({**row, "dod": t["dod"][:120]})
            continue

        missing = [a for a in arts if not la._exists(a, root, recipes)]
        present = [a for a in arts if a not in missing]
        caveats: list[str] = []
        for ref in refs:
            probe = run_tests.collects(ref, root)
            if not probe["collected"]:
                missing.append({"kind": "test", "ref": ref, "why": probe["reason"]})
            elif not probe["verified"]:
                # The runner could not be *asked*. Recorded, never counted as proof.
                caveats.append(f"{ref} — {probe['reason']}")

        entry = {**row, "present": present, "missing": missing, "caveats": caveats,
                 "checked": len(arts) + len(refs)}
        (broken if missing else verified).append(entry)

    broken.sort(key=lambda r: (-len(r["missing"]), r["task"]))
    verified.sort(key=lambda r: r["task"])
    # Complex completed tasks whose DoD names no test — grandfathered by PH15-T04,
    # reported as a gap figure rather than silently ignored.
    complex_no_test = sum(
        1 for t in tl.all_tasks(root=root)
        if is_done(t) and t.get("complex") and not t.get("test_ref")
    )
    return {"verified": verified, "broken": broken, "unverifiable": unverifiable,
            "no_dod": no_dod, "done": done, "unclaimed": unclaimed,
            "checkable": len(verified) + len(broken),
            "complex_no_test": complex_no_test}


def render(r: dict, show_all: bool) -> None:
    print("═" * 78)
    print("  ✅ CONFORMANCE — does a task the ledger calls DONE still hold?")
    print(f"  {r['done']} completed task(s) examined")
    print("═" * 78)

    if not r["done"]:
        print("\n  🤷 The ledger declares no completed task — nothing to re-verify.")
        print("═" * 78)
        return

    if r["broken"]:
        print(f"\n  ❌ {len(r['broken'])} completed task(s) name something that is GONE:\n")
        for f in r["broken"]:
            print(f"  {f['task']}  ({len(f['missing'])} of {f['checked']} missing)")
            print(f"     {f['title']}")
            for a in f["missing"]:
                why = f" — {a['why']}" if a.get("why") else ""
                print(f"       ❌ {a['kind']}: {a['ref']}{why}")
            for a in f["present"]:
                print(f"       ✅ {a['kind']}: {a['ref']}")
            print("     → the ledger calls this done; the artefact it named no longer exists")
            print()
    else:
        print("\n  ✅ No completed task names an artefact that has since disappeared.")

    if r["verified"]:
        print(f"\n  ✅ {len(r['verified'])} completed task(s) still resolve every artefact "
              f"their DoD names.")
        caveated = [f for f in r["verified"] if f["caveats"]]
        for f in caveated:
            print(f"     ⚠️  {f['task']} — collection NOT verified: {f['caveats'][0]}")

    if r["unverifiable"]:
        print(f"\n  🤷 {len(r['unverifiable'])} completed task(s) have a DoD that names nothing "
              f"checkable — NOT counted as verified:")
        shown = r["unverifiable"] if show_all else r["unverifiable"][:5]
        for f in shown:
            print(f"       {f['task']}: {f['dod']}")
        if len(shown) < len(r["unverifiable"]):
            print(f"       … {len(r['unverifiable']) - len(shown)} more (--all)")

    if r["no_dod"]:
        print(f"\n  ❓ {len(r['no_dod'])} completed task(s) have no `DoD:` line at all — "
              f"nothing was ever checkable:")
        shown = r["no_dod"] if show_all else r["no_dod"][:5]
        for f in shown:
            print(f"       {f['task']}: {f['title']}")
        if len(shown) < len(r["no_dod"]):
            print(f"       … {len(r['no_dod']) - len(shown)} more (--all)")

    print("\n" + "─" * 78)
    blind = r["done"] - r["checkable"]
    if r["checkable"]:
        pct = round(100 * len(r["verified"]) / r["checkable"])
        print(f"  {len(r['verified'])}/{r['checkable']} checkable task(s) still verified ({pct}%)"
              f"  ·  {r['checkable']}/{r['done']} completed task(s) were checkable at all")
    else:
        print(f"  0 of {r['done']} completed task(s) could be checked at all — "
              f"no coverage figure is computable, and none is invented")
    if blind:
        print(f"  ⚠️  {blind} completed task(s) rest on nobody's verification — "
              f"that is the size of the blind spot, not a pass")
    if r["unclaimed"]:
        print(f"  ⚠️  {r['unclaimed']} further task(s) claim neither done nor open, so "
              f"neither this nor `just ledger-audit` examines them")
    if r.get("complex_no_test"):
        print(f"  ⚠️  {r['complex_no_test']} completed [complex] task(s) name no collectible test — "
              "gap under PH15-T04 (grandfathered; future tasks are blocked at `work-done`)")
    print("  Existence is not correctness: a stub still resolves. Nothing was written.")
    print("═" * 78)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Re-verify every task the ledger calls done. Pure read.")
    ap.add_argument("--no-tests", action="store_true",
                    help="skip runner collection probes (artefacts only)")
    ap.add_argument("--all", action="store_true", help="list every unverifiable task")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    r = audit(check_tests=not args.no_tests)
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        render(r, args.all)
    return 1 if r["broken"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
