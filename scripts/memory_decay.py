#!/usr/bin/env python3
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
now = datetime.now(timezone.utc)

def parse_frontmatter(text):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    block = m.group(1)
    meta = {}
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]

for path in ROOT.glob("*.md"):
    if path.name == "archive":
        continue
    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)
    ttl = int(meta.get("ttl_days", "7"))
    last = meta.get("last_verified")
    if not last:
        continue
    try:
        dt = datetime.fromisoformat(last.replace("Z", "+00:00"))
    except Exception:
        continue
    age_days = (now - dt).days
    stale = age_days > ttl
    meta["freshness"] = "stale" if stale else "fresh"
    meta["age_days"] = str(age_days)
    new_frontmatter = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n"
    path.write_text(new_frontmatter + body, encoding="utf-8")
    print(f"[{path.name}] Freshness: {meta['freshness']} (Age: {age_days} days)")
