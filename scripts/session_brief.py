#!/usr/bin/env python3
"""
session_brief.py — portable Markdown session briefing.  Run: `just session-brief`

For contexts where the SessionStart hook does NOT run — claude.ai web, the
Antigravity chat panel, a fresh AI you're handing off to. Prints a clean
Markdown block you can paste as project instructions / the first message so any
AI boots with correct God Mode context.
"""

import json
import re
import subprocess
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


def _phase() -> str:
    ac = MB / "activeContext.md"
    if ac.exists():
        m = re.search(r"\*\*Current Phase:\*\*\s*(.+)", ac.read_text())
        if m:
            return m.group(1).strip()
    return "unknown"


def _pending() -> list[str]:
    ac = MB / "activeContext.md"
    out = []
    if ac.exists():
        for line in ac.read_text().splitlines():
            if re.match(r"^- .+\(Pending\)", line):
                out.append(re.sub(r"^-\s+", "", line).strip())
    return out


def _recent_changelog(n=3) -> list[str]:
    cl = ROOT / "AI_CHANGELOG.md"
    if not cl.exists():
        return []
    heads = [ln for ln in cl.read_text().splitlines() if ln.startswith("## [")]
    return heads[-n:]


def _os_version() -> str:
    f = ROOT / ".ai" / "os_version.json"
    if f.exists():
        try:
            return json.loads(f.read_text()).get("version", "?")
        except Exception:
            pass
    return "?"


def main():
    phase = _phase()
    pending = _pending()
    recent = _recent_changelog()
    print("```markdown")
    print(f"# Session Briefing — {ROOT.name} (God Mode AI OS v{_os_version()})")
    print()
    print("You are operating in a God Mode AI OS workspace. Follow `AGENTS.md` "
          "(canonical protocol). Key rules:")
    print("- Read `.ai/memory-bank/activeContext.md` + `progress.md` before acting.")
    print("- Classify blast radius; `[Destructive/Dependency]` needs my approval.")
    print("- Append to `AI_CHANGELOG.md` after any file change.")
    print("- Pass the validation gate (`just verify-safe` → evidence.json) before side effects.")
    print("- TickTick: never auto-sync — show Pending tasks, ask which + when to remind, "
          "then `just tt-sync \"<ids>\" \"<when>\"`.")
    print()
    print(f"**Current phase:** {phase}")
    print()
    if pending:
        print("**Pending tasks:**")
        for t in pending:
            print(f"- {t}")
    else:
        print("**Pending tasks:** none recorded.")
    print()
    if recent:
        print("**Recent changes:**")
        for r in recent:
            print(f"- {r.lstrip('# ').strip()}")
    print("```")


if __name__ == "__main__":
    main()
