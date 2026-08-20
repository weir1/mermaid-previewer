#!/usr/bin/env python3
"""
rotate_changelog.py — God Mode AI OS Changelog Rotator
=======================================================
Keeps AI_CHANGELOG.md lean by:
  1. Archiving entries older than KEEP_DAYS to AI_CHANGELOG_archive/YYYY-MM.md
  2. Rewriting AI_CHANGELOG.md with only recent entries
  3. Writing/updating a pinned SUMMARY BLOCK at the top of AI_CHANGELOG.md
     (last 5 sessions, compact table — always readable even by models that skim)

Usage:
  python3 scripts/rotate_changelog.py
  just rotate-changelog

Called automatically by session_end.py at end of every session.
"""

import re
from datetime import datetime, timezone, timedelta
from pathlib import Path

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


ROOT = _ws_root()
CHANGELOG = ROOT / "AI_CHANGELOG.md"
ARCHIVE_DIR = ROOT / "AI_CHANGELOG_archive"
KEEP_DAYS = 30          # entries older than this get archived
MAX_SUMMARY_ROWS = 5    # rows in the pinned summary block

SUMMARY_START = "<!-- GODMODE:SUMMARY:START -->"
SUMMARY_END   = "<!-- GODMODE:SUMMARY:END -->"


def parse_entries(text: str) -> list[dict]:
    """Split changelog text into list of {header, date_str, body, full} dicts."""
    # Strip the summary block if present
    text = re.sub(
        rf"{re.escape(SUMMARY_START)}.*?{re.escape(SUMMARY_END)}\n*",
        "",
        text,
        flags=re.S
    )
    entries = []
    # Each entry starts with ## [YYYY-MM-DD ...] or ## [YYYY-MM-DD HH:MM ...]
    pattern = re.compile(r"^## \[(\d{4}-\d{2}-\d{2}[^\]]*)\]", re.MULTILINE)
    matches = list(pattern.finditer(text))
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        full = text[start:end].strip()
        date_str = m.group(1).strip()
        # Parse date — strip time/timezone suffix
        date_clean = re.sub(r"\s+\d{2}:\d{2}.*$", "", date_str).strip()
        try:
            date = datetime.strptime(date_clean, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            date = datetime.now(timezone.utc)  # fallback
        # Extract one-line summary from Files Changed or first content line
        body_lines = [l for l in full.splitlines()[1:] if l.strip()]
        summary = ""
        for line in body_lines:
            if line.startswith("**Summary:**"):
                summary = line.replace("**Summary:**", "").strip()
                break
            elif line.startswith("**Files Changed:**"):
                summary = line.replace("**Files Changed:**", "").strip()
                break
        if not summary and body_lines:
            summary = body_lines[0][:80]
        entries.append({
            "header": m.group(0),
            "date_str": date_str,
            "date": date,
            "body": full,
            "summary": summary[:80],
        })
    return entries


def build_summary_block(recent_entries: list[dict]) -> str:
    """Build the pinned summary table for the top of AI_CHANGELOG.md."""
    rows = []
    for e in recent_entries[-MAX_SUMMARY_ROWS:]:
        date = e["date_str"][:10]
        summary = e["summary"].replace("|", "\\|")
        rows.append(f"| {date} | {summary} |")
    table = "\n".join(rows) if rows else "| — | No recent entries |"
    return (
        f"{SUMMARY_START}\n"
        f"> **AI QUICK READ** — Last {MAX_SUMMARY_ROWS} sessions (auto-updated). "
        f"Full history below or in `AI_CHANGELOG_archive/`.\n\n"
        f"| Date | Summary |\n"
        f"|------|---------|\n"
        f"{table}\n"
        f"{SUMMARY_END}\n"
    )


def archive_entries(old_entries: list[dict]):
    """Append old entries to monthly archive files."""
    ARCHIVE_DIR.mkdir(exist_ok=True)
    by_month: dict[str, list[str]] = {}
    for e in old_entries:
        month_key = e["date"].strftime("%Y-%m")
        by_month.setdefault(month_key, []).append(e["body"])
    for month, bodies in by_month.items():
        archive_file = ARCHIVE_DIR / f"{month}.md"
        # Read existing archive to avoid duplicates
        existing = archive_file.read_text(encoding="utf-8") if archive_file.exists() else ""
        new_content = ""
        for body in bodies:
            if body[:60] not in existing:  # rough dedup check
                new_content += body + "\n\n"
        if new_content:
            with archive_file.open("a", encoding="utf-8") as f:
                f.write(new_content)
            print(f"  📦 Archived {len(bodies)} entr(ies) to {archive_file.name}")


def rotate():
    if not CHANGELOG.exists():
        print("  ℹ️  AI_CHANGELOG.md not found — nothing to rotate.")
        return

    text = CHANGELOG.read_text(encoding="utf-8")
    entries = parse_entries(text)

    if not entries:
        print("  ℹ️  No dated entries found in AI_CHANGELOG.md.")
        return

    cutoff = datetime.now(timezone.utc) - timedelta(days=KEEP_DAYS)
    recent = [e for e in entries if e["date"] >= cutoff]
    old    = [e for e in entries if e["date"] <  cutoff]

    if old:
        archive_entries(old)
        print(f"  🗂️  {len(old)} old entr(ies) archived ({KEEP_DAYS}+ days old)")
    else:
        print(f"  ✅ No entries older than {KEEP_DAYS} days — no archiving needed")

    # Rebuild AI_CHANGELOG.md: summary block + recent entries only
    summary_block = build_summary_block(recent)
    body = "\n\n".join(e["body"] for e in recent)
    new_content = f"# AI Changelog\n\n{summary_block}\n\n---\n\n{body}\n"
    CHANGELOG.write_text(new_content, encoding="utf-8")

    size_kb = CHANGELOG.stat().st_size / 1024
    print(f"  ✅ AI_CHANGELOG.md rewritten — {len(recent)} recent entries, {size_kb:.1f}KB")
    print(f"  📌 Pinned summary block updated (last {min(MAX_SUMMARY_ROWS, len(recent))} sessions)")


if __name__ == "__main__":
    print("\n── AI_CHANGELOG Rotation")
    rotate()
    print()
