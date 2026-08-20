#!/usr/bin/env python3
"""
context_pack.py — one paste-able boot set for a task, assembled from what
already exists (PH20-T04, Goal G8).

## Why this exists

Retrieval-first (`AGENTS.md` § CONTEXT BUDGET) is a rule an agent follows by
discipline: read `INDEX.md`, read `codemap.md`, find the plan, check known
issues — four separate lookups, redone by hand every time. PH10's delegation
contract makes this concrete: an executor onboarding onto `PH#-T##` needs
exactly this bundle to start work without re-scanning the repo, and today
nothing assembles it — the operator (or the executor) does it by hand, which is
the exact context waste retrieval-first exists to kill, just moved one level up.

## Composed, not duplicated

Every field is read from a source this workspace already generates or
maintains — never a second index, per the task's own DoD:

  - **DoD / Goal / status / source line** — `task_ledger.find_task()`, the one
    reader of the ledger (PH7-T02/PH9-T08).
  - **Plan** — `.ai/plans/PH#-T##.md`, verbatim, if `just plan` wrote one.
  - **Codemap rows** — `codemap.scan()` (the same scan `just codemap` writes),
    filtered to modules this task's own text actually names: either the task id
    appears in the module's purpose sentence, or the module's filename appears
    in the task's title/DoD/plan text. A task that names no module gets an
    empty list, honestly — not every task touches code.
  - **INDEX pointers** — `.ai/memory-bank/INDEX.md`'s own bullet lines,
    filtered by shared significant words with the task's text (the same
    word-overlap idea `off_plan.match_goal()` uses for goals, at line
    granularity here since INDEX has no per-line title to score against). The
    two "Current state" pointers (`activeContext.md`, `progress.md`) are always
    included — every task is declared in the ledger and every task has history,
    so those two are relevant regardless of content.
  - **Open issues** — `note_issue.parse_entries()` (the same parser
    `issues-gap`/`resolve-issue` use), filtered to unresolved entries whose raw
    text names this task id.
  - **Token cost** — `token_budget.estimate_tokens()` run over the assembled
    text. Not re-estimated: the same divisor, the same caveat.

## Not found is not an empty pack

A task id the ledger has never heard of gets `found: False` and nothing else —
never a pack with empty sections that *looks* like it found nothing to report.
The two cases must stay visibly different, the same rule `token_budget.py`'s
`missing` list and `codemap.py`'s blank-purpose both already observe: absence
is reported, never silently smoothed into a zero.

Usage:
  context_pack.py PH20-T04            # human-readable, paste-able
  context_pack.py PH20-T04 --json
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import codemap  # noqa: E402
import note_issue  # noqa: E402
import task_ledger  # noqa: E402
import token_budget  # noqa: E402

# INDEX.md pointers under "## Current state" that are relevant to every task,
# regardless of content — every task is declared in the ledger and has a
# session history. Matched by the file the bullet's [[link]] targets, not by
# the human-readable label (the label wording is free to change).
ALWAYS_RELEVANT_TARGETS = (
    "activeContext.md",
    "progress.md",
)
# Below this many shared significant words, an INDEX line is not counted as
# "touching" the task — otherwise every line sharing one common word (a stop
# word slipping past the length filter) would match every task, and the filter
# would stop meaning anything. Same shape as off_plan.MATCH_THRESHOLD: a named
# constant, not a number buried in an expression.
MIN_INDEX_OVERLAP = 1
_STOPWORDS = {
    "this", "that", "with", "from", "into", "over", "read", "when", "what",
    "than", "then", "each", "have", "will", "your", "which", "their", "these",
    "about", "after", "before", "never", "every", "first", "there", "where",
}


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _words(text: str) -> set[str]:
    """Significant, lowercased words (len >= 4, not a stopword). Deliberately
    crude — the goal is "shares real vocabulary", not a ranked similarity."""
    return {w for w in re.findall(r"[a-zA-Z]{4,}", text.lower()) if w not in _STOPWORDS}


def _plan_block(task_id: str, root: Path) -> dict:
    rel = f".ai/plans/{task_id}.md"
    path = root / rel
    if not path.is_file():
        return {"path": rel, "exists": False, "content": ""}
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {"path": rel, "exists": False, "content": ""}
    return {"path": rel, "exists": True, "content": content}


def _codemap_rows(task_id: str, search_text: str, root: Path) -> list[dict]:
    """Modules this task's own text names — a filter over `codemap.scan()`,
    never a second scan of the source tree."""
    try:
        modules = codemap.scan(root)["modules"]
    except Exception:  # noqa: BLE001
        return []
    lowered = search_text.lower()
    hits = []
    for m in modules:
        stem = Path(m["path"]).name.lower()
        if task_id.lower() in m["purpose"].lower() or stem in lowered:
            hits.append({"path": m["path"], "purpose": m["purpose"], "recipe": m["recipe"]})
    return hits


def _index_pointers(search_text: str, root: Path) -> list[str]:
    """INDEX.md bullet lines relevant to this task — the always-relevant pair
    plus any line sharing significant vocabulary with the task's own text."""
    path = root / ".ai" / "memory-bank" / "INDEX.md"
    if not path.is_file():
        return []
    task_words = _words(search_text)
    out: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped.startswith("-"):
            continue
        m = re.search(r"\[\[([^\]#]+)", stripped)
        target = m.group(1) if m else ""
        always = any(target.endswith(t) for t in ALWAYS_RELEVANT_TARGETS)
        overlap = len(_words(stripped) & task_words) >= MIN_INDEX_OVERLAP
        if always or overlap:
            out.append(stripped)
    return out


def _open_issues(task_id: str, root: Path) -> list[dict]:
    """Unresolved `knownIssues.md` entries naming this task — reuses
    `note_issue.parse_entries()`, never a second issue parser."""
    path = root / ".ai" / "memory-bank" / "knownIssues.md"
    if not path.is_file():
        return []
    try:
        entries = note_issue.parse_entries(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:  # noqa: BLE001
        return []
    return [{"title": e["title"], "raw": e["raw"]}
            for e in entries if not e["resolved"] and task_id in e["raw"]]


def build_pack(task_id: str, root: Path | None = None) -> dict:
    """Assemble the pack, or report `found: False` with nothing else."""
    root = root or ws_root()
    task = task_ledger.find_task(task_id, root=root)
    if not task["found"]:
        return {"task": task_id, "found": False, "searched": task["searched"]}

    pack = {
        "task": task_id, "found": True,
        "title": task["title"], "status": task["status"], "dod": task["dod"],
        "goal": task["goal"], "test_ref": task["test_ref"],
        "source": task["source"], "line": task["line"], "complex": task["complex"],
    }
    pack["plan"] = _plan_block(task_id, root)

    search_text = " ".join([pack["title"], pack["dod"], pack["plan"]["content"][:2000]])
    pack["codemap_rows"] = _codemap_rows(task_id, search_text, root)
    pack["index_pointers"] = _index_pointers(search_text, root)
    pack["open_issues"] = _open_issues(task_id, root)

    pack["tokens"] = token_budget.estimate_tokens(render_text(pack))
    return pack


def render_text(pack: dict) -> str:
    """The paste-able boot set. `tokens` is deliberately excluded from the
    rendered text itself — it is a measurement OF this text, not part of it,
    and including it would make the estimate describe a string one line longer
    than the one actually handed to an executor."""
    if not pack.get("found"):
        return f"# {pack['task']}\n\nNot declared in {' or '.join(pack.get('searched', []))}.\n"

    lines = [f"# {pack['task']} — {pack['title'] or '(no title)'}", ""]
    if pack["status"]:
        lines.append(f"Status: {pack['status']}")
    if pack["goal"]:
        lines.append(f"Goal: {pack['goal']}")
    lines.append(f"Declared: {pack['source']}:{pack['line']}")
    lines.append("")
    lines.append("## Definition of Done")
    lines.append(pack["dod"] or "_(no DoD line declared)_")
    if pack["test_ref"]:
        lines.append(f"\ntest: {pack['test_ref']}")
    lines.append("")

    lines.append("## Plan")
    if pack["plan"]["exists"]:
        lines.append(f"({pack['plan']['path']})\n")
        lines.append(pack["plan"]["content"].rstrip())
    else:
        lines.append(f"_(none filed — {pack['plan']['path']} does not exist)_")
    lines.append("")

    lines.append("## Codemap rows this task names")
    if pack["codemap_rows"]:
        for r in pack["codemap_rows"]:
            recipe = f" — `{r['recipe']}`" if r["recipe"] else ""
            lines.append(f"- `{r['path']}` — {r['purpose'] or '(no docstring)'}{recipe}")
    else:
        lines.append("_(none matched)_")
    lines.append("")

    lines.append("## INDEX.md pointers")
    if pack["index_pointers"]:
        lines.extend(pack["index_pointers"])
    else:
        lines.append("_(none matched)_")
    lines.append("")

    lines.append("## Open known issues naming this task")
    if pack["open_issues"]:
        for i in pack["open_issues"]:
            lines.append(f"- {i['title']}")
    else:
        lines.append("_(none)_")
    lines.append("")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Assemble one paste-able boot set for a task from existing sources.")
    ap.add_argument("task", help="task id, e.g. PH20-T04")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if not task_ledger.TASK_RE.fullmatch(args.task.strip()):
        print(f"❌ {args.task!r} is not a task id (expected PH#-T##).", file=sys.stderr)
        return 2

    pack = build_pack(args.task.strip())
    if args.json:
        print(json.dumps(pack, indent=2))
    elif not pack["found"]:
        print(f"❌ {pack['task']} is not declared in {' or '.join(pack['searched'])}.")
    else:
        print(render_text(pack))
        print(f"≈ {pack['tokens']:,} tokens (token_budget.py, ±{token_budget.ACCURACY_PCT}%)")
    return 0 if pack["found"] else 1


if __name__ == "__main__":
    sys.exit(main())
