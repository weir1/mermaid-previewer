#!/usr/bin/env python3
"""
policy_check.py — God Mode preflight policy checker (CLI over policy_engine).

Returns a JSON blast-radius verdict + an exit code for a single operation, so it
can run as a Claude PreToolUse guard or an Antigravity preflight step:

  just policy-check --op write  --path scripts/foo.py     → exit 0  (allow)
  just policy-check --op write  --path justfile           → exit 20 (approval)
  just policy-check --op delete --path .git/config        → exit 30 (blocked)

Exit codes: 0 allow · 20 approval_required · 30 deny · 40 policy missing.

Every verdict is appended to `.ai/decision-log/` (PH6-T13) — a preflight check is an
agent deciding whether it may act, which is exactly what `just audit` needs to count.
Pass `--no-log` when calling it to *explore* the policy rather than to gate an action.
"""

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policy_engine as pe  # noqa: E402


def _ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:
        pass
    return Path(".").resolve()


_DECISION_BY_EXIT = {
    pe.EXIT_ALLOW: "allow",
    pe.EXIT_APPROVAL: "ask",
    pe.EXIT_DENY: "deny",
    pe.EXIT_POLICY_MISSING: "policy_missing",
}


def _log(root: Path, result: dict, args) -> None:
    """Record the preflight verdict. Best effort — never change the exit code."""
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import decision_log as dl
        dl.record("policy", _DECISION_BY_EXIT.get(result["exit_code"], "unknown"),
                  "policy_check", root=root, op=args.op,
                  path=args.path or args.scope or "", rule=result.get("matched_rule"),
                  pattern=result.get("matched_pattern"),
                  blast_radius=result.get("blast_radius"),
                  reason=result.get("reason") if result["exit_code"] != pe.EXIT_ALLOW else "")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="God Mode Preflight Policy Checker")
    parser.add_argument("--op", required=True, help="Operation (read, write, create, delete)")
    parser.add_argument("--path", help="Target path for filesystem operations")
    parser.add_argument("--scope", help="Scope for external actions")
    parser.add_argument("--workspace", help="Workspace name")
    parser.add_argument("--no-log", action="store_true",
                        help="don't append this verdict to .ai/decision-log/")
    args = parser.parse_args()

    root = _ws_root()
    policy_path = root / ".ai" / "policy.yaml"
    if not policy_path.exists():
        result = {
            "allowed": False, "decision": "deny", "matched_rule": "none",
            "path": args.path, "operation": args.op, "blast_radius": "Unknown",
            "reason": "Policy file .ai/policy.yaml not found.",
            "requires_approval": False, "always_blocked": True,
            "exit_code": pe.EXIT_POLICY_MISSING,
        }
        print(json.dumps(result, indent=2))
        return pe.EXIT_POLICY_MISSING

    policy = pe.load_policy(policy_path)
    policy = pe.merge_role_overlay(policy, root)  # PH8-T04: additive only.

    if args.path:
        result = pe.evaluate_path(policy, args.op, args.path)
    else:
        # External-action scopes (deploy/publish/tt/telegram) still require approval.
        result = pe._verdict(
            "approval_required", "external_actions.default", None,
            "Destructive/External",
            "External action — requires explicit user approval.",
            args.op, args.scope or "", pe.EXIT_APPROVAL,
            requires_approval=True, always_blocked=False)

    if not args.no_log:
        _log(root, result, args)
    print(json.dumps(result, indent=2))
    return result["exit_code"]


if __name__ == "__main__":
    sys.exit(main())
