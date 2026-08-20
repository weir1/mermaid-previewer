#!/usr/bin/env python3
"""
session_start.py — God Mode AI OS Session Auditor
==================================================
Run at the START of every AI session for the Context workspace.

What it does (in order):
  1. Checks all memory bank files for freshness (stale TTL)
  2. Validates evidence.json (real vs stub)
  3. Reads AI_CHANGELOG.md — last 10 entries — so AI knows what changed recently
  4. Scans Telegram inbox for notes routed to THIS workspace using workspace-specific tag
  5. Shows (Pending) TickTick tasks and ASKS user which to sync (NEVER auto-syncs)
  6. Prints a formatted SESSION BRIEFING the AI presents to the user

## The briefing is read-only; only the budget reset is not (PH7-T09)

Everything above only *reads*. The one side effect is section 8, which resets the
work-budget counter — and that counter is a credential (PH7-T02) whose state also
carries the base of the self-review diff (PH7-T04). Claude Code fires SessionStart
on resume and compaction too, so this script runs *inside* live sessions, where a
reset destroys earned credit and silently narrows what `close git-push` reviews.

Two things follow:
  • the hook payload arrives as JSON on stdin and names the `source`
    (startup/resume/clear/compact) — read it and pass it on, so the budget can
    tell a new session from a re-entry instead of guessing;
  • `--no-reset` renders the whole briefing and touches nothing, for the case
    that started this: running the script by hand to look at its output.

Reading stdin is best-effort and must never block — a hang here breaks session
start in all 38 workspaces. No payload simply means "source unknown", which
`session_budget.should_reset` treats as the protective case.

Usage:
  python3 scripts/session_start.py
  python3 scripts/session_start.py --no-reset      # briefing only, no state change
  just session-start
"""

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
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


def uncommitted_paths(root=None) -> int | None:
    """How many paths the working tree still holds — or None if git could not say.

    PH22-T05. `None` and `0` are kept strictly apart. A failed `git status`
    returning "" reads as a clean tree, which would tell an operator their
    unclosed work was safely committed at the exact moment it was not. That
    error-path-equals-success-path shape is this workspace's recurring defect;
    every caller must branch on `is None` before truthiness.
    """
    try:
        r = subprocess.run(["git", "status", "--porcelain"],
                           cwd=str(root or ROOT), capture_output=True,
                           text=True, timeout=5)
        if r.returncode != 0:
            return None
        return len([ln for ln in r.stdout.splitlines() if ln.strip()])
    except Exception:  # noqa: BLE001
        return None


def hook_source(stream=None, timeout: float = 0.2) -> str:
    """The SessionStart hook's `source` (startup/resume/clear/compact), or "".

    Claude Code delivers the hook payload as JSON on stdin. Reading it is what
    lets the budget distinguish a new session from a resume — but it is strictly
    best-effort: this runs at the top of every session in every workspace, so a
    block here is a fleet-wide hang. Hence: never read a TTY, never read without
    `select()` saying data is ready, and treat every failure as "unknown", which
    `session_budget.should_reset` handles protectively.

    `--source X` on the command line wins, so the value can be forced for testing
    and by any harness that has no stdin payload to give.
    """
    if "--source" in sys.argv:
        i = sys.argv.index("--source")
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1].strip()
    stream = stream or sys.stdin
    try:
        if stream is None or stream.closed or stream.isatty():
            return ""
        import select
        if not select.select([stream], [], [], timeout)[0]:
            return ""
        payload = json.loads(stream.read() or "{}")
        return str(payload.get("source", "")).strip()
    except Exception:  # noqa: BLE001
        return ""
MEMORY_BANK = ROOT / ".ai" / "memory-bank"
EVIDENCE = MEMORY_BANK / "evidence.json"
ACTIVE_CONTEXT = MEMORY_BANK / "activeContext.md"
AI_CHANGELOG = ROOT / "AI_CHANGELOG.md"
TELEGRAM_INBOX = Path(os.path.expanduser("~/Documents/Notes/Telegram/Received_Notes"))
ROUTES_FILE = Path(os.path.expanduser("~/.gemini/m_protocol_routes.json"))
CONTEXT_KERNEL = Path(os.path.expanduser("~/Documents/Context"))
OS_VERSION_FILE = ROOT / ".ai" / "os_version.json"


def _required_memory_files() -> list:
    """The memory-bank files this workspace must keep fresh — from its ROLE.

    PH8-T02: this was a literal list of seven paths, duplicating the one in
    `doctor.py`. Both now read `role_registry`, which resolves the workspace's
    declared `role_pack:` (absent → `software-engineer`, i.e. exactly today's
    seven, in today's order — the migration guarantee for all 46 workspaces).

    Never raises. This runs at the top of EVERY session in every governed
    workspace; a stack trace here breaks the whole fleet at once, which is worse
    than any wrong answer. On failure the freshness check degrades to evidence
    only and records WHY in `REQUIRED_FILES_ERROR`, which section 1 prints — a
    silent degrade would mean the memory-freshness check quietly stopped
    happening while the briefing still looked normal, which is worse than the
    crash it replaced.
    """
    global REQUIRED_FILES_ERROR
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import role_registry
        names = role_registry.required_memory(ROOT)
        return [MEMORY_BANK / f"{n}.md" for n in names] + [EVIDENCE]
    except Exception as exc:  # noqa: BLE001
        REQUIRED_FILES_ERROR = (
            f"role_registry unavailable ({exc}) — memory freshness NOT checked "
            "this session; run `just doctor` for the schema state")
        return [EVIDENCE]


#: Set by `_required_memory_files()` when the role schema could not be read.
REQUIRED_FILES_ERROR = ""
REQUIRED_FILES = _required_memory_files()

now = datetime.now(timezone.utc)
WORKSPACE_NAME = ROOT.resolve().name  # e.g. "Context"


def _usable_route(tag: str, path: str) -> bool:
    """Is this routes-file entry even eligible to be compared, before we try? (PH0-T02)

    The real incident: `m_protocol_routes.json` contained `"@": "."`. A relative path
    resolves against the CURRENT PROCESS's cwd, not the workspace being checked — and
    sessions are conventionally started from the workspace root, so a relative
    catch-all entry silently resolved to whatever workspace happened to be running and
    matched it, handing every workspace the tag `@`. Since note-matching is a substring
    test, `@` then matched every note in everyone's inbox.

    Two independent guards, because either alone still leaves a hole:
      - the tag must look like a real tag (`@something`, not bare `@` or empty or a
        word with no `@` at all) — a degenerate tag is not worth resolving even if its
        path is fine;
      - the path must be absolute — a relative path is a workspace-root-dependent
        landmine regardless of what tag it's paired with.
    """
    if not tag or not tag.startswith("@") or len(tag) < 2:
        return False
    return Path(path).is_absolute()


def get_workspace_tag() -> str:
    """Determine this workspace's Telegram tag from the routes file.
    Each workspace has its own unique @tag — ONLY check notes for this workspace.
    """
    if not ROUTES_FILE.exists():
        return "@" + WORKSPACE_NAME.lower()
    try:
        routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        resolved = ROOT.resolve()
        for tag, path in routes.items():
            try:
                if not _usable_route(tag, path):
                    continue
                if Path(path).resolve() == resolved:
                    return tag  # e.g. "@context"
            except Exception:
                continue
    except Exception:
        pass
    return "@" + WORKSPACE_NAME.lower()


WORKSPACE_TAG = get_workspace_tag()


def section(title: str):
    print(f"\n{'─'*50}")
    print(f"  {title}")
    print(f"{'─'*50}")


def parse_frontmatter(text: str) -> dict:
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}
    meta = {}
    for line in m.group(1).splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta


# ── 0. OS Drift Detection ───────────────────────────────────────────────
def check_os_drift() -> tuple[str, str, str]:
    """Compare workspace os_version.json against Context kernel master.
    Returns (status, workspace_ver, context_ver).
    status: 'ok' | 'drift' | 'missing' | 'context_missing'
    """
    context_ver_file = CONTEXT_KERNEL / ".ai" / "os_version.json"

    # If we ARE the Context workspace, no drift possible
    if ROOT.resolve() == CONTEXT_KERNEL.resolve():
        if OS_VERSION_FILE.exists():
            data = json.loads(OS_VERSION_FILE.read_text())
            return "self", data.get("version", "?"), data.get("version", "?")
        return "self", "?", "?"

    # Context kernel version
    if not context_ver_file.exists():
        return "context_missing", "?", "?"
    try:
        ctx = json.loads(context_ver_file.read_text())
        ctx_ver = ctx.get("version", "?")
    except Exception:
        return "context_missing", "?", "?"

    # Workspace version
    if not OS_VERSION_FILE.exists():
        return "missing", "not stamped", ctx_ver

    try:
        ws = json.loads(OS_VERSION_FILE.read_text())
        ws_ver = ws.get("version", "?")
    except Exception:
        return "missing", "corrupt", ctx_ver

    if ws_ver == ctx_ver:
        return "ok", ws_ver, ctx_ver
    return "drift", ws_ver, ctx_ver


# ── Changelog Freshness Check ───────────────────────────────────────────
def check_changelog_freshness() -> tuple[bool, str]:
    """Detect REAL changelog drift: did the latest commit change files without
    updating AI_CHANGELOG.md? (The old check warned whenever a day passed with no
    session — a false positive. We now compare against git, not the calendar.)
    Returns (is_ok, message).
    """
    import subprocess
    if not AI_CHANGELOG.exists():
        return False, "AI_CHANGELOG.md does not exist yet."
    mtime = AI_CHANGELOG.stat().st_mtime
    try:
        last_commit_ts = int(subprocess.check_output(
            ["git", "log", "-1", "--format=%ct"],
            stderr=subprocess.DEVNULL, text=True).strip())
    except Exception:
        last_commit_ts = 0
    if last_commit_ts and last_commit_ts > mtime + 60:
        try:
            files = subprocess.check_output(
                ["git", "show", "--name-only", "--format=", "HEAD"],
                stderr=subprocess.DEVNULL, text=True).split()
        except Exception:
            files = []
        if files and not any("AI_CHANGELOG" in f for f in files):
            when = datetime.fromtimestamp(last_commit_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
            return False, (f"⚠️  The last commit ({when} UTC) changed files but did NOT "
                           f"update AI_CHANGELOG.md. Append your entry before ending this session.")
    ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M")
    return True, f"In sync with git (last change {ts} UTC) — ✅"


# ── 1. Memory Bank Freshness ───────────────────────────────────────────────
def check_memory_bank() -> tuple[bool, list[str]]:
    issues = []
    all_ok = True
    for path in REQUIRED_FILES:
        if not path.exists():
            issues.append(f"  ❌ MISSING: {path.name}")
            all_ok = False
            continue
        if path.suffix == ".json":
            continue  # evidence.json checked separately
        text = path.read_text(encoding="utf-8")
        meta = parse_frontmatter(text)
        freshness = meta.get("freshness", "unknown")
        age = meta.get("age_days", "?")
        if freshness == "stale":
            issues.append(f"  ⚠️  STALE ({age}d): {path.name} — run: just freshen-memory")
            all_ok = False
        else:
            issues.append(f"  ✅ fresh: {path.name}")
    return all_ok, issues


# ── 2. Evidence.json Validation ────────────────────────────────────────────
def active_task_lines(root=None) -> list[str]:
    """What `evidence.json` will bind to, stated at the START of the session.

    PH16-T32. `active task: none` is a perfectly normal answer — most sessions
    open on maintenance — but it is also what a session that *has* declared a task
    sees when the declaration is malformed, and until now nothing said so until
    `just work-done` refused the claim, two ~80s verification runs later. Naming
    the resolved binding costs one line and moves that discovery to the top of the
    session, where it is still cheap to fix.

    Delegates to `task_ledger`, which owns the rule and the orphan warning, so the
    briefing cannot disagree with the evidence it is briefing about.
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import task_ledger
    except Exception:  # noqa: BLE001
        return []
    try:
        report = task_ledger.active_task_report(Path(root) if root is not None else ROOT)
        warnings = task_ledger.orphan_warning(report)
    except Exception:  # noqa: BLE001
        return []
    bound = report["task"] or "none (evidence will record a maintenance run)"
    out = [f"  🔗 active task: {bound}"]
    out += [f"  {w}" for w in warnings]

    # PH22-T01: say it at the START of the session, not at credit time. This is
    # PH16-T32's lesson applied to the new gate — a control that only fires when
    # the claim is refused reports the problem after the cost has been paid.
    if report["task"]:
        try:
            import prework
            bv = prework.validate(report["task"],
                                  Path(root) if root is not None else ROOT)
            if not bv["ok"]:
                out.append(f'  ⚠️  no pre-work brief for {report["task"]} — '
                           f'`just brief "{report["task"]}"`, discuss it, then '
                           "`--accept \"his words\"`. `work-done` will refuse without it.")
            elif bv["timing"] == "post-hoc":
                out.append(f'  ℹ️  {report["task"]}\'s brief was written after '
                           f'{bv["changed_since"]} file(s) had already changed.')
        except Exception:  # noqa: BLE001 — a briefing must never break the session
            pass
    return out


def check_evidence() -> tuple[str, str]:
    """Delegates to gate_check.py — the single implementation of the gate contract.
    Falls back to a local read only if the shared checker isn't deployed yet."""
    gate = ROOT / "scripts" / "gate_check.py"
    if gate.exists():
        try:
            r = subprocess.run(["python3", str(gate), "--json"],
                               capture_output=True, text=True, cwd=ROOT)
            v = json.loads(r.stdout)
            return ("PASS" if v["open"] else "BLOCKED"), v["reason"]
        except Exception:  # noqa: BLE001
            pass
    if not EVIDENCE.exists():
        return "MISSING", "evidence.json not found — run: just verify-safe"
    try:
        ev = json.loads(EVIDENCE.read_text())
    except Exception:
        return "CORRUPT", "evidence.json is not valid JSON"
    status = ev.get("status", "unknown")
    msg = ev.get("message", "")
    pipeline = ev.get("pipeline", "unknown")
    ts = ev.get("timestamp", "")
    if "Onboarding" in msg or pipeline in ("onboarding", "unverified"):
        return "BLOCKED", f"evidence.json is a bootstrap stub (pipeline={pipeline}). Run: just verify-safe"
    if status != "passed":
        return "BLOCKED", f"evidence.json status={status}. Run: just verify-safe"
    return "PASS", f"Passed [{pipeline}] at {ts}"


# ── 3. AI_CHANGELOG — Recent Changes ──────────────────────────────────────
def get_recent_changelog(n_entries: int = 10) -> list[str]:
    """Read last N changelog entries from AI_CHANGELOG.md.
    Used so AI knows what changed recently without reading the full file.
    """
    if not AI_CHANGELOG.exists():
        return ["⚠️ AI_CHANGELOG.md not found."]
    lines = AI_CHANGELOG.read_text(encoding="utf-8").splitlines()
    # Find lines starting with "## [" — these are entry headers
    entries = []
    current = []
    for line in reversed(lines):
        if line.startswith("## [") and current:
            entries.append(line)
            if len(entries) >= n_entries:
                break
            current = [line]
        else:
            current.append(line)
    if not entries and lines:
        # Fallback: return last 15 lines
        return lines[-15:]
    return list(reversed(entries))


# ── 4. Telegram Inbox — Workspace Notes ────────────────────────────────────
def check_telegram_inbox() -> list[dict]:
    """Find notes that genuinely belong to THIS workspace.

    Two structured sources only — NEVER a free-text substring scan. The old
    version matched the workspace tag as a bare English word anywhere in a
    note's body (e.g. @context, @job, @life, @money are all common words),
    so unrelated journal entries / AI digests that merely used that word in
    a sentence got flagged as belonging to this workspace.

      1. <workspace>/docs/**/note_*.md — notes telegram_pull.py already
         routed here via an EXACT @tag match against m_protocol_routes.json.
         Always relevant; shown as "routed".
      2. Received_Notes/*.md whose frontmatter `workspace:`/`tags:` field is
         an EXACT match for this workspace's tag — catches a typo'd or
         unregistered route mpull couldn't resolve. Shown as "unrouted".
    """
    tag_clean = WORKSPACE_TAG.lstrip("@").lower()
    found = []

    docs_dir = ROOT / "docs"
    if docs_dir.exists():
        for note_file in sorted(docs_dir.rglob("note_*.md"), reverse=True)[:50]:
            text = note_file.read_text(encoding="utf-8", errors="ignore")
            meta = parse_frontmatter(text)
            body = re.sub(r"^---.*?---\n", "", text, flags=re.S).strip()
            found.append({
                "file": str(note_file.relative_to(ROOT)),
                "date": meta.get("date", "unknown"),
                "matched_on": f"routed → {meta.get('workspace', WORKSPACE_TAG)}",
                "preview": body[:200],
            })

    if TELEGRAM_INBOX.exists():
        for note_file in sorted(TELEGRAM_INBOX.glob("*.md"), reverse=True)[:50]:
            text = note_file.read_text(encoding="utf-8", errors="ignore")
            meta = parse_frontmatter(text)
            workspace_meta = meta.get("workspace", "").lstrip("@").strip().lower()
            tags_meta = [
                t.strip().lower()
                for t in meta.get("tags", "").strip("[]").split(",")
                if t.strip()
            ]
            matched_tag = None
            if workspace_meta == tag_clean:
                matched_tag = f"@{workspace_meta}"
            elif tag_clean in tags_meta:
                matched_tag = f"#{tag_clean}"
            if matched_tag:
                body = re.sub(r"^---.*?---\n", "", text, flags=re.S).strip()
                found.append({
                    "file": note_file.name,
                    "date": meta.get("date", "unknown"),
                    "matched_on": f"unrouted in inbox ({matched_tag})",
                    "preview": body[:200],
                })
    return found


# ── 4b. Findings received from other workspaces (PH16-T25) ─────────────────
def check_findings_inbox(root=None) -> list[dict]:
    """Notes another workspace filed into `.ai/inbox/` via `just finding`.

    PH16-T22 built the channel and nothing read it. `@jobscraper` filed a correct
    kernel defect the day it shipped and the note sat unread — this briefing scanned
    the *Telegram* inbox and stopped there, so the only reason anyone learned of it
    was the operator saying so out loud.

    Discovery and formatting are delegated to `finding.py` rather than reimplemented
    here, so the reader and the writer cannot disagree about where notes live or how
    one is titled. A note whose origin line is missing (a hand-written one) still
    surfaces — an unparseable field must cost that field, never the whole note.
    """
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import finding
    except Exception:  # noqa: BLE001
        return []

    base = Path(root) if root is not None else ROOT
    out = []
    for note in finding.inbox_notes(base):
        try:
            origin, title = finding._origin_of(note), finding._title_of(note)
        except Exception:  # noqa: BLE001
            origin, title = "(unknown origin)", note.stem
        out.append({"file": note.name, "origin": origin, "title": title})
    return out


# ── 5. Pending TickTick Tasks ──────────────────────────────────────────────
def get_pending_tasks() -> list[dict]:
    """Parse (Pending) tasks from activeContext.md.
    Returns list of {id, title, subtasks} dicts.
    NEVER auto-syncs — only shows for user approval.
    """
    if not ACTIVE_CONTEXT.exists():
        return []
    due_re = re.compile(r"@due:\s*([^@]+?)(?=\s@\w+:|$)", re.I)
    prio_re = re.compile(r"@prio:\s*(none|low|medium|high)", re.I)
    tasks = []
    current_task = None
    for line in ACTIVE_CONTEXT.read_text().splitlines():
        if re.match(r"^- .+\(Pending\)", line):
            if current_task:
                tasks.append(current_task)
            raw = re.sub(r"^-\s+", "", line)
            raw = re.sub(r"\s*\(Pending\)\s*", " ", raw)
            due_m = due_re.search(raw)
            title = prio_re.sub("", due_re.sub("", raw)).strip()
            task_id_match = re.search(r"PH\d+-T\d+", title)
            task_id = task_id_match.group(0) if task_id_match else None
            current_task = {"id": task_id, "title": title, "subtasks": [],
                            "due": due_m.group(1).strip() if due_m else None}
        elif re.match(r"^- ", line):
            # a non-Pending top-level task ends the current capture (no subtask bleed)
            if current_task:
                tasks.append(current_task)
                current_task = None
        elif re.match(r"^  +- ", line) and current_task is not None:
            subtask = re.sub(r"^[\s-]+", "", line).strip()
            subtask = re.sub(r"^\[[ x]\]\s*", "", subtask)
            if subtask:
                current_task["subtasks"].append(subtask)
    if current_task:
        tasks.append(current_task)
    return tasks


# ── 6. Print Session Briefing ──────────────────────────────────────────────
def main():
    print("\n" + "═"*50)
    print(f"  🧠 GOD MODE SESSION BRIEFING")
    print(f"  Workspace: {WORKSPACE_NAME} | Tag: {WORKSPACE_TAG}")
    print(f"  {now.strftime('%Y-%m-%d %H:%M')} UTC")
    print("═"*50)

    # Workspace intent — leads the briefing (PH9-T04): what this workspace is
    # for, its standing, and why/impact for what's queued this session. Wrapped
    # like section 7's token estimate: a bug here degrades to a one-line notice,
    # never breaks session-start fleet-wide.
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import session_open
        render_open = getattr(session_open, "render_open", None)
        if render_open:
            render_open(session_open.opening(ROOT))
    except Exception as exc:  # noqa: BLE001
        print(f"  ℹ️  Workspace intent unavailable ({exc}).")

    # Health — the one question the other nine sections never asked (PH16-T01).
    # Placed here, ahead of the numbered sections, because a briefing that leads
    # with the OS version while two checks are failing has buried the headline.
    # Wrapped like the intent opening above: a bug here degrades to one notice,
    # and a check that could not run says so rather than going quiet.
    section("🩺 WORKSPACE HEALTH — is this workspace working right now?")
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import health
        health.render(health.check(ROOT))
    except Exception as exc:  # noqa: BLE001
        print(f"  ⚠️  Health check unavailable ({exc}) — treat this workspace as UNVERIFIED.")

    # OS Drift Check
    section("0. GOD MODE OS VERSION CHECK")
    drift_status, ws_ver, ctx_ver = check_os_drift()
    if drift_status == "self":
        print(f"  ✅ Context workspace — OS v{ws_ver} (this IS the master kernel)")
    elif drift_status == "ok":
        print(f"  ✅ OS up to date: v{ws_ver} (matches Context kernel)")
    elif drift_status == "drift":
        print(f"  ⚠️  OS DRIFT DETECTED")
        print(f"     Workspace OS : v{ws_ver}")
        print(f"     Context OS   : v{ctx_ver}  ← newer")
        print(f"     Fix: run god-upgrade . (from inside this workspace)")
    elif drift_status == "missing":
        print(f"  ⚠️  No os_version.json in this workspace (pre-v3.2 onboarding)")
        print(f"     Context OS: v{ctx_ver}")
        print(f"     Fix: run god-upgrade . to stamp this workspace")
    elif drift_status == "context_missing":
        print(f"  ℹ️  Context kernel os_version.json not found — skipping drift check")

    # Memory Bank
    section("1. MEMORY BANK STATUS")
    # PH8-T02: if the role schema could not be read, the list below is NOT the
    # workspace's required set and the freshness check silently covered almost
    # nothing. Say so first — a degraded check that looks normal is worse than
    # the crash it replaced.
    if REQUIRED_FILES_ERROR:
        print(f"  ❌ {REQUIRED_FILES_ERROR}")
    ok, issues = check_memory_bank()
    for line in issues:
        print(line)
    if not ok:
        print("  ⚡ ACTION: Run `just freshen-memory` to reset staleness.")

    # Evidence
    section("2. VALIDATION GATE (evidence.json)")
    ev_status, ev_msg = check_evidence()
    icon = "✅" if ev_status == "PASS" else "❌"
    print(f"  {icon} [{ev_status}] {ev_msg}")
    if ev_status != "PASS":
        print("  ⚡ ACTION: Run `just verify-safe` to regenerate evidence.")
    for line in active_task_lines():
        print(line)

    # AI Changelog freshness + recent changes
    section("3. AI_CHANGELOG STATUS")
    cl_fresh, cl_msg = check_changelog_freshness()
    print(f"  {cl_msg}")
    if not cl_fresh:
        print("     ➤ Append your entry to AI_CHANGELOG.md before ending this session.")
    print()
    print("  Recent entries (last 10):")
    changelog_entries = get_recent_changelog(10)
    if changelog_entries:
        for entry in changelog_entries:
            print(f"  {entry}")
    else:
        print("  📭 No changelog entries found.")

    # Telegram
    section(f"4. TELEGRAM INBOX (notes tagged {WORKSPACE_TAG} only)")
    tg_notes = check_telegram_inbox()
    if not tg_notes:
        print(f"  📭 No notes found for {WORKSPACE_TAG}.")
    else:
        print(f"  📬 {len(tg_notes)} note(s) found for this workspace:\n")
        for i, note in enumerate(tg_notes[:5], 1):
            print(f"  [{i}] {note['file']} | {note['date']} | {note['matched_on']}")
            print(f"       {note['preview'][:120]}..." if len(note['preview']) > 120 else f"       {note['preview']}")
            print()
        if len(tg_notes) > 5:
            print(f"  ... and {len(tg_notes)-5} more. Run `just check-inbox` to see all.")

    # Findings filed here by other workspaces (PH16-T25). Silent when the inbox is
    # empty or absent — this section exists to deliver notes, not to add a line.
    findings = check_findings_inbox()
    if findings:
        section("4b. FINDINGS RECEIVED (filed by other workspaces)")
        print(f"  📥 {len(findings)} finding(s) sent to this workspace:\n")
        for i, f in enumerate(findings, 1):
            print(f"  [{i}] from {f['origin']} — {f['title']}")
            print(f"       {f['file']}")
        print("\n  Read one: `just finding-show <name>` · list: `just findings`")

    # TickTick Pending
    section("5. TICKTICK — PENDING TASKS (AWAITING YOUR APPROVAL)")
    pending = get_pending_tasks()
    if not pending:
        print("  📭 No (Pending) tasks found in activeContext.md.")
        print("  Note: To sync to TickTick, mark a task (Pending) with a PH1-T01 ID prefix and subtasks.")
    else:
        print(f"  📋 {len(pending)} task(s) ready to sync — AWAITING USER APPROVAL:\n")
        for i, task in enumerate(pending, 1):
            reminder = f"   ⏰ {task['due']}" if task.get("due") else "   ⏰ (no reminder set)"
            print(f"  [{i}] {task['id'] or 'NO-ID'}: {task['title']}{reminder}")
            for sub in task["subtasks"]:
                print(f"        • {sub}")
            print()
        print("  ⚠️  AI MUST ask BOTH: \"Which of these do you want in TickTick?\"")
        print("      and \"When should each remind you?\" (e.g. 'tomorrow 9am').")
        print("      Then push with a reminder:  just tt-sync \"PH#-T##,...\" \"tomorrow 9am\"")
        print("      DO NOT auto-sync. A task with no reminder won't notify you.")

    # Summary line for AI
    section("6. AI ACTION REQUIRED")
    actions = []
    if not ok:
        actions.append("Run `just freshen-memory` (stale memory files)")
    if ev_status != "PASS":
        actions.append("Run `just verify-safe` (evidence.json is not valid)")
    if tg_notes:
        actions.append(f"Review {len(tg_notes)} Telegram note(s) for {WORKSPACE_TAG}")
    if findings:
        actions.append(f"Review {len(findings)} finding(s) filed by other workspaces "
                       f"(`just findings`) — each needs a task here to act on")
    if pending:
        actions.append(f"ASK USER which of {len(pending)} Pending task(s) to push to TickTick")

    # PH10-T09 — delegation state, raised from "reported somewhere" to "you are
    # being told to act". Computed here and reused by Section 9 below so the two
    # sections can never disagree about what is waiting.
    #
    # THE 46-WORKSPACE GUARANTEE, stated precisely: the DELEGATION SCAN adds
    # nothing to a workspace that holds no contracts. `delegation_actions` returns
    # [] there and the Section 9 renderer returns "", so neither section gains a
    # line — not a "0 armed · 0 executed" line either, which would have changed
    # every briefing on the fleet to say nothing that was not already true.
    #
    # It does NOT say the whole briefing is byte-identical after this commit: the
    # model/mode declaration below deliberately adds a Section 6 action and a
    # Section 9 `Mode:` line everywhere, because the operator asked for exactly
    # that. Two changes shipped together; only one of them claims invisibility,
    # and conflating them would be the overclaim this repo keeps paying for.
    #
    # The `except` is not defensive habit — this runs inside the SessionStart
    # hook, where a raise costs the operator the whole briefing.
    deleg_status = None
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import delegation as _dg
        deleg_status = _dg.find_delegation_status(ROOT)
        actions.extend(_dg.delegation_actions(deleg_status))
        dg_mod = _dg
    except Exception:  # noqa: BLE001
        deleg_status = None
        dg_mod = None

    # PH10-T09 — the running model was a suggestion nobody had ever acted on, so
    # the tier system governed nothing on any session where the operator did not
    # happen to type the command. It is a REQUIRED action now.
    #
    # What this line can and cannot do: `session_start` runs non-interactively
    # inside a hook, so it cannot ask. It states the requirement; the AI asks on
    # turn 1 with `AskUserQuestion`. Nothing here CHECKS that the AI complied —
    # do not read this as an enforced gate, the way `gate_check.py` is one. The
    # protocol rule that makes it binding is PH10-T11's scope in AGENTS.md, and
    # is deliberately not restated here.
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import model_registry as _mreg
        _running = _mreg.resolve_running(ROOT)
        if not (_running.get("declared") or {}).get("name"):
            actions.insert(0, "[Turn 1] DECLARE THE RUNNING MODEL — ask the user (or self-report) "
                              "and run `just models-set <name>`. Until then this session resolves "
                              "to the most restrictive tier and its permissions mean nothing.")
        elif not _running.get("mode"):
            actions.insert(0, f"[Turn 1] ASK THE USER whether this session is here to PLAN or to "
                              f"EXECUTE — {_running['name']} is declared, its mode is not.")
    except Exception:  # noqa: BLE001
        pass

    if not actions:
        print("  ✅ All systems nominal. Ready for task execution.")
    else:
        for a in actions:
            print(f"  ➤ {a}")

    # Context token budget — what this session has already spent before starting
    section("7. CONTEXT TOKEN BUDGET (~150k)")
    tokens_ok = True
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import token_budget as tbg
        rep = tbg.report(root=ROOT)
        print(f"  📊 {tbg.brief_line(rep)}")
        heaviest = sorted(rep["boot"], key=lambda e: -e["tokens"])[:3]
        print("     heaviest: " + " · ".join(
            f"{Path(e['path']).name} {e['tokens']/1000:.1f}k" for e in heaviest))
        if rep["oversized"]:
            tokens_ok = False
            names = ", ".join(f"{Path(e['path']).name} ({e['lines']} ln)"
                              for e in rep["oversized"])
            print(f"  ⚠️  Over the ~{tbg.HOT_LINE_BUDGET}-line hot budget: {names}")
            print("     → Run `just archive-memory` (dry run), then `--apply`.")
        print(f"     Full breakdown: `just tokens` · estimate ±{tbg.ACCURACY_PCT}%")
    except Exception as exc:  # noqa: BLE001
        print(f"  ℹ️  Token estimate unavailable ({exc}).")

    # Session Work Budget — start (or resume) this session's counter + show the rule
    section("8. SESSION WORK BUDGET (hard limit)")
    # PH26-T01 — the cap is a per-workspace declaration now, so the briefing names
    # which profile produced it. A raised cap the operator cannot see in the opening
    # is a raised cap nobody agreed to; `session_budget.render` prints the same line
    # from the same resolver, so the two can never disagree about what is active.
    try:
        import workspace_profile as _wprof
        _r = _wprof.resolve(ROOT)
        print(f"  🎚️  profile: {_wprof.label(ROOT)}")
        if not _r["ok"] or _r["expired"]:
            print(f"     ⚠️  {_r['reason']}")
        elif _r["fast_closure"]:
            print('     closure: `just wrap "…"` → read the diff → `just self-review '
                  'pass "…"` → `just land "…" "…"`')
    except Exception:  # noqa: BLE001
        pass
    budget = ROOT / "scripts" / "session_budget.py"
    if budget.exists():
        try:
            # The child writes straight to the fd while our own prints sit in a
            # block buffer whenever stdout is a pipe — which is exactly what the
            # hook gives us. Without this flush the budget verdict surfaces at
            # line 1, above the banner, instead of here under its own heading.
            sys.stdout.flush()
            if "--no-reset" in sys.argv:
                subprocess.run([sys.executable, str(budget), "status"], check=False)
                print("  ℹ️  --no-reset: briefing only, the counter was not touched.")
            else:
                cmd = [sys.executable, str(budget), "start"]
                source = hook_source()
                if source:
                    cmd += ["--source", source]
                subprocess.run(cmd, check=False)
        except Exception:
            pass

    # PH22-T05: the session that just ended may have left finished work behind.
    # `session_budget` names the ids; this adds the half only the briefing can
    # measure — whether that work is still sitting uncommitted in the tree.
    # Rendered here rather than in section 6 deliberately: it is about the budget
    # that was just reset, and it must sit beside the counter that replaced it.
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import session_budget as sbud
        prior = (sbud.load_raw() or {}).get("prior") or {}
        if prior.get("verdict") == "unclosed":
            print()
            print("  ⚠️  THE LAST SESSION DID THE WORK AND NEVER CLOSED IT.")
            print(f"     credited: {', '.join(prior.get('work') or ['—'])}")
            print(f"     never ran: {', '.join(prior.get('missing') or ['—'])}")
            dirty = uncommitted_paths()
            if dirty is None:
                # NOT 0. An unreadable tree reported as "clean" is this repo's
                # signature defect — the error path returning the success value.
                print("     tree: could not be read — run `git status` yourself before trusting this.")
            elif dirty:
                print(f"     tree: {dirty} uncommitted path(s) — that work exists ONLY here.")
                print("     → `just verify-safe` → self-review → `just commit-all` FIRST.")
            else:
                print("     tree: clean — the work was committed; only the record was lost.")
    except Exception:
        pass

    # PH26-T03: the same resolver `session_budget.render` uses, so the imperative
    # sentence and the fraction four lines above it can never disagree. Through
    # `session_budget.work_max` rather than `workspace_profile` directly: that wrapper
    # already swallows a broken resolver down to the kernel default, and this runs
    # inside the SessionStart hook where a raise costs the operator the whole briefing.
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import session_budget as _sbud
        _cap = _sbud.work_max(ROOT)
    except Exception:  # noqa: BLE001
        _cap = 2
    print(f"  Rule: complete at most {_cap} WORK tasks from tasks.md this session,")
    print("  then 3 CLOSURE tasks (git-push · docs/memory update · issue triage),")
    print("  then `just handover \"<next>\"` and START A NEW SESSION.")
    print("  → `just work-done \"PH#-T##\"` after each task · `just close <kind>` for closure.")

    # PH10-T01: the running model, its tier, and what that tier may do —
    # scarce Opus hours governed rather than felt.
    section("9. DELEGATION — RUNNING MODEL")
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import model_registry as mreg
        _resolved = mreg.resolve_running(ROOT)
        print(mreg.render_running(_resolved))
        # None means UNDECLARED. Printed as such rather than defaulted to a mode,
        # for the same reason the tier is never guessed from a name.
        print(f"   Mode: {_resolved.get('mode') or 'not declared — ask the user (plan / execute)'}")
        # PH10-T09: reuses the scan computed in Section 6 — one read, one answer.
        # Empty string when nothing is waiting, which is what keeps the 46
        # workspaces that hold no contracts byte-for-byte unchanged.
        if deleg_status is not None:
            _block = dg_mod.render_delegation_status(deleg_status) if dg_mod else ""
            if _block:
                print(_block)
        print("  → `just models` lists the registry · `just models-set <name>` declares one.")
    except Exception as exc:  # noqa: BLE001
        print(f"  ℹ️  Model registry unavailable ({exc}).")

    print()
    print("  💡 Run `just session-end --summary 'summary'` when done.")
    print("  💡 Run `just note-issue 'title' 'desc'` to log any issue you notice.")
    print("  💡 Budget reached? → `just handover \"next step\"` → new session.")
    print("\n" + "═"*50 + "\n")


if __name__ == "__main__":
    main()
