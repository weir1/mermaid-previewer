#!/usr/bin/env python3
"""context_status.py — PH14-T03: what has this session actually done, on demand.

`just tokens` (PHASE 25) measures the boot set — memory bank + handover, the
cost of *starting* a session. Nothing measured what a session does *after*
that: how many tool calls, how many distinct files, how long it has been
running, and whether reads lean whole-file or ranged. Those are the
"observable proxies" PH14-T03 names, and this is the manual-check half the
operator chose over a hook that auto-injects a nudge on every prompt (see
`.ai/prework/PH14-T03.md`) — read-only, run whenever you want the numbers
instead of a hunch.

## Where the numbers come from

Claude Code writes this session's own transcript to
`~/.claude/projects/<slugified-cwd>/<CLAUDE_CODE_SESSION_ID>.jsonl` — one JSON
record per turn. This module reads *that*, not any new hook or log this
workspace invented. That is a deliberate, load-bearing scope limit:

  * **Claude Code only.** The path and the slug rule are undocumented,
    harness-owned conventions, observed empirically, not published. A
    session running under Gemini/Antigravity (no `CLAUDE_CODE_SESSION_ID`)
    or a harness that changes its layout degrades to "not available" —
    never a guess dressed as a number.
  * **What it cannot see, it says so.** Live context-window percentage and
    prompt-cache pinning are not recoverable from a transcript file and are
    never claimed here — the DoD's own line: "a rule nothing checks is a
    TODO, not a feature."
  * **Advisory only.** The `HANDOVER_HINTS` thresholds are unmeasured
    starting points, not a gate — nothing here blocks a read, nothing here
    forces a handover. Retune once real sessions have run against them.

`parse_transcript()` is a pure function over text, independent of the file
path resolution — so tests exercise it against a synthetic transcript and
never touch a real one (test-first: isolation is part of correctness).
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Tools whose `input.file_path` counts as "a file this session touched".
FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}

# Advisory only, printed as a suggestion — see the module docstring. Starting
# points, not calibrated against real session outcomes yet.
HANDOVER_HINTS = {
    "tool_calls": 150,
    "distinct_files": 40,
    "elapsed_minutes": 90,
}


def transcripts_root(home: Path | None = None) -> Path:
    return (home or Path.home()) / ".claude" / "projects"


def slugify_cwd(cwd: str) -> str:
    """Best-effort mirror of Claude Code's own project-directory slug: every
    `/` — including the leading one — becomes `-`. If the real harness ever
    slugs differently, `locate_transcript` degrades to "not found" rather
    than reading the wrong session's transcript."""
    return cwd.replace("/", "-")


def locate_transcript(session_id: str, cwd: str, root: Path | None = None) -> Path | None:
    path = (root or transcripts_root()) / slugify_cwd(cwd) / f"{session_id}.jsonl"
    return path if path.is_file() else None


def _parse_ts(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def parse_transcript(text: str) -> dict:
    """JSONL transcript text -> activity stats. No file I/O, no env lookup —
    the seam tests patch."""
    tool_calls = 0
    by_tool: dict[str, int] = {}
    files: set[str] = set()
    reads_total = reads_ranged = 0
    timestamps: list[str] = []

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue  # a truncated last line (mid-write) must not abort the whole read
        if not isinstance(rec, dict) or rec.get("type") != "assistant":
            continue
        ts = rec.get("timestamp")
        if isinstance(ts, str):
            timestamps.append(ts)
        msg = rec.get("message")
        content = msg.get("content") if isinstance(msg, dict) else None
        if not isinstance(content, list):
            continue
        for block in content:
            if not isinstance(block, dict) or block.get("type") != "tool_use":
                continue
            name = block.get("name") or "?"
            tool_calls += 1
            by_tool[name] = by_tool.get(name, 0) + 1
            inp = block.get("input") if isinstance(block.get("input"), dict) else {}
            if name in FILE_TOOLS:
                fp = inp.get("file_path")
                if isinstance(fp, str) and fp:
                    files.add(fp)
            if name == "Read":
                reads_total += 1
                if "offset" in inp or "limit" in inp:
                    reads_ranged += 1

    elapsed_minutes = None
    if len(timestamps) >= 2:
        try:
            first, last = _parse_ts(min(timestamps)), _parse_ts(max(timestamps))
            elapsed_minutes = round((last - first).total_seconds() / 60, 1)
        except ValueError:
            elapsed_minutes = None  # malformed timestamp: unknown, not zero

    return {
        "readable": True,
        "tool_calls": tool_calls,
        "by_tool": by_tool,
        "distinct_files": len(files),
        "reads_total": reads_total,
        "reads_ranged": reads_ranged,
        "reads_whole_file": reads_total - reads_ranged,
        "elapsed_minutes": elapsed_minutes,
    }


def collect(session_id: str | None, cwd: str, root: Path | None = None) -> dict:
    """The wrapper that touches the real world; `parse_transcript` does not."""
    if not session_id:
        return {"readable": False, "reason": "no CLAUDE_CODE_SESSION_ID set — not running "
                "under Claude Code, or this harness does not expose one"}
    path = locate_transcript(session_id, cwd, root=root)
    if path is None:
        return {"readable": False,
                "reason": f"no transcript found for session {session_id} under {cwd}"}
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return {"readable": False, "reason": f"transcript unreadable: {exc}"}
    stats = parse_transcript(text)
    stats["source"] = str(path)
    return stats


def hints(stats: dict) -> list[str]:
    """Advisory strings only — see HANDOVER_HINTS. Empty when nothing crossed,
    including when the stats are not readable."""
    if not stats.get("readable"):
        return []
    out = []
    if stats["tool_calls"] >= HANDOVER_HINTS["tool_calls"]:
        out.append(f"tool calls ({stats['tool_calls']}) past the "
                   f"{HANDOVER_HINTS['tool_calls']} heuristic")
    if stats["distinct_files"] >= HANDOVER_HINTS["distinct_files"]:
        out.append(f"distinct files touched ({stats['distinct_files']}) past the "
                   f"{HANDOVER_HINTS['distinct_files']} heuristic")
    em = stats.get("elapsed_minutes")
    if em is not None and em >= HANDOVER_HINTS["elapsed_minutes"]:
        out.append(f"elapsed ({em:.0f}m) past the {HANDOVER_HINTS['elapsed_minutes']}m heuristic")
    return out


def render(stats: dict) -> str:
    if not stats.get("readable"):
        return ("📊 SESSION ACTIVITY — not available: " + stats.get("reason", "unknown") + "\n"
                "   (Claude Code only — needs CLAUDE_CODE_SESSION_ID and its transcript file.)\n")

    lines = ["📊 SESSION ACTIVITY (this session's own transcript — Claude Code only)"]
    tool_bits = ", ".join(f"{n} {c}" for n, c in
                          sorted(stats["by_tool"].items(), key=lambda kv: -kv[1]))
    lines.append(f"   tool calls: {stats['tool_calls']}" + (f" ({tool_bits})" if tool_bits else ""))
    lines.append(f"   distinct files touched: {stats['distinct_files']}")
    em = stats["elapsed_minutes"]
    lines.append(f"   elapsed: {em:.0f}m" if em is not None
                else "   elapsed: unknown (fewer than two timestamped turns so far)")
    if stats["reads_total"]:
        pct = round(100 * stats["reads_whole_file"] / stats["reads_total"])
        lines.append(f"   reads: {stats['reads_total']} total — {stats['reads_ranged']} ranged, "
                      f"{stats['reads_whole_file']} whole-file ({pct}% whole-file)")
    h = hints(stats)
    if h:
        lines.append("   ⚠️  " + "; ".join(h) + " — consider `just handover \"<next step>\"`.")
    lines.append("   NOT available from here: live context %, prompt-cache pinning — "
                  "harness-side, not claimed as a feature (PH14-T03).")
    return "\n".join(lines) + "\n"


def main() -> int:
    stats = collect(os.environ.get("CLAUDE_CODE_SESSION_ID"), os.getcwd())
    sys.stdout.write(render(stats))
    return 0  # advisory-only: "not available" is honest output, not a failure


if __name__ == "__main__":
    sys.exit(main())
