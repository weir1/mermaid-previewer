#!/usr/bin/env python3
"""
delegate_review.py — the review handback (PH10-T04, Goal G8).

PH10-T02 gave a task a written contract. PH10-T03 enforced it: an executor
cannot touch a file outside the allowlist, and cannot push. Neither answers
the operator's own last clause — *"after that u just review work."* Today
that review is `self_review.py`'s general, self-reported "claude" check,
satisfiable by the same actor the operator does not trust unsupervised. This
closes that gap for a DELEGATED task specifically: a verdict bound to the
diff's content hash, recordable only by a session whose resolved tier is
actually PERMITTED to approve or reject — never by the executor itself.

## Why a session cannot approve its own work

`.ai/models.yaml`'s `executor` tier permits only `execute-within-allowlist`
and explicitly denies `approve-own-review`. Rather than re-encode that as a
second rule here, `record()` checks the SAME `tiers:`/`permits`/`denies`
data `leash.check_push()` already reads via `model_registry.resolve_running()`
— `"approve"` for a `pass` verdict, `"reject"` for a `fail` one. Today only
the `reviewer` tier permits either action, so an `executor` (and, just as
deliberately, a `planner` — who *reviews* per the registry's own notes, but
does not *approve/reject* a delegation) cannot record ANY verdict. One
source of truth for "who may do what" — this module adds no second copy of
it that could drift.

## The record is bound to the diff, not to a promise — the PH7-T04 pattern

Reuses `self_review.digest()` verbatim (not a second diff implementation):
the sha256 of the exact staged diff, computed the identical way PH7-T04's
self-review and PH10-T03's allowlist override already do. Edit anything
after a `pass` is recorded and the digest moves, `check()` no longer finds a
covering record, and `close git-push` (see `session_budget.verify_closure`)
refuses again — the same "any further edit voids it" property those two
already have, reused rather than reinvented a third time.

## Where records live, and why not `.ai/reviews/`

`.ai/delegation/reviews/{task}/{stamp}-{sha}.json` — a NEW, task-scoped
directory, deliberately separate from `self_review.py`'s `.ai/reviews/`.
That directory answers "has ANY reviewer looked at ANY session's diff, ever"
(required for every closure); this one answers "has a reviewer-tier model
approved THIS delegated task's diff" (required only when a contract is
active). Sharing one directory would let either question's record satisfy
the other purely by coincidental sha256 overlap.

`gate_check.BOOKKEEPING` gains exactly this one new path — the review
records are the protocol's own machinery, not the task's work, the same
reasoning already applied to `evidence.json`/`.ai/decision-log/`. The
contract file itself (`.ai/delegation/*.md`) is deliberately NOT exempted:
`reviewed_by` is recorded only inside this JSON record, never written back
into the contract's `## Attribution` section, because that section lives in
the SAME file as the `## File allowlist` the leash enforces — exempting the
file to let a review land would also let an executor hand-widen its own
allowlist on the very next read. See `.ai/plans/PH10-T04.md`.

Usage:
  delegate_review.py pass "note"       # record, for the CURRENT active contract
  delegate_review.py fail "note"       # record a rejection
  delegate_review.py status            # is the current diff covered by a passing verdict?
  delegate_review.py --json   (with any subcommand)
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

VERDICTS = ("pass", "fail")
ACTION_FOR_VERDICT = {"pass": "approve", "fail": "reject"}
REVIEWS_DIRNAME = Path(".ai") / "delegation" / "reviews"


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
    allow, or vice versa. Same contract as leash.py's/session_budget.py's own `_log`."""
    try:
        import decision_log as dl
        dl.record(kind, decision, "delegate_review", root=root or ws_root(),
                  action=action, reason=reason, **fields)
    except Exception:  # noqa: BLE001
        pass


def reviews_dir(task: str, root: Path | None = None) -> Path:
    return (root or ws_root()) / REVIEWS_DIRNAME / task


# ────────────────────────────── tier gating ───────────────────────────────

def _permitted(root: Path, verdict: str) -> dict:
    """May the running model's resolved tier record `verdict`?

    Reads the SAME permits/denies shape `leash.check_push()` already reads
    from `model_registry.resolve_running()` — never a second schema.
    """
    import model_registry
    resolved = model_registry.resolve_running(root)
    action = ACTION_FOR_VERDICT[verdict]
    permits = resolved.get("actions", {}).get("permits") or []
    return {"ok": action in permits, "resolved": resolved, "action": action}


# ────────────────────────────────── records ────────────────────────────────

def read_reviews(task: str, root: Path | None = None) -> list[dict]:
    """Every recorded review for `task`, newest first. Unreadable files are
    skipped, never fatal — a corrupt record must not be able to open a
    closure it should not, nor crash close git-push."""
    root = root or ws_root()
    directory = reviews_dir(task, root)
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


def record(task: str, verdict: str, note: str, root: Path | None = None) -> dict:
    """Record `verdict` for `task`, bound to the CURRENT session diff.

    Refuses (writes nothing) when the running model's resolved tier does not
    permit the verdict's action — this is what makes self-approval
    structurally impossible, not merely discouraged.
    """
    root = root or ws_root()
    if verdict not in VERDICTS:
        return {"ok": False, "recorded": False,
                "reason": f"verdict must be one of {VERDICTS}, got {verdict!r}."}
    if not note.strip():
        return {"ok": False, "recorded": False,
                "reason": "a review with no note is the unfalsifiable claim this exists to "
                          "prevent — say what you actually checked."}

    perm = _permitted(root, verdict)
    if not perm["ok"]:
        resolved = perm["resolved"]
        return {"ok": False, "recorded": False,
                "reason": (f"model {resolved['name']!r} resolves to tier {resolved['tier']!r}, "
                           f"which does not permit {perm['action']!r}. Only a tier whose "
                           f".ai/models.yaml entry permits {perm['action']!r} may record this "
                           f"verdict for {task} — an executor cannot approve its own work.")}

    import self_review
    d = self_review.digest(root)
    if not d["readable"]:
        return {"ok": False, "recorded": False,
                "reason": f"could not read the diff against {d['base_ref']!r} — git failed."}
    if d["empty"]:
        return {"ok": False, "recorded": False,
                "reason": "there is no diff to review — nothing would be bound to this record."}

    now = datetime.now(timezone.utc)
    entry = {
        "recorded_at": now.isoformat(),
        "task": task,
        "verdict": verdict,
        "note": note.strip(),
        "reviewed_by": perm["resolved"]["name"],
        "reviewer_tier": perm["resolved"]["tier"],
        "self_reported": perm["resolved"]["self_reported"],
        "diff_sha256": d["sha256"],
        "base": d["base"],
        "base_ref": d["base_ref"],
        "base_source": d["base_source"],
        "head": d["head"],
        "files": d["files"],
        "insertions": d["insertions"],
        "deletions": d["deletions"],
    }
    directory = reviews_dir(task, root)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now.strftime('%Y-%m-%d-%H%M%S')}-{d['sha256'][:12]}.json"
    path.write_text(json.dumps(entry, indent=2) + "\n")
    entry["_path"] = str(path.relative_to(root))
    entry["ok"] = True
    entry["recorded"] = True
    entry["reason"] = f"recorded — {verdict} by {entry['reviewed_by']} (tier {entry['reviewer_tier']})."
    return entry


def check(task: str, root: Path | None = None) -> dict:
    """Is the CURRENT session diff covered by a PASSING review for `task`?

    `{ok, reason, digest, review}` — the same shape `self_review.check()`
    returns, deliberately, so callers (session_budget's closure gate) treat
    both the same way.
    """
    root = root or ws_root()
    import self_review
    d = self_review.digest(root)
    v = {"ok": False, "reason": "", "digest": d, "review": None}

    if not d["readable"]:
        v["reason"] = (f"could not read the diff against {d['base_ref']!r} — git failed. "
                       "An unreadable diff is an unreviewed one; it is never 'no changes'.")
        return v

    if d["empty"]:
        # Mirrors self_review.check()'s identical rule: nothing changed against
        # the true session base is a fact; nothing changed against a fallback
        # base is a guess, and `record()` never writes for an empty diff either
        # way — so an empty-but-unauthoritative diff can never be reviewed and
        # must not silently open the closure.
        if d["authoritative"]:
            v["ok"] = True
            v["reason"] = (f"nothing to review — no changes since the session base "
                           f"({d['base'][:8]}).")
        else:
            v["reason"] = (
                f"the diff against {d['base_ref']} is empty, but that base is a fallback "
                f"({d['base_source']}), not the recorded session start — 'nothing changed' "
                "is a guess, not a fact.")
        return v

    matches = [r for r in read_reviews(task, root) if r.get("diff_sha256") == d["sha256"]]
    if not matches:
        prior = read_reviews(task, root)
        v["reason"] = (
            f"no delegation review covers {task}'s current diff "
            f"({d['files']} file(s), sha {d['sha256'][:12]}). "
            + (f"The newest of {len(prior)} record(s) covers "
               f"{str(prior[0].get('diff_sha256'))[:12]} — the diff changed since it was "
               "reviewed, so it no longer applies. "
               if prior else "")
            + f"Record one: `just delegate-review pass \"what you checked\"`.")
        return v

    passing = [r for r in matches if r.get("verdict") == "pass"]
    if not passing:
        newest = matches[0]
        v["review"] = newest
        v["reason"] = (
            f"{task}'s current diff was reviewed at {newest.get('recorded_at')} by "
            f"{newest.get('reviewed_by')} and the verdict was 'fail': "
            f"{newest.get('note') or 'no note'}. Fix the findings (which changes the diff) "
            "and get it re-reviewed.")
        return v

    v["ok"] = True
    v["review"] = passing[0]
    v["reason"] = (f"{task} reviewed at {passing[0].get('recorded_at')} by "
                   f"{passing[0].get('reviewed_by')} (tier {passing[0].get('reviewer_tier')}) "
                   "— verdict pass.")
    return v


# ────────────────────────────────────── CLI ──────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Record/verify a delegation review (PH10-T04).")
    ap.add_argument("command", choices=["pass", "fail", "status"])
    ap.add_argument("note", nargs="?", default="",
                    help="what you actually checked — required for pass/fail")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    root = ws_root()

    import leash
    contract = leash.active_contract(root)
    if not contract:
        msg = "no active, complete delegation contract — nothing to review."
        if args.json:
            print(json.dumps({"ok": False, "reason": msg}))
        else:
            print(f"  🛑 {msg}")
        return 1
    task = contract["task"]

    if args.command == "status":
        v = check(task, root)
        if args.json:
            print(json.dumps(v, indent=2, default=str))
        elif v["ok"]:
            print(f"  ✅ {v['reason']}")
        else:
            print(f"  🛑 {v['reason']}")
        return 0 if v["ok"] else 1

    if not args.note.strip():
        print("❌ a note is required — say what you actually checked.", file=sys.stderr)
        return 1
    entry = record(task, args.command, args.note, root)
    _log("gate", "allow" if entry.get("ok") else "deny", entry.get("reason", ""),
         root=root, action=f"delegate-review {args.command}", task=task)
    if args.json:
        print(json.dumps(entry, indent=2, default=str))
    elif entry.get("ok"):
        print(f"  ✅ {entry['reason']}")
        print(f"     {entry['_path']}")
        print("     Any further edit to the diff changes it and voids this record.")
    else:
        print(f"  🛑 {entry['reason']}", file=sys.stderr)
    return 0 if entry.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
