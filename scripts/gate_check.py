#!/usr/bin/env python3
"""
gate_check.py — THE validation gate. One implementation of the contract.

Why this file exists: the gate used to be re-implemented in three places that
disagreed. `doctor.py` read only `status` and reported ✅ on an onboarding stub;
`session_start.py` caught the stub; nothing on the side-effect path (push,
commit-all, tt-sync) checked anything at all. Every consumer now calls this.

The contract (.agents/rules/00-validation-gate.md §3) — ALL must hold:
  0. the active task, if `[complex]`, has a written plan (PH16-T28 — checked FIRST)
  1. .ai/memory-bank/evidence.json exists and parses
  2. status   == "passed"
  3. exit_code == 0
  4. pipeline in {"safe", "release"}   — "onboarding"/"unverified" is NOT proof
  5. FRESH: validated_at is newer than the newest tracked working-tree change
  6. task_id matches the active task, when one is active (empty = maintenance, ok)
  7. an active delegation contract's file allowlist holds (PH10-T03)
  8. that contract's iteration limit is not exceeded (PH10-T07)

Usage:
  gate_check.py                    # human-readable; exit 0 pass / 1 blocked
  gate_check.py --json             # machine-readable verdict
  gate_check.py --quiet            # exit code only
  gate_check.py --require-task PH5-T01
  gate_check.py --action "git push"   # gating a real side effect → recorded in the decision log

Exit codes: 0 gate open · 1 gate blocked · 2 workspace/setup error.

## What gets written to `.ai/decision-log/` (PH6-T13)
A verdict is logged when the caller declares an `--action`, i.e. when the gate is
actually GOVERNING something (`just push`, `just commit-all`, `just tt-sync`,
`ticktick_sync.py`). Observers that merely *report* the gate — `doctor`,
`session-start`, `fleet-status` — pass no action and are not logged: a status poll is a
query, not a decision, and counting polls would drown the real stops in noise.
`--log` forces a record anyway; `--no-log` suppresses one.
"""

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

VALID_PIPELINES = {"safe", "release"}
# evidence-pack.sh stamps whole seconds (%Y-%m-%dT%H:%M:%SZ) while mtimes are
# sub-second, so a file written in the same second as the evidence can look
# newer by up to 1s. Tolerate that truncation — nothing more.
CLOCK_SKEW_S = 2.0
# Files that evidence is *about* — changes here invalidate it. Everything else
# (memory bank, changelog, session state) is bookkeeping written after the run.
#
# PH16-T06: this used to name only two files inside .ai/memory-bank/
# (evidence.json, test-run.json), so the comment above claimed a broader
# exemption than the tuple granted. AGENTS.md's SESSION END CHECKLIST writes
# the *whole* memory bank (activeContext.md, progress.md, knownIssues.md, ...)
# and AI_CHANGELOG.md AFTER `verify-safe` has already stamped evidence.json —
# so following the protocol as written reliably flipped a passing gate to
# STALE. Excluded by directory/file now, matching what the comment always
# claimed, rather than by an enumeration that has already proven itself
# incomplete once (and would again the next time a memory-bank file is added).
#
# Same session, same fix, one more instance: `just session-end` runs
# `rotate_changelog.py`, which moves old entries into `AI_CHANGELOG_archive/`
# — a changelog rotation is exactly the "changelog" the comment already
# names, just archived rather than live. This directory did not exist until
# this session's own first-ever rotation, so the gap was latent, not missed.
BOOKKEEPING = (
    ".ai/memory-bank/",
    "AI_CHANGELOG.md",
    "AI_CHANGELOG_archive/",
    ".ai/session-state.json",
    ".ai/session-log/",
    ".ai/decision-log/",
    ".ai/handover/",
    ".ai/session-ledger.md",
    # PH10-T04: delegation review records are the protocol's own machinery
    # (the same reasoning already applied to evidence.json) — NOT the whole
    # `.ai/delegation/` tree. The contract files themselves
    # (`.ai/delegation/*.md`) stay OUTSIDE bookkeeping deliberately: they
    # carry the `## File allowlist` the leash enforces, and exempting them
    # would let an executor hand-widen its own allowlist on the leash's very
    # next (always-fresh) read. See `.ai/plans/PH10-T04.md`.
    ".ai/delegation/reviews/",
)


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:
        pass
    return Path(".").resolve()


def _parse_ts(value: str):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _newest_change(root: Path) -> tuple[float, str]:
    """Newest mtime among git-tracked + untracked-but-not-ignored files,
    excluding bookkeeping the gate itself writes. Returns (mtime, path)."""
    try:
        out = subprocess.check_output(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=root, stderr=subprocess.DEVNULL, text=True)
    except Exception:
        return 0.0, ""
    newest, where = 0.0, ""
    for rel in out.splitlines():
        if not rel or rel.startswith(BOOKKEEPING):
            continue
        f = root / rel
        try:
            m = f.stat().st_mtime
        except OSError:
            continue
        if m > newest:
            newest, where = m, rel
    return newest, where


def _active_task(root: Path) -> str:
    """The task declared `(In Progress)` in activeContext.md — delegated to
    `task_ledger.active_task`, the single implementation of that rule.

    This used to be its own copy that matched *any line containing* the marker
    and took the first task id on it, so a prose summary quoting the marker
    resolved to a phantom task. Three copies of the rule disagreed; now there is
    one. Falls back to no-task if the module is missing (older deployments),
    which is the safe direction: rule 6 simply doesn't fire.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import task_ledger
        return task_ledger.active_task(root)
    except Exception:  # noqa: BLE001
        return ""


def _plan_debt(root: Path, task: str) -> dict:
    """Does the active task owe a plan it has not written? (PH16-T28)

    `[complex]` tasks have owed a plan since PH7-T03, and the requirement was
    implemented in exactly one place: `session_budget.verify_work_claim()`, which
    is reached only through `just work-done` — the command that *claims a
    finished task*. So the rule permitted this order: mark the task `[complex]`,
    do the work, mutate the world, then get refused credit for having no plan.

    That is not a hypothetical ordering. PH16-T27 was marked `[complex]`
    precisely because it wrote into 45 workspaces; the deploy ran, and `work-done`
    then exited 7 for a missing plan. Its **Rollback** section — the one part of a
    plan whose entire purpose is to be known before the first write — was authored
    after the last one, and writing it was the moment it became clear that no
    fleet rollback exists at all.

    So the debt is checked here instead, where every recipe with real blast radius
    already arrives: `fleet-upgrade --apply`, `fleet-verify --apply`,
    `route-reaper --apply`, `push`, `tt-sync` and `commit-all` all gate on this
    module. The alternative — a `just plan --check` line in each of those recipes
    — would put one rule in six places, each needing its own copy of "which task
    is active", which is the drift this file was created to end.

    Composed from the two existing single implementations rather than re-deriving
    either: `task_ledger.find_task()` owns the `[complex]` marker rule (including
    the mention-vs-declaration distinction that has been wrong twice), and
    `plan.validate()` owns what makes a plan a plan rather than a scaffold.

    **Fails OPEN**, exactly as steps 7 and 8 do: a fleet workspace whose deployed
    `plan.py` or `task_ledger.py` predates this must not newly break because the
    kernel learned a new rule. It is also a strict no-op with no active task, or
    an active task that is not `[complex]` — the two cases that cover nearly every
    session.

    What this does NOT do is make thinking precede *code*: nothing intercepts the
    first edit, because the OS has no start-of-task hook. It makes thinking
    precede the irreversible part, which is where the cost actually is.
    """
    if not task:
        return {"ok": True, "reason": ""}
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import task_ledger
        import plan as plan_mod
        info = task_ledger.find_task(task, root=root)
        if not info.get("complex"):
            return {"ok": True, "reason": ""}
        verdict = plan_mod.validate(task, root=root)
    except Exception:  # noqa: BLE001
        return {"ok": True, "reason": ""}
    if verdict.get("ok"):
        return {"ok": True, "reason": f"{task} is `[complex]` and its plan is written."}
    return {"ok": False, "reason": verdict.get("reason", "no written plan.")}


def failure_cause(ev: dict) -> str:
    """What closed the gate, from the evidence — or that nothing recorded it (PH16-T35).

    Before PH16-T35 a closed gate said only `status='failed'`. All six workspaces blocked
    on 2026-08-14 carried byte-equivalent evidence, so the kernel could see six red lights
    and not one reason, and "each needs its own session" meant a session to *find out*.

    **Evidence with no `failed_stage` is reported as not recorded, never guessed.** Forty-
    three workspaces hold packs written before this field existed; inventing a plausible
    cause for them would be the `None`-is-not-`set()` mistake PH16-T24 paid for, and the
    reader could not tell an inferred cause from a measured one.
    """
    stage = (ev.get("failed_stage") or "").strip()
    if not stage:
        return ("Cause not recorded — this evidence was written by a pack older than "
                "PH16-T35. Re-run `just verify-safe` there to record which stage failed.")
    if stage == "unknown":
        return ("Cause not recorded — the failing stage could not be named (the digest "
                "was unavailable). Re-run `just verify-safe` to name it.")
    checks = [str(c) for c in (ev.get("failed_checks") or [])]
    if not checks:
        return f"Failed in the {stage} stage; no individual check could be named."
    note = ev.get("failure_note") or ""
    tail = f" ({note})" if note else ""

    # PH24-T13 — the name says what broke, the reason says what to do about it, and this
    # workspace proved they are not the same fact: a gate naming only
    # `test_progress_md_is_actually_within_the_line_budget` sent three consecutive sessions
    # at the archiver, while the message the test printed said the archiver could not fix
    # it. A check with no recorded reason renders exactly as it did before — evidence from
    # an older pack, or a runner that printed none, must not be given one.
    reasons = ev.get("failed_reasons") or {}
    if not isinstance(reasons, dict):
        reasons = {}
    rendered = [f"{c} — {reasons[c]}" if reasons.get(c) else c for c in checks]
    # `;` rather than `,` because a reason may legitimately contain commas.
    return f"Failed in the {stage} stage: {'; '.join(rendered)}.{tail}"


def check(root: Path | None = None, require_task: str = "") -> dict:
    root = root or ws_root()
    ev_path = root / ".ai" / "memory-bank" / "evidence.json"
    v = {"open": False, "reason": "", "checks": {}, "evidence_path": str(ev_path),
         "remediation": "Run `just verify-safe`, then re-check."}

    # Step 0 (PH16-T28): the plan debt, BEFORE evidence is even read.
    #
    # Deliberately first. A plan is a precondition on having started, not a
    # property of the proof — and it is both the cheapest debt to state and the
    # earliest in time. Checked last, a session with stale evidence and no plan
    # would be sent to run the whole pipeline and only then told the one thing it
    # needed to hear first. This mirrors `verify_work_claim`, which checks the
    # plan before the gate for the same reason.
    want = require_task or _active_task(root)
    v["checks"]["expected_task"] = want
    plan_v = _plan_debt(root, want)
    v["checks"]["plan"] = plan_v["reason"]
    if not plan_v["ok"]:
        v["reason"] = (f"BLOCKED before any side effect — {want} is marked `[complex]` "
                       f"and {plan_v['reason']}")
        v["remediation"] = (f"Write the plan first: `just plan \"{want}\"` scaffolds it — "
                            "fill every section, especially Rollback, BEFORE the change "
                            "this gate is protecting.")
        return v

    if not ev_path.exists():
        v["reason"] = "evidence.json not found — nothing proves this workspace was validated."
        return v
    try:
        ev = json.loads(ev_path.read_text())
    except Exception as exc:  # noqa: BLE001
        v["reason"] = f"evidence.json is not valid JSON ({exc})."
        return v

    status = ev.get("status")
    exit_code = ev.get("exit_code")
    pipeline = ev.get("pipeline")
    validated_at = ev.get("validated_at") or ev.get("timestamp") or ""
    task_id = ev.get("task_id", "") or ""

    v["checks"]["status"] = status
    v["checks"]["exit_code"] = exit_code
    v["checks"]["pipeline"] = pipeline
    v["checks"]["validated_at"] = validated_at
    v["checks"]["task_id"] = task_id

    if pipeline not in VALID_PIPELINES:
        v["reason"] = (f"pipeline={pipeline!r} is not proof of validation "
                       f"(expected one of {sorted(VALID_PIPELINES)}). "
                       "This is a bootstrap placeholder, not a verified run.")
        return v
    if status != "passed":
        v["reason"] = (f"status={status!r} — the last validation did not pass. "
                       f"{failure_cause(ev)}")
        return v
    if exit_code != 0:
        v["reason"] = (f"exit_code={exit_code!r} — the validation pipeline reported "
                       f"failure. {failure_cause(ev)}")
        return v

    ts = _parse_ts(validated_at)
    if ts is None:
        v["reason"] = f"validated_at={validated_at!r} is missing or unparseable — freshness unknown."
        return v
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    newest, where = _newest_change(root)
    v["checks"]["newest_change"] = where
    if newest and newest > ts.timestamp() + CLOCK_SKEW_S:
        changed = datetime.fromtimestamp(newest, timezone.utc).isoformat(timespec="seconds")
        v["reason"] = (f"STALE — `{where}` changed at {changed}, after evidence was written at "
                       f"{ts.isoformat(timespec='seconds')}. Prior evidence does not cover it.")
        return v

    # `want` was resolved once at step 0 and is reused here rather than re-read:
    # two reads of activeContext.md in one verdict can disagree, and the gate
    # must not check the plan of one task and the evidence of another.
    if want and task_id and task_id != want:
        v["reason"] = (f"task mismatch — evidence covers {task_id!r} but the active task is {want!r}.")
        return v

    # Step 7 (PH10-T03): an active, complete delegation contract's file
    # allowlist. A no-op for every ordinary session — only fires when the
    # active task actually has a filled-in contract. Missing/broken leash.py
    # fails OPEN, the same convention `_active_task` already uses for a
    # missing task_ledger (an undeployed fleet workspace must not newly break).
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import leash
        leash_v = leash.check_allowlist(root)
    except Exception:  # noqa: BLE001
        leash_v = {"ok": True, "reason": ""}
    v["checks"]["leash_allowlist"] = leash_v.get("reason", "")
    if not leash_v.get("ok", True):
        v["reason"] = f"BLOCKED by the delegation allowlist — {leash_v['reason']}"
        return v

    # Step 8 (PH10-T07): the same active, complete delegation contract's
    # iteration limit. Identical no-op discipline and identical fail-open
    # convention as step 7 — only fires with a real contract in flight.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import leash
        iter_v = leash.check_iterations(root)
    except Exception:  # noqa: BLE001
        iter_v = {"ok": True, "reason": ""}
    v["checks"]["leash_iterations"] = iter_v.get("reason", "")
    if not iter_v.get("ok", True):
        v["reason"] = f"BLOCKED by the delegation iteration limit — {iter_v['reason']}"
        return v

    v["open"] = True
    v["reason"] = (f"Gate open — {pipeline} pipeline passed at {ts.isoformat(timespec='seconds')}"
                   + (f" for {task_id}" if task_id else " (maintenance session, no active task)") + ".")
    v["remediation"] = ""
    return v


def _log_verdict(root: Path, verdict: dict, action: str) -> None:
    """Append the gate verdict to `.ai/decision-log/`. Best effort — a logging failure
    must never turn an OPEN gate into a blocked one, or vice versa."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import decision_log as dl
        checks = verdict.get("checks", {})
        extra = {}
        # The gate already resolved which task the evidence must cover; reuse it rather
        # than letting decision_log re-read activeContext.md and possibly disagree.
        task = checks.get("expected_task") or checks.get("task_id")
        if task:
            extra["task"] = task
        dl.record("gate", "open" if verdict["open"] else "blocked", "gate_check", root=root,
                  action=action, reason=verdict.get("reason", ""), **extra)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description="God Mode validation gate — the single check.")
    ap.add_argument("--json", action="store_true", help="emit the verdict as JSON")
    ap.add_argument("--quiet", action="store_true", help="exit code only")
    ap.add_argument("--require-task", default="", help="task id the evidence must cover")
    ap.add_argument("--action", default="", help="label the side effect being gated (for the message)")
    ap.add_argument("--log", action="store_true",
                    help="record this verdict in .ai/decision-log/ even without --action")
    ap.add_argument("--no-log", action="store_true",
                    help="never record this verdict (for observers that only report the gate)")
    args = ap.parse_args()

    root = ws_root()
    if not (root / ".ai").is_dir():
        print("❌ Not a God Mode workspace (no .ai/) — run from the workspace root.", file=sys.stderr)
        return 2

    v = check(root, require_task=args.require_task)
    if (args.action or args.log) and not args.no_log:
        _log_verdict(root, v, args.action)

    if args.json:
        print(json.dumps(v, indent=2))
    elif not args.quiet:
        what = f" for: {args.action}" if args.action else ""
        if v["open"]:
            print(f"✅ VALIDATION GATE OPEN{what}\n   {v['reason']}")
        else:
            print(f"🛑 VALIDATION GATE BLOCKED{what}\n   {v['reason']}")
            if v["remediation"]:
                print(f"   → {v['remediation']}")
    return 0 if v["open"] else 1


if __name__ == "__main__":
    sys.exit(main())
