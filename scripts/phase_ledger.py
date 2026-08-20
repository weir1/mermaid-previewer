#!/usr/bin/env python3
"""
phase_ledger.py — a phase ledger structural enough to stamp a version from. (PH19-T01 Slice 1)

`version_plan.ladder()` (PH9-T03) refused any bump unless **every** goal in the whole plan was
met — the wrong denominator once a plan accretes goals faster than any one phase ships (this
workspace: G1..G17 and counting). Each `## PHASE N — Title (vX.Y.Z)` heading in `tasks.md`
targets one rung of the ladder; this module is what lets `version_plan.py` propose off *that*,
instead of off the whole plan at once.

## Two jobs, because a version stamp is only as honest as what it is computed from

1. **Structure.** A phase heading is a contract today only in prose: nothing checks it declares
   a target version, what "done" means for the phase (`**Exit:**`), or what it supersedes
   (`**Retires:**`). `check()` is the FAIL `doctor` names.
2. **Completion, and the ladder.** `next_stampable()` walks phases above the current stamped
   version in document order and reports the highest one reachable without skipping an open
   phase — plus, by name, the phase currently blocking the next rung.

## Phase completion is computed from the merged ledger, by id prefix — not by position

A phase's own `tasks.md` section is not always where its tasks are declared: PHASE 7's tasks
(`PH7-T05`..`T07`) live in `activeContext.md`, and a position-based scan under the heading would
find zero and misreport "complete" by the empty case — the exact "never invent a number" trap
this codebase keeps naming. Instead: gather every task from `task_ledger.all_tasks()` (already
merges both ledger files) whose id matches `PH<N>-T\\d+`, and ask `ledger_audit.is_open()` —
the one, already-tested "is this task still open" predicate — rather than a second one.

## The walk's two unchecked assumptions, both now checked (PH19-T04, 2026-08-11)

A phase invisible to the walk is a rung skipped in silence, and there were two ways to be
invisible. Both were live, and the second was found only because it nearly swallowed the fix
for the first.

1. **No `## PHASE` heading at all.** `unheaded_phases()` reconciles the headings against the
   merged ledger: any `PH<N>-T##` id with no `## PHASE <N>` heading is named. This was live in
   v3.8 Roles (three OPEN tasks, filed under a `## DEFERRED —` heading the regex does not match)
   and v3.9 Intent (fully shipped, declared only in `activeContext.md`) — measured from v3.7.0,
   the walk reported PHASE 15 (v3.10.0) as the blocker, stepping over both.
2. **A heading in the wrong place.** The walk is **document-ordered**, which climbs the ladder
   only if the file's order is ascending-version order. `misordered_headings()` is what checks
   that. It matters more than it sounds: the obvious home for PHASE 8's heading was beside its
   tasks at the bottom of the file, which would have left v3.10.0 ahead of v3.8.0 in the walk —
   the rung still skipped, while the check in (1) reported green.

Both are `check()` FAILs, so `doctor` names them. Absent `tasks.md` stays silent: child
workspaces have none, and a bare FAIL would redden `doctor` on 41 of them for a file they are
not supposed to have.

## Once stamped, a version is not re-verified

`next_stampable()` only inspects phases whose target version is strictly above the current
stamped one. Nothing un-ships a shipped rung, and re-checking below it would need mapping tasks
whose own numbering already drifted from their phase's (PHASE 4.6's tasks are `PH4-T22`..`T25`,
not `PH4.6-T##`) for no benefit.

## Collisions: the same id, two different tasks

`task_ledger.all_tasks()` dedupes by id and keeps the first declaration found — exactly the
shape that let `PH6-T20` silently resolve to an unrelated, already-shipped task instead of the
real, open "session-eval dashboards" one for three days (found and fixed 2026-08-10, this same
task). `id_collisions()` is the standing guard: every raw declaration, not deduped, grouped by
id, flagged when two declarations of the same id score too *low* on Dice word-overlap to be a
restatement of the same task — reusing `off_plan._tokens()`'s scoring rather than a second
similarity metric, the same threshold shape as its `MATCH_THRESHOLD`.

Usage:
  phase_ledger.py                 # structural check, human-readable
  phase_ledger.py --json
  phase_ledger.py --next-version 3.4.0   # what would stamp next, from that current version
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import task_ledger  # noqa: E402
import ledger_audit  # noqa: E402
import off_plan  # noqa: E402

TASKS_MD = Path(".ai") / "docs" / "tasks.md"

# `PH19-T04` → phase "19". The phase part tolerates a decimal because this
# ledger has one (PHASE 4.6) — though its own tasks are numbered `PH4-T##`,
# so the decimal form has never actually appeared on a task id.
LEDGER_TASK_RE = re.compile(r"^PH(?P<num>\d+(?:\.\d+)?)-T\d+$")

# "is there a heading for this phase at all", asked without requiring it to be
# well-formed. `unheaded_phases()` needs this looser question than
# `PHASE_HEADING_RE` answers: a heading that exists but is malformed is already
# its own FAIL in `bad_headings`, and calling it *missing* on top of that both
# double-reports one defect and points the reader at the wrong repair — adding
# a second heading for a phase that already has one.
PHASE_NUM_RE = re.compile(r"^##\s+PHASE\s+(?P<num>\d+(?:\.\d+)?)\b")

# `## PHASE 19 — Subtraction (v3.14.0): theme text` — the canonical shape every
# heading is normalized to by this task. An optional `(qualifier)` (PHASE 7's
# `(finish)`) may sit between the number and the dash. Trailing text after the
# version — a stale `— IN PROGRESS` label, a `✅` sigil — is tolerated rather
# than required to be absent: normalizing the *labels* is a separate, human
# judgment call (some are still honestly true); this parser's only job is to
# find the number, title and version, wherever the line runs afterward.
PHASE_HEADING_RE = re.compile(
    r"^##\s+PHASE\s+(?P<num>\d+(?:\.\d+)?)"
    r"(?:\s*\([^)]*\))?"
    r"\s*[—–-]\s*"
    r"(?P<title>.+?)"
    r"\s*\(v(?P<version>\d+\.\d+\.\d+)\)"
    r"(?:\s*[:：]\s*(?P<theme>.*))?"
    # Deliberately no `$` here: a stale `— IN PROGRESS` label or a trailing
    # `✅` sigil must not stop the number/title/version from being read.
)
EXIT_RE = re.compile(r"^\*\*Exit:?\*\*\s*(.+)$", re.I)
RETIRES_RE = re.compile(r"^\*\*Retires:?\*\*\s*(.+)$", re.I)

# Below this Dice score, two declarations of the same id are presumed to
# describe different tasks rather than restate one — see id_collisions().
COLLISION_THRESHOLD = 0.35


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def parse_headings(root: Path | None = None) -> list[dict]:
    """Every `## PHASE` heading in `tasks.md`, structurally parsed.

    `version`/`exit`/`retires` are `""` when absent — never guessed. `exit`/
    `retires` are read from the heading's own block (up to the next `## `
    line at any level), because both lines routinely sit paragraphs below
    the heading itself (see PHASE 19/20's own text).
    """
    root = root or ws_root()
    path = root / TASKS_MD
    if not path.is_file():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    n = len(lines)
    headings: list[dict] = []
    i = 0
    while i < n:
        line = lines[i]
        if line.startswith("## PHASE"):
            m = PHASE_HEADING_RE.match(line.strip())
            entry = {"line": i + 1, "raw": line.strip(), "num": None, "title": "",
                      "version": "", "theme": "", "exit": "", "retires": ""}
            if m:
                entry["num"] = m.group("num")
                entry["title"] = m.group("title").strip()
                entry["version"] = m.group("version")
                entry["theme"] = (m.group("theme") or "").strip()
            j = i + 1
            while j < n and not lines[j].startswith("## "):
                stripped = lines[j].strip()
                em = EXIT_RE.match(stripped)
                if em and not entry["exit"]:
                    entry["exit"] = em.group(1).strip()
                rm = RETIRES_RE.match(stripped)
                if rm and not entry["retires"]:
                    entry["retires"] = rm.group(1).strip()
                j += 1
            headings.append(entry)
            i = j
            continue
        i += 1
    return headings


def unheaded_phases(root: Path | None = None) -> list[str]:
    """Phase numbers with real `PH<N>-T##` tasks in the merged ledger and no
    `## PHASE <N>` heading in `tasks.md`. (PH19-T04)

    The ledger and the headings are two records of the same fact, and until
    this existed nothing reconciled them. A phase in this state is invisible
    to `next_stampable()` — the walk can report it neither as shipped nor as
    blocking, so it silently skips the rung. Measured live 2026-08-11:
    PHASE 8 (v3.8.0, three open tasks, sitting under a `## DEFERRED —`
    heading the regex does not match) and PHASE 9 (v3.9.0, fully shipped,
    declared only in `activeContext.md`) were both in exactly this state, and
    `--next-version 3.7.0` reported PHASE 15 (v3.10.0) as the blocker.

    Only this direction is a defect. A heading with no tasks yet is the
    inverse case and already handled honestly by `phase_status`, which calls
    it "not machine-checkable" rather than complete.

    Absent `tasks.md` → `[]`, never a failure: child workspaces have no
    `.ai/docs/tasks.md` at all, and a bare FAIL would turn `doctor` red on 41
    of them for a file they are not supposed to have. Same absent-is-fine /
    present-and-bad-is-FAIL asymmetry the codemap, guide and protocol_budget
    checks already use.
    """
    root = root or ws_root()
    if not (root / TASKS_MD).is_file():
        return []
    headed = set()
    for h in parse_headings(root=root):
        num = h["num"]
        if not num:
            loose = PHASE_NUM_RE.match(h["raw"])
            num = loose.group("num") if loose else None
        if num:
            headed.add(num)
    declared = set()
    for t in task_ledger.all_tasks(root=root):
        m = LEDGER_TASK_RE.match(t["task"])
        if m:
            declared.add(m.group("num"))
    return sorted(declared - headed,
                  key=lambda n: tuple(int(p) for p in n.split(".")))


def misordered_headings(root: Path | None = None) -> list[dict]:
    """Headings whose target version does not ascend from the one before it
    in the file. (PH19-T04)

    `next_stampable()` filters headings to those above the current stamp and
    then walks them in **document order** — which is a walk up the ladder only
    if document order is ascending-version order. It is, for all 18 real
    headings, and until now nothing checked it.

    That gap nearly swallowed this task's own fix: PHASE 8's tasks live at the
    bottom of `tasks.md` under `## DEFERRED`, and putting its heading with them
    would have left PHASE 15 (v3.10.0, far earlier in the file) ahead of
    PHASE 8 (v3.8.0) in the walk — v3.8.0 skipped exactly as before, while
    `unheaded_phases()` reported green. The fix would have been asserted
    rather than achieved.

    Equal versions count as not-ascending: two phases cannot target the same
    rung, or the walk picks one arbitrarily by position. Headings with no
    parseable version are skipped, not read as `0.0.0` — that is already its
    own FAIL in `bad_headings`, and treating it as zero would both
    double-report it and make every later heading look misordered against it.
    """
    root = root or ws_root()
    bad: list[dict] = []
    prev = None
    for h in parse_headings(root=root):
        v = _semver_tuple(h["version"]) if h["version"] else None
        if v is None:
            continue
        if prev is not None and v <= prev[0]:
            bad.append({"heading": h["raw"], "line": h["line"],
                        "version": h["version"], "after": prev[1]})
        prev = (v, h["version"])
    return bad


def check(root: Path | None = None) -> dict:
    """Structural verdict `doctor` reads: bad headings + id collisions +
    phases with ledger tasks and no heading to stamp from (PH19-T04).

    Pure read throughout — the same PH7-T09 discipline every check in this
    tier follows.
    """
    root = root or ws_root()
    headings = parse_headings(root=root)
    bad_headings = []
    for h in headings:
        missing = []
        if not h["version"]:
            missing.append("target version")
        if not h["exit"]:
            missing.append("Exit:")
        if not h["retires"]:
            missing.append("Retires:")
        if missing:
            bad_headings.append({"heading": h["raw"], "line": h["line"], "missing": missing})
    collisions = id_collisions(root=root)
    unheaded = unheaded_phases(root=root)
    misordered = misordered_headings(root=root)
    return {"ok": not bad_headings and not collisions and not unheaded and not misordered,
           "headings": headings, "bad_headings": bad_headings,
           "collisions": collisions, "unheaded_phases": unheaded,
           "misordered_headings": misordered}


def phase_tasks(num: str, root: Path | None = None) -> list[dict]:
    """Ledger tasks whose id matches `PH<num>-T\\d+`, from the merged ledger
    (both files) — not restricted to physical position under the heading."""
    root = root or ws_root()
    prefix_re = re.compile(rf"^PH{re.escape(num)}-T\d+$")
    return [t for t in task_ledger.all_tasks(root=root) if prefix_re.match(t["task"])]


def phase_status(num: str, root: Path | None = None) -> dict:
    """Completion verdict for one phase number. Zero matched tasks is
    "not machine-checkable", never "complete" — see module docstring."""
    root = root or ws_root()
    tasks = phase_tasks(num, root=root)
    out = {"num": num, "task_count": len(tasks), "complete": None,
          "open_tasks": [], "basis": ""}
    if not tasks:
        out["basis"] = f"no task matched `PH{num}-T##` in the merged ledger — not machine-checkable"
        return out
    open_ids = [t["task"] for t in tasks if ledger_audit.is_open(t)]
    out["open_tasks"] = open_ids
    out["complete"] = not open_ids
    out["basis"] = f"{len(tasks)} task(s) matched by id prefix"
    return out


def _semver_tuple(v) -> tuple[int, int, int] | None:
    m = re.match(r"^(\d+)\.(\d+)\.(\d+)", str(v))
    return tuple(int(g) for g in m.groups()) if m else None  # type: ignore[return-value]


def next_stampable(current: str, root: Path | None = None) -> dict:
    """The next version this ladder proposes, walking phase headings above
    `current` in document order.

    Stops at the first phase that is not provably complete and names it as
    the blocker; the proposal (if any) is the target version of the last
    phase confirmed complete before that point. Never skips an open phase
    to reach a later, already-shipped one — see module docstring's design
    law on sequential order.
    """
    root = root or ws_root()
    out = {"ok": False, "proposal": None, "blocker": None, "shipped": [], "reason": ""}
    cur = _semver_tuple(current)
    if cur is None:
        out["reason"] = f"current version {current!r} is not valid SemVer."
        return out

    candidates = []
    for h in parse_headings(root=root):
        if not h["version"]:
            continue
        v = _semver_tuple(h["version"])
        if v is None or v <= cur:
            continue
        candidates.append(h)

    out["ok"] = True
    if not candidates:
        out["reason"] = "no phase heading targets a version above current."
        return out

    for h in candidates:
        st = phase_status(h["num"], root=root)
        if st["complete"] is True:
            out["shipped"].append({"phase": h["num"], "version": h["version"], "title": h["title"]})
            continue
        out["blocker"] = {"phase": h["num"], "title": h["title"], "version": h["version"],
                          "open_tasks": st["open_tasks"], "basis": st["basis"]}
        break

    if out["shipped"]:
        last = out["shipped"][-1]
        out["proposal"] = {"next": last["version"],
                           "reason": f"PHASE {last['phase']} ({last['title']}) complete",
                           "phases": [s["phase"] for s in out["shipped"]]}
        tail = f" — next blocked by PHASE {out['blocker']['phase']}" if out["blocker"] else ""
        out["reason"] = f"propose {current} → {out['proposal']['next']}{tail}"
    else:
        b = out["blocker"]
        out["reason"] = (f"no bump earned yet — PHASE {b['phase']} ({b['title']}) still open"
                         if b else "no phase above current could be evaluated.")
    return out


def _title_for_scoring(raw_title: str) -> str:
    """Strip the id + status/date noise before scoring two declarations
    against each other, so a bare status difference does not itself depress
    the Dice score."""
    t = re.sub(r"^PH\d+(?:\.\d+)?-T\d+\s*:?\s*", "", raw_title)
    t = re.sub(r"\(\s*(Pending|In Progress|Complete|Done|Dropped)\b[^)]{0,40}\)", "", t, flags=re.I)
    t = re.sub(r"✅\s*[*_]{0,2}\s*(Pending|In Progress|Complete|Done|Dropped)\b", "", t, flags=re.I)
    return t


def _raw_declarations(root: Path | None = None) -> list[dict]:
    """Every task declaration, NOT deduped across files — collision detection
    needs to see both sides, unlike `task_ledger.all_tasks()`, which merges
    them by design (that merge is the whole reason a collision can hide)."""
    root = root or ws_root()
    out: list[dict] = []
    for rel in task_ledger.LEDGER_FILES:
        path = root / rel
        if not path.is_file():
            continue
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        seen: set[str] = set()
        for i, line in enumerate(lines):
            if not task_ledger.is_declaration(line, allow_heading=True):
                continue
            m = task_ledger.TASK_RE.match(task_ledger._strip_bullet(line))
            if not m or m.group(0) in seen:
                continue
            seen.add(m.group(0))
            hit = task_ledger._extract(lines, i, m.group(0))
            hit["source"] = str(rel)
            out.append(hit)
    return out


_DONE_STATUSES = {"complete", "done", "dropped"}


def id_collisions(root: Path | None = None) -> list[dict]:
    """Task ids declared more than once whose declarations describe
    different tasks.

    **Two gates, both required — title similarity alone was tried first and
    measured unusable.** This ledger's house style pairs a terse
    activeContext.md status note ("PH16-T02 — 5/5 slices done") with a full
    tasks.md description; those legitimately share almost no words even
    though they name the same task, so a pure Dice-score classifier flagged
    ~15 real tasks on the live ledger — cried wolf on everything from
    `PH9-T16` to `PH6-T19`. The signal that actually separated the one real
    collision (`PH6-T20`, found and fixed 2026-08-10) from all fifteen false
    positives, measured against this repo's own ledger before shipping: the
    real collision had one side declaring an **explicit** done status
    (`declared_status` in `{Complete, Done, Dropped}`) and the other an
    **explicit** unticked checkbox (`checked is False`) — a real
    contradiction, not merely "no information on this side". Every false
    positive above had at most a *terse* or *empty* status on one side, never
    an outright contradiction. Word-overlap is kept as the second gate,
    against `PH6-T18` — same explicit-done/explicit-open contradiction (an
    older activeContext.md note calling it "Complete" before it was
    superseded, `[-]`, by a later task) but a title score of 0.55, nothing
    like `PH6-T20`'s near-zero — so a real contradiction over near-identical
    content is not reported; only a real contradiction over near-*unrelated*
    content is.
    """
    root = root or ws_root()
    by_id: dict[str, list[dict]] = {}
    for hit in _raw_declarations(root=root):
        by_id.setdefault(hit["task"], []).append(hit)

    collisions = []
    for tid, hits in sorted(by_id.items()):
        if len(hits) < 2:
            continue
        has_explicit_done = any(h["status"].lower() in _DONE_STATUSES for h in hits)
        has_explicit_open = any(h["checked"] is False for h in hits)
        if not (has_explicit_done and has_explicit_open):
            continue
        token_sets = [off_plan._tokens(_title_for_scoring(h["title"])) for h in hits]
        worst = 1.0
        for a in range(len(token_sets)):
            for b in range(a + 1, len(token_sets)):
                ta, tb = token_sets[a], token_sets[b]
                score = 0.0 if not ta or not tb else 2 * len(ta & tb) / (len(ta) + len(tb))
                worst = min(worst, score)
        if worst < COLLISION_THRESHOLD:
            collisions.append({"task": tid, "score": round(worst, 3),
                               "declarations": [{"source": h["source"], "line": h["line"],
                                                 "title": h["title"]} for h in hits]})
    return collisions


def render(c: dict) -> None:
    print("\n" + "─" * 54)
    print("  🏛️  PHASE LEDGER — structural check")
    print("─" * 54)
    if c["bad_headings"]:
        for b in c["bad_headings"]:
            print(f"  ❌ line {b['line']}: missing {', '.join(b['missing'])} — {b['heading'][:70]}")
    else:
        print(f"  ✅ all {len(c['headings'])} phase heading(s) structurally complete")
    if c["collisions"]:
        for col in c["collisions"]:
            print(f"  ❌ {col['task']} — {col['score']*100:.0f}% overlap across "
                  f"{len(col['declarations'])} declarations (< {COLLISION_THRESHOLD*100:.0f}% = collision)")
    else:
        print("  ✅ no id collisions in the ledger")
    if c.get("unheaded_phases"):
        for num in c["unheaded_phases"]:
            print(f"  ❌ PHASE {num} has `PH{num}-T##` tasks in the ledger but no "
                  f"`## PHASE {num}` heading — the ladder cannot stamp or block on it")
    else:
        print("  ✅ every phase with ledger tasks has a heading to stamp from")
    if c.get("misordered_headings"):
        for m in c["misordered_headings"]:
            print(f"  ❌ line {m['line']}: v{m['version']} does not ascend from "
                  f"v{m['after']} — the walk is document-ordered — {m['heading'][:50]}")
    else:
        print("  ✅ headings run in ascending version order")
    print("─" * 54 + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Phase ledger structure + version ladder.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--next-version", metavar="X.Y.Z",
                     help="what would stamp next, from this current version")
    args = ap.parse_args()

    root = ws_root()
    if args.next_version:
        result = next_stampable(args.next_version, root=root)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(f"  🏷️  {result['reason']}")
        return 0 if result["ok"] else 1

    c = check(root=root)
    if args.json:
        print(json.dumps(c, indent=2))
    else:
        render(c)
    return 0 if c["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
