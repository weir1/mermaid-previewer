#!/usr/bin/env python3
"""
handover.py — generate a COMPLETE session handover.  Run: `just handover "next step"`

Writes ONE file (.ai/handover/latest.md) that a fresh session reads to resume with
full context — without re-scanning the whole workspace (keeps the next session lean).
Captures: phase, what this session did (from session-state), ongoing/next tasks,
open issues, git HEAD, and the exact boot steps.

Usage: handover.py "next step" [--from claude|antigravity]
"""

import argparse
import json
import re
import subprocess
from datetime import datetime, timedelta, timezone

IST = timezone(timedelta(hours=5, minutes=30))
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
MB = ROOT / ".ai" / "memory-bank"


def _cap() -> int:
    """This workspace's work-slot cap — asked of the profile, never written down here.

    PH26-T03. The handover is the site with the longest blast radius: it does not
    merely display the cap, it *instructs the next session*, and that session obeys the
    sentence rather than any counter. A literal `2` here silently overrode an `8` the
    workspace had been granted.

    Imported inside the function, not at module scope, so a missing or broken
    `session_budget` costs the correct number and not the whole handover — and the
    fallback is the kernel default, which is the STRICTER answer.
    """
    try:
        import sys
        sys.path.insert(0, str(ROOT / "scripts"))
        import session_budget
        return session_budget.work_max(ROOT)
    except Exception:  # noqa: BLE001
        return 2


def _git(*a) -> str:
    try:
        return subprocess.check_output(["git", *a], stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return ""


def _phase() -> str:
    ac = MB / "activeContext.md"
    if ac.exists():
        m = re.search(r"\*\*Current Phase:\*\*\s*(.+)", ac.read_text())
        if m:
            return m.group(1).strip()
    return "unknown"


def _tasks_by_state(state: str, limit=6) -> list[str]:
    ac = MB / "activeContext.md"
    out = []
    if ac.exists():
        for line in ac.read_text().splitlines():
            if re.match(rf"^- .+\({state}\)", line):
                out.append(re.sub(r"^-\s+", "", line).strip())
    return out[:limit]


def _open_issues(limit=8) -> list[str]:
    ki = MB / "knownIssues.md"
    out = []
    if ki.exists():
        grab = False
        for line in ki.read_text().splitlines():
            if line.startswith("## Active") or line.startswith("## Ongoing"):
                grab = True
                continue
            if line.startswith("## ") and grab:
                grab = False
            if grab and line.strip().startswith("- "):
                out.append(line.strip())
    return out[:limit]


def _os_version() -> str:
    f = ROOT / ".ai" / "os_version.json"
    if f.exists():
        try:
            return json.loads(f.read_text()).get("version", "?")
        except Exception:
            pass
    return "?"


def _session_done() -> dict:
    f = ROOT / ".ai" / "session-state.json"
    if f.exists():
        try:
            return json.loads(f.read_text())
        except Exception:
            pass
    return {"work": [], "closure": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("next_step")
    ap.add_argument("--from", dest="frm", default="claude")
    args = ap.parse_args()

    now = datetime.now(IST).strftime("%Y-%m-%d %H:%M IST")
    head = _git("rev-parse", "--short", "HEAD")
    subj = _git("log", "-1", "--format=%s")
    dirty = _git("status", "--porcelain")
    done = _session_done()
    pending = _tasks_by_state("Pending")
    ongoing = _tasks_by_state("In Progress")
    issues = _open_issues()

    lines = []
    cap = _cap()
    lines.append(f"# Handover — {ROOT.name} (God Mode AI OS v{_os_version()})")
    lines.append(f"_Generated {now} · from {args.frm}_")
    lines.append("")
    lines.append("## ▶ Boot the next session (do this FIRST, in order)")
    lines.append("1. Read `AGENTS.md`, **this file**, `.ai/memory-bank/activeContext.md`, `progress.md`. "
                 "Do NOT scan the whole repo — these hold the full context.")
    lines.append("2. `just session-start` (Claude: runs via hook) and `just budget`.")
    lines.append(f"3. Pick **at most {cap}** work tasks below, then do the 3 closure tasks + a new handover.")
    lines.append("")
    lines.append("## Where we are")
    lines.append(f"- **Phase:** {_phase()}")
    lines.append(f"- **Git HEAD:** `{head}` — {subj}")
    lines.append(f"- **Working tree:** {'⚠️ uncommitted changes present' if dirty else 'clean'}")
    lines.append("")
    lines.append("## Done this session")
    lines.append(f"- Work tasks: {', '.join(done.get('work') or ['—'])}")
    lines.append(f"- Closure: {', '.join(sorted(set(done.get('closure') or []))) or '—'}")
    lines.append("")
    lines.append(f"## Ongoing / next — pick ≤ {cap}")
    for t in ongoing:
        lines.append(f"- 🔄 (in progress) {t}")
    for t in (pending or ["(none recorded — see .ai/docs/tasks.md backlog)"]):
        lines.append(f"- ⬜ {t}")
    lines.append("")
    lines.append("## Open issues")
    for i in (issues or ["- none"]):
        lines.append(i if i.startswith("-") else f"- {i}")
    lines.append("")
    lines.append("## Next step (handed off)")
    lines.append(f"> {args.next_step}")
    lines.append("")
    lines.append("## Session budget reminder")
    lines.append(f"- Max **{cap} work tasks** + **3 closure** (git-push · docs/memory update · issue triage), then hand off again.")

    out = ROOT / ".ai" / "handover"
    out.mkdir(parents=True, exist_ok=True)
    latest = out / "latest.md"
    latest.write_text("\n".join(lines) + "\n")

    ledger = ROOT / ".ai" / "session-ledger.md"
    if not ledger.exists():
        ledger.write_text("# Session Ledger (cross-AI handoff, append-only)\n\n")
    with ledger.open("a") as f:
        f.write(f"- {now} · from **{args.frm}** · git `{head}` · next → {args.next_step}\n")

    print(f"✅ Complete handover written to {latest.relative_to(ROOT)}")
    print("   The next session (Claude or Antigravity) reads that one file to resume with full context.\n")
    print("Paste to the next AI:\n")
    print("```")
    print(f"Resume the {ROOT.name} God Mode workspace. Read .ai/handover/latest.md first, "
          f"then AGENTS.md + activeContext.md, then `just session-start`. Respect the "
          f"{cap}+3 session budget.")
    print("```")


if __name__ == "__main__":
    main()
