#!/usr/bin/env python3
"""
handoff.py — cross-AI handoff ledger.  Run: `just handoff "next step"`

Lets Claude and Antigravity tag-team on the same project with shared state:
  1. Appends one terse line to .ai/session-ledger.md (append-only audit trail).
  2. Prints a copy-pasteable Handoff Prompt for the next AI (either tool).

Usage: python3 scripts/handoff.py "next step" [--from claude|antigravity] [--task PH#-T##]
"""

import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def _ws_root() -> Path:
    try:
        t = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                    stderr=subprocess.DEVNULL, text=True).strip()
        if t:
            return Path(t)
    except Exception:
        pass
    return Path(".").resolve()


ROOT = _ws_root()
LEDGER = ROOT / ".ai" / "session-ledger.md"


def _git_hash() -> str:
    try:
        return subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return "no-commit"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("next_step", help="what the next AI should do")
    ap.add_argument("--from", dest="frm", default="claude", help="claude | antigravity")
    ap.add_argument("--task", default="", help="active PH#-T## task id")
    args = ap.parse_args()

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    ghash = _git_hash()
    task = args.task or "-"

    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    if not LEDGER.exists():
        LEDGER.write_text("# Session Ledger (cross-AI handoff, append-only)\n\n")
    with LEDGER.open("a") as f:
        f.write(f"- {ts} · from **{args.frm}** · task {task} · git `{ghash}` · next → {args.next_step}\n")

    print(f"✅ Handoff logged to {LEDGER.relative_to(ROOT)}\n")
    print("Paste this to the next AI (Claude or Antigravity):\n")
    print("```")
    print(f"Continuing work in the {ROOT.name} God Mode workspace.")
    print("Boot: read AGENTS.md + .ai/memory-bank/activeContext.md + progress.md, then run `just session-start`.")
    print(f"Handoff from {args.frm} at git {ghash} (task {task}).")
    print(f"Next task: {args.next_step}")
    print("Follow the validation gate before any side effects.")
    print("```")


if __name__ == "__main__":
    main()
