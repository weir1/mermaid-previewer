#!/usr/bin/env python3
"""
model_registry.py — the model registry (PH10-T01, Goal G8).

Origin, the operator's own words (`.ai/docs/tasks.md`, PHASE 10): *"I can use
[Opus] only temporary for a few hours daily but for the rest of the days I have
gemini 3.1 pro, gemini flash & even claude sonnet ... I can't trust 'em because
they create something else."* This module turns that constraint into something
the OS governs: every model declares a TIER (`planner` / `executor` /
`reviewer`) in `.ai/models.yaml`, and `session_start.py` / `session_hook.py`
render which tier is running and what it may do — before PH10-T02's contract
and PH10-T03's leash exist to enforce it.

## The running model cannot be auto-detected here — checked live, not assumed

No environment variable a Claude Code subprocess can read names the model
(`claude-sonnet-5` etc. are known to the model's own context, never to `env`),
and `.claude/settings.json` carries no pinned model field. `claude --version`
reports the CLI build (e.g. `2.1.226`), not the model behind the session. So
the running model is **self-reported**: `just models set <name>` (or an
external wrapper exporting `CONTEXT_MODEL`) declares it, and every render of
that fact is labelled `self_reported: true` — never presented as verified.
PH10-T05 is where corroboration against the environment gets built; this
module's job is to be honest about not having it yet (design law 2).

## Unknown is UNKNOWN, never guessed into a tier

A model absent from the registry, or no declaration at all, resolves to
`.ai/models.yaml`'s `unknown_tier` (`executor` today) — the most restrictive of
the three, because a typo'd or spoofed name inheriting `planner`/`reviewer`
trust is the failure direction that matters. `resolve_running()` always states
whether the name was *found* in the registry or *unknown*; nothing here ever
silently upgrades an unrecognised name.

## Where the declaration lives

`.ai/session-state.json` — already gitignored, already per-session/per-machine
(`session_budget.py`'s own state file), reused here via its own
`load_raw()`/`save()` rather than a second file for one fact.

Usage:
  model_registry.py                 # list the registry (default)
  model_registry.py set <name>      # self-report the running model this session
  model_registry.py resolve         # the resolved running model + tier + actions
  model_registry.py --json          # machine-readable, with any subcommand
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

MODELS_YAML = "models.yaml"
UNKNOWN_NAME = "unknown"


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


def registry_path(root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / MODELS_YAML


def _load_yaml(path: Path) -> dict:
    """Prefer real PyYAML; fall back to the bundled reader (PH10-T01's own
    "one rule, one implementation" choice — `policy_engine.mini_yaml_load` is
    already proven against this exact block-YAML shape, so this does not grow
    a second parser)."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        import policy_engine
        data = policy_engine.mini_yaml_load(text)
    return data or {}


def load_registry(root: Path | None = None) -> dict:
    """The registry, or an empty-but-valid shape if the file is missing/broken.

    A missing/unreadable registry must never crash session start — it renders
    as "no registry", not a stack trace at the top of every session.
    """
    path = registry_path(root)
    empty = {"models": {}, "tiers": {}, "unknown_tier": "executor", "path": str(path),
             "exists": False, "error": ""}
    if not path.is_file():
        empty["error"] = f"no registry at {path}"
        return empty
    try:
        data = _load_yaml(path)
    except Exception as exc:  # noqa: BLE001
        empty["error"] = f"registry unreadable: {exc}"
        return empty
    return {
        "models": data.get("models") or {},
        "tiers": data.get("tiers") or {},
        "unknown_tier": data.get("unknown_tier") or "executor",
        "path": str(path),
        "exists": True,
        "error": "",
    }


def tier_actions(registry: dict, tier: str) -> dict:
    """{"permits": [...], "denies": [...]} for `tier`, or empty lists if the
    registry names no such tier — never invented."""
    spec = (registry.get("tiers") or {}).get(tier) or {}
    permits = spec.get("permits") or []
    denies = spec.get("denies") or []
    return {"permits": list(permits), "denies": list(denies)}


# ─────────────────────────── self-reported declaration ──────────────────────

def _state_module():
    import session_budget
    return session_budget


#: What a session declares it is here to DO (PH10-T09). A closed set, checked on
#: the way in, for the same reason `unknown_tier` exists: a mode nobody
#: recognises must never resolve to the permissive reading. `None` — undeclared —
#: is the honest fourth answer and is what every session says until it is asked.
MODES = ("plan", "execute")


def declare(name: str, root: Path | None = None, mode: str | None = None) -> dict:
    """Self-report the running model for this session. Written into the same
    per-session, gitignored state file `session_budget.py` already owns —
    Rejected in the plan: a second state file for one fact.

    `mode` is the operator's rule (PH10-T09): a session says whether it is here
    to plan or to execute, because those carry different permissions and, in his
    words, *"reveiwing the task & approving is not equal to 1 slot"*. An
    unrecognised mode RAISES rather than being stored — storing it would leave a
    reader to decide what an unknown mode means, and every such decision in this
    module is required to take the restrictive side.
    """
    if mode is not None and mode not in MODES:
        raise ValueError(
            f"mode must be one of {MODES} or None, got {mode!r} — a mode nobody "
            f"recognises is not stored, because a later reader would have to guess "
            f"what it permits."
        )
    sb = _state_module()
    state = sb.load_raw() or sb.new_state(root)
    entry = {
        "name": name.strip(),
        "declared_at": datetime.now(timezone.utc).isoformat(),
        "self_reported": True,
    }
    if mode is not None:
        entry["mode"] = mode
    state["model"] = entry
    sb.save(state)
    return dict(state["model"])


def resolve_tier_actions(tier: str, root: Path | None = None) -> dict:
    """`{"permits": [...], "denies": [...]}` for `tier` in this workspace's registry.

    A named convenience over `load_registry` + `tier_actions` so a caller asking
    "what may a planner do?" never re-implements the lookup — the drift this
    module exists to prevent.
    """
    return tier_actions(load_registry(root), tier)


def declared_model(root: Path | None = None) -> dict | None:
    """The self-reported declaration for this session, or None if none exists."""
    import os
    env_name = os.environ.get("CONTEXT_MODEL", "").strip()
    if env_name:
        return {"name": env_name, "declared_at": "", "self_reported": True,
                "source": "CONTEXT_MODEL env"}
    sb = _state_module()
    state = sb.load_raw()
    if not state or not isinstance(state.get("model"), dict):
        return None
    d = dict(state["model"])
    d.setdefault("source", "just models set")
    return d


def resolve_running(root: Path | None = None) -> dict:
    """The full picture: declared name (if any), whether it is known to the
    registry, its resolved tier, and that tier's permitted/denied actions.

    Design law 2, enforced structurally rather than by convention: an unknown
    name can ONLY resolve to `registry["unknown_tier"]` — there is no code path
    that looks a name up and falls through to a more permissive default.
    """
    registry = load_registry(root)
    declared = declared_model(root)
    result = {
        "declared": declared,
        "name": (declared or {}).get("name") or UNKNOWN_NAME,
        "known": False,
        "tier": "",
        "self_reported": bool((declared or {}).get("self_reported", True)),
        "actions": {"permits": [], "denies": []},
        "registry_error": registry.get("error", ""),
        # None means UNDECLARED, never "probably executing". The renderer says so
        # out loud; nothing downstream may read the absence as a mode.
        "mode": (declared or {}).get("mode") or None,
    }
    models = registry.get("models") or {}
    name = result["name"]
    if declared and name in models:
        entry = models.get(name) or {}
        result["known"] = True
        result["tier"] = entry.get("tier") or registry.get("unknown_tier", "executor")
    else:
        # Not declared, or declared but not in the registry — UNKNOWN either way.
        result["known"] = False
        result["tier"] = registry.get("unknown_tier", "executor")
    result["actions"] = tier_actions(registry, result["tier"])
    return result


# ────────────────────────────────── rendering ────────────────────────────────

def render_list(registry: dict) -> str:
    """`just models` — the registry table. AI + human channel share this;
    it is short by construction (one line per model)."""
    if registry.get("error"):
        return f"🤖 MODEL REGISTRY — {registry['error']}"
    models = registry.get("models") or {}
    if not models:
        return "🤖 MODEL REGISTRY — empty (.ai/models.yaml has no `models:` entries)"
    lines = [f"🤖 MODEL REGISTRY — {len(models)} model(s), unknown → tier "
             f"'{registry.get('unknown_tier', 'executor')}'"]
    for name, entry in sorted(models.items()):
        entry = entry or {}
        tier = entry.get("tier", "?")
        cost_in = entry.get("cost_in_per_mtok", "?")
        cost_out = entry.get("cost_out_per_mtok", "?")
        notes = entry.get("notes", "")
        lines.append(f"   • {name} — tier: {tier} · ${cost_in}/${cost_out} per Mtok in/out"
                     + (f" — {notes}" if notes else ""))
    return "\n".join(lines)


def render_running(resolved: dict) -> str:
    """One block: the declared model, whether it's known, its tier, what that
    tier may/may not do — for the AI channel (`session_start.py`)."""
    name = resolved["name"]
    tag = "self-reported, not verified" if resolved.get("self_reported") else "declared"
    known = "known" if resolved["known"] else "UNKNOWN — not guessed"
    permits = ", ".join(resolved["actions"]["permits"]) or "(none)"
    denies = ", ".join(resolved["actions"]["denies"]) or "(none)"
    lines = [
        "🤖 RUNNING MODEL",
        f"   Model: {name} ({tag}) — {known}",
        f"   Tier: {resolved['tier']}",
        f"   Permitted: {permits}",
        f"   Denied: {denies}",
    ]
    if resolved.get("registry_error"):
        lines.append(f"   ⚠️  {resolved['registry_error']}")
    if resolved["name"] == UNKNOWN_NAME:
        lines.append("   ➤ No model declared this session — run `just models set <name>`.")
    return "\n".join(lines)


def plain_lines(resolved: dict) -> list:
    """The one-line human-channel rendering (PH9-T17 two-channel shape) —
    joined into `session_hook.py`'s `human_message()` alongside health.py."""
    if resolved["name"] == UNKNOWN_NAME:
        return ["🤖 Running model: not declared this session (`just models set <name>`)."]
    tag = "self-reported" if resolved.get("self_reported") else "declared"
    known = "" if resolved["known"] else ", unrecognised — most restrictive tier applied"
    return [f"🤖 Running model: {resolved['name']} ({tag}) — tier {resolved['tier']}{known}."]


# ────────────────────────────────────── CLI ──────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="The model registry (PH10-T01).")
    ap.add_argument("action", nargs="?", default="list", choices=("list", "set", "resolve"))
    ap.add_argument("name", nargs="?", help="model name, for `set`")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = ws_root()

    if args.action == "set":
        if not args.name:
            print("🛑 `just models set <name>` needs a model name.")
            return 1
        d = declare(args.name, root)
        if args.json:
            print(json.dumps(d, indent=2))
        else:
            print(f"✅ Declared running model: {d['name']} (self-reported, this session only).")
        return 0

    if args.action == "resolve":
        r = resolve_running(root)
        if args.json:
            print(json.dumps(r, indent=2))
        else:
            print(render_running(r))
        return 0

    registry = load_registry(root)
    if args.json:
        print(json.dumps(registry, indent=2))
    else:
        print(render_list(registry))
    return 0


if __name__ == "__main__":
    sys.exit(main())
