#!/usr/bin/env python3
"""
self_review.py — record and verify a self-review of the session's own diff (PH7-T04).

A senior engineer never pushes an unreviewed diff. The `git-push` closure task
was the one closure step with a real external side effect and no requirement to
have *looked* at what was going out.

## The record is bound to the diff, not to a promise

The failure mode this has to survive is the one PH7-T02 and PH6-T13 already hit
twice: a claim that names nothing specific is unfalsifiable, so it drifts into
being narrated. `"reviewed: yes"` is that claim.

So a review record stores the **sha256 of the exact diff it reviewed**. The check
recomputes the digest at close time and demands a record that matches it. The
consequences fall out for free:

  • review early, keep editing → the digest moves, the record no longer covers
    the diff, `close git-push` refuses again;
  • review a *different* branch/range → different digest, no coverage;
  • fix a finding → the fix itself changes the digest, forcing a re-review.

**What this does NOT bind:** that the review was any good. Nothing mechanical can
check that a human or an agent actually read the hunks. What it binds is that a
review was recorded against *this exact content*, at a stated time, by a stated
reviewer — a specific, falsifiable, non-transferable claim instead of a vague
one. The craft half lives in the `self-review-diff` skill; this is the ratchet
that makes skipping it visible.

## What "the session's own diff" means

Everything this session would put on the remote: commits made since the session
started **plus** the working tree, including untracked files (`just commit-all`
runs `git add -A`, so they ship too).

It is computed by staging everything into a **temporary index** and diffing that
against the base — i.e. git's own rendering of "what this looks like committed".
That is what keeps the digest identical before and after `commit-all`, which the
workflow requires: the diff is reviewed, then committed, then pushed, and only
then is the closure recorded. See `_staged_diff`.

The base is resolved in this order, and which one was used is recorded and
printed rather than assumed:

  1. `head_at_start` from `.ai/session-state.json` — the true session base,
     stamped by `session_budget.py start`.
  2. `@{upstream}` — everything unpushed. Correct when the review runs before the
     push; wrong afterwards, which is why it is not first.
  3. `HEAD` — the working tree only. Weakest, and the reason an *empty* diff from
     a fallback base is refused rather than waved through: the protocol runs
     `close git-push` **after** the push, so "no diff" from an unknown base is
     far more likely to mean "already pushed" than "nothing was done".

Exit codes: 0 covered · 1 usage · 6 no review covers the current diff.

Usage:
  self_review.py status                      # what would be reviewed + coverage
  self_review.py diff                        # print the exact text under review
  self_review.py record --verdict pass --note "…" [--finding "…"]…
  self_review.py check                       # exit 6 if the diff is unreviewed
"""

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REVIEW_DIRNAME = Path(".ai") / "reviews"
STATE_REL = Path(".ai") / "session-state.json"
EXIT_UNREVIEWED = 6
VERDICTS = ("pass", "pass-with-findings", "fail")
PASSING = ("pass", "pass-with-findings")


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _git_ok(root: Path, *args: str, env: dict | None = None) -> tuple[bool, str]:
    """Run git in `root`, keeping success and output separate.

    The distinction matters: a *failed* `git diff` also produces no output, and
    treating that as "empty diff" would report "nothing to review" — opening the
    closure precisely when the tool could not see what changed.
    """
    try:
        return True, subprocess.check_output(["git", "-C", str(root), *args],
                                             stderr=subprocess.DEVNULL, text=True,
                                             env=env)
    except Exception:  # noqa: BLE001
        return False, ""


def _git(root: Path, *args: str) -> str:
    """Output, or "" on failure — for callers where absent and empty are the same."""
    return _git_ok(root, *args)[1]


def _rev(root: Path, ref: str) -> str:
    """Resolve `ref` to a commit, or "" if it is not one.

    `^{commit}` is what makes this a real existence check: `git rev-parse
    --verify` echoes a well-formed 40-hex string back even when no such object
    exists, so a stale `head_at_start` would have been accepted as a valid base
    and every subsequent git call against it would quietly fail.
    """
    return _git(root, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}").strip()


def empty_tree(root: Path) -> str:
    """Git's empty tree object for THIS repository's hash algorithm (PH26-T02).

    Computed, never hardcoded. `4b825dc642cb6eb9a060e54bf8d69288fbee4904` is the
    SHA-1 answer and is the one everybody pastes; a SHA-256 repository has a
    different one, and a plausible-looking wrong hash here would fail as
    "git failed" — the exact symptom this function exists to end. `hash-object`
    without `-w` computes without writing anything.
    """
    ok, out = _git_ok(root, "hash-object", "-t", "tree", os.devnull)
    return out.strip() if ok else ""


# ── The base of the session's diff ────────────────────────────────────────────
def resolve_base(root: Path, explicit: str = "") -> dict:
    """Where the session's diff starts. `{base, ref, source, authoritative}`.

    `authoritative` is True only for a base we can prove is the session's start
    (explicit or recorded); a fallback is a guess and is labelled as one.
    """
    if explicit:
        sha = _rev(root, explicit)
        return {"base": sha or explicit, "ref": explicit, "source": "explicit",
                "authoritative": bool(sha)}

    try:
        state = json.loads((root / STATE_REL).read_text())
    except Exception:  # noqa: BLE001
        state = {}
    recorded = str(state.get("head_at_start") or "").strip()
    if recorded and _rev(root, recorded):
        return {"base": recorded, "ref": recorded, "source": "session",
                "authoritative": True}

    upstream = _rev(root, "@{upstream}")
    if upstream:
        return {"base": upstream, "ref": "@{upstream}", "source": "upstream",
                "authoritative": False}

    head = _rev(root, "HEAD")
    if head:
        return {"base": head, "ref": "HEAD", "source": "head", "authoritative": False}

    # No commits at all — a repository's first commit (PH26-T02, filed by @zenithos).
    # The diff is *everything against nothing*, which git can express: the empty
    # tree. Before this, `read-tree HEAD` failed, `check()` reported "git failed",
    # and `close git-push` could only be satisfied by an override — so the first
    # thing every newly-onboarded workspace learned about the strongest gate in
    # this OS was that it can be waved through.
    #
    # `authoritative: True`, unlike the two fallbacks above, and that is the whole
    # point rather than an oversight: when nothing has ever been committed,
    # everything in the tree IS exactly what this session would push. It is
    # provable, not guessed. Labelling it a guess would leave a genuinely empty new
    # repository permanently unclosable, since `check()` reads this field to tell
    # "nothing changed" from "I cannot tell".
    tree = empty_tree(root)
    if tree:
        return {"base": tree, "ref": "the empty tree", "source": "empty-tree",
                "authoritative": True}
    return {"base": "", "ref": "HEAD", "source": "head", "authoritative": False}


# ── The diff itself ───────────────────────────────────────────────────────────
# Never part of the diff under review, regardless of the workspace's .gitignore:
# a record is a file, so counting it would change the very digest it was written
# to cover and every review would void itself the instant it was made.
#
# Removed from the temp index *after* staging rather than excluded during it. A
# `:(exclude)` pathspec reads to git as explicitly naming the path, so `git add`
# refuses the whole batch with "the following paths are ignored" the moment a
# record exists — i.e. from the second review onward, in every workspace.
EXCLUDE_PATH = REVIEW_DIRNAME.as_posix()


def _staged_diff(root: Path, base: str, *args: str,
                  paths: tuple[str, ...] = ()) -> tuple[bool, str]:
    """`git diff base` as if everything had already been committed.

    `paths`, when given, restricts the diff to those pathspecs — appended after
    the trailing `--` below, so the default (`paths=()`) is byte-identical to
    every call site that predates PH27-T12: an empty pathspec after `--` means
    "no restriction", not "match nothing".

    **Why a temporary index.** The digest must be identical before and after
    `just commit-all`, because the protocol reviews the diff and *then* commits
    and pushes it. Rendering untracked files by hand does not survive that: once
    staged, git emits `new file mode` / `index` headers that no hand-built hunk
    matches, so committing silently voided the review. Found by dogfooding —
    `close git-push` refused the very session that shipped it.

    Staging into `GIT_INDEX_FILE` gives git's own answer to "what would this look
    like committed?" without touching the real index. `git add` does write blobs
    for new content into `.git/objects`; they are unreferenced and collected by
    routine gc, the same as any staged-then-discarded change.
    """
    with tempfile.TemporaryDirectory() as tmp:
        env = {**os.environ, "GIT_INDEX_FILE": str(Path(tmp) / "index")}
        # Seeded from HEAD so `add -A` overlays the worktree on top of what is
        # already committed. With no commits there is nothing to seed from, and the
        # empty tree is the honest starting point rather than an error (PH26-T02).
        seed = _rev(root, "HEAD") or empty_tree(root)
        if not seed:
            return False, ""
        for cmd in (["read-tree", seed], ["add", "-A", "--", "."]):
            if not _git_ok(root, *cmd, env=env)[0]:
                return False, ""
        # Safety net for a workspace whose .gitignore lacks the entry; a no-op
        # otherwise. `--ignore-unmatch` keeps it silent when nothing matches.
        _git_ok(root, "rm", "--cached", "-r", "-q", "--ignore-unmatch", "--",
                EXCLUDE_PATH, env=env)
        return _git_ok(root, "diff", "--cached", "--no-color", *args, base, "--",
                       *paths, env=env)


def session_diff(root: Path, base: str) -> str:
    """The canonical text under review: everything that would be pushed."""
    return _staged_diff(root, base)[1]


def _session_diff(root: Path, base: str) -> tuple[bool, str]:
    """`(git-diff-succeeded, text)`. See `_git_ok` for why the flag is carried."""
    return _staged_diff(root, base)


def _numstat(root: Path, base: str) -> tuple[int, int, int]:
    files = adds = dels = 0
    for line in _staged_diff(root, base, "--numstat")[1].splitlines():
        cols = line.split("\t")
        if len(cols) < 3:
            continue
        files += 1
        if cols[0].isdigit():
            adds += int(cols[0])
        if cols[1].isdigit():
            dels += int(cols[1])
    return files, adds, dels


# ── Compact rendering (PH27-T12) ────────────────────────────────────────────
# A view, never a narrower thing under review: nothing here ever feeds
# digest()/record()/check() — those call _staged_diff()/_session_diff() with no
# `paths` restriction, exactly as before this task existed. Compact rendering
# only changes what `diff` prints to a human, never what the ship/land gate
# hashes.

# Extensions treated as low-risk to skim — generated, documentation, or data,
# never code a reviewer must read line-by-line. An extensionless file (e.g.
# `justfile`) or any extension not listed here always renders full: the
# categorizer errs toward showing more, not toward guessing something is safe
# to compact.
LOW_RISK_EXTENSIONS = {
    ".md", ".json", ".yaml", ".yml", ".jsonl", ".csv", ".txt", ".lock", ".html",
}


def _categorize(paths: list[str]) -> tuple[list[str], list[str]]:
    """`(full-hunk paths, stat-only paths)`."""
    full, stat_only = [], []
    for p in paths:
        (stat_only if Path(p).suffix.lower() in LOW_RISK_EXTENSIONS else full).append(p)
    return full, stat_only


def compact_diff(root: Path, base: str, full: bool = False) -> str:
    """The text `just review-diff` shows by default: full hunks for source and
    script files, a `git diff --stat` line for generated/doc/data files.

    `full=True` reproduces `session_diff()` exactly — the pre-PH27-T12 output,
    byte for byte — for the reviewer who wants to see everything.
    """
    if full:
        return session_diff(root, base)

    ok, name_text = _staged_diff(root, base, "--name-only")
    if not ok or not name_text.strip():
        return ""
    changed = [p for p in name_text.splitlines() if p.strip()]
    full_paths, stat_paths = _categorize(changed)

    parts = []
    if full_paths:
        parts.append(_staged_diff(root, base, paths=tuple(full_paths))[1])
    if stat_paths:
        _, stat_text = _staged_diff(root, base, "--stat", paths=tuple(stat_paths))
        parts.append(
            "── compact: generated/doc/data file(s) — stat only "
            "(`just review-diff \"\" full` for hunks) ──\n" + stat_text
        )
    return "\n".join(p for p in parts if p)


def digest(root: Path | None = None, explicit_base: str = "") -> dict:
    """Identify the current session diff: sha256 + stats + how the base was found."""
    root = root or ws_root()
    info = resolve_base(root, explicit_base)
    ok, text = _session_diff(root, info["base"])
    # The staged diff already contains untracked files, so `_numstat` counts them
    # too — the manual tally this used to add on top double-counted every new file.
    files, adds, dels = _numstat(root, info["base"])
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "base": info["base"],
        "base_ref": info["ref"],
        "base_source": info["source"],
        "authoritative": info["authoritative"],
        "head": _rev(root, "HEAD"),
        "files": files,
        "insertions": adds,
        "deletions": dels,
        "empty": not text.strip(),
        "readable": ok,
        "bytes": len(text),
    }


# ── Records ───────────────────────────────────────────────────────────────────
def read_reviews(root: Path | None = None) -> list[dict]:
    """Every recorded review, newest first. Unreadable files are skipped, never
    fatal — a corrupt record must not be able to *open* the gate or crash close."""
    root = root or ws_root()
    directory = root / REVIEW_DIRNAME
    if not directory.is_dir():
        return []
    out = []
    for path in directory.glob("*.json"):
        try:
            data = json.loads(path.read_text(errors="replace"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and data.get("diff_sha256"):
            data["_path"] = str(path.relative_to(root))
            out.append(data)
    return sorted(out, key=lambda d: str(d.get("recorded_at", "")), reverse=True)


def record(root: Path | None = None, *, verdict: str, note: str,
           findings: list[str] | None = None, reviewer: str = "claude",
           tool: str = "manual", explicit_base: str = "") -> dict:
    """Write a review record bound to the current diff. Returns the record."""
    root = root or ws_root()
    d = digest(root, explicit_base)
    now = datetime.now(timezone.utc)
    entry = {
        "recorded_at": now.isoformat(),
        "reviewer": reviewer,
        "tool": tool,
        "verdict": verdict,
        "note": note.strip(),
        "findings": list(findings or []),
        "diff_sha256": d["sha256"],
        "base": d["base"],
        "base_ref": d["base_ref"],
        "base_source": d["base_source"],
        "head": d["head"],
        "files": d["files"],
        "insertions": d["insertions"],
        "deletions": d["deletions"],
    }
    directory = root / REVIEW_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now.strftime('%Y-%m-%d-%H%M%S')}-{d['sha256'][:12]}.json"
    path.write_text(json.dumps(entry, indent=2) + "\n")
    entry["_path"] = str(path.relative_to(root))
    return entry


def check(root: Path | None = None, explicit_base: str = "") -> dict:
    """Is the current session diff covered by a recorded review?

    `{ok, reason, digest, review}`. Coverage is by content hash, so a review
    recorded in an earlier session still counts if and only if the diff it
    reviewed is byte-for-byte the diff on the table now.
    """
    root = root or ws_root()
    d = digest(root, explicit_base)
    v = {"ok": False, "reason": "", "digest": d, "review": None}

    if not d["readable"]:
        v["reason"] = (f"could not read the diff against {d['base_ref']!r} — git failed. "
                       "An unreadable diff is an unreviewed one; it is never 'no changes'.")
        return v

    if d["empty"]:
        if d["authoritative"]:
            v["ok"] = True
            v["reason"] = (f"nothing to review — no changes since the session base "
                           f"({d['base'][:8]}).")
        else:
            v["reason"] = (
                f"the diff against {d['base_ref']} is empty, but that base is a "
                f"fallback ({d['base_source']}), not the recorded session start — so "
                "'nothing changed' is a guess, not a fact. If the push already "
                "happened, review the range explicitly: "
                "`just self-review-status <base-sha>`.")
        return v

    matches = [r for r in read_reviews(root) if r.get("diff_sha256") == d["sha256"]]
    if not matches:
        prior = read_reviews(root)
        v["reason"] = (
            f"no recorded review covers this diff ({d['files']} file(s), "
            f"+{d['insertions']}/-{d['deletions']}, sha {d['sha256'][:12]}). "
            + (f"The newest of {len(prior)} record(s) covers "
               f"{str(prior[0].get('diff_sha256'))[:12]} — the diff changed after it "
               "was reviewed, so it no longer applies. "
               if prior else "")
            + "Review the diff, then `just self-review \"verdict\" \"what you checked\"`.")
        return v

    passing = [r for r in matches if r.get("verdict") in PASSING]
    if not passing:
        newest = matches[0]
        v["review"] = newest
        v["reason"] = (
            f"this diff was reviewed at {newest.get('recorded_at')} and the verdict was "
            f"{newest.get('verdict')!r}: {newest.get('note') or 'no note'}. "
            "Fix the findings (which changes the diff) and review again.")
        return v

    v["ok"] = True
    v["review"] = passing[0]
    v["reason"] = (f"reviewed at {passing[0].get('recorded_at')} by "
                   f"{passing[0].get('reviewer')} — verdict {passing[0].get('verdict')}.")
    return v


# ── CLI ───────────────────────────────────────────────────────────────────────
def _print_status(root: Path, explicit_base: str) -> None:
    v = check(root, explicit_base)
    d = v["digest"]
    label = "session start" if d["base_source"] == "session" else f"{d['base_source']} (fallback)"
    print(f"  📋 Session diff — base {d['base'][:8] or '?'} via {d['base_ref']} [{label}]")
    print(f"     {d['files']} file(s) · +{d['insertions']}/-{d['deletions']} · "
          f"sha {d['sha256'][:12]}")
    if v["ok"]:
        print(f"  ✅ REVIEWED — {v['reason']}")
        if v["review"] and v["review"].get("findings"):
            for f in v["review"]["findings"]:
                print(f"     • {f}")
    else:
        print(f"  🛑 UNREVIEWED — {v['reason']}")


def main() -> int:
    ap = argparse.ArgumentParser(description="Record/verify a self-review of the session diff.")
    ap.add_argument("command", nargs="?", default="status",
                    choices=["status", "check", "record", "diff"])
    ap.add_argument("--verdict", choices=VERDICTS)
    ap.add_argument("--note", default="", help="what you actually checked")
    ap.add_argument("--finding", action="append", default=[],
                    help='repeatable, e.g. --finding "medium: unbounded read in x.py:42"')
    ap.add_argument("--reviewer", default="claude")
    ap.add_argument("--tool", default="manual", help="e.g. /code-review, /security-review")
    ap.add_argument("--base", default="", help="review against this ref instead of the session base")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--full", action="store_true",
                    help="show full hunks for every file, including generated/doc/data "
                         "(default: compact — stat-only for low-risk file types)")
    args = ap.parse_args()
    root = ws_root()

    if args.command == "diff":
        base = resolve_base(root, args.base)["base"]
        sys.stdout.write(compact_diff(root, base, full=args.full))
        return 0

    if args.command == "record":
        if not args.verdict:
            print("❌ --verdict is required: " + " | ".join(VERDICTS), file=sys.stderr)
            return 1
        if not args.note.strip():
            print("❌ --note is required — say what you actually checked. A review "
                  "with no words is the unfalsifiable claim this exists to prevent.",
                  file=sys.stderr)
            return 1
        if args.verdict != "pass" and not args.finding:
            print(f"❌ verdict {args.verdict!r} needs at least one --finding.", file=sys.stderr)
            return 1
        if args.verdict == "pass" and args.finding:
            print("❌ verdict 'pass' with findings is contradictory — use "
                  "'pass-with-findings' (accepted/deferred) or 'fail' (must fix).",
                  file=sys.stderr)
            return 1
        d = digest(root, args.base)
        if d["empty"]:
            print("❌ there is no diff to review against "
                  f"{d['base_ref']} — nothing would be recorded.", file=sys.stderr)
            return 1
        entry = record(root, verdict=args.verdict, note=args.note,
                       findings=args.finding, reviewer=args.reviewer,
                       tool=args.tool, explicit_base=args.base)
        if args.json:
            print(json.dumps(entry, indent=2))
            return 0
        print(f"  ✅ Review recorded — {entry['_path']}")
        print(f"     verdict {entry['verdict']} · {entry['files']} file(s) · "
              f"+{entry['insertions']}/-{entry['deletions']} · sha {entry['diff_sha256'][:12]}")
        for f in entry["findings"]:
            print(f"     • {f}")
        print("     Any further edit changes the diff and voids this record.")
        return 0

    v = check(root, args.base)
    if args.json:
        out = dict(v)
        print(json.dumps(out, indent=2, default=str))
    elif args.command == "check":
        print(("  ✅ " if v["ok"] else "  🛑 ") + v["reason"])
    else:
        _print_status(root, args.base)
    return 0 if v["ok"] else EXIT_UNREVIEWED


if __name__ == "__main__":
    sys.exit(main())
