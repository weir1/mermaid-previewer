#!/usr/bin/env python3
"""`just guide-check` — does the usage guide still describe the OS that exists? (PH15-T02)

`doc/USER_GUIDE.md` is the one file written for a *human* rather than an agent.
`AGENTS.md` tells an AI how to behave; this tells the operator what he can do. When
it was written, 56 recipes existed, `README.md` documented 5, and 11 were described
nowhere outside the `justfile`.

A guide's real failure mode is not being wrong when written — it is being right then
and quietly false three months later. That is precisely what happened to the roadmap's
tick marks, which read as pending for three shipped features until a human re-read
them by hand on 2026-07-07. **A document nothing verifies is a TODO**, by this repo's
own guiding principle, so the guide gets a ratchet.

## What counts as documented

A recipe is documented when it **declares itself as the first cell of a table row**:

    | `just doctor` | health-check both entrypoints |

Prose saying "run `just doctor` if something feels off" is a *mention*, and a mention
is not a declaration — the same rule `plan.py` applies to `[complex]`,
`plan_workspace.py` to a goal, `task_ledger.py` to `(In Progress)` and `note_issue.py`
to a declared absent test. Sixth instance of the class, designed in rather than
patched after. Only the first cell declares: a "see also" column naming other commands
would otherwise document half the OS by accident.

## Both directions are drift

  * `undocumented` — a recipe with no entry. The reader cannot discover the feature.
  * `phantom` — an entry for a recipe that no longer exists. Worse, because it sends
    the reader to a command that errors, and the guide loses their trust wholesale.

## Absence is a prompt, not breakage

`doc/` is not deployed to child workspaces, so 38 of them have no guide. An absent
guide is reported as absent and **passes** — the WARN/FAIL asymmetry the codemap check
established, for the same reason: a check that is red in every workspace on deploy day
is a check everyone learns to click past.

Usage:
  guide_check.py            # the report
  guide_check.py --json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import ledger_audit as la  # noqa: E402
import task_ledger as tl  # noqa: E402

GUIDE = Path("doc") / "USER_GUIDE.md"

# A row's FIRST cell, and only if it is a code span invoking `just`.
_ROW = re.compile(r"^\|\s*`just\s+([a-z0-9][a-z0-9._-]*)[^`]*`\s*\|")


def _root() -> Path:
    """The workspace root, resolved the cwd-safe way every script here uses."""
    return tl.ws_root()


def documented(root: Path) -> set[str]:
    """Recipe names *declared* by a table row in the guide."""
    f = root / GUIDE
    if not f.is_file():
        return set()
    text = f.read_text(encoding="utf-8", errors="replace")
    return {m.group(1) for line in text.splitlines() if (m := _ROW.match(line))}


def check(root: Path | None = None) -> dict:
    """Pure read. `ok` is False only when the guide exists and disagrees with the OS."""
    root = Path(root) if root else _root()
    recipes = la._recipes(root)
    present = (root / GUIDE).is_file()
    if not present:
        # Nothing was omitted from a guide that does not exist. Reporting 56
        # omissions here would drown the one fact that matters: there is no guide.
        return {"ok": True, "guide_present": False, "undocumented": [], "phantom": [],
                "documented": 0, "total": len(recipes),
                "reason": f"no {GUIDE.as_posix()} — run `just guide-check` after writing one"}

    have = documented(root)
    undocumented = sorted(recipes - have)
    phantom = sorted(have - recipes)
    return {"ok": not (undocumented or phantom), "guide_present": True,
            "undocumented": undocumented, "phantom": phantom,
            "documented": len(have & recipes), "total": len(recipes),
            "reason": ""}


def render(r: dict) -> None:
    if not r["guide_present"]:
        print(f"⚠️  {r['reason']}")
        print(f"   {r['total']} recipe(s) exist and none of them is described for a human.")
        return

    if r["ok"]:
        print(f"✅ doc/USER_GUIDE.md documents all {r['total']} recipe(s).")
        return

    if r["undocumented"]:
        print(f"❌ {len(r['undocumented'])} recipe(s) have no entry in doc/USER_GUIDE.md —")
        print("   a feature nobody can discover is a feature nobody uses:")
        for name in r["undocumented"]:
            print(f"     · just {name}")
    if r["phantom"]:
        print(f"❌ {len(r['phantom'])} guide entr(y/ies) name a recipe that no longer exists —")
        print("   the guide would send a reader to a command that errors:")
        for name in r["phantom"]:
            print(f"     · just {name}")
    print(f"\n   {r['documented']}/{r['total']} documented. A row's FIRST cell declares a "
          f"recipe;\n   a mention in prose does not.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Does doc/USER_GUIDE.md still describe every recipe that exists?")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    r = check()
    print(json.dumps(r, indent=2)) if args.json else render(r)
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
