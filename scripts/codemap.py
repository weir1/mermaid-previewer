#!/usr/bin/env python3
"""codemap.py — the INDEX retrieval trick, applied to source (PH7-T07).

## Why this exists

`AGENTS.md` § CONTEXT BUDGET says: *"RETRIEVAL-FIRST — read `.ai/memory-bank/INDEX.md`
first … Do NOT grep or scan the repo to rediscover where something lives — that
search is the context waste the index exists to kill."*

That rule is enforced for **memory** by a real index whose links `just doctor`
validates. For **source** there was nothing. `scripts/` holds 45 modules, and the
only way to learn what `upgrade_merge.py` does — or which of the four `session_*`
scripts owns the hook contract — is to open them. Every session pays that cost
from scratch.

It is also PHASE 10's other half. A delegation contract constrains an executor to
a named set of files. PH7-T05 gave the contract its *failing test*; the file
allowlist is the rest, and picking one from memory means guessing it.

## Derived, never authored

Every field comes from the source: the purpose is the first sentence of the module
docstring (Python) or of the leading comment block (anything else), the entry
point comes from the file's own structure, and the recipe comes from the justfile.
A module whose docstring is wrong yields a visibly wrong row — a better failure
than a hand-written map that is confidently out of date. A module with **no**
docstring yields a blank, never an invented purpose.

The `just` recipe column has no analogue in the memory index and is the highest-
value field here: "what runs this" is precisely what you otherwise learn by
grepping the justfile.

## Staleness is a content hash, not an mtime

The task's original wording was "older than the newest source file it maps".
Implemented with mtimes that check **cannot tell the truth**: `git clone` and
`git checkout` set every mtime to checkout time, so it fires on a fresh clone
where nothing changed and goes quiet after a `touch` where everything did. It
also cannot see a *deleted* file at all.

So each row carries a short content hash, and `check()` compares hashes and names
what drifted in all three directions — `changed` · `added` · `removed`. Strictly
stronger than the mtime comparison, and a deliberate substitution recorded in
`.ai/plans/PH7-T07.md`.

## Absent is a WARN; present-but-stale is a FAIL

Exactly the asymmetry PH7-T05 set for the test runner, and for the same reason:
38 workspaces have no codemap, and failing `doctor` for its absence would turn
every workspace red the day this deploys — a red doctor everywhere is a red doctor
nowhere. Having one is opting in; a stale one is a real defect.

`check()` is a **pure read and never writes**. A check that repairs what it finds
stops reporting the truth about the workspace.

Usage:
    python3 scripts/codemap.py --write     # regenerate .ai/codemap.md
    python3 scripts/codemap.py --check     # exit 1 if it no longer matches the tree
    python3 scripts/codemap.py --json
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

MAP_PATH = ".ai/codemap.md"

# Where source lives, in the order a reader wants it. `scripts/` first because in
# a governed workspace that is the OS itself.
SOURCE_DIRS = ("scripts", "src", "lib", "app")
SOURCE_SUFFIXES = (".py", ".sh", ".js", ".ts", ".jsx", ".tsx")

# The same exclusions the test runner and the minimal pre-commit config carry —
# at least one fleet workspace has a committed node_modules/.
SKIP_DIRS = {"__pycache__", "node_modules", ".venv", "venv", "dist", "build",
             "vendor", ".git", "archive"}

# One row must stay one readable line. A map that spills each module's opening
# essay costs more context than it saves, which would make it a net loss against
# the budget it exists to serve.
MAX_PURPOSE = 160

HASH_LEN = 12

_RECIPE_RE = re.compile(r"^([a-z][a-z0-9-]*)\s+[^:\n]*:|^([a-z][a-z0-9-]*):", re.M)
_SENTENCE_END = re.compile(r"(?<=[\w`)\"'])\.(?:\s|$)")


def _ws_root() -> Path:
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


# ── Reading the tree ───────────────────────────────────────────────────────

def _source_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for d in SOURCE_DIRS:
        base = root / d
        if not base.is_dir():
            continue
        for p in base.rglob("*"):
            if not p.is_file() or p.suffix not in SOURCE_SUFFIXES:
                continue
            if SKIP_DIRS & set(p.relative_to(root).parts):
                continue
            out.append(p)
    return sorted(out, key=lambda p: str(p.relative_to(root)))


def _first_sentence(text: str) -> str:
    """The opening sentence of a docstring, collapsed to one line.

    Real modules here open with an essay: a title line, a blank, then `##`
    headings and tables. The title line alone is the useful part.
    """
    text = (text or "").strip()
    if not text:
        return ""
    head = text.split("\n\n", 1)[0].strip()
    head = " ".join(head.split())
    m = _SENTENCE_END.search(head)
    if m:
        head = head[:m.end()].strip()
    if len(head) > MAX_PURPOSE:
        head = head[:MAX_PURPOSE - 1].rstrip() + "…"
    return head


def _python_facts(path: Path) -> tuple[str, bool]:
    """(purpose, is_entry_point) — parsed, never imported.

    Importing a module to read its docstring runs its import-time code: executing
    code to answer a question *about* it, and one exploding module would take the
    whole map with it. Same law as `run_tests.discover()`.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return "", False
    purpose = _first_sentence(ast.get_docstring(tree) or "")
    entry = any(
        isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == "main"
        for n in tree.body)
    if not entry:
        entry = "__main__" in path.read_text(encoding="utf-8", errors="replace")
    return purpose, entry


def _comment_facts(path: Path) -> tuple[str, bool]:
    """Leading `#` / `//` comment block of a non-Python file."""
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    collected: list[str] = []
    for line in lines:
        s = line.strip()
        if s.startswith("#!") or not s and not collected:
            continue
        if s.startswith("#") or s.startswith("//"):
            collected.append(s.lstrip("#/").strip())
            continue
        break
    return _first_sentence(" ".join(collected)), path.suffix == ".sh"


def _recipes(root: Path) -> dict[str, str]:
    """script path → the `just` recipe that runs it.

    Read from real recipe *bodies*: a script named only in a usage comment is
    documented, not runnable, and claiming otherwise would be the mention-vs-
    declaration mistake this repo keeps finding.
    """
    justfile = root / "justfile"
    if not justfile.is_file():
        return {}
    mapping: dict[str, str] = {}
    current = ""
    for line in justfile.read_text(encoding="utf-8", errors="replace").splitlines():
        if line[:1] not in (" ", "\t") and line.strip() and not line.lstrip().startswith("#"):
            # `set positional-arguments := true` is an assignment, not a recipe,
            # and matches the recipe pattern — it would make the next indented
            # line look like the body of a recipe called `set`.
            if ":=" in line:
                current = ""
                continue
            m = _RECIPE_RE.match(line)
            current = (m.group(1) or m.group(2)) if m else ""
            continue
        if not current or line.lstrip().startswith("#"):
            continue
        for hit in re.findall(r"(?:scripts|src|lib|app)/[\w./-]+", line):
            mapping.setdefault(hit, f"just {current}")
    return mapping


def _digest(path: Path) -> str:
    """Hash of the file's normalised content — trailing whitespace stripped, so
    a pre-commit whitespace fix does not read as a change of substance."""
    body = path.read_text(encoding="utf-8", errors="replace")
    body = "\n".join(line.rstrip() for line in body.splitlines())
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:HASH_LEN]


def scan(root: Path | str = ".") -> dict:
    """Every mapped module, as rows. Pure read; imports nothing."""
    root = Path(root)
    recipes = _recipes(root)
    rows = []
    for p in _source_files(root):
        rel = str(p.relative_to(root))
        purpose, entry = _python_facts(p) if p.suffix == ".py" else _comment_facts(p)
        rows.append({
            "path": rel,
            "purpose": purpose,
            "entry": entry,
            "recipe": recipes.get(rel, ""),
            "hash": _digest(p),
        })
    return {"modules": rows, "count": len(rows)}


# ── Rendering ──────────────────────────────────────────────────────────────

def render(data: dict) -> str:
    """The map. No timestamp and no volatile counts — the file must be a pure
    function of the source, or `--check` becomes a diff nobody trusts."""
    lines = [
        "# Code map — where things live",
        "",
        "> **Generated by `just codemap`. Do not hand-edit.** Every field is derived from the",
        "> source: purpose is the module's own opening sentence, `run` is the `just` recipe whose",
        "> body invokes it. `just doctor` fails when this no longer matches the tree.",
        "",
        "Read this instead of grepping — that search is the context waste it exists to kill",
        "(`AGENTS.md` § CONTEXT BUDGET). Its memory-side twin is `.ai/memory-bank/INDEX.md`.",
        "",
        "| module | purpose | run | ⚙ | hash |",
        "|---|---|---|---|---|",
    ]
    for r in data["modules"]:
        purpose = r["purpose"].replace("|", "\\|") or "_(no docstring)_"
        recipe = f"`{r['recipe']}`" if r["recipe"] else "—"
        lines.append(f"| `{r['path']}` | {purpose} | {recipe} | "
                     f"{'✔' if r['entry'] else ''} | `{r['hash']}` |")
    lines += ["", f"{data['count']} module(s) mapped.", ""]
    return "\n".join(lines)


def write(root: Path | str = ".") -> Path:
    root = Path(root)
    path = root / MAP_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render(scan(root)), encoding="utf-8")
    return path


# ── Checking ───────────────────────────────────────────────────────────────

_ROW_RE = re.compile(r"^\|\s*`([^`]+)`\s*\|.*\|\s*`([0-9a-f]{6,})`\s*\|\s*$", re.M)


def recorded(root: Path | str = ".") -> dict[str, str]:
    """path → hash, as the committed map claims them."""
    path = Path(root) / MAP_PATH
    if not path.is_file():
        return {}
    return dict(_ROW_RE.findall(path.read_text(encoding="utf-8", errors="replace")))


def check(root: Path | str = ".") -> dict:
    """Does the map still match the tree? Pure read — never writes, never repairs."""
    root = Path(root)
    exists = (root / MAP_PATH).is_file()
    if not exists:
        # Absent is not stale. The whole WARN/FAIL asymmetry rests on this line.
        return {"exists": False, "stale": False, "fresh": False,
                "changed": [], "added": [], "removed": [],
                "reason": f"{MAP_PATH} does not exist — run `just codemap`"}

    was = recorded(root)
    now = {r["path"]: r["hash"] for r in scan(root)["modules"]}
    changed = sorted(p for p in was.keys() & now.keys() if was[p] != now[p])
    added = sorted(now.keys() - was.keys())
    removed = sorted(was.keys() - now.keys())
    stale = bool(changed or added or removed)

    bits = []
    if changed:
        bits.append(f"changed: {', '.join(changed[:4])}"
                    + (f" (+{len(changed) - 4} more)" if len(changed) > 4 else ""))
    if added:
        bits.append(f"added: {', '.join(added[:4])}"
                    + (f" (+{len(added) - 4} more)" if len(added) > 4 else ""))
    if removed:
        bits.append(f"removed: {', '.join(removed[:4])}"
                    + (f" (+{len(removed) - 4} more)" if len(removed) > 4 else ""))

    return {"exists": True, "stale": stale, "fresh": not stale,
            "changed": changed, "added": added, "removed": removed,
            "reason": " · ".join(bits) if stale else f"{MAP_PATH} matches the source tree"}


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Generate or verify .ai/codemap.md.")
    ap.add_argument("--write", action="store_true", help="regenerate the map")
    ap.add_argument("--check", action="store_true",
                    help="verify only; exit 1 when the map no longer matches (writes nothing)")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--root", default=None, help="workspace root (default: git toplevel)")
    args = ap.parse_args(argv)

    root = Path(args.root) if args.root else _ws_root()

    if args.check:
        c = check(root)
        if args.json:
            print(json.dumps(c, indent=2))
        elif not c["exists"]:
            print(f"⚠️  {c['reason']}")
        elif c["stale"]:
            print(f"❌ {MAP_PATH} is out of date — {c['reason']}")
            print("   Run `just codemap` and commit the result.")
        else:
            print(f"✅ {c['reason']}")
        return 1 if c["stale"] else 0

    if args.write or not args.json:
        path = write(root)
        data = scan(root)
        if args.json:
            print(json.dumps(data, indent=2))
        else:
            print(f"✅ {path.relative_to(root)} — {data['count']} module(s) mapped")
        return 0

    print(json.dumps(scan(root), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
