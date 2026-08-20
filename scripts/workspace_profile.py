#!/usr/bin/env python3
"""
workspace_profile.py — how much governance ceremony a workspace runs (PH26-T01, Goal G21).

The 2-work-slot session budget was designed for THIS workspace, where one task
rewrites an enforcement path and deserves a whole session. It was then deployed to
46 workspaces, and in the ones doing ten-minute scaffolding tasks it is not obeyed —
it is overridden.

That is measured, not felt. `@zenithos` was onboarded on 2026-08-18; by 08:19 that
same morning its `.ai/decision-log/2026-08-18.jsonl` held **five** `override work-done`
records, every one reading *"PH1 boot: user authorized exceeding 2+3 for this phase"*,
and its `session-state.json` `prior` block recorded a session that credited seven work
tasks and closed none of the three closures. Each override was correct, authorised and
logged, exactly as designed — which is the finding. A control whose normal outcome is
an authorised override is no longer distinguishable from a control that is off, and the
operator who learns overrides are routine is the same operator who has to take
`close git-push --override` seriously. `role_registry.py` states the general form: once
someone learns to ignore one check, the gate stops working for the reasons it was built.

So a workspace may now say what shape of work it does, and the OS stops pretending
there is one right answer.

## Profiles are data; this module is the only code

A profile is a map in `.ai/profiles.yaml` — `work_max` and `fast_closure`, and nothing
else. Adding one is editing data. That is the same choice `role_registry` made for role
packs and it is what keeps "ship two profiles" a sane scope instead of a framework.

## The knobs are a CLOSED set, and no knob is a gate

`KNOBS` is the whole vocabulary: the work-slot cap, and whether the two composite
closure recipes are available. There is deliberately no key for the validation gate,
the unit-test suite, the pre-work brief, `plan-before-code`, or the pre-push
self-review — those are required in every profile. `lite` means *fewer turns*, never
*less proof*, and the test suite fails the day a pack declares a key outside `KNOBS`.
That test is the safety argument for deploying this fleet-wide; without it "no profile
can weaken a gate" is a sentence in a docstring, which is the class of claim this
workspace exists to distrust.

## Absent is the default, unknown is refused, and both go STRICT

Three resolutions, one direction:

  • **no `profile:` key** → `full`, which is today's behaviour byte for byte. The
    migration guarantee for the 45 workspaces that will never declare one, asserted in
    `tests/test_workspace_profile.py` against the literal `2` rather than against
    whatever the code happens to say.
  • **an unknown name** → refused BY NAME, with the available profiles listed, and
    resolved to `full`. Never silently upgraded to the laxer profile. The identical
    rule `role_registry.resolve` and `model_registry.resolve_running` already apply to
    roles and model tiers, for the identical reason: a typo must not grant privilege.
  • **an expired `profile_until:`** → `full`, with the date in the reason.

`profile_until:` exists because the operator's request scoped the relaxation to
*"initial scaffolding/sprints"*, and a relaxation with no end date is scoped to nothing.
`@zenithos` had already written its own bootstrap expiry (2026-09-15) into its
`workspace.yaml` notes before this was built; reading the date the workspace already
declared is the cheap half. An UNPARSEABLE date resolves to `full` too — a broken
declaration is a broken declaration, and the failure direction that costs something is
the one where a malformed line grants `lite` forever.

Usage:
  workspace_profile.py show [--json]
  workspace_profile.py set lite --until 2026-09-15
  workspace_profile.py set full
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import policy_engine  # noqa: E402

#: The complete vocabulary a profile may speak. Adding to this list is adding a
#: dimension along which workspaces can differ — argue it in the test first.
KNOBS = ("work_max", "fast_closure")

DEFAULT_PROFILE = "full"

#: Used when `.ai/profiles.yaml` is missing entirely (an old workspace that has not
#: received the deploy yet). It must equal the shipped `full` pack — a test asserts
#: the two agree — so a workspace running yesterday's tree behaves like one running
#: today's, rather than losing its budget to a missing file.
FALLBACK = {"full": {"work_max": 2, "fast_closure": False}}

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _load_yaml(path: Path) -> dict:
    """Real PyYAML when available, the bundled reader otherwise.

    Same choice `role_registry._load_yaml` and `model_registry._load_yaml` made, for
    the same reason: this repo must run on a machine with no third-party packages.
    """
    text = path.read_text(encoding="utf-8")
    try:
        import yaml  # type: ignore
        data = yaml.safe_load(text)
    except ImportError:
        data = policy_engine.mini_yaml_load(text)
    return data if isinstance(data, dict) else {}


def packs_path(root: Path | None = None) -> Path:
    return (Path(root) if root is not None else ws_root()) / ".ai" / "profiles.yaml"


def _as_int(value, fallback: int) -> int:
    """mini_yaml_load has no numeric type — every scalar arrives as a string.

    The same normalisation `workspace_declare.validate_schema` applies to
    `ladder_stage` and `north_star.load_ladder` applies to `stage`, and it is here
    for the same reason: the pack must mean the same thing under both readers.
    """
    if isinstance(value, bool) or value is None:
        return fallback
    if isinstance(value, int):
        return value
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return fallback


def _as_bool(value, fallback: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    return str(value).strip().lower() in ("true", "yes", "1")


def packs_deployed(packs: Path | None = None, root: Path | None = None) -> bool:
    """Does this workspace actually have the profile pack, or is it on `FALLBACK`?

    Worth its own function because the two cases produce the same resolution
    (`full`) and need different sentences. A workspace that declares `lite` and has
    never received `.ai/profiles.yaml` has not made a typo — it is behind on a
    deploy, and telling it "unknown profile 'lite'" sends it to fix the one thing
    that is not wrong. That failure shape is precisely the `@zenithos` finding about
    `deployed-digest` naming a remedy the workspace cannot run.
    """
    path = Path(packs) if packs is not None else packs_path(root)
    return path.is_file()


def load_packs(packs: Path | None = None, root: Path | None = None) -> dict:
    """Every declared profile, normalised. Missing/unreadable file → `FALLBACK`."""
    path = Path(packs) if packs is not None else packs_path(root)
    if not path.is_file():
        return {k: dict(v) for k, v in FALLBACK.items()}
    try:
        raw = _load_yaml(path)
    except Exception:  # noqa: BLE001
        return {k: dict(v) for k, v in FALLBACK.items()}
    out: dict = {}
    for name, body in (raw or {}).items():
        if not isinstance(body, dict):
            continue
        pack = {}
        for key, value in body.items():
            if key in ("title", "summary"):
                pack[key] = str(value or "")
            elif key == "work_max":
                pack[key] = _as_int(value, FALLBACK["full"]["work_max"])
            elif key == "fast_closure":
                pack[key] = _as_bool(value)
            else:
                # Kept verbatim so the closed-set test can SEE it and fail. Dropping
                # an unknown key here would hide exactly what that test looks for.
                pack[key] = value
        out[str(name).strip()] = pack
    return out or {k: dict(v) for k, v in FALLBACK.items()}


def declared(root: Path | None = None) -> tuple[str, str]:
    """`(profile, profile_until)` from `.ai/workspace.yaml`; `("", "")` if undeclared.

    An unreadable or absent file is "undeclared", never an error: an unonboarded
    directory must resolve rather than raise.
    """
    path = (Path(root) if root is not None else ws_root()) / ".ai" / "workspace.yaml"
    if not path.is_file():
        return "", ""
    try:
        data = _load_yaml(path)
    except Exception:  # noqa: BLE001
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    return (str(data.get("profile") or "").strip(),
            str(data.get("profile_until") or "").strip())


def resolve(root: Path | None = None, packs: Path | None = None,
            today: date | None = None) -> dict:
    """Which profile is active, and — when a declaration was not honoured — why.

    `ok` is the field that matters. `id` is always populated with something usable so
    callers keep working; `ok: False` means the workspace asked for something it did
    not get, and it always got the STRICTER thing.
    """
    root = Path(root) if root is not None else ws_root()
    table = load_packs(packs, root)
    name, until = declared(root)
    today = today or date.today()

    out = {"id": DEFAULT_PROFILE, "declared": bool(name), "declared_as": name,
           "ok": True, "reason": "", "expires": until, "expired": False,
           "work_max": 0, "fast_closure": False, "available": sorted(table)}

    def _apply(pid: str) -> dict:
        pack = table.get(pid) or FALLBACK["full"]
        out["id"] = pid
        out["work_max"] = _as_int(pack.get("work_max"), FALLBACK["full"]["work_max"])
        out["fast_closure"] = _as_bool(pack.get("fast_closure"))
        out["title"] = str(pack.get("title") or pid)
        return out

    if not name:
        out["reason"] = "no `profile:` in .ai/workspace.yaml — the default, unchanged behaviour"
        return _apply(DEFAULT_PROFILE)

    key = name.strip().lower()
    if key not in table:
        out["ok"] = False
        if not packs_deployed(packs, root):
            out["reason"] = (
                f"this workspace declares profile {name!r}, but `.ai/profiles.yaml` "
                f"is not deployed here — so nothing can say what {name!r} means. "
                f"Running {DEFAULT_PROFILE!r} until it is: `god-upgrade .` from the "
                "kernel ships the pack. This is a missing deploy, not a typo.")
        else:
            out["reason"] = (
                f"unknown profile {name!r} — available: {', '.join(sorted(table))}. "
                f"Running {DEFAULT_PROFILE!r} instead: an unrecognised declaration is "
                "never upgraded to a laxer profile.")
        return _apply(DEFAULT_PROFILE)

    if until:
        if not _DATE_RE.match(until):
            out["ok"] = False
            out["reason"] = (
                f"`profile_until: {until!r}` is not a YYYY-MM-DD date, so this "
                f"declaration cannot be shown to be current — running "
                f"{DEFAULT_PROFILE!r}. A relaxation whose end date nothing can read "
                "is a relaxation with no end date.")
            return _apply(DEFAULT_PROFILE)
        try:
            deadline = date.fromisoformat(until)
        except ValueError:
            out["ok"] = False
            out["reason"] = (f"`profile_until: {until!r}` is not a real date — "
                             f"running {DEFAULT_PROFILE!r}.")
            return _apply(DEFAULT_PROFILE)
        if deadline < today:
            out["expired"] = True
            # PH26-T04: `ok` answers "did this workspace get what it declared", and an
            # expired declaration did not — it asked for `key` and got `full`, the same
            # shape as an unknown name or an undeployed pack, both of which already
            # report False. Set here rather than in `_render` so every reader inherits
            # the right answer instead of remembering `and not r["expired"]` for itself;
            # `session_start.py`'s condition becomes redundant rather than load-bearing.
            out["ok"] = False
            out["reason"] = (
                f"the {key!r} profile expired on {until} — back on "
                f"{DEFAULT_PROFILE!r}. Extend the date deliberately (`just profile-set "
                f"{key} --until <date>`) or leave it; a sprint relaxation is not "
                "supposed to outlive the sprint.")
            return _apply(DEFAULT_PROFILE)
        out["reason"] = f"declared in .ai/workspace.yaml — {key}, until {until}"
        return _apply(key)

    out["reason"] = f"declared in .ai/workspace.yaml — {key}, no expiry set"
    return _apply(key)


def work_max(root: Path | None = None, packs: Path | None = None) -> int:
    """The session work-slot cap for this workspace."""
    return resolve(root, packs)["work_max"]


def fast_closure(root: Path | None = None, packs: Path | None = None) -> bool:
    """May this workspace use `just wrap` / `just land`?"""
    return bool(resolve(root, packs)["fast_closure"])


def label(root: Path | None = None, packs: Path | None = None) -> str:
    """One line for a briefing: the active profile and what it costs."""
    r = resolve(root, packs)
    bits = [f"{r['id']}"]
    if r["expires"] and not r["expired"]:
        bits.append(f"until {r['expires']}")
    if r["expired"]:
        bits.append(f"expired {r['expires']}")
    suffix = " · ".join(bits[1:])
    return (f"{bits[0]}" + (f" ({suffix})" if suffix else "")
            + f" — {r['work_max']} work slot(s)"
            + (" · fast closure" if r["fast_closure"] else ""))


# ── declaring one ────────────────────────────────────────────────────────────

def set_profile(name: str, until: str = "", root: Path | None = None,
                packs: Path | None = None) -> dict:
    """Write `profile:` (and optionally `profile_until:`) into `.ai/workspace.yaml`.

    Refuses an unknown name up front rather than writing a declaration that
    `resolve()` would then refuse — a file whose stated intent the OS ignores is
    worse than no file.
    """
    root = Path(root) if root is not None else ws_root()
    path = root / ".ai" / "workspace.yaml"
    table = load_packs(packs, root)
    key = (name or "").strip().lower()
    if key not in table:
        return {"ok": False, "reason": f"unknown profile {name!r} — available: "
                                       f"{', '.join(sorted(table))}"}
    if until and not _DATE_RE.match(until.strip()):
        return {"ok": False, "reason": f"--until must be YYYY-MM-DD, got {until!r}"}
    if not path.is_file():
        return {"ok": False, "reason": f"no {path} — run `god-new` / `god-repair` first"}

    lines = path.read_text(encoding="utf-8").splitlines()
    kept = [ln for ln in lines
            if not re.match(r"^\s*profile(_until)?\s*:", ln)]
    while kept and not kept[-1].strip():
        kept.pop()
    kept.append("")
    kept.append("# PH26-T01 — how much governance ceremony this workspace runs.")
    kept.append("# `full` (or omitting this) is the kernel's own 2-slot budget and")
    kept.append("# step-by-step closure. `lite` raises the slot cap and enables")
    kept.append("# `just wrap` / `just land`. NO profile weakens a gate.")
    kept.append(f'profile: "{key}"')
    if until:
        kept.append(f'profile_until: "{until.strip()}"')
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")
    return {"ok": True, "reason": f"{path} now declares profile: {key}"
                                  + (f" (until {until})" if until else ""),
            "profile": key, "until": until}


# ── CLI ──────────────────────────────────────────────────────────────────────

def _render(r: dict) -> None:
    icon = "✅" if r["ok"] else "⚠️ "
    print("═" * 62)
    print("  🎚️  GOVERNANCE PROFILE — how much ceremony this workspace runs")
    print("═" * 62)
    print(f"  {icon} active: {r['id']}  ({r.get('title', r['id'])})")
    print(f"     {r['reason']}")
    print("  " + "─" * 58)
    print(f"     work slots per session : {r['work_max']}")
    print(f"     fast closure recipes   : "
          f"{'just wrap / just land' if r['fast_closure'] else 'not enabled — run the pipeline step by step'}")
    print(f"     available profiles     : {', '.join(r['available'])}")
    print("  " + "─" * 58)
    print("  Unchanged in EVERY profile: just verify-safe, the unit-test suite, the")
    print("  pre-work brief, plan-before-code, and the self-review before a push.")
    print("  A profile moves how many tasks you START, never how well you do each one.")
    print("═" * 62)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="The workspace's governance profile.")
    sub = ap.add_subparsers(dest="cmd")
    p_show = sub.add_parser("show", help="the active profile")
    p_show.add_argument("--json", action="store_true")
    p_set = sub.add_parser("set", help="declare a profile in .ai/workspace.yaml")
    p_set.add_argument("name")
    p_set.add_argument("--until", default="", help="YYYY-MM-DD — when it reverts to full")
    p_set.add_argument("--json", action="store_true")
    ap.add_argument("--root", default="")
    args = ap.parse_args(argv)
    root = Path(args.root) if args.root else ws_root()

    if args.cmd == "set":
        out = set_profile(args.name, args.until, root)
        if args.json:
            print(json.dumps(out, indent=2))
        else:
            print(("✅ " if out["ok"] else "❌ ") + out["reason"])
            if out["ok"]:
                _render(resolve(root))
        return 0 if out["ok"] else 1

    r = resolve(root)
    if getattr(args, "json", False):
        print(json.dumps(r, indent=2))
    else:
        _render(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
