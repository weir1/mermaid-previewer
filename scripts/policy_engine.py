#!/usr/bin/env python3
"""
policy_engine.py — the real blast-radius engine for the God Mode AI OS.

This is the library that turns `.ai/policy.yaml` from prose into executed code.
`policy_check.py` (the CLI / preflight guard) and the test-suite both import it.

Design constraints (ADR-013 — dependency-light):
  • Runs on system python3. PyYAML is used when present, otherwise a small,
    purpose-built block-YAML reader (`mini_yaml_load`) parses the subset that
    policy.yaml actually uses. Both paths are covered by tests/test_policy_engine.py.

Evaluation precedence (first match wins):
  always_blocked  →  approval_required  →  autonomous_allowed  →  DEFAULT.
The DEFAULT for a path that matches nothing is **approval_required** ("when in
doubt, ask") — a governance gate should fail safe, not fail open.
"""

import re
from pathlib import Path

# Exit codes (kept stable for the CLI + any PreToolUse guard consuming them)
EXIT_ALLOW = 0
EXIT_APPROVAL = 20
EXIT_DENY = 30
EXIT_POLICY_MISSING = 40

BLAST_ALLOW = "Safe Refactor"
BLAST_APPROVAL = "Destructive/Dependency"
BLAST_DENY = "Destructive"


# ─────────────────────────── minimal block-YAML reader ──────────────────────
def _strip_comment(line: str) -> str:
    """Remove a trailing/whole-line `#` comment while respecting quoted scalars."""
    out, quote = [], None
    for ch in line:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            out.append(ch)
        elif ch == "#":
            break
        else:
            out.append(ch)
    return "".join(out).rstrip()


def _scalar(tok: str):
    tok = tok.strip()
    if not tok:
        return None
    if (tok[0] == tok[-1]) and tok[0] in ('"', "'") and len(tok) >= 2:
        return tok[1:-1]
    if tok.lower() in ("true", "false"):
        return tok.lower() == "true"
    return tok


def _flow_list(tok: str) -> list:
    """Parse an inline flow sequence like ["read", "write"]."""
    inner = tok.strip()[1:-1].strip()
    if not inner:
        return []
    return [_scalar(p) for p in _split_flow(inner)]


def _split_flow(inner: str) -> list:
    parts, buf, quote = [], [], None
    for ch in inner:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = None
        elif ch in ('"', "'"):
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf))
    return [p.strip() for p in parts if p.strip()]


def _indent(raw: str) -> int:
    return len(raw) - len(raw.lstrip(" "))


# A bare `>` or `|` (optionally with a chomping `+`/`-` and/or an indentation
# digit, in either order — real YAML allows both) is a BLOCK SCALAR
# indicator, never a literal value. This reader does not implement block
# scalars (folded/literal multi-line text) — treating one as an ordinary
# scalar previously returned the literal string ">" for the key and left the
# real multi-line content as unconsumed, over-indented lines, which made the
# ENCLOSING mapping parse terminate early (a `col > indent` line breaks the
# `while` loop) and silently produced a wrong-but-valid, truncated result —
# e.g. an empty `tiers:` dict for the whole file, with every downstream
# permit/deny resolving to `[]` and nothing raised anywhere. Found live
# 2026-08-10 (knownIssues.md) shipping PH10-T06/T07. Raising here trades a
# silent wrong answer for a loud, immediate one — the same trade this
# workspace makes everywhere else (fail closed, not fail open quietly).
_BLOCK_SCALAR_RE = re.compile(r"^[|>][0-9+-]*$")


class MiniYamlUnsupportedError(ValueError):
    """mini_yaml_load hit real YAML syntax outside the documented subset
    (block-YAML: maps, sequences, scalars, inline flow lists, comments)."""


def mini_yaml_load(text: str):
    """Parse the block-YAML subset used by policy.yaml (maps, sequences, scalars,
    inline flow lists, comments). Returns nested dict/list — matching what
    PyYAML would produce for this file's shape."""
    lines = []
    for raw in text.splitlines():
        stripped = _strip_comment(raw)
        if stripped.strip() == "":
            continue
        lines.append((_indent(stripped), stripped.strip(), stripped))
    value, _ = _parse_block(lines, 0, 0)
    return value


def _parse_block(lines, i, indent):
    """Parse a mapping or sequence whose items sit at column `indent`.
    Returns (value, next_index)."""
    if i >= len(lines):
        return None, i
    is_seq = lines[i][1].startswith("- ") or lines[i][1] == "-"
    if is_seq:
        return _parse_sequence(lines, i, indent)
    return _parse_mapping(lines, i, indent)


def _parse_mapping(lines, i, indent):
    result = {}
    while i < len(lines):
        col, content, _raw = lines[i]
        if col < indent:
            break
        if col > indent or content.startswith("- "):
            break
        if ":" not in content:
            break
        key, _, rest = content.partition(":")
        key, rest = key.strip(), rest.strip()
        i += 1
        if _BLOCK_SCALAR_RE.match(rest):
            raise MiniYamlUnsupportedError(
                f"mini_yaml_load: {key!r}: {rest!r} is a YAML block-scalar "
                "indicator (multi-line folded/literal text), which this "
                "reader does not support — it would otherwise silently drop "
                "every key after it. Rewrite the value on one line, or make "
                "PyYAML available so the real parser is used instead.")
        if rest == "":
            # nested block belongs to this key (deeper indent) — else null
            if i < len(lines) and lines[i][0] > indent:
                child, i = _parse_block(lines, i, lines[i][0])
                result[key] = child
            else:
                result[key] = None
        elif rest.startswith("["):
            result[key] = _flow_list(rest)
        else:
            result[key] = _scalar(rest)
    return result, i


def _parse_sequence(lines, i, indent):
    items = []
    while i < len(lines):
        col, content, _raw = lines[i]
        if col != indent or not (content.startswith("- ") or content == "-"):
            break
        after = content[2:].strip() if content.startswith("- ") else ""
        if after == "":
            i += 1
            if i < len(lines) and lines[i][0] > indent:
                child, i = _parse_block(lines, i, lines[i][0])
                items.append(child)
            else:
                items.append(None)
        elif ":" in after and not after.startswith("["):
            # item is a mapping; its first key sits at column indent+2
            item_indent = indent + 2
            synthesized = [(item_indent, after, " " * item_indent + after)]
            merged = synthesized + lines[i + 1:]
            item, consumed = _parse_mapping(merged, 0, item_indent)
            # `consumed` counts into `merged`; translate back to `lines`
            i = i + 1 + (consumed - 1)
            items.append(item)
        elif after.startswith("["):
            items.append(_flow_list(after))
            i += 1
        else:
            items.append(_scalar(after))
            i += 1
    return items, i


# ─────────────────────────────── glob matching ──────────────────────────────
def glob_to_regex(pattern: str) -> str:
    i, n, out = 0, len(pattern), ["^"]
    while i < n:
        c = pattern[i]
        if c == "*":
            if i + 1 < n and pattern[i + 1] == "*":
                if i + 2 < n and pattern[i + 2] == "/":
                    out.append("(?:.*/)?")   # **/  → zero or more path segments
                    i += 3
                else:
                    out.append(".*")          # **   → anything
                    i += 2
            else:
                out.append("[^/]*")           # *    → within one segment
                i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return "".join(out)


def glob_match(pattern: str, path: str) -> bool:
    return re.match(glob_to_regex(pattern), path) is not None


# ─────────────────────────────── evaluation ─────────────────────────────────
def load_policy(policy_path) -> dict:
    """Load policy.yaml. Prefer PyYAML; fall back to the bundled reader so the
    engine works on a bare system python3."""
    text = Path(policy_path).read_text()
    try:
        import yaml
        data = yaml.safe_load(text)
    except ModuleNotFoundError:
        data = mini_yaml_load(text)
    return data if isinstance(data, dict) else {}


def merge_role_overlay(policy: dict, root=None, roles_root=None) -> dict:
    """Union a workspace's own policy with its role pack's blast-radius overlay
    (PH8-T04, Goal G13) — additive only, and scoped to `executive-coach` today.

    Every role's `.ai/roles/<id>/policy.yaml` has been read since PH8-T02, but
    nothing consumed it — this is that consumption, for the one pack with a
    real, tested schema. Every other resolved role (`software-engineer` the
    default, `researcher`/`content-writer` the drafts) returns `policy`
    byte-unchanged, so 45 of 46 workspaces never take this branch at all. The
    cheaper scope was agreed in `.ai/prework/PH8-T04.md`.

    A role pack's `policy.yaml` is a flat list of glob strings (`role.yaml`'s
    own "one-line values only" rule) — a simpler shape than the engine's own
    `.ai/policy.yaml`, which nests dict entries (each with a `paths` list)
    under `rules.filesystem.*` (`evaluate_path`'s real input). This function is
    the translation between the two, not a raw key union: the role's glob list
    becomes ONE appended entry per category, in the engine's own shape.

    The overlay can only ADD entries to `approval_required` / `always_blocked`
    — never remove one the workspace's own policy already had, and it never
    touches `autonomous_allowed`. A role pack adds caution to a workspace's
    policy; it must never subtract from it, so the one direction a bug in this
    function can fail toward is "asks more often", not "silently allows more".

    `roles_root` is a test seam, same shape as every `role_registry` function.
    """
    try:
        import role_registry as rr
    except Exception:  # noqa: BLE001 — a missing/broken role_registry must
        return policy  # degrade to "no overlay", never to a crash of the guard.
    try:
        resolved = rr.resolve(root, roles_root=roles_root)
    except Exception:  # noqa: BLE001
        return policy
    if not resolved.get("ok") or resolved.get("id") != "executive-coach":
        return policy
    overlay = (resolved.get("pack") or {}).get("policy") or {}
    approval_paths = [p for p in (overlay.get("approval_required") or []) if isinstance(p, str)]
    blocked_paths = [p for p in (overlay.get("always_blocked") or []) if isinstance(p, str)]
    if not approval_paths and not blocked_paths:
        return policy

    merged = dict(policy)
    rules = dict(merged.get("rules") or {})
    fs = dict(rules.get("filesystem") or {})
    if approval_paths:
        fs["approval_required"] = list(fs.get("approval_required") or []) + [{
            "paths": approval_paths,
            "reason": f"{resolved['id']} role policy (.ai/roles/{resolved['id']}/policy.yaml) "
                      "— domain-destructive record, no code-style re-run regenerates it.",
        }]
    if blocked_paths:
        fs["always_blocked"] = list(fs.get("always_blocked") or []) + [{
            "paths": blocked_paths,
            "reason": f"{resolved['id']} role policy (.ai/roles/{resolved['id']}/policy.yaml) "
                      "— always blocked for this role.",
        }]
    rules["filesystem"] = fs
    merged["rules"] = rules
    return merged


def _normalize(target: str) -> str:
    t = target.strip()
    if t.startswith("./"):
        t = t[2:]
    return t


def _entry_matches(entry: dict, operation: str, target: str):
    if not isinstance(entry, dict):
        return None
    ops = entry.get("operations") or []
    if ops and operation not in ops:
        return None
    for pat in entry.get("paths") or []:
        if isinstance(pat, str) and glob_match(pat, target):
            return pat
    return None


def _verdict(decision, matched_rule, matched_pattern, blast, reason,
             operation, target, exit_code, requires_approval, always_blocked):
    return {
        "allowed": decision == "allow",
        "decision": decision,
        "matched_rule": matched_rule,
        "matched_pattern": matched_pattern,
        "path": target,
        "operation": operation,
        "blast_radius": blast,
        "reason": reason,
        "requires_approval": requires_approval,
        "always_blocked": always_blocked,
        "exit_code": exit_code,
    }


def evaluate_path(policy: dict, operation: str, target: str) -> dict:
    """Return a blast-radius verdict for a filesystem operation, evaluated
    against the real policy.yaml globs in precedence order."""
    target = _normalize(target)
    fs = ((policy.get("rules") or {}).get("filesystem")) or {}

    for entry in fs.get("always_blocked") or []:
        pat = _entry_matches(entry, operation, target)
        if pat:
            return _verdict("deny", "filesystem.always_blocked", pat,
                            entry.get("blast_radius", BLAST_DENY),
                            entry.get("reason", "Blocked by policy — never permitted."),
                            operation, target, EXIT_DENY,
                            requires_approval=False, always_blocked=True)

    for entry in fs.get("approval_required") or []:
        pat = _entry_matches(entry, operation, target)
        if pat:
            return _verdict("approval_required", "filesystem.approval_required", pat,
                            entry.get("blast_radius", BLAST_APPROVAL),
                            entry.get("reason", "Change requires human approval."),
                            operation, target, EXIT_APPROVAL,
                            requires_approval=True, always_blocked=False)

    for entry in fs.get("autonomous_allowed") or []:
        pat = _entry_matches(entry, operation, target)
        if pat:
            return _verdict("allow", "filesystem.autonomous_allowed", pat,
                            entry.get("blast_radius", BLAST_ALLOW),
                            "Path + operation matched an autonomous rule.",
                            operation, target, EXIT_ALLOW,
                            requires_approval=False, always_blocked=False)

    # Reads are non-mutating: allow by default (an explicit rule above can still
    # block/gate a read by listing "read" in its operations, e.g. for secrets).
    if operation == "read":
        return _verdict("allow", "filesystem.default_read", None, BLAST_ALLOW,
                        "Read is non-mutating and matched no restricting rule.",
                        operation, target, EXIT_ALLOW,
                        requires_approval=False, always_blocked=False)

    # Fail-safe default for mutations: unknown path → require approval (never fail open).
    return _verdict("approval_required", "filesystem.default_unknown", None,
                    BLAST_APPROVAL,
                    "Path matched no rule; unknown blast radius — ask before acting.",
                    operation, target, EXIT_APPROVAL,
                    requires_approval=True, always_blocked=False)
