#!/usr/bin/env python3
"""write_journal.py — PostToolUse: record which paths THIS session wrote (PH16-T24).

## Why this exists

`commit_scope` (PH16-T22 rule D) decides what `commit-all` may stage by comparing
each file's mtime to the session lock's `acquired_at`. **mtime answers "was this
changed *during* my session". It never answers "was it changed *by* my
session."** The two differ exactly when a second writer is present — which is the
only situation rule (D) exists for. Both directions were observed in one week:

  * 2026-08-14 (b) — a peer session wrote three files fourteen minutes into this
    session's window. By the rule they were this session's, and would have been
    committed under its name and its self-review hash.
  * 2026-08-14 (c) — the mirror image: a `/clear` rotated the conversation id,
    and a window reset there would have dropped this session's own earlier work
    out of its own commit.

The author was always knowable — the harness fires `PostToolUse` after every
write with the path it wrote. It was simply never written down. This module
writes it down, as an adapter on an existing contract, the same shape
`policy_hook.py` already has on `PreToolUse`.

  stdin ← PostToolUse payload (tool_name + tool_input)
  exit  → **always 0**

## This hook may never block a write

`policy_hook` refuses by design; this one must not, ever. A journal that loses an
entry costs one staged path, which is visible in the next `git status`. A journal
that fails a write costs the session. Every error path here — unreadable payload,
unwritable file, unresolvable identity — records nothing and exits 0.

## Whose journal

The entry is written to the journal of the **session's own workspace**
(`CLAUDE_PROJECT_DIR`), not of the file's workspace. That is what makes the
evidence negative as well as positive: a session in `@life` editing this tree
runs `@life`'s hook and appends to `@life`'s journal, so its writes are *absent*
from ours — and absence, in a journal the hook demonstrably wrote to during this
tenure, is evidence rather than ignorance.

## "No coverage" is not "wrote nothing"

`paths_for()` returns `None` when the journal holds no entry for the tenure at
all, and a `set` (possibly empty) when it does. The distinction is the whole
degrade path: `None` means the hook never ran here, so the caller must fall back
to mtime and say so; `set()` means the hook ran and this session wrote none of
the files in question. Collapsing the two would take `commit-all` offline in
every workspace the hook has not reached yet.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

JOURNAL_RELPATH = Path(".ai") / "write-journal.jsonl"

# Tools whose input names a file that was written. Mirrors policy_hook.PATH_KEYS —
# a tool missing from here is a file that silently loses its author.
PATH_KEYS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}

# Above this, the journal is pruned to the current tenure on the next write. It is
# session-local and gitignored, but append-only files still need a ceiling.
MAX_BYTES = 1_000_000


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ws_root() -> Path:
    """The session's own workspace, per the AGENTS.md anti-drift rule."""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root).resolve()
    try:
        import subprocess
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def journal_path(root: Path) -> Path:
    return Path(root) / JOURNAL_RELPATH


def _identity() -> dict:
    """Reuse the lock's resolver — one implementation of "who is this session".

    A second copy here is exactly how the same rule ends up with two answers,
    which is the recurring defect class this workspace polices.
    """
    try:
        import session_lock
        return session_lock.identity()
    except Exception:  # noqa: BLE001
        return {}


def read_entries(root: Path) -> list[dict]:
    """Every readable entry. A corrupt line is skipped, never fatal — a partial
    write during a crash must not cost the surrounding entries."""
    out = []
    try:
        text = journal_path(root).read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return out
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:  # noqa: BLE001
            continue
        if isinstance(item, dict) and item.get("path"):
            out.append(item)
    return out


def _parse(stamp: str):
    try:
        when = datetime.fromisoformat(str(stamp).strip().replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None
    return when.replace(tzinfo=timezone.utc) if when.tzinfo is None else when


def record(root: Path, tool: str, raw_path: str, cwd: Path | None = None) -> dict | None:
    """Append one entry. Returns it, or None when there was nothing to record."""
    key_tool = PATH_KEYS.get(tool)
    if not key_tool or not raw_path:
        return None
    me = _identity()
    session = me.get("session") or ""
    if not session:
        # An unidentifiable session cannot own an entry, and a journal entry with
        # no author is worse than none — it would read as somebody's.
        return None

    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(cwd or Path.cwd()) / path

    entry = {"ts": _now(), "session": session, "pid": me.get("pid") or 0,
             "tool": tool, "path": str(path)}

    target = journal_path(root)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and target.stat().st_size > MAX_BYTES:
            _prune(root)
        with target.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry) + "\n")
    except Exception:  # noqa: BLE001
        return None
    return entry


def _prune(root: Path) -> None:
    """Keep the current tenure. Best effort: a failed prune must not fail a write.

    PH16-T38 — this pruned by `ts >= acquired_at` too, which would delete the very
    entries `paths_for()` now depends on the moment the journal passed `MAX_BYTES`:
    a tenure's own writes from before it took the lock. It keeps two things now:

      * every entry from this tenure, **by session id**, whenever it was written;
      * every entry from anyone else that is no older than this tenure's first
        write — because a stranger's *later* write to a path is the evidence
        `contested()` uses to stop this session committing that stranger's bytes.
        Pruning strangers indiscriminately would silently disarm that guard.
    """
    try:
        entries = read_entries(root)
        tenure = tenure_sessions(root)
        ours = [e for e in entries if e.get("session") in tenure]
        if not ours:
            return
        stamps = [t for t in (_parse(e.get("ts", "")) for e in ours) if t]
        floor = min(stamps) if stamps else None
        kept = [e for e in entries
                if e.get("session") in tenure
                or floor is None
                or (_parse(e.get("ts", "")) or floor) >= floor]
        journal_path(root).write_text(
            "".join(json.dumps(e) + "\n" for e in kept), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def paths_for(root: Path, sessions, since: str) -> set[str] | None:
    """Repo-relative paths this tenure wrote — or None when there is no coverage.

    `sessions` is every identity that has held this tenure: the lock's holder
    plus its `lineage`, because `/clear` rotates the id mid-process (PH16-T23).
    Reading only the current id would make a session a stranger to its own
    earlier work.

    ## Identity decides, not the clock (PH16-T38)

    This used to keep only entries stamped `ts >= since`, `since` being the
    lock's `acquired_at`. That silently answered a different question. An entry
    already carries the **session id the harness recorded at the moment of the
    write** — direct evidence of authorship — and the timestamp is at best a
    proxy for it. Filtering by the proxy and discarding the evidence loses work
    exactly when `acquired_at` postdates it, which is the ordinary case: `just
    session-start` takes the lock with both streams discarded and the failure
    swallowed (`justfile:27`), so a session opening while a previous holder is
    still live runs with no lock at all and only reclaims one at credit time.

    Measured on this workspace's own journal, 2026-08-16: 25 entries covering 8
    distinct paths — `scripts/commit_scope.py` and `tests/test_commit_scope.py`
    among them — were dropped from the commit of the session that wrote them.
    Whichever of them `verify-safe`'s pre-commit fixers happened to rewrite got
    their mtime bumped past the cutoff and survived; the rest did not. That
    arbitrary split produced HEAD `28e5b0e`, which failed its own suite.

    `since` is therefore no longer a filter. It is accepted and ignored, kept in
    the signature because every caller passes it and because removing it would
    make an old caller silently pass a session set as a stamp.

    ## The one thing identity alone gets wrong

    A path this tenure wrote and a **stranger overwrote afterwards** holds the
    stranger's bytes. The journal entry is real and is still not a licence to
    commit them under this session's review hash — that is the PH16-T22
    corruption arriving by a new route. Such a path is dropped, and
    `contested()` names the competing writer so the omission can be explained
    rather than merely noticed.

    ## Coverage

    `None` still means "nobody recorded the authors, fall back to mtime and say
    so". It is now decided by `_covered()`, which **widens** the old test rather
    than replacing it: an entry from this tenure is coverage whenever it was
    written, and the previous rule — any entry inside the window — is kept as a
    second way to qualify. Keeping both matters in opposite directions. The old
    rule alone reported "no coverage" in exactly the case where coverage was
    complete (every entry predating a late-acquired lock), which is how the
    incident degraded to mtime instead of being caught. Identity alone would
    have dropped PH16-T24's deliberate case: a journal holding only *another*
    session's in-window write is still proof the hook ran, so this session's
    absence from it is evidence rather than ignorance.
    """
    root = Path(root)
    wanted = {s for s in (sessions or ()) if s}
    entries = read_entries(root)
    if not _covered(entries, wanted, since):
        # Either the hook has never run here, or it has never run for this
        # tenure. Both mean the journal cannot answer — and "cannot answer" must
        # not be returned as "wrote nothing".
        return None
    mine, theirs = _by_path(root, entries, wanted)
    return {rel for rel, ts in mine.items() if rel not in theirs or theirs[rel] <= ts}


def _covered(entries: list, wanted: set, since: str) -> bool:
    """Did the hook run for this tenure at all? Either qualifies (see above).

    Note the path filter is deliberately *not* applied here: a session whose only
    journalled write went into another workspace's tree still demonstrates that
    the hook ran, so `set()` — "covered, and this session wrote nothing stageable
    here" — is the honest answer rather than `None`.
    """
    if any(e.get("session") in wanted for e in entries):
        return True
    cutoff = _parse(since)
    if cutoff is None:
        return False
    return any((_parse(e.get("ts", "")) or cutoff) >= cutoff for e in entries)


def contested(root: Path, sessions, _since: str = "") -> dict:
    """`{path: (my_last_write, their_last_write)}` for paths this tenure wrote
    that a session outside it wrote **later**.

    Exists so the exclusion can be reported with the competing writer named. A
    path that vanishes from a commit with no reason sends the operator to
    `--all`, which is the blanket staging this whole rule replaces.
    """
    root = Path(root)
    wanted = {s for s in (sessions or ()) if s}
    mine, theirs = _by_path(root, read_entries(root), wanted)
    return {rel: (ts, theirs[rel]) for rel, ts in mine.items()
            if rel in theirs and theirs[rel] > ts}


def _by_path(root: Path, entries: list, wanted: set):
    """(mine, theirs): repo-relative path → the LAST write by this tenure, and by
    anyone else. One pass, so both readers above agree by construction."""
    root = Path(root).resolve()
    mine: dict = {}
    theirs: dict = {}
    for entry in entries:
        try:
            rel = str(Path(entry["path"]).resolve().relative_to(root))
        except Exception:  # noqa: BLE001
            continue    # a path outside this workspace is not one it can stage
        when = _parse(entry.get("ts", ""))
        if when is None:
            # An unreadable stamp cannot order anything. Counted as this
            # tenure's write when the id matches (identity is what we trust
            # here) and, for a stranger, treated as the newest possible write so
            # the contested guard fails safe rather than open.
            when = datetime.min.replace(tzinfo=timezone.utc)
            if entry.get("session") not in wanted:
                when = datetime.max.replace(tzinfo=timezone.utc)
        bucket = mine if entry.get("session") in wanted else theirs
        if when > bucket.get(rel, datetime.min.replace(tzinfo=timezone.utc)):
            bucket[rel] = when
    return mine, theirs


def tenure_sessions(root: Path) -> set[str]:
    """The lock holder plus every identity it has adopted (PH16-T23 `lineage`)."""
    try:
        import session_lock
        lock = session_lock.read_lock(Path(root))
    except Exception:  # noqa: BLE001
        return set()
    if not lock:
        return set()
    known = {str(lock.get("session") or "")}
    known.update(str(s) for s in (lock.get("lineage") or []))
    return {s for s in known if s}


def main(argv=None) -> int:
    """Always 0. See the module docstring: this hook may not block a write."""
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return 0
        tool = str(payload.get("tool_name") or "")
        tool_input = payload.get("tool_input") or {}
        if not isinstance(tool_input, dict):
            return 0
        key = PATH_KEYS.get(tool)
        if not key:
            return 0
        record(ws_root(), tool, str(tool_input.get(key) or ""))
    except Exception:  # noqa: BLE001
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
