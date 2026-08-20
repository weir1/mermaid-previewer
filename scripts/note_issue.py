#!/usr/bin/env python3
"""
note_issue.py — the ONE writer of `.ai/memory-bank/knownIssues.md`.

Files issues, and resolves them — **and refuses to resolve one that names no
regression test** (PH7-T06).

## Why resolution lives here

Before this, an issue was filed as prose and "resolved" by editing the bullet to
say `✅ RESOLVED <date> by <task>`. Nothing anywhere asked whether the defect could
still happen. That is the same shape as every other defect this workspace keeps
finding: a claim nothing verifies. `evidence.json` cannot be hand-written; the
word `Resolved` could.

It is also PHASE 10's missing artefact. A delegation contract hands an executor a
*named failing test* and lets it claim the work only when that test goes green. A
workspace that never produces one has nothing to delegate. PH7-T05 gave every
workspace a runner; this makes finding a bug produce a test for it.

Resolution is a subcommand here rather than a new script because **one file has
one writer** — the rule `plan_workspace.add_goal()` follows for the plan and
`decision_log.append()` for the log. It also means the fleet inherits this by
upgrading a script it already has. (`archive_memory.py` moves whole `## Resolved`
sections and is deliberately untouched.)

## The four outcomes of a resolve, decided in this order

  | entry state                                   | verdict                    |
  |-----------------------------------------------|----------------------------|
  | `test:` names a ref the runner collects        | resolve — **verified**     |
  | `test:` is `(none yet)`, or the ref fails      | **REFUSED** (exit 3)       |
  | no `test:` line and filed before ENFORCED_FROM | resolve — **grandfathered**|
  | no `test:` line and filed on/after that date   | **REFUSED** — hand-edited  |

`--no-test "reason"` overrides a refusal, demands a written reason, and lands in
`.ai/decision-log/` — the no-silent-bypass rule the gate and `work-done` follow.
It is needed for real: several open entries are explicitly *notices, not defects*
("no code change is proposed" appears in their own text), and a regression test
for a notice is nonsense. The waiver makes that a recorded judgement rather than a
rule everyone quietly ignores.

**Grandfathering is a date, not a shrug.** ~11 open entries predate this feature
and resolve without friction; everything filed from `ENFORCED_FROM` carries the
field. The DoD's words: *grandfathered, not retro-flagged, so the check reports a
real gap rather than a wall of noise*. `--gap` prints that gap as a number.

## Two rules this must never break

1. **A refusal never writes.** A half-applied resolution would corrupt the record
   the operator actually reads. Every early return leaves the file byte-identical.
2. **Filing never depends on verification.** `note-issue` is how issues get
   reported at all, in 38 workspaces. The `test:` value is recorded *as given* at
   file time and only checked at resolve time, so a bug in the checking code can
   lose a resolution but never a report.

What this does **not** bind: that the named test is any good, or that it would
have caught the bug. A test asserting `True` satisfies every check here. That is
`test-first`'s job — this is the ratchet that makes skipping it visible, the same
limit `just plan` and `just self-review` state about themselves.

Usage:
  note_issue.py "Issue title" "Full description" [test-ref]     # file one
  note_issue.py resolve "<title substring>" --by PH7-T06 --test tests/x.py::C::test_y
  note_issue.py resolve "<title substring>" --by PH7-T06 --no-test "why none applies"
  note_issue.py --gap                                           # open issues with no test
  just note-issue "Title" "Description"  ·  just resolve-issue "match" "ref" "PH#-T##"
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Enforcement starts the day this shipped. Entries older than this predate the
# `test:` field entirely; refusing them would be retro-flagging ~11 issues nobody
# can now write a test for, which turns a real signal into noise everyone learns
# to click past.
ENFORCED_FROM = "2026-08-08"

# A declared absence. NOT the same as a missing line: "no test yet" is a
# statement that can be counted and refused, while a missing line is
# indistinguishable from a legacy entry. Mention vs declaration, again.
NO_TEST = "(none yet)"

TEST_FIELD_INDENT = "  "

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_TEST = 3      # refused: no verified regression test
EXIT_SELECT = 4       # refused: nothing matched, or too much did

_BULLET_RE = re.compile(r"^- (?P<icon>⚠️|✅)\s*(?P<rest>.*)$")
_TS_RE = re.compile(r"\[(?P<date>\d{4}-\d{2}-\d{2})[ ,](?P<time>\d{2}:\d{2})?")
_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")
_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)
_TEST_LINE_RE = re.compile(r"^\s*[-*]\s*test:\s*(?P<value>.*?)\s*$", re.I)


def _ws_root() -> Path:
    """Resolve workspace root cwd-safely (git toplevel; fallback to cwd)."""
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


ROOT = _ws_root()
ISSUES_FILE = ROOT / ".ai" / "memory-bank" / "knownIssues.md"


def issues_file(root: Path | str | None = None) -> Path:
    base = Path(root) if root else ROOT
    return base / ".ai" / "memory-bank" / "knownIssues.md"


def parse_frontmatter(text: str):
    m = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not m:
        return {}, text
    block = m.group(1)
    meta = {}
    for line in block.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, text[m.end():]


# ── Reading the record ─────────────────────────────────────────────────────

def parse_entries(text: str) -> list[dict]:
    """Every issue in the file, as blocks.

    An entry is a `- ⚠️`/`- ✅` line at column 0 plus every indented line under
    it — real entries carry multi-paragraph continuations, and the `test:` field
    is one of those lines. Anything else (headings, blanks, prose) ends the block.
    """
    lines = text.splitlines()
    entries: list[dict] = []
    i = 0
    while i < len(lines):
        m = _BULLET_RE.match(lines[i])
        if not m:
            i += 1
            continue
        start = i
        i += 1
        while i < len(lines) and lines[i][:1].isspace() and lines[i].strip():
            i += 1
        block = lines[start:i]
        entries.append(_entry(block, start, i))
    _mark_superseded(entries)
    return entries


def _mark_superseded(entries: list[dict]) -> None:
    """Recognise the file's *other* resolution convention, structurally.

    Two shapes exist in `knownIssues.md`, both hand-written before this module
    owned resolution:

      in-place  `- ✅ **RESOLVED 2026-08-05 (PH7-T11)** [2026-08-05 16:36 IST] **title**…`
      prefix    `- ✅ **RESOLVED 2026-08-07 by PH9-T16** — …`  ← then the original `- ⚠️` below

    Counting the prefix form's original as *open* would make `--gap` report
    already-fixed issues, which is the "number nobody measured" failure this
    repo keeps finding. The discriminator is not a guess: the in-place form
    always carries the original entry's own `[timestamp]` because it *is* that
    entry, and the prefix form never does because it is a separate note. Pair it
    with strict line adjacency (`end == start`) and the rule is structural.

    `_apply()` only ever writes the in-place form, so this ambiguity stops
    growing from here — this reads history, it does not sanction the shape.
    """
    for a, b in zip(entries, entries[1:]):
        if a["resolved"] and not a["own_ts"] and not b["resolved"] and a["end"] == b["start"]:
            b["resolved"] = True
            b["superseded"] = True


def _entry(block: list[str], start: int, end: int) -> dict:
    bullet = block[0]
    resolved = "✅" in bullet.split("]", 1)[0] or bullet.startswith("- ✅")

    ts = _TS_RE.search(bullet)
    own_ts = bool(ts)
    if ts:
        date, time = ts.group("date"), ts.group("time") or ""
    else:
        # Some resolved entries carry only `**RESOLVED 2026-08-07 …**`; a few
        # archived ones carry no date at all. `0000-00-00` sorts before
        # ENFORCED_FROM, so a dateless entry grandfathers — the safe default,
        # since this writer always stamps a real date.
        d = _DATE_RE.search(bullet)
        date, time = (d.group(1) if d else "0000-00-00"), ""

    title = ""
    for bold in _BOLD_RE.findall(bullet):
        candidate = bold.strip()
        if candidate.upper().startswith("RESOLVED"):
            title = title or candidate       # fallback only
            continue
        title = candidate
        break
    if not title:
        title = re.sub(r"\s+", " ", bullet[2:]).strip()[:80]

    field = None
    for line in block[1:]:
        m = _TEST_LINE_RE.match(line)
        if m:
            field = m.group("value")
            break

    ref = field if (field and field != NO_TEST and not field.startswith("(")) else None
    return {
        "start": start, "end": end, "raw": "\n".join(block), "bullet": bullet,
        "resolved": resolved, "superseded": False, "own_ts": own_ts,
        "date": date, "timestamp": f"{date} {time}".strip(),
        "title": title, "test_field": field, "test_ref": ref,
    }


def is_grandfathered(entry: dict) -> bool:
    """Filed before the field existed. Date compare is safe: ISO dates sort."""
    return entry["date"] < ENFORCED_FROM


def find_issue(entries: list[dict], match: str) -> dict:
    """Select one OPEN entry by a case-insensitive substring of its title.

    Never "closest match wins": >1 hit refuses and names every candidate, because
    resolving the wrong issue silently is worse than asking again.
    """
    needle = (match or "").strip().lower()
    if not needle:
        return {"ok": False, "reason": "no match text given", "candidates": []}

    hits = [e for e in entries if needle in e["title"].lower()]
    if not hits:
        hits = [e for e in entries if needle in e["raw"].lower()]
    if not hits:
        return {"ok": False, "reason": f"no issue matches {match!r}", "candidates": []}

    open_hits = [e for e in hits if not e["resolved"]]
    if not open_hits:
        return {"ok": False, "candidates": hits,
                "reason": f"{match!r} matches only already-resolved issue(s)"}
    if len(open_hits) > 1:
        return {"ok": False, "candidates": open_hits,
                "reason": (f"{match!r} matches {len(open_hits)} open issues — "
                           f"be more specific")}
    return {"ok": True, "entry": open_hits[0], "candidates": open_hits, "reason": ""}


def issues_without_tests(root: Path | str | None = None) -> list[dict]:
    """The real gap: open issues carrying no usable test reference."""
    path = issues_file(root)
    if not path.is_file():
        return []
    return [{"title": e["title"], "date": e["date"],
             "declared": e["test_field"], "grandfathered": is_grandfathered(e)}
            for e in parse_entries(path.read_text(encoding="utf-8"))
            if not e["resolved"] and not e["test_ref"]]


# ── Writing: filing ────────────────────────────────────────────────────────

def fold_description(description: str) -> str:
    """Render a description into the shape `parse_entries` calls ONE entry.

    PH16-T37. `parse_entries` defines an entry as the bullet plus every **indented,
    non-blank** line under it, so a block ends at the first blank line. This function
    wrote the description verbatim and then appended `- test:` after all of it — so a
    multi-paragraph description put the field *outside* the entry that owns it, and the
    entry read as having no `test:` line at all, which PH7-T06 treats as a hand-edited
    legacy entry and refuses to resolve. `@job` filed such a finding here on 2026-08-15
    and reddened this workspace's suite; `finding.py` writes both halves, so no sender
    could avoid it, and `just note-issue "t" "$(cat file)"` reaches it with no sender at
    all. Hence the fix lives with the reader, in the module that owns both.

    Blank separators are dropped and every continuation line is indented — which is
    exactly the repair that was applied by hand to `@job`'s entry. **Nothing is
    removed:** each non-blank line survives, in order, with its own relative
    indentation intact, so a quoted block keeps its shape. What is lost is the visual
    paragraph break, and that is the whole price.

    A description line that itself begins `- test:` is harmless: the real field is
    written last and `_entry` takes the last match, verified rather than assumed.
    """
    lines = [ln.rstrip() for ln in (description or "").replace("\r\n", "\n").split("\n")]
    kept = [ln for ln in lines if ln.strip()]
    if not kept:
        return ""
    return kept[0].strip() + "".join(
        f"\n{TEST_FIELD_INDENT}{ln}" for ln in kept[1:])


def log_issue(title: str, description: str, test: str | None = None,
              root: Path | str | None = None) -> None:
    """File an issue. Records `test:` as given — verifies nothing (rule 2)."""
    path = issues_file(root)
    now = datetime.now()
    date_str = now.strftime("%Y-%m-%d")
    time_str = now.strftime("%H:%M IST")

    if not path.exists():
        print(f"❌ {path} not found. Run `just session-start` first.")
        sys.exit(1)

    text = path.read_text(encoding="utf-8")
    meta, body = parse_frontmatter(text)

    ref = (test or "").strip() or NO_TEST
    new_entry = (f"\n- ⚠️ [{date_str} {time_str}] **{title}**: "
                 f"{fold_description(description)}"
                 f"\n{TEST_FIELD_INDENT}- test: {ref}")

    section_header = f"## Active Issues ({date_str})"
    if section_header in body:
        body = body.replace(section_header, section_header + new_entry)
    elif "## Active Issues" in body:
        body = body.replace("## Active Issues",
                            f"{section_header}{new_entry}\n\n## Active Issues", 1)
    else:
        body = body.rstrip() + f"\n\n{section_header}{new_entry}\n"

    if meta:
        frontmatter = "---\n" + "\n".join(f"{k}: {v}" for k, v in meta.items()) + "\n---\n"
        path.write_text(frontmatter + body, encoding="utf-8")
    else:
        path.write_text(body, encoding="utf-8")

    print(f"✅ Issue logged to {path}")
    print(f"   [{date_str} {time_str}] {title}: {description}")
    print(f"   test: {ref}")
    print()
    if ref == NO_TEST:
        print("⚠️  No regression test named. This issue cannot be marked Resolved")
        print("    until one is — write the failing test first (`.agents/skills/test-first`),")
        print("    then: just resolve-issue \"<title>\" \"tests/…::Cls::test_x\" \"PH#-T##\"")
    print("Next steps:")
    print("  • AI_CHANGELOG.md should also be updated")
    print("  • Run `just commit-all` to commit the issue log")


# ── Writing: resolution ────────────────────────────────────────────────────

def _log_decision(decision: str, root: Path, title: str, reason: str,
                  by: str = "") -> None:
    """Best effort — a logging failure must never flip a verdict (log rule 1)."""
    try:
        import decision_log
        decision_log.record("issue", decision, "note_issue", root=Path(root),
                            action=f"resolve issue: {title[:60]}",
                            reason=reason, task=by or None)
    except Exception:  # noqa: BLE001
        pass


def _apply(path: Path, entry: dict, by: str, field_value: str, today: str) -> None:
    """Rewrite the bullet as RESOLVED and set its `test:` line. Called only after
    a verdict is reached, so there is no partial-write path to unwind."""
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    block = lines[entry["start"]:entry["end"]]

    bullet = block[0]
    marker = f"**RESOLVED {today}" + (f" ({by})**" if by else "**")
    block[0] = re.sub(r"^- ⚠️\s*", f"- ✅ {marker} ", bullet, count=1)

    field_line = f"{TEST_FIELD_INDENT}- test: {field_value}"
    for i, line in enumerate(block[1:], start=1):
        if _TEST_LINE_RE.match(line):
            block[i] = field_line
            break
    else:
        block.insert(1, field_line)

    lines[entry["start"]:entry["end"]] = block
    path.write_text("\n".join(lines) + ("\n" if text.endswith("\n") else ""),
                    encoding="utf-8")


def resolve_issue(match: str, test: str | None = None, by: str = "",
                  waive: str | None = None,
                  root: Path | str | None = None) -> dict:
    """Mark one issue Resolved, or refuse. Returns {ok, code, verdict, reason}.

    A refusal leaves the file byte-identical — see rule 1 in the module docstring.
    """
    base = Path(root) if root else ROOT
    path = issues_file(base)
    out = {"ok": False, "code": EXIT_SELECT, "verdict": None,
           "reason": "", "title": "", "candidates": []}

    if not path.is_file():
        out["reason"] = f"{path} not found"
        return out

    entries = parse_entries(path.read_text(encoding="utf-8"))
    picked = find_issue(entries, match)
    out["candidates"] = [e["title"] for e in picked.get("candidates", [])]
    if not picked["ok"]:
        out["reason"] = picked["reason"]
        return out

    entry = picked["entry"]
    out["title"] = entry["title"]
    today = datetime.now().strftime("%Y-%m-%d")

    # 1. An explicit, written waiver.
    if waive is not None:
        if not waive.strip():
            out.update(code=EXIT_USAGE,
                       reason="--no-test needs a written reason; a blank one is not a waiver")
            _log_decision("refused", base, entry["title"], "blank waiver reason", by)
            return out
        _apply(path, entry, by, f"(waived by {by or 'unknown'} — {waive.strip()})", today)
        _log_decision("waived", base, entry["title"], waive.strip(), by)
        return {**out, "ok": True, "code": EXIT_OK, "verdict": "waived",
                "reason": f"resolved with a recorded waiver: {waive.strip()}"}

    ref = (test or entry["test_ref"] or "").strip()

    # 2. A named test — the runner has to agree it exists.
    if ref:
        import run_tests
        verdict = run_tests.collects(ref, base)
        if not verdict["collected"]:
            out.update(code=EXIT_NO_TEST,
                       reason=f"{verdict['reason']} — issue NOT resolved")
            _log_decision("refused", base, entry["title"], verdict["reason"], by)
            return out
        note = "" if verdict["verified"] else "  (collection NOT verified — see reason)"
        _apply(path, entry, by, ref, today)
        _log_decision("resolved", base, entry["title"], verdict["reason"], by)
        return {**out, "ok": True, "code": EXIT_OK, "verdict": "verified",
                "reason": verdict["reason"] + note}

    # 3. No usable ref. Only a pre-feature entry that never had the field is
    #    let through — a *declared* absence is exactly what this refuses.
    if entry["test_field"] is None and is_grandfathered(entry):
        _apply(path, entry, by,
               f"(grandfathered — filed {entry['date']}, before {ENFORCED_FROM}; "
               f"no regression test on record)", today)
        _log_decision("grandfathered", base, entry["title"],
                      f"filed {entry['date']} before {ENFORCED_FROM}", by)
        return {**out, "ok": True, "code": EXIT_OK, "verdict": "grandfathered",
                "reason": f"filed {entry['date']}, before {ENFORCED_FROM} — grandfathered"}

    if entry["test_field"] is None:
        reason = (f"filed {entry['date']}, on or after {ENFORCED_FROM}, yet carries no "
                  f"`test:` field — this writer always writes one, so the entry was "
                  f"hand-edited. Add the field or use --no-test \"reason\".")
    else:
        reason = (f"this issue declares `test: {entry['test_field']}` — name the "
                  f"regression test that proves it fixed, or waive it with "
                  f"--no-test \"reason\".")
    out.update(code=EXIT_NO_TEST, reason=reason)
    _log_decision("refused", base, entry["title"], reason, by)
    return out


# ── CLI ────────────────────────────────────────────────────────────────────

def _print_gap(root: Path | str | None) -> int:
    gap = issues_without_tests(root)
    if not gap:
        print("✅ every open issue names a regression test.")
        return 0
    old = sum(1 for g in gap if g["grandfathered"])
    print(f"⚠️  {len(gap)} open issue(s) with no regression test "
          f"({old} grandfathered, {len(gap) - old} filed since {ENFORCED_FROM}):")
    for g in gap:
        tag = "grandfathered" if g["grandfathered"] else "NEEDS A TEST"
        print(f"   [{g['date']}] {tag} — {g['title'][:80]}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)

    root = None
    if "--root" in argv:
        i = argv.index("--root")
        if i + 1 >= len(argv):
            print("--root needs a path")
            return EXIT_USAGE
        root = argv[i + 1]
        del argv[i:i + 2]

    if argv and argv[0] == "resolve":
        ap = argparse.ArgumentParser(prog="note_issue.py resolve")
        ap.add_argument("match", help="substring of the issue title")
        ap.add_argument("--test", default=None, help="tests/x.py::Class::test_name")
        ap.add_argument("--by", default="", help="the task doing the fix (PH#-T##)")
        ap.add_argument("--no-test", dest="waive", default=None,
                        help="waive the requirement — needs a written reason; logged")
        args = ap.parse_args(argv[1:])
        out = resolve_issue(args.match, test=args.test, by=args.by,
                            waive=args.waive, root=root)
        if out["ok"]:
            print(f"✅ Resolved ({out['verdict']}): {out['title']}")
            print(f"   {out['reason']}")
        else:
            print(f"❌ NOT resolved: {out['title'] or out['reason']}")
            if out["title"]:
                print(f"   {out['reason']}")
            for c in out["candidates"]:
                print(f"   • {c[:90]}")
        return out["code"]

    if argv and argv[0] in ("--gap", "gap"):
        return _print_gap(root)

    # `file` is the explicit filing verb, and therefore the escape hatch for the one-word rule
    # below: it says "what follows is my title, not a subcommand I mistyped".
    forced = bool(argv) and argv[0] == "file"
    if forced:
        argv = argv[1:]

    # Flags on the positional path. Before PH16-T17 there were none: `--test REF` was simply
    # two more positionals, so `note_issue.py add "T" "D" --test "R"` filed title=`add`,
    # description=`T`, test=`D` and dropped the flag — every field shifted by one, and the
    # entry then counted as *tested* in `--gap`, which is the hole PH7-T06 exists to close.
    flag_test, positional = None, []
    i = 0
    while i < len(argv):
        token = argv[i]
        if token == "--test":
            if i + 1 >= len(argv):
                print("--test needs a test reference (or \"(none yet)\")")
                return EXIT_USAGE
            flag_test, i = argv[i + 1], i + 2
            continue
        if token.startswith("--"):
            # Never absorbed into the entry. A typo'd flag becoming the description is the
            # same silent shift by another route.
            print(f"❌ unrecognised option {token!r}.")
            print("   Filing accepts only --test. See `note_issue.py` with no arguments.")
            return EXIT_USAGE
        positional.append(token)
        i += 1

    if len(positional) < 2:
        print("Usage: note_issue.py \"Issue title\" \"Full description\" [test-ref]")
        print("       note_issue.py \"Issue title\" \"Full description\" --test tests/x.py::Cls::test_y")
        print("       note_issue.py file \"One-word-title\" \"Full description\"")
        print("       note_issue.py resolve \"<title substring>\" --by PH#-T## "
              "--test tests/x.py::Cls::test_y")
        print("       note_issue.py --gap")
        return EXIT_USAGE

    # A title is a sentence; a subcommand is one word. That is the only thing the two never
    # share, and it is what makes a mistyped verb detectable at all — `add "T" "D"` is three
    # positionals, which is a legal filing, so arity cannot tell them apart (PH16-T17).
    if not forced and not re.search(r"\s", positional[0]):
        print(f"❌ unrecognised subcommand {positional[0]!r} — refusing to file it as a title.")
        print("   Recognised: resolve · file · gap (--gap). Nothing was written.")
        print(f"   If {positional[0]!r} really is the title, force it: "
              f"note_issue.py file {positional[0]!r} \"<description>\"")
        return EXIT_USAGE

    if flag_test is not None and len(positional) > 2:
        print("❌ the test reference was given twice — as --test and as a third positional.")
        print(f"   --test {flag_test!r} · positional {positional[2]!r}. Nothing was written.")
        return EXIT_USAGE

    test = flag_test if flag_test is not None else (positional[2] if len(positional) > 2 else None)
    log_issue(positional[0], positional[1], test=test, root=root)
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
