#!/usr/bin/env python3
"""
plan_workspace.py — `.ai/plan.md`: what this workspace is actually for.

The OS knows *what* a session is doing (`activeContext.md`, the handover) and
*whether it is safe* (the gate, the policy engine). It has never known **why the
workspace exists**. `.ai/docs/project-init.md` has held a 7-question discovery
protocol since PH6-T14 — it is not on `onboard_project.sh`'s copy list, so no
workspace in the fleet has it, nothing runs it, nothing checks it, and it is
never surfaced at session start. `onboard_project.sh` signs off by printing
"Populate .ai/docs/prd.md", an instruction with no procedure behind it and
nothing that notices it was ignored.

So the operator's goal for a workspace lives only in his head and is
re-explained conversationally every session. That is the same shape as `DoD:`
before PH7-T02 and `plan-before-code` before PH7-T03: a documented procedure
with no artifact and no ratchet.

## The shape of a plan

```markdown
---
status: draft          # draft → discussing → agreed
agreed_at: null
plan_version: 1
---
# Workspace Plan — <name>

## What I want          ← USER-OWNED. No writer here ever rewrites it.
## Proposed additions   ← AI-OWNED. `- [ ]` open · `- [x]` accepted · `- [~]` declined
## Goals                ← `- G1 — <title> — <success criterion>`
## Non-goals
## Constraints
```

## Two properties this module exists to hold

**Agreement is a declaration, not a phrase.** `status:` is read from YAML
frontmatter only, so the word "agreed" occurring anywhere in the prose cannot
satisfy it. This repo has now been bitten four times by mention-vs-declaration
(`DoD:` in PH7-T02, `(In Progress)` in `active_task()`, `` `[complex]` `` in
PH7-T03, and `evidence-pack.sh` grepping a task id out of a summary sentence).
Designing the fifth one structurally up front is cheaper than a fifth
post-mortem. A goal is defined the same way: a *list item whose content starts
with* `G<n>`, so prose that mentions G1 is not a goal.

**The user's own words are never rewritten.** "What I want" is user-owned and
every writer here is pinned against it. If the AI could edit the vision in place
its paraphrase silently becomes the record, and the operator's actual intent is
unrecoverable.

## What this does NOT bind

That the plan is any *good*, or that it is still true. Five filled sections of
junk pass this checker. Judging a plan is a conversation; keeping it honest
against the code is PH9-T02's audit. This is the ratchet that makes an
un-discussed plan unable to produce documents — the same split PH7-T03 drew
between `plan.py` and the `plan-before-code` skill.

Usage:
  plan_workspace.py                # scaffold if absent, else report status
  plan_workspace.py --check        # pure read; exit 1 unless agreed and complete
  plan_workspace.py --agree        # draft → agreed (refuses an incomplete plan)
  plan_workspace.py --generate     # write .ai/docs/prd.md from the agreed goals
  plan_workspace.py --check --json
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

# The five sections of a workspace plan, in order.
REQUIRED_SECTIONS = ("What I want", "Proposed additions", "Goals", "Non-goals", "Constraints")

# The section the AI must never rewrite. Named once, used by every writer.
USER_SECTION = "What I want"

SECTION_GUIDANCE = {
    "What I want": "YOUR WORDS. Plain English, as long or short as you like — what do you\n"
                   "want this workspace to achieve? The AI may propose a clearer rewrite,\n"
                   "but it replaces nothing until you accept it and your verbatim original\n"
                   "is kept underneath. It proposes additions below; you accept or decline.\n"
                   "\n"
                   "Three callouts, written by you, are what the session opening reads.\n"
                   "Without them it can only say what this workspace is, not why it is here:\n"
                   "  **Standing context:** <what this workspace is, in one line>\n"
                   "  **Why it exists:** <the problem it was built to end>\n"
                   "  **How it helps:** <what it does for you day to day>",
    "Proposed additions": "The AI writes here, one item per line:\n"
                          "  - [ ] P1 — <the addition> — why it is worth doing\n"
                          "You resolve each one: [x] accepted, [~] declined.\n"
                          "Nothing can be agreed while an item is still [ ].",
    "Goals": "The outcomes you both settled on:\n"
             "  - G1 — <title> — <how you will know it is done>\n"
             "A goal is a list item STARTING with its id. Prose mentioning G1 is not a goal.",
    "Non-goals": "What this workspace deliberately will not do. This is what stops\n"
                 "scope creep from being argued later.",
    "Constraints": "Stack, budget, time, privacy, anything that bounds the design.",
}

_HEADING_RE = re.compile(r"^\s{0,3}##\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")

# A goal/proposal is a LIST ITEM whose content starts with its id — optional bold
# markers around it. Anything else is a mention. Same rule as `active_task()`.
_GOAL_RE = re.compile(r"^\s*[-*+]\s+\**\s*(G\d+)\b\**\s*(.*)$")
_PROPOSAL_RE = re.compile(r"^\s*[-*+]\s+\[([ x~])\]\s*\**\s*(P\d+)\b\**\s*(.*)$")

# Checkbox marker -> proposal status, for v["proposals"] (PH9-T10 — a price
# tag needs the proposal's text, not just the id `v["unresolved"]` already had).
_PROPOSAL_STATUS = {" ": "open", "x": "accepted", "~": "declined"}

# A goal line the close (PH9-T06) has already stamped as met: `mark_goals_met`
# appends this literally, so parsing it back is the only way `validate()` and a
# second close in the same day agree on "already marked" without a third
# duplicate notion of the same fact.
_GOAL_MET_RE = re.compile(r"\s*✅\s*\*{0,2}met\*{0,2}\s+(\d{4}-\d{2}-\d{2})\s*$", re.I)

GENERATED_MARKER = "generated_from: .ai/plan.md"


def ws_root() -> Path:
    """Git top-level, falling back to cwd. Every script here is cwd-relative."""
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def plan_path(root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / "plan.md"


def prd_path(root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / "docs" / "prd.md"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Leading `---` block → dict, plus the body after it.

    Deliberately line-based rather than PyYAML: the OS must run on system
    python3 with no dependencies, and this file's frontmatter is flat by design.
    Only a *leading* fence counts, so a `---` rule further down the document
    cannot introduce a second, contradicting status.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            fm = {}
            for line in lines[1:i]:
                if ":" in line and not line.lstrip().startswith("#"):
                    k, _, v = line.partition(":")
                    fm[k.strip()] = v.strip()
            return fm, "\n".join(lines[i + 1:])
    return {}, text


def _sections(text: str) -> dict[str, list[str]]:
    """`## Section` → body lines, ignoring headings inside code fences.

    The fence check matters because a plan that quotes its own template — this
    module's docstring does exactly that — would otherwise register the example's
    `## Goals` as the real section.
    """
    out: dict[str, list[str]] = {}
    current, fenced = None, False
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            fenced = not fenced
            if current:
                out[current].append(line)
            continue
        if not fenced:
            m = _HEADING_RE.match(line)
            if m:
                current = m.group(1).strip()
                out.setdefault(current, [])
                continue
        if current:
            out[current].append(line)
    return out


def _uncommented(body: list[str]) -> list[str]:
    """Section lines with HTML comments removed — the only view items are read from.

    The scaffold carries its guidance *inside* `<!-- -->`, and that guidance shows
    an example goal and an example proposal. Parsing raw lines therefore made
    every freshly scaffolded plan carry one phantom `G1` and one phantom
    unresolved `P1`, and made the kernel's own plan report "P1, P1" — the
    template's illustration of an item counting as one.

    That is mention-vs-declaration for the fifth time in this repo (`DoD:`,
    `(In Progress)`, `` `[complex]` ``, `evidence-pack.sh`'s task-id grep), and
    the first where the tool generated its own false positive. `_is_written` had
    always stripped comments; the item parsers had not. One helper now, so the
    two cannot drift apart again.
    """
    return re.sub(r"<!--.*?-->", "", "\n".join(body), flags=re.S).splitlines()


def _is_written(body: list[str]) -> bool:
    """One line of real content — not blank, not an HTML comment.

    The scaffold carries its guidance in comments, so a section holding only
    comments is one nobody has filled in. Existence alone would make `touch`
    sufficient — a requirement that measures a filename.
    """
    return any(line.strip() for line in _uncommented(body))


def validate(root: Path | None = None) -> dict:
    """Read the plan and report on it. Pure read — writes nothing, ever.

    Kept strictly side-effect free so PH9-T04 can render plan status inside
    `session-start` without mutating state. PH7-T09 is the standing lesson: a
    read path that writes is a bug waiting for a hook to re-fire.
    """
    path = plan_path(root)
    v = {"path": str(path), "exists": path.is_file(), "ok": False, "status": None,
         "agreed": False, "agreed_at": None, "missing": [], "empty": [],
         "unresolved": [], "proposals": [], "goals": [], "reason": ""}

    if not v["exists"]:
        v["reason"] = ("no plan for this workspace — run `just plan-workspace` and write, "
                       "in plain English, what you want it to achieve.")
        return v

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        v["reason"] = f"plan unreadable ({exc})."
        return v

    fm, body = _split_frontmatter(raw)
    v["status"] = fm.get("status")
    agreed_at = fm.get("agreed_at")
    v["agreed_at"] = agreed_at if agreed_at not in (None, "", "null", "~") else None
    v["agreed"] = v["status"] == "agreed"

    found = _sections(body)
    for section in REQUIRED_SECTIONS:
        if section not in found:
            v["missing"].append(section)
        elif not _is_written(found[section]):
            v["empty"].append(section)

    for line in _uncommented(found.get("Goals", [])):
        m = _GOAL_RE.match(line)
        if m:
            met_m = _GOAL_MET_RE.search(line)
            # The met-marker is stripped back out of the title so a stamped goal's
            # display text doesn't grow a `✅ met …` suffix everywhere its title is
            # shown (goal_progress render lines, PRD generation, this module's own
            # `_report`). "Was it stamped" lives in `marked_met`, not in the title.
            title = _GOAL_MET_RE.sub("", m.group(2)).strip(" —-:*")
            v["goals"].append({"id": m.group(1), "title": title,
                               "marked_met": bool(met_m),
                               "marked_met_at": met_m.group(1) if met_m else None})

    for line in _uncommented(found.get("Proposed additions", [])):
        m = _PROPOSAL_RE.match(line)
        if not m:
            continue
        v["proposals"].append({"id": m.group(2), "text": m.group(3).strip(" —-:*"),
                               "status": _PROPOSAL_STATUS.get(m.group(1), "open")})
        if m.group(1) == " ":
            v["unresolved"].append(m.group(2))

    v["reason"] = _blocker(v) or (
        f"agreed{' ' + v['agreed_at'] if v['agreed_at'] else ''} · "
        f"{len(v['goals'])} goal(s)")
    v["ok"] = not _blocker(v)
    return v


def _user_words(root: Path | None = None) -> str:
    """The uncommented body of `What I want`, or "" when there is no plan."""
    path = plan_path(root)
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    _, body = _split_frontmatter(raw)
    return "\n".join(_uncommented(_sections(body).get(USER_SECTION, [])))


def _callout(text: str, label: str) -> str:
    """A `**Label:** …` callout's value, collapsed to one line, or "".

    Runs to the next blank line, so a callout may wrap in the source and still
    render as a sentence. `_uncommented` has already removed the scaffold's
    guidance, so a label that appears only inside the template comment is a
    mention and does not match — the same declaration rule goals and proposals
    are parsed under.
    """
    m = re.search(rf"\*\*{re.escape(label)}[^*]*\*\*:?\s*(.+?)(?:\n\s*\n|\Z)", text, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


PURPOSE_CALLOUT = "Standing context"
WHY_CALLOUT = "Why it exists"
HELPS_CALLOUT = "How it helps"


def essence(root: Path | None = None) -> dict:
    """What this workspace is, why it exists, how it helps (PH9-T18).

    `purpose_line` answers only the first, and the session opening was leading
    with it as though it were the whole explanation. The operator asked for
    "a brief explaination of workspace, why it exist, how it helps" — three
    statements, and the second two had nowhere to live.

    They live as two more callouts in `What I want`, read by the same parser as
    the `Standing context` one that was already there: a new top-level section
    would have to be either added to `REQUIRED_SECTIONS` (invalidating every
    plan that already exists) or exempted from validation (a section the
    checker ignores). Unwritten callouts come back as "" and are listed in
    `missing`, because a renderer that can compose a purpose is a renderer that
    can invent one, and this is the user's statement of intent, not the OS's.

    Pure read.
    """
    text = _user_words(root)
    out = {
        "what": purpose_line(root=root),
        "why": _callout(text, WHY_CALLOUT),
        "helps": _callout(text, HELPS_CALLOUT),
    }
    out["missing"] = [label for label, key in
                      ((PURPOSE_CALLOUT, "what"), (WHY_CALLOUT, "why"), (HELPS_CALLOUT, "helps"))
                      if not out[key]]
    out["complete"] = not out["missing"]
    return out


def purpose_line(root: Path | None = None) -> str:
    """One line, in the user's own words: what is this workspace *for*? (PH9-T04)

    `session-start` leads every briefing with this, so it has to come from the
    plan's own text rather than a second summary someone has to keep in sync.
    Preferred source is a written **Standing context** callout inside `What I
    want` — this workspace's own plan already carries one, added the moment an
    audit or a discussion needed to say what a workspace *is* in one line. Where
    none has been written, falls back to the first real sentence of the user's
    own words. An unwritten or missing plan returns "" — never invented text.

    Pure read, and deliberately reuses `_sections`/`_uncommented` rather than a
    second parser: this module owns parsing `.ai/plan.md`, on purpose, so a
    change to the section-comment convention only has one place to break. The
    callout match itself is `_callout`, shared with `essence()` (PH9-T18) for
    the same reason.
    """
    text = _user_words(root)
    line = _callout(text, PURPOSE_CALLOUT)
    if line:
        return line
    for raw_line in text.splitlines():
        candidate = raw_line.strip().lstrip("#>").strip()
        candidate = re.sub(r"^\d+\.\s*", "", candidate)  # a numbered list item
        if candidate:
            return re.sub(r"\s+", " ", candidate).strip()
    return ""


def mark_goals_met(root: Path | None = None, met_ids: set[str] | None = None,
                    when: str | None = None) -> dict:
    """Stamp `✅ **met** <date>` on each `met_ids` goal's own line in `## Goals`. (PH9-T06)

    The write half of "a completed goal is marked in the plan, so it cannot drift
    from reality" — called from `session_close.write_back()`, never from a read
    path (PH7-T09's standing lesson: a report that also writes is a hook re-fire
    from corrupting state, so the two stay separate functions on purpose).

    Idempotent and surgical: a goal line already carrying the marker is left with
    its original date (the *first* close to notice wins), and only the matched
    goal's own line changes — every other byte of the plan, including every other
    goal and the user's own `What I want`, is untouched. `met_ids` naming a goal
    this plan does not declare matches nothing, the same "never invent a mapping"
    rule `goal_progress.py` applies to a dangling `Goal:` reference.
    """
    path = plan_path(root)
    out = {"ok": False, "reason": "", "marked": []}
    if not met_ids:
        out.update(ok=True, reason="nothing to mark.")
        return out
    if not path.is_file():
        out["reason"] = "no plan to write back to."
        return out
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        out["reason"] = f"plan unreadable ({exc})."
        return out

    stamp = when or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    lines = raw.splitlines()
    marked = []
    for i, line in enumerate(lines):
        m = _GOAL_RE.match(line)
        if not m or m.group(1) not in met_ids:
            continue
        if _GOAL_MET_RE.search(line):
            continue  # already stamped — the first date recorded wins
        lines[i] = line.rstrip() + f" ✅ **met** {stamp}"
        marked.append(m.group(1))

    if marked:
        path.write_text("\n".join(lines) + ("\n" if raw.endswith("\n") else ""),
                        encoding="utf-8")
    out.update(ok=True, marked=marked,
               reason=(f"marked {', '.join(marked)} met." if marked
                       else "already marked — nothing new to write."))
    return out


def _section_bounds(lines: list[str], name: str) -> tuple[int, int] | None:
    """`(first_body_line, end)` of `## <name>`, fence-aware. None if absent.

    Line-indexed rather than text-sliced because its callers insert or replace
    *between* lines and have to leave every other byte alone. Tracks code
    fences for the same reason `_sections` does: a plan quoting the template
    must not have the example's `## Goals` mistaken for the real one.
    """
    start = end = None
    fenced = False
    for i, line in enumerate(lines):
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced:
            continue
        m = _HEADING_RE.match(line)
        if not m:
            continue
        if start is None and m.group(1).strip() == name:
            start = i + 1
        elif start is not None:
            end = i
            break
    if start is None:
        return None
    return start, (len(lines) if end is None else end)


def _goals_section_bounds(lines: list[str]) -> tuple[int, int] | None:
    return _section_bounds(lines, "Goals")


def add_goal(text: str, root: Path | None = None) -> dict:
    """Append one new `- G<n> — <text>` to `## Goals`. (PH9-T05)

    The write half of accepting an off-plan request. It lives here, not in
    `off_plan.py`, because this module is the only writer of `.ai/plan.md`
    (`agree`, `mark_goals_met`, `retire`) — a second writer is how two parsers
    of the same file drift apart, and this one has to produce a line that
    `_GOAL_RE` will parse back as a *declaration* rather than a mention.

    Surgical and append-only within the section: the new goal goes after the
    last written line of `## Goals` and nothing else in the file is touched,
    the same discipline `mark_goals_met()` is held to — this is the user's own
    intent document, and an AI rewriting any other part of it is the failure
    mode `USER_SECTION` exists to prevent.

    Idempotent on the goal's text ("first call wins", as in `retire`): asking
    twice returns the existing id and writes nothing, so a repeated accept
    cannot fork one intention into two goals. The next id is `max + 1`, never
    `count + 1` — a plan whose goals are G1 and G7 must not re-issue G3 and
    collide with a retired or renumbered one.
    """
    path = plan_path(root)
    out = {"ok": False, "reason": "", "goal_id": None, "created": False}

    clean = re.sub(r"\s+", " ", text or "").strip(" —-:*")
    if not clean:
        out["reason"] = "a goal needs text — nothing to add."
        return out
    if not path.is_file():
        out["reason"] = ("no plan for this workspace — run `just plan-workspace` first. "
                         "A goal cannot be added to a plan that does not exist.")
        return out

    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        out["reason"] = f"plan unreadable ({exc})."
        return out

    existing = validate(root=root)["goals"]
    for g in existing:
        if g["title"].strip().lower() == clean.lower():
            out.update(ok=True, goal_id=g["id"],
                       reason=f"already a goal ({g['id']}) — plan unchanged.")
            return out

    lines = raw.splitlines()
    bounds = _goals_section_bounds(lines)
    if bounds is None:
        out["reason"] = "plan has no `## Goals` section — re-scaffold it before adding goals."
        return out

    next_id = f"G{max((int(g['id'][1:]) for g in existing), default=0) + 1}"
    start, end = bounds
    # After the last *written* line of the section, so the goal joins the list
    # rather than landing beyond the blank line that separates it from the next
    # heading — and so a section with no goals yet still gets a valid first one.
    insert_at = start
    for i in range(end - 1, start - 1, -1):
        if lines[i].strip():
            insert_at = i + 1
            break
    lines.insert(insert_at, f"- {next_id} — {clean}")
    path.write_text("\n".join(lines) + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    out.update(ok=True, goal_id=next_id, created=True,
               reason=f"added {next_id} — {clean}")
    return out


def retirement(root: Path | None = None) -> dict:
    """Is this workspace retired? Pure read of `.ai/plan.md` frontmatter. (PH9-T11)

    Read from frontmatter only — the same "a declaration, not a phrase" rule
    `status`/`agreed_at` already follow — so nothing in the prose can retire a
    workspace by accident. A missing or unreadable plan is simply not retired,
    never an error: most workspaces never get retired, and that has to be the
    cheap, ambient case.
    """
    path = plan_path(root)
    out = {"retired": False, "at": None, "reason": None}
    if not path.is_file():
        return out
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    fm, _ = _split_frontmatter(raw)
    out["retired"] = fm.get("retired", "").strip().lower() == "true"
    if out["retired"]:
        out["at"] = fm.get("retired_at") or None
        out["reason"] = fm.get("retired_reason") or None
    return out


def ladder_stage(root: Path | None = None) -> int | None:
    """Which North-Star ladder stage this workspace serves — `.ai/plan.md`
    frontmatter's `ladder_stage:` (PH16-T04, Goal G9), or `None` when it is
    genuinely undeclared. Same "a declaration, not a phrase" rule as
    `retirement()`: read from frontmatter only, so a stage number mentioned
    in prose (e.g. an audit discussing "stage 2 work") can never be mistaken
    for the workspace's own declaration.

    Deliberately does NOT validate the number against `.ai/north-star.yaml` —
    that file is kernel-only and not deployed to the fleet (PH11-T02), so a
    workspace has no copy of the ladder to check against locally. Cross-
    referencing against the real ladder is `fleet_ladder.py`'s job, run from
    the kernel where both facts are available.
    """
    path = plan_path(root)
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    fm, _ = _split_frontmatter(raw)
    raw_stage = fm.get("ladder_stage")
    if raw_stage in (None, "", "~", "null"):
        return None
    try:
        return int(raw_stage)
    except (TypeError, ValueError):
        return None


def retire(reason: str, root: Path | None = None, when: str | None = None) -> dict:
    """Mark this workspace retired — done being worked on, not "all goals met." (PH9-T11)

    Deliberately a different fact from `goal_progress.all_met()`: a workspace
    can be retired with goals unfinished (abandoned, superseded) or reach 100%
    without ever being explicitly retired (nothing left today, but not
    declared closed). This is the operator's own declaration, the write half
    of `retirement()`, landing in `.ai/plan.md` frontmatter next to
    `status`/`agreed_at` — the same file, the same declaration convention.

    Idempotent, the same "first call wins" contract `mark_goals_met()` already
    has: a workspace already retired keeps its original reason and date, so a
    re-run cannot quietly replace a real reason with a vaguer one. There is no
    un-retire here — reactivating a workspace is a separate, larger decision
    (does old evidence still count? does the plan need re-agreeing?) than the
    inverse of this function.
    """
    path = plan_path(root)
    out = {"ok": False, "reason": ""}
    if not path.is_file():
        out["reason"] = "no plan to retire — nothing for this workspace to declare done."
        return out

    existing = retirement(root)
    if existing["retired"]:
        out.update(ok=True, reason=f"already retired ({existing['reason']}).")
        return out

    clean_reason = re.sub(r"\s+", " ", reason or "").strip()
    if not clean_reason:
        out["reason"] = "a retirement needs a reason."
        return out

    raw = path.read_text(encoding="utf-8", errors="replace")
    if "\n---" not in raw:
        out["reason"] = "plan has no frontmatter — status must be declared there, not in prose."
        return out
    stamp = when or datetime.now(timezone.utc).strftime("%Y-%m-%d")
    head, body = raw.split("\n---", 1)
    for key, value in (("retired", "true"), ("retired_at", stamp), ("retired_reason", clean_reason)):
        if re.search(rf"^{key}:.*$", head, flags=re.M):
            head = re.sub(rf"^{key}:.*$", f"{key}: {value}", head, count=1, flags=re.M)
        else:
            head += f"\n{key}: {value}"
    path.write_text(head + "\n---" + body, encoding="utf-8")
    out.update(ok=True, reason=f"retired {stamp}: {clean_reason}")
    return out


def _blocker(v: dict) -> str:
    """The one reason this plan is not ready, or "" if it is. Order = what to fix first."""
    if not v["exists"]:
        return v["reason"]
    if v["missing"]:
        return "missing section(s): " + ", ".join(v["missing"])
    if v["empty"]:
        return ("unfilled section(s): " + ", ".join(v["empty"]) +
                " — a scaffold is not a plan.")
    if v["unresolved"]:
        return ("unresolved proposal(s): " + ", ".join(v["unresolved"]) +
                " — mark each [x] accepted or [~] declined.")
    if not v["goals"]:
        return ("no goals — a goal is a list item starting with its id, "
                "e.g. `- G1 — <title> — <how you know it is done>`.")
    if not v["agreed"]:
        return (f"status is {v['status'] or 'unset'}, not agreed — "
                "run `just plan-workspace --agree` once you are happy with it.")
    return ""


def scaffold(root: Path | None = None) -> Path:
    """Write the empty plan. Raises FileExistsError rather than clobber a real one."""
    path = plan_path(root)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    name = (root or ws_root()).name
    parts = ["---\nstatus: draft\nagreed_at: null\nplan_version: 1\n---\n",
             f"# Workspace Plan — {name}\n"]
    for section in REQUIRED_SECTIONS:
        guidance = SECTION_GUIDANCE[section].replace("--", "—")
        parts.append(f"## {section}\n<!--\n{guidance}\n-->\n")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def agree(root: Path | None = None) -> dict:
    """draft → agreed. Refuses an incomplete plan; the status does not move.

    This is the moment the plan becomes the thing later sessions are judged
    against, so it fails closed: an unfilled section, an open proposal, or zero
    goals all block it, and nothing is written on a refusal.
    """
    path = plan_path(root)
    v = validate(root=root)
    if not v["exists"]:
        return {"ok": False, "reason": v["reason"]}
    if v["agreed"]:
        return {"ok": True, "reason": f"already agreed ({v['agreed_at'] or 'undated'})."}

    # Everything except the status itself must be in order.
    v_without_status = dict(v, agreed=True, status="agreed")
    blocker = _blocker(v_without_status)
    if blocker:
        return {"ok": False, "reason": blocker}

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    raw = path.read_text(encoding="utf-8")
    fm, _ = _split_frontmatter(raw)
    if "status" not in fm:
        return {"ok": False, "reason": "plan has no frontmatter — status must be declared "
                                       "there, not in prose. Re-scaffold or add it."}
    # Frontmatter-only substitution: the body is never touched, so the user's
    # own words in "What I want" cannot be rewritten by agreeing.
    head, body = raw.split("\n---", 1)[0], raw.split("\n---", 1)[1]
    head = re.sub(r"^status:.*$", "status: agreed", head, count=1, flags=re.M)
    if re.search(r"^agreed_at:.*$", head, flags=re.M):
        head = re.sub(r"^agreed_at:.*$", f"agreed_at: {now}", head, count=1, flags=re.M)
    else:
        head += f"\nagreed_at: {now}"
    path.write_text(head + "\n---" + body, encoding="utf-8")
    return {"ok": True, "reason": f"agreed at {now} · {len(v['goals'])} goal(s).",
            "agreed_at": now, "goals": v["goals"]}


# ── Clarity rewrite (PH9-T07) ──────────────────────────────────────────────
#
# `AGENTS.md` was amended on 2026-08-05: the AI *does* rewrite "What I want"
# for clarity, and the verbatim original stays underneath in a collapsed block
# so "the core survived" is checkable rather than asserted. The amendment sat
# in the protocol with nothing implementing it — the code still enforced the
# superseded "never rewrite" rule, so the protocol and the tool disagreed.
#
# Two properties carry the whole safety argument:
#   1. **Propose ≠ accept.** A rewrite is staged in its own file and replaces
#      nothing until a second, explicit call. The user's own words are the one
#      thing here no revert can reconstruct if it was never committed.
#   2. **The original is the FIRST original, permanently.** A second rewrite
#      carries the existing archive through untouched instead of archiving the
#      previous polish — otherwise each accepted rewrite quietly moves the
#      record one paraphrase further from what he actually wrote, which is
#      exactly what the old rule was protecting against.
REWRITE_FILENAME = "plan.rewrite.md"
ARCHIVE_SUMMARY = "Original, in my own words (verbatim — never rewritten)"
_DETAILS_RE = re.compile(r"<details>\s*<summary>(.*?)</summary>(.*?)</details>", re.S)
# The archive is identified by what its summary *says*, not by being the first
# `<details>` in the section. Matching any collapsible block would mean a user
# who writes one for his own reasons has his surrounding prose discarded on the
# next accepted rewrite — the single worst outcome this feature can produce.
# Loose on purpose: the kernel's own archive was hand-written by an earlier
# session with a different summary, and it still has to be recognised.
_ARCHIVE_MARKER_RE = re.compile(r"\boriginal\b|\bverbatim\b", re.I)


def _find_archive(text: str) -> re.Match | None:
    """The verbatim-original block in `text`, or None if there isn't one."""
    for m in _DETAILS_RE.finditer(text):
        if _ARCHIVE_MARKER_RE.search(m.group(1)):
            return m
    return None


def rewrite_path(root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / REWRITE_FILENAME


def staged_rewrite(root: Path | None = None) -> dict:
    """The pending rewrite, if one is staged. Pure read."""
    path = rewrite_path(root)
    out = {"pending": False, "text": "", "path": str(path)}
    if not path.is_file():
        return out
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    out.update(pending=bool(text.strip()), text=text)
    return out


def propose_rewrite(text: str, root: Path | None = None) -> dict:
    """Stage a clarified `What I want` for the user to accept or reject.

    **Writes the staging file only — `.ai/plan.md` is byte-identical after
    this call.** The proposal is a document on disk rather than a message in a
    chat so the user can read it, diff it, and come back to it next session.
    """
    path = rewrite_path(root)
    out = {"ok": False, "reason": "", "path": str(path)}
    if not (text or "").strip():
        out["reason"] = "an empty rewrite proposes nothing."
        return out
    if not plan_path(root).is_file():
        out["reason"] = "no plan to rewrite — run `just plan-workspace` first."
        return out
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text.rstrip() + "\n", encoding="utf-8")
    out.update(ok=True, reason=(f"rewrite staged in .ai/{REWRITE_FILENAME} — nothing in the "
                                "plan has changed. Accept it to apply, reject it to drop it."))
    return out


def reject_rewrite(root: Path | None = None) -> dict:
    """Drop the staged rewrite. The plan was never touched, so there is nothing to undo."""
    path = rewrite_path(root)
    if not path.is_file():
        return {"ok": True, "reason": "no rewrite was staged."}
    path.unlink()
    return {"ok": True, "reason": "staged rewrite discarded — the plan is unchanged."}


def accept_rewrite(root: Path | None = None) -> dict:
    """Apply the staged rewrite, keeping the verbatim original in the file.

    The only function that replaces the user's own words, and it refuses
    unless he explicitly staged something first. The archive block is carried
    through verbatim on every subsequent rewrite (see the module note above),
    so `verbatim_original()` keeps returning what he actually typed however
    many times the prose is polished.
    """
    path = plan_path(root)
    staged = staged_rewrite(root)
    out = {"ok": False, "reason": ""}

    if not staged["pending"]:
        out["reason"] = ("no rewrite staged — propose one first. Nothing replaces "
                         "`What I want` without an explicit acceptance.")
        return out
    if not path.is_file():
        out["reason"] = "no plan to apply the rewrite to."
        return out

    raw = path.read_text(encoding="utf-8", errors="replace")
    lines = raw.splitlines()
    bounds = _section_bounds(lines, USER_SECTION)
    if bounds is None:
        out["reason"] = f"plan has no `## {USER_SECTION}` section — re-scaffold it."
        return out

    start, end = bounds
    body = "\n".join(lines[start:end])
    existing = _find_archive(body)
    if existing:
        archive = existing.group(0)                 # the FIRST original, kept as-is
        original_text = existing.group(2).strip()
    else:
        # Everything currently in the section is his — including any collapsible
        # block he wrote himself, which is archived with the rest rather than
        # mistaken for an archive and left behind while his prose is dropped.
        original_text = body.strip()
        archive = (f"<details>\n<summary>{ARCHIVE_SUMMARY}</summary>\n\n"
                   f"{original_text}\n\n</details>")

    new_body = ["", staged["text"].strip(), "", archive, ""]
    path.write_text("\n".join(lines[:start] + new_body + lines[end:])
                    + ("\n" if raw.endswith("\n") else ""), encoding="utf-8")
    rewrite_path(root).unlink(missing_ok=True)      # consumed, so it cannot re-apply
    out.update(ok=True, reason=(f"`{USER_SECTION}` rewritten for clarity — the verbatim "
                                "original is kept in the collapsed block below it."),
               original_kept=bool(original_text))
    return out


def verbatim_original(root: Path | None = None) -> str:
    """The user's own words, as he first wrote them. "" if never rewritten.

    The retrievability the DoD demands, as a function rather than a claim
    about markup — a test can assert on this; it cannot assert on a promise.
    """
    path = plan_path(root)
    if not path.is_file():
        return ""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    _, body = _split_frontmatter(raw)
    section = "\n".join(_sections(body).get(USER_SECTION, []))
    m = _find_archive(section)
    return m.group(2).strip() if m else ""


# ── Interactive discussion (PH9-T07) ───────────────────────────────────────
def discussion_options(root: Path | None = None) -> list[dict]:
    """The open decisions on this plan, as labelled choices. Pure read.

    `AGENTS.md` requires real decisions to be asked as concrete multiple-choice
    questions, never as an open prose question the turn ends on. Nothing
    computed *what* the open decisions were, so every session re-derived them
    by reading the plan — and re-derived them differently. This is that list,
    ordered by what to resolve first, ready to hand to `AskUserQuestion` (or to
    render as a numbered menu where no such tool exists).

    It asks; it never answers, and it never writes. An agreed, unblocked plan
    returns `[]` — the honest "nothing to discuss".
    """
    root = root or ws_root()
    if not plan_path(root).is_file():
        return [{"id": "create",
                 "question": "This workspace has no plan. Write one?",
                 "options": [
                     {"label": "Scaffold it",
                      "description": "Create .ai/plan.md so you can say, in your own words, "
                                     "what this workspace is for."},
                     {"label": "Not now",
                      "description": "Carry on without a plan — nothing will be able to "
                                     "report standing or price new scope."}]}]

    v = validate(root=root)
    questions: list[dict] = []

    if staged_rewrite(root)["pending"]:
        questions.append({
            "id": "rewrite",
            "question": f"A clarified `{USER_SECTION}` is staged. Apply it?",
            "options": [
                {"label": "Accept",
                 "description": "Replace the section with the clarified text. Your verbatim "
                                "original stays in the file, in a collapsed block."},
                {"label": "Reject",
                 "description": "Discard the proposal. The plan is unchanged."}]})

    if v["missing"] or v["empty"]:
        unwritten = ", ".join(v["missing"] + v["empty"])
        questions.append({
            "id": "sections",
            "question": f"These plan sections are still empty: {unwritten}. Fill them now?",
            "options": [
                {"label": "Fill them in",
                 "description": "Work through them now — the plan cannot be agreed until "
                                "every section says something."},
                {"label": "Later",
                 "description": "Leave the plan as a draft. Nothing is generated from it "
                                "until it is complete and agreed."}]})

    for p in v["proposals"]:
        if p["status"] != "open":
            continue
        questions.append({
            "id": f"proposal:{p['id']}",
            "question": f"{p['id']} — {p['text']}",
            "options": [
                {"label": "Accept",
                 "description": f"Mark {p['id']} `[x]` — it becomes part of the plan."},
                {"label": "Decline",
                 "description": f"Mark {p['id']} `[~]` — recorded as considered and "
                                "rejected, not silently dropped."}]})

    # Only worth asking once everything else is resolved: `agree` refuses an
    # incomplete plan anyway, and offering it earlier invites a refusal the
    # user did not cause.
    if not v["agreed"] and not _blocker(dict(v, agreed=True, status="agreed")):
        questions.append({
            "id": "agree",
            "question": "The plan is complete. Agree it?",
            "options": [
                {"label": "Agree",
                 "description": "Lock it in as what this workspace is for. Sessions are "
                                "measured against these goals from here on."},
                {"label": "Keep drafting",
                 "description": "Leave it as a draft — no docs or versions are generated "
                                "from an unagreed plan."}]})
    return questions


# ── The finalize chain (PH9-T07) ───────────────────────────────────────────
def finalize(root: Path | None = None) -> dict:
    """agree → version → docs, in one invocation, stopping at the first refusal.

    The order was real but unwritten: docs must not be generated from an
    unagreed plan, and a version must not be proposed from goals nobody agreed
    to. It survived only in whichever session last performed it correctly.

    **`agree` gates the chain.** If it refuses, the later steps are reported
    `skipped` rather than omitted — a step that vanishes from the output reads
    as one that passed, and this repo's whole design law 2 is that a report
    never flatters itself.

    **The version step is advisory and never stops the chain.** It proposes a
    bump and writes nothing (a stamp is its own approved decision); a workspace
    with too little ledger history to propose one is not a reason to withhold
    the PRD, whose only real precondition is an agreed plan.
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "steps": []}

    a = agree(root=root)
    out["steps"].append({"step": "agree", "ok": a["ok"], "reason": a["reason"]})
    if not a["ok"]:
        for step in ("version", "docs"):
            out["steps"].append({"step": step, "ok": False, "skipped": True,
                                 "reason": "not attempted — the chain stopped at `agree`."})
        out["reason"] = f"stopped at `agree` — {a['reason']}"
        return out

    try:
        import version_plan
        ladder = version_plan.ladder(root)
        out["steps"].append({"step": "version", "ok": True, "advisory": True,
                             "reason": ladder["reason"],
                             "proposal": ladder.get("proposal")})
    except Exception as exc:  # noqa: BLE001
        out["steps"].append({"step": "version", "ok": True, "advisory": True,
                             "reason": f"no version proposal — version_plan unavailable ({exc})."})

    g = generate(root=root)
    out["steps"].append({"step": "docs", "ok": g["ok"], "reason": g["reason"]})
    out["ok"] = g["ok"]
    out["reason"] = (f"plan finalized — {g['reason']}" if g["ok"]
                     else f"stopped at `docs` — {g['reason']}")
    return out


def render_prd(v: dict, name: str, when: str) -> str:
    """The PRD text for an agreed plan. Goals are copied, never re-worded."""
    out = [f"---\n{GENERATED_MARKER}\ngenerated_at: {when}\nplan_agreed_at: "
           f"{v['agreed_at'] or 'undated'}\n---\n",
           f"# PRD — {name}\n",
           "> Generated from `.ai/plan.md`. **Do not hand-edit** — edit the plan and "
           "re-run `just plan-workspace --generate`.\n",
           "## Goals\n"]
    for g in v["goals"]:
        out.append(f"- **{g['id']}** — {g['title']}")
    out.append("")
    return "\n".join(out)


def generate(root: Path | None = None) -> dict:
    """Write `.ai/docs/prd.md` from the agreed goals. Refuses otherwise, writing nothing.

    Three refusals, in order: the plan is not agreed; a proposal is still open;
    or the target PRD exists and this tool did not write it. The last one is the
    only genuinely destructive path in this module — a hand-written PRD
    represents work no revert can reconstruct if it was never committed.
    """
    r = root or ws_root()
    v = validate(root=r)
    blocker = _blocker(v)
    if blocker:
        return {"ok": False, "reason": blocker}

    target = prd_path(r)
    if target.exists():
        existing = target.read_text(encoding="utf-8", errors="replace")
        if GENERATED_MARKER not in existing:
            return {"ok": False, "reason": (
                f"{target.name} exists and was not generated from the plan — refusing to "
                "overwrite hand-written work. Move or delete it first if you want it "
                "replaced.")}

    when = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_prd(v, r.name, when), encoding="utf-8")
    return {"ok": True, "reason": f"wrote {target.name} from {len(v['goals'])} goal(s).",
            "path": str(target), "goals": v["goals"]}


def _report(v: dict, root: Path) -> None:
    rel = Path(v["path"]).relative_to(root)
    if v["ok"]:
        print(f"✅ Workspace plan — {v['reason']}")
        for g in v["goals"]:
            print(f"     {g['id']} — {g['title']}")
    else:
        print(f"🛑 Workspace plan — {v['reason']}")
    print(f"   {rel}")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scaffold, validate, agree, or generate docs from .ai/plan.md.")
    ap.add_argument("--check", action="store_true", help="validate only; never writes.")
    ap.add_argument("--agree", action="store_true", help="mark the plan agreed.")
    ap.add_argument("--generate", action="store_true", help="write .ai/docs/prd.md.")
    ap.add_argument("--rewrite", metavar="TEXT",
                    help="stage a clarified `What I want` (writes nothing to the plan).")
    ap.add_argument("--accept-rewrite", action="store_true",
                    help="apply the staged rewrite, keeping the verbatim original.")
    ap.add_argument("--reject-rewrite", action="store_true", help="discard the staged rewrite.")
    ap.add_argument("--original", action="store_true",
                    help="print the user's verbatim original words.")
    ap.add_argument("--discuss", action="store_true",
                    help="the open decisions on this plan, as labelled options.")
    ap.add_argument("--finalize", action="store_true",
                    help="agree → version → docs in one run; stops at the first refusal.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = ws_root()

    if args.original:
        print(verbatim_original(root=root) or
              "(nothing archived — `What I want` has never been rewritten.)")
        return 0

    if args.discuss:
        questions = discussion_options(root=root)
        if args.json:
            print(json.dumps(questions, indent=2, ensure_ascii=False))
        elif not questions:
            print("✅ Nothing to discuss — the plan is complete and agreed.")
        else:
            print(f"🗣️  {len(questions)} open decision(s) — ask these as choices, "
                  "never as an open prose question:\n")
            for q in questions:
                print(f"  {q['question']}")
                for i, o in enumerate(q["options"], 1):
                    print(f"    {i}. {o['label']} — {o['description']}")
                print()
        return 0

    if args.finalize:
        r = finalize(root=root)
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        else:
            for s in r["steps"]:
                icon = "⏭️ " if s.get("skipped") else ("✅" if s["ok"] else "🛑")
                print(f"  {icon} {s['step']:<8} {s['reason']}")
            print(("✅ " if r["ok"] else "🛑 ") + r["reason"])
        return 0 if r["ok"] else 1

    if args.rewrite or args.accept_rewrite or args.reject_rewrite:
        if args.rewrite:
            r = propose_rewrite(args.rewrite, root=root)
        elif args.accept_rewrite:
            r = accept_rewrite(root=root)
        else:
            r = reject_rewrite(root=root)
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        else:
            print(("✅ " if r["ok"] else "🛑 ") + r["reason"])
        return 0 if r["ok"] else 1

    if args.agree or args.generate:
        r = agree(root=root) if args.agree else generate(root=root)
        if args.json:
            print(json.dumps(r, indent=2))
        else:
            print(("✅ " if r["ok"] else "🛑 ") + r["reason"])
        return 0 if r["ok"] else 1

    v = validate(root=root)
    if not args.check and not v["exists"]:
        path = scaffold(root=root)
        rel = path.relative_to(root)
        if args.json:
            print(json.dumps({"created": str(rel)}, indent=2))
        else:
            print(f"📝 Workspace plan scaffolded: {rel}")
            print("   Write section 1 in your own words — what do you want this "
                  "workspace to achieve?")
            print("   Then discuss it with the AI, resolve its proposals, and run "
                  "`just plan-workspace --agree`.")
        return 0

    if args.json:
        print(json.dumps(v, indent=2))
    else:
        _report(v, root)
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
