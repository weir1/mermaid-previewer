#!/usr/bin/env python3
"""doc_stamp.py — every doc states what it is true for, and none of it is typed (PH22-T07).

## Why this exists

A reader of `doc/architecture.md` could not tell whether it described the OS they
were running or the one from three versions ago. Measured 2026-08-16 across
`doc/`, `.ai/docs/`, `.ai/memory-bank/` and the repo root: **32 documents, 12
with any frontmatter at all, in two incompatible schemas, and not one carrying a
version or an update count.**

That is this workspace's own recurring defect — a claim nothing re-checks — turned
on its documentation. It is not hypothetical: on 2026-08-15
`doc/fastest_execution_philosophy.md` had to be read against the source by hand to
discover that three of its nine pillars were built, five were half-built and one
did not exist. Nothing in the file had said it was a proposal.

## Three fields, all derived

    version:        the OS version the doc was last stamped against
    updated:        the last commit that touched it, in IST
    revisions:      how many commits have ever touched it
    updated_basis:  `git`, or `mtime` when git has never seen the file

**Derived is the entire design.** A hand-maintained `revisions:` counter is a lie
with a schedule: nobody increments it, the first stale value is invisible, and
after that every reader correctly ignores the field. Because these come out of
git, the field cannot be wrong without `--check` saying so — which is the only
thing that makes it worth reading. Same rule as `evidence.json` and
`.ai/codemap.md`: generated, never hand-written.

`version` is the one field that is *stamped* rather than continuously recomputed,
and the distinction is deliberate. Its meaning is "this document was last brought
into line with OS v3.5.0". Recomputing it on every run would silently re-assert
that a check had happened, which is the forgery this file is built against.

## Why `revisions` is checked as a window, not an equality

Writing a stamp modifies the file, and that modification is itself committed —
so a stamp that equalled the commit count when written is one behind the moment
it lands. **Requiring equality would make the field unstable by construction**,
reporting drift on every doc after every commit forever. Found by the check's own
test going red, not reasoned about in advance.

So `--apply` writes `count + 1` — the count the file will have once the stamp
commit exists — and `--check` accepts `revisions ∈ [count, count + 1]`: equal
after the stamp is committed, one ahead while it is still uncommitted. Anything
outside that window was typed by a human, which is exactly what this is for.

## What it will not do

It does not read the document. A stamp says when a doc last changed and against
which OS — never that its contents are correct. Anything claiming the latter
would be the same unverifiable assertion in a new place.

**`updated` and `version` are refreshed by `--apply` and are not equality-checked**,
for the same circularity reason: every commit moves the last-commit date, so
comparing it would re-flag every file forever. Their accuracy is therefore bounded
by when `--apply` last ran, and that is a real limit rather than a hidden one —
which is why closure runs `just doc-stamps --apply` before committing. Only
`revisions` and `updated_basis` carry a checkable claim.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

IST = timezone(timedelta(hours=5, minutes=30))

#: Directories whose `.md` files are documents. The repo root is included at
#: depth 0 only — `.ai/plans/`, `.ai/prework/` and `.ai/handover/` are working
#: records of a single task, not documents that outlive one, and stamping them
#: would put a revision counter on a file written once and never revised.
DOC_DIRS = ("doc", ".ai/docs", ".ai/memory-bank")

#: Declared, never inferred. An exemption a reader cannot see is an exemption
#: nobody can review — the rule `deploy_refs.KERNEL_ONLY` follows, for the same
#: reason. Each entry states why the file cannot carry a derived stamp.
EXEMPT = {
    "AI_CHANGELOG.md":
        "an append-only log, not a document — every entry already carries its own "
        "IST timestamp by protocol, and a whole-file 'updated' would say only that "
        "somebody appended, which the last entry says better",
    "CLAUDE.md":
        "an agent entrypoint whose entire body is the `@AGENTS.md` import plus notes; "
        "it has no content of its own to be true or stale about, and AGENTS.md — which "
        "it imports — is stamped",
}

STAMP_KEYS = ("version", "updated", "revisions", "updated_basis")


def ws_root() -> Path:
    out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                         capture_output=True, text=True).stdout.strip()
    return Path(out) if out else Path.cwd()


def os_version(root: Path) -> str:
    """The workspace's own OS version, or `unknown` — never a guess.

    A workspace with no `.ai/os_version.json` is one this OS has not been
    deployed to; writing a plausible number there would be inventing the very
    fact the field exists to report.
    """
    try:
        return str(json.loads(
            (root / ".ai" / "os_version.json").read_text(encoding="utf-8")
        ).get("version") or "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def documents(root: Path) -> list:
    """Every `.md` this rule covers, repo-relative and sorted, exemptions removed."""
    found = set()
    for name in sorted(p.name for p in root.glob("*.md") if p.is_file()):
        found.add(name)
    for rel in DOC_DIRS:
        base = root / rel
        if base.is_dir():
            for path in base.glob("*.md"):
                if path.is_file():
                    found.add(str(path.relative_to(root)))
    return sorted(f for f in found if f not in EXEMPT)


def _git(root: Path, *args) -> str:
    run = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 else ""


def dirty_paths(root: Path) -> set:
    """Repo-relative paths with uncommitted changes — staged, unstaged or untracked.

    PH23-T02. The window `[count, count + 1]` is right for a doc nobody is editing
    and wrong for one this session touched: the coming commit adds a revision, so a
    dirty doc stamped `count` lands one *below* the window the moment it ships. That
    is the entire "committing invalidates the stamps" cascade the closure audit
    recorded — not a circularity, just the one case the window cannot cover, because
    for a dirty doc the acceptable value is a single number rather than a range.

    Consulted by `check` and `apply` through the same helper so the two cannot
    disagree about what "dirty" means — the discipline `verdict()` already enforces
    for what "stamped" means.
    """
    # `-uall` is load-bearing: git's default collapses an untracked DIRECTORY to a
    # single `doc/` entry instead of naming the files inside it, so a whole folder
    # of new docs would read as clean and every one of them would drift on the
    # commit that created it. Invisible in this repo, where `doc/` is long since
    # tracked; found by a fixture that starts from nothing.
    out = subprocess.run(["git", "status", "--porcelain", "-uall", "--"], cwd=root,
                         capture_output=True, text=True)
    if out.returncode != 0:
        return set()
    paths = set()
    for line in out.stdout.splitlines():
        if len(line) < 4:
            continue
        rel = line[3:]
        # A rename reads `XY old -> new`; the new name is the one on disk.
        if " -> " in rel:
            rel = rel.split(" -> ", 1)[1]
        paths.add(rel.strip().strip('"'))
    return paths


def derive(root: Path, rel: Path | str, dirty: bool = False) -> dict:
    """The three fields, read from git. Never from the file being stamped.

    `dirty` says this file is going into the coming commit, and the fields are
    derived for the state *after* it — the same anticipation `apply` already
    applies to `revisions` (PH23-T02). Without it every brand-new doc drifted on
    the very commit that created it: `updated_basis` is honestly `mtime` while git
    has no record of the file, and becomes `git` the instant the commit lands.
    """
    root, rel = Path(root), str(rel)
    count = _git(root, "rev-list", "--count", "HEAD", "--", rel)
    revisions = int(count) if count.isdigit() else 0

    if revisions:
        when = _git(root, "log", "-1", "--format=%cI", "--", rel)
        basis = "git"
    else:
        # git has never recorded this file. `revisions: 0` is the honest count,
        # and the filesystem time is reported AS a filesystem time — presenting
        # an mtime as a commit date is the "no data reported as 0" error this
        # workspace keeps hitting.
        #
        # `dirty` is the one exception, and it is not a softening: the file is
        # staged or waiting to be, so git is about to have a record of it. The
        # basis is what will be true when the stamp is read, not when it is
        # written. `updated` stays the mtime — it is the field AGENTS.md calls
        # refreshed rather than verified, and the mtime of a file about to be
        # committed is within seconds of its commit date.
        when, basis = "", ("git" if dirty else "mtime")
        try:
            when = datetime.fromtimestamp(
                (root / rel).stat().st_mtime, timezone.utc).isoformat()
        except OSError:
            pass

    stamp = "unknown"
    if when:
        try:
            stamp = datetime.fromisoformat(when).astimezone(IST).strftime(
                "%Y-%m-%d %H:%M IST")
        except ValueError:
            stamp = "unknown"

    return {"version": os_version(root), "updated": stamp,
            "revisions": revisions, "updated_basis": basis}


def split(text: str):
    """(frontmatter_lines, body). A document with no frontmatter yields `[]`."""
    if not text.startswith("---\n"):
        return [], text
    end = text.find("\n---\n", 3)
    if end == -1:
        return [], text
    return text[4:end + 1].splitlines(), text[end + 5:]


def _existing(front: list) -> dict:
    out = {}
    for line in front:
        key, sep, value = line.partition(":")
        if sep and not key.startswith((" ", "\t", "#")):
            out[key.strip()] = value.strip()
    return out


def render(text: str, want: dict) -> str:
    """The file with its stamp set, every other frontmatter line preserved in
    place. Pre-existing keys are not reordered or reformatted: the memory bank's
    `last_verified` / `ttl_days` block is read by real tooling, and a rewrite
    that 'tidies' it is a rewrite that breaks it."""
    front, body = split(text)
    kept = [l for l in front
            if (l.partition(":")[0].strip() not in STAMP_KEYS
                or l.startswith((" ", "\t")))]
    stamp = [f"{k}: {want[k]}" for k in STAMP_KEYS]
    return "---\n" + "\n".join(kept + stamp) + "\n---\n" + body


def verdict(have: dict, want: dict, dirty: bool = False) -> str:
    """"" when the stamp is acceptable, else why it is not.

    The one predicate `check` and `apply` both consult, so "what counts as
    stamped" cannot drift between reporting it and fixing it.

    `dirty` narrows the window to a point (PH23-T02). For a doc with uncommitted
    changes the count is *known* to be about to rise by one, so `count + 1` is not
    merely tolerated — it is the only value that survives the commit. Accepting
    `count` there is what let a correctly-stamped doc fail `doctor` immediately
    after the commit that shipped it, one step too late to fix cheaply.
    """
    missing = [k for k in STAMP_KEYS if k not in have]
    if missing:
        return "no stamp — missing " + ", ".join(missing)
    if have.get("updated_basis") != want["updated_basis"]:
        return (f"updated_basis: says {have.get('updated_basis')!r}, "
                f"git says {want['updated_basis']!r}")
    try:
        n = int(have["revisions"])
    except (TypeError, ValueError):
        return f"revisions: {have['revisions']!r} is not a number"
    count = want["revisions"]
    if dirty:
        if n != count + 1:
            return (f"revisions: says {n}, but this file has uncommitted changes and git "
                    f"counts {count} commit(s) touching it — the commit will make that "
                    f"{count + 1}, so the stamp must be {count + 1}")
        return ""
    if not count <= n <= count + 1:
        return (f"revisions: says {n}, git counts {count} commit(s) touching this file "
                f"(a stamp may be {count} or {count + 1}, never {n}) — this was typed")
    return ""


def check(root: Path | None = None) -> list:
    """[{path, why}] for every covered doc that is unstamped or disagrees with git.

    Missing and wrong are one verdict on purpose. A rule that validates only the
    files already complying with it is not a rule.
    """
    root = Path(root or ws_root())
    dirty = dirty_paths(root)
    out = []
    for rel in documents(root):
        try:
            text = (root / rel).read_text(encoding="utf-8")
        except OSError as exc:
            out.append({"path": rel, "why": f"unreadable ({exc.strerror})"})
            continue
        why = verdict(_existing(split(text)[0]), derive(root, rel, str(rel) in dirty),
                      str(rel) in dirty)
        if why:
            out.append({"path": rel, "why": why})
    return out


def apply(root: Path | None = None) -> list:
    """Stamp every covered doc that needs it. Returns the paths rewritten.

    **Idempotent, and it takes care to be.** A file whose stamp already satisfies
    `verdict()` is left alone rather than re-rendered — otherwise every run would
    rewrite `revisions` to `count + 1` again, and a doc would churn on every
    session, which is how a stamp becomes noise in a review instead of a signal.
    """
    root = Path(root or ws_root())
    dirty = dirty_paths(root)
    changed = []
    for rel in documents(root):
        path = root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        want = derive(root, rel, str(rel) in dirty)
        if not verdict(_existing(split(text)[0]), want, str(rel) in dirty):
            continue
        # `+ 1` counts the commit this very write will be part of. See the module
        # docstring: equality is unreachable because stamping is itself a change.
        want["revisions"] = want["revisions"] + 1
        out = render(text, want)
        if out != text:
            path.write_text(out, encoding="utf-8")
            changed.append(rel)
    return changed


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Derived version/updated/revisions stamps on docs.")
    ap.add_argument("--root", default=None)
    ap.add_argument("--check", action="store_true", help="report drift, exit 1 if any")
    ap.add_argument("--apply", action="store_true", help="write the stamps")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else ws_root()

    if args.apply:
        changed = apply(root)
        print(f"  ✅ doc-stamps: {len(changed)} file(s) stamped"
              if changed else "  ✅ doc-stamps: already current — nothing to write")
        for rel in changed:
            print(f"       • {rel}")
        return 0

    drift = check(root)
    total = len(documents(root))
    if not drift:
        print(f"  ✅ doc-stamps: all {total} doc(s) carry a stamp that agrees with git"
              + (f" · {len(EXEMPT)} declared exemption(s)" if EXEMPT else ""))
        return 0
    print(f"  ❌ doc-stamps: {len(drift)} of {total} doc(s) unstamped or drifted")
    for d in drift:
        print(f"       ✗ {d['path']}\n           {d['why']}")
    print("     These fields are derived from git, so a disagreement means the file was\n"
          "     edited by hand. Run `just doc-stamps --apply` to restore them.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
