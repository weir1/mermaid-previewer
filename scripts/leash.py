#!/usr/bin/env python3
"""
leash.py — enforce the delegation contract (PH10-T03, Goal G8).

PH10-T01 gave a session a declared model and a tier. PH10-T02 gave a task a
written contract: an objective, a failing test, a file allowlist, an
iteration limit. Neither is enforced — an `executor`-tier session could edit
any file it liked and push it, and nothing would stop it. This is what turns
"the contract replaces trust" from a sentence into a gate.

Three independent rules, because they answer three different questions:

## 1. The file allowlist — enforced inside the validation gate

`gate_check.check()` calls `check_allowlist()` as an additional criterion.
When the active task (`task_ledger.active_task`, the one implementation of
"which task is this session working on") has a COMPLETE delegation contract
(`delegation.validate(task, root, run=False)["ok"]` — a scaffold or a
placeholder-filled contract does not count), every file the session's diff
touches must match one of the contract's `## File allowlist` globs. The first
file that does not closes the gate, named by path. No active task, no
contract, or an incomplete one → this is a pure no-op, and an ordinary
(non-delegated) session behaves exactly as it did before this module existed.

"The session's diff" is never re-derived here — `self_review.py` already
computes it (a temp-index staged diff against `head_at_start`, handling
untracked files and the fallback-base cases correctly). `touched_files()`
calls `self_review.resolve_base()` / `self_review._staged_diff()` directly,
the same "reuse a sibling module's private helper" shape `delegation.py`
already uses for `plan._sections`/`plan._is_written`. An UNREADABLE diff is
treated as a violation, not as "nothing changed" — `self_review.check()`
already makes exactly this call for exactly this reason.

Glob matching reuses `policy_engine.glob_match` — the one glob implementation
in this repo (`.ai/policy.yaml` already depends on its exact semantics), not a
second one built on `fnmatch`.

## 2. The iteration limit — enforced inside the validation gate (PH10-T07)

The same "declared, not enforced" gap the allowlist used to have: `delegation.validate()`
requires `## Iteration limit` to be a positive integer before a contract counts as complete,
but nothing ever counted a real attempt against it until now. One genuine `--check` run of
the named test command is one attempt (`delegation._log_check_attempt()`, logged to
`.ai/decision-log/` — the same append-only log every other count in this repo already reads,
not a second counter file). `check_iterations()` compares `delegation.iteration_count(task,
root)` against the contract's own declared limit and funnels through `gate_check.check()`
exactly like the allowlist — one gate, not a second enforcement point. No dedicated override:
the remedy is to re-scope the contract, the same "fix the cause and re-run" rule the gate's
other six criteria already follow.

## 3. No push for a denied tier — enforced at `just push` only

Independent of any contract: `model_registry.resolve_running()["actions"]`
names what the resolved tier may/deny do; `executor` denies `"push"` today.
This is deliberately NOT folded into `gate_check.check()`'s `open` field —
`open` answers "was the last validation trustworthy", which stays true
regardless of who is asking; "may THIS caller push" is a different question,
and folding them together would also block `work-done`/`close`/`commit-all`
for an executor session, which nothing in PH10-T03's DoD asks for (an
executor should be able to finish the contract's test and earn local credit;
only shipping to the remote is denied). `check_push()` is called only from
the `push` recipe, right before `git push` itself.

## Override — no silent bypass, either path

- `push`: a one-shot action at one call site. `leash.py push --override
  "reason"` logs a `gate_override` decision-log entry and proceeds — the same
  shape `close git-push --override`/`work-done --override` already use.
- `allowlist`: checked continuously inside `gate_check.check()`, which has no
  single call site and (unlike every OTHER PH10-T03-adjacent gate) no
  existing override of its own — none of `gate_check`'s other 6 criteria has
  one; you fix the cause and re-run `verify-safe`. Threading `--override`
  through every caller (`work-done`, `push`, `commit-all`, `tt-sync`, `gate`)
  would be four plumbing changes for one escape hatch. Instead
  `leash.py override "<task>" "<reason>"` binds the exception to the CURRENT
  session diff's own sha256 — literally `self_review.digest(root)["sha256"]`,
  the exact value `self_review.py` already computes for its own
  review-coverage contract, not a second hash. `check_allowlist()` treats a
  violation as excused only while the stored override's `diff_sha256` still
  equals the live one; any further edit changes the diff, changes the hash,
  and silently re-closes the gate — the identical "any further edit voids the
  record" property `self_review.py` already has.

Usage:
  leash.py allowlist                       # is the active contract's allowlist satisfied?
  leash.py iterations                      # is the active contract under its iteration limit?
  leash.py push                            # may the running model's tier push?
  leash.py push --override "reason"        # log an exception and allow this push
  leash.py override "PH10-T02" "reason"    # excuse the CURRENT diff's allowlist violation
  leash.py --json   (with any subcommand)
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

EXIT_BLOCKED = 1


def ws_root() -> Path:
    import subprocess
    try:
        t = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                    stderr=subprocess.DEVNULL, text=True).strip()
        if t:
            return Path(t)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _log(kind: str, decision: str, reason: str, root: Path | None = None,
         action: str = "", **fields) -> None:
    """Best effort — a logging failure must never turn a refusal into an
    allow, or vice versa. Same contract as session_budget.py's own `_log`."""
    try:
        import decision_log as dl
        dl.record(kind, decision, "leash", root=root or ws_root(),
                  action=action, reason=reason, **fields)
    except Exception:  # noqa: BLE001
        pass


# ─────────────────────────────── the active contract ─────────────────────────

def active_contract(root: Path | None = None) -> dict | None:
    """The active task's delegation contract, IF it is complete — None for "no
    active task", "no contract file", or "contract still a scaffold". A
    contract that is not yet fully filled in has nothing enforceable named."""
    root = root or ws_root()
    try:
        import task_ledger
        task = task_ledger.active_task(root)
    except Exception:  # noqa: BLE001
        return None
    if not task:
        return None
    try:
        import delegation
        path = delegation.contract_path(task, root)
        if not path.is_file():
            return None
        v = delegation.validate(task, root=root, run=False)
    except Exception:  # noqa: BLE001
        return None
    if not v.get("ok"):
        return None
    # PH10-T08: a RETIRED contract stops enforcing, with no file moved. Before
    # this, a fulfilled contract kept constraining every later session that made
    # the same task active, and the documented remedy was for the operator to
    # remember to move the file — *"dont leave a manual step, if a human has to
    # remember to move the file then its broken."* Retirement is derived from the
    # ledger's own checkbox, so it cannot be forged by editing the contract.
    #
    # Only `retired` releases. Any other state — including an unreadable one —
    # keeps the leash on: enforcing a finished contract costs friction, releasing
    # an unfinished one costs an executor off its allowlist.
    try:
        if delegation.state(task, root).get("state") == delegation.RETIRED:
            return None
    except Exception:  # noqa: BLE001
        pass
    return {"task": task, "allowlist": v["allowlist"], "path": v["path"],
            "iteration_limit": v.get("iteration_limit")}


def touched_files(root: Path | None = None) -> list | None:
    """Every path the session's diff touches, or None if the diff could not be
    read (an unreadable diff must be treated as a violation, never as 'nothing
    changed' — see self_review.check()'s identical rule)."""
    root = root or ws_root()
    import self_review
    base = self_review.resolve_base(root)["base"]
    ok, text = self_review._staged_diff(root, base, "--name-only")
    if not ok:
        return None
    return [line for line in text.splitlines() if line.strip()]


# ─────────────────────────────────── checks ──────────────────────────────────

def check_allowlist(root: Path | None = None) -> dict:
    """{ok, task, offending, allowlist, reason}. `ok=True` with an empty
    `task` means "not enforced" (no active, complete contract) — the no-op
    case every ordinary session hits."""
    root = root or ws_root()
    v = {"ok": True, "task": "", "offending": "", "allowlist": [], "reason": ""}
    contract = active_contract(root)
    if not contract:
        v["reason"] = "no active, complete delegation contract — allowlist not enforced."
        return v
    v["task"] = contract["task"]
    v["allowlist"] = contract["allowlist"]

    files = touched_files(root)
    if files is None:
        v["ok"] = False
        v["reason"] = (f"{contract['task']} has an active contract but the session diff "
                       "could not be read — an unreadable diff is treated as a violation, "
                       "not as 'nothing changed'.")
        return v

    import policy_engine
    import gate_check
    for f in files:
        if f.startswith(gate_check.BOOKKEEPING):
            # The protocol's own machinery, not the task's work — running the
            # required `verify-safe` writes evidence.json; a review writes
            # .ai/decision-log/; neither is something the executor chose to
            # touch. Same category gate_check.BOOKKEEPING already carries for
            # freshness dating, reused rather than a second list (see the
            # plan's corrected reasoning — found live: without this, a
            # legitimately-scoped delegation could never pass `verify-safe`
            # at all, since evidence.json is not normally on any allowlist).
            continue
        if not any(policy_engine.glob_match(pat, f) for pat in contract["allowlist"]):
            excuse = _matching_override(root, contract["task"])
            if excuse:
                v["reason"] = (f"{f!r} is outside {contract['task']}'s allowlist, but an "
                               f"override covers this exact diff: {excuse['reason']!r} "
                               f"(recorded {excuse['recorded_at']}).")
                return v
            v["ok"] = False
            v["offending"] = f
            v["reason"] = (f"{f!r} is outside {contract['task']}'s allowlist "
                           f"({', '.join(contract['allowlist'])}).")
            return v

    v["reason"] = f"every touched file is within {contract['task']}'s allowlist."
    return v


def check_iterations(root: Path | None = None) -> dict:
    """{ok, task, count, limit, reason}. `ok=True` with an empty `task`
    means "not enforced" (no active, complete contract) — the same no-op
    shape `check_allowlist()` already has.

    No dedicated override, unlike the allowlist: the remedy is to re-scope
    the contract (raise `## Iteration limit`, or narrow the objective) —
    the same "fix the cause and re-run" rule the gate's other six criteria
    already follow (PH10-T07, see the plan's Approach for why)."""
    root = root or ws_root()
    v = {"ok": True, "task": "", "count": 0, "limit": None, "reason": ""}
    contract = active_contract(root)
    if not contract:
        v["reason"] = "no active, complete delegation contract — iteration limit not enforced."
        return v
    v["task"] = contract["task"]
    limit = contract.get("iteration_limit")
    v["limit"] = limit
    if not limit:
        # Defensive only — active_contract() only ever returns a COMPLETE
        # contract, and validate() already refuses one with no positive
        # integer limit, so this cannot happen via the normal path.
        return v

    import delegation
    count = delegation.iteration_count(contract["task"], root)
    v["count"] = count
    if count > limit:
        v["ok"] = False
        v["reason"] = (f"{contract['task']} has made {count} attempt(s) against a declared "
                       f"limit of {limit} — re-scope the contract (raise the `## Iteration "
                       "limit` or narrow the objective) before the next `delegate-check` run.")
        return v
    v["reason"] = f"{contract['task']}: {count}/{limit} attempt(s) used."
    return v


def check_push(root: Path | None = None) -> dict:
    """{ok, model, tier, reason}. False when the resolved tier's `denies` list
    names 'push'."""
    root = root or ws_root()
    import model_registry
    resolved = model_registry.resolve_running(root)
    tier = resolved["tier"]
    denied = "push" in (resolved.get("actions", {}).get("denies") or [])
    v = {"ok": not denied, "model": resolved["name"], "tier": tier, "reason": ""}
    if denied:
        v["reason"] = (f"model {resolved['name']!r} resolves to tier {tier!r}, which denies "
                       "push. A planner/reviewer-tier model, or a human, must push this diff.")
    else:
        v["reason"] = f"tier {tier!r} may push."
    return v


# ─────────────────────────────── the allowlist override ──────────────────────

def _override_state_key() -> str:
    return "leash_override"


def _matching_override(root: Path, task: str) -> dict | None:
    """The stored allowlist override, if one exists AND still covers the
    CURRENT session diff. Any edit after the override was recorded changes
    the diff's sha256, so a stale override silently stops applying."""
    import session_budget
    state = session_budget.load_raw()
    if not state:
        return None
    excuse = state.get(_override_state_key())
    if not excuse or excuse.get("task") != task:
        return None
    import self_review
    current = self_review.digest(root)["sha256"]
    if excuse.get("diff_sha256") != current:
        return None
    return excuse


def record_allowlist_override(task: str, reason: str, root: Path | None = None) -> dict:
    """Excuse the CURRENT diff's allowlist violation for `task`. Bound to
    `self_review.digest()`'s own sha256 — the same value self-review already
    uses, not a second hash — so any further edit voids it automatically."""
    root = root or ws_root()
    import self_review
    import session_budget
    from datetime import datetime, timezone

    d = self_review.digest(root)
    entry = {"task": task, "reason": reason.strip(),
             "diff_sha256": d["sha256"],
             "recorded_at": datetime.now(timezone.utc).isoformat()}
    state = session_budget.load_raw() or session_budget.new_state(root)
    state[_override_state_key()] = entry
    session_budget.save(state)
    _log("override", "gate_override", f"allowlist override for {task}: {reason}",
         root=root, action="leash allowlist", task=task)
    return entry


# ────────────────────────────────────── CLI ──────────────────────────────────

def _cmd_allowlist(args, root: Path) -> int:
    v = check_allowlist(root)
    if args.json:
        print(json.dumps(v, indent=2))
    elif v["ok"]:
        print(f"  ✅ {v['reason']}")
    else:
        print(f"  🛑 BLOCKED — {v['reason']}")
    return 0 if v["ok"] else EXIT_BLOCKED


def _cmd_iterations(args, root: Path) -> int:
    v = check_iterations(root)
    if args.json:
        print(json.dumps(v, indent=2))
    elif v["ok"]:
        print(f"  ✅ {v['reason']}")
    else:
        print(f"  🛑 BLOCKED — {v['reason']}")
    return 0 if v["ok"] else EXIT_BLOCKED


def _cmd_push(args, root: Path) -> int:
    v = check_push(root)
    if args.override:
        _log("override", "gate_override", f"push override: {args.override}",
             root=root, action="leash push", model=v["model"], tier=v["tier"])
        if not args.json:
            print(f"  ⚠️  OVERRIDE — push allowed despite tier {v['tier']!r}.")
            print(f"     Reason: {args.override}")
            print("     Recorded in .ai/decision-log/ — this is visible in `just audit`.")
        else:
            print(json.dumps({**v, "ok": True, "override": args.override}, indent=2))
        return 0
    _log("gate", "allow" if v["ok"] else "deny", v["reason"], root=root,
         action="leash push", model=v["model"], tier=v["tier"])
    if args.json:
        print(json.dumps(v, indent=2))
    elif v["ok"]:
        print(f"  ✅ {v['reason']}")
    else:
        print(f"  🛑 PUSH REFUSED — {v['reason']}")
        # NOT `just leash-override` — that's the task-scoped ALLOWLIST
        # override (leash.py override <task> <reason>), a different
        # mechanism bound to an active delegation contract. This refusal is
        # the tier/push rule, whose own override is `push`'s own optional
        # param (leash.py push --override <reason>) — no dedicated recipe
        # name of its own. Found live 2026-08-10 (knownIssues.md) after a
        # session followed the old (wrong) hint verbatim and hit a usage
        # error naming two required args it didn't have.
        print("     → `just push \"reason\"` if the exception is genuine.")
    return 0 if v["ok"] else EXIT_BLOCKED


def _cmd_override(args, root: Path) -> int:
    task = (args.task or "").strip()
    reason = (args.reason or "").strip()
    if not task or not reason:
        print("❌ usage: leash.py override \"<task>\" \"<reason>\"", file=sys.stderr)
        return 1
    entry = record_allowlist_override(task, reason, root)
    if args.json:
        print(json.dumps(entry, indent=2))
    else:
        print(f"  ⚠️  OVERRIDE recorded for {task} — bound to the current diff "
              f"(sha {entry['diff_sha256'][:12]}).")
        print(f"     Reason: {reason}")
        print("     Any further edit changes the diff and voids this override.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Enforce the delegation contract (PH10-T03).")
    sub = ap.add_subparsers(dest="cmd")

    p_allow = sub.add_parser("allowlist", help="check the active contract's file allowlist")
    p_allow.add_argument("--json", action="store_true")

    p_iter = sub.add_parser("iterations", help="check the active contract's iteration limit")
    p_iter.add_argument("--json", action="store_true")

    p_push = sub.add_parser("push", help="may the running model's tier push?")
    p_push.add_argument("--override", default="", help="written reason — logs an exception")
    p_push.add_argument("--json", action="store_true")

    p_over = sub.add_parser("override", help="excuse the CURRENT diff's allowlist violation")
    p_over.add_argument("task")
    p_over.add_argument("reason")
    p_over.add_argument("--json", action="store_true")

    args = ap.parse_args()
    root = ws_root()

    if args.cmd == "push":
        return _cmd_push(args, root)
    if args.cmd == "override":
        return _cmd_override(args, root)
    if args.cmd == "iterations":
        return _cmd_iterations(args, root)
    args.json = getattr(args, "json", False)
    return _cmd_allowlist(args, root)


if __name__ == "__main__":
    sys.exit(main())
