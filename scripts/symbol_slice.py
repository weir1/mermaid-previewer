#!/usr/bin/env python3
"""symbol_slice.py — inspect one symbol without paying for the whole file (PH25-T01).

## Why

Several of this repo's own scripts run past 500 lines, and today the only way to see one
function's signature or body is a whole-file `Read` — the cost is worst exactly when context
is tightest, late in a session, which this OS's own memory bank has repeatedly recorded as
when it makes its worst decisions. This returns just the symbol asked for.

## Why `ast`, not a name grep

`scripts/test_impact.py`'s `DATA_TESTS` table refuses filename/pattern matching for the same
reason this refuses regex over `def `/`class ` lines: a name can appear in a string, a
comment, or a nested scope with the same simple name as an unrelated top-level one. `ast`
already tracks scope, indentation and exact source positions correctly — reimplementing that
with text scanning would be strictly worse and still wrong in the same repo it was meant to
help read.

## Exact line numbers, honestly

`node.end_lineno` (Python's own AST, no re-parsing) gives the end of the definition
including its body. `start_line` includes the first decorator, because a decorator is part of
what a reader — or a precision edit — needs to see. Both are 1-indexed to match every editor
and this repo's own line-numbered `Read` output.

## The signature is reconstructed, not sliced

`body` is the literal source text for the symbol's line range — byte-identical to the file.
`signature` is rebuilt via `ast.unparse` on a shallow copy of the node with its body swapped
for a single `pass`, then that synthetic last line dropped. This is always syntactically valid
regardless of the original formatting (multi-line parameter lists, inline type hints) — the
alternative, finding where the header's own colon ends by scanning tokens, has to solve
"which colon" for `def f(x: int) -> dict[str, int]:`, which already contains three unrelated
colons before the one that matters. Unparsing a `pass`-bodied copy sidesteps the question
entirely, at the honestly-stated cost of not being byte-identical to the original formatting.

## Ambiguity is refused, not guessed

Two different classes can each define a same-named method. Returning the first one found
would silently hand back the wrong body — worse than the whole-file read this tool exists to
avoid, because it looks authoritative. `slice_symbol` refuses and lists every dotted
`qualname` candidate so the caller can re-ask with e.g. `Outer.run`.
"""

from __future__ import annotations

import argparse
import ast
import copy
import json
from pathlib import Path


def _default_root() -> Path:
    """The AGENTS.md anti-drift rule — same resolver every other script uses:
    the directory containing this repo's own AGENTS.md, not the caller's cwd."""
    here = Path(__file__).resolve().parent
    for candidate in (here, *here.parents):
        if (candidate / "AGENTS.md").is_file():
            return candidate
    return Path.cwd()


def _resolve_path(path: str | Path, root: Path | str | None) -> Path:
    p = Path(path)
    if p.is_absolute():
        return p
    base = Path(root) if root is not None else _default_root()
    return base / p


_DEF_TYPES = (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _candidates(tree: ast.Module, symbol: str) -> list[tuple[str, ast.AST]]:
    """Every (dotted qualname, node) in the tree whose simple name OR dotted
    qualname matches `symbol` — walked so a nested class/function builds its
    qualname from every enclosing class/function, not only its immediate one."""
    hits: list[tuple[str, ast.AST]] = []

    def walk(node: ast.AST, prefix: str) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, _DEF_TYPES):
                qualname = f"{prefix}.{child.name}" if prefix else child.name
                if child.name == symbol or qualname == symbol:
                    hits.append((qualname, child))
                walk(child, qualname)
            else:
                walk(child, prefix)

    walk(tree, "")
    return hits


def _signature(node: ast.AST) -> str:
    """Decorators + header, no body — via `ast.unparse` so it is always valid
    regardless of the source's original formatting. See the module docstring's
    'The signature is reconstructed, not sliced'."""
    shell = copy.copy(node)
    shell.body = [ast.Pass()]
    lines = ast.unparse(shell).splitlines()
    return "\n".join(lines[:-1])  # drop the synthetic "    pass" line


def slice_symbol(path: str | Path, symbol: str, root: Path | str | None = None) -> dict:
    """Return `symbol`'s signature, docstring, body and exact line numbers
    from `path`, or `{"ok": False, "reason": ...}` — never a guess."""
    fp = _resolve_path(path, root)
    if not fp.is_file():
        return {"ok": False, "reason": f"{path} is not a file"}

    source = fp.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source, filename=str(fp))
    except SyntaxError as e:
        return {"ok": False, "reason": f"{path} does not parse: {e}"}

    hits = _candidates(tree, symbol)
    if not hits:
        return {"ok": False, "reason": f"no function or class named {symbol!r} in {path}"}

    exact = [h for h in hits if h[0] == symbol]
    if not exact and len(hits) > 1:
        names = sorted(q for q, _ in hits)
        return {"ok": False, "candidates": names,
                "reason": f"{symbol!r} is ambiguous in {path} — {len(hits)} matches: "
                          f"{', '.join(names)}. Use the dotted form, e.g. {names[0]!r}."}
    qualname, node = exact[0] if exact else hits[0]

    lines = source.splitlines()
    start = node.decorator_list[0].lineno if getattr(node, "decorator_list", None) else node.lineno
    end = node.end_lineno
    body = "\n".join(lines[start - 1:end])

    return {
        "ok": True,
        "file": str(path),
        "symbol": symbol,
        "qualname": qualname,
        "kind": type(node).__name__,
        "start_line": start,
        "end_line": end,
        "signature": _signature(node),
        "docstring": ast.get_docstring(node),
        "body": body,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("path")
    ap.add_argument("symbol")
    ap.add_argument("--root", default=None)
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args(argv)

    out = slice_symbol(args.path, args.symbol, root=args.root)

    if args.json:
        print(json.dumps(out, indent=2))
        return 0 if out["ok"] else 1

    if not out["ok"]:
        print(f"  ❌ {out['reason']}")
        return 1

    print(f"{out['file']}:{out['start_line']}-{out['end_line']}  "
          f"{out['qualname']} ({out['kind']})")
    print()
    print("Signature:")
    print(out["signature"])
    if out["docstring"]:
        print()
        print("Docstring:")
        print(out["docstring"])
    print()
    print(f"Body (lines {out['start_line']}-{out['end_line']}):")
    print(out["body"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
