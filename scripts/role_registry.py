#!/usr/bin/env python3
"""
role_registry.py — one OS, many kinds of workspace (PH8-T02, Goal G13).

The kernel is deployed to 46 workspaces and, until this module, assumed every one
of them was a software project. That was not a design opinion — it was two literal
lists, in `doctor.py` and `session_start.py`, naming the same seven memory-bank
files and requiring them everywhere.

The cost was measurable on disk before this was written. `@fuel`'s REQUIRED
`techContext.md` is 69 lines whose own text reads *"Pure Markdown Life Operating
System. No application code. No runtime. No build process… nothing to compile,
test, or deploy"*. `@seduction`'s is an 11-line untouched `> TODO: Document the
tech stack` stub. Both workspaces are green. A required file that its own owner
has correctly identified as inapplicable is a check that teaches people to ignore
checks — and once someone learns to ignore one check, the gate stops working for
the reasons it was actually built.

## Packs are data; this module is the only code

A role pack is four declarative files in `.ai/roles/<id>/` (`role.yaml`,
`schema.yaml`, `policy.yaml`, `dod_template.md`). Adding a role is adding a
folder — no code change, no migration. That is deliberate: it is what makes
"ship one real role and two declared drafts" a sane scope rather than a half-built
feature, because promoting a draft later costs no engineering.

## The migration guarantee (why this is safe to ship to 46 workspaces)

A workspace that declares no `role_pack:` resolves to `software-engineer`, whose
schema is today's seven names in today's order. Not "approximately today's
behaviour" — `tests/test_role_packs.py` asserts the resolved list equals that
literal, so the day it stops matching is the day a test fails, rather than the day
46 workspaces quietly change. Nothing outside `@context` needs to be touched.

## `role_pack:`, not `role:`

`.ai/workspace.yaml` already has a `role:` key: PH11-T01's mesh declaration, which
holds a PROSE SENTENCE ("The God Mode AI OS kernel — builds and maintains the
governance tooling…"). Reusing it would make one key mean both a sentence and an
enum, and the first thing to break would be a parser guessing which. Two
similarly-named keys is the cheaper confusion, and it is stated here because
that is the kind of decision that looks like an accident in six months.

## Unknown is refused, never upgraded — and a draft is refused distinctly

An unrecognised role name is refused BY NAME with the available roles listed. It
never silently becomes the default: a typo'd role inheriting the default schema is
the failure direction that matters, exactly as `model_registry.resolve_running`
refuses to let an unknown model inherit a trusted tier. A pack marked
`status: draft` is refused too, but with a DIFFERENT message, because "you typed
this wrong" and "this exists but is not finished" are different problems and
collapsing them wastes the reader's time.

Drafts ship because the operator's bar for this feature was *"proven & battle
tested"*. A schema guessed for a workspace nobody has opened, shipped as usable,
would be this module committing the defect it was built to remove.

## Degrade, never crash

A missing `.ai/roles/`, an unreadable `workspace.yaml`, a directory that was never
onboarded — all resolve to the built-in default WITH an `error` string explaining
why. This runs at the top of every session; a stack trace there breaks every
workspace at once, which is a worse outcome than any wrong answer this module can
give. `model_registry.load_registry` established the rule and this follows it.

Usage:
  role_registry.py                          # list the packs (default)
  role_registry.py resolve                   # the active role for this workspace
  role_registry.py show <id>                 # one pack in full
  role_registry.py --json                    # machine-readable, with any subcommand
  role_registry.py --required-memory-files   # one .ai/memory-bank/<name>.md path per
                                              # line — evidence-pack.sh's shell entry
                                              # point (PH24-T19), same rule as
                                              # task_ledger.py --active for task_id.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_ROLE = "software-engineer"

#: The seven, in the order `doctor.py` held them before this module existed. This
#: is the LAST-RESORT fallback for when `.ai/roles/` is missing entirely — not a
#: second copy of the schema. The live source is
#: `.ai/roles/software-engineer/schema.yaml`; this exists so that a workspace whose
#: roles directory has not been deployed yet still boots with today's behaviour
#: instead of an empty required-list (which would report every workspace as
#: complete — a check that passes because it checks nothing).
BUILTIN_REQUIRED = ["projectbrief", "activeContext", "systemPatterns", "techContext",
                    "decisions", "progress", "knownIssues"]

STABLE, DRAFT = "stable", "draft"


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


def roles_dir(root: Path | None = None) -> Path:
    return (root or ws_root()) / ".ai" / "roles"


def _load_yaml(path: Path) -> dict:
    """Prefer real PyYAML; fall back to the bundled reader.

    Same choice `model_registry._load_yaml` made, for the same reason: this repo
    has one bundled YAML reader that is already proven against this block shape,
    and a second parser would be a second set of quirks to keep true.
    """
    text = path.read_text(encoding="utf-8")
    try:
        import yaml
        data = yaml.safe_load(text)
    except Exception:  # noqa: BLE001
        import policy_engine
        data = policy_engine.mini_yaml_load(text)
    return data or {}


# ────────────────────────────────── packs ────────────────────────────────────

def load_pack(role_id: str, roles_root: Path | None = None) -> dict | None:
    """One pack, fully read, or None if there is no such directory.

    Never raises on malformed content: a broken pack reports `status: draft` with
    an `error`, so it is refused as active rather than crashing the caller.
    """
    base = Path(roles_root) if roles_root else roles_dir()
    d = base / role_id
    if not d.is_dir():
        return None
    pack = {"id": role_id, "status": DRAFT, "title": role_id, "path": str(d),
            "error": "", "memory": {}, "policy": {}, "persona": "", "summary": "",
            "draft_reason": "", "default": False}
    try:
        meta = _load_yaml(d / "role.yaml") if (d / "role.yaml").is_file() else {}
    except Exception as exc:  # noqa: BLE001
        pack["error"] = f"role.yaml unreadable: {exc}"
        return pack
    pack["id"] = meta.get("id") or role_id
    pack["title"] = meta.get("title") or role_id
    pack["status"] = meta.get("status") or DRAFT
    pack["persona"] = meta.get("persona") or ""
    pack["summary"] = meta.get("summary") or ""
    pack["draft_reason"] = meta.get("draft_reason") or ""
    pack["default"] = bool(meta.get("default"))
    if (d / "schema.yaml").is_file():
        try:
            pack["memory"] = (_load_yaml(d / "schema.yaml").get("memory") or {})
        except Exception as exc:  # noqa: BLE001
            pack["error"] = f"schema.yaml unreadable: {exc}"
    if (d / "policy.yaml").is_file():
        try:
            pack["policy"] = _load_yaml(d / "policy.yaml")
        except Exception as exc:  # noqa: BLE001
            pack["error"] = f"policy.yaml unreadable: {exc}"
    # A pack claiming `stable` with no memory schema cannot serve as one — it would
    # resolve to an empty required-list, i.e. a check that passes vacuously.
    if pack["status"] == STABLE and not pack["memory"].get("required"):
        pack["status"] = DRAFT
        pack["error"] = pack["error"] or "declared stable but ships no memory schema"
    return pack


def list_packs(roles_root: Path | None = None) -> dict:
    """Every pack on disk, by id. Missing directory → empty, never an exception."""
    base = Path(roles_root) if roles_root else roles_dir()
    out: dict = {}
    if not base.is_dir():
        return out
    for d in sorted(p for p in base.iterdir() if p.is_dir()):
        pack = load_pack(d.name, base)
        if pack:
            out[d.name] = pack
    return out


def available(roles_root: Path | None = None) -> list:
    """The ids that may actually be declared — stable packs only.

    Drafts are deliberately absent: this list is what a refusal message offers the
    operator, and offering a draft would be offering something that will be refused.
    """
    return sorted(k for k, p in list_packs(roles_root).items() if p["status"] == STABLE)


# ──────────────────────────────── declaration ────────────────────────────────

def declared_role(root: Path | None = None) -> str:
    """The `role_pack:` value from `.ai/workspace.yaml`, or "" if undeclared.

    Deliberately NOT `role:` — see the module docstring. An unreadable or absent
    file is "undeclared", never an error: an unonboarded directory must resolve.
    """
    path = (root or ws_root()) / ".ai" / "workspace.yaml"
    if not path.is_file():
        return ""
    try:
        data = _load_yaml(path)
    except Exception:  # noqa: BLE001
        return ""
    if not isinstance(data, dict):
        return ""
    return str(data.get("role_pack") or "").strip()


def resolve(root: Path | None = None, roles_root: Path | None = None) -> dict:
    """The full picture: which role is active, whether it was declared, and — when
    the declaration cannot be honoured — why, in a sentence meant to be read.

    `ok` is the field that matters. `id` is always populated with something usable
    so that callers keep working, but `ok: False` means the workspace asked for
    something it did not get, and a caller that acts on a role (onboarding) must
    refuse rather than proceed.
    """
    root = Path(root) if root is not None else ws_root()
    base = Path(roles_root) if roles_root is not None else roles_dir(root)
    name = declared_role(root)
    packs = list_packs(base)
    result = {"id": DEFAULT_ROLE, "declared": bool(name), "declared_as": name,
              "ok": True, "status": STABLE, "error": "", "pack": None,
              "available": sorted(k for k, p in packs.items() if p["status"] == STABLE),
              "roles_dir": str(base)}

    if not packs:
        # Degraded, and it says so. Today's behaviour is preserved via BUILTIN_REQUIRED.
        result["error"] = (f"no role packs found at {base} — falling back to the "
                           f"built-in '{DEFAULT_ROLE}' defaults")
        return result

    if not name:
        pack = packs.get(DEFAULT_ROLE)
        if not pack:
            result["error"] = (f"no '{DEFAULT_ROLE}' pack at {base} — falling back to "
                               "built-in defaults")
            return result
        result["pack"] = pack
        return result

    pack = packs.get(name)
    if pack is None:
        result["ok"] = False
        result["error"] = (f"unknown role '{name}' — available roles: "
                           + (", ".join(result["available"]) or "(none)"))
        result["pack"] = packs.get(DEFAULT_ROLE)
        return result

    if pack["status"] != STABLE:
        why = pack.get("draft_reason") or pack.get("error") or "no schema shipped"
        result["ok"] = False
        result["status"] = DRAFT
        result["error"] = (f"role '{name}' is a draft and cannot be active — {why}. "
                           "Available roles: "
                           + (", ".join(result["available"]) or "(none)"))
        result["pack"] = packs.get(DEFAULT_ROLE)
        return result

    result["id"] = name
    result["pack"] = pack
    return result


# ─────────────────────────────── the one source ──────────────────────────────

def _memory(root, roles_root, key: str, fallback: list) -> list:
    r = resolve(root, roles_root)
    pack = r.get("pack")
    if not pack:
        return list(fallback)
    vals = (pack.get("memory") or {}).get(key)
    if vals is None:
        return list(fallback) if key == "required" else []
    return [str(v) for v in vals]


def required_memory(root: Path | None = None, roles_root: Path | None = None) -> list:
    """The memory-bank files this workspace must have — THE one source.

    `doctor.py` and `session_start.py` both call this. They used to hold a literal
    list each; `tests/test_role_packs.py::TheRequiredListHasExactlyOneSource` reads
    their source and fails if either grows one back.
    """
    return _memory(root, roles_root, "required", BUILTIN_REQUIRED)


def prohibited_memory(root: Path | None = None, roles_root: Path | None = None) -> list:
    """Files this role says should NOT exist here (e.g. `techContext` for a coach).

    Callers should WARN, not fail, on a prohibited file that is present: the file is
    harmless, and failing would turn a green workspace red the moment it declared a
    role — punishing migration at exactly the moment it should be easy.
    """
    return _memory(root, roles_root, "prohibited", [])


def optional_memory(root: Path | None = None, roles_root: Path | None = None) -> list:
    return _memory(root, roles_root, "optional", [])


# ────────────────────────────────── rendering ────────────────────────────────

def render_list(packs: dict) -> str:
    if not packs:
        return "🎭 ROLE PACKS — none found (.ai/roles/ is missing or empty)"
    stable = [p for p in packs.values() if p["status"] == STABLE]
    lines = [f"🎭 ROLE PACKS — {len(packs)} pack(s), {len(stable)} usable"]
    for _id, p in sorted(packs.items()):
        mark = "✅" if p["status"] == STABLE else "📝"
        tail = "" if p["status"] == STABLE else f" — DRAFT: {p['draft_reason'] or p['error']}"
        req = ", ".join((p.get("memory") or {}).get("required") or []) or "(no schema)"
        lines.append(f"   {mark} {_id} — {p['title']}{tail}")
        lines.append(f"        requires: {req}")
    return "\n".join(lines)


def render_active(r: dict) -> str:
    lines = ["🎭 ACTIVE ROLE"]
    src = "declared in .ai/workspace.yaml" if r["declared"] else "not declared — default"
    lines.append(f"   Role: {r['id']} ({src})")
    if not r["ok"]:
        lines.append(f"   ⛔ {r['error']}")
        lines.append(f"   ➤ Running as '{r['id']}' meanwhile; fix `role_pack:` "
                     "in .ai/workspace.yaml.")
    elif r["error"]:
        lines.append(f"   ⚠️  {r['error']}")
    pack = r.get("pack") or {}
    req = ", ".join((pack.get("memory") or {}).get("required") or BUILTIN_REQUIRED)
    pro = ", ".join((pack.get("memory") or {}).get("prohibited") or [])
    lines.append(f"   Requires: {req}")
    if pro:
        lines.append(f"   Prohibits: {pro}")
    return "\n".join(lines)


# ────────────────────────────────────── CLI ──────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="Role packs — one OS, many kinds of workspace.")
    ap.add_argument("action", nargs="?", default="list", choices=("list", "resolve", "show"))
    ap.add_argument("name", nargs="?", help="role id, for `show`")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--required-memory-files", action="store_true",
                    help="print this workspace's required memory-bank files, one "
                         "workspace-relative path per line (e.g. .ai/memory-bank/"
                         "activeContext.md) — the shell entry point `evidence-pack.sh` "
                         "reads instead of holding a fourth copy of the list (PH24-T19).")
    args = ap.parse_args()

    root = ws_root()

    # `evidence-pack.sh`'s bootstrap gate needs this list in bash, without a second
    # implementation of it there — `required_memory()` stays THE one source
    # `TheRequiredListHasExactlyOneSource` pins doctor.py and session_start.py to;
    # this just gives a third language the same answer instead of its own guess.
    if args.required_memory_files:
        for name in required_memory(root):
            print(f".ai/memory-bank/{name}.md")
        return 0

    if args.action == "resolve":
        r = resolve(root)
        print(json.dumps(r, indent=2) if args.json else render_active(r))
        return 0 if r["ok"] else 1

    if args.action == "show":
        if not args.name:
            print("🛑 `role_registry.py show <id>` needs a role id.")
            return 1
        pack = load_pack(args.name)
        if pack is None:
            print(f"🛑 unknown role '{args.name}' — available: "
                  + (", ".join(available()) or "(none)"))
            return 1
        print(json.dumps(pack, indent=2) if args.json
              else render_list({args.name: pack}))
        return 0

    packs = list_packs()
    print(json.dumps(packs, indent=2) if args.json else render_list(packs))
    return 0


if __name__ == "__main__":
    sys.exit(main())
