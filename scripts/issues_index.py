#!/usr/bin/env python3
"""issues_index.py — a per-issue index for knownIssues.md, `INDEX.md`/`codemap.md`'s
retrieval trick applied one level deeper (PH27-T13).

## Why this exists
`.ai/memory-bank/INDEX.md` already points at `knownIssues.md` — but at WHOLE-FILE
granularity (`knownIssues.md#active-issues`). Consulting it for one specific issue
still means opening all of it: 133,270 bytes / 313 lines / 66 open + 2 resolved
entries, measured 2026-08-19. This generates one compact line per entry — date,
status, a short title, and a `path:line` pointer — so an agent scans ~70 short
lines instead, and opens the full file only for the entries that actually matter.

## What this does NOT do
`knownIssues.md` itself is never touched: no entry moves, no open issue is hidden,
trimmed, or reordered. A 2026-08-13 task already established that archiving open
issues out of the hot file is the wrong move (they are live state, not history,
and `archive_memory.protected_lines()`-style guarantees exist for exactly this
reason) — this is a new READ path, not a rewrite of that guarantee.

## Why `path:line`, not a `[[wiki link]]`
The brief that requested this described a `[[knownIssues.md#anchor]]`-style
pointer. `check_links.py.anchors_of()` only recognises HEADING anchors (a
slugified `## Heading`) — `knownIssues.md`'s entries are bullets, not headings,
so a synthesized per-entry anchor would never resolve and `just doctor` would
carry a permanently broken link. `path:line` is the convention this repo already
uses for exact locations (`symbol_slice.py`, code review references) and it
survives every edit to the file without needing a slug scheme invented for it.

## Staleness
One hash of the whole source file, stored in the generated index's own header —
simpler than `codemap.py`'s per-row hash because there is one source file, not
many. `check()` mirrors `codemap.check()`'s shape (`exists`/`stale`/`reason`) so
`doctor.py` wires it in the same way, absent-is-WARN / stale-is-FAIL, for the
same reason: a red doctor in every workspace that has not opted in trains the
operator to stop reading doctor at all.

## Parsing, and what "never drop an entry" costs
A top-level bullet (indent 0, starts with `- `) is one entry. Three shapes exist
in the real file, tried in order — the fallback exists so an entry with no icon
or no date (most of `## Known Limitations`, several `## Active Issues` audit
findings) still gets indexed rather than silently omitted:
  1. `- <icon> [DATE ...] **title**: ...`          (the common, dated shape)
  2. `- <icon> **title**` / `- <icon> **RESOLVED ...** [DATE] **title**`
  3. `- **title** ...` / `- plain prose ...`        (no icon, sometimes no bold)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

SOURCE_PATH = Path(".ai") / "memory-bank" / "knownIssues.md"
INDEX_PATH = Path(".ai") / "memory-bank" / "knownIssues-index.md"
HASH_LEN = 12

_DATED_RE = re.compile(
    r"^-\s+(?P<icon>\S+)\s+"
    r"(?:\*\*RESOLVED\s+\d{4}-\d{2}-\d{2}[^*]*\*\*\s+)?"
    r"\[(?P<date>\d{4}-\d{2}-\d{2})[^\]]*\]\s+"
    r"\*\*(?P<title>[^*]+)\*\*"
)
# `\S+` rather than an explicit emoji range: ⚠️ is two codepoints (WARNING SIGN
# + VARIATION SELECTOR-16), and a single-character class would match the first
# and then fail on the second where `_DATED_RE`'s own `\S+` does not. A bare
# `**title**` line (no icon token) still falls through safely to
# `_TITLE_ONLY_RE` — `\S+` cannot cross the space inside a multi-word title to
# reach the closing `**`, so it never mis-claims an icon that isn't there.
_ICON_TITLE_RE = re.compile(
    r"^-\s+(?P<icon>\S+)\s+"
    r"(?:\*\*RESOLVED\s+\d{4}-\d{2}-\d{2}[^*]*\*\*\s+)?"
    r"\*\*(?P<title>[^*]+)\*\*"
)
_TITLE_ONLY_RE = re.compile(r"^-\s+\*\*(?P<title>[^*]+)\*\*")


def _digest(text: str) -> str:
    body = "\n".join(line.rstrip() for line in text.splitlines())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:HASH_LEN]


def _clean(title: str) -> str:
    """Strip stray markdown emphasis a regex left behind — a title truncated by
    an internal `*italic*` span (`_TITLE_ONLY_RE`'s `[^*]+` stops at the first
    `*`, so "**title** ... *record* ..." never reaches its own closing `**`)
    or the raw shape-3b fallback still carries its leading `**`/backticks."""
    return title.replace("**", "").strip(" *")


def _entry(line: str, line_no: int) -> dict:
    for rx in (_DATED_RE, _ICON_TITLE_RE, _TITLE_ONLY_RE):
        m = rx.match(line)
        if m:
            g = m.groupdict()
            return {"line": line_no, "date": g.get("date", ""),
                     "icon": g.get("icon", ""), "title": _clean(g["title"])}
    # Shape 3b: no bold title at all — first ~90 chars of the stripped bullet,
    # so the entry is still indexed rather than silently dropped.
    text = line[1:].strip()
    title = (text[:90] + "…") if len(text) > 90 else text
    return {"line": line_no, "date": "", "icon": "", "title": _clean(title)}


def scan(root: Path | str = ".") -> dict:
    """Every top-level bullet in `knownIssues.md`, as index rows. Pure read."""
    root = Path(root)
    path = root / SOURCE_PATH
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    entries = []
    section = ""
    for i, line in enumerate(lines):
        if line.startswith("#"):
            section = line.lstrip("#").strip()
            continue
        if not line.startswith("- "):
            continue
        e = _entry(line, i + 1)
        e["section"] = section
        entries.append(e)
    return {"entries": entries, "source_hash": _digest(text), "source_lines": len(lines)}


# ── Rendering ────────────────────────────────────────────────────────────────

def render(data: dict) -> str:
    """The index. Carries the source hash in its own header so `check()` needs
    no second file — the same reason `codemap.md` embeds a hash per row."""
    lines = [
        "# knownIssues.md — per-issue index",
        "",
        f"<!-- source-hash: {data['source_hash']} -->",
        "> **Generated by `just issues-index`. Do not hand-edit.** One line per entry in",
        "> `.ai/memory-bank/knownIssues.md` — read this first, then open the full file only",
        "> at the `path:line` for the entries that actually matter. `just doctor` fails when",
        "> this drifts from the real file. Nothing in `knownIssues.md` moves or is trimmed —",
        "> this is a new read path, not a rewrite of the never-move-an-open-issue guarantee.",
        "",
        f"{len(data['entries'])} entries indexed, from a {data['source_lines']}-line source.",
        "",
        "| date | status | title | source |",
        "|---|---|---|---|",
    ]
    for e in data["entries"]:
        title = e["title"].replace("|", "\\|")
        status = e["icon"] or ("✅" if "RESOLVED" in e["title"] else "")
        date = e["date"] or "—"
        lines.append(f"| {date} | {status} | {title} | "
                      f"`{SOURCE_PATH}:{e['line']}` |")
    lines.append("")
    return "\n".join(lines)


def write(root: Path | str = ".") -> Path:
    root = Path(root)
    path = root / INDEX_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(scan(root)), encoding="utf-8")
    return path


# ── Checking ─────────────────────────────────────────────────────────────────

_HASH_COMMENT_RE = re.compile(r"<!--\s*source-hash:\s*([0-9a-f]{6,})\s*-->")


def recorded_hash(root: Path | str = ".") -> str | None:
    path = Path(root) / INDEX_PATH
    if not path.is_file():
        return None
    m = _HASH_COMMENT_RE.search(path.read_text(encoding="utf-8", errors="replace"))
    return m.group(1) if m else None


def check(root: Path | str = ".") -> dict:
    """Does the index still match `knownIssues.md`? Pure read — never writes."""
    root = Path(root)
    exists = (root / INDEX_PATH).is_file()
    if not exists:
        return {"exists": False, "stale": False,
                "reason": f"{INDEX_PATH} does not exist — run `just issues-index`"}
    was = recorded_hash(root)
    now = _digest((root / SOURCE_PATH).read_text(encoding="utf-8", errors="replace"))
    stale = was != now
    return {"exists": True, "stale": stale,
            "reason": (f"{SOURCE_PATH} changed since the index was built "
                       f"→ run `just issues-index`") if stale
                      else f"{INDEX_PATH} matches {SOURCE_PATH}"}


# ── CLI ──────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Generate or verify the per-issue index of knownIssues.md.")
    ap.add_argument("--write", action="store_true", help="regenerate the index (default)")
    ap.add_argument("--check", action="store_true",
                     help="verify only; exit 1 when the index is stale or absent")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--root", default=None, help="workspace root (default: cwd)")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else Path(".")

    if args.check:
        result = check(root)
        if args.json:
            print(json.dumps(result, indent=2))
        else:
            print(("✅ " if not result["stale"] and result["exists"] else
                    "⚠️  " if not result["exists"] else "❌ ") + result["reason"])
        return 1 if result["stale"] else 0

    path = write(root)
    data = scan(root)
    if args.json:
        print(json.dumps({"path": str(path), "count": len(data["entries"])}, indent=2))
    else:
        print(f"✅ {INDEX_PATH} — {len(data['entries'])} entries indexed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
