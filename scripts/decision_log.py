#!/usr/bin/env python3
"""
decision_log.py — the ONE writer (and reader) for `.ai/decision-log/YYYY-MM-DD.jsonl`.

Audit finding **F-16: the decision log is dead.** `.ai/decision-log/` held a single
hand-written file from 2026-07-10; nothing in the OS ever appended to it, so every
claim about autonomy, approvals and blocked actions was narrated rather than
measured. `just audit` (PH5-T03) had no data to read and stayed blocked.

This module closes that: every *verdict* the OS reaches — the policy engine's
allow/ask/deny on a tool call, and the validation gate's open/blocked on a real side
effect — is appended here as one JSON object per line.

## Design rules (why this is a module and not three copy-pastes)
1. **Never raise, never block.** This is called from a `PreToolUse` hook that runs
   before every Edit/Write/Bash. A logging bug must not brick a session, so every
   entry point is wrapped and returns a bool instead of throwing.
2. **One schema.** `record()` is the only constructor; callers pass fields, not
   dicts, so the log stays queryable. Unknown/empty fields are dropped, not written
   as nulls, which keeps lines short and greppable.
3. **Append-only, one line per verdict.** Written with a single `O_APPEND` write so
   concurrent hook processes interleave lines rather than corrupting them.
4. **The log is bookkeeping, not evidence.** `gate_check.BOOKKEEPING` already
   excludes `.ai/decision-log/` from the freshness computation — writing here can
   never invalidate the gate it is recording. Keep it that way.

## Schema (v1)
    ts            ISO-8601 UTC, whole seconds
    kind          "policy" | "gate" | "override"
    decision      policy: allow|ask|deny · gate: open|blocked · override: gate_override
    source        the script that reached the verdict (policy_hook, gate_check, …)
    tool          Claude Code tool name, when the verdict came from a hook
    op            read|write|create|delete
    path          workspace-relative target
    rule          policy rule that matched (e.g. autonomous_allowed.filesystem)
    pattern       the glob inside that rule
    blast_radius  Docs Only | Safe Refactor | Destructive/Dependency
    action        human label for the gated side effect ("git push", "TickTick sync")
    task          active PH#-T## at the time, when one is in progress
    reason        why — truncated to keep lines small
    session       session_start timestamp from .ai/session-state.json (groups a run)
    model         PH10-T05: the self-reported running model at the moment this entry was
                  written (`model_registry.resolve_running()["name"]`) — auto-filled by
                  `record()`, never presented as verified (see that module's own docstring
                  on why detection is impossible here). Entries written before this field
                  existed simply lack it; readers report that as "unrecorded", never guessed.

Pre-existing 2026-07-10 lines use an older ad-hoc shape (`policy_matched`,
`autonomous`). Readers here tolerate both; `summarize()` counts only what it
understands rather than guessing.

Usage:
  decision_log.py --tail 20            # last N entries, human-readable
  decision_log.py --summary            # counts by kind/decision (what `just audit` will build on)
  decision_log.py --summary --days 7   # ...over the last 7 log files
  decision_log.py --tail 20 --json     # machine-readable

Kill switch: set GODMODE_DECISION_LOG=0 to disable writing (reads still work).
"""

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

SCHEMA_VERSION = 1
LOG_DIRNAME = Path(".ai") / "decision-log"
# Keep lines small: a hook fires on every tool call, and an unbounded `reason`
# (or a pasted heredoc in a Bash command) would turn the log into a liability.
MAX_FIELD = 400
# Canonical key order — stable output makes the file diff-able and grep-able.
FIELD_ORDER = ("ts", "kind", "decision", "source", "tool", "op", "path", "rule",
               "pattern", "blast_radius", "action", "task", "reason", "session", "model")

KINDS = ("policy", "gate", "override", "attribution", "finding")
_TASK_RE = re.compile(r"PH\d+-T\d+")


def enabled() -> bool:
    return os.environ.get("GODMODE_DECISION_LOG", "1").strip().lower() not in (
        "0", "off", "false", "no")


def ws_root() -> Path:
    """Workspace root, resolved the way AGENTS.md's anti-drift rule demands:
    the harness project dir, then the git top-level, then cwd — never the open file."""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root).resolve()
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def log_path(root: Path, when: datetime | None = None) -> Path:
    when = when or datetime.now(timezone.utc)
    return root / LOG_DIRNAME / f"{when:%Y-%m-%d}.jsonl"


def active_task(root: Path) -> str:
    """The task declared `(In Progress)` in activeContext.md.

    Delegates to `task_ledger.active_task` so a decision entry, the evidence it
    gates, and the gate itself genuinely agree on the task — this docstring used
    to *claim* they shared a rule while each kept its own copy, and all three
    copies matched prose that merely quoted the marker. Never raises (design
    rule 1); an unresolvable task is simply empty."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import task_ledger
        return task_ledger.active_task(root)
    except Exception:  # noqa: BLE001
        return ""


def session_key(root: Path) -> str:
    """The current session's start timestamp — the natural grouping key for
    `just audit`'s per-session summary. Empty when no session has been started."""
    try:
        state = json.loads((root / ".ai" / "session-state.json").read_text())
        return str(state.get("session_start", "") or "")
    except Exception:  # noqa: BLE001
        return ""


def declared_model(root: Path) -> str:
    """PH10-T05: the self-reported running model at THIS moment
    (`model_registry.resolve_running()["name"]`) — the same call
    `leash.check_push()`/`delegate_review.py` already make, reused rather
    than a second lookup. Never raises (design rule 1); a missing/broken
    registry drops the field entirely (see `build_entry`), which is
    distinct from — and never confused with — the real, resolvable
    `"unknown"` name a registry legitimately returns for an undeclared
    session."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import model_registry
        return model_registry.resolve_running(root)["name"]
    except Exception:  # noqa: BLE001
        return ""


def _clip(value) -> str:
    text = str(value).replace("\n", " ").strip()
    return text if len(text) <= MAX_FIELD else text[: MAX_FIELD - 1] + "…"


def build_entry(kind: str, decision: str, source: str, **fields) -> dict:
    """Construct a canonical entry. Empty fields are dropped, not written as null."""
    entry = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": kind,
        "decision": decision,
        "source": source,
    }
    for key in FIELD_ORDER:
        if key in entry:
            continue
        value = fields.get(key)
        if value in (None, "", []):
            continue
        entry[key] = _clip(value)
    entry["v"] = SCHEMA_VERSION
    return entry


def append(entry: dict, root: Path | None = None) -> bool:
    """Append one entry. Returns True if written. NEVER raises — see design rule 1."""
    if not enabled():
        return False
    try:
        root = root or ws_root()
        path = log_path(root)
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        # Single O_APPEND write: concurrent hook processes interleave whole lines
        # instead of corrupting each other's.
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
        return True
    except Exception:  # noqa: BLE001
        return False


def record(kind: str, decision: str, source: str, root: Path | None = None,
           with_context: bool = True, **fields) -> bool:
    """Build + append in one call. `with_context` fills task/session from the
    workspace unless the caller already supplied them."""
    try:
        if not enabled():
            return False
        root = root or ws_root()
        if with_context:
            fields.setdefault("task", active_task(root))
            fields.setdefault("session", session_key(root))
            fields.setdefault("model", declared_model(root))
        return append(build_entry(kind, decision, source, **fields), root)
    except Exception:  # noqa: BLE001
        return False


# ── Reading (the substrate `just audit` / PH5-T03 will build on) ───────────
def read_entries(root: Path | None = None, days: int | None = None) -> list[dict]:
    """Entries from the log, oldest first. `days=N` reads only the last N dates."""
    root = root or ws_root()
    directory = root / LOG_DIRNAME
    if not directory.is_dir():
        return []
    files = sorted(p for p in directory.glob("*.jsonl") if p.is_file())
    if days:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        files = [p for p in files if p.stem >= cutoff]
    entries: list[dict] = []
    for path in files:
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue          # a truncated line must not poison the whole read
            if isinstance(obj, dict):
                obj.setdefault("_file", path.name)
                entries.append(obj)
    return entries


def summarize(entries: list[dict]) -> dict:
    """Counts the raw material for PH5-T03: autonomy rate, approvals, blocks, gate stops.

    Legacy 2026-07-10 lines have no `kind`; they are counted under `legacy` rather
    than silently reshaped into the new schema.
    """
    out = {
        "total": len(entries),
        "by_kind": {},
        "by_decision": {},
        "policy": {"allow": 0, "ask": 0, "deny": 0},
        "gate": {"open": 0, "blocked": 0},
        "overrides": 0,
        "legacy": 0,
        "autonomy_rate": None,
        "gate_pass_rate": None,
        "sessions": [],
    }
    sessions: list[str] = []
    for e in entries:
        kind = e.get("kind")
        decision = e.get("decision", "")
        if not kind:
            out["legacy"] += 1
            continue
        out["by_kind"][kind] = out["by_kind"].get(kind, 0) + 1
        if decision:
            out["by_decision"][decision] = out["by_decision"].get(decision, 0) + 1
        if kind == "policy" and decision in out["policy"]:
            out["policy"][decision] += 1
        elif kind == "gate" and decision in out["gate"]:
            out["gate"][decision] += 1
        elif kind == "override":
            out["overrides"] += 1
        s = e.get("session")
        if s and s not in sessions:
            sessions.append(s)
    graded = sum(out["policy"].values())
    if graded:
        out["autonomy_rate"] = round(out["policy"]["allow"] / graded, 3)
    # PH6-T20: the validation-gate pass rate. `out["gate"]` only ever gains entries
    # whose decision is "open"/"blocked" (the loop above only increments it when
    # `decision in out["gate"]`) — kind="gate" is overloaded (leash and
    # delegate_review reuse it with allow/deny for a *different* gate each), and
    # that same guard is what already keeps them out of this denominator.
    graded_gate = out["gate"]["open"] + out["gate"]["blocked"]
    if graded_gate:
        out["gate_pass_rate"] = round(out["gate"]["open"] / graded_gate, 3)
    out["sessions"] = sessions
    return out


def _render_entry(e: dict) -> str:
    if not e.get("kind"):
        return (f"  {e.get('ts', '?'):<26} legacy   {e.get('action', '')} "
                f"[{e.get('policy_matched', '')}]")
    icon = {"allow": "✅", "ask": "🟡", "deny": "⛔",
            "open": "✅", "blocked": "🛑", "gate_override": "⚠️"}.get(e.get("decision", ""), "•")
    what = e.get("path") or e.get("action") or e.get("tool") or ""
    tail = e.get("reason", "")
    return (f"  {e.get('ts', '?'):<26} {icon} {e.get('kind', ''):<8} "
            f"{e.get('decision', ''):<14} {what}"
            + (f"\n{'':<29}   {tail}" if tail else ""))


def main() -> int:
    ap = argparse.ArgumentParser(description="Read the God Mode decision log.")
    ap.add_argument("--tail", type=int, default=0, metavar="N", help="show the last N entries")
    ap.add_argument("--summary", action="store_true", help="counts by kind and decision")
    ap.add_argument("--days", type=int, default=0, metavar="N", help="limit to the last N days")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    root = ws_root()
    entries = read_entries(root, days=args.days or None)
    if not args.tail and not args.summary:
        args.summary = True

    if args.json:
        payload: dict = {}
        if args.summary:
            payload["summary"] = summarize(entries)
        if args.tail:
            payload["entries"] = entries[-args.tail:]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    if not entries:
        print("📭 Decision log is empty. It fills as the policy hook and the gate run.")
        return 0

    if args.summary:
        s = summarize(entries)
        rate = "no data" if s["autonomy_rate"] is None else f"{s['autonomy_rate'] * 100:.1f}%"
        print(f"🧾 Decision log — {s['total']} entr{'y' if s['total'] == 1 else 'ies'}"
              + (f" (last {args.days}d)" if args.days else "") + "\n")
        print(f"  Policy   ✅ allow {s['policy']['allow']}  "
              f"🟡 ask {s['policy']['ask']}  ⛔ deny {s['policy']['deny']}")
        print(f"  Gate     ✅ open {s['gate']['open']}  🛑 blocked {s['gate']['blocked']}")
        print(f"  Override ⚠️  {s['overrides']}"
              + (f"   ·  legacy pre-schema lines: {s['legacy']}" if s["legacy"] else ""))
        gate_rate = "no data" if s["gate_pass_rate"] is None else f"{s['gate_pass_rate'] * 100:.1f}%"
        print(f"  Autonomy rate: {rate}   ·   Gate pass rate: {gate_rate}"
              f"   ·   sessions recorded: {len(s['sessions'])}")
        if args.tail:
            print()

    if args.tail:
        print(f"🕑 Last {min(args.tail, len(entries))} entries:")
        for e in entries[-args.tail:]:
            print(_render_entry(e))
    return 0


if __name__ == "__main__":
    sys.exit(main())
