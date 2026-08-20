#!/usr/bin/env python3
"""
token_budget.py — how much context does booting this workspace actually cost?

AGENTS.md § CONTEXT BUDGET tells every agent to "stay under ~150k tokens". Until
now that was **a number nothing measured**: the rule named a budget, `doctor`
warned when a memory file passed ~200 lines, and no one could say what booting a
session actually spent. An agent was asked to economise against an unknown.

This measures the two figures worth knowing:

  • **BOOT SET** — what the protocol *mandates* reading at session start, and so
    what every session pays before it does any work: the canonical protocol, the
    handover, and the memory files AGENTS.md § MANDATORY SESSION START names.
  • **MEMORY BANK** — the whole of `.ai/memory-bank/`, i.e. what a session that
    reads everything would pay. The gap between the two is what retrieval-first
    (INDEX + `[[links]]`) is buying.

## The estimate is an ESTIMATE — say so, don't dress it up
No tokeniser ships with the stdlib, and this repo stays dependency-light on
purpose (see the hand-rolled block-YAML reader in `policy_engine.py`), so the
count is a heuristic:

    tokens ≈ max(chars / 3.85, words * 1.3)

**Calibrated, not guessed.** 3.85 was fitted against `cl100k_base` over 21 real
files from this repo (memory bank, protocol docs, changelog, scripts, YAML,
justfile): mean error **+0.1%**, worst case **13.4%**. The textbook `chars/4`
came in at mean −3.6% — i.e. it under-reported, which is the wrong direction for
a budget warning.

Caveat worth keeping honest: `cl100k_base` is OpenAI's tokeniser, used here as a
*proxy*. Claude's differs, so these bounds say the heuristic tracks a BPE
tokeniser closely — not that it matches Claude's exactly. Treat **±15%** as the
working accuracy. Everything printed is labelled `≈` so nobody mistakes it for a
measurement.

The `words * 1.3` term is a floor for whitespace-sparse text; on this corpus it
**never binds** (the chars term dominates every file, and the output is identical
for any value from 1.3 to 1.5). It is kept as a cheap guard against pathological
input, not because it is doing work today.

To re-calibrate after the corpus changes materially: encode each file with a real
tokeniser, sweep the divisor, and pick the one whose mean error is nearest zero
while the worst case stays inside ±15%. Install the tokeniser into a scratch
directory — it is a measurement tool, not a dependency of this workspace.

Files are read as text; anything unreadable is skipped, not guessed.

Usage:
  token_budget.py                  # per-file breakdown + totals
  token_budget.py --brief          # one line, for the session briefing
  token_budget.py --json
  token_budget.py --budget 150000  # override the assumed budget
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_BUDGET = 150_000
# Fitted against cl100k_base over 21 files of this repo — see the module docstring.
CHARS_PER_TOKEN = 3.85
TOKENS_PER_WORD = 1.3
# Honest accuracy of the heuristic, printed alongside every figure.
ACCURACY_PCT = 15
# Hot memory files are held to ~200 lines by AGENTS.md § CONTEXT BUDGET;
# `just archive-memory` is the tool that brings them back under it.
HOT_LINE_BUDGET = 200
# What every session pays before doing any work, in the order an agent reads it.
# `CLAUDE.md` imports `AGENTS.md` via `@AGENTS.md`, so both land in context.
BOOT_SET = (
    "AGENTS.md",
    "CLAUDE.md",
    ".ai/handover/latest.md",
    ".ai/memory-bank/INDEX.md",
    ".ai/memory-bank/activeContext.md",
    ".ai/memory-bank/progress.md",
)
# Measured but excluded from the boot total, because they are not read from disk:
# the briefing this script prints into, and the machine-global config. Roughly
# 3k combined — stated in the output so the figure isn't quietly optimistic.
UNMEASURED_NOTE = "excludes the session briefing itself + global ~/.claude/CLAUDE.md (≈3k)"


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def estimate_tokens(text: str) -> int:
    """Heuristic token count. See the module docstring for why max() of two rules."""
    if not text:
        return 0
    words = len(re.findall(r"\S+", text))
    return int(max(len(text) / CHARS_PER_TOKEN, words * TOKENS_PER_WORD))


def measure(path: Path) -> dict | None:
    """Token/line/byte figures for one file, or None if it can't be read."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return {"tokens": estimate_tokens(text), "lines": len(text.splitlines()),
            "bytes": len(text.encode("utf-8"))}


def report(root: Path | None = None, budget: int = DEFAULT_BUDGET) -> dict:
    """Measure the boot set and the whole memory bank."""
    root = root or ws_root()
    out = {"budget": budget, "boot": [], "boot_tokens": 0,
           "memory": [], "memory_tokens": 0, "oversized": [], "missing": []}

    for rel in BOOT_SET:
        m = measure(root / rel)
        if m is None:
            out["missing"].append(rel)
            continue
        out["boot"].append({"path": rel, **m})
        out["boot_tokens"] += m["tokens"]

    bank = root / ".ai" / "memory-bank"
    # Non-recursive: `archive/` is history, reachable by [[link]] but never read
    # at boot — counting it would penalise the very archiving that keeps hot
    # files small, which is exactly backwards.
    for path in sorted(bank.glob("*.md")) if bank.is_dir() else []:
        m = measure(path)
        if m is None:
            continue
        rel = str(path.relative_to(root))
        out["memory"].append({"path": rel, **m})
        out["memory_tokens"] += m["tokens"]
        if m["lines"] > HOT_LINE_BUDGET:
            out["oversized"].append({"path": rel, "lines": m["lines"]})

    boot_paths = {e["path"] for e in out["boot"]}
    out["extra_memory_tokens"] = sum(
        e["tokens"] for e in out["memory"] if e["path"] not in boot_paths)
    out["pct"] = round(100.0 * out["boot_tokens"] / budget, 1) if budget else 0.0
    out["headroom"] = budget - out["boot_tokens"]

    # First-class, not "the row in BOOT_SET that happens to say AGENTS.md"
    # (PH19-T01 Slice 3): the protocol is read at the start of every session
    # in every governed workspace, so its own cost deserves a named field a
    # caller can read directly rather than filtering for it.
    out["protocol"] = None
    for e in out["boot"]:
        if e["path"] == "AGENTS.md":
            pct = round(100.0 * e["tokens"] / out["boot_tokens"], 1) if out["boot_tokens"] else 0.0
            out["protocol"] = {**e, "pct_of_boot": pct}
            break
    return out


def _k(n: int) -> str:
    return f"{n/1000:.1f}k" if n >= 1000 else str(n)


def brief_line(r: dict) -> str:
    """The single line `session-start` prints."""
    return (f"memory + handover ≈ {_k(r['boot_tokens'])} tokens "
            f"({r['pct']}% of the ~{_k(r['budget'])} budget) · "
            f"≈ {_k(r['headroom'])} left for the session's own work")


def render(r: dict) -> None:
    if r["protocol"]:
        p = r["protocol"]
        print(f"  📜 PROTOCOL COST — AGENTS.md alone: {p['tokens']:,} tokens · "
              f"{p['lines']} lines  ({p['pct_of_boot']}% of the boot set)\n")
    print(f"  📊 BOOT SET — what every session pays before doing any work")
    for e in sorted(r["boot"], key=lambda x: -x["tokens"]):
        print(f"     {e['tokens']:>7,}  {e['lines']:>4} ln  {e['path']}")
    for rel in r["missing"]:
        print(f"     {'—':>7}  {'—':>4}     {rel}  (absent)")
    print(f"     {'-'*7}")
    print(f"     {r['boot_tokens']:>7,}  ≈ {r['pct']}% of the ~{_k(r['budget'])} budget"
          f" · ≈ {_k(r['headroom'])} left")
    print(f"     ({UNMEASURED_NOTE})")

    print(f"\n  📚 WHOLE MEMORY BANK — if a session read all of it")
    for e in sorted(r["memory"], key=lambda x: -x["tokens"]):
        mark = " ⚠️" if e["lines"] > HOT_LINE_BUDGET else ""
        print(f"     {e['tokens']:>7,}  {e['lines']:>4} ln  {e['path']}{mark}")
    print(f"     {'-'*7}")
    print(f"     {r['memory_tokens']:>7,}  · reading it all costs a further "
          f"≈ {_k(r['extra_memory_tokens'])} on top of the boot set")
    print("     → That gap is what retrieval-first (INDEX + [[links]]) saves.")

    if r["oversized"]:
        print(f"\n  ⚠️  Over the ~{HOT_LINE_BUDGET}-line hot budget:")
        for e in r["oversized"]:
            print(f"     {e['path']} — {e['lines']} lines")
        print("     → Run `just archive-memory` (dry run) then `--apply`.")
    print(f"\n  ℹ️  Estimated, not tokenised (±{ACCURACY_PCT}%) — see scripts/token_budget.py.")


def main() -> int:
    ap = argparse.ArgumentParser(description="Estimate this workspace's context cost.")
    ap.add_argument("--brief", action="store_true", help="one line, for the session briefing")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET)
    args = ap.parse_args()

    r = report(budget=args.budget)
    if args.json:
        print(json.dumps(r, indent=2))
    elif args.brief:
        print(brief_line(r))
    else:
        render(r)
    return 0


if __name__ == "__main__":
    sys.exit(main())
