#!/usr/bin/env python3
"""
workspace_declare.py — the mesh declaration itself (PH11-T01 Slice 1, sub-part a, Goal G9).

Origin, the operator's own reframe (`.ai/plan.md` G9, `.ai/docs/roadmap.md` v4.1.0): "zenith
core decision making depend upon jobscraper & jobscraper to work depend upon 5g proxy & overall
goal we are trying to achieve via job workspace, website portfolio will give me leads & proof in
upwork & zenith engineering we will create portfolio demo, which use by job giver & i get job."
No workspace's standing is judged against that chain today — each is evaluated in isolation.
`.ai/workspace.yaml` is the first structured fact: what a workspace is, what it depends on, what
it serves, and which `.ai/north-star.yaml` (PH11-T02) stage its work advances.

## A missing file is "undeclared," never a guessed role

Same contract `north_star.score()` already holds for a stage with no `exit_criterion`: refuse to
invent, report the absence explicitly. `load_workspace()` on a missing file returns
`role: "undeclared"` — never something guessed from the directory name, `plan.md`'s prose, or
anything else. A *malformed* file (present but fails the schema) is a different, real defect and
is treated as one — `validate_os.py` fails the gate on it, the same way a malformed `policy.yaml`
already does.

## Dependency-light validation, matching every other schema check in this repo

No `jsonschema` package (confirmed unavailable in this environment, same as everywhere else this
repo checks JSON Schema — see `validate_os._validate_policy_structure`). `validate_schema()` below
is a hand-written structural check against `workspace.schema.json`'s actual shape, not a generic
JSON-Schema engine — narrower, but it is what every other checker in this repo already is.

## Not this slice

Edge reciprocity / cycle detection (`just fleet-map`), ranking against the active ladder stage
(`just fleet-triage`), and the composed daily briefing (`just morning-audit`) are sub-parts
(b)/(c)/(d) of the merged PH11-T01 task — later slices. This module only declares and validates
one workspace's own file.

Usage:
  workspace_declare.py                # render this workspace's declaration
  workspace_declare.py --json
  workspace_declare.py --check        # validate; exit 1 on a malformed file (missing is not an error)
  workspace_declare.py --scaffold     # write a template; refuses to overwrite an existing file
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

WORKSPACE_YAML = "workspace.yaml"
WORKSPACE_SCHEMA = "workspace.schema.json"
UNDECLARED = "undeclared"


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


def workspace_path(root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / WORKSPACE_YAML


def schema_path(root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / "schemas" / WORKSPACE_SCHEMA


def _load_yaml(path: Path) -> dict:
    """Prefer real PyYAML; fall back to the bundled reader — the same two-tier
    read `north_star._load_yaml` / `model_registry._load_yaml` already use,
    not a third implementation of "try yaml, fall back to mini_yaml_load"."""
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        import policy_engine
        data = policy_engine.mini_yaml_load(text)
    return data or {}


# ────────────────────────────────── loading ───────────────────────────────────

def load_workspace(root: Path | None = None) -> dict:
    """This workspace's declaration, or an honestly-undeclared shape if the
    file is missing/unreadable. Never crashes a caller and never guesses a
    role — the same contract `north_star.load_ladder()` holds for a missing
    ladder."""
    path = workspace_path(root)
    empty = {
        "role": UNDECLARED, "success_criteria": "", "depends_on": [],
        "serves": [], "ladder_stage": None, "notes": "",
        "path": str(path), "exists": False, "error": "",
    }
    if not path.is_file():
        empty["error"] = f"no declaration at {path}"
        return empty
    try:
        data = _load_yaml(path)
    except Exception as exc:  # noqa: BLE001
        empty["error"] = f"declaration unreadable: {exc}"
        return empty

    role = data.get("role")
    stage = data.get("ladder_stage")
    try:
        stage = int(stage) if stage not in (None, "", "~", "null") else None
    except (TypeError, ValueError):
        stage = None

    return {
        "role": role if role else UNDECLARED,
        "success_criteria": data.get("success_criteria") or "",
        "depends_on": [str(x) for x in (data.get("depends_on") or [])],
        "serves": [str(x) for x in (data.get("serves") or [])],
        "ladder_stage": stage,
        "notes": data.get("notes") or "",
        "path": str(path),
        "exists": True,
        "error": "",
    }


# ─────────────────────────────── schema validation ────────────────────────────

def validate_schema(data: dict) -> list[str]:
    """Structural check against workspace.schema.json's actual shape
    (dependency-light — no jsonschema lib, matching every other schema check
    in this repo). Returns a list of violations; empty means valid."""
    errs: list[str] = []
    if not isinstance(data, dict):
        return ["workspace.yaml: top level must be a mapping"]
    role = data.get("role")
    if not isinstance(role, str) or not role.strip():
        errs.append("workspace.yaml: 'role' is required and must be a non-empty string")
    if "success_criteria" in data and data["success_criteria"] is not None \
            and not isinstance(data["success_criteria"], str):
        errs.append("workspace.yaml: 'success_criteria' must be a string")
    for key in ("depends_on", "serves"):
        if key in data and data[key] is not None:
            v = data[key]
            if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
                errs.append(f"workspace.yaml: '{key}' must be a list of strings")
    if "ladder_stage" in data and data["ladder_stage"] is not None:
        v = data["ladder_stage"]
        if isinstance(v, bool) or not isinstance(v, int):
            # mini_yaml_load has no numeric scalar type (everything is a str) —
            # a numeric-looking string is accepted, same normalisation
            # north_star.load_ladder() applies to `stage`/`active_stage`.
            if not (isinstance(v, str) and v.lstrip("-").isdigit()):
                errs.append("workspace.yaml: 'ladder_stage' must be an integer or null")
    if "notes" in data and data["notes"] is not None and not isinstance(data["notes"], str):
        errs.append("workspace.yaml: 'notes' must be a string")
    return errs


def check(root: Path | None = None) -> dict:
    """Pure read for the gate. A MISSING file is not a failure (undeclared is
    honest); a PRESENT, malformed file is — the same asymmetry `doctor.py`
    already applies to the codemap and usage-guide checks."""
    path = workspace_path(root)
    if not path.is_file():
        return {"ok": True, "exists": False, "errors": [], "path": str(path)}
    try:
        data = _load_yaml(path)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "exists": True, "errors": [f"workspace.yaml: {exc}"], "path": str(path)}
    errs = validate_schema(data)
    return {"ok": not errs, "exists": True, "errors": errs, "path": str(path)}


# ────────────────────────────────── scaffold ──────────────────────────────────

SCAFFOLD_TEMPLATE = """\
# .ai/workspace.yaml — the mesh declaration (PH11-T01). What this workspace IS,
# not what the AI guesses it is — fill this in yourself, the same "operator's own
# words" rule .ai/plan.md's "What I want" section holds.

role: ""              # required — what this workspace is, in your own words
success_criteria: ""  # optional — what "done" or "working" looks like here
depends_on: []         # optional — workspace names this one's outcome depends on
serves: []              # optional — workspace names this one's output feeds
ladder_stage:           # optional int — which .ai/north-star.yaml stage this advances
notes: ""              # optional — say why a field above is honestly empty, if it is
"""


def scaffold(root: Path | None = None) -> Path:
    """Write the empty template. Raises FileExistsError rather than clobber a
    real declaration — the same contract `plan_workspace.scaffold()` holds."""
    path = workspace_path(root)
    if path.exists():
        raise FileExistsError(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(SCAFFOLD_TEMPLATE, encoding="utf-8")
    return path


# ────────────────────────────────── rendering ─────────────────────────────────

def render(w: dict) -> str:
    if not w["exists"]:
        return (f"🧩 WORKSPACE DECLARATION — undeclared (no {w['path']} — "
                f"run `just workspace-declare` to scaffold one)")
    if w["error"]:
        return f"🧩 WORKSPACE DECLARATION — {w['error']}"
    lines = [f"🧩 WORKSPACE DECLARATION — role: {w['role']}"]
    if w["success_criteria"]:
        lines.append(f"   success: {w['success_criteria']}")
    lines.append(f"   depends_on: {w['depends_on'] or '(none)'}  ·  serves: {w['serves'] or '(none)'}")
    lines.append(f"   ladder_stage: {w['ladder_stage'] if w['ladder_stage'] is not None else 'undeclared'}")
    if w["notes"]:
        lines.append(f"   notes: {w['notes']}")
    return "\n".join(lines)


# ────────────────────────────────────── CLI ────────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="This workspace's mesh declaration (PH11-T01).")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--check", action="store_true", help="validate; exit 1 on a malformed file")
    ap.add_argument("--scaffold", action="store_true", help="write a template (never overwrites)")
    args = ap.parse_args()

    root = ws_root()

    if args.scaffold:
        try:
            path = scaffold(root)
        except FileExistsError as exc:
            print(f"🛑 already exists: {exc.args[0]} — edit it directly, not overwritten.")
            return 1
        print(f"✅ scaffolded {path} — fill in 'role' at minimum, then `just workspace-check`.")
        return 0

    if args.check:
        c = check(root)
        if c["ok"]:
            print(f"✅ workspace.yaml — {'valid' if c['exists'] else 'undeclared (no file, not an error)'}")
            return 0
        print("❌ workspace.yaml invalid:")
        for e in c["errors"]:
            print(f"   - {e}")
        return 1

    w = load_workspace(root)
    if args.json:
        print(json.dumps(w, indent=2))
    else:
        print(render(w))
    return 0


if __name__ == "__main__":
    sys.exit(main())
