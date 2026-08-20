#!/usr/bin/env python3
"""
domain_verifier.py — the domain-real half of `just verify-safe` for a non-code
role (PH8-T03, Goal G13).

`run_tests.py` is "pytest for a workspace that has code". This is its opposite
number: the check a role pack's OWN schema names as the gap `verify-safe` must
be able to fail on, for a workspace that has none. The `justfile`'s `verify-safe`
/ `verify-release` recipes call this INSTEAD of `run_tests.py` when the resolved
role is one this module knows how to check; every other role's recipe path is
untouched — see `doc/ROLES_SPEC.md` §4, "the outside is invariant". This module
never touches `gate_check.py` or `evidence.json`; it only decides an exit code,
exactly like `run_tests.py` does today.

Only `executive-coach` is wired here — PH8-T03's agreed cheaper scope, recorded
in `.ai/prework/PH8-T03.md`. Its own `schema.yaml` names the exact gap: a
`protocol.md` (what the operator committed to) with no dated `log.md` entries
(what he actually did) is a plan nobody is following, and that gap is precisely
what this check must be able to fail on. `researcher` / `content-writer` stay
drafts — `role_registry.resolve()` cannot select them — so nothing here runs for
them; `software-engineer` never reaches this module at all, its `verify-safe`
branch stays `run_tests.py`, unchanged.

Usage:
  domain_verifier.py            run the active role's domain check; exit 0/1
  domain_verifier.py --json     machine-readable verdict

Fails CLOSED, never silently open. A role with no registered check reports
`applicable: false` and exits 0 — the `justfile` branch never calls this module
for such a role in the first place, so this is the defensive second layer, not
the only one. An internal error (unreadable `log.md`, a `role_registry`
exception) exits 1 and names the reason: the same "never a silent pass" rule
`evidence-pack.sh` already enforces for its own missing-script case.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import role_registry as rr  # noqa: E402

# Same dating convention AI_CHANGELOG.md / activeContext.md already use in this repo.
DATED_ENTRY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _check_executive_coach(root: Path) -> dict:
    """`log.md` must carry at least one dated entry.

    The gap named in the pack's own `schema.yaml`: a `protocol.md` with nothing
    logged against it is a plan nobody is following, and that is exactly what
    this check must be able to fail on.
    """
    log_path = root / ".ai" / "memory-bank" / "log.md"
    if not log_path.is_file():
        return {"ok": False, "detail": f"{log_path} does not exist"}
    text = log_path.read_text(encoding="utf-8")
    if DATED_ENTRY_RE.search(text):
        return {"ok": True, "detail": "log.md carries at least one dated entry"}
    return {"ok": False,
            "detail": "log.md has no dated entry (YYYY-MM-DD) — a protocol with "
                       "nothing logged against it"}


# role id -> callable(root: Path) -> {"ok": bool, "detail": str}. Adding a role
# here is the only step: the justfile dispatch and `run()` below need no change.
CHECKS = {
    "executive-coach": _check_executive_coach,
}


def run(root: Path | None = None, roles_root: Path | None = None) -> dict:
    """The active role's domain verdict, or `applicable: False` when this module
    has no check registered for it. `roles_root` is a test seam — same shape as
    every `role_registry` function; production code never passes it."""
    root = root or rr.ws_root()
    resolved = rr.resolve(root, roles_root=roles_root)
    role_id = resolved.get("id", "")
    check = CHECKS.get(role_id) if resolved.get("ok") else None
    if check is None:
        return {"ok": True, "role": role_id, "applicable": False,
                 "detail": f"no domain check registered for role '{role_id}' — not applicable"}
    try:
        result = check(root)
    except Exception as exc:  # noqa: BLE001 — fail closed, never a silent pass.
        return {"ok": False, "role": role_id, "applicable": True,
                 "detail": f"domain check raised: {exc}"}
    result["role"] = role_id
    result["applicable"] = True
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Run the active role's domain-real verify-safe check.")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    result = run()
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        icon = "✅" if result["ok"] else "❌"
        print(f"{icon} domain_verifier[{result['role']}]: {result['detail']}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
