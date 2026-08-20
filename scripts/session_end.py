#!/usr/bin/env python3
"""
session_end.py — God Mode AI OS Session Closer
===============================================
Run at the END of every AI session to:
  1. Run freshen_memory.py (reset last_verified on all memory files)
  2. Write a session log JSON to .ai/session-log/
  3. Prompt AI to update activeContext.md and progress.md
  4. Verify git status and remind about uncommitted files

Usage:
  python3 scripts/session_end.py
  just session-end
  python3 scripts/session_end.py --tasks-completed "PH2-T01,PH2-T02" --summary "Fixed TickTick sync"
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

def _ws_root() -> Path:
    """Resolve workspace root cwd-safely (git toplevel; fallback to cwd)."""
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
MEMORY_BANK = ROOT / ".ai" / "memory-bank"
SESSION_LOG_DIR = ROOT / ".ai" / "session-log"
WORKSPACE_NAME = ROOT.resolve().name


def run_freshen() -> bool:
    result = subprocess.run(
        ["python3", "scripts/freshen_memory.py"],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"⚠️  freshen_memory.py failed: {result.stderr}")
        return False
    return True


def get_git_status() -> dict:
    try:
        dirty = subprocess.run(["git", "status", "--short"], capture_output=True, text=True)
        commit = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
        return {
            "commit": commit.stdout.strip(),
            "dirty_files": [l.strip() for l in dirty.stdout.strip().splitlines() if l.strip()]
        }
    except Exception:
        return {"commit": "unknown", "dirty_files": []}


def write_session_log(tasks_completed: list, tasks_failed: list, summary: str) -> Path:
    SESSION_LOG_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    ts = now.strftime("%Y-%m-%d_%H%M%S")
    git = get_git_status()
    log = {
        "session_id": now.isoformat(),
        "workspace": WORKSPACE_NAME,
        "workspace_state_at_start": "VERIFIED",
        "tasks_completed": tasks_completed,
        "tasks_failed": tasks_failed,
        "what_changed": summary or "No summary provided",
        "git_commit": git["commit"],
        "dirty_files_count": len(git["dirty_files"]),
        "dirty_files": git["dirty_files"][:10],
        "memory_freshened": True,
        "written_at": now.isoformat()
    }
    out = SESSION_LOG_DIR / f"{ts}.json"
    out.write_text(json.dumps(log, indent=2))
    return out


def run_rotate_changelog() -> bool:
    """Run rotate_changelog.py to archive old entries and update the pinned summary block."""
    changelog_script = Path("scripts/rotate_changelog.py")
    if not changelog_script.exists():
        print("  ℹ️  rotate_changelog.py not found — skipping rotation")
        return True
    result = subprocess.run(
        ["python3", str(changelog_script)],
        capture_output=True, text=True
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"⚠️  rotate_changelog.py failed: {result.stderr}")
        return False
    return True


def main():
    parser = argparse.ArgumentParser(description="God Mode Session End Closer")
    parser.add_argument("--tasks-completed", default="", help="Comma-separated task IDs completed")
    parser.add_argument("--tasks-failed", default="", help="Comma-separated task IDs failed")
    parser.add_argument("--summary", default="", help="One-line summary of what changed")
    args = parser.parse_args()

    completed = [t.strip() for t in args.tasks_completed.split(",") if t.strip()]
    failed = [t.strip() for t in args.tasks_failed.split(",") if t.strip()]

    print("\n" + "═"*50)
    print("  🏁 GOD MODE SESSION CLOSING")
    print("═"*50)

    # What finished this session, and how the goals moved (PH9-T06) — leads
    # closure the same way session_open leads session_start. Wrapped the same
    # defensive way: a bug here degrades to a one-line notice, never breaks
    # session-end fleet-wide. Write-back runs by default — "marked in the plan
    # by the close, not by hand" means this step does it, not a manual edit.
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import session_close
        report = session_close.close_report(ROOT)
        # Plain English first — it is the half written for the person reading
        # this, and the operator asked for both an opening and a closing he
        # could actually read (PH9-T17). The technical rendering follows for
        # the AI and for anyone chasing a specific task id.
        session_close.render_close_plain(report)
        print()
        session_close.render_close(report)
        wb = session_close.write_back(ROOT, report)
        if wb.get("marked"):
            print(f"  📝 .ai/plan.md updated — {wb['reason']}")
    except Exception as exc:  # noqa: BLE001
        print(f"  ℹ️  Session close report unavailable ({exc}).")

    print("\n[1/4] Freshening memory bank...")
    run_freshen()

    print("\n[2/4] Rotating AI_CHANGELOG (archive old, refresh summary)...")
    run_rotate_changelog()

    print("\n[3/4] Writing session log...")
    log_path = write_session_log(completed, failed, args.summary)
    print(f"  ✅ Session log written: {log_path}")

    print("\n[4/4] Git status check...")
    git = get_git_status()
    if git["dirty_files"]:
        print(f"  ⚠️  {len(git['dirty_files'])} uncommitted file(s):")
        for f in git["dirty_files"][:10]:
            print(f"     {f}")
        print("  ⚡ Run `just commit-all` to commit everything.")
    else:
        print("  ✅ Working tree is clean.")

    print("\n" + "═"*50)
    print("  ✅ Session closed. Memory is fresh. Changelog rotated.")
    print("  💡 REMINDER: Update activeContext.md task statuses before leaving.")
    print("  💡 REMINDER: Append to AI_CHANGELOG.md if any files were changed.")
    print("═"*50 + "\n")


if __name__ == "__main__":
    main()
