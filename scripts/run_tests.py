#!/usr/bin/env python3
"""run_tests.py — the one test runner every governed workspace carries (PH7-T05).

## Why this exists

PHASE 10's design law is *the contract replaces trust*: an executor model is handed
a named failing test and may claim the work only when that test goes green. **That
contract is unbuildable in a workspace that cannot run a test**, and until this
script none of the 38 governed workspaces could. `.ai/policies/pre-commit-config.minimal.yaml`
ships seven whitespace/JSON/secret hooks and deliberately no test hook — so
`just verify-safe` out there proved "no trailing whitespace" and opened the gate
on the strength of it.

## Two calls with deliberately different costs

`discover()` is a **pure read that executes nothing**. It finds the runner and
counts test functions with `ast`. Counting by importing would run each module's
import-time code — executing untrusted code to answer a question *about* it —
and one exploding module would take the whole count with it. `fleet-status` calls
only this: running 38 suites to draw a status table would execute arbitrary code
across the fleet from a single status command.

`run()` executes. It is invoked by `just verify-safe` and `just test` — deliberate,
local actions — and never by anything that walks the fleet.

## "none" exits 0, and is never reported as "pass"

31 of 35 workspaces have no tests. A runner that failed there would close 31 gates
the day it deployed, and a red gate everywhere is a red gate nowhere. So absence
exits 0 — but it reports itself as `none`. This is the standing refusal pattern
here (`effort_forecast` refusing to estimate, `off_plan` printing its refusal):
the absence is *stated* so it can be acted on, never silently coloured green.

For the same reason an `npm` runner reports `count: None` rather than a guess: a
number nobody measured is not a number (design law 2).

## The scaffold declares that it is a scaffold

`onboard_project.sh` deploys `.ai/templates/tests/test_smoke.py` so a new workspace
has a working runner from minute one. One scaffold test passing must not read as
coverage — so `discover()` reports `scaffold_only` when everything it found carries
`SCAFFOLD_MARKER`. "1 test" that is the scaffold and "1 test" someone wrote are
different facts, and are reported as different facts.

## `collects()` — a third cost, between the two (PH7-T06)

Resolving a known issue must prove the named regression test is real. `collects()`
answers "would this workspace's own runner reach `tests/x.py::Cls::test_y`?" by
asking the **real loader**, in a subprocess, without running the test.

It is a third point on the cost curve on purpose. `discover()` may not import (it
walks the fleet). `run()` executes everything (a deliberate local action).
`collects()` imports **one** module — enough to let `unittest`'s own loader answer
instead of `ast` guessing which functions look like tests, cheap enough for a
command a human types now and then. An `ast` check would happily pass a `test_*`
method on a class the loader never instantiates, which is exactly the false green
PH7-T06 exists to prevent.

Two independent conditions, because either alone is a lie:
  * the file is one `unittest discover -s tests -p 'test_*.py'` actually reaches —
    a loader will happily load a name in a file discovery never visits, and that
    test would be green here and never run in CI;
  * the loader yields a real test for the name (not a `_FailedTest` placeholder).

Usage:
    python3 scripts/run_tests.py                 # run them; exit 1 only on a real failure
    python3 scripts/run_tests.py --quiet         # one line of output
    python3 scripts/run_tests.py --discover      # count only, executes nothing
    python3 scripts/run_tests.py --discover --json
    python3 scripts/run_tests.py --collects "tests/test_x.py::Cls::test_y"
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Written into the shipped scaffold. Its whole job is to make "this test came
# with the workspace" a fact a tool can read, rather than a filename convention
# a workspace can silently break by renaming the file.
SCAFFOLD_MARKER = "godmode:scaffold"

TESTS_DIR = "tests"
PATTERN = "test_*.py"
DEFAULT_TIMEOUT = 600

# Vendor/build trees: the same exclusion the minimal pre-commit config carries,
# and for the same reason — at least one fleet workspace has a committed
# node_modules/, and counting its tests would be nonsense.
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "venv", "dist", "build", "vendor"}


def _test_files(root: Path) -> list[Path]:
    """Every `test_*.py` under `tests/`, in the order unittest would find them."""
    base = root / TESTS_DIR
    if not base.is_dir():
        return []
    out = []
    for p in sorted(base.rglob(PATTERN)):
        if SKIP_DIRS & set(p.relative_to(base).parts):
            continue
        out.append(p)
    return out


def _count_tests(path: Path) -> int | None:
    """Test functions in one file, or None if it does not parse.

    `ast.parse` reads the source. It never imports, so a module that raises at
    import time is still counted — see `test_counting_never_imports_the_module`.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return None
    n = 0
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            n += 1
    return n


def _npm_test_script(root: Path) -> bool:
    pkg = root / "package.json"
    if not pkg.is_file():
        return False
    try:
        data = json.loads(pkg.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return False
    return bool(isinstance(data, dict) and (data.get("scripts") or {}).get("test"))


def discover(root: Path | str = ".") -> dict:
    """What this workspace can run, and how much of it — executing nothing.

    Returns `runner` · `count` (None = a runner we cannot count without running
    it) · `files` · `scaffold_only` · `unparseable` · `basis`.
    """
    root = Path(root)
    files = _test_files(root)

    if files:
        count, unparseable, scaffold_files = 0, [], 0
        for f in files:
            n = _count_tests(f)
            if n is None:
                unparseable.append(str(f.relative_to(root)))
                continue
            count += n
            if SCAFFOLD_MARKER in f.read_text(encoding="utf-8", errors="replace"):
                scaffold_files += 1
        parsed = len(files) - len(unparseable)
        basis = f"{count} test function(s) in {parsed} file(s) under {TESTS_DIR}/, parsed not imported"
        if unparseable:
            basis += f"; {len(unparseable)} file(s) did not parse and are NOT counted"
        return {
            "runner": "unittest",
            "count": count,
            "files": len(files),
            # Only-the-scaffold is a real state; nothing-at-all is not "only the
            # scaffold", it is nothing — and `files` is non-empty here by construction.
            "scaffold_only": count > 0 and scaffold_files == parsed,
            "unparseable": unparseable,
            "basis": basis,
        }

    if _npm_test_script(root):
        return {
            "runner": "npm",
            # Refusing to guess: the count lives inside whatever `npm test`
            # shells out to, and inventing one would be a number with no basis.
            "count": None,
            "files": 0,
            "scaffold_only": False,
            "unparseable": [],
            "basis": "package.json declares a test script; its test count is not "
                     "knowable without running it, and is not guessed",
        }

    return {
        "runner": None,
        "count": 0,
        "files": 0,
        "scaffold_only": False,
        "unparseable": [],
        "basis": f"no {TESTS_DIR}/{PATTERN} and no package.json test script — this "
                 f"workspace has no tests to run",
    }


#: Below this many discovered modules, a process pool costs more (spawn
#: overhead) than it could possibly save, so `run()` never builds one — the
#: same threshold makes single/zero-module fixtures in this test suite behave
#: exactly as they did before this task (PH25-T04).
MIN_MODULES_FOR_POOL = 2


def _run_module(args: tuple[str, str, int]) -> tuple[int, str]:
    """Run one test module in its own subprocess. `(returncode, output)`.

    A **module-level** function (not a closure) because `ProcessPoolExecutor`
    pickles whatever it submits — a closure over `Path`/`root` objects is not
    picklable, so the three primitives a worker needs (dotted module name,
    the `sys.path` entry, the per-module timeout) are passed as plain strings
    and an int instead.
    """
    module, syspath, timeout = args
    cmd = [sys.executable, "-m", "unittest", module]
    try:
        proc = subprocess.run(cmd, cwd=syspath, capture_output=True, text=True, timeout=timeout)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"module {module} exceeded {timeout}s\n"
    except OSError as exc:  # noqa: BLE001 — a module that can't even start a subprocess
        return 1, f"could not run module {module}: {exc}\n"


def _aggregate(results: list[tuple[int, str]], elapsed: float) -> tuple[int, str]:
    """Combine per-module `(returncode, output)` pairs into the ONE shape every
    consumer of `run()`'s `output` already parses.

    The synthesized `Ran N tests in Xs` / `OK`|`FAILED (...)` line goes FIRST,
    not appended: `_tally()`'s regexes use `.search()` — first match only — so
    a per-module summary line buried in the concatenated text would otherwise
    win over the true aggregate. Everything else downstream (`failing_ids()`,
    `failing_reasons()`) scans by `.findall()`/self-contained block structure,
    which is position-independent, so each module's own `FAIL:`/`ERROR:`
    blocks are found correctly wherever they land in the concatenation (see
    `.ai/plans/PH25-T04.md` for the state-machine trace proving one module's
    footer can't bleed into the next module's leading header).
    """
    total_ran = total_failures = total_errors = 0
    any_tally_missing = False
    for rc, out in results:
        t = _tally(out)
        if t["ran"] is None:
            any_tally_missing = True
            continue
        total_ran += t["ran"]
        total_failures += t["failures"] or 0
        total_errors += t["errors"] or 0

    rc_out = 0 if all(rc == 0 for rc, _ in results) else 1
    if any_tally_missing:
        summary = f"Ran an unknown number of tests in {elapsed:.3f}s\n\n"
    else:
        summary = f"Ran {total_ran} tests in {elapsed:.3f}s\n\n"
    summary += "OK\n" if rc_out == 0 else f"FAILED (failures={total_failures}, errors={total_errors})\n"

    body = "\n".join(out for _, out in results)
    return rc_out, summary + "\n" + body


def _run_parallel(root: Path, files: list[Path], timeout: int, workers: int | None) -> tuple[int, str] | None:
    """The pool path. Returns `None` (never raises) if the pool cannot start or
    run — the caller falls back to the serial path, which is the DoD's literal
    "falls back to it automatically when the pool cannot start."
    """
    tasks = []
    for f in files:
        module, syspath = _dotted(root, f)
        tasks.append((module, str(syspath), timeout))

    n_workers = workers or min(len(tasks), os.cpu_count() or 1)
    started = time.monotonic()
    try:
        with concurrent.futures.ProcessPoolExecutor(max_workers=n_workers) as pool:
            results = list(pool.map(_run_module, tasks))
    except Exception:  # noqa: BLE001 — any pool-startup/runtime failure, fall back
        return None
    return _aggregate(results, time.monotonic() - started)


def run(root: Path | str = ".", timeout: int = DEFAULT_TIMEOUT,
        serial: bool = False, workers: int | None = None) -> dict:
    """Execute the suite. `status` is pass · fail · none — never a bare boolean.

    `none` carries returncode 0 on purpose (see the module docstring): absence
    must not close 31 gates, but it is reported as absence rather than as a pass.

    PH25-T04: `unittest` suites of `MIN_MODULES_FOR_POOL` or more modules run
    across a process pool by default — one subprocess per module — instead of
    one subprocess for the whole suite. `serial=True` (the CLI's `--serial`)
    forces the old single-subprocess path; the pool path also falls back to it
    by itself on any failure to start or run. The returned dict's shape never
    changes: `evidence.json`, `gate_check.py` and `failure_digest.py` all read
    it (or the CLI's printed form of it) unchanged either way.
    """
    root = Path(root)
    d = discover(root)

    if d["runner"] is None:
        return {"status": "none", "returncode": 0, "runner": None,
                "count": 0, "basis": d["basis"], "output": ""}

    if d["runner"] == "unittest" and not serial:
        files = _test_files(root)
        if len(files) >= MIN_MODULES_FOR_POOL:
            parallel = _run_parallel(root, files, timeout, workers)
            if parallel is not None:
                rc, output = parallel
                return {"status": "pass" if rc == 0 else "fail", "returncode": rc,
                        "runner": d["runner"], "count": d["count"], "basis": d["basis"],
                        "output": output}
            # pool could not start or run — fall through to the serial path below

    if d["runner"] == "unittest":
        cmd = [sys.executable, "-m", "unittest", "discover", "-s", TESTS_DIR, "-p", PATTERN]
    else:
        cmd = ["npm", "test", "--silent"]

    try:
        proc = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
        rc, output = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except FileNotFoundError:
        # `npm` absent. A runner that cannot start is a failure to report, not a
        # pass to assume — but it is distinguishable from a failing test.
        return {"status": "fail", "returncode": 127, "runner": d["runner"],
                "count": d["count"], "basis": d["basis"],
                "output": f"runner not found: {cmd[0]}"}
    except subprocess.TimeoutExpired:
        return {"status": "fail", "returncode": 124, "runner": d["runner"],
                "count": d["count"], "basis": d["basis"],
                "output": f"test run exceeded {timeout}s"}

    return {"status": "pass" if rc == 0 else "fail", "returncode": rc,
            "runner": d["runner"], "count": d["count"], "basis": d["basis"],
            "output": output}


# ── The record of the last real run (PH16-T01) ─────────────────────────────
#
# The session opening has to report a test verdict and cannot afford to produce
# one: this suite takes ~20s against a 0.19s opening. So a run leaves a record
# and the opening reads it.
#
# The record states what it is a verdict *about* — the timestamp is the whole
# point, because `health.py` compares it against the newest change in the tree
# and refuses to call a record green once anything has moved underneath it. A
# record with no timestamp is therefore useless, not merely incomplete.
#
# Only a real `run()` writes one. `--discover` counts without executing and
# `--collects` loads a single name; neither is a verdict on the suite, and
# recording either would be the exact forgery this file spends its docstring
# arguing against.

RECORD_PATH = ".ai/memory-bank/test-run.json"

# unittest's own summary lines. `Ran N tests in Xs`, then either `OK` or
# `FAILED (failures=A, errors=B, ...)`. Parsed rather than counted by us,
# because the runner is the authority on what it just ran.
_RAN_RE = re.compile(r"^Ran (\d+) tests? in ", re.M)
_FAILED_RE = re.compile(r"^FAILED \((.*)\)\s*$", re.M)
_TALLY_RE = re.compile(r"(failures|errors)=(\d+)")


def _tally(output: str) -> dict:
    """(ran, failures, errors) from unittest's summary — None where unstated.

    A count we could not read is `None`, never 0: "the runner said nothing about
    failures" and "the runner said zero failures" are different facts, and only
    the second one is allowed to render as green.
    """
    ran = _RAN_RE.search(output or "")
    tally = {"ran": int(ran.group(1)) if ran else None,
             "failures": None, "errors": None}
    m = _FAILED_RE.search(output or "")
    if m:
        found = dict((k, int(v)) for k, v in _TALLY_RE.findall(m.group(1)))
        tally["failures"] = found.get("failures", 0)
        tally["errors"] = found.get("errors", 0)
    elif ran and re.search(r"^OK(\s|$)", output or "", re.M):
        tally["failures"] = tally["errors"] = 0
    return tally


def record(result: dict, root: Path | str = ".") -> Path | None:
    """Persist a real run's verdict. Returns the path written, or None.

    Never raises: a workspace with an unwritable `.ai/` still gets its tests run,
    it just gets no record — and `health.py` reports a missing record as *not
    run*, which is the honest reading of exactly that situation.
    """
    from datetime import datetime, timezone
    root = Path(root)
    try:
        path = root / RECORD_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        tally = _tally(result.get("output", "") or "")
        path.write_text(json.dumps({
            "recorded_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "status": result.get("status"),
            "runner": result.get("runner"),
            "returncode": result.get("returncode"),
            "declared": result.get("count"),
            "ran": tally["ran"],
            "failures": tally["failures"],
            "errors": tally["errors"],
            "basis": result.get("basis", ""),
        }, indent=2) + "\n", encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001
        return None


def read_record(root: Path | str = ".") -> dict | None:
    """The last recorded run, or None when there is none / it is unreadable."""
    try:
        data = json.loads((Path(root) / RECORD_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:  # noqa: BLE001
        return None


# ── The run's own output outlives the run (PH24-T08) ───────────────────────
#
# `verify-safe` tees this script's stdout into a `mktemp -d` that its own trap
# deletes on exit, and runs it `--quiet`, which used to suppress the runner's
# output on a FAILING run too. Between them there was no surviving copy of what
# broke — so learning the failing test's name cost a second full suite run,
# measured twice on 2026-08-17 at ~2 minutes each. Worse, the second run is not
# guaranteed to reproduce the first: any tracked write in between changes what
# is under test.
#
# So the output lands here, every real run, pass or fail. Every run rather than
# only failures, because a file that sometimes describes an older run is a
# stale answer to "what just happened" — the same reason `record()` is written
# unconditionally.
#
# GITIGNORED, and `validate_os.GENERATED_RECORDS` re-checks that: this is raw
# captured output, so it carries absolute paths, environment, and — for a
# failing secrets hook — the secret. `failure_digest`'s first rule is that none
# of that may reach a tracked file, and `evidence.json` is tracked in 46 of them.

LOG_PATH = ".ai/last-test-run.log"

#: Matches `failure_digest.MAX_CHECKS`, which caps the same list in the evidence.
#: Kept as its own constant rather than imported: `run_tests.py` is the more
#: fundamental module (`fleet_status` loads it out of workspaces that may not
#: carry a digest), and a hard import would make it fail where it must not.
MAX_NAMED_FAILURES = 12

#: unittest's own failure header — `FAIL: test_x (module.Class.test_x)`. The whole
#: line is kept and reprinted VERBATIM, because that exact shape is what
#: `failure_digest._UNITTEST_RE` parses out of the log `verify-safe` tees. One
#: format, read by the operator and the digest alike, with nothing to drift.
_FAILURE_HEADER_RE = re.compile(r"^(?:FAIL|ERROR):\s+\S+\s+\(.+\)\s*$", re.M)

#: PH24-T13 — printed at column 0 under each header, carrying the exception line unittest
#: produced for that failure. Same contract and same fragility as the header above it:
#: `failure_digest._REASON_RE` is `^`-anchored, so an indent for looks starves it.
REASON_PREFIX = "REASON: "


def failing_ids(output: str) -> list[str]:
    """unittest's failure header lines, verbatim and in the order it printed them."""
    return _FAILURE_HEADER_RE.findall(output or "")


#: The dotted `module.Class.test_method` unittest itself prints inside the
#: header's parens — `failure_digest._UNITTEST_RE` extracts the identical
#: group from the identical header shape; kept as a sibling constant here
#: rather than imported, for the same one-way-dependency reason `run_tests.py`
#: never imports `failure_digest` (see MAX_NAMED_FAILURES above): this module
#: must stay loadable in a workspace that doesn't carry the digest.
_QUALNAME_RE = re.compile(r"\(([^)\s]+)\)\s*$")


def failing_names(output: str) -> list[str]:
    """Dotted names (`module.Class.test_method`) pulled from `failing_ids()`'s
    headers — what `python -m unittest <name>` needs to re-run exactly the
    tests that failed, nothing else (PH27-T10)."""
    out = []
    for header in failing_ids(output):
        m = _QUALNAME_RE.search(header)
        if m:
            out.append(m.group(1))
    return out


def failing_reasons(output: str) -> dict[str, str]:
    """`{failure header line: the exception line unittest printed for it}`.

    PH24-T13. `--quiet` printed the header and dropped the reason, so `evidence.json`
    recorded *what* broke and never *why* — and the two imply different cures. Measured
    2026-08-17: a gate naming only `test_progress_md_is_actually_within_the_line_budget`
    sent three consecutive sessions at `archive-memory`, while the message the test itself
    printed ("its archiver floor is 257 — it has no live state to blame") said the archiver
    could not fix it. The reason was in `result["output"]` the whole time; `--quiet` simply
    never printed it, so it never reached the log the digest reads.

    Keyed by the header LINE, verbatim, because that is what `failing_ids` returns and what
    `_render` iterates — no second identity to keep in step.

    Read from unittest's block structure: header, optional docstring line, a `---` rule,
    then the traceback, whose first column-0 line is the exception. Only that one line is
    taken, and the block is closed as soon as it is found, so a multi-line diff below it
    cannot overwrite it or leak in.

    **No cap and no redaction here, deliberately** — the redaction rule has exactly one
    implementation, in `failure_digest._sanitize_reason`, at the boundary it protects.
    This output goes to the operator's screen, to a `mktemp -d` log removed on exit, and to
    the gitignored `.ai/last-test-run.log`, all three of which already hold the full
    traceback. `evidence.json` is the tracked artefact, and its writer sanitizes.
    """
    out: dict[str, str] = {}
    header: str | None = None
    in_traceback = False
    for raw in (output or "").splitlines():
        line = raw.rstrip()
        if _FAILURE_HEADER_RE.match(line):
            header, in_traceback = line, False
            continue
        if header is None:
            continue
        if line.startswith("===================="):
            header, in_traceback = None, False       # next block; this one had no reason
            continue
        if line.startswith("--------------------"):
            in_traceback = True                      # the docstring line, if any, is above
            continue
        if not in_traceback or not line:
            continue
        if raw[:1].isspace() or line == "Traceback (most recent call last):":
            continue                                 # frames are indented; the header is not
        out.setdefault(header, line)
        header, in_traceback = None, False           # first column-0 line wins
    return out


def write_log(result: dict, root: Path | str = ".") -> Path | None:
    """Persist the run's full output. Returns the path written, or None.

    Never raises, for the same reason `record()` never does: a workspace with an
    unwritable `.ai/` still gets its tests run, it just gets no log — and a
    refusal that then names no file is the honest rendering of that.
    """
    from datetime import datetime, timezone
    root = Path(root)
    try:
        path = root / LOG_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        header = (f"# {result.get('status')} · {result.get('runner')} · "
                  f"exit {result.get('returncode')} · {stamp}\n"
                  f"# Full output of the last `run_tests.py` run. Regenerated every run.\n\n")
        path.write_text(header + (result.get("output") or ""), encoding="utf-8")
        return path
    except Exception:  # noqa: BLE001
        return None


# ── Does the runner reach a named test? (PH7-T06) ──────────────────────────

# Run in a subprocess: loading a name imports its module, and a module that
# explodes on import must fail *this one check* rather than the process asking
# the question. `_FailedTest` is unittest's placeholder for a name it could not
# load — it looks like a test until you run it, so it is caught by type, not by
# a truthiness check on the suite.
_LOADER_SNIPPET = """\
import sys, unittest
sys.path.insert(0, sys.argv[1])
loader = unittest.defaultTestLoader
try:
    suite = loader.loadTestsFromName(sys.argv[2])
except Exception as exc:
    print("%s: %s" % (type(exc).__name__, exc)); raise SystemExit(2)
if getattr(loader, "errors", None):
    print(str(loader.errors[0])[:400]); raise SystemExit(2)
def flat(s):
    for t in s:
        if isinstance(t, unittest.TestSuite):
            yield from flat(t)
        else:
            yield t
tests = list(flat(suite))
bad = [t for t in tests if type(t).__name__ == "_FailedTest"]
if bad:
    print("the loader could not load this name: %s" % bad[0]); raise SystemExit(2)
if not tests:
    print("the name loaded but yielded no test"); raise SystemExit(3)
print("%d" % len(tests))
"""


def parse_ref(ref: str) -> tuple[str, str] | None:
    """`path::Cls::test_y` or `path::Cls.test_y` → (path, dotted qualname)."""
    parts = [p for p in str(ref or "").split("::") if p.strip()]
    if len(parts) < 2:
        return None
    return parts[0].strip(), ".".join(p.strip() for p in parts[1:])


def _dotted(root: Path, path: Path) -> tuple[str, Path]:
    """The module name + `sys.path` entry `unittest discover` would use.

    Mirrors discovery's own rule: with no `tests/__init__.py` the start dir *is*
    the top-level dir (module `test_x`); with one, the package root is (module
    `tests.test_x`). Getting this wrong makes every ref unloadable in one of the
    two layouts.
    """
    if (root / TESTS_DIR / "__init__.py").is_file():
        rel = path.relative_to(root)
        return ".".join(rel.with_suffix("").parts), root
    rel = path.relative_to(root / TESTS_DIR)
    return ".".join(rel.with_suffix("").parts), root / TESTS_DIR


def collects(ref: str, root: Path | str = ".", timeout: int = 60) -> dict:
    """Would this workspace's runner reach `ref`? `collected` · `verified` · `reason`.

    `verified` is False when the runner cannot be *asked* (an `npm` suite knows its
    own tests only by running them). Refusing to assert beats guessing — a number
    nobody measured is not a number.
    """
    root = Path(root)
    out = {"ref": ref, "collected": False, "verified": False,
           "runner": None, "reason": ""}

    parsed = parse_ref(ref)
    if not parsed:
        out["reason"] = ("not a test reference — expected "
                         "<path>::<Class>::<test_name>, e.g. "
                         "tests/test_x.py::WidgetTest::test_it")
        return out
    rel_path, qualname = parsed

    out["runner"] = runner = discover(root)["runner"]
    target = root / rel_path
    if not target.is_file():
        out["reason"] = f"no such file: {rel_path}"
        return out

    if runner is None:
        out["reason"] = ("this workspace has no test runner, so nothing can collect "
                         "a test here — `.ai/templates/tests/test_smoke.py` is the "
                         "scaffold that gives it one")
        return out

    if runner != "unittest":
        # An npm suite's test list lives inside whatever `npm test` shells out to.
        out.update(collected=True, verified=False,
                   reason=(f"{rel_path} exists, but a {runner} suite cannot be asked "
                           f"what it collects without running it — existence checked, "
                           f"collection NOT verified"))
        return out

    if target.resolve() not in {p.resolve() for p in _test_files(root)}:
        out["reason"] = (f"{rel_path} exists but `unittest discover -s {TESTS_DIR} "
                         f"-p '{PATTERN}'` never reaches it — a test there would be "
                         f"green here and never run")
        return out

    module, syspath = _dotted(root, target)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", _LOADER_SNIPPET, str(syspath), f"{module}.{qualname}"],
            cwd=root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        out["reason"] = f"loading {ref} exceeded {timeout}s"
        return out

    detail = ((proc.stdout or "") + (proc.stderr or "")).strip().splitlines()
    detail = detail[-1] if detail else "no output"
    if proc.returncode != 0:
        out["reason"] = f"the runner does not collect {ref} — {detail}"
        return out

    out.update(collected=True, verified=True,
               reason=f"loaded by unittest from {rel_path} as {module}.{qualname}")
    return out


def _render(result: dict, quiet: bool, log: Path | None = None) -> None:
    icon = {"pass": "✅", "fail": "❌", "none": "⚠️ "}[result["status"]]
    n = result["count"]
    n_txt = "an unknown number of" if n is None else str(n)
    if result["status"] == "none":
        print(f"{icon} no tests — {result['basis']}")
        print("   Not a pass. `.ai/templates/tests/test_smoke.py` is the scaffold "
              "that gives this workspace a runner.")
        return
    print(f"{icon} tests {result['status']} — {n_txt} test(s) via {result['runner']}")

    # PH24-T08 — quiet may hide the noise, never the reason. A green run stays
    # one line; a red one always names what broke and where to read the rest.
    if result["status"] == "fail":
        if quiet:
            output = result.get("output", "")
            ids = failing_ids(output)
            reasons = failing_reasons(output)
            for line in ids[:MAX_NAMED_FAILURES]:
                # Column 0, not indented for looks: `failure_digest._UNITTEST_RE`
                # is `^`-anchored, and a cosmetic two spaces here silently starves
                # the digest again. Caught by test_the_digest_can_read_what_a_
                # quiet_run_prints, which exists for exactly this.
                print(line)
                # PH24-T13 — and the reason under it, same contract, same column.
                # A failure with no parseable exception line prints no REASON at all
                # rather than an empty one: absent is unknown, never invented.
                why = reasons.get(line)
                if why:
                    print(f"{REASON_PREFIX}{why}")
            if len(ids) > MAX_NAMED_FAILURES:
                # Stated, never silent: a truncated list reads as a complete one.
                print(f"   … and {len(ids) - MAX_NAMED_FAILURES} more "
                      f"(capped at {MAX_NAMED_FAILURES}; the file below has all of them)")
        if log is not None:
            print(f"   full output: {log}")

    if not quiet and result["output"]:
        print(result["output"].rstrip())


# ── PH27-T10: re-run only what failed last time ─────────────────────────────
#
# A debug-loop accelerant, never a gate — it gains no authority `verify-safe`
# doesn't already have, and it never calls `record()`/`write_log()`: those two
# are what `evidence.json` and `health.py` trust as "the last REAL full-suite
# run", and a partial re-run must never be mistaken for one.

def _failed_syspath(root: Path) -> Path:
    """Where `-m unittest <dotted name>` must run from — the same branch
    `_dotted()` uses, but computed once rather than per-file: every dotted
    name `failing_names()` returns shares one workspace-wide convention."""
    return root if (root / TESTS_DIR / "__init__.py").is_file() else root / TESTS_DIR


def run_failed(root: Path | str = ".", timeout: int = DEFAULT_TIMEOUT) -> dict:
    """Re-run exactly the test(s) that failed on the last recorded run.

    Returns `{"ok": bool, "ran": [dotted names], "returncode": int, "output": str,
    "reason": str}` — `reason` is set (and `ran` empty) only for the two
    non-executing outcomes: no prior run recorded, or nothing failed on it.
    """
    root = Path(root)
    log = root / LOG_PATH
    if not log.is_file():
        return {"ok": False, "ran": [], "returncode": 1, "output": "",
                "reason": f"no {LOG_PATH} yet — run the suite first"}

    names = failing_names(log.read_text(encoding="utf-8", errors="replace"))
    if not names:
        return {"ok": True, "ran": [], "returncode": 0, "output": "",
                "reason": "nothing failed on the last recorded run — nothing to re-run"}

    cmd = [sys.executable, "-m", "unittest"] + names
    proc = subprocess.run(cmd, cwd=_failed_syspath(root), capture_output=True,
                          text=True, timeout=timeout)
    output = (proc.stdout or "") + (proc.stderr or "")
    return {"ok": proc.returncode == 0, "ran": names, "returncode": proc.returncode,
            "output": output, "reason": ""}


def _render_failed(r: dict) -> None:
    if r["reason"] and not r["ran"]:
        icon = "✅" if r["ok"] else "⚠️ "
        print(f"{icon} {r['reason']}")
        return
    icon = "✅" if r["ok"] else "❌"
    print(f"{icon} re-ran {len(r['ran'])} previously-failing test(s): {', '.join(r['ran'])}")
    if r["output"]:
        print(r["output"].rstrip())


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Run (or count) this workspace's tests.")
    ap.add_argument("--discover", action="store_true",
                    help="count only — executes nothing")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--quiet", action="store_true", help="verdict only, no runner output")
    ap.add_argument("--root", default=".", help="workspace root (default: cwd)")
    ap.add_argument("--collects", metavar="REF",
                    help="does the runner reach <path>::<Class>::<test>? imports one "
                         "module; runs nothing")
    ap.add_argument("--serial", action="store_true",
                    help="run every module in one process, the pre-PH25-T04 path — "
                         "also what the pool falls back to by itself if it can't start")
    ap.add_argument("--workers", type=int, metavar="N",
                    help="process pool size (default: min(modules, cpu count))")
    ap.add_argument("--failed", action="store_true",
                    help="re-run only the test(s) that failed on the last recorded run "
                         "(PH27-T10) — a debug-loop accelerant; never a gate, and never "
                         "recorded as a run of its own")
    args = ap.parse_args(argv)

    if args.failed:
        r = run_failed(args.root)
        if args.json:
            print(json.dumps(r, indent=2))
        else:
            _render_failed(r)
        return 0 if r["ok"] else 1

    if args.collects:
        c = collects(args.collects, args.root)
        if args.json:
            print(json.dumps(c, indent=2))
        else:
            icon = "✅" if c["collected"] and c["verified"] else (
                "⚠️ " if c["collected"] else "❌")
            print(f"{icon} {c['ref']} — {c['reason']}")
        return 0 if c["collected"] else 1

    if args.discover:
        d = discover(args.root)
        if args.json:
            print(json.dumps(d, indent=2))
        else:
            n = "unknown" if d["count"] is None else d["count"]
            scaffold = " (scaffold only — no hand-written tests yet)" if d["scaffold_only"] else ""
            print(f"runner: {d['runner'] or 'none'} · tests: {n}{scaffold}")
            print(f"basis: {d['basis']}")
        return 0

    result = run(args.root, serial=args.serial, workers=args.workers)
    record(result, args.root)          # PH16-T01 — the session opening reads this
    log = write_log(result, args.root)  # PH24-T08 — the output outlives the run
    if args.json:
        print(json.dumps({**result, "log": str(log) if log else None}, indent=2))
    else:
        _render(result, args.quiet, log)
    return 1 if result["status"] == "fail" else 0


if __name__ == "__main__":
    sys.exit(main())
