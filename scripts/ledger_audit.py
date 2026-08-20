#!/usr/bin/env python3
"""`just ledger-audit` — does a task the ledger calls pending already exist? (PH9-T14)

Every completion signal in this repo flows one way. `just work-done` writes a
credit to `.ai/decision-log/`; the credit marks a task done. A task finished by a
session that never ran `work-done` — or shipped before the credit machinery
existed — has **no path back to the ledger**. Nothing re-reads a `- [ ]` and asks
whether it is still true.

`goal_progress.py` exists because a ledger `(Complete)` cannot be trusted, and it
splits *credited* from *asserted* rather than merging them. This module asks the
question nobody was asking in the other direction: **an open checkbox is taken on
faith, forever.** In two days that produced four observed failures — four tasks
ticked by hand after the fact, PH9-T13 chosen as P0 on a premise measurement
disproved, PH9-T07 left `(In Progress)` for a day so every gate run stamped the
wrong `task_id`, and an effort forecast inflated by phantom-open work.

**This module reports a disagreement and cannot resolve one.** It never writes —
enforced by a source-scan test, not by intention. Auto-ticking the checkbox would
put a writer into the ledger the effort forecast is computed from, which is the
trust problem relocated rather than fixed. The operator settles it; the tool only
makes the disagreement visible.

**Design law 2 — state the basis or refuse.** A DoD naming nothing checkable is
reported as such, not scored. A task with no DoD is its own bucket. A task with no
checkbox is *not* assumed open, because absent is not unticked.

**Design law 1** — deployed by `onboard_project.sh`, so each workspace audits its
own ledger locally rather than the kernel auditing on its behalf.

**The known false positive, stated rather than hidden:** a DoD naming a file it
intends to *modify* reads as satisfied. That is why a finding carries its matched
artefacts and a coverage ratio instead of a verdict — a wrong call is arguable
rather than silent, the same bargain `off_plan.notice()` makes with its threshold.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_ledger as tl  # noqa: E402

# Statuses that assert a task is finished — same meaning as goal_progress.DONE_STATUSES.
DONE_STATUSES = {"Complete", "Done"}
# Statuses that assert it is not.
OPEN_STATUSES = {"Pending", "In Progress", "Blocked"}

# `code spans` are how this repo names an artefact; prose around them is commentary.
_SPAN_RE = re.compile(r"`([^`]+)`")
_RECIPE_RE = re.compile(r"^just\s+([a-z0-9][a-z0-9._-]*)", re.I)
_MODULE_RE = re.compile(r"^([a-z_][a-z0-9_]*)\.[a-z_][a-z0-9_]*\(?\)?$", re.I)
_PATHISH_RE = re.compile(r"^[\w./-]+\.(py|sh|md|json|yaml|yml|toml|txt)$")


def _recipes(root: Path) -> set[str]:
    """Target names declared in the justfile. A recipe is a line-initial
    `name:` — a `name:` inside a recipe body is a shell command, not a target."""
    f = root / "justfile"
    if not f.is_file():
        return set()
    try:
        text = f.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    return set(re.findall(r"^([a-z0-9][a-z0-9._-]*)\s*(?:[^:\n]*)?:(?!=)", text,
                          re.M | re.I))


def spans(text: str) -> list[str]:
    """The code spans in a DoD, stripped — this repo's one way of naming a thing.

    Public because `conformance.py` needs the same extraction to find test
    references, and a second copy of "what counts as a span" would drift from
    this one the first time either changed.
    """
    return [s.strip() for s in _SPAN_RE.findall(text)]


def artefacts(dod: str) -> list[dict]:
    """The checkable things a DoD names, in the three shapes this repo uses.

    Anything else in a code span — a status word, a flag, a quoted phrase — is
    not an artefact and is deliberately dropped rather than guessed at.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for text in spans(dod):
        kind = ref = ""
        if m := _RECIPE_RE.match(text):
            kind, ref = "recipe", m.group(1)
        elif _PATHISH_RE.match(text):
            kind, ref = "path", text
        elif m := _MODULE_RE.match(text):
            kind, ref = "module", m.group(1)
        if kind and (kind, ref) not in seen:
            seen.add((kind, ref))
            out.append({"kind": kind, "ref": ref})
    return out


def _exists(art: dict, root: Path, recipes: set[str]) -> bool:
    if art["kind"] == "recipe":
        return art["ref"] in recipes
    if art["kind"] == "path":
        ref = art["ref"]
        if (root / ref).exists():
            return True
        # House style writes `onboard_project.sh`, not `scripts/onboard_project.sh` —
        # the script directory is implied. Only a BARE filename gets this fallback:
        # `doc/thing.md` names that path and must not match somewhere else.
        return "/" not in ref and (root / "scripts" / ref).exists()
    return (root / "scripts" / f"{art['ref']}.py").is_file()


def is_open(task: dict) -> bool:
    """Is the ledger claiming this task is unfinished?

    An explicit unticked checkbox or an open status word says yes. **No checkbox
    and no status says nothing**, and nothing is not a claim — a heading-declared
    downstream ledger carries neither, and inventing "open" there would fabricate
    the status this tool exists to question.

    A done signal wins over an open one, so a self-contradicting `- [x] … (Pending)`
    is treated as finished and stays silent. That is the conservative direction for
    a tool whose failure mode is crying wolf: the cost is one missed finding, where
    the reverse would be a false accusation against a task someone already ticked.
    """
    if task.get("status") in DONE_STATUSES or task.get("checked") is True:
        return False
    return task.get("checked") is False or task.get("status") in OPEN_STATUSES


def audit(root: Path | None = None) -> dict:
    """Pure read. Returns findings ranked by coverage, plus the two refusals."""
    root = root or tl.ws_root()
    recipes = _recipes(root)
    findings: list[dict] = []
    uncheckable: list[dict] = []
    no_dod: list[dict] = []
    checked = 0

    for t in tl.all_tasks(root=root):
        if not is_open(t):
            continue
        checked += 1
        row = {"task": t["task"], "title": t["title"][:80], "source": t.get("source", "")}
        if not t.get("dod"):
            no_dod.append(row)
            continue
        arts = artefacts(t["dod"])
        if not arts:
            uncheckable.append({**row, "dod": t["dod"][:120]})
            continue
        present = [a for a in arts if _exists(a, root, recipes)]
        if not present:
            continue  # genuinely pending — the common, correct case
        # A recipe alone is weak: DoDs name `just <recipe>` as a *consumer* at
        # least as often as a deliverable ("`just audit` can report work by
        # model"). A path or module is the thing itself. Both are reported —
        # a suppressed weak finding is an invisible false negative — but they
        # are never presented as the same claim.
        strong = any(a["kind"] in ("path", "module") for a in present)
        findings.append({
            **row,
            "ratio": round(len(present) / len(arts), 3),
            "confidence": "strong" if strong else "weak",
            "present": present,
            "missing": [a for a in arts if a not in present],
            "verdict": (f"claims pending, but {len(present)} of {len(arts)} "
                        f"artefact(s) its DoD names already exist — verify"),
        })

    findings.sort(key=lambda f: (f["confidence"] != "strong", -f["ratio"], f["task"]))
    return {"findings": findings, "uncheckable": uncheckable,
            "no_dod": no_dod, "checked": checked}


def render(r: dict, show_all: bool) -> None:
    print("═" * 78)
    print("  🔎 LEDGER AUDIT — has a pending task quietly become true?")
    print(f"  {r['checked']} open task(s) examined")
    print("═" * 78)

    def show(f):
        pct = int(f["ratio"] * 100)
        print(f"  {f['task']}  ({pct}% of named artefacts present)")
        print(f"     {f['title']}")
        for a in f["present"]:
            print(f"       ✅ {a['kind']}: {a['ref']}")
        for a in f["missing"]:
            print(f"       ⬜ {a['kind']}: {a['ref']} — still absent")
        print(f"     → {f['verdict']}")
        print()

    if not r["findings"]:
        print("\n  ✅ No open task names an artefact that already exists.")
    else:
        strong = [f for f in r["findings"] if f["confidence"] == "strong"]
        weak = [f for f in r["findings"] if f["confidence"] == "weak"]
        if strong:
            print(f"\n  ⚠️  {len(strong)} task(s) claim pending while a script or file "
                  f"their DoD names already exists:\n")
            for f in strong:
                show(f)
        if weak:
            # Kept, and kept separate. Suppressing these would trade a visible
            # false positive for an invisible false negative — the worse deal.
            print(f"  ℹ️  {len(weak)} weaker signal(s) — only a `just` recipe matched, "
                  f"which a DoD often names as a consumer rather than a deliverable:\n")
            for f in weak:
                show(f)

    # The refusals are printed, not hidden: a blind spot that is counted can be
    # argued with, and this is the number `off_plan`'s classifier issue wishes
    # it had.
    if r["uncheckable"]:
        print(f"  🤷 {len(r['uncheckable'])} open task(s) name nothing checkable in "
              f"their DoD — not scored:")
        for t in (r["uncheckable"] if show_all else r["uncheckable"][:5]):
            print(f"       {t['task']}: {t['dod']}")
        if not show_all and len(r["uncheckable"]) > 5:
            print(f"       … {len(r['uncheckable']) - 5} more (--all)")
        print()
    if r["no_dod"]:
        print(f"  ❓ {len(r['no_dod'])} open task(s) have no `DoD:` line at all — "
              f"they cannot be audited, or finished:")
        for t in (r["no_dod"] if show_all else r["no_dod"][:5]):
            print(f"       {t['task']}: {t['title'][:60]}")
        if not show_all and len(r["no_dod"]) > 5:
            print(f"       … {len(r['no_dod']) - 5} more (--all)")
        print()

    print("  This reports a disagreement; it does not settle one. Nothing was "
          "written.")
    print("═" * 78)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Audit the ledger for tasks that "
                                             "claim pending but appear to be done.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--all", action="store_true", help="list every refusal, not the first 5")
    args = ap.parse_args(argv)

    r = audit()
    if args.json:
        print(json.dumps(r, indent=2))
    else:
        render(r, args.all)
    return 1 if r["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
