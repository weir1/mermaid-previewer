#!/usr/bin/env python3
"""
workspace_audit.py — what a workspace actually is, before anyone asks what it should be.

`.ai/plan.md` (PH9-T01) scaffolds the *same blank template* for a five-minute-old
workspace and one with 400 commits and a real test suite. The operator's own
words in this workspace's plan say what should happen instead: *"For workspaces
that already exist, the AI works it out first. It audits all the code and all
the docs, tells me what the workspace actually is, how much is done and how
much remains — then asks me what I want the end goal to be anyway."*
`plan_workspace.py`'s own docstring names this file: *"keeping it honest against
the code is PH9-T02's audit."*

## Two different kinds of "how much is done" — never blended

1. **Structural standing** — observable in *any* workspace, whether or not it
   has adopted `.ai/plan.md` yet: git activity, test/doc/README presence, a
   source-file histogram, and ledger task counts if a ledger exists. This is
   what lets the tool say something true about a workspace **before** a goal
   exists — the common case; most of the fleet has no plan yet.
2. **Goal-based completion** — delegates to `goal_progress.progress()`
   (PH9-T08) when `.ai/plan.md` already declares goals. Not recomputed here: a
   second private version of "how much of G4 is done" is exactly the drift
   `active_task()` and `decision_log.summarize()` exist to prevent. Where no
   plan exists yet, this half is reported as unavailable, not guessed —
   design law 2 ("measured, never asserted") applies to the *absence* of a
   number as much as to the number itself.

## This tool is read-only

It reports. It does not scaffold, seed, or touch `.ai/plan.md` — that stays on
`plan_workspace.py`'s existing scaffold/agree/generate path, whose write
discipline (never overwrite, never rewrite "What I want", refuse to generate
from an unagreed plan) already exists. `standing_context()` renders a
paste-ready paragraph in the same voice as this workspace's own `.ai/plan.md`
("Standing context (from the workspace's history, not from this
conversation)") for the AI to offer *into* a discussion — the "ask the end goal
anyway" step stays a conversation the AI has with the user, using
`AskUserQuestion` per AGENTS.md's interactive-decisions rule, not something a
script performs unattended.

Usage:
  workspace_audit.py                # plain-English report for this workspace
  workspace_audit.py --json
  workspace_audit.py --standing     # print just the paste-ready paragraph
"""

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

GIT_TIMEOUT_S = 15

# Directories never worth walking for language/test signals: vendor code, VCS
# internals, caches. A miscount here (e.g. counting node_modules as "the
# workspace") would make the histogram describe a dependency tree, not the
# workspace.
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "dist",
              "build", ".next", ".cache", "vendor", "target", ".pytest_cache"}


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _git(root: Path, *args: str) -> tuple[bool, str]:
    try:
        r = subprocess.run(["git", *args], cwd=root, capture_output=True, text=True,
                           timeout=GIT_TIMEOUT_S)
        if r.returncode != 0:
            return False, (r.stderr or r.stdout).strip()
        return True, r.stdout
    except FileNotFoundError:
        return False, "git not installed"
    except subprocess.TimeoutExpired:
        return False, f"git {' '.join(args)} timed out after {GIT_TIMEOUT_S}s"
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def git_stats(root: Path) -> dict:
    """Commit history and working-tree state. Never raises — reports `available`.

    A target that is not a git repo (a handful of fleet workspaces genuinely
    aren't — see PH6-T15's known-issues list) or where `git` itself is missing
    must still produce a usable audit for its other sections, so failure here
    is data, not an exception.
    """
    ok, out = _git(root, "log", "--format=%aI")
    if not ok:
        return {"available": False, "reason": out or "not a git repository"}
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return {"available": True, "commits": 0, "first_commit": None,
                "last_commit": None, "idle_days": None, "dirty": False,
                "dirty_count": 0}
    last_raw, first_raw = lines[0], lines[-1]
    try:
        last_dt = datetime.fromisoformat(last_raw)
        idle_days = round((datetime.now(timezone.utc) - last_dt.astimezone(timezone.utc))
                          .total_seconds() / 86400, 1)
    except ValueError:
        idle_days = None
    dirty_ok, dirty_out = _git(root, "status", "--porcelain")
    dirty_lines = [ln for ln in dirty_out.splitlines() if ln.strip()] if dirty_ok else []
    return {"available": True, "commits": len(lines), "first_commit": first_raw,
            "last_commit": last_raw, "idle_days": idle_days,
            "dirty": bool(dirty_lines), "dirty_count": len(dirty_lines)}


def file_stats(root: Path, cap: int = 20000) -> dict:
    """README / tests / docs presence + a source-language histogram.

    Bounded by `cap` total files walked so a workspace with a large data or
    cache directory (real in this fleet — scrapers, exports) cannot make an
    audit hang; hitting the cap is reported rather than silently truncating.
    """
    readme = next((p.name for p in root.glob("README*") if p.is_file()), None)
    ext_counts: dict[str, int] = {}
    test_files = 0
    walked = 0
    capped = False

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS and not d.startswith(".")]
        for name in filenames:
            walked += 1
            if walked > cap:
                capped = True
                break
            ext = Path(name).suffix.lower() or "(none)"
            ext_counts[ext] = ext_counts.get(ext, 0) + 1
            lower = name.lower()
            if (lower.startswith("test_") or lower.endswith("_test.py")
                    or lower.endswith((".test.js", ".test.ts", ".spec.js", ".spec.ts"))):
                test_files += 1
        if capped:
            break

    top_langs = sorted(ext_counts.items(), key=lambda kv: -kv[1])[:6]
    docs_dir = next((d for d in ("docs", "doc") if (root / d).is_dir()), None)
    ai_docs = root / ".ai" / "docs"
    ai_doc_files = sorted(p.name for p in ai_docs.glob("*.md")) if ai_docs.is_dir() else []

    return {"readme": readme, "test_files": test_files,
            "docs_dir": docs_dir, "ai_docs": ai_doc_files,
            "top_languages": top_langs, "files_walked": walked, "capped": capped}


def ledger_stats(root: Path) -> dict:
    """Task counts by status, if this workspace has adopted the ledger convention.

    Delegates entirely to `task_ledger.all_tasks()` — the same denominator
    `goal_progress.py` uses — rather than re-deriving status counts from the raw
    files a second way.
    """
    try:
        import task_ledger
    except Exception as exc:  # noqa: BLE001
        return {"available": False, "reason": f"task_ledger.py unavailable ({exc})"}
    tasks = task_ledger.all_tasks(root=root)
    by_status: dict[str, int] = {}
    for t in tasks:
        status = t["status"] or "(unstated)"
        by_status[status] = by_status.get(status, 0) + 1
    return {"available": True, "total": len(tasks), "by_status": by_status,
            "complex": sum(1 for t in tasks if t["complex"]),
            "with_dod": sum(1 for t in tasks if t["dod"]),
            "with_goal": sum(1 for t in tasks if t["goal"])}


def plan_and_goals(root: Path) -> dict:
    """Plan status, and goal-based completion ONLY when a plan with goals exists.

    Delegates to `goal_progress.progress()` rather than recomputing — the one
    place this module could quietly drift from the number `just goals` prints.
    """
    try:
        import plan_workspace
    except Exception as exc:  # noqa: BLE001
        return {"plan_exists": False, "reason": f"plan_workspace.py unavailable ({exc})"}
    plan = plan_workspace.validate(root=root)
    out = {"plan_exists": plan["exists"], "status": plan["status"],
          "agreed": plan["agreed"], "goal_count": len(plan["goals"]),
          "goals": plan["goals"], "progress": None}
    if plan["exists"] and plan["goals"]:
        try:
            import goal_progress
            out["progress"] = goal_progress.progress(root=root)
        except Exception as exc:  # noqa: BLE001
            out["progress"] = {"ok": False, "reason": f"goal_progress.py unavailable ({exc})"}
    return out


def audit(root: Path | None = None) -> dict:
    """The full audit. Pure read — nothing here writes to any file.

    Kept side-effect free for the same reason `goal_progress.progress()` and
    `plan_workspace.validate()` are: PH7-T09 is this repo's standing lesson
    about a read path that turns out to write.
    """
    root = root or ws_root()
    return {"root": str(root), "name": root.name,
            "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "git": git_stats(root), "files": file_stats(root),
            "ledger": ledger_stats(root), "plan": plan_and_goals(root)}


def standing_context(v: dict) -> str:
    """A paste-ready paragraph for a plan's "What I want" section.

    Same voice as the hand-written one in this workspace's own `.ai/plan.md`
    ("Standing context (from the workspace's history, not from this
    conversation)") — formalising a convention that previously existed in
    exactly one place.
    """
    g, f, l = v["git"], v["files"], v["ledger"]
    bits = [f"**Standing context (from the workspace's history, not from this "
            f"conversation):** `{v['name']}`"]
    if g["available"]:
        idle = f", idle {g['idle_days']:.0f}d" if g["idle_days"] is not None else ""
        bits.append(f" has {g['commits']} commit(s) since {g['first_commit'] or '?'}"
                    f"{idle}{' (uncommitted changes present)' if g['dirty'] else ''}.")
    else:
        bits.append(f" is not a git repository ({g['reason']}).")
    langs = ", ".join(f"{n} {ext}" for ext, n in f["top_languages"][:3])
    bits.append(f" Dominant file types: {langs or 'none found'}."
               f" {f['test_files']} test file(s) found, README "
               f"{'present' if f['readme'] else 'absent'}.")
    if l["available"] and l["total"]:
        status_bits = ", ".join(f"{n} {s}" for s, n in sorted(l["by_status"].items()))
        bits.append(f" Ledger declares {l['total']} task(s) ({status_bits}).")
    if v["plan"]["plan_exists"]:
        bits.append(f" A `.ai/plan.md` already exists (status: {v['plan']['status']}).")
    else:
        bits.append(" No `.ai/plan.md` yet.")
    return "".join(bits)


def render(v: dict, standing_only: bool = False) -> None:
    if standing_only:
        print(standing_context(v))
        return

    print(f"  🔎 WORKSPACE AUDIT — {v['name']}")
    print(f"     {v['root']}")

    g = v["git"]
    if g["available"]:
        idle = f"{g['idle_days']:.0f}d ago" if g["idle_days"] is not None else "unknown"
        dirty = f", {g['dirty_count']} uncommitted change(s)" if g["dirty"] else ", clean tree"
        print(f"\n  📜 Git: {g['commits']} commit(s), first {g['first_commit'] or '?'}, "
              f"last active {idle}{dirty}")
    else:
        print(f"\n  📜 Git: unavailable — {g['reason']}")

    f = v["files"]
    langs = ", ".join(f"{ext}×{n}" for ext, n in f["top_languages"][:5])
    cap_note = " (walk capped — large tree)" if f["capped"] else ""
    print(f"\n  📁 Structure: {langs or 'no source files found'}{cap_note}")
    print(f"     README: {'✅ ' + f['readme'] if f['readme'] else '❌ absent'}  ·  "
          f"tests: {f['test_files']} file(s)  ·  "
          f"docs/: {'✅' if f['docs_dir'] else '❌'}  ·  "
          f".ai/docs/: {len(f['ai_docs'])} file(s)")

    l = v["ledger"]
    if l["available"] and l["total"]:
        print(f"\n  📋 Ledger: {l['total']} task(s) declared — "
              + ", ".join(f"{n} {s}" for s, n in sorted(l["by_status"].items())))
        print(f"     {l['with_dod']} with DoD · {l['with_goal']} tagged with a goal "
              f"· {l['complex']} marked [complex]")
    elif l["available"]:
        print("\n  📋 Ledger: no tasks declared")
    else:
        print(f"\n  📋 Ledger: {l['reason']}")

    p = v["plan"]
    if not p["plan_exists"]:
        print("\n  🎯 Plan: none yet — nothing to compute a goal percentage against.")
    elif not p["goal_count"]:
        print(f"\n  🎯 Plan: {p['status']}, 0 goals declared — nothing to compute yet.")
    elif p["progress"] and p["progress"].get("ok"):
        print(f"\n  🎯 Plan: {p['status']}, {p['goal_count']} goal(s) — "
              f"{p['progress']['basis']}")
        for goal in p["progress"]["goals"]:
            if goal["total"]:
                print(f"       {goal['id']} — {goal['percent']}% "
                      f"({goal['done']}/{goal['total']}) {goal['title'][:60]}")
    else:
        reason = (p["progress"] or {}).get("reason", "unavailable")
        print(f"\n  🎯 Plan: {p['status']}, {p['goal_count']} goal(s) — {reason}")

    print("\n  ── Paste-ready for a plan's \"What I want\" section: ──")
    print("  " + standing_context(v))


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Audit a workspace's actual state — structural standing plus "
                    "goal-based completion where a plan already exists.")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--standing", action="store_true",
                    help="print only the paste-ready standing-context paragraph.")
    args = ap.parse_args()

    v = audit(ws_root())
    if args.json:
        print(json.dumps(v, indent=2))
    else:
        render(v, standing_only=args.standing)
    return 0


if __name__ == "__main__":
    sys.exit(main())
