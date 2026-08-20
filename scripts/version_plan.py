#!/usr/bin/env python3
"""
version_plan.py — the next version bump, tied to specific goals, proposed not typed. (PH9-T03)

`.ai/plan.md` §3, in the operator's own words: *"Versions get planned properly — small
and big — derived from those docs, and named properly, for code and non-code
workspaces alike."* G3: *"a bump is proposed only when its goals' criteria are met."*

Today a version number is whatever an AI decided to type into a sentence.
`doc/VERSIONING.md` documents a real SemVer policy, but `.ai/os_version.json` —
the file `session_start.py` actually reads for drift — has not moved since
3.4.0 (2026-07-19) while five theme-phases (v3.5.0 ... v3.9.0) have shipped in
prose only. This module is the missing join: plan goals -> a real version
proposal, computed, never asserted.

## One ladder rung per plan -- OR per phase, once a plan has phases (PH19-T01)

A workspace holds exactly one agreed `.ai/plan.md` at a time. The ORIGINAL
design (PH9-T03) tied the next bump to *all* of the plan's goals at once,
reusing `goal_progress.all_met()` -- correct for a plan whose goals ARE one
milestone, wrong once a plan accretes goals faster than any one phase ships
(this workspace: G1..G17 and counting, spanning phases targeting v3.5.0
through v4.5.0). Requiring every goal met before ANY bump meant `v3.4.0` sat
stamped through six-plus shipped phases -- found live 2026-08-10 (PH19-T01).

**The fix:** when `tasks.md` declares real `## PHASE N (vX.Y.Z)` headings,
`phase_ledger.next_stampable()` computes the proposal instead -- one rung
per phase, walked in document order, never skipping an open phase to reach
a later shipped one. See `phase_ledger.py`'s own docstring for the full
design. A workspace with no such headings (every fleet workspace today
except this kernel) falls through unchanged to the original all-goals-met
path below -- this is additive, not a replacement, and every existing test
for the flat path is untouched.

## "Current version" means something different per workspace

`.ai/os_version.json` is the **kernel's own release stamp** only in the kernel
(this repo) -- `doc/VERSIONING.md`'s release checklist already bumps it by
hand. In every *other* onboarded workspace the same file is a **kernel-drift
stamp** ("which kernel version was last deployed here"), unrelated to that
workspace's own product version. Reusing it as "current version" for a child
workspace would silently report the kernel's version as if it were the
child's. So: the kernel reads/proposes against `.ai/os_version.json` directly
(the file it already canonically owns); every other workspace reads a new,
workspace-owned `.ai/version.json` (SemVer) or `.ai/edition.json` (edition),
defaulting to `0.1.0` / edition `1` **in memory only** when absent -- this
tool never creates either file.

## Scheme detection is a heuristic, pending PH8-T01

`.ai/workspace.yaml`'s `role:` (PH9-T03's forward-compat hook for PH8-T01,
which does not exist yet) is checked first. Failing that: this workspace *is*
the kernel (`scripts/onboard_project.sh` exists at root -- never deployed to a
child, so a clean unique signal) -> SemVer. A recognizable manifest at root
-> SemVer. Otherwise **edition** -- the honest default for "no evidence this
is a software codebase." Counting source files was rejected: every governed
workspace carries kernel-deployed `scripts/*.py` regardless of domain, so
file-count alone cannot distinguish a life-planning workspace from a real
Python project.

## `--apply` writes the real stamp; deploying it stays a separate, asked step

`--apply` writes exactly the proposal `ladder()` computed (never a guessed
number) to the workspace's own version file, gated on the open validation
gate. It does NOT update `doc/VERSIONING.md`'s history table or
`AI_CHANGELOG.md` prose -- `doc/VERSIONING.md`'s own release checklist still
owns that as a deliberate, written step. Stamping the kernel is also not
deploying it: a successful kernel apply returns a `fleet_offer` string (never
auto-run) pointing at `just fleet-upgrade --apply`, so the version bump and
the fleet-wide deploy stay two explicit, separately-approved acts (PH19-T01
Slice 2) -- the same "never automatic" rule the deploy step itself already
enforces.

Usage:
  version_plan.py                # this workspace's next-version proposal
  version_plan.py --json
  version_plan.py --apply        # write the real stamp (gate must be open)
"""

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

IST = timezone(timedelta(hours=5, minutes=30))

# Manifest files that signal "this is a software codebase" in the absence of
# a declared role. Order doesn't matter -- any one hit is enough.
_MANIFESTS = ("pyproject.toml", "package.json", "requirements.txt", "setup.py",
              "Cargo.toml", "go.mod", "Gemfile")

# Non-code roles, for the PH8-T01 forward-compat hook. Kept narrow and
# explicit rather than "anything not 'software-engineer'" so an unrecognised
# future role falls through to the file-based heuristic instead of guessing.
_NON_CODE_ROLES = {"life-coach", "financial-analyst", "writer", "research-analyst", "coach"}

DEFAULT_SEMVER = "0.1.0"
DEFAULT_EDITION = 1

# PH19-T01 Slice 2, sub-part (c): a stamp is immediately followed by an
# explicit, gated OFFER to deploy it -- never an auto-run. Kernel-only: a
# child workspace's own version.json/edition.json stamp is its product
# version, not a kernel drift stamp, and it has no fleet to deploy to.
FLEET_OFFER = ("Deploy this stamp to the fleet? Not automatic — run "
               "`just fleet-upgrade --apply` yourself when ready (the "
               "validation gate and ADR-021's branch policy still apply).")


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def is_kernel(root: Path) -> bool:
    """Is this workspace the God Mode kernel itself?

    `scripts/onboard_project.sh` deploys the fleet; it is never itself one of
    the files deployed *to* a workspace (see `doc/architecture.md`'s
    kernel-owned/workspace-owned split), so its presence is a clean, unique
    signal -- unlike every other kernel-deployed script, which lands
    everywhere regardless of domain.
    """
    return (root / "scripts" / "onboard_project.sh").is_file()


def _declared_role(root: Path) -> str | None:
    """`.ai/workspace.yaml`'s `role:` line, if the file exists. (PH8-T01 hook)

    No workspace has this file yet -- PH8-T01 is still pending -- so this is
    dead code in practice today, pinned by a fixture so scheme() does not need
    revisiting once PH8-T01 ships. Deliberately reads one line with a plain
    regex rather than a YAML parser: this repo is stdlib-first, and the same
    dependency-light reading `plan_workspace._split_frontmatter` already uses.
    """
    path = root / ".ai" / "workspace.yaml"
    if not path.is_file():
        return None
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    m = re.search(r"^role:\s*(\S+)", raw, re.M)
    return m.group(1).strip("'\"") if m else None


def scheme(root: Path | None = None) -> str:
    """"semver" or "edition" -- which version scheme this workspace uses."""
    root = root or ws_root()
    role = _declared_role(root)
    if role:
        return "edition" if role in _NON_CODE_ROLES else "semver"
    if is_kernel(root):
        return "semver"
    if any((root / m).is_file() for m in _MANIFESTS):
        return "semver"
    return "edition"


def _read_json_field(path: Path, field: str):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data.get(field)


def current_version(root: Path, sch: str) -> tuple[str | int, str]:
    """(current value, where it came from). Never writes; a missing file is a
    default, not an error."""
    if sch == "semver":
        if is_kernel(root):
            v = _read_json_field(root / ".ai" / "os_version.json", "version")
            if v:
                return v, ".ai/os_version.json (the kernel's own release stamp)"
            return DEFAULT_SEMVER, ".ai/os_version.json missing/unreadable — defaulting"
        v = _read_json_field(root / ".ai" / "version.json", "version")
        if v:
            return v, ".ai/version.json"
        return DEFAULT_SEMVER, "no .ai/version.json yet — defaulting"
    v = _read_json_field(root / ".ai" / "edition.json", "edition")
    if isinstance(v, int):
        return v, ".ai/edition.json"
    return DEFAULT_EDITION, "no .ai/edition.json yet — defaulting"


_SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")


def _bump_minor(current: str) -> str | None:
    """x.y.z -> x.(y+1).0. None if `current` doesn't parse as SemVer."""
    m = _SEMVER_RE.match(str(current))
    if not m:
        return None
    major, minor, _patch = (int(g) for g in m.groups())
    return f"{major}.{minor + 1}.0"


def ladder(root: Path | None = None) -> dict:
    """The next version bump for this workspace, tied to its plan's goals.

    Pure read. Refuses (via `goal_progress.progress()`'s own reason) when
    there is no agreed plan or no goals to measure against -- delegated, not
    re-implemented, the same rule every PH9 module in this tier follows.
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "scheme": None, "current": None,
          "current_source": "", "retired": False, "proposal": None, "basis": "",
          "blocker": None}

    try:
        import plan_workspace
        import goal_progress
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"plan_workspace/goal_progress unavailable ({exc})."
        return out

    ret = plan_workspace.retirement(root)
    sch = scheme(root)
    out["scheme"] = sch
    current, source = current_version(root, sch)
    out["current"], out["current_source"] = current, source

    if ret["retired"]:
        out.update(ok=True, retired=True,
                   reason=f"workspace retired ({ret['reason'] or 'no reason recorded'}) "
                          "— no further version bumps proposed.")
        return out

    # Phase-ledger path (PH19-T01): only for semver, and only when tasks.md
    # actually declares `## PHASE` headings -- a workspace without them
    # (every fleet workspace today except this kernel) falls straight
    # through to the unchanged all-goals-met path below.
    if sch == "semver":
        try:
            import phase_ledger
        except Exception:  # noqa: BLE001
            phase_ledger = None
        if phase_ledger is not None and phase_ledger.parse_headings(root):
            out["basis"] = "phase_ledger — sequential phase headings, not all-goals-met"
            ns = phase_ledger.next_stampable(str(current), root=root)
            if not ns["ok"]:
                out["reason"] = ns["reason"]
                return out
            out.update(ok=True, reason=ns["reason"], blocker=ns["blocker"])
            if ns["proposal"]:
                out["proposal"] = {"next": ns["proposal"]["next"],
                                   "goals": ns["proposal"]["phases"],
                                   "reason": ns["proposal"]["reason"]}
            return out

    prog = goal_progress.progress(root)
    if not prog["ok"]:
        out["reason"] = prog["reason"]
        return out

    goal_ids = [g["id"] for g in prog["goals"]]
    met = goal_progress.all_met(prog)

    if not met:
        open_ids = [g["id"] for g in prog["goals"] if not g["met"]]
        out.update(ok=True,
                   reason=f"{len(open_ids)} of {len(goal_ids)} goal(s) still open "
                          f"({', '.join(open_ids)}) — no bump proposed.")
        return out

    if sch == "semver":
        next_v = _bump_minor(current)
        if next_v is None:
            out["reason"] = f"cannot propose — {source} value {current!r} is not valid SemVer."
            return out
    else:
        next_v = int(current) + 1

    out.update(ok=True,
               proposal={"next": next_v, "goals": goal_ids,
                        "reason": f"all {len(goal_ids)} goal(s) met "
                                  f"({', '.join(goal_ids)})"},
               reason=f"all {len(goal_ids)} goal(s) met — propose {current} → {next_v}")
    return out


def render(l: dict) -> None:
    if not l["ok"]:
        print(f"🛑 Version plan unavailable — {l['reason']}")
        return
    print(f"  🏷️  VERSION LADDER — {l['scheme']} · current {l['current']} "
          f"({l['current_source']})")
    if l["retired"]:
        print(f"     {l['reason']}")
        return
    if l["proposal"]:
        p = l["proposal"]
        print(f"     ✅ propose {l['current']} → {p['next']} — {p['reason']}")
    else:
        print(f"     {l['reason']}")
    if l.get("blocker"):
        b = l["blocker"]
        tail = f" ({', '.join(b['open_tasks'])})" if b["open_tasks"] else ""
        print(f"     ⏭  next blocked by PHASE {b['phase']} (v{b['version']}, {b['title']}){tail}")


def apply_stamp(root: Path | None = None, note: str = "") -> dict:
    """Write the real version stamp `ladder()` proposed. Never guesses a
    proposal of its own -- calls `ladder()` and stamps exactly what it says.

    Gated on the open validation gate, the same embargo `push`/`tt-sync`/
    `fleet-upgrade --apply` already obey — a version stamp is read by every
    other workspace's drift check, so it is exactly the kind of external-
    facing mutation this workspace never lets an unverified session make.
    Decision-logged on every path, refusals included (PH7-T09's own
    "the allows are the denominator" rule, applied to a refusal ledger too).
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "written": None, "fleet_offer": None}

    l = ladder(root)
    if not l["ok"] or not l.get("proposal"):
        out["reason"] = l["reason"] or "no proposal to apply."
        _log_apply("deny", out["reason"], root)
        return out

    try:
        import gate_check
        gate = gate_check.check(root)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"gate_check.py unavailable ({exc}) — refusing to stamp blind."
        _log_apply("deny", out["reason"], root)
        return out
    if not gate["open"]:
        out["reason"] = (f"validation gate is BLOCKED — {gate['reason']}. "
                         "Run `just verify-safe`, then re-try.")
        _log_apply("deny", out["reason"], root)
        return out

    # PH22-T02: the rung's own contract. The gate above proves the workspace is
    # *safe* to stamp; this proves the version being stamped actually contains
    # what was agreed it would contain. A workspace with no `.ai/versions.md`
    # (every workspace today) passes straight through — absence is honest, and
    # refusing a bump for a contract nobody has written yet would block the whole
    # fleet on a file this task deliberately does not write on their behalf.
    try:
        import versions as versions_mod
        blocked = versions_mod.bump_blocked(root=root)
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"versions.py unavailable ({exc}) — refusing to stamp blind."
        _log_apply("deny", out["reason"], root)
        return out
    if blocked["blocked"]:
        out["reason"] = (f"the version contract is not met — {blocked['reason']}. "
                         "Finish or re-verify them, or move them to a later rung in "
                         "`.ai/versions.md` with the operator's agreement.")
        _log_apply("deny", out["reason"], root)
        return out

    sch = l["scheme"]
    next_v, reason = l["proposal"]["next"], l["proposal"]["reason"]
    now = datetime.now(timezone.utc)
    entry = note or f"v{next_v}: {reason}"

    if sch == "semver" and is_kernel(root):
        path = root / ".ai" / "os_version.json"
        data = json.loads(path.read_text()) if path.is_file() else {"changelog": []}
        data["version"] = next_v
        data["updated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        data["updated_at_ist"] = now.astimezone(IST).strftime("%Y-%m-%d %H:%M IST")
        data.setdefault("changelog", []).insert(0, entry)
        path.write_text(json.dumps(data, indent=2) + "\n")
    elif sch == "semver":
        path = root / ".ai" / "version.json"
        path.write_text(json.dumps({"version": next_v}, indent=2) + "\n")
    else:
        path = root / ".ai" / "edition.json"
        path.write_text(json.dumps({"edition": next_v}, indent=2) + "\n")

    out.update(ok=True, written=str(path),
              reason=f"stamped {l['current']} → {next_v} — {reason}")
    if sch == "semver" and is_kernel(root):
        out["fleet_offer"] = FLEET_OFFER
    _log_apply("allow", out["reason"], root)
    return out


def _log_apply(decision: str, reason: str, root: Path) -> None:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import decision_log
        decision_log.record("policy", decision, "version_plan", root=root,
                            action="version-plan --apply", reason=reason)
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    ap = argparse.ArgumentParser(
        description="The next version bump this workspace has earned, tied to its plan's goals.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--apply", action="store_true",
                     help="write the real stamp (gated on the open validation gate)")
    ap.add_argument("--note", default="", help="changelog entry text for --apply (optional)")
    args = ap.parse_args()

    root = ws_root()
    if args.apply:
        a = apply_stamp(root, note=args.note)
        if args.json:
            print(json.dumps(a, indent=2, default=str))
        elif a["ok"]:
            print(f"  ✅ {a['reason']}\n     wrote {a['written']}")
            if a.get("fleet_offer"):
                print(f"\n  ⬆️  {a['fleet_offer']}")
        else:
            print(f"  🛑 {a['reason']}")
        return 0 if a["ok"] else 1

    l = ladder(root)
    if args.json:
        print(json.dumps(l, indent=2, default=str))
    else:
        render(l)
    return 0 if l["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
