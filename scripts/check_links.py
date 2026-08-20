#!/usr/bin/env python3
"""
check_links.py — validate the memory-bank retrieval graph.

The context-economy design (v3.4/v3.5) has the AI navigate memory via an INDEX +
`[[file#anchor]]` cross-links instead of grepping the repo. An unvalidated link is
worse than none — it sends the AI to a dead end — so this module resolves every
`[[...]]` link and reports danglers. Used by `just doctor` and tests/test_links.py.

Link syntax scanned:
    [[name]]                       → bare name, resolved by filename (same directory
                                      as the linking file first, then the workspace
                                      root, then anywhere under it — memory-bank and
                                      docs/ cross-link by bare name and neither lives
                                      in the other's directory)
    [[path/to/file]]                → path relative to the linking file's own
                                      directory, falling back to workspace-root-relative
    [[path/to/file.md#anchor]]     → file must exist AND contain that heading
    [[#anchor]]                    → anchor must exist in the SAME file
    [[target|label]]               → the `|label` part is a display alias, ignored
                                      for resolution

AGENTS.md and tasks.md both *document* this syntax by writing `[[file#anchor]]`
inside backticks — a naive checker reports those as dangling links, fails the
health check on its own docs, and gets switched off. So fenced code blocks AND
inline code spans (single or double backtick, including nested single backticks
inside a double-backtick span) are masked before scanning, character-for-character,
so line numbers in error messages stay accurate.
"""

import re
import subprocess
import sys
from difflib import get_close_matches
from pathlib import Path

LINK_RE = re.compile(r"\[\[([^\]]+)\]\]")
EXPLICIT_ID_RE = re.compile(r"\{#([a-zA-Z0-9_-]+)\}\s*$")


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:
        pass
    return Path(".").resolve()


def slugify(heading: str) -> str:
    """GitHub-style heading → anchor slug (good enough for ASCII headings)."""
    h = heading.strip().lstrip("#").strip().lower()
    # Markdown emphasis (`_word_`, `__word__`) must not survive into the slug — GitHub's
    # own algorithm strips formatting before slugifying. `*`/`**` emphasis is already
    # gone once punctuation is dropped below, but `_` is a \w character and would
    # otherwise survive. Only underscores at a word boundary count as emphasis, so
    # `check_links` (no boundary on either side) keeps its underscore.
    h = re.sub(r"(?<!\w)_{1,2}(\S+?)_{1,2}(?!\w)", r"\1", h)
    h = re.sub(r"[^\w\s-]", "", h)     # drop punctuation/emoji, keep word/space/-
    h = h.strip().replace(" ", "-")
    return re.sub(r"-+", "-", h)


def _mask_code_spans(line: str) -> str:
    """Blank backtick-delimited code spans in place (same length, so column/line
    positions of anything AFTER the span are unaffected).

    Handles double-backtick spans (`` `x` `` — lets a literal single backtick live
    inside as plain content) by pairing backtick RUNS of equal length, CommonMark-
    style, rather than matching single backticks greedily. An unclosed run (no
    matching close of the same length) is left as literal text rather than
    swallowing the rest of the line.
    """
    runs = []
    i, n = 0, len(line)
    while i < n:
        if line[i] == "`":
            j = i
            while j < n and line[j] == "`":
                j += 1
            runs.append((i, j))
            i = j
        else:
            i += 1

    out = list(line)
    k = 0
    while k < len(runs):
        start_a, end_a = runs[k]
        len_a = end_a - start_a
        match = None
        for m in range(k + 1, len(runs)):
            if (runs[m][1] - runs[m][0]) == len_a:
                match = m
                break
        if match is None:
            k += 1
            continue
        _, end_b = runs[match]
        for idx in range(start_a, end_b):
            out[idx] = " "
        k = match + 1
    return "".join(out)


def _masked_lines(text: str) -> list:
    """`text`'s lines with fenced blocks fully blanked and inline code spans masked —
    same line count as the original, so 1-indexed line numbers stay accurate.
    """
    out = []
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        if in_fence:
            out.append("")
            continue
        out.append(_mask_code_spans(line))
    return out


def anchors_of(path: Path) -> set:
    """Every heading anchor a file offers: the slugified title, plus any explicit
    `{#custom-id}` GitHub-style override. Headings inside fenced code blocks don't
    count — a `#` there is a shell comment, not a heading.
    """
    try:
        text = path.read_text()
    except Exception:
        return set()
    anchors = set()
    in_fence = False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if not line.lstrip().startswith("#"):
            continue
        m = EXPLICIT_ID_RE.search(line)
        if m:
            anchors.add(m.group(1).lower())
            line = EXPLICIT_ID_RE.sub("", line)
        anchors.add(slugify(line))
    return anchors


def _candidates(target: str) -> list:
    """`doc/config` and `doc/config.md` both must resolve — try the target as given,
    and with `.md` appended when it has no extension of its own."""
    return [target] if Path(target).suffix else [target, target + ".md"]


def _resolve_target(root: Path, rel_dir: Path, target: str):
    """Resolve a [[target]] to an existing file, or None.

    Search order: relative to the LINKING file's own directory (`archive/x` written
    inside `.ai/memory-bank/knownIssues.md` means `.ai/memory-bank/archive/x.md`),
    then relative to the workspace root (`doc/architecture.md`), then — bare names
    only, no "/" — anywhere under the workspace root by filename (`[[tasks]]`
    written in memory-bank/ means `.ai/docs/tasks.md`; neither directory contains
    the other, so a directory-relative search alone can never find it).
    """
    for cand in _candidates(target):
        p = (root / rel_dir / cand).resolve()
        if p.is_file():
            return p
    for cand in _candidates(target):
        p = (root / cand).resolve()
        if p.is_file():
            return p
    if "/" not in target:
        for cand in _candidates(target):
            matches = sorted(m for m in root.rglob(cand) if m.is_file())
            if matches:
                return matches[0]
    return None


def validate(root: Path, files) -> list:
    """Return a list of human-readable error strings for dangling links."""
    errors = []
    anchor_cache = {}

    def anchors_for(path: Path) -> set:
        if path not in anchor_cache:
            anchor_cache[path] = anchors_of(path)
        return anchor_cache[path]

    for f in files:
        f = Path(f)
        try:
            text = f.read_text()
        except Exception:
            continue
        rel = f.relative_to(root) if f.is_absolute() else f
        rel_dir = rel.parent

        for i, line in enumerate(_masked_lines(text), start=1):
            for m in LINK_RE.finditer(line):
                raw = m.group(1).strip()
                body, _, _label = raw.partition("|")
                target, _, anchor = body.partition("#")
                target = target.strip()
                anchor = anchor.strip() or None

                if target == "":
                    tpath = f.resolve()  # [[#anchor]] — same file
                else:
                    tpath = _resolve_target(root, rel_dir, target)
                    if tpath is None:
                        errors.append(f"{rel}:{i}: [[{raw}]] → no such file: {target}")
                        continue

                if not anchor:
                    continue
                anchors = anchors_for(tpath)
                slug = slugify(anchor)
                if slug in anchors:
                    continue
                suggestion = get_close_matches(slug, anchors, n=1)
                hint = f" — did you mean '{suggestion[0]}'?" if suggestion else ""
                where = tpath.relative_to(root) if root in tpath.parents or tpath == root else tpath
                errors.append(
                    f"{rel}:{i}: [[{raw}]] → '{anchor}' is not a heading in {where}{hint}"
                )
    return errors


def default_scan(root: Path) -> list:
    """Files whose links we validate: the memory bank + the canonical protocol."""
    files = sorted((root / ".ai" / "memory-bank").glob("*.md"))
    for extra in ("AGENTS.md", "CLAUDE.md"):
        p = root / extra
        if p.exists():
            files.append(p)
    return files


def main() -> int:
    root = ws_root()
    errors = validate(root, default_scan(root))
    if errors:
        print("❌ Dangling memory links:")
        for e in errors:
            print(f"   - {e}")
        return 1
    print("✅ All memory [[links]] resolve.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
