#!/usr/bin/env python3
"""protocol_budget.py — does AGENTS.md obey its own context-budget rule? (PH19-T01 Slice 3,
token ceiling added PH24-T01)

AGENTS.md § CONTEXT BUDGET already tells every memory file to stay <= 200 lines,
and `doctor` already FAILs one that doesn't (see doctor.py's oversized-memory
check). AGENTS.md itself never obeyed that rule -- 450+ lines, read at the start
of every session in every workspace this kernel governs, exempt from the exact
budget it enforces on everything else. This module closes that self-exemption.

**A config number, not a buried constant.** `LINE_BUDGET` below is the SAME
200 the memory-file rule already uses (`fleet_status.HOT_MEMORY_MAX_LINES`,
`token_budget.HOT_LINE_BUDGET`) -- reused verbatim, not reinvented, so the
protocol is finally held to the rule it already states rather than a second,
independently-chosen number that could quietly drift from the first.

**PH24-T01 — a line cap alone is gameable.** A file of 100 long lines can blow
the token target while sailing under a line-count check; PH23-T05 discovered
its own token overrun by hand because nothing measured it. `TOKEN_BUDGET`
closes that gap as a second, independent ceiling using the same estimator
`token_budget.estimate_tokens()` already uses elsewhere (not a second, drifting
heuristic). **Anchored to a live measurement, not the assessment's aspirational
1,200** (which this file would already blow through on its preamble alone) --
a ratchet must be reachable to be enforced. The assessment's own baseline
(5,165 tokens / 189 body lines, captured 2026-08-17) was already stale by the
time this shipped: two days of real edits had grown the file to 196 lines /
5,538 tokens (2026-08-19) without changing its line-per-token density
(~28 tokens/line either way). 6,000 is that current measurement plus ~8%
headroom -- the same shape LINE_BUDGET's headroom over its own file takes,
not a re-derived guess.

**Deliberately narrow: this reports the debt, it does not pay it down.**
Moving a section's content into `.agents/skills/` (loaded on demand -- the
same shape the five existing engineering skills already use) is real content
work, out of scope for one slice. The check's job is to make the debt
visible and keep it visible until that follow-up work lands -- named here,
not silently assumed away.

Candidates are the biggest `##` sections by measured size, not judged by
content -- the same "never invent a number" rule this workspace applies
everywhere else. Ranked by lines when the line cap is what's blown, by
tokens when only the token cap is (the long-lines case this exists for) --
otherwise a token-only overage would point at the wrong sections to shrink.

Usage:
  protocol_budget.py            # this workspace's AGENTS.md verdict
  protocol_budget.py --json
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import token_budget  # noqa: E402

# Reused verbatim from the memory-file rule AGENTS.md itself states
# (§ CONTEXT BUDGET: "Hot memory files ≤ ~200 lines") -- see module docstring.
LINE_BUDGET = 200
# Independent second ceiling (PH24-T01) -- see module docstring for the
# measurement it is anchored to and why 1,200 (the assessment's aspirational
# figure) was rejected as unreachable.
TOKEN_BUDGET = 6_000
# Enough extraction candidates to act on, not so many the FAIL message
# becomes a second copy of the file.
TOP_N_CANDIDATES = 5

_H2_RE = re.compile(r"^##\s+(.+)$")
_FENCE_RE = re.compile(r"^```")


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def sections(text: str) -> list[dict]:
    """Split on `##` headings -- AGENTS.md's own section grain -- into
    [{"title", "line", "lines", "tokens"}], each sized to the next `##`
    heading or EOF. `###` sub-headings are left inside their parent section:
    a skill extraction moves a whole topic, not a fragment of one.

    Fenced ``` code blocks are tracked and skipped while matching: AGENTS.md's
    own CHANGELOG ENFORCEMENT section contains a literal `## [YYYY-MM-DD...]`
    example inside a fence, and treating that as a real heading split one
    real section into two and mis-sized both -- found live reviewing this
    slice's own diff against the real file, not by a synthetic test alone.
    """
    lines = text.splitlines()
    total_lines = len(lines)
    headings = []  # [(line_no, title), ...]
    in_fence = False
    for i, line in enumerate(lines, start=1):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        m = _H2_RE.match(line)
        if m:
            headings.append((i, m.group(1).strip()))

    out = []
    for idx, (line_no, title) in enumerate(headings):
        end_line = headings[idx + 1][0] if idx + 1 < len(headings) else total_lines + 1
        body = "\n".join(lines[line_no - 1:end_line - 1])
        out.append({"title": title, "line": line_no, "lines": end_line - line_no,
                     "tokens": token_budget.estimate_tokens(body)})
    return out


def _body(text: str) -> str:
    """AGENTS.md without its YAML frontmatter (PH22-T07).

    The budget exists because every agent reads this file's **rules** into
    context at session start. A derived `doc_stamp` block is metadata — nothing
    an agent obeys — so counting it would spend protocol budget on bookkeeping,
    and would have made stamping this file impossible while it sits at exactly
    its cap. This is a real, if small, loosening and is stated rather than
    slipped in; the body cap itself is unchanged.
    """
    if not text.startswith("---\n"):
        return text
    end = text.find("\n---\n", 3)
    return text if end == -1 else text[end + 5:]


def check(root: Path | None = None) -> dict:
    """Pure read. Absent AGENTS.md is not-applicable, not a failure -- doctor's
    own separate "root AGENTS.md" check already owns that fact.

    Two independent ceilings (PH24-T01): `ok` is true only when BOTH the line
    count and the estimated token count are within budget -- a file can blow
    either one on its own (long lines vs. many short ones), so neither check
    alone is sufficient. `over_by` / `tokens_over_by` are reported separately
    so a caller can tell which ceiling (or both) was the cause.
    """
    root = root or ws_root()
    path = root / "AGENTS.md"
    out = {"ok": True, "exists": path.is_file(), "path": "AGENTS.md",
           "lines": 0, "budget": LINE_BUDGET, "over_by": 0,
           "tokens": 0, "token_budget": TOKEN_BUDGET, "tokens_over_by": 0,
           "candidates": []}
    if not path.is_file():
        return out

    text = _body(path.read_text(encoding="utf-8", errors="replace"))
    n = len(text.splitlines())
    tokens = token_budget.estimate_tokens(text)
    out["lines"] = n
    out["tokens"] = tokens

    lines_over = n > LINE_BUDGET
    tokens_over = tokens > TOKEN_BUDGET
    if not lines_over and not tokens_over:
        return out

    out["ok"] = False
    if lines_over:
        out["over_by"] = n - LINE_BUDGET
    if tokens_over:
        out["tokens_over_by"] = tokens - TOKEN_BUDGET

    # A token-only overage (few, long lines) must point at the sections
    # actually driving the token count -- ranking by line count would name
    # the wrong candidates precisely in the case this ceiling exists for.
    key = (lambda s: -s["tokens"]) if (tokens_over and not lines_over) else (lambda s: -s["lines"])
    out["candidates"] = sorted(sections(text), key=key)[:TOP_N_CANDIDATES]
    return out


def render(c: dict) -> None:
    if not c["exists"]:
        print("  🛑 AGENTS.md not found — nothing to check.")
        return
    if c["ok"]:
        print(f"  ✅ AGENTS.md — {c['lines']} lines (budget {c['budget']}) / "
              f"{c['tokens']:,} tokens (budget {c['token_budget']:,})")
        return
    line_part = (f"{c['over_by']} over the {c['budget']}-line budget" if c["over_by"]
                 else f"within the {c['budget']}-line budget")
    token_part = (f"{c['tokens_over_by']:,} over the {c['token_budget']:,}-token budget"
                  if c["tokens_over_by"] else f"within the {c['token_budget']:,}-token budget")
    print(f"  ❌ AGENTS.md — {c['lines']} lines ({line_part}); "
          f"{c['tokens']:,} tokens ({token_part})")
    print("     Biggest sections (skill-extraction candidates, by size):")
    for s in c["candidates"]:
        print(f"       {s['lines']:>4} ln  {s['tokens']:>6,} tok  line {s['line']:<5} {s['title']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Does AGENTS.md obey its own line and token budget?")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    c = check(ws_root())
    if args.json:
        print(json.dumps(c, indent=2))
    else:
        render(c)
    return 0 if c["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
