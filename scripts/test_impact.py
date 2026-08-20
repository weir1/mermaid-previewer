#!/usr/bin/env python3
"""test_impact.py — run the tests a change could have broken, and name what it skipped.

**Why this exists, measured rather than assumed.** The session that built this
spent roughly twelve minutes of one sitting on three full `verify-safe` runs —
2,252 tests each — and not one of those runs needed 2,252 tests. The first
needed `test_codemap` and `test_ledger_consolidation`, the second `test_doc_stamp`.
They were found by guessing and running them by hand. That guess is what this
module makes systematic, and — more importantly — makes *honest about its own
blind spots*, which a guess never is.

**Not a gate. Deliberately, permanently, and stated in its own output.**
Selection here is static: it reads `import` statements, so it cannot see a test
that shells out to a script, reads a data file, or resolves a module by name at
run time. Those false negatives are acceptable *only* because
`just verify-safe` still runs everything and is the only thing the validation
gate reads. If a future change ever lets this satisfy the gate, that trade
collapses and this file becomes a liability rather than a convenience — so the
disclaimer is printed by `lines()` and asserted by a test, not left here where
only a reader of source would find it.

**The honest half is the point.** "No tests are affected" and "no import edge
can reach what you changed" render identically as an empty list, and only the
first is safe to act on. A changed path that no import can reach — `AGENTS.md`,
a `justfile`, a memory-bank document — is reported *by name* as unlinkable. The
same rule `run_tests.discover()` follows for a test count it cannot measure:
absence is reported as absence, never as a pass.

**One resolution of module names.** `run_tests` already owns what
`unittest discover` would call a test module, including the fork between the two
layouts (`tests/__init__.py` present → `tests.test_x`, absent → `test_x`), and
this module borrows it rather than keeping a second copy. A drifted copy would
surface across 46 deployed workspaces as "that test does not exist".
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run_tests  # noqa: E402

SCRIPTS_DIR = "scripts"


# ── The declared data map (PH27-T06) ────────────────────────────────────────
#
# The import graph has no edge a Markdown file can sit on, so before this table
# every memory-bank or ledger edit came back `unlinkable` and fell through to all
# 94 modules — measured 2026-08-18 on `.ai/memory-bank/activeContext.md` and
# `.ai/docs/tasks.md`, which are the two most-edited paths in the repo. The
# selector was blind in exactly the place a session spends its day.
#
# **Declared, never inferred, for the same reason the Python side refuses filename
# matching.** This repo names its tests after behaviours, so guessing which module
# covers a data file would be the graph's mistake with less information behind it.
# A path absent from this table stays `unlinkable` — "I cannot tell", which is
# safe — rather than being linked to a plausible-looking subset, which would run
# green while never having covered the change.
#
# **Membership rule, and it is narrow on purpose:** a module belongs here only if
# it reads the REAL file at `ROOT` and asserts on its content. A fixture-driven
# test exercises the *parser*, and no edit to the data can break it — including it
# would inflate the selection while adding no coverage. That rule excluded
# `test_ledger_consolidation`, `test_archive_memory`, `test_context_pack`,
# `test_token_budget`, `test_doc_stamp` and `test_protocol_score`, every one of
# which mentions these paths but only ever inside a temp workspace. Checked, not
# assumed — grepping for the filename is the mention-vs-declaration trap this
# workspace keeps paying for.
#
# Both rot directions fail a test rather than degrading quietly:
# `unmapped_modules()` catches an entry naming a module discovery does not
# collect, `unmapped_paths()` catches a pattern that matches no file.
DATA_TESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    # `active_task()`, the declaration scan and the orphan-marker rules all read
    # this file directly; `entry_budget.check(ROOT)` holds it to its line budget.
    (".ai/memory-bank/*.md", ("test_task_ledger", "test_entry_budget")),
    # The master ledger: `all_tasks`/`find_task` parse it, and `phase_ledger`
    # asserts heading order and phase headedness over the real file.
    (".ai/docs/tasks.md", ("test_task_ledger", "test_phase_ledger")),
)


def _data_targets(rel: str) -> tuple[str, ...]:
    """Test module stems declared for a non-Python path, or () if unmapped."""
    from fnmatch import fnmatch
    hit: list[str] = []
    for pattern, mods in DATA_TESTS:
        if fnmatch(rel, pattern):
            hit += [m for m in mods if m not in hit]
    return tuple(hit)


def unmapped_modules(root: Path | str | None = None) -> list[str]:
    """Declared test modules `unittest discover` does not actually collect.

    A table naming a module that no longer exists selects nothing while reporting
    success — the exact failure mode `.ai/codemap.md` has `just doctor` for.
    """
    root = _ws_root(root)
    known = {p.stem for p in run_tests._test_files(root)}
    declared = {m for _, mods in DATA_TESTS for m in mods}
    return sorted(declared - known)


def unmapped_paths(root: Path | str | None = None) -> list[str]:
    """Declared patterns that match nothing in the tree — dead rows that read
    as coverage."""
    root = _ws_root(root)
    return sorted(pattern for pattern, _ in DATA_TESTS
                  if not any(root.glob(pattern)))


def _ws_root(root: Path | str | None = None) -> Path:
    if root is not None:
        return Path(root)
    try:
        out = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                             capture_output=True, text=True, timeout=10)
        if out.returncode == 0 and out.stdout.strip():
            return Path(out.stdout.strip())
    except (OSError, subprocess.SubprocessError):
        pass
    return Path.cwd()


def _imports(path: Path) -> set[str]:
    """Top-level names this file imports. Unparseable → no edges, never a crash.

    A file that does not parse is a file whose edges are unknown; inventing zero
    edges for it is wrong in the safe direction (it under-links, and the full
    suite is the backstop), while raising would take the whole selector down for
    one bad file — which is how a convenience becomes an obstacle.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            # `from . import x` has no module; a relative import cannot name a
            # top-level workspace module, so there is nothing to link.
            if node.level == 0 and node.module:
                names.add(node.module.split(".")[0])
    return names


def graph(root: Path | str | None = None) -> dict:
    """`{module_name: {module_name, ...}}` over this workspace's own Python only.

    Nodes are `scripts/*.py` (imported by bare stem — the convention every test
    in this repo follows) plus every test file `run_tests._test_files()` finds,
    including helpers like `tests/prework_fixture.py`. Third-party and stdlib
    names are dropped: an edge to `json` links nothing and would make every test
    reachable from every change, which is a selector that selects everything.
    """
    root = _ws_root(root)
    files: dict[str, Path] = {}
    src = root / SCRIPTS_DIR
    if src.is_dir():
        for p in sorted(src.glob("*.py")):
            files[p.stem] = p
    for p in run_tests._test_files(root):
        files.setdefault(p.stem, p)
    # tests/ may hold non-`test_*` helpers that tests import (prework_fixture.py);
    # they are nodes so a change to one reaches the tests that use it.
    tdir = root / run_tests.TESTS_DIR
    if tdir.is_dir():
        for p in sorted(tdir.glob("*.py")):
            files.setdefault(p.stem, p)

    known = set(files)
    return {"files": files,
            "edges": {name: (_imports(path) & known) - {name}
                      for name, path in files.items()}}


def _reaches(start: str, targets: set[str], edges: dict) -> bool:
    """Can `start` reach any of `targets` by imports? Cycle-safe by seen-set.

    `a → b → a` is legal Python and exists in real trees. A naive walk recurses
    forever; a walk that bails on revisiting a node loses tests. Marking seen on
    *entry* does both correctly.
    """
    seen, stack = {start}, [start]
    while stack:
        node = stack.pop()
        if node in targets:
            return True
        for nxt in edges.get(node, ()):
            if nxt not in seen:
                seen.add(nxt)
                stack.append(nxt)
    return False


def select(changed: list[str], root: Path | str | None = None) -> dict:
    """Which test modules could a change to `changed` have broken?

    Returns `changed` · `selected` · `skipped` · `all_tests` · `unlinkable`,
    where module names are exactly what `unittest` would be handed. `unlinkable`
    is the honest field: paths the import graph cannot speak about at all.
    """
    root = _ws_root(root)
    g = graph(root)
    edges, files = g["edges"], g["files"]

    test_paths = run_tests._test_files(root)
    # The runner's own rule, not a second copy of it.
    dotted = {p.stem: run_tests._dotted(root, p)[0] for p in test_paths}
    all_tests = [dotted[p.stem] for p in test_paths]

    targets: set[str] = set()
    direct: set[str] = set()
    unlinkable: list[str] = []
    for raw in changed:
        rel = str(raw).strip()
        if not rel:
            continue
        stem = Path(rel).stem
        # A path is linkable only when it IS one of the graph's nodes — the same
        # file, not merely a file with the same stem somewhere else in the tree.
        node = files.get(stem)
        if node is not None and Path(rel).suffix == ".py" and \
                node.resolve() == (root / rel).resolve():
            targets.add(stem)
            continue
        # Not a graph node. Before conceding, ask the declared data map — the
        # graph cannot speak about a `.md` or `.yaml` file at all (PH27-T06).
        # An entry naming a module discovery does not collect is dropped rather
        # than selected, so a stale table under-selects visibly instead of
        # crashing; `unmapped_modules()` is what fails on it.
        mapped = [m for m in _data_targets(rel) if m in dotted]
        if mapped:
            direct.update(mapped)
        else:
            unlinkable.append(rel)

    # Union, not precedence: a session that edits a script AND a memory file gets
    # both sets. Letting either side win would silently drop the other's tests.
    selected = [dotted[p.stem] for p in test_paths
                if p.stem in direct or (targets and _reaches(p.stem, targets, edges))]
    chosen = set(selected)
    return {"changed": [str(c) for c in changed],
            "selected": selected,
            "skipped": [t for t in all_tests if t not in chosen],
            "all_tests": all_tests,
            "unlinkable": unlinkable,
            "root": str(root)}


def changed_files(root: Path | str | None = None) -> list[str]:
    """What this working tree has changed against HEAD, untracked included.

    Untracked matters: a brand-new module and its brand-new test are exactly the
    change a selector must not miss, and they are invisible to `git diff` alone.
    """
    root = _ws_root(root)
    out: list[str] = []
    for args in (["diff", "--name-only", "HEAD"],
                 ["ls-files", "--others", "--exclude-standard"]):
        try:
            proc = subprocess.run(["git", *args], cwd=root,
                                  capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode == 0:
            out += [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    return sorted(set(out))


def lines(sel: dict) -> list[str]:
    """The report a person reads. Says what it chose, what it skipped, and what
    it could not see — in that order, because the last one is the one that bites."""
    out = ["🎯 test-fast — the tests this change could have broken"]
    n_sel, n_all = len(sel["selected"]), len(sel["all_tests"])
    out.append(f"   changed: {len(sel['changed'])} path(s)")

    if sel["selected"]:
        out.append(f"   selected: {n_sel} of {n_all} test module(s)")
        for name in sel["selected"]:
            out.append(f"     • {name}")
    else:
        out.append(f"   selected: 0 of {n_all} test module(s) — no import edge "
                   "reaches anything that changed")
    out.append(f"   skipped:  {len(sel['skipped'])} module(s)")

    if sel["unlinkable"]:
        out.append(f"   ⚠️  {len(sel['unlinkable'])} changed path(s) the import graph "
                   "CANNOT speak about:")
        for p in sel["unlinkable"]:
            out.append(f"     • {p}")
        out.append("      Static analysis sees `import x` — not a test that reads a file, "
                   "shells out to")
        out.append("      a script, or resolves a name at run time. For these, this tool has "
                   "no opinion;")
        out.append("      it is NOT saying they are safe.")

    out.append("   ℹ️  NOT A GATE. `just verify-safe` runs all of them and is the only thing "
               "the")
    out.append("      validation gate reads. This is the inner loop, never the proof.")
    return out


def run(sel: dict, timeout: int = run_tests.DEFAULT_TIMEOUT) -> int:
    """Execute the selected modules the way the runner would. 0 when none ran."""
    if not sel["selected"]:
        return 0
    root = Path(sel["root"])
    paths = run_tests._test_files(root)
    syspaths = {str(run_tests._dotted(root, p)[1]) for p in paths}
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        sorted(syspaths) + ([env["PYTHONPATH"]] if env.get("PYTHONPATH") else []))
    proc = subprocess.run([sys.executable, "-m", "unittest", "-v", *sel["selected"]],
                          cwd=root, env=env, timeout=timeout)
    return proc.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run the tests a change could have broken. NOT a gate.")
    ap.add_argument("paths", nargs="*",
                    help="changed paths (default: this working tree vs HEAD, "
                         "untracked included)")
    ap.add_argument("--json", action="store_true", help="the selection, unrendered")
    ap.add_argument("--list", action="store_true",
                    help="report the selection without running it")
    ap.add_argument("--root", default=None)
    args = ap.parse_args(argv)

    root = _ws_root(args.root)
    changed = args.paths or changed_files(root)
    sel = select(changed, root=root)

    if args.json:
        print(json.dumps(sel, indent=2))
        return 0

    print("\n".join(lines(sel)))
    if args.list:
        return 0
    if not sel["selected"]:
        return 0
    print()
    return run(sel)


if __name__ == "__main__":
    sys.exit(main())
