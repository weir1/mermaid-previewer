#!/usr/bin/env python3
"""The closure DAG, and the board that reads it (PH23-T01).

## What this is for

A session was watched for its full 68-minute runtime while it completed one work
task correctly and then spent longer closing than building — four consecutive
`git commit --amend` cycles, a self-review voided twice, doc-stamps drifting after
the commit that was supposed to ship them. The audit is `doc/CLOSURE_AUDIT_BRIEFING.md`.

Its finding, in one line: **this workspace has strong late-stage gates and no
ordering between them.** Source edits, derived artefacts, attestations and
task-state transitions are peer operations that may run in any sequence, and
several of them invalidate each other. The correct sequence was written in prose,
in `AGENTS.md`, and the session that produced this evidence was following that
prose — it stated the right order in its own output at minute 55 and violated it
at minute 65, because compliance with a multi-step ordering rule decays as the
context fills. More prose is therefore not the fix.

So the ordering is declared here as **data**: `STEPS` is the dependency graph,
each node carrying a probe that answers "is this settled *right now*". Two things
fall out of that which prose could never provide:

  * `next_action()` — one line answering "where am I", derived rather than
    remembered. It cannot decay out of working memory because it is not in
    working memory.
  * `invalidations()` — the check nothing did before: an attestation that is
    recorded while something it derives from is still unsettled is *already
    condemned*, and saying so costs one line instead of an amend cycle.

## Reading the graph

An edge `A ← B` (`A.depends_on = [B]`) means **redoing B invalidates A**. That is
a stronger claim than "B runs first", and it is the claim that matters: the gate
depends on doc-stamps because stamping rewrites tracked `.md` files and evidence
freshness is measured against the newest tracked file; the self-review depends on
both because its record is bound to the sha256 of the whole session diff, which
contains `evidence.json`, the stamps and the codemap alike.

`STEPS` is declared in topological order and a test pins that it stays that way,
so walking the list *is* walking the graph.

## What this module does not do

It never repairs anything, and it never writes. It is a read-only board — the
Andon cord, not the hand that pulls it. `just prep-close` and `just ship`
(PH23-T02) are the recipes that act; this is what they and the operator consult.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# A step is settled ("done"), needs redoing ("stale"), has not run yet
# ("pending"), does not apply to this session ("n/a"), or could not be answered
# ("unknown"). `unknown` exists because a fleet workspace may be running a
# deployment that predates a probe's module, and "the probe is missing" must
# never render as "the step is done".
STATES = ("done", "stale", "pending", "n/a", "unknown")
UNSETTLED = ("stale", "pending", "unknown")

# Phase 2 carries no `Step`, and that absence used to look like an off-by-one to
# anyone reading this dict. It is not: CONSTRUCTION is the one phase whose output
# no other phase derives from, so there is nothing here to probe. Naming it costs
# one line and removes the "did someone delete phase 2?" question — its two
# prohibitions (transition a task's state only AFTER credit; write no tracked file
# after the review is recorded) are unenforceable by construction and therefore
# live in the `phase-locked-lifecycle` skill, which is where PH23-T03 put them.
# `render()` and `render_dag()` skip a phase with no steps, so this stays honest
# rather than printing an empty section.
PHASES = {
    1: "SPECIFICATION — what is being done, and was it agreed",
    2: "CONSTRUCTION — the work itself; no closure step, by design",
    3: "HARMONIZATION — derived artefacts caught up with the source",
    4: "ATTESTATION & CLOSURE — the proofs, then the irreversible acts",
}

# The written procedure for what may NOT happen inside each phase (PH23-T03).
# Named here so the board can point at it: this module knows the order, the skill
# knows the prohibitions the order cannot express.
LIFECYCLE_SKILL = ".agents/skills/phase-locked-lifecycle/SKILL.md"

ICON = {"done": "✅", "stale": "♻️ ", "pending": "⏳", "n/a": "➖", "unknown": "❔"}


@dataclass(frozen=True)
class Step:
    id: str
    phase: int
    title: str
    probe: object
    depends_on: tuple = ()
    # An advisory step is reported but never blocks: it describes health that a
    # later phase legitimately fixes, so making it the next action would stall
    # the pipeline on a condition the pipeline itself resolves.
    advisory: bool = False
    fix: str = ""


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _answer(state: str, detail: str = "") -> dict:
    return {"state": state, "detail": detail}


def _guard(fn):
    """Every probe fails to `unknown`, never to `done`.

    A probe reaches an answer by importing a sibling module and reading the tree.
    Both can be absent — this kernel is deployed to 46 workspaces, not all of them
    on the same version — and an exception must not be the reason a step reads as
    settled. Wrapping is done once here rather than in eleven `try` blocks, so the
    fail-safe direction cannot be got wrong in a probe written later.
    """
    def run(root: Path) -> dict:
        try:
            root = Path(root)
            if not root.is_dir():
                return _answer("unknown", f"{root} is not a workspace")
            return fn(root)
        except Exception as exc:  # noqa: BLE001
            return _answer("unknown", f"{type(exc).__name__}: {exc}")
    run.__name__ = getattr(fn, "__name__", "probe")
    run.__doc__ = fn.__doc__
    return run


# ── Phase 1: specification ───────────────────────────────────────────────────

def _active(root: Path) -> str:
    import task_ledger
    return task_ledger.active_task(root)


@_guard
def _probe_task(root: Path) -> dict:
    """Is a task declared `(In Progress)` — the binding evidence.json attaches to?"""
    task = _active(root)
    if not task:
        return _answer("n/a", "no task declared — this is a maintenance session")
    return _answer("done", task)


@_guard
def _probe_ledger_agrees(root: Path) -> dict:
    """Do the prose and the checkbox agree about what is finished? (PH16-T41)

    Phase 1, not phase 4, and for the same reason `ledger-test` is: settling this
    means editing a *tracked* file, and the gate's freshness is measured against
    tracked files — so a disagreement discovered after `verify-safe` costs a second
    full verification of work that was already verified.

    Not scoped to the active task. The specimen that produced this check was a task
    a *previous* session shipped and never ticked, and the whole failure is that
    nothing noticed across the session boundary; asking only about the current task
    would reproduce it exactly.
    """
    import task_ledger
    bad = task_ledger.disagreements(root)
    if not bad:
        return _answer("done", "the prose and the ledger agree about what is finished")
    head = " · ".join(f"{d['task']} ({d['kind']})" for d in bad[:3])
    more = f" (+{len(bad) - 3} more)" if len(bad) > 3 else ""
    return _answer("pending", f"{len(bad)} disagreement(s): {head}{more} — {bad[0]['detail']}")


@_guard
def _probe_brief(root: Path) -> dict:
    """`just work-done` refuses without one, and it must be written first."""
    task = _active(root)
    if not task:
        return _answer("n/a", "no active task to brief")
    import prework
    v = prework.validate(task, root=root)
    return _answer("done" if v.get("ok") else "pending", v.get("reason", ""))


@_guard
def _probe_ledger_test(root: Path) -> dict:
    """Does a `[complex]` task's DoD name a collectible test?

    In phase 1 rather than phase 4, and found the hard way: this is a precondition
    of `work-done`, but satisfying it means editing `.ai/docs/tasks.md`, which is a
    tracked file — so a session that discovers it at credit time must edit the
    ledger, which makes the evidence STALE, which forces a second full `verify-safe`
    to credit a task whose work was already finished and attested.

    That is the closure audit's root cause reproduced inside the fix for it: an
    attestation invalidated by satisfying something it depends on. The cure is not
    an exemption — the ledger is real content — but a phase boundary. Name the test
    when you write it, in phase 1, and the loop cannot form.
    """
    task = _active(root)
    if not task:
        return _answer("n/a", "no active task")
    import task_ledger
    info = task_ledger.find_task(task, root=root)
    if not info.get("complex"):
        return _answer("n/a", f"{task} is not [complex] — no test is owed")
    if info.get("test_ref"):
        return _answer("done", info["test_ref"])
    return _answer("pending", f"{task} is [complex] and its DoD names no test — add "
                              "`test: tests/x.py::Class::test_name` BEFORE the gate runs")


@_guard
def _probe_plan(root: Path) -> dict:
    """Only a `[complex]` task owes a plan; for everything else this is n/a."""
    task = _active(root)
    if not task:
        return _answer("n/a", "no active task")
    import task_ledger
    info = task_ledger.find_task(task, root=root)
    if not info.get("complex"):
        return _answer("n/a", f"{task} is not marked [complex]")
    import plan as plan_mod
    v = plan_mod.validate(task, root=root)
    return _answer("done" if v.get("ok") else "pending", v.get("reason", ""))


# ── Phase 3: harmonization ───────────────────────────────────────────────────

@_guard
def _probe_codemap(root: Path) -> dict:
    """Derived from every source file. Regenerating it changes the session diff."""
    import codemap
    v = codemap.check(root)
    if not v.get("exists"):
        return _answer("pending", v.get("reason", ""))
    return _answer("stale" if v.get("stale") else "done", v.get("reason", ""))


@_guard
def _probe_stamps(root: Path) -> dict:
    """Derived from git history. Stamping rewrites tracked `.md` — see the gate."""
    import doc_stamp
    drift = doc_stamp.check(root)
    if not drift:
        return _answer("done", "every covered doc agrees with git")
    head = ", ".join(d["path"] for d in drift[:3])
    more = f" (+{len(drift) - 3} more)" if len(drift) > 3 else ""
    return _answer("stale", f"{len(drift)} doc(s) drifted: {head}{more}")


@_guard
def _probe_doctor(root: Path) -> dict:
    """Structural health. Advisory: its own gate check is red until phase 4 runs."""
    import doctor
    checks = doctor.run_checks(root)
    n_fail, n_warn = doctor.counts(checks)
    state = "done" if not n_fail else "stale"
    return _answer(state, f"{len(checks)} checks · {n_fail} fail · {n_warn} warn")


@_guard
def _probe_archive(root: Path) -> dict:
    """Is closed history sitting in a hot memory file that archiving would move?

    Scoped to *movable history*, deliberately NOT to *file size* (PH25-T03). An
    over-budget file with nothing archivable is bloated by live state, which
    `archive_memory` will never touch and should not: that is already `doctor`'s
    warn and `entry_budget`'s report, and a second owner of the ~200-line threshold
    is this repo's own defect class. Answering it here would also leave the board
    permanently unsettled on a condition the pipeline cannot resolve.

    The one distinction that matters is the one the tool cannot make about itself.
    This file's history shapes have drifted twice (PH16-T05, PH16-T15) and both
    times `archive-memory` answered "nothing to archive" in the same words it uses
    for a healthy file. That was survivable while a human typed the command and
    read the output; running it on every closure makes the silence routine. So a
    `blind` file — over budget, with more unrecognised lines than the whole budget
    — reads `unknown`, which blocks, rather than `done`.
    """
    import entry_budget
    v = entry_budget.check(root)
    blind = v.get("blind") or []
    if blind:
        return _answer("unknown",
                       f"{', '.join(blind)} — over budget, and most of it matches no known "
                       f"entry shape, so archiving is a no-op that reports success. Fix "
                       f"`archive_memory.RULES`; do NOT lower --keep to work around it.")
    owed = v.get("unarchived") or []
    if owed:
        return _answer("pending",
                       f"{', '.join(owed)} — over budget with closed history still in it")
    return _answer("done", "no closed history is waiting to move")


# ── Phase 4: attestation and closure ─────────────────────────────────────────

@_guard
def _probe_gate(root: Path) -> dict:
    """evidence.json: the tests passed AND nothing changed since they did."""
    import gate_check
    v = gate_check.check(root)
    if v.get("open"):
        return _answer("done", v.get("reason", ""))
    reason = v.get("reason", "")
    return _answer("stale" if "STALE" in reason else "pending", reason)


@_guard
def _probe_work_done(root: Path) -> dict:
    """Has the active task been credited against the session budget?"""
    task = _active(root)
    if not task:
        return _answer("n/a", "no active task to credit")
    import session_budget
    state = session_budget.load() or {}
    if task in (state.get("work") or []):
        return _answer("done", f"{task} credited")
    return _answer("pending", f"{task} not yet credited — `just work-done \"{task}\"`")


@_guard
def _probe_review(root: Path) -> dict:
    """Bound to the sha256 of the whole session diff. Any later write voids it."""
    import self_review
    v = self_review.check(root)
    if v.get("ok"):
        return _answer("done", v.get("reason", ""))
    prior = self_review.read_reviews(root)
    # A record that exists but no longer matches is a *voided* review, which is a
    # different situation from never having reviewed: it means the diff moved
    # under a review that was really done, and the honest word for that is stale.
    return _answer("stale" if prior else "pending", v.get("reason", ""))


@_guard
def _probe_commit(root: Path) -> dict:
    """Is the working tree clean — i.e. is everything actually in a commit?"""
    out = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, stderr=subprocess.DEVNULL, text=True)
    dirty = [ln for ln in out.splitlines() if ln.strip()]
    if not dirty:
        return _answer("done", "working tree clean")
    head = ", ".join(ln[3:] for ln in dirty[:3])
    more = f" (+{len(dirty) - 3} more)" if len(dirty) > 3 else ""
    return _answer("pending", f"{len(dirty)} uncommitted path(s): {head}{more}")


@_guard
def _probe_push(root: Path) -> dict:
    """Is the session's work on origin?

    Deliberately NOT "is HEAD on the remote". At the start of a session HEAD is
    always already pushed, so that narrower question answers `done` while every
    line of the session's work is still on disk — and the board would then report
    a settled push sitting over a pending commit, which reads as an invalidation
    and is merely a session that has not finished yet. A dirty tree means there
    is work the remote has not seen, whatever HEAD says.
    """
    dirty = subprocess.check_output(
        ["git", "status", "--porcelain"], cwd=root, stderr=subprocess.DEVNULL, text=True)
    try:
        subprocess.check_output(["git", "rev-parse", "--abbrev-ref", "@{upstream}"],
                                cwd=root, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        return _answer("unknown", "no upstream branch configured")
    ahead = subprocess.check_output(
        ["git", "rev-list", "--count", "@{upstream}..HEAD"],
        cwd=root, stderr=subprocess.DEVNULL, text=True).strip()
    if ahead != "0":
        return _answer("pending", f"{ahead} commit(s) not pushed")
    if dirty.strip():
        return _answer("pending", "HEAD is on the remote, but the tree has "
                                  "uncommitted work the remote has not seen")
    return _answer("done", "HEAD is on the remote and the tree is clean")


def _closure_probe(kind: str):
    @_guard
    def probe(root: Path) -> dict:
        import session_budget
        state = session_budget.load() or {}
        if kind in (state.get("closure") or []):
            return _answer("done", f"closure `{kind}` recorded")
        return _answer("pending", f"not closed — `just close {kind}`")
    probe.__name__ = f"_probe_close_{kind.replace('-', '_')}"
    return probe


# ── The graph ────────────────────────────────────────────────────────────────
#
# Declared in topological order; `test_the_declaration_order_is_topological`
# keeps it that way, because `next_action` walks the list and calls that the
# graph. An edge means "redoing the dependency invalidates this step".

STEPS: tuple = (
    Step("task-declared", 1, "task marked (In Progress)", _probe_task,
         fix='mark it in .ai/memory-bank/activeContext.md'),
    Step("brief", 1, "pre-work brief agreed", _probe_brief, ("task-declared",),
         fix='just brief "<task>" --accept "<his words>"'),
    Step("plan", 1, "plan written ([complex] only)", _probe_plan, ("task-declared",),
         fix='just plan "<task>"'),
    # Phase 1, not phase 4, though `work-done` is what enforces it — see the probe.
    Step("ledger-test", 1, "DoD names a collectible test ([complex] only)",
         _probe_ledger_test, ("task-declared",),
         fix="add `test: tests/x.py::Class::test_name` under the DoD in .ai/docs/tasks.md"),
    # No `depends_on`: unlike its phase-1 siblings this asks nothing about the active
    # task. The specimen it was built from was a task a PREVIOUS session shipped and
    # never ticked, so binding it to `task-declared` would make a maintenance session
    # — the one most likely to inherit the mess — the one session that cannot see it.
    Step("ledger-agrees", 1, "prose and ledger agree on what is done", _probe_ledger_agrees,
         fix='tick the box in .ai/docs/tasks.md, or correct the prose in activeContext.md'),

    # First in the phase, because it is the only phase-3 step that rewrites files
    # the others derive from (PH25-T03). `.ai/memory-bank/` is a `doc_stamp.DOC_DIRS`
    # directory and its files are tracked, so archiving after the stamps leaves them
    # a revision behind, after the gate makes minutes-old evidence STALE, and after
    # the review voids it outright. The edge is on `doc-stamps`; `doctor`, `gate` and
    # `self-review` inherit it transitively, and `blockers()` walks the graph.
    Step("archive", 3, "hot memory files pruned", _probe_archive,
         fix="just prep-close  (or `just archive-memory` to see what it would move)"),
    # Both derived from the source tree; neither depends on the other. `doctor`
    # reads both, which is why it is last in the phase and advisory.
    Step("codemap", 3, "codemap matches the tree", _probe_codemap,
         fix="just codemap"),
    Step("doc-stamps", 3, "doc stamps agree with git", _probe_stamps, ("archive",),
         fix="just doc-stamps --apply"),
    Step("doctor", 3, "structural health", _probe_doctor, ("codemap", "doc-stamps"),
         advisory=True, fix="just doctor"),

    # Evidence freshness is measured against the newest tracked working-tree file,
    # so BOTH phase-3 artefacts are genuine dependencies: regenerating either one
    # after this point makes the gate stale.
    Step("gate", 4, "gate open (fresh evidence)", _probe_gate,
         ("task-declared", "brief", "plan", "ledger-test", "ledger-agrees",
          "codemap", "doc-stamps", "archive"),
         fix="just verify-safe"),
    Step("work-done", 4, "task credited", _probe_work_done,
         ("gate", "brief", "plan", "ledger-test"), fix='just work-done "<task>"'),
    # The review's hash covers the whole session diff — evidence.json, the stamps
    # and the codemap included — so it must be recorded after all three settle.
    Step("self-review", 4, "session diff reviewed", _probe_review,
         ("gate", "codemap", "doc-stamps"),
         fix='just review-diff, then just self-review pass "<what you checked>"'),
    Step("commit", 4, "everything committed", _probe_commit,
         ("self-review", "work-done"), fix='just commit-all "<message>"'),
    Step("push", 4, "pushed to origin", _probe_push, ("commit",), fix="just push"),
    Step("close-git-push", 4, "closure: git-push", _closure_probe("git-push"),
         ("push", "self-review"), fix="just close git-push"),
    # The three closure records are PEERS, and deliberately carry no edges to each
    # other. Their conventional order (git-push · docs · issues) is expressed by
    # their position here, which is what `next_action` walks — but position is not
    # dependency, and an edge in this graph means "redoing this voids that".
    # Redoing the push does not void the fact that the memory bank was updated.
    #
    # Found by the board itself, on the session that built it: `close-docs` was
    # declared to depend on `close-git-push` to encode the documented order, and
    # `invalidations()` duly reported a recorded docs closure as condemned by a
    # pending push. The report was correct given the edge; the edge was wrong.
    # Conflating "happens after" with "is invalidated by" is precisely the
    # imprecision this graph exists to remove, so it must not be reintroduced in
    # the graph itself.
    Step("close-docs", 4, "closure: docs", _closure_probe("docs"),
         fix="just close docs"),
    Step("close-issues", 4, "closure: issues", _closure_probe("issues"),
         fix="just close issues"),
)

BY_ID = {s.id: s for s in STEPS}


# ── Pure reasoning over the graph ────────────────────────────────────────────

def assess(root: Path | str | None = None) -> dict:
    """`{step_id: state}` for this workspace. Read-only; never repairs."""
    root = Path(root or ws_root())
    return {s.id: s.probe(root)["state"] for s in STEPS}


def detail(root: Path | str | None = None) -> dict:
    """`{step_id: {state, detail}}` — the same walk, keeping each probe's words."""
    root = Path(root or ws_root())
    return {s.id: s.probe(root) for s in STEPS}


def next_action(states: dict) -> str | None:
    """The first unsettled, non-advisory step in DAG order — or None when done."""
    for step in STEPS:
        if step.advisory:
            continue
        if states.get(step.id, "unknown") in UNSETTLED:
            return step.id
    return None


def blockers(states: dict, step_id: str) -> list:
    """Every transitive dependency of `step_id` that is not settled, in DAG order.

    What `just ship` refuses on (PH23-T02). Asking the graph rather than re-listing
    the preconditions is the point: a recipe that keeps its own copy of "what must
    be true before a commit" is a second declaration of the order, and a second
    declaration is the thing this phase exists to abolish.

    Advisory steps are skipped — they assert nothing, so they block nothing.
    """
    want, seen, out = [step_id], set(), []
    while want:
        cur = want.pop()
        for dep in BY_ID[cur].depends_on:
            if dep in seen:
                continue
            seen.add(dep)
            want.append(dep)
    for step in STEPS:  # DAG order, not discovery order
        if step.id in seen and not step.advisory \
                and states.get(step.id, "unknown") in UNSETTLED:
            out.append(step.id)
    return out


def invalidations(states: dict) -> list:
    """Attestations that are already condemned by an unsettled dependency.

    This is the check that did not exist, and the reason the observed session
    needed four amend cycles: it recorded a review, then regenerated a codemap,
    then re-stamped docs, then re-ran the gate — each act voiding the one before
    it, each discovered only when a later command refused.

    An advisory step neither invalidates nor is invalidated: it asserts nothing.
    """
    out = []
    for step in STEPS:
        if step.advisory or states.get(step.id) != "done":
            continue
        for dep in step.depends_on:
            if BY_ID[dep].advisory:
                continue
            if states.get(dep, "unknown") in UNSETTLED:
                out.append({"step": step.id, "dependency": dep,
                            "why": f"{step.id} is recorded, but {dep} is "
                                   f"{states.get(dep)} — settling {dep} will void it."})
    return out


# ── CLI ──────────────────────────────────────────────────────────────────────

def render(root: Path) -> int:
    probed = detail(root)
    states = {k: v["state"] for k, v in probed.items()}

    print("═" * 62)
    print("  🚦 CLOSURE STATUS — where this session is in the pipeline")
    print("═" * 62)
    for phase, title in PHASES.items():
        in_phase = [s for s in STEPS if s.phase == phase]
        if not in_phase:  # CONSTRUCTION — named in PHASES, nothing to probe
            continue
        print(f"\n  PHASE {phase}: {title}")
        for step in in_phase:
            a = probed[step.id]
            tag = " (advisory)" if step.advisory else ""
            print(f"    {ICON[a['state']]} {step.title}{tag}")
            if a["detail"]:
                print(f"         {a['detail'][:150]}")

    bad = invalidations(states)
    if bad:
        print("\n  " + "─" * 58)
        print("  ⚠️  ALREADY CONDEMNED — an attestation over an unsettled input:")
        for i in bad:
            print(f"     • {i['why']}")
        print("     Settle the dependency FIRST, then redo the step above it.")
        # An invalidation is the symptom of a write made in the wrong phase, so
        # this is the exact moment the procedure is worth reading (PH23-T03).
        print(f"     Why this keeps happening: {LIFECYCLE_SKILL}")

    nxt = next_action(states)
    print("\n  " + "─" * 58)
    if nxt is None:
        print("  ✅ Nothing left in the pipeline — the session is closed.")
    else:
        step = BY_ID[nxt]
        print(f"  ➤ NEXT: {step.title}")
        if step.fix:
            print(f"    {step.fix}")
    print(f"\n  What may NOT happen inside each phase → {LIFECYCLE_SKILL}")
    print("═" * 62)
    return 0 if (nxt is None and not bad) else 1


def render_dag() -> str:
    """The graph as a Mermaid diagram — derived from `STEPS`, never typed.

    `doc/closure-dag.md` points at this rather than embedding a copy. A diagram
    maintained by hand beside the code it describes is the same defect class as a
    hand-kept `revisions:` counter: it is right on the day it is written and a lie
    with a schedule thereafter. Regenerate, do not edit.
    """
    # Mermaid node ids may not carry hyphens; the step ids do, because they match
    # the `just close <kind>` words an operator types.
    def nid(step_id: str) -> str:
        return step_id.replace("-", "_")

    lines = ["```mermaid", "graph TD"]
    for phase, title in PHASES.items():
        in_phase = [s for s in STEPS if s.phase == phase]
        if not in_phase:  # an empty subgraph is a Mermaid syntax error
            continue
        lines.append(f'  subgraph P{phase}["PHASE {phase} — {title.split(" — ")[0]}"]')
        for s in in_phase:
            body = f'(["{s.title}"])' if s.advisory else f'["{s.title}"]'
            lines.append(f"    {nid(s.id)}{body}")
        lines.append("  end")
    for s in STEPS:
        for dep in s.depends_on:
            lines.append(f"  {nid(dep)} -->|redoing this voids| {nid(s.id)}")
    lines.append("```")
    lines += ["", "| Step | Phase | Invalidated by redoing | Advisory |",
              "|---|---|---|---|"]
    for s in STEPS:
        deps = ", ".join(f"`{d}`" for d in s.depends_on) or "—"
        lines.append(f"| `{s.id}` | {s.phase} | {deps} | {'yes' if s.advisory else ''} |")
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Where is this session in the closure DAG?")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--next", action="store_true", help="print only the next step id")
    ap.add_argument("--dag", action="store_true", help="render the graph (Mermaid)")
    ap.add_argument("--root", default="", help="workspace root (default: git toplevel)")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else ws_root()

    if args.dag:
        print(render_dag())
        return 0
    if args.next:
        print(next_action(assess(root)) or "")
        return 0
    if args.json:
        probed = detail(root)
        states = {k: v["state"] for k, v in probed.items()}
        print(json.dumps({
            "steps": [{"id": s.id, "phase": s.phase, "title": s.title,
                       "depends_on": list(s.depends_on), "advisory": s.advisory,
                       **probed[s.id]} for s in STEPS],
            "next": next_action(states),
            "invalidations": invalidations(states)}, indent=2))
        return 0
    return render(root)


if __name__ == "__main__":
    raise SystemExit(main())
