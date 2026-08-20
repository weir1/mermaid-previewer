#!/usr/bin/env python3
"""
ladder.py — this workspace's own North-Star ladder self-report (PH16-T04,
Goal G9).

Origin — this task's own measurement, done by hand 2026-08-08: kernel 58
commits/30d, `@jobscraper` 121, `@zenithcore` 7, `@website` **0** (last
commit 2026-05-27), `@portfolio` not even a git repo — ~96% of activity in
two workspaces while the two the plan says produce leads and proof are dead.
Nobody would know that without re-running `git log` by hand in each of ~40
directories. This is the fact-gathering half; `fleet_ladder.py` is the
kernel-only half that buckets these reports by stage.

## Deployed fleet-wide, deliberately self-contained

Every workspace answers for itself (design law 1): this script needs only
`.ai/plan.md` (via `plan_workspace.ladder_stage()`) and its own git history.
It does NOT read `.ai/north-star.yaml` — that file is kernel-only
(PH11-T02) and never deployed, so a workspace has no local copy of the
ladder to check its own stage number against. Whether stage 7 is a stage
that exists, or a stage that has not started, is `fleet_ladder.py`'s job,
run from the kernel where both facts are available.

## "commits or hours" — commits only, and that is stated, not hidden

Nothing in this OS tracks hours yet (PH12's cost ledger is where that would
live). Commit count is a real, if partial, proxy — the same one this task's
own origin measurement used — and every render says so rather than
implying a fuller number exists.

Usage:
  ladder.py                  # this workspace's own report, last 30 days
  ladder.py --days 14
  ladder.py --json
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

DEFAULT_DAYS = 30


def ws_root() -> Path:
    try:
        t = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                    stderr=subprocess.DEVNULL, text=True).strip()
        if t:
            return Path(t)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def commits_since(root: Path, days: int) -> tuple[int | None, str]:
    """Commit count on the checked-out branch over the last `days` days, or
    `(None, reason)` when this is not a usable git repo — never a guessed 0,
    which would be indistinguishable from a real, measured zero-activity
    period (the same "undeclared is not 0" rule PH11-T02/`fleet_ladder.py`
    apply to the ladder itself)."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
    try:
        out = subprocess.check_output(
            ["git", "log", f"--since={since}", "--oneline"],
            cwd=root, stderr=subprocess.DEVNULL, text=True)
    except subprocess.CalledProcessError:
        return None, "not a git repository (or no commits yet)"
    except Exception as exc:  # noqa: BLE001
        return None, f"git log failed: {exc}"
    lines = [l for l in out.splitlines() if l.strip()]
    return len(lines), ""


def own_report(root: Path | None = None, days: int = DEFAULT_DAYS) -> dict:
    root = root or ws_root()
    import plan_workspace
    stage = plan_workspace.ladder_stage(root)
    n_commits, reason = commits_since(root, days)
    return {
        "declared_stage": stage,
        "days": days,
        "commits": n_commits,
        "commits_reason": reason,
        "basis": "commits only — hours are not tracked anywhere in this OS yet",
    }


# ────────────────────────────────── rendering ────────────────────────────────

def render(report: dict) -> str:
    stage = report["declared_stage"]
    stage_s = f"stage {stage}" if stage is not None else "undeclared"
    if report["commits"] is None:
        commits_s = f"commits unknown ({report['commits_reason']})"
    else:
        commits_s = f"{report['commits']} commit(s) in the last {report['days']}d"
    lines = [f"🪜 LADDER SELF-REPORT — declared: {stage_s} · {commits_s}"]
    if stage is None:
        lines.append("   ➤ Not declared. Add `ladder_stage: <n>` to .ai/plan.md's frontmatter.")
    return "\n".join(lines)


# ────────────────────────────────────── CLI ──────────────────────────────────

def main() -> int:
    ap = argparse.ArgumentParser(description="This workspace's North-Star ladder self-report (PH16-T04).")
    ap.add_argument("--days", type=int, default=DEFAULT_DAYS)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    report = own_report(ws_root(), args.days)
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print(render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
