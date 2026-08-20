#!/usr/bin/env python3
"""
freshen_memory.py — Reset last_verified timestamps in all memory bank files.

Usage: python3 scripts/freshen_memory.py
       Or: just freshen-memory

Resets last_verified to now (UTC) and sets freshness: fresh in frontmatter.
Run this after updating memory bank content to reset the TTL clock.
"""

from pathlib import Path
from datetime import datetime, timezone
import re

def _ws_root() -> Path:
    """Resolve workspace root cwd-safely (git toplevel; fallback to cwd)."""
    import subprocess
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:
        pass
    return Path(".").resolve()


ROOT = _ws_root() / ".ai" / "memory-bank"
now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text, None
    block = m.group(1)
    meta = {}
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():], m


def freshen(path: Path):
    text = path.read_text(encoding="utf-8")
    meta, body, match = parse_frontmatter(text)
    if match is None:
        print(f"  ⚠️  [{path.name}] No frontmatter found — skipping")
        return

    meta["last_verified"] = now_iso
    meta["freshness"] = "fresh"
    meta["age_days"] = "0"
    new_frontmatter = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n"
    path.write_text(new_frontmatter + body, encoding="utf-8")
    print(f"  ✅ [{path.name}] last_verified reset to {now_iso}")


if __name__ == "__main__":
    print(f"Freshening all memory bank files...")
    for path in ROOT.glob("*.md"):
        if path.name == "archive":
            continue
        freshen(path)
    print(f"\nDone. Run 'just memory-decay' to verify freshness.")
