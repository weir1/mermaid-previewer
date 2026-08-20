#!/usr/bin/env python3
"""
prework.py — no task is credited on a discussion that never happened (PH22-T01).

`.ai/plan.md` G20, in the operator's own words: *"nothing should be blind work,
every session every task needs discussion, simple english understanding, how it
moves towards goals & solid justification from both side, because at the end its
me the user the moin knows what i want & i only knows wholesome context."*

The OS already governs whether an act is **safe** (`gate_check.py`) and whether
the scope **grew** (`off_plan.py`). Nothing governed whether the operator
*understood and agreed* before a session slot was spent — so a session could be
perfectly safe, perfectly on-plan, and still burn its budget on work he would
have trimmed had anyone asked him in English.

Two measurements taken 2026-08-15 are why this is a mechanism rather than a
paragraph in `AGENTS.md`. `zenith-core`'s `UPWORK_HUNT_KIT.md` was finished
2026-07-20 and untouched for 26 days while its own MISSION_LOG (2026-07-15) read
*"Under Law 7 the hunt starts immediately"*. `@jobscraper`'s last product commit
aged to 15 days while every file touched there that week was `.ai/` kernel
state. **Both workspaces were following their plans correctly throughout.**
Alignment was never the failure; an unheld conversation was. No pre-existing
check fires on either case, because every pre-existing check asks *is this
allowed* and none asks *did he agree*.

## What this can and cannot prove

**It cannot prove a human spoke.** It proves a brief exists, that it names a
real goal from `.ai/plan.md`, that its cheaper-alternative field is filled, that
the operator's justification is not empty and not the untouched scaffold, and
whether it predated the code. An AI determined to forge all of that can.

The self-review gate (PH7-T04) has the identical limit and says so. Stating it
here is not modesty — overclaiming would make this module an instance of the
defect class it exists to police, which is the most common bug in this repo: a
claim nothing verifies. What it buys is that forging the discussion becomes a
*deliberate act* instead of the default path.

## Why the check sits at `work-done` and not at the first edit

Nothing can observe "the first edit of a task": the agent writes files directly,
and a pre-edit hook would have to guess which edit belongs to which task id — it
would fire on memory-bank updates and scaffolds, get muted within a week, and
protect nothing. `work-done` is the one moment the OS already owns where a task
claims credit, and the `[complex]` plan gate already proves that placement works.

The cost of the later placement is real and is therefore *measured rather than
hidden*: the brief records `base_head` **and** `base_dirty` (the tree's already-
changed paths at scaffold time), and `validate()` reports `pre-work` or
`post-hoc` with the number of files that changed *after the discussion*. This
module cannot prevent a brief being written after the code. It must not be able
to conceal it.

`base_dirty` is not redundant with `base_head`. Comparing to `base_head` alone
made every brief after a session's first one read `post-hoc`, because the earlier
task's work is still uncommitted and therefore "changed since HEAD" no matter
when this brief was written. That flaw is invisible with one task per session and
surfaced the first time a second brief was written in one — 2026-08-15 (e), the
same session that shipped the field.

## Single implementation of "is this section filled in?"

`_sections()` and `_is_written()` are imported from `plan.py` rather than
re-written here. The scaffold carries its guidance in HTML comments precisely so
an untouched scaffold fails validation *by construction* — the same trick, and
the same rule, must not exist twice and drift. This repo's recurring structural
defect is a rule implemented in several places that slowly disagree.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import plan as plan_mod  # noqa: E402  — sole owner of "is a Markdown section written?"

TASK_RE = re.compile(r"^PH[0-9]+(?:\.[0-9]+)?-T[0-9]+[a-z]?$", re.I)

# Order matters: the scaffold is written in this order, and a refusal names the
# first unfilled section, so the operator is sent to the top of the file first.
REQUIRED_SECTIONS = (
    "Why this task, now",
    "What",
    "Why it matters",
    "Goal",
    "North-Star stage",
    "Price",
    "Cheaper alternative",
    "Operator's justification",
)

# The two fields whose emptiness is not merely incomplete but *forgery-relevant*,
# called out separately so the refusal can say which half of the discussion is
# missing rather than listing eight section names.
CHEAPER_HALF = "Cheaper alternative"
OPERATOR_HALF = "Operator's justification"

# The sections that together are *the explanation the operator is reacting to*.
# `accept()` refuses while any is unwritten (PH22-T09): he cannot endorse a task
# he has not been shown, and on 2026-08-16 he was asked to do exactly that.
# `Goal`, `North-Star stage` and `Price` are deliberately NOT here — they are
# lookups, not argument, and blocking the conversation on a forecast number would
# add ceremony without adding explanation. Ordered as the scaffold writes them,
# so a refusal reads top-down.
AI_SECTIONS = (
    "Why this task, now",
    "What",
    "Why it matters",
    CHEAPER_HALF,
)

# Paths whose change means the session was spent on the OS rather than on the
# thing the workspace exists to produce. Deliberately a prefix list and not an
# inferred rule: an inferred one would have to guess, and a wrong guess here
# reports the opposite of the truth (see `governance_ratio`).
GOVERNANCE_PREFIXES = (".ai/", ".agents/", ".claude/", "scripts/", "doc/", "tests/")
GOVERNANCE_FILES = ("AGENTS.md", "CLAUDE.md", "justfile", "AI_CHANGELOG.md", "README.md")

# ...except in the kernel, where `scripts/`, `tests/` and `doc/` ARE the product.
# Measured on this repo the day it was written, the fleet-wide list scored the
# kernel at 99% governance — a number that is true of every session it will ever
# have, and therefore says nothing about any of them. A metric that cannot vary
# is noise, and `off_plan.py`'s docstring already paid for the lesson that noise
# gets tuned out and kills the mechanism. In the kernel, only the state the
# kernel *governs itself with* counts as governance.
KERNEL_GOVERNANCE_PREFIXES = (".ai/", ".agents/", ".claude/")
KERNEL_GOVERNANCE_FILES = ("AGENTS.md", "CLAUDE.md", "AI_CHANGELOG.md")

# A baseline this long is not worth storing in a Markdown header; the field says
# so and `_timing` refuses rather than counting from a truncated list.
MAX_BASE_DIRTY = 200
_UNRECORDED = "?"

DEFAULT_RATIO_DAYS = 14
RATIO_WARN_AT = 3


def ws_root() -> Path:
    """The workspace root — the directory containing `.ai/`, never the cwd blindly."""
    here = Path.cwd().resolve()
    for cand in (here, *here.parents):
        if (cand / ".ai").is_dir():
            return cand
    return here


def prework_dir(root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / "prework"


def brief_path(task: str, root: Path | None = None) -> Path:
    return prework_dir(root) / f"{task}.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


# ---------------------------------------------------------------- git helpers


def _git(root: Path, *args: str) -> tuple[int, str]:
    try:
        p = subprocess.run(["git", *args], cwd=root, capture_output=True,
                           text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return 1, ""
    return p.returncode, p.stdout.strip()


def _head(root: Path) -> str:
    code, out = _git(root, "rev-parse", "HEAD")
    return out if code == 0 else ""


def _front_matter(text: str) -> dict:
    """The `key: value` block between the first two `---` lines, if any."""
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def _dirty(root: Path, base_head: str) -> set[str] | None:
    """Paths differing from `base_head`, including untracked. None if unanswerable."""
    if not base_head:
        return None
    code, _ = _git(root, "cat-file", "-e", f"{base_head}^{{commit}}")
    if code != 0:
        return None
    code, tracked = _git(root, "diff", "--name-only", base_head)
    if code != 0:
        return None
    _, untracked = _git(root, "ls-files", "--others", "--exclude-standard")
    return {ln.strip() for ln in (tracked + "\n" + untracked).splitlines() if ln.strip()}


def _timing(root: Path, base_head: str, base_dirty: str,
            self_path: str = "") -> tuple[str, int]:
    """`pre-work`, `post-hoc` with a file count, or `unknown` — never a guess.

    **Measured against the brief, not against HEAD.** The first version compared
    the tree to `base_head` alone, which made every brief after the session's
    first one read `post-hoc` — the earlier task's work is still uncommitted, so
    those files are "changed since HEAD" no matter when this brief was written.
    Found the first time a second brief was written in one session (2026-08-15
    (e)), which is the only way it could have been found: the flaw is invisible
    with one task per session. `base_dirty` records the tree's already-changed
    paths at scaffold time, so what is counted here is what changed *after the
    discussion*, which is the only thing the field ever claimed to measure.

    A brief whose base commit is gone (history rewritten) reports `unknown`
    rather than silently counting from an arbitrary point. That failure mode is
    not hypothetical: it is exactly what @job filed on 2026-08-15 about
    `session-state.json`'s `head_at_start` after a `git-filter-repo` run.
    """
    now = _dirty(root, base_head)
    if now is None:
        return "unknown", 0
    if base_dirty == _UNRECORDED:
        # Written before this field existed, or too many baseline paths to record.
        # Refuse rather than report a number that counts another task's work.
        return "unknown", 0
    base = {p for p in base_dirty.split(",") if p}
    # The brief did not exist when its own baseline was taken, so without this it
    # counts itself as work done after the discussion it *is*.
    after = now - base - {self_path}
    n = len(after)
    return ("pre-work", 0) if n == 0 else ("post-hoc", n)


# ------------------------------------------------------------------- the plan


def _plan_goals(root: Path) -> list[dict]:
    """Goal ids from `.ai/plan.md`. `plan_workspace` keeps sole ownership of that text."""
    try:
        import plan_workspace
        return plan_workspace.validate(root=root).get("goals") or []
    except Exception:  # noqa: BLE001 — a missing/unreadable plan is not a crash here
        return []


# ----------------------------------------------------- the delegation proposal


DEFAULT_ITERATION_LIMIT = 3


def _plan_files(task: str, root: Path) -> tuple[list[str], str]:
    """Candidate allowlist: the first backtick-quoted path per bullet in
    `.ai/plans/{task}.md`'s `Files to touch` section — the same section
    `plan.py` itself requires non-empty for a `[complex]` task. Empty with a
    stated reason when there is no plan yet or it names no quoted paths;
    never a guess at what the task might touch."""
    path = root / ".ai" / "plans" / f"{task}.md"
    if not path.is_file():
        return [], f"no .ai/plans/{task}.md yet"
    try:
        text = _read(path)
    except OSError as exc:
        return [], f".ai/plans/{task}.md unreadable ({exc})"
    body = plan_mod._sections(text).get("Files to touch", [])
    # Only a line that STARTS a bullet (`- `) names a path; a wrapped
    # continuation line belongs to the previous bullet's description and its
    # own backtick spans (code, flag names, function calls) are not paths.
    # Found live testing this against PH10-T10's own plan: without the
    # bullet-start check, `--delegate {accept,decline}` and `main()` from a
    # wrapped description read as candidate files.
    out = [m.group(1) for ln in body if ln.lstrip().startswith("- ")
           and (m := re.search(r"`([^`]+)`", ln))]
    if not out:
        return [], f".ai/plans/{task}.md's 'Files to touch' names no backtick-quoted path"
    return out, ""


def _test_command(test_ref: str) -> str:
    """`tests/test_x.py::Class::method` → `python3 -m unittest
    tests.test_x.Class.method` — the exact shape `delegation.py`'s own
    `Command:` docstring example uses. Any other shape is passed through
    unchanged rather than mangled into something unrunnable."""
    m = re.match(r"^(.+)\.py::(.+)$", test_ref)
    if not m:
        return test_ref
    module = m.group(1).replace("/", ".")
    rest = m.group(2).replace("::", ".")
    return f"python3 -m unittest {module}.{rest}"


def propose_delegation(task: str, root: Path | None = None) -> dict:
    """PH10-T10: when the resolved running tier is `planner`, this is what
    `just brief` proposes instead of staying silent about delegation — a
    CANDIDATE, computed and stated, never armed here. A pure read; nothing
    in this function writes anything.

    This module cannot call `AskUserQuestion` itself — it is a non-interactive
    script invoked by `just`, the same limit `session_start.py`'s Section 6
    already lives with. `offer: True` is the signal the calling agent turns
    into an actual interactive choice rather than idle prose (AGENTS.md).
    """
    root = root or ws_root()
    out = {"task": task, "offer": False, "tier": "", "test_ref": "",
           "test_reason": "", "allowlist": [], "allowlist_reason": "",
           "command": "", "objective": ""}
    try:
        import model_registry
        out["tier"] = model_registry.resolve_running(root).get("tier", "")
    except Exception:  # noqa: BLE001 — an unreadable registry offers nothing, never guesses
        return out
    if out["tier"] != "planner":
        return out
    out["offer"] = True

    try:
        import task_ledger
        rec = task_ledger.find_task(task, root=root) or {}
    except Exception:  # noqa: BLE001
        rec = {}

    test_ref = rec.get("test_ref", "")
    if test_ref:
        out["test_ref"] = test_ref
        out["command"] = _test_command(test_ref)
    else:
        out["test_reason"] = (f"{task}'s ledger entry names no test — offered with the "
                              "test slot empty rather than skipped.")
    out["objective"] = rec.get("dod") or rec.get("title") or ""
    out["allowlist"], out["allowlist_reason"] = _plan_files(task, root)
    return out


def apply_delegation_decision(task: str, decision: str, reason: str = "",
                               root: Path | None = None) -> dict:
    """Act on the operator's answer to a `propose_delegation()` offer.

    `decision="decline"` writes one `decision_log` entry and changes nothing
    else. `decision="accept"` calls `delegation.scaffold_prefilled()` with a
    freshly RECOMPUTED proposal (not one threaded in as an argument) so
    `accept` can never arm a stale or hand-edited proposal it never verified
    still holds — the same non-TOCTOU discipline `gate_check.py` applies to
    evidence freshness.
    """
    root = root or ws_root()
    res = {"ok": False, "reason": ""}
    if decision not in ("accept", "decline"):
        res["reason"] = f"unknown decision {decision!r} — must be 'accept' or 'decline'."
        return res

    prop = propose_delegation(task, root=root)
    if not prop["offer"]:
        res["reason"] = (f"{task}: no delegation was offered (resolved tier is "
                         f"'{prop['tier'] or 'unknown'}', not planner) — nothing to "
                         f"{decision}.")
        return res

    if decision == "decline":
        try:
            import decision_log
            decision_log.record("delegation", "declined", "prework.py", root=root,
                                task=task, reason=reason or "operator declined the proposal")
        except Exception:  # noqa: BLE001 — the record is best-effort, never blocking
            pass
        res["ok"] = True
        res["reason"] = f"{task}: delegation declined, recorded. Nothing else changed."
        return res

    if not prop["command"]:
        res["reason"] = (f"{task}: no candidate test command — {prop['test_reason']}. "
                         f'Run `just delegate "{task}"` and fill it in by hand instead.')
        return res
    if not prop["allowlist"]:
        res["reason"] = (f"{task}: no candidate allowlist — {prop['allowlist_reason']}. "
                         f'Run `just delegate "{task}"` and fill it in by hand instead.')
        return res
    if not prop["objective"]:
        res["reason"] = (f"{task}: the ledger names no DoD/title to seed the Objective "
                         f'with. Run `just delegate "{task}"` and fill it in by hand.')
        return res

    try:
        import delegation
        path = delegation.scaffold_prefilled(
            task, prop["objective"], prop["command"], prop["allowlist"],
            DEFAULT_ITERATION_LIMIT, root=root)
    except FileExistsError:
        res["reason"] = f"{task}: a contract already exists — nothing overwritten."
        return res
    res["ok"] = True
    res["path"] = str(path)
    res["reason"] = (f"{task}: contract written with the proposal's fields — "
                     f'`just delegate-check "{task}"` can run now.')
    return res


# -------------------------------------------------------------- the ratio


def governance_ratio(root: Path | None = None, days: int = DEFAULT_RATIO_DAYS) -> dict:
    """How much of recent work went to the OS rather than to what it produces.

    Computed from **what the diff touched, not which workspace it ran in.** That
    distinction is the whole point and was found the hard way: measured
    2026-08-15, `@jobscraper` — an income workspace — had spent a week whose every
    touched file was `.ai/` kernel state. A per-workspace classifier would have
    counted that week as outcome work and reported the exact opposite of the truth.

    States its basis or refuses. Never returns a bare number.
    """
    root = root or ws_root()
    out = {"ratio": None, "governance": 0, "product": 0, "commits": 0,
           "days": days, "kernel": False, "basis": "", "reason": ""}

    try:
        import version_plan
        out["kernel"] = version_plan.is_kernel(root)
    except Exception:  # noqa: BLE001 — absent module means "assume not the kernel"
        pass
    prefixes = KERNEL_GOVERNANCE_PREFIXES if out["kernel"] else GOVERNANCE_PREFIXES
    files = KERNEL_GOVERNANCE_FILES if out["kernel"] else GOVERNANCE_FILES

    code, log = _git(root, "log", f"--since={days} days ago", "--name-only",
                     "--pretty=format:%H")
    if code != 0:
        out["reason"] = "git history unavailable — cannot measure, so not guessing."
        return out

    commits, gov, prod = 0, 0, 0
    for line in log.splitlines():
        s = line.strip()
        if not s:
            continue
        if re.fullmatch(r"[0-9a-f]{7,40}", s):
            commits += 1
            continue
        if s.startswith(prefixes) or s in files:
            gov += 1
        else:
            prod += 1

    total = gov + prod
    out.update(governance=gov, product=prod, commits=commits)
    if total == 0:
        out["reason"] = (f"no files changed in the last {days} days "
                         f"({commits} commit(s)) — nothing to measure.")
        return out

    out["ratio"] = round(gov / total, 3)
    scope = ("kernel scope: only `.ai/`, `.agents/`, `.claude/` and the protocol "
             "files count — here `scripts/` and `doc/` are the product"
             if out["kernel"] else "fleet scope: kernel-deployed paths count as governance")
    out["basis"] = (f"{gov} governance / {total} changed path(s) across {commits} "
                    f"commit(s) in the last {days} days, classified by path — {scope}")
    return out


# --------------------------------------------------------------- the scaffold


_SCAFFOLD_GUIDE = {
    "Why this task, now": "Why THIS task and not one of the other open ones — the "
                          "AI's own argument for the pick: what it unblocks, what "
                          "it costs to defer. This is the thing he is being asked "
                          "to react to, so it is written before he is asked.",
    "What": "In plain English, no jargon. What will actually be built or changed?",
    "Why it matters": "The business lens. What does this save, unlock or earn? "
                      "If the honest answer is 'nothing yet', write that.",
    "Goal": "The goal id from .ai/plan.md this serves, e.g. G20. "
            "An id that is not in the plan is refused, not warned about.",
    "North-Star stage": "Which ladder stage this feeds, from .ai/north-star.yaml. "
                        "'None — this is infrastructure' is a legitimate answer.",
    "Price": "Cost in sessions, with its basis. `just forecast` computes it.",
    "Cheaper alternative": "REQUIRED, and it is the AI's job to fill it. Name the "
                           "smaller version that gets most of the value. "
                           "'None' is an answer only if you argue why.",
    "Operator's justification": "THE OPERATOR'S OWN WORDS. Not the AI's summary of "
                                "them. Left empty, this brief is refused and the "
                                "task cannot be credited — that refusal is the "
                                "entire point of the file.",
}


def scaffold(task: str, root: Path | None = None) -> Path:
    """Write an unfilled brief. Never overwrites an existing one."""
    root = root or ws_root()
    path = brief_path(task, root)
    if path.exists():
        return path
    path.parent.mkdir(parents=True, exist_ok=True)

    head = _head(root)
    now = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    dirty = _dirty(root, head)
    if dirty is None or len(dirty) > MAX_BASE_DIRTY:
        base_dirty = _UNRECORDED
    else:
        base_dirty = ",".join(sorted(dirty))
    lines = [
        "---",
        f"task: {task}",
        f"base_head: {head}",
        f"base_dirty: {base_dirty}",
        f"created: {now}",
        "---",
        "",
        f"# Pre-work brief — {task}",
        "",
        "<!-- Every section below must carry real text. Guidance lives in HTML",
        "     comments, so an untouched scaffold fails validation by construction. -->",
        "",
    ]
    for section in REQUIRED_SECTIONS:
        lines += [f"## {section}", "", f"<!-- {_SCAFFOLD_GUIDE[section]} -->", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


# --------------------------------------------------------------- validation


def validate(task: str, root: Path | None = None) -> dict:
    """Is there a real, two-sided brief for `task`? Pure read — writes nothing."""
    root = root or ws_root()
    path = brief_path(task, root)
    v = {"task": task, "path": str(path), "exists": path.is_file(), "ok": False,
         "missing": [], "empty": [], "goal": "", "timing": "unknown",
         "changed_since": 0, "reason": ""}

    if not v["exists"]:
        v["reason"] = (f'no pre-work brief for {task} — run `just brief "{task}"`, '
                       "discuss it, and record his answer before claiming the task.")
        return v

    try:
        text = _read(path)
    except OSError as exc:
        v["reason"] = f"brief unreadable ({exc})."
        return v

    fm = _front_matter(text)
    # A brief is bound to its task id. `brief_path` already keys by task, but a
    # copied file would otherwise satisfy a claim it was never written for —
    # the same forgery the evidence/task binding closed in PH16-T09.
    declared = fm.get("task", "")
    if declared and declared != task:
        v["reason"] = (f"this brief declares `task: {declared}` but is being read for "
                       f"{task} — one brief cannot credit another task.")
        return v

    found = plan_mod._sections(text)
    for section in REQUIRED_SECTIONS:
        if section not in found:
            v["missing"].append(section)
        elif not plan_mod._is_written(found[section]):
            v["empty"].append(section)

    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = ""
    v["timing"], v["changed_since"] = _timing(
        root, fm.get("base_head", ""), fm.get("base_dirty", _UNRECORDED), rel)

    # The two forgery-relevant fields are named before the generic list, so the
    # message says *which half of the discussion is missing* rather than
    # reciting seven section names.
    for half, who in ((OPERATOR_HALF, "the operator's justification is empty"),
                      (CHEAPER_HALF, "the cheaper alternative is empty")):
        if half in v["missing"] or half in v["empty"]:
            v["reason"] = (
                f"{task}: {who} — `## {half}` carries no text. "
                + ("A brief the AI wrote alone is not a discussion; it is a claim "
                   "nothing verifies. Record his own words with "
                   f'`just brief "{task}" --accept "<what he said>"`.'
                   if half == OPERATOR_HALF else
                   "Naming the smaller version that gets most of the value is the "
                   "AI's half of this and is not optional."))
            return v

    if v["missing"] or v["empty"]:
        bits = []
        if v["missing"]:
            bits.append("missing section(s): " + ", ".join(v["missing"]))
        if v["empty"]:
            bits.append("unfilled section(s): " + ", ".join(v["empty"]))
        v["reason"] = "; ".join(bits) + " — a scaffold is not a brief."
        return v

    # The goal must be real. Refused, not warned about: a warning here is how
    # `Goal:`-less tasks accumulated until `session-start` reported "unmapped"
    # on its own next candidate and nothing acted on it.
    stated = " ".join(found["Goal"]).strip()
    ids = [g["id"] for g in _plan_goals(root)]
    hit = next((i for i in ids if re.search(rf"\b{re.escape(i)}\b", stated)), "")
    if ids and not hit:
        v["reason"] = (f"{task} names goal `{stated[:40]}` which is not in `.ai/plan.md`. "
                       f"Valid ids: {', '.join(ids)}. "
                       "A brief pointing at a goal that does not exist aligns with nothing.")
        return v
    v["goal"] = hit

    v["ok"] = True
    timing = (f"written before the code" if v["timing"] == "pre-work"
              else f"written after {v['changed_since']} file(s) had already changed"
              if v["timing"] == "post-hoc" else "timing not measurable (no recorded baseline)")
    v["reason"] = f"brief complete — both sides recorded, {timing}."
    return v


# ------------------------------------------------------------------ acceptance


def ai_half_unwritten(text: str) -> list[str]:
    """Which parts of the AI's explanation still carry no real text.

    `validate()` asks the same question of the whole brief, but only at
    `work-done` — by which time the discussion has already happened and the
    answer can no longer change anything. This is that rule applied at the one
    moment it is still useful: before the operator is asked to endorse.

    Reuses `plan._is_written`, which strips HTML comments, so an untouched
    scaffold reads as empty by construction. A second copy of that rule is the
    drift this repo keeps closing.
    """
    found = plan_mod._sections(text)
    return [s for s in AI_SECTIONS
            if s not in found or not plan_mod._is_written(found[s])]


def accept(task: str, words: str, root: Path | None = None) -> dict:
    """Record the operator's own words as a separate, dated act.

    Separate from `scaffold()` on purpose: acceptance being its own invocation is
    what makes it a distinct event in the record rather than a field the same
    keystroke could have filled while writing the rest.
    """
    root = root or ws_root()
    path = brief_path(task, root)
    if not path.is_file():
        return {"ok": False, "reason": f'no brief to accept — run `just brief "{task}"` first.'}
    if not words.strip():
        return {"ok": False, "reason": "an empty justification is what this file exists to refuse."}

    text = _read(path)

    # Explain first, then ask. Until PH22-T09 this function checked only that his
    # words were non-empty and that the section existed — never that there was
    # anything for him to react to. On 2026-08-16 a session scaffolded a brief and
    # asked for his justification against a page of untouched scaffold comments.
    # He stopped it: "u just picked one task, which i dont even have idea what the
    # task is all about." This is the checkable half of PH22-T01's honest limit —
    # *who* typed the justification can never be proven; whether the explanation
    # came first is a plain property of the file.
    owed = ai_half_unwritten(text)
    if owed:
        named = ", ".join(f"`## {s}`" for s in owed)
        return {"ok": False, "reason": (
            f"{task}: the AI has not explained this task yet — {named} "
            f"{'carries' if len(owed) == 1 else 'carry'} no text. "
            "He cannot justify a task he has not been shown, and the explanation "
            "must be *shown to him*, not merely written to the file. "
            f'Fill those sections, show them, then re-run '
            f'`just brief "{task}" --accept "<what he said>"`.')}

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    marker = f"## {OPERATOR_HALF}"
    if marker not in text:
        return {"ok": False, "reason": f"brief has no `{marker}` section."}

    head, _, tail = text.partition(marker)
    rest = tail.split("\n## ", 1)
    body = f"\n\n> {words.strip()}\n\n_Recorded {stamp}._\n"
    text = head + marker + body + ("\n## " + rest[1] if len(rest) > 1 else "")
    path.write_text(text, encoding="utf-8")

    try:
        import decision_log
        decision_log.record("prework", "accepted", "prework.py", root=root,
                            task=task, justification=words.strip()[:400])
    except Exception:  # noqa: BLE001 — the brief is the record; the log is the index
        pass
    return {"ok": True, "reason": f"recorded against {task}.", "path": str(path)}


# ------------------------------------------------------------------------ cli


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("task", nargs="?", help="task id, e.g. PH22-T01")
    ap.add_argument("--check", action="store_true", help="validate only; exit 1 if not ok")
    ap.add_argument("--accept", metavar="WORDS", help="record the operator's own words")
    ap.add_argument("--ratio", action="store_true", help="show the governance ratio only")
    ap.add_argument("--delegate", nargs="+", metavar=("DECISION", "REASON"),
                     help="--delegate accept | --delegate decline \"reason\" "
                          "(PH10-T10, only meaningful when the resolved tier is planner)")
    args = ap.parse_args()

    root = ws_root()

    if args.ratio:
        r = governance_ratio(root=root)
        if r["ratio"] is None:
            print(f"⚠️  governance ratio: unknown — {r['reason']}")
        else:
            print(f"📊 governance ratio: {r['ratio']:.0%} — {r['basis']}")
        return 0

    if not args.task or not TASK_RE.match(args.task):
        print("Usage: just brief \"PH#-T##\" [--check] [--accept \"his words\"] "
              "[--delegate accept|decline]",
              file=sys.stderr)
        return 2

    if args.delegate:
        decision = args.delegate[0]
        reason = " ".join(args.delegate[1:])
        res = apply_delegation_decision(args.task, decision, reason, root=root)
        print(("✅ " if res["ok"] else "❌ ") + res["reason"])
        return 0 if res["ok"] else 1

    if args.accept is not None:
        res = accept(args.task, args.accept, root=root)
        print(("✅ " if res["ok"] else "❌ ") + res["reason"])
        return 0 if res["ok"] else 1

    if not args.check:
        path = scaffold(args.task, root=root)
        existed = "already exists" if path.stat().st_size and _front_matter(_read(path)) \
            and plan_mod._sections(_read(path)) and validate(args.task, root=root)["ok"] \
            else "scaffolded"
        print(f"📝 Brief {existed}: {path.relative_to(root)}")

        r = governance_ratio(root=root)
        if r["ratio"] is not None and r["ratio"] >= 0.8:
            print(f"⚠️  {r['ratio']:.0%} of recent changed paths were governance, not product.")
            print(f"    Basis: {r['basis']}")
            print("    Lead the discussion with this — it is the number the operator asked for.")

        print("   Fill every section, then record HIS words:")
        print(f'     just brief "{args.task}" --accept "<what he actually said>"')

        prop = propose_delegation(args.task, root=root)
        if prop["offer"]:
            print()
            print("🤝 DELEGATION PROPOSAL — resolved tier: planner")
            if prop["test_ref"]:
                print(f"   Candidate test: {prop['test_ref']}")
                print(f"   Candidate command: {prop['command']}")
            else:
                print(f"   Candidate test: (none — {prop['test_reason']})")
            if prop["allowlist"]:
                print(f"   Candidate allowlist ({len(prop['allowlist'])} path(s), from "
                      f".ai/plans/{args.task}.md):")
                for p in prop["allowlist"]:
                    print(f"     - {p}")
            else:
                print(f"   Candidate allowlist: none — {prop['allowlist_reason']}")
            print(f'   Accept:  just brief "{args.task}" --delegate accept')
            print(f'   Decline: just brief "{args.task}" --delegate decline "<reason>"')
            print("   ➤ Offer this as an AskUserQuestion — never leave it as idle prose "
                  "(AGENTS.md).")

    v = validate(args.task, root=root)
    print(("✅ " if v["ok"] else "❌ ") + v["reason"])
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
