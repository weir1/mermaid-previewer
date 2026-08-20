#!/usr/bin/env python3
"""
ticktick_sync.py — Idempotent TickTick task sync from activeContext.md

Pushes (Pending) tasks to TickTick WITH reminders so you actually get notified
and can continue the project. Never auto-runs — call it only after the user has
approved which tasks to push (see AGENTS.md TickTick protocol).

Usage:
  python3 scripts/ticktick_sync.py <tag> <name> [--only ID1,ID2] [--due "<when>"] [--force]

  --only   Comma-separated task IDs to push (e.g. PH4-T01,PH4-T03). Default: all Pending.
  --due    Natural-language reminder applied to tasks lacking their own @due: token
           (e.g. "tomorrow 9am", "friday 6pm"). This is what makes TickTick REMIND you.
  --force  Re-push even if a task ID was already synced (updates the reminder).

Per-task tokens (place at the end of the task line in activeContext.md):
  @due:tomorrow 9am     → sets this task's reminder
  @prio:high            → none | low | medium | high

Requires the `tt` CLI (TickTick Global CLI) on PATH.
"""

import argparse
import json
import re
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
SYNC_STATE = ROOT / ".ai" / "memory-bank" / "ticktick_sync_state.json"
ACTIVE_CONTEXT = ROOT / ".ai" / "memory-bank" / "activeContext.md"

DUE_RE = re.compile(r"@due:\s*([^@]+?)(?=\s@\w+:|$)", re.I)
PRIO_RE = re.compile(r"@prio:\s*(none|low|medium|high)", re.I)

# ── Markdown flattening (PH0-T06) ────────────────────────────────────────────────
# The real incident: "(Pending)" sat *inside* the bold markers ("**(Pending) PH2-T03:
# …**"), so removing just the literal marker text left a stray "**" that reached the
# phone verbatim, and a wrapped title line was read only up to the line break. Fixed
# by joining wrapped lines BEFORE cleanup, then flattening markdown AFTER the literal
# "(Pending)" marker is removed — so a marker that happened to sit mid-emphasis is a
# case this module explicitly handles, not an accident it has to avoid.

_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_CODE_RE = re.compile(r"`([^`]*)`")
# Backreference-paired: the CLOSING run must match the opening run's marker and
# length exactly, so "**" only ever pairs with "**" (never a stray "*"), and the
# lookarounds require a non-word boundary around the markers — not the content —
# so "** PH2-T03: x**" (a leading space right after the marker, real-world debris
# from stripping "(Pending) " out of the middle of a bold span) still unwraps
# correctly, while "ALERT_BOT_TOKEN" (underscores with a word character on both
# sides) is never touched.
_MD_EMPHASIS_RE = re.compile(r"(?<!\w)(\*{1,2}|_{1,2})(.+?)\1(?!\w)")

TOP_BULLET_RE = re.compile(r"^-\s+(.*)$")
CHECKBOX_RE = re.compile(r"^\[[ xX~-]\]\s*")
TASK_ID_RE = re.compile(r"PH\d+-T\d+")

# TickTick's list view truncates long titles anyway; better to truncate on our own
# terms (word-ish boundary, explicit "…") than let the CLI/UI do it silently. The
# full text always survives in `content` — nothing pushed is ever actually lost.
TITLE_MAX = 80


def strip_markdown(text: str) -> str:
    """Flatten inline markdown to plain text for a TickTick title/body.

    `**bold**`, `*it*`, `_it_`, `` `code` `` and `[label](url)` all become their
    bare content. Underscores that aren't wrapping a word at a boundary — the
    identifiers this project quotes constantly, `ALERT_BOT_TOKEN`,
    `5g_proxy_workspace` — are left alone; a title should not censor them.
    """
    out = _MD_LINK_RE.sub(r"\1", text)
    out = _MD_CODE_RE.sub(r"\1", out)
    out = _MD_EMPHASIS_RE.sub(r"\2", out)
    return re.sub(r"\s+", " ", out).strip()


def _split_title(text: str) -> tuple[str, str]:
    """Split parsed task text into (title, content) for TickTick's two fields.

    An em-dash marks an explicit title/detail boundary ("Title — detail"). Without
    one, a title over `TITLE_MAX` chars is truncated with an ellipsis so it fits
    TickTick's list view — but nothing is lost: the full text always survives in
    `content` (used as the task's TickTick description).
    """
    if " — " in text:
        title, _, content = text.partition(" — ")
        return title.strip(), content.strip()
    if len(text) <= TITLE_MAX:
        return text, ""
    return text[:TITLE_MAX].rstrip() + "…", text


def _extract_tokens(text: str) -> tuple:
    """Pull `@due:`/`@prio:` tokens out of `text`, returning (due, prio, remainder).
    Tokens may land on a wrapped continuation line — this runs on the ALREADY-joined
    text, so a token's physical line in the source file doesn't matter."""
    due_m = DUE_RE.search(text)
    prio_m = PRIO_RE.search(text)
    due = due_m.group(1).strip() if due_m else None
    prio = prio_m.group(1).lower() if prio_m else None
    text = DUE_RE.sub("", text)
    text = PRIO_RE.sub("", text)
    return due, prio, text


def load_state(workspace: str) -> dict:
    if SYNC_STATE.exists():
        return json.loads(SYNC_STATE.read_text())
    return {"workspace": workspace, "synced": {}, "last_sync": ""}


def save_state(state: dict) -> None:
    SYNC_STATE.write_text(json.dumps(state, indent=2))


def parse_pending_tasks(context_path: Path) -> list[dict]:
    """Parse (Pending) tasks + their (possibly wrapped) title, indented subtasks
    (themselves possibly wrapped), and optional @due:/@prio: tokens.

    Only returns tasks explicitly marked (Pending). A top-level bullet's block ends
    at the next top-level bullet OR the first unindented non-bullet line (prose) —
    once ended, nothing after it is attributed to it even if later lines happen to
    look like subtask bullets again (`test_unindented_prose_ends_the_block`).
    """
    lines = context_path.read_text().splitlines()
    tasks: list[dict] = []

    current: dict | None = None
    title_parts: list[str] = []
    subtasks: list[str] = []

    def finish():
        nonlocal current, title_parts, subtasks
        if current is None:
            return
        joined = " ".join(p.strip() for p in title_parts if p.strip())
        due, prio, joined = _extract_tokens(joined)
        joined = re.sub(r"\s*\(Pending\)\s*", " ", joined)
        joined = strip_markdown(joined)
        title, content = _split_title(joined)
        id_m = TASK_ID_RE.search(title) or TASK_ID_RE.search(joined)
        current.update({
            "id": id_m.group(0) if id_m else None,
            "title": title,
            "content": content,
            "subtasks": subtasks,
            "due": due,
            "prio": prio,
        })
        tasks.append(current)
        current, title_parts, subtasks = None, [], []

    for line in lines:
        if line.startswith("- "):
            finish()  # a new top-level bullet always ends the previous block
            body = line[2:]
            if "(Pending)" in body:
                current = {}
                title_parts = [body]
                subtasks = []
            continue

        if current is None:
            continue

        if not line.strip():
            continue  # a blank line alone does not end the block

        if len(line) - len(line.lstrip(" ")) == 0:
            # Unindented, non-bullet prose ends the block permanently — even a
            # bullet-shaped line further down must not be attributed to this task.
            finish()
            continue

        stripped = line.strip()
        if stripped.startswith("- "):
            sub = CHECKBOX_RE.sub("", stripped[2:].strip())
            if sub:
                subtasks.append(sub)
        elif subtasks:
            subtasks[-1] = (subtasks[-1] + " " + stripped).strip()
        else:
            title_parts.append(stripped)

    finish()
    return tasks


def sync_tasks(tag: str, workspace: str, only=None, default_due=None,
               force=False) -> tuple[int, int]:
    if not ACTIVE_CONTEXT.exists():
        print("⚠️  No activeContext.md found. Skipping TickTick sync.")
        return 0, 0

    state = load_state(workspace)
    tasks = parse_pending_tasks(ACTIVE_CONTEXT)
    synced = skipped = 0

    for task in tasks:
        if task["id"] is None:
            print(f"  ⚠️  Skipping (no ID): '{task['title'][:60]}' — add a PH#-T## prefix")
            skipped += 1
            continue
        if only and task["id"] not in only:
            continue
        if task["id"] in state["synced"] and not force:
            print(f"  ⏭️  Already synced: {task['id']} (use --force to re-push)")
            skipped += 1
            continue

        cmd = ["tt", "add", task["title"], "-t", tag]
        if task["subtasks"]:
            cmd += ["-s", ", ".join(task["subtasks"])]  # CLI wants ONE comma-joined value

        due = task["due"] or default_due
        if due:
            cmd += ["-d", due]
        else:
            print(f"  ⏰ {task['id']}: no reminder set — pass --due or add @due: for a notification")
        if task["prio"]:
            cmd += ["-p", task["prio"]]

        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode == 0:
            reminder = f" ⏰ {due}" if due else ""
            print(f"  ✅ Synced [{task['id']}]: {task['title']}{reminder}  ({len(task['subtasks'])} subtasks)")
            state["synced"][task["id"]] = {
                "title": task["title"],
                "tag": tag,
                "due": due,
                "priority": task["prio"],
                "subtask_count": len(task["subtasks"]),
                "synced_at": datetime.now(timezone.utc).isoformat(),
            }
            synced += 1
        else:
            print(f"  ⚠️  Failed [{task['id']}]: {result.stderr.strip() or result.stdout.strip()}")

    state["last_sync"] = datetime.now(timezone.utc).isoformat()
    save_state(state)
    return synced, skipped


def _gate_allows(override_reason: str | None) -> bool:
    """Run the shared validation gate. An override is permitted but never silent —
    it is written to .ai/decision-log/ so `just audit` can count bypasses."""
    root = _ws_root()
    gate = root / "scripts" / "gate_check.py"
    if not gate.exists():
        print("🛑 gate_check.py not found — cannot verify the validation gate. Sync blocked.")
        print("   → Run `god-repair .` to deploy the current OS scripts.")
        return False
    r = subprocess.run([sys.executable, str(gate), "--action",
                        "TickTick sync (external side effect)"], cwd=root)
    if r.returncode == 0:
        return True
    if not override_reason:
        print("   → Fix the gate, or pass --override-gate \"<reason>\" (logged, not silent).")
        return False

    # PH6-T13: one writer for the decision log. This used to hand-roll its own line
    # shape, so the only entries the OS ever produced were unqueryable alongside
    # everything else. Falls back to the inline append if decision_log.py isn't
    # deployed — an override must be recorded even on an older workspace.
    written = False
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import decision_log as dl
        written = dl.record("override", "gate_override", "ticktick_sync", root=root,
                            action="ticktick_sync", reason=override_reason)
    except Exception:  # noqa: BLE001
        written = False
    if not written:
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": "ticktick_sync",
            "decision": "gate_override",
            "reason": override_reason,
        }
        log_dir = root / ".ai" / "decision-log"
        log_dir.mkdir(parents=True, exist_ok=True)
        with (log_dir / f"{datetime.now(timezone.utc):%Y-%m-%d}.jsonl").open("a") as fh:
            fh.write(json.dumps(entry) + "\n")
    print(f"⚠️  GATE OVERRIDDEN — logged to .ai/decision-log/. Reason: {override_reason}")
    return True


def main():
    ap = argparse.ArgumentParser(description="Sync approved (Pending) tasks to TickTick with reminders.")
    ap.add_argument("tag", help="workspace tag, e.g. context_workspace")
    ap.add_argument("name", help="workspace name, e.g. Context")
    ap.add_argument("--only", default="", help="comma-separated task IDs to push")
    ap.add_argument("--due", default="", help="natural-language reminder for tasks without @due:")
    ap.add_argument("--force", action="store_true", help="re-push already-synced tasks")
    ap.add_argument("--override-gate", metavar="REASON",
                    help="proceed with a CLOSED validation gate. Requires a written reason, "
                         "which is recorded in .ai/decision-log/. Never silent.")
    args = ap.parse_args()

    # Validation gate — TickTick is an external side effect (AGENTS.md §VALIDATION GATE).
    # Enforced here as well as in `just tt-sync` so calling the script directly can't bypass it.
    if not _gate_allows(args.override_gate):
        return 1

    only = {i.strip() for i in args.only.split(",") if i.strip()} or None
    synced, skipped = sync_tasks(args.tag, args.name, only=only,
                                 default_due=args.due or None, force=args.force)
    print()
    print("──────────────────────────────────────────")
    print(f"✅ TickTick sync complete [tag: {args.tag}]")
    print(f"   Synced: {synced}  |  Skipped/Deduped: {skipped}")
    print(f"   State: {SYNC_STATE}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
