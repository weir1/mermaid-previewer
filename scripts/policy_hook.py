#!/usr/bin/env python3
"""
policy_hook.py — PreToolUse guard: the adapter that finally *runs* the policy engine.

Audit finding F-03: `policy_engine.py` has existed since v3.5 but no `PreToolUse` hook
exists in any of the 38 workspaces, so it is reachable only via `just policy-check`,
which nothing calls. Blast radius has been self-narrated prose. This script is the
missing translation layer between Claude Code's hook contract and the policy engine.

  stdin  ← Claude Code PreToolUse payload (tool_name + tool_input)
  stdout → hook JSON when the verdict is "ask"
  stderr → the blocking reason when the verdict is "deny"
  exit   → 0 fall through / 2 block

## Why not emit `permissionDecision: "allow"`
An explicit "allow" *bypasses* the user's own permission settings. This guard exists to
ADD restrictions, never to remove the ones the user configured. So an autonomous verdict
exits 0 with no decision and the normal permission flow continues untouched. The hook can
only ever make Claude Code more careful, never less.

## Exit-code translation (policy_engine → Claude Code)
    EXIT_ALLOW    0  → exit 0, no output          (normal permission flow)
    EXIT_APPROVAL 20 → permissionDecision "ask"   (no exit-code equivalent exists)
    EXIT_DENY     30 → exit 2 + stderr            (documented blocking path)
    EXIT_POLICY_MISSING 40 → exit 0 + stderr note (never brick a session on a config gap)

## Bash coverage is best-effort, by construction
A shell command can obfuscate any path (`$VAR`, `eval`, base64). This extracts targets
from common mutating forms and checks those. It is defence in depth on top of the
permission system, NOT a sandbox — do not treat an exit 0 here as proof a command is safe.
"""

from __future__ import annotations

import json
import os
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import policy_engine as pe  # noqa: E402

# Tools whose input names a file we can resolve to a single path.
PATH_KEYS = {
    "Write": "file_path",
    "Edit": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}

# Directories a write may target without tripping the anti-drift check (AGENTS.md:
# "Before writing ANY file, confirm the target path starts with the workspace root").
TEMP_PREFIXES = ("/tmp/", "/private/tmp/", "/var/folders/", "/private/var/folders/")

# The two paths a cross-workspace finding is allowed to land on (PH16-T22 rule A).
# Everything else inside another governed workspace is denied — see _foreign().
CHANNEL_PATHS = (".ai/inbox/", ".ai/memory-bank/knownIssues.md")

# Shell constructs that mutate the filesystem. Anything matched here has its argument
# paths evaluated against the policy.
MUTATING_CMDS = {
    "rm", "rmdir", "mv", "cp", "dd", "truncate", "shred", "chmod", "chown",
    "ln", "install", "tee", "unlink",
}
# `sed -i` / `git` are conditional — handled explicitly below.
REDIRECT_RE = re.compile(r"(?:^|\s)\d?>>?\s*([^\s;|&]+)")
# Shell separators — argument scanning must not run past one (see _bash_targets).
SEGMENT_RE = re.compile(r"(?:&&|\|\||[;|&\n])")


def _workspace_root(payload: dict) -> Path:
    """Resolve the workspace root WITHOUT shelling out — this runs on every tool call.

    Order matches the AGENTS.md anti-drift rule: the harness-provided project dir wins,
    the payload cwd is the fallback, and neither is allowed to be overridden by whatever
    file happens to be open.
    """
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root:
        return Path(env_root).resolve()
    cwd = payload.get("cwd")
    if cwd:
        return Path(cwd).resolve()
    return Path.cwd().resolve()


def _relativize(target: str, root: Path):
    """Return (relative_path_or_None, is_outside_workspace).

    `None` with is_outside=True means the path is outside this workspace and therefore
    not governed by this workspace's policy.yaml.
    """
    p = Path(target).expanduser()
    if not p.is_absolute():
        # Relative paths are interpreted against the workspace root, matching how
        # every other script in this OS resolves them. Normalisation is delegated to
        # the engine: a hand-rolled `.lstrip("./")` here strips *characters*, not a
        # prefix, so ".git/config" became "git/config" and every dotfile rule
        # (.git, .ai, .env, .github, .claude) silently stopped matching.
        return pe._normalize(Path(target).as_posix()), False
    try:
        return p.resolve().relative_to(root).as_posix(), False
    except ValueError:
        return None, True


def is_governed_workspace(root: Path) -> bool:
    """The OS's own definition, and the ONLY copy of it (`finding.py` imports this).

    From the global protocol: a workspace is God Mode governed when it has **both**
    `.ai/memory-bank/` **and** a `justfile` with a `session-start` target. Both
    halves earn their place — `$HOME` on this machine carries a `.ai/memory-bank/`
    and a `justfile` left over from a May 2026 experiment, and is in none of the 46
    routes. A `.ai/`-only test would classify the home directory as a workspace and
    therefore deny every write under `~` (`~/.claude/settings.json`,
    `~/.gemini/GEMINI.md` — files this OS legitimately manages). Found by driving
    this against the real filesystem before it shipped.
    """
    try:
        if not (root / ".ai" / "memory-bank").is_dir():
            return False
        justfile = root / "justfile"
        if not justfile.is_file():
            return False
        return "session-start" in justfile.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False


def _governed_root(target: Path):
    """The governed workspace containing `target`, or None.

    Walks up because the write is usually several directories deep and the file
    itself typically does not exist yet. The walk stops at `$HOME` — a home
    directory is not a project, and treating it as one is how the check above
    would have failed.
    """
    try:
        home = Path.home().resolve()
    except Exception:  # noqa: BLE001
        home = None
    for parent in [target, *target.parents]:
        if home is not None and parent == home:
            return None
        if is_governed_workspace(parent):
            return parent
    return None


def _foreign(raw: str, op: str, root: Path, base: dict) -> dict | None:
    """Verdict for a path outside this workspace — or None to fall through.

    PH16-T22 rule A. A write into ANOTHER GOVERNED WORKSPACE is denied, because
    that is the incident this rule was filed for: on 2026-08-13 the `@life`
    session found a real kernel defect and began writing the fix here, which
    bound `evidence.json` to no task, held the gate closed for both sessions,
    and voided a review hash that could no longer be satisfied. An `ask` is one
    confirmation away from all of that, and confirmations are what an operator
    learns to click through.

    Two things deliberately DO NOT change, because a guard that over-refuses gets
    switched off and then guards nothing:

      * a path outside any governed workspace keeps the plain anti-drift `ask`;
      * the finding channel itself (`.ai/inbox/**`, `knownIssues.md`) is never
        denied — the rule must not block the mechanism it exists to route work
        into. It stays at `ask` rather than becoming `allow`: this hook may only
        ever ADD restrictions, and an explicit allow would bypass the operator's
        own permission settings.

    Not covered, and stated rather than discovered: writes performed *inside* a
    shell script this guard invokes are invisible to it. That is deliberate —
    `god-upgrade` and `fleet-upgrade` write into 46 workspaces by design — so
    this rule constrains the agent's own edits, which is where the incident was.
    """
    resolved = Path(raw).expanduser().resolve()
    peer = _governed_root(resolved)
    if peer and peer != root:
        rel = resolved.relative_to(peer).as_posix()
        if not any(rel == c or rel.startswith(c) for c in CHANNEL_PATHS):
            return {**base, "decision": "deny", "op": op, "path": str(resolved),
                    "rule": "cross-workspace.finding-channel",
                    "blast_radius": "Another workspace",
                    "reason": (
                        f"BLOCKED — {rel} is inside ANOTHER God Mode workspace "
                        f"({peer}). PH16-T22 rule A: a finding travels as a note, "
                        f"not an edit. Work written into a workspace whose ledger "
                        f"you are not in binds its evidence to no task, closes its "
                        f"gate for whoever is working there, and voids its review "
                        f"hash. Send it instead:\n"
                        f"    just finding \"@{peer.name.lower()}\" \"<title>\" "
                        f"\"<what you observed>\"\n"
                        f"then let that workspace pick it up in its own budget.")}

    if str(resolved).startswith(TEMP_PREFIXES):
        return None

    # AGENTS.md anti-drift rule, made mechanical: writing into another
    # workspace is the exact failure this OS has already hit in production.
    return {**base, "decision": "ask", "op": op, "path": raw,
            "rule": "anti-drift.workspace-root", "blast_radius": "Outside workspace",
            "reason": f"{raw} is OUTSIDE the workspace root ({root}). "
                      "AGENTS.md anti-drift rule: confirm the target before writing."}


def _op_for_write(rel_path: str, root: Path) -> str:
    """`create` when the file does not exist yet, else `write` — so policy entries that
    list only one of the two still match honestly."""
    return "write" if (root / rel_path).exists() else "create"


def _bash_targets(command: str) -> list[tuple[str, str]]:
    """Extract (operation, path) pairs from a shell command. Conservative and
    best-effort — see the module docstring.

    Arguments are scanned per SEGMENT (split on `;`, `&&`, `||`, `|`). Scanning the
    whole token stream instead would drag the next command's words in as if they were
    paths — `rm foo && echo done` would evaluate "echo" and "done", each falling through
    to `default_unknown` → a spurious approval prompt. Prompt fatigue is the specific
    failure mode PH6-T11 exists to prevent, so precision here is the point.
    """
    targets: list[tuple[str, str]] = []

    for match in REDIRECT_RE.finditer(command):
        targets.append(("write", match.group(1)))

    for segment in SEGMENT_RE.split(command):
        try:
            tokens = shlex.split(segment)
        except ValueError:
            # Unbalanced quotes — we cannot reason about it; redirects above still stand.
            continue
        if not tokens:
            continue

        base = Path(tokens[0]).name
        rest = tokens[1:]
        args = [a for a in rest if not a.startswith("-")]

        if base in MUTATING_CMDS:
            if base in ("chmod", "chown", "install"):
                args = args[1:]          # first positional is the mode/owner, not a path
            op = "delete" if base in ("rm", "rmdir", "shred", "unlink") else "write"
            targets.extend((op, a) for a in args)
        elif base == "sed" and any(a.startswith("-i") for a in rest):
            targets.extend(("write", a) for a in args)
        elif base == "git" and args[:1] and args[0] in ("checkout", "reset", "clean"):
            # These rewrite the working tree; treat the index as the blast target.
            targets.append(("write", ".git/index"))

    return targets


def evaluate(payload: dict, root: Path, policy: dict) -> dict:
    """Reach a verdict and describe *why* — the shape the decision log records.

    Returns {decision, reason, tool, op, path, rule, pattern, blast_radius}, where the
    non-decision fields describe the target that DROVE the verdict (the denied path, or
    the first path needing approval), not merely the last one scanned.

    Pure function over the payload so the test-suite can drive it without a subprocess.
    """
    tool = payload.get("tool_name", "")
    tool_input = payload.get("tool_input") or {}
    base = {"decision": "allow", "reason": "", "tool": tool, "op": "", "path": "",
            "rule": "", "pattern": "", "blast_radius": ""}

    if tool in PATH_KEYS:
        raw = tool_input.get(PATH_KEYS[tool])
        if not raw:
            return base
        candidates = [("write", raw)]
    elif tool == "Bash":
        command = tool_input.get("command", "")
        candidates = _bash_targets(command)
        if not candidates:
            return base
    else:
        return base

    worst = dict(base)
    # The first cleanly-allowed target, kept so an `allow` verdict still names WHAT was
    # allowed. Without it the decision log fills with contentless "allow" lines: countable
    # for an autonomy rate, useless for asking "what did the agent touch unprompted?".
    first_allowed: dict | None = None

    for op, raw in candidates:
        rel, outside = _relativize(raw, root)

        if outside:
            verdict = _foreign(raw, op, root, base)
            if verdict is None:              # temp scratch — governed by nothing
                continue
            if verdict["decision"] == "deny":
                return verdict
            if worst["decision"] == "allow":
                worst = verdict
            continue

        if tool in PATH_KEYS and op == "write":
            op = _op_for_write(rel, root)

        verdict = pe.evaluate_path(policy, op, rel)
        if verdict["exit_code"] == pe.EXIT_DENY:
            return {**base, "decision": "deny", "op": op, "path": rel,
                    "rule": verdict["matched_rule"], "pattern": verdict["matched_pattern"],
                    "blast_radius": verdict["blast_radius"],
                    "reason": (f"BLOCKED by .ai/policy.yaml — {rel} ({op}). "
                               f"{verdict['reason']} "
                               f"[rule: {verdict['matched_rule']} · pattern: "
                               f"{verdict['matched_pattern']}]")}
        if verdict["exit_code"] == pe.EXIT_APPROVAL and worst["decision"] == "allow":
            worst = {**base, "decision": "ask", "op": op, "path": rel,
                     "rule": verdict["matched_rule"], "pattern": verdict["matched_pattern"],
                     "blast_radius": verdict["blast_radius"],
                     "reason": (f"{rel} ({op}) → {verdict['blast_radius']}. "
                                f"{verdict['reason']} "
                                f"[rule: {verdict['matched_rule']}]")}
        elif verdict["exit_code"] == pe.EXIT_ALLOW and first_allowed is None:
            first_allowed = {"op": op, "path": rel, "rule": verdict["matched_rule"],
                             "pattern": verdict["matched_pattern"],
                             "blast_radius": verdict["blast_radius"]}

    if worst["decision"] == "allow" and first_allowed:
        worst.update(first_allowed)
    return worst


def decide(payload: dict, root: Path, policy: dict):
    """Return (decision, reason). Decision ∈ {allow, ask, deny}. Thin view of evaluate()."""
    v = evaluate(payload, root, policy)
    return v["decision"], v["reason"]


def _log(root: Path, verdict: dict) -> None:
    """Append the verdict to `.ai/decision-log/` — best effort, never fatal.

    Imported lazily and defensively: a workspace that received `policy_hook.py` from an
    older kernel drop may not have `decision_log.py` yet, and a missing logger must
    degrade to "no analytics", never to "no guard".
    """
    try:
        import decision_log as dl
        dl.record("policy", verdict["decision"], "policy_hook", root=root,
                  tool=verdict.get("tool"), op=verdict.get("op"), path=verdict.get("path"),
                  rule=verdict.get("rule"), pattern=verdict.get("pattern"),
                  blast_radius=verdict.get("blast_radius"),
                  # An `allow` has no interesting reason; keep those lines short.
                  reason=verdict.get("reason") if verdict["decision"] != "allow" else "")
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # A malformed payload must not block the session.
        return 0

    root = _workspace_root(payload)
    policy_path = root / ".ai" / "policy.yaml"
    if not policy_path.exists():
        print(f"[policy_hook] no .ai/policy.yaml at {root} — guard inactive",
              file=sys.stderr)
        return 0

    try:
        policy = pe.load_policy(policy_path)
        policy = pe.merge_role_overlay(policy, root)  # PH8-T04: additive only.
        v = evaluate(payload, root, policy)
    except Exception as exc:                                  # noqa: BLE001
        # Fail open with a loud note: a crashing guard must never brick the session,
        # but it must not fail silently either.
        print(f"[policy_hook] guard error ({exc}) — falling through", file=sys.stderr)
        return 0

    # PH6-T13 / F-16: every verdict is recorded, including `allow` — the allows are the
    # denominator of the autonomy rate, so dropping them would leave `just audit` able to
    # count complaints but never the work that went through untouched. `record` never
    # raises and never blocks (decision_log design rule 1).
    decision = v["decision"]
    _log(root, v)

    if decision == "deny":
        print(v["reason"], file=sys.stderr)
        return 2

    if decision == "ask":
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "ask",
                "permissionDecisionReason": v["reason"],
            }
        }))
        return 0

    return 0


if __name__ == "__main__":
    sys.exit(main())
