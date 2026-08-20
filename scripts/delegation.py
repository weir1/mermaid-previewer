#!/usr/bin/env python3
"""
delegation.py — the delegation contract (PH10-T02, Goal G8).

PH10-T01 gave a session a declared model and a tier. This gives a *task* an
enforceable definition of done an `executor`-tier model cannot talk its way
around — the operator's own framing: *"if we provide them exact things to do
& test should become green then only they will pass & after that u just
review work."* The contract is that exact thing: an objective, a named test
that must go from red to green, a file allowlist, an iteration limit, and who
planned/executed/reviewed it.

## Why the scaffolder does not run the test, but `--check` does

At scaffold time the objective/command/allowlist are all still placeholders —
there is nothing runnable yet. `just delegate "PH#-T##"` only writes the
template (never overwrites, guidance lives in HTML comments — the exact
`plan.py` shape, reused here via `plan._sections`/`plan._is_written` rather
than re-implemented). `just delegate --check "PH#-T##"` is where "watched
failing for the right reason" (the `test-first` skill's own words) becomes a
recorded fact: it runs the filled-in `Command:` via `subprocess.run` with a
`shlex`-split argv (never a shell string — the same discipline
`self_review.py`/`run_tests.py` already apply to anything that executes an
operator-authored command) and:

  * **exit 0 (already passes) → refused.** A contract proves something went
    from red to green; a command that starts green proves nothing.
  * **non-zero exit → recorded** into the file: timestamp, exit code, the
    last ~20 lines of combined stdout/stderr. Verified, never asserted — the
    same rule PH7-T06 applied to `Resolved`, applied here to a contract's
    entry condition.
  * **cannot even run** (bad path, no such module) → refused, distinctly from
    "already passes" — a typo must not be recorded as a legitimate failure.

An empty file allowlist is refused: PH10-T03's leash (not built yet) will have
nothing to enforce against otherwise. Placeholder text anywhere required fails
the same way an untouched `plan.py` scaffold does.

## What this does NOT do

Enforce the allowlist or the no-push rule (built in PH10-T03) or bind a
reviewer verdict to the diff (built in PH10-T04) — those live in
`leash.py`/`delegate_review.py`. `scaffold()` DOES now log an independent
`attribution` decision-log entry for `planned_by` (PH10-T05), which
`attribution.check_contract()` checks the contract's own static line
against — see that module for why `executed_by`/`reviewed_by` are
deliberately never written back into this file.

Usage:
  delegation.py PH10-T02          # scaffold (or report status if it exists)
  delegation.py PH10-T02 --check  # validate; on success, RUN the test and record it
  delegation.py PH10-T02 --check --json
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import plan  # noqa: E402 — reuses _sections()/_is_written(), the plan.py shape

TASK_RE = plan.TASK_RE

REQUIRED_SECTIONS = ("Objective", "Failing test", "File allowlist",
                      "Iteration limit", "Attribution")

NOT_YET = "(not yet — run `just delegate-check \"{task}\"`)"
TIMEOUT_SECONDS = 120
OUTPUT_TAIL_LINES = 20

TEMPLATE = """# Delegation Contract — {task}

Status: scaffolded

## Objective
<!--
Plain English: what should exist when this is done. Write it the way you
would hand it to someone who has never opened this codebase.
-->

## Failing test
<!--
The exact, runnable command that must go from failing to passing, e.g.
`python3 -m unittest tests.test_x.ClassName.test_method`. `just delegate-check`
RUNS this: refuses if it already passes, records the failure if it genuinely
fails. It is run with a split argv, never a shell string.
-->
Command:

Recorded failure: """ + NOT_YET + """

## File allowlist
<!--
One path or glob per line, `- ` prefixed. PH10-T03's leash blocks any write
outside this list once it ships. An empty allowlist is refused — there would
be nothing for the leash to enforce.
-->

## Iteration limit
<!-- Max attempts the executor gets before this must be re-scoped. An integer. -->

## Attribution
planned_by: {planned_by}
executed_by: (unassigned)
reviewed_by: (unassigned)
"""


def ws_root() -> Path:
    return plan.ws_root()


def contract_path(task: str, root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / "delegation" / f"{task}.md"


def _resolved_planner(root: Path | None) -> str:
    try:
        import model_registry
        return model_registry.resolve_running(root)["name"]
    except Exception:  # noqa: BLE001
        return "unknown"


def _log_planned_by(task: str, planned_by: str, root: Path | None = None) -> None:
    """PH10-T05: the independent record `attribution.check_contract()` checks
    the contract's static `planned_by:` line against later. Best effort —
    a logging failure must never break scaffolding, the same rule every
    other `_log` helper in this repo already follows."""
    try:
        import decision_log as dl
        dl.record("attribution", "declared", "delegation", root=root, task=task,
                  action="planned_by", model=planned_by,
                  reason=f"{task} scaffolded, planned_by={planned_by}")
    except Exception:  # noqa: BLE001
        pass


def scaffold(task: str, root: Path | None = None) -> Path:
    """Write the empty contract template. Raises FileExistsError rather than clobber."""
    path = contract_path(task, root)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    planned_by = _resolved_planner(root)
    path.write_text(TEMPLATE.format(task=task, planned_by=planned_by), encoding="utf-8")
    _log_planned_by(task, planned_by, root)
    return path


def _fill(text: str) -> str:
    """`text\\n` if non-empty, else `""`.

    The difference between a filled line sitting before the next section's
    blank-line gap and the gap standing alone — this is what makes
    `_render_template`'s all-empty output line up with `TEMPLATE` byte for
    byte (pinned by `TheTwoContractRenderersAgreeOnEmptyInput`)."""
    return f"{text}\n" if text else ""


def _render_template(task: str, planned_by: str, *, objective: str = "",
                      command: str = "", allowlist: list | None = None,
                      iteration_limit: str = "") -> str:
    """The contract's shape, parameterised (PH10-T10).

    Additive, not a replacement for `TEMPLATE`: `tests/test_attribution.py`
    calls `delegation.TEMPLATE.format(task=..., planned_by=...)` directly, so
    `TEMPLATE` is a live public surface and rewriting `scaffold()` in terms of
    this function would risk that test for no gain. The two are independently
    written and pinned to agree on empty input by a parity test — the same
    "one source of truth" guarantee `TheRequiredListHasExactlyOneSource`
    enforces elsewhere in this repo, applied here without adding coupling
    neither caller needs.
    """
    allowlist_block = "\n".join(f"- {p}" for p in (allowlist or []))
    command_suffix = f" {command}" if command else ""
    return (
        f"# Delegation Contract — {task}\n"
        "\n"
        "Status: scaffolded\n"
        "\n"
        "## Objective\n"
        "<!--\n"
        "Plain English: what should exist when this is done. Write it the way you\n"
        "would hand it to someone who has never opened this codebase.\n"
        "-->\n"
        f"{_fill(objective)}"
        "\n"
        "## Failing test\n"
        "<!--\n"
        "The exact, runnable command that must go from failing to passing, e.g.\n"
        "`python3 -m unittest tests.test_x.ClassName.test_method`. `just delegate-check`\n"
        "RUNS this: refuses if it already passes, records the failure if it genuinely\n"
        "fails. It is run with a split argv, never a shell string.\n"
        "-->\n"
        f"Command:{command_suffix}\n"
        "\n"
        f"Recorded failure: {NOT_YET.format(task=task)}\n"
        "\n"
        "## File allowlist\n"
        "<!--\n"
        "One path or glob per line, `- ` prefixed. PH10-T03's leash blocks any write\n"
        "outside this list once it ships. An empty allowlist is refused — there would\n"
        "be nothing for the leash to enforce.\n"
        "-->\n"
        f"{_fill(allowlist_block)}"
        "\n"
        "## Iteration limit\n"
        "<!-- Max attempts the executor gets before this must be re-scoped. An integer. -->\n"
        f"{_fill(str(iteration_limit) if iteration_limit else '')}"
        "\n"
        "## Attribution\n"
        f"planned_by: {planned_by}\n"
        "executed_by: (unassigned)\n"
        "reviewed_by: (unassigned)\n"
    )


def scaffold_prefilled(task: str, objective: str, command: str, allowlist: list,
                        iteration_limit: int, root: Path | None = None) -> Path:
    """PH10-T10: write a contract with Objective/Command/File allowlist/Iteration
    limit already filled from an operator-accepted delegation proposal, so
    `just delegate-check` can run immediately — the DoD's "arm unmodified".

    Still Status: scaffolded, still unarmed: this only pre-fills the fields a
    human would otherwise type by hand. `leash.py` enforces nothing until
    `--check` runs and records a genuine failing test, same as every contract.
    Raises FileExistsError rather than clobber, same as `scaffold()`.
    """
    path = contract_path(task, root)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    planned_by = _resolved_planner(root)
    text = _render_template(task, planned_by, objective=objective, command=command,
                             allowlist=allowlist, iteration_limit=str(iteration_limit))
    path.write_text(text, encoding="utf-8")
    _log_planned_by(task, planned_by, root)
    return path


# ─────────────────────────── iteration counting (PH10-T07) ───────────────────

CHECK_ACTION = "delegate check"


def _log_check_attempt(task: str, outcome: dict, root: Path | None = None) -> None:
    """One entry per GENUINE `--check` run — counted later by
    `iteration_count()` against the contract's own declared `## Iteration
    limit` (`leash.check_iterations()`). Only called once `_run_command()`
    has confirmed the command actually ran (`outcome["error"]` empty): a
    command that could not even run is a broken contract, not an attempt,
    and must not consume the executor's budget. Reuses the SAME append-only
    decision log every other count in this repo reads (`attribution.by_model`,
    `protocol_score.py`) rather than a second counter file that could drift
    from what the log says actually happened. Best effort — a logging
    failure must never turn "already ran" into "never happened," the same
    rule every other `_log*` helper here follows."""
    try:
        import decision_log as dl
        dl.record("gate", "checked", "delegation", root=root, task=task,
                  action=CHECK_ACTION,
                  reason=f"{task}: `--check` ran the named command "
                         f"(exit {outcome.get('returncode')}).")
    except Exception:  # noqa: BLE001
        pass


def iteration_count(task: str, root: Path | None = None) -> int:
    """How many times `--check` has genuinely run `task`'s named command —
    read from the decision log `_log_check_attempt()` writes to, never a
    second counter file. `_record_failure()` REPLACES the contract's own
    `Recorded failure:` block on every run, so the file itself only ever
    shows the LATEST attempt; the append-only log is the one place that can
    answer "how many, total"."""
    root = root or ws_root()
    import decision_log
    entries = decision_log.read_entries(root)
    return sum(1 for e in entries
               if e.get("source") == "delegation" and e.get("action") == CHECK_ACTION
               and e.get("task") == task)


# ─────────────────────────────── field extraction ────────────────────────────

def _strip_comments(body: list) -> str:
    import re
    return re.sub(r"<!--.*?-->", "", "\n".join(body), flags=re.S)


def _line_value(body: list, prefix: str) -> str | None:
    """The text after the first line whose stripped content starts with `prefix`,
    scanning only outside HTML comments. None if no such line exists."""
    text = _strip_comments(body)
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith(prefix):
            return line[len(prefix):].strip()
    return None


def _allowlist(body: list) -> list:
    text = _strip_comments(body)
    out = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("- "):
            item = line[2:].strip()
            if item:
                out.append(item)
    return out


def _iteration_limit(body: list) -> int | None:
    text = _strip_comments(body)
    for raw in text.splitlines():
        line = raw.strip()
        if line.isdigit() and int(line) > 0:
            return int(line)
    return None


def _clean_command(raw: str | None) -> str:
    if not raw:
        return ""
    cmd = raw.strip()
    if cmd.startswith("`") and cmd.endswith("`") and len(cmd) >= 2:
        cmd = cmd[1:-1].strip()
    return cmd


# ────────────────────────────────── validation ────────────────────────────────

def validate(task: str, root: Path | None = None, run: bool = False) -> dict:
    """Is `task`'s contract complete? With `run=True`, also executes the named
    test command and records the outcome — the only path that writes."""
    root = root or ws_root()
    path = contract_path(task, root)
    v = {"task": task, "path": str(path), "exists": path.is_file(),
         "ok": False, "ran": False, "reason": "", "command": "", "allowlist": [],
         "iteration_limit": None}
    if not v["exists"]:
        v["reason"] = (f"no contract at {path.relative_to(root)} — "
                       f"run `just delegate \"{task}\"` and fill it in before `--check`.")
        return v

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        v["reason"] = f"contract unreadable ({exc})."
        return v

    sections = plan._sections(text)
    missing = [s for s in REQUIRED_SECTIONS if s not in sections]
    if missing:
        v["reason"] = "missing section(s): " + ", ".join(missing) + " — not a valid contract."
        return v

    if not plan._is_written(sections["Objective"]):
        v["reason"] = f"{task}'s `## Objective` is empty — describe what must exist when done."
        return v

    command = _clean_command(_line_value(sections["Failing test"], "Command:"))
    v["command"] = command
    if not command:
        v["reason"] = (f"{task}'s `## Failing test` names no `Command:` — write the exact, "
                       "runnable test command that must go from failing to passing.")
        return v

    allowlist = _allowlist(sections["File allowlist"])
    v["allowlist"] = allowlist
    if not allowlist:
        v["reason"] = (f"{task}'s `## File allowlist` is empty — a delegation with nothing "
                       "named has nothing for the leash to enforce.")
        return v

    limit = _iteration_limit(sections["Iteration limit"])
    v["iteration_limit"] = limit
    if not limit:
        v["reason"] = f"{task}'s `## Iteration limit` names no positive integer."
        return v

    planned_by = _line_value(sections["Attribution"], "planned_by:")
    if not planned_by or planned_by.startswith("(unassigned"):
        v["reason"] = f"{task}'s `## Attribution` names no `planned_by:`."
        return v

    if not run:
        v["ok"] = True
        v["reason"] = (f"contract complete — command and allowlist named; not yet run "
                       "(`just delegate-check` proves the test currently fails).")
        return v

    outcome = _run_command(command, root)
    v["ran"] = True
    if outcome["error"]:
        v["reason"] = f"the named command could not be run: {outcome['error']}"
        return v
    _log_check_attempt(task, outcome, root)
    if outcome["returncode"] == 0:
        v["reason"] = (f"refused — `{command}` already exits 0. Delegation proves something "
                       "went from failing to passing; a command that starts green proves "
                       "nothing.")
        return v

    _record_failure(path, task, outcome)
    v["ok"] = True
    v["reason"] = (f"recorded — `{command}` fails (exit {outcome['returncode']}) as expected. "
                   "The executor's job is to make it pass without leaving the allowlist.")
    return v


def check(task: str, root: Path | None = None) -> dict:
    return validate(task, root=root, run=True)


def _run_command(command: str, root: Path) -> dict:
    try:
        argv = shlex.split(command)
    except ValueError as exc:
        return {"error": f"unparseable command ({exc})", "returncode": None, "output": ""}
    if not argv:
        return {"error": "empty command", "returncode": None, "output": ""}
    try:
        proc = subprocess.run(argv, cwd=str(root), capture_output=True, text=True,
                              timeout=TIMEOUT_SECONDS, shell=False)
    except FileNotFoundError as exc:
        return {"error": str(exc), "returncode": None, "output": ""}
    except subprocess.TimeoutExpired:
        return {"error": f"timed out after {TIMEOUT_SECONDS}s", "returncode": None, "output": ""}
    except Exception as exc:  # noqa: BLE001
        return {"error": str(exc), "returncode": None, "output": ""}
    combined = (proc.stdout or "") + (proc.stderr or "")
    tail = "\n".join(combined.splitlines()[-OUTPUT_TAIL_LINES:])
    return {"error": "", "returncode": proc.returncode, "output": tail}


def _record_failure(path: Path, task: str, outcome: dict) -> None:
    """Replace the `Recorded failure:` line (and any prior recorded block) with
    the real, just-observed evidence. Only ever called after the command has
    genuinely run and genuinely failed."""
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()

    start = None
    for i, line in enumerate(lines):
        if line.strip().startswith("Recorded failure:"):
            start = i
            break
    if start is None:
        # Defensive — validate() already required this section to exist.
        return

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break

    stamp = datetime.now(timezone.utc).isoformat()
    block = [
        f"Recorded failure: {stamp} (exit {outcome['returncode']})",
        "```",
        outcome["output"] or "(no output captured)",
        "```",
        "",
    ]
    new_lines = lines[:start] + block + lines[end:]
    path.write_text("\n".join(new_lines), encoding="utf-8")


# ────────────────────────────────────── CLI ──────────────────────────────────

# ── contract lifecycle state (PH10-T08, Goal G8) ─────────────────────────────
#
# Until this existed, a contract enforced the leash for as long as the FILE
# existed. Nothing marked one finished, so `doc/RUNBOOK_delegation.md` had to
# state the consequence as a rule for the human: *"A fulfilled contract keeps
# enforcing … move the file out of `.ai/delegation/`"*. The operator's words when
# accepting the task: *"dont leave a manual step, if a human has to remember to
# move the file then its broken."*
#
# Every state is DERIVED from a record that already exists — a logged check, a
# review verdict, the ledger's own checkbox. Nothing here reads the contract's
# `Status:` line, which is template prose: if hand-editing it could retire a
# contract, the state would be exactly the hand-kept counter this subsystem
# exists to remove, and the leash would be openable by typing.

RETIRED, REVIEWED, EXECUTED, ARMED = "retired", "reviewed", "executed", "armed"

#: `- [x] PH10-T08 …` — a checkbox and an id at the START of a bullet. A prose
#: line merely MENTIONING the id is not a completion (the mention-vs-declaration
#: defect this repo keeps paying for).
_LEDGER_DONE_RE = re.compile(r"^\s*-\s*\[(?P<box>[ xX])\]\s*(?P<task>PH\d+-T\d+)\b")


def _logged_checks(task: str, root: Path) -> list:
    """Every `delegate check` entry for this task, oldest first.

    `exit_code` is read as a STRUCTURED field. Grepping it out of the human
    `reason` sentence would be reading state by pattern-matching prose — the
    thing this module refuses to do anywhere else.
    """
    out: list = []
    d = root / ".ai" / "decision-log"
    if not d.is_dir():
        return out
    for f in sorted(d.glob("*.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except Exception:  # noqa: BLE001
            continue
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                entry = json.loads(line)
            except Exception:  # noqa: BLE001
                continue  # a malformed line is skipped, never fatal
            if not isinstance(entry, dict):
                continue
            if entry.get("source") != "delegation" or entry.get("action") != CHECK_ACTION:
                continue
            if entry.get("task") != task or "exit_code" not in entry:
                continue
            out.append(entry)
    # Stable sort: entries written in the same second (the common case, and what
    # the tests write) keep their append order, which IS their true order.
    out.sort(key=lambda e: str(e.get("at") or ""))
    return out


def _ledger_retired(task: str, root: Path) -> str:
    """The basis string if the ledger reports `task` complete, else ""."""
    for rel in (Path(".ai") / "docs" / "tasks.md",
                Path(".ai") / "memory-bank" / "activeContext.md"):
        p = root / rel
        if not p.is_file():
            continue
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:  # noqa: BLE001
            continue
        for line in text.splitlines():
            m = _LEDGER_DONE_RE.match(line)
            if m and m.group("task") == task and m.group("box").lower() == "x":
                return f"{rel} reports {task} complete — `- [x] {task}`"
    return ""


def _passing_review(task: str, root: Path) -> str:
    """The basis string if a passing delegation review is on file, else "".

    Reads the review directory directly rather than via
    `delegate_review.read_reviews()`. The path helper IS shared, so the two
    always agree on where records live.

    **Corrected 2026-08-18 (PH10-T09).** This docstring used to justify the
    direct read by saying `read_reviews()` filters on `diff_sha256`, "a field
    belonging to the SELF-review record shape, not the delegation verdict shape
    (`{task, verdict, digest, reviewed_by}`)". That is false, and it was filed in
    knownIssues.md as a production bug on the strength of it.
    `delegate_review.record()` — the only writer of a delegation review — emits
    `diff_sha256` and emits no `digest` key at all; the shape quoted above was
    invented by a TEST FIXTURE (`tests/test_delegation_state.py`), since fixed.

    The direct read is kept, on a true justification rather than a false one:
    this derives a contract's STATE, and a contract is `reviewed` because a
    passing verdict exists, not because that verdict still matches the working
    tree. `read_reviews()` serves `check()`, which must bind a review to a
    specific diff. Same records, deliberately different questions — and
    `find_delegation_status` uses `read_reviews()` for exactly the diff-bound
    question (`voided`), which is the half this function must not answer.
    """
    try:
        import delegate_review
        directory = delegate_review.reviews_dir(task, root)
    except Exception:  # noqa: BLE001
        return ""
    if not directory.is_dir():
        return ""
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:  # noqa: BLE001
            continue  # a corrupt record must never open a closure it should not
        if not isinstance(data, dict):
            continue
        if str(data.get("verdict", "")).lower() == "pass":
            who = data.get("reviewed_by") or "unknown reviewer"
            return (f"a passing review verdict is on file for {task} "
                    f"(reviewed_by: {who}, {path.name})")
    return ""


def state(task: str, root: Path | None = None) -> dict:
    """Which of four things this contract is — and the record that says so.

    Returns `{"state": <str|None>, "basis": <str>, "task": ..., "path": ...}`.
    `state` is None when there is no enforceable contract at all; `basis` is
    ALWAYS non-empty, because "no contract" is an answer that must say so rather
    than return silence a caller can misread as a state.

    Precedence — retired → reviewed → executed → armed, first match wins. The
    records are cumulative: a retired contract still carries its original RED and
    its review, so without a stated precedence the answer would depend on which
    record happened to be looked at first.

    Only `retired` stops enforcing. Every other state keeps the leash on, which
    is the conservative direction: the cost of enforcing a finished contract is
    friction, and the cost of releasing an unfinished one is an executor off its
    allowlist.
    """
    root = Path(root) if root is not None else ws_root()
    result = {"task": task, "state": None, "basis": "", "path": ""}

    try:
        path = contract_path(task, root)
    except Exception as exc:  # noqa: BLE001
        result["basis"] = f"contract path unresolvable: {exc}"
        return result
    result["path"] = str(path)

    if not path.is_file():
        result["basis"] = f"no contract file at {path}"
        return result

    try:
        v = validate(task, root=root, run=False)
    except Exception as exc:  # noqa: BLE001
        result["basis"] = f"contract unreadable: {exc}"
        return result
    if not v.get("ok"):
        # A scaffold with nothing enforceable named is not a state. `leash.
        # active_contract()` already refuses it; agreeing here keeps one answer.
        reason = v.get("reason") or "contract is still a scaffold"
        result["basis"] = f"contract present but not enforceable — {reason}"
        return result

    retired = _ledger_retired(task, root)
    if retired:
        result["state"] = RETIRED
        result["basis"] = retired
        return result

    reviewed = _passing_review(task, root)
    if reviewed:
        result["state"] = REVIEWED
        result["basis"] = reviewed
        return result

    checks = _logged_checks(task, root)
    if checks:
        last = checks[-1]
        if int(last.get("exit_code", 1)) == 0:
            result["state"] = EXECUTED
            result["basis"] = (f"the most recent logged `{CHECK_ACTION}` for {task} "
                               f"saw the named command PASS (exit 0, at {last.get('at')})")
            return result
        result["state"] = ARMED
        result["basis"] = (f"the most recent logged `{CHECK_ACTION}` for {task} saw the "
                           f"named command FAIL (exit {last.get('exit_code')}, "
                           f"at {last.get('at')})")
        return result

    result["state"] = ARMED
    result["basis"] = (f"a complete contract at {path} with no logged "
                       f"`{CHECK_ACTION}` yet — armed is the conservative reading")
    return result



# ───────────────────── the fleet-wide scan (PH10-T09) ──────────────────────
#
# `state()` answers "what is THIS contract?". Session start needs "what is
# waiting in this workspace?", which is the same question asked of every
# contract at once. It lives here, beside `state()`, rather than in
# `session_start.py` where the DoD's phrasing points: two modules deciding what
# a contract's state is would give the four-state precedence a second, drifting
# implementation — the defect class this repo keeps paying for.
#
# THE CONSTRAINT THAT SHAPES ALL OF THIS: 46 governed workspaces have no
# `.ai/delegation/` directory at all. This code runs inside the SessionStart
# hook, where a raise costs the operator the entire briefing. So every failure
# path here returns an empty bucket, and with nothing to report the renderer
# returns the empty string — the feature is invisible until a contract exists.


def _review_is_voided(task: str, root: Path) -> bool:
    """Is a review on file for `task` that no longer covers the current diff?

    "A later edit voided the review" is only meaningful when a review EXISTS, so
    a missing record is not voided — it is nothing. Any failure to read (no git,
    no repo, an unreadable record) returns False: this feeds a briefing line, and
    reporting a review as voided because git was unavailable would be inventing
    an alarm out of an error.
    """
    try:
        import delegate_review
        if not delegate_review.read_reviews(task, root):
            return False
        return not delegate_review.check(task, root).get("ok", False)
    except Exception:  # noqa: BLE001
        return False


def find_delegation_status(root: Path | None = None) -> dict:
    """What delegation work is waiting in this workspace.

    Returns `{"armed": [...], "executed": [...], "voided": [...], "basis": str}`.
    `basis` is ALWAYS non-empty — design law 2, the same rule `state()` follows:
    a count with no record behind it says so rather than presenting a bare zero
    as a measurement.

    `retired` and plain `reviewed` contracts appear in no bucket. Retired is the
    one non-enforcing state and needs nothing from anyone; a reviewed contract
    whose review still covers the diff is likewise waiting on no one.
    """
    root = Path(root) if root is not None else ws_root()
    out: dict = {"armed": [], "executed": [], "voided": [], "basis": ""}
    directory = root / ".ai" / "delegation"
    if not directory.is_dir():
        out["basis"] = ("no `.ai/delegation/` directory — this workspace holds no "
                        "contracts, which is the state of every workspace that has "
                        "never delegated")
        return out
    try:
        tasks = sorted(p.stem for p in directory.glob("*.md"))
    except OSError as exc:
        out["basis"] = f"`.ai/delegation/` could not be listed ({exc}) — nothing is claimed"
        return out
    if not tasks:
        out["basis"] = "`.ai/delegation/` exists and holds no contract file"
        return out
    unreadable = 0
    for task in tasks:
        try:
            resolved = state(task, root).get("state")
        except Exception:  # noqa: BLE001
            unreadable += 1
            continue
        if resolved == ARMED:
            out["armed"].append(task)
        elif resolved == EXECUTED:
            out["executed"].append(task)
        elif resolved == REVIEWED and _review_is_voided(task, root):
            out["voided"].append(task)
    out["basis"] = (f"{len(tasks)} contract file(s) in `.ai/delegation/`, each resolved "
                    f"by `delegation.state()`")
    if unreadable:
        out["basis"] += f" — {unreadable} could not be read and are counted nowhere"
    return out


def delegation_actions(status: dict) -> list:
    """The lines Section 6 should raise, or `[]` when nothing is waiting.

    Section 9 reports; Section 6 is where a session is TOLD to do something. An
    `executed` contract is the one that actually blocks a human — work is
    finished and no one has reviewed it — so it is named first.
    """
    actions = []
    for task in status.get("executed") or []:
        actions.append(f"[Reviewer Action] {task} — work is executed and awaiting review "
                       f"(`just delegate-review pass|fail \"note\"`)")
    for task in status.get("voided") or []:
        actions.append(f"[Reviewer Action] {task} — a later edit voided its review; "
                       f"re-review before this can close")
    for task in status.get("armed") or []:
        actions.append(f"[Executor Action] {task} — a contract is armed; the allowlist and "
                       f"iteration limit are enforced on this task")
    return actions


def delegation_status_json(status: dict) -> dict:
    """The `--json` shape: the same three counts the text renders, plus the basis.

    Counts rather than the lists, deliberately — this is the fleet roll-up shape,
    and a task id from another workspace means nothing in an aggregate.
    """
    return {
        "armed": len(status.get("armed") or []),
        "executed": len(status.get("executed") or []),
        "voided": len(status.get("voided") or []),
        "basis": status.get("basis", ""),
    }


def render_delegation_status(status: dict) -> str:
    """The Section 9 block, or EXACTLY "" when nothing is waiting.

    The empty string is the contract, not an implementation detail: 46
    workspaces hold no contracts, and a "0 armed · 0 executed" line would change
    the briefing on all of them the day this deploys, to say nothing that was not
    already true.
    """
    armed = status.get("armed") or []
    executed = status.get("executed") or []
    voided = status.get("voided") or []
    if not (armed or executed or voided):
        return ""
    lines = []
    if executed:
        lines.append(f"   ⏳ awaiting review: {', '.join(executed)}")
    if voided:
        lines.append(f"   ♻️  review voided by a later edit: {', '.join(voided)}")
    if armed:
        lines.append(f"   🔒 armed (leash enforcing): {', '.join(armed)}")
    lines.append(f"      basis: {status.get('basis', '')}")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Scaffold or validate a delegation contract in .ai/delegation/.")
    # Optional ONLY so `--status` can ask about the workspace rather than a task.
    # Every other path still refuses an absent or malformed id below — making it
    # optional must not become a way to run a task command with no task.
    ap.add_argument("task", nargs="?", help="task id, e.g. PH10-T02")
    ap.add_argument("--status", action="store_true",
                    help="what delegation work is waiting in this workspace (PH10-T09)")
    ap.add_argument("--check", action="store_true",
                    help="validate, and on success RUN the named test and record it")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    # PH10-T09. Reads the same `find_delegation_status` the SessionStart briefing
    # reads, so the rendered text and this payload cannot drift apart.
    if args.status:
        status = find_delegation_status()
        if args.json:
            print(json.dumps(delegation_status_json(status), indent=2))
        else:
            block = render_delegation_status(status)
            print(block if block else f"  no delegation work waiting — {status['basis']}")
        return 0

    if not args.task:
        ap.error("a task id is required (or pass --status)")
    task = args.task.strip()
    if not TASK_RE.fullmatch(task):
        print(f"❌ {args.task!r} is not a task id (expected PH#-T##).", file=sys.stderr)
        return 2

    root = ws_root()

    if not args.check and not contract_path(task, root).is_file():
        path = scaffold(task, root=root)
        rel = path.relative_to(root)
        if args.json:
            print(json.dumps({"created": str(rel)}, indent=2))
        else:
            print(f"📝 Delegation contract scaffolded: {rel}")
            print("   Fill in the objective, the failing test command, the file allowlist "
                  "and the iteration limit, THEN run `just delegate-check`.")
        return 0

    v = validate(task, root=root, run=args.check)
    if args.json:
        print(json.dumps(v, indent=2))
    elif v["ok"]:
        print(f"✅ {task} — {v['reason']}")
        print(f"   {Path(v['path']).relative_to(root)}")
    else:
        print(f"🛑 {task} — {v['reason']}")
        if v["exists"]:
            print(f"   {Path(v['path']).relative_to(root)}")
    return 0 if v["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
