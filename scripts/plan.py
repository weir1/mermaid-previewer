#!/usr/bin/env python3
"""
plan.py — scaffold and validate `.ai/plans/PH#-T##.md`.

`.agents/skills/plan-before-code/SKILL.md` has instructed every agent to write a
plan before the first edit of a non-trivial task since 2026-08-02. Nothing read
it, `.ai/plans/` did not exist, and no plan was ever written. That is exactly
where the DoD convention sat before PH7-T02 gave it teeth: a documented rule,
quoted in AGENTS.md, enforced by nobody.

This module is the read/write half. `session_budget.verify_work_claim()` is the
enforcement half: a task whose declaration carries the `[complex]` marker cannot
be credited until a valid plan exists.

## What "valid" means, and why it is not "the file exists"
A plan is valid when all six sections from the skill are present, each one
carries at least one line that is neither blank nor an HTML comment, **and the
`Approach` declares at least one rejected alternative** (PH15-T07).

Existence alone would make `touch .ai/plans/PH9-T01.md` sufficient — a
requirement that measures a filename. So the scaffold deliberately writes its
guidance inside `<!-- -->` comments: an untouched scaffold has six headings and
nothing else, and fails validation by construction. Filling the plan is the only
way to pass, which is the entire point of the rule.

## The alternatives rule, and why it is positional
`SECTION_GUIDANCE["Approach"]` has instructed authors to record "the one or two
alternatives you rejected and why" since PH7-T03, and calls them the most useful
part of the file in six weeks. Nothing checked it: measured 2026-08-08, 5 of 25
real plans declared none and all 5 passed. The most valuable half of the template
was the unenforced half.

A **declaration** is a line whose content — after list markers, emphasis and
backticks — *begins* with `Rejected`. A paragraph merely containing the word does
not count, and that distinction is not pedantry: the ledger's own filed
observation of this bug ("PH15-T01 names 4, PH7-T06 names 3") over-counted both,
because in each case one "alternative" was a rejection stated mid-paragraph that
reads as a declaration to a human and is invisible to a parser. Accepting the
substring would certify exactly the plans that need certifying least — the eighth
mention-read-as-declaration in this repo, introduced on purpose while fixing the
seventh (PH15-T06).

The rule was not invented here; it is what the 20 passing plans already do, in
three house styles (`**Rejected alternative 1 — …**`, `**Rejected:** …`,
`Rejected alternatives:` over bullets), which is why it lands without rewriting
one of them.

## What this does NOT bind
That the plan is any *good*. Six headings each holding one junk word pass this
checker. Judging a plan is the `plan-before-code` skill's job; this is the
ratchet that makes skipping it visible, in the same split PH7-T04 drew between
`self_review.py` and the `self-review-diff` skill. Claiming otherwise here would
be the kind of unverified assertion this OS exists to stop.

Usage:
  plan.py PH7-T03              # create the scaffold, or report on an existing plan
  plan.py PH7-T03 --check      # validate only; exit 1 if the plan is missing/unfilled
  plan.py PH7-T03 --check --json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

TASK_RE = re.compile(r"PH\d+-T\d+")

# The six sections of the skill's template, in order. Kept in sync with
# .agents/skills/plan-before-code/SKILL.md — a test pins that they match.
REQUIRED_SECTIONS = ("Problem", "Approach", "Files to touch", "Risks", "Rollback", "DoD")

SECTION_GUIDANCE = {
    "Problem": "What is actually wrong today — the observed behaviour, not the desired one.\n"
               "If you cannot state it without describing the solution, you do not have it yet.",
    "Approach": "The design, plus the one or two alternatives you rejected and why.\n"
                "The rejected options are the most useful part of this file in six weeks,\n"
                "so `--check` REQUIRES at least one line here that starts with the word\n"
                "\"Rejected\". Prose mentioning a rejection does not satisfy it. E.g.:\n"
                "Rejected: a sidecar JSON file — two records to keep true, and he reads\n"
                "the Markdown.",
    "Files to touch": "Each path + what changes there. Anything not on this list is scope\n"
                      "creep: add it deliberately or leave it out.",
    "Risks": "What breaks if this is wrong, what that looks like in production,\n"
             "and what is irreversible.",
    "Rollback": "The exact way back. \"Revert the commit\" only counts if nothing\n"
                "external was mutated.",
    "DoD": "The concrete acceptance test. It must be something that can *fail*.\n"
           "`work-done` reads this line.",
}

_HEADING_RE = re.compile(r"^\s{0,3}##\s+(.+?)\s*$")
_FENCE_RE = re.compile(r"^\s{0,3}(```|~~~)")

# A rejected alternative is DECLARED when the line opens with the word, past only
# a list marker and markdown emphasis. Position is what separates a declaration
# from a mention — the same rule PH15-T06 used for the `[complex]` marker zone.
_DECLARED_REJECT_RE = re.compile(r"^\s*(?:[-*+]\s+|\d+[.)]\s+)?[*_`\s]*rejected\b", re.I)

_HOW_TO_DECLARE = (
    "add a line to `## Approach` that STARTS with \"Rejected\" — e.g. "
    "`Rejected: a sidecar JSON file — two records to keep true.`"
)


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def plan_path(task: str, root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / "plans" / f"{task}.md"


def _sections(text: str) -> dict[str, list[str]]:
    """Map `## Section` → its body lines, ignoring headings inside code fences.

    The fence check is not hypothetical: a plan that shows the template it is
    following (this repo's plans quote markdown) would otherwise register the
    example's `## Rollback` as the real section — and the example's placeholder
    comment as the real body, which is precisely the content the validator is
    supposed to reject.
    """
    out: dict[str, list[str]] = {}
    current = None
    fenced = False
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


def _is_written(body: list[str]) -> bool:
    """At least one line of actual content — not blank, not an HTML comment.

    Comments are how the scaffold carries its guidance, so a section that holds
    only comments is a section nobody has filled in yet.
    """
    joined = "\n".join(body)
    stripped = re.sub(r"<!--.*?-->", "", joined, flags=re.S)
    return any(line.strip() for line in stripped.splitlines())


def _declared_alternatives(body: list[str]) -> list[str]:
    """The lines in an `## Approach` body that declare a rejected alternative.

    Two exclusions, both of which are forgery holes rather than tidiness:

    * **Fenced code is skipped.** A plan that quotes the template — and this
      repo's plans do quote markdown — would otherwise satisfy the requirement by
      showing an example of it. `_sections()` already tracks fences for the twin
      of this reason.
    * **HTML comments are stripped.** The scaffold's own guidance now contains a
      `Rejected:` example line, so without this an *unfilled* scaffold would
      declare an alternative on the author's behalf. The requirement would then
      certify the one file it is guaranteed to be wrong about.
    """
    kept, fenced = [], False
    for line in body:
        if _FENCE_RE.match(line):
            fenced = not fenced
            continue
        if not fenced:
            kept.append(line)
    text = re.sub(r"<!--.*?-->", "", "\n".join(kept), flags=re.S)
    return [ln.strip() for ln in text.splitlines() if _DECLARED_REJECT_RE.match(ln)]


def validate(task: str, root: Path | None = None) -> dict:
    """Is there a written plan for `task`? Pure read — writes nothing."""
    path = plan_path(task, root)
    v = {"task": task, "path": str(path), "exists": path.is_file(),
         "ok": False, "missing": [], "empty": [], "alternatives": [], "reason": ""}
    if not v["exists"]:
        v["reason"] = (f"no plan at {path.relative_to(root or ws_root())} — "
                       f"run `just plan \"{task}\"` and write it before the code.")
        return v

    try:
        found = _sections(path.read_text(encoding="utf-8", errors="replace"))
    except OSError as exc:
        v["reason"] = f"plan file unreadable ({exc})."
        return v

    for section in REQUIRED_SECTIONS:
        if section not in found:
            v["missing"].append(section)
        elif not _is_written(found[section]):
            v["empty"].append(section)

    if v["missing"] or v["empty"]:
        bits = []
        if v["missing"]:
            bits.append("missing section(s): " + ", ".join(v["missing"]))
        if v["empty"]:
            bits.append("unfilled section(s): " + ", ".join(v["empty"]))
        v["reason"] = "; ".join(bits) + " — a scaffold is not a plan."
        return v

    # PH15-T07: the Approach owes at least one DECLARED rejected alternative.
    # Checked last, so an author with an empty section is told that first rather
    # than being sent to fix the subtler requirement in a file they have not
    # finished writing.
    v["alternatives"] = _declared_alternatives(found["Approach"])
    if not v["alternatives"]:
        v["reason"] = (f"{task}'s `## Approach` declares no rejected alternative — the part of "
                       f"a plan that is still useful in six weeks. To fix: {_HOW_TO_DECLARE} "
                       "Prose that merely mentions a rejection does not count.")
        return v

    v["ok"] = True
    v["reason"] = (f"plan written — all {len(REQUIRED_SECTIONS)} sections filled, "
                   f"{len(v['alternatives'])} rejected alternative(s) declared.")
    return v


def scaffold(task: str, root: Path | None = None) -> Path:
    """Write the empty plan template. Raises FileExistsError rather than clobber."""
    path = plan_path(task, root)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)

    parts = [f"# {task} — <title>\n"]
    for section in REQUIRED_SECTIONS:
        guidance = SECTION_GUIDANCE[section].replace("--", "—")
        parts.append(f"## {section}\n<!--\n{guidance}\n-->\n")
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scaffold or validate a task's plan in .ai/plans/.")
    ap.add_argument("task", help="task id, e.g. PH7-T03")
    ap.add_argument("--check", action="store_true",
                    help="validate only; never writes. Exit 1 if unwritten.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    task = args.task.strip()
    if not TASK_RE.fullmatch(task):
        print(f"❌ {args.task!r} is not a task id (expected PH#-T##).", file=sys.stderr)
        return 2

    root = ws_root()
    v = validate(task, root=root)

    if not args.check and not v["exists"]:
        path = scaffold(task, root=root)
        rel = path.relative_to(root)
        if args.json:
            print(json.dumps({"created": str(rel)}, indent=2))
        else:
            print(f"📝 Plan scaffolded: {rel}")
            print("   Fill every section BEFORE the first edit — an empty scaffold does "
                  "not satisfy `just work-done`.")
            print("   Procedure: .agents/skills/plan-before-code/SKILL.md")
        return 0

    if args.json:
        print(json.dumps(v, indent=2))
    elif v["ok"]:
        print(f"✅ {task} — {v['reason']}")
        print(f"   {Path(v['path']).relative_to(root)}")
    else:
        print(f"🛑 {task} — {v['reason']}")
        if v["exists"]:
            print(f"   {Path(v['path']).relative_to(root)}")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
