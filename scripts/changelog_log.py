#!/usr/bin/env python3
"""
changelog_log.py — `just log "<summary>"` stamps AI_CHANGELOG.md (PH27-T07).

AGENTS.md's CHANGELOG ENFORCEMENT rule fires after every file change, "no
exceptions" — and every entry has always been hand-typed: the IST timestamp
converted from the system clock, and the changed-file list enumerated by
hand. Both are the error-prone, low-value half of the entry; the summary
prose is the half that actually needs a person (or an agent) to write it.

This derives the other two from the tree itself and appends one conforming
entry, so neither is retyped:

  * the header — `## [YYYY-MM-DD HH:MM IST]`, from the system clock;
  * `**Files Changed:**` — every path `git status --porcelain` reports
    (staged, unstaged, untracked alike), captured BEFORE the append so the
    changelog file's own edit is never listed as a change to itself, the
    same omission every hand-written entry in this file already makes.

The summary is still the caller's: deriving prose from a diff is a
different, much larger problem this script does not attempt.

Usage: changelog_log.py "what changed and why"
"""

import argparse
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))
CHANGELOG = "AI_CHANGELOG.md"


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                       stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def changed_paths(root: Path) -> list[str]:
    """Every path `git status --porcelain` reports, deduped and sorted.

    A rename (`R  old -> new`) keeps only the new path — the name a person
    would actually write down. Empty when the tree is clean, which is a
    real, honest answer (nothing to log), not an error.
    """
    try:
        out = subprocess.check_output(["git", "status", "--porcelain"],
                                       cwd=root, stderr=subprocess.DEVNULL, text=True)
    except Exception:  # noqa: BLE001
        return []
    paths: set[str] = set()
    for line in out.splitlines():
        if len(line) < 4:
            continue
        rest = line[3:]
        if " -> " in rest:
            rest = rest.split(" -> ", 1)[1]
        paths.add(rest.strip())
    return sorted(paths)


def render_entry(summary: str, paths: list[str], when: datetime | None = None) -> str:
    """The conforming block — same shape every hand-written entry uses."""
    when = when or datetime.now(IST)
    header = when.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
    files = ", ".join(paths) if paths else "(none — working tree clean)"
    return f"## [{header}]\n**Files Changed:** {files}\n**Summary:** {summary}\n"


def append_entry(root: Path, summary: str, when: datetime | None = None) -> tuple[Path, list[str]]:
    """Append one entry to `AI_CHANGELOG.md`, creating it if absent.

    Paths are read BEFORE the write below, so the changelog's own edit is
    never listed as one of its changed files.
    """
    paths = changed_paths(root)
    entry = render_entry(summary, paths, when=when)
    path = root / CHANGELOG
    text = path.read_text(encoding="utf-8") if path.exists() else "# AI Changelog\n\n"
    text = text.rstrip("\n") + "\n\n" + entry
    path.write_text(text, encoding="utf-8")
    return path, paths


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.strip().splitlines()[0])
    ap.add_argument("summary", help="what changed and why — the prose half of the entry")
    args = ap.parse_args()

    summary = args.summary.strip()
    if not summary:
        print("❌ a summary is required: just log \"what changed and why\"")
        return 2

    root = ws_root()
    path, paths = append_entry(root, summary)
    print(f"✅ appended to {path.relative_to(root)}")
    print(f"   {len(paths)} changed path(s)" if paths else
          "   ⚠️  working tree was clean — no paths recorded")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
