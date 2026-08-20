#!/usr/bin/env python3
"""finding.py — a finding travels as a note, not an edit (PH16-T22, rule A).

## The incident this exists for

On 2026-08-13 the `@life` session, validating its own post-upgrade state, found a
real defect in the **kernel**: `doctor.py` warns *"memory INDEX.md — run
god-upgrade to add it"* while `onboard_project.sh` never seeded `INDEX.md`, so the
remedy the health check advertises does not exist. The finding was right, and so
was the instinct that a fix only survives in the kernel. Then it began writing the
fix into the kernel's tree — a new `tests/test_memory_index.py`, an edit to
`scripts/onboard_project.sh` — while another session was mid-closure there.

That defeats three enforcement paths at once, and none of them can even see it:

  * `task_ledger.active_task()` reads the **receiving** workspace's
    `activeContext.md`, so the arriving work binds `evidence.json` to no task. It
    lands with no DoD, no plan and no budget slot.
  * `verify-safe` runs the whole suite, so a foreign red or half-written test holds
    the gate closed for **both** sessions.
  * `self_review` binds its record to the diff's content hash precisely so "review,
    then quietly add one more thing" is impossible; a second writer makes that
    binding un-satisfiable rather than merely false, which pressures the operator
    toward an override.

## What this does instead

A finding is **information**; acting on it is **work**, and work needs a slot, a
plan and a DoD in the workspace that does it. So a finding crosses a workspace
boundary as two inert artefacts:

  1. an entry in the target's `knownIssues.md`, **naming the workspace it came
     from**, filed through `note_issue.log_issue(root=…)` — that file's one writer
     already takes a root, so this adds a caller and not a second writer;
  2. the reproduction as text at `<target>/.ai/inbox/<date>-<origin>-<slug>.md`.

Nothing executable. Nothing under `tests/` or `scripts/`. The receiving session
picks the note up on its own terms, in its own budget.

## Two deliberate choices

**A suggested test is recorded as prose, not in the `test:` field.** The field
means *the regression test that proves this fixed*, and `resolve-issue` checks the
receiving workspace's own runner collects it. A ref pointing at a file that exists
only in the origin would make `--gap` count the issue as tested when the target has
no test at all — the "claim nothing verifies" defect this whole workspace is built
against. The suggestion is carried where a human reads it and a counter does not.

**The send is decision-logged in the ORIGIN only.** The target's record of the
event is the issue entry and the note. Writing another workspace's decision log
from outside is the same foreign write this rule refuses.

## The other half of rule (A)

This is the channel; `policy_hook.py` is what makes using it non-optional — a write
into another *governed* workspace is denied there, naming this command. A channel
nobody is forced to use is a README.

Usage:
    just finding "@context" "title" "what happened"
    python3 scripts/finding.py send --to @context --title T --desc D \\
            [--repro TEXT | --repro-file PATH] [--test tests/x.py::C::t] [--origin @life]
    python3 scripts/finding.py list [--root .]
    python3 scripts/finding.py show <note-name>
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROUTES_FILE = Path.home() / ".gemini" / "m_protocol_routes.json"
INBOX = Path(".ai") / "inbox"

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_TARGET = 2       # the target cannot receive a finding
EXIT_SAME = 3            # origin and target are the same workspace


class SendError(RuntimeError):
    """The finding could not be routed. Nothing is written on this path."""


# ── Where a finding is allowed to land ─────────────────────────────────────

def is_governed(root: Path) -> bool:
    """A workspace that can receive a finding is a governed one.

    Imported from `policy_hook`, deliberately, rather than re-implemented: the
    guard denies a write *because* this channel exists, so a second copy of
    "what is a workspace" would eventually deny writes into a target this
    command refuses to send to. Read from the filesystem, never from the route
    table — a route entry is a claim, `.ai/memory-bank/` + a `justfile` with
    `session-start` is the thing itself.
    """
    from policy_hook import is_governed_workspace
    return is_governed_workspace(Path(root))


def resolve_workspace(name: str) -> Path:
    """`@tag` via the routes file, or a path. Never guesses between the two."""
    if not name:
        raise SendError("no target given")
    if name.startswith("@"):
        try:
            routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            raise SendError(f"cannot read {ROUTES_FILE} to resolve {name}: {exc}") from exc
        path = routes.get(name)
        if not path:
            raise SendError(f"{name} is not in {ROUTES_FILE} — pass a path instead")
        return Path(path).expanduser().resolve()
    return Path(name).expanduser().resolve()


def workspace_root(start: Path | None = None) -> Path:
    """This workspace's root, resolved the anti-drift way (harness dir, then git
    top-level, then cwd) — never whatever file happens to be open."""
    env_root = os.environ.get("CLAUDE_PROJECT_DIR")
    if env_root and start is None:
        return Path(env_root).resolve()
    base = Path(start or Path.cwd()).resolve()
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      cwd=base, stderr=subprocess.DEVNULL,
                                      text=True).strip()
        if top:
            return Path(top).resolve()
    except Exception:  # noqa: BLE001
        pass
    return base


def workspace_tag(root: Path) -> str:
    """The origin's own @tag from the routes file, falling back to its directory
    name. A finding must say where it came from even from an unrouted workspace."""
    try:
        routes = json.loads(ROUTES_FILE.read_text(encoding="utf-8"))
        for tag, path in routes.items():
            try:
                if Path(path).expanduser().resolve() == root:
                    return tag
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    return "@" + root.name.lower()


# ── The note ───────────────────────────────────────────────────────────────

def slug(text: str, limit: int = 48) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return (s[:limit].rstrip("-") or "finding")


def note_path(target: Path, origin_tag: str, title: str, when: datetime) -> Path:
    base = f"{when.strftime('%Y-%m-%d')}-{slug(origin_tag.lstrip('@'), 24)}-{slug(title)}"
    inbox = target / INBOX
    candidate = inbox / f"{base}.md"
    n = 2
    while candidate.exists():
        candidate = inbox / f"{base}-{n}.md"
        n += 1
    return candidate


def render_note(origin_tag: str, origin_root: Path, title: str, desc: str,
                repro: str, test: str, when: datetime) -> str:
    """Inert text. No front matter the target's tooling parses, no code it runs."""
    lines = [
        f"# Finding from {origin_tag} — {title}",
        "",
        f"- **Origin workspace:** {origin_tag} (`{origin_root}`)",
        f"- **Sent:** {when.strftime('%Y-%m-%d %H:%M')} UTC",
        f"- **Origin session:** {os.environ.get('CLAUDE_CODE_SESSION_ID') or os.environ.get('GEMINI_SESSION_ID') or '(unidentified)'}",
        "",
        "## What was observed",
        "",
        desc.strip(),
        "",
    ]
    if repro.strip():
        lines += ["## Reproduction (text, carried — not run)", "", "```",
                  repro.rstrip(), "```", ""]
    if test.strip():
        lines += [
            "## Suggested regression test",
            "",
            f"`{test.strip()}`",
            "",
            "Recorded as a suggestion, deliberately **not** in the issue's `test:` "
            "field: that field means a test this workspace's own runner collects, "
            "and a ref that only exists in the origin would make `--gap` count this "
            "issue as tested when nothing here tests it.",
            "",
        ]
    lines += [
        "## How to act on this",
        "",
        "This note is information, not work. Give it a task in this workspace's own "
        "ledger (`.ai/docs/tasks.md`) with its own DoD and budget slot, write the "
        "failing test here first, then resolve the issue entry by name:",
        "",
        "```",
        'just resolve-issue "<title substring>" "tests/…::Cls::test_x" "PH#-T##"',
        "```",
        "",
        f"_Filed by `finding.py` (PH16-T22 rule A) from {origin_tag}. "
        "The origin did not edit any file in this workspace._",
        "",
    ]
    return "\n".join(lines)


# ── Can the recipient actually surface what it received? (PH16-T26) ────────

#: The one call PH16-T25 routed briefing section 4b through, deliberately, "rather
#: than a second glob so reader and writer cannot disagree". A `session_start.py`
#: that never names it cannot announce a note, whatever headings it contains — which
#: is why this probes the call site and not the heading text. Grepping for
#: "4b. FINDINGS RECEIVED" would match a comment, a docstring or a changelog line
#: quoting it: the mention-vs-declaration bug this workspace has shipped before.
ANNOUNCE_CALL = "inbox_notes"

#: `just findings` needs both halves. Either one absent and the command fails, so
#: they are checked together and reported as one capability.
LIST_SCRIPT = Path("scripts") / "finding.py"
LIST_RECIPE = re.compile(r"^findings\s*:", re.MULTILINE)


def reader_state(target: Path | str) -> dict:
    """Will `target` ever show a human the finding it just received?

    Two capabilities, probed separately because they fail separately and have
    different consequences: **announce** (the briefing tells you unprompted) and
    **list** (`just findings` when you think to ask). A workspace mid-upgrade
    commonly has one and not the other.

    **Presence, never version.** A target whose copies merely differ from the
    kernel's is not reported as unable to read — it usually reads fine, and a
    warning that fires on a healthy workspace is worse than no warning at all: the
    operator stops reading the line, and the real case goes with it. `compare()` in
    `deploy_digest` answers the version question and is the wrong tool here; all 45
    workspaces are stale by that measure today.

    `known: False` means the probe could not answer — never a clean bill of health
    it did not establish.
    """
    ws = Path(target).resolve()
    state = {"announce": False, "list": False, "missing": [], "known": True}
    try:
        boot = ws / "scripts" / "session_start.py"
        state["announce"] = (boot.is_file()
                             and ANNOUNCE_CALL in boot.read_text(encoding="utf-8",
                                                                 errors="replace"))
        jf = ws / "justfile"
        state["list"] = ((ws / LIST_SCRIPT).is_file() and jf.is_file()
                         and bool(LIST_RECIPE.search(jf.read_text(encoding="utf-8",
                                                                  errors="replace"))))
    except OSError:
        return {"announce": False, "list": False, "missing": [], "known": False}

    if not state["announce"]:
        state["missing"].append(
            "nothing will announce it — that workspace's session-start briefing has no "
            "findings section (scripts/session_start.py predates PH16-T25)")
    if not state["list"]:
        state["missing"].append(
            "nobody can list it — `just findings` does not exist there "
            "(scripts/finding.py or the `findings` recipe is absent)")
    return state


def _reachability_lines(target: Path, state: dict) -> list[str]:
    """What the sender is told. Silent when the recipient can read — the silence is
    the feature, and it is asserted by its own test."""
    if not state.get("known"):
        return ["   ⚠️  could not check whether that workspace can surface this note."]
    if not state["missing"]:
        return []
    return [
        f"   ⚠️  {workspace_tag(target)} cannot surface this note yet:",
        *[f"        · {m}" for m in state["missing"]],
        "      The note and the issue entry are written and durable — nothing is lost,",
        "      and they become visible the moment that workspace is upgraded:",
        f"          cd {target} && god-upgrade .",
        "      Do NOT edit that workspace to compensate (PH16-T22 rule A).",
    ]


# ── Sending ────────────────────────────────────────────────────────────────

def send(to: str, title: str, desc: str, repro: str = "", test: str = "",
         origin: str = "", origin_root: Path | None = None) -> dict:
    """File a finding into another governed workspace.

    Returns `{target, note, issue, reader}` — `reader` being whether the recipient
    can surface what it was just handed (PH16-T26).

    Every refusal happens BEFORE the first write, so a refused send leaves the
    target byte-identical — the rule `note_issue` follows for a refused resolve.
    """
    if not (title or "").strip():
        raise SendError("a finding needs a title")
    if not (desc or "").strip():
        raise SendError("a finding needs a description — a title alone is a rumour")

    src = Path(origin_root).resolve() if origin_root else workspace_root()
    target = resolve_workspace(to)

    if not target.is_dir():
        raise SendError(f"{target} does not exist")
    if target == src:
        raise SendError(f"{target} is this workspace — file it locally with "
                        f"`just note-issue` instead")
    if not is_governed(target):
        raise SendError(f"{target} is not a God Mode workspace (no .ai/memory-bank/) "
                        f"— there is nowhere for a finding to land, and creating one "
                        f"would be the foreign write this rule refuses")
    if not (target / ".ai" / "memory-bank" / "knownIssues.md").is_file():
        raise SendError(f"{target} has a memory bank but no knownIssues.md — its own "
                        f"`just session-start` creates that file, and this must not "
                        f"create it on the workspace's behalf")

    origin_tag = origin.strip() or workspace_tag(src)
    when = datetime.now(timezone.utc)

    note = note_path(target, origin_tag, title, when)
    note.parent.mkdir(parents=True, exist_ok=True)
    note.write_text(render_note(origin_tag, src, title, desc, repro, test, when),
                    encoding="utf-8")

    rel_note = note.relative_to(target).as_posix()
    body = (f"**Reported from {origin_tag}** (`{src}`) — {desc.strip()} "
            f"Reproduction and full context: `{rel_note}`.")
    if test.strip():
        body += (f" The origin suggests `{test.strip()}` as the regression test; it is "
                 f"a suggestion, not a test this workspace has.")
    body += (f" This arrived as a note, not an edit — {origin_tag} changed no file here "
             f"(PH16-T22 rule A). Acting on it needs a task in this workspace's ledger.")

    import note_issue
    note_issue.log_issue(title.strip(), body, test=None, root=target)

    _log(src, origin_tag, target, title)
    return {"target": target, "note": note, "issue": target / ".ai" / "memory-bank"
            / "knownIssues.md", "reader": _reader_state_safely(target)}


def _reader_state_safely(target: Path) -> dict:
    """The probe runs AFTER both writes and can never raise past this point.

    A diagnostic that eats the thing it diagnoses is worse than no diagnostic: the
    finding would be lost while the sender is told it was filed, which is a sharper
    version of the exact defect this function exists to report.
    """
    try:
        return reader_state(target)
    except Exception:  # noqa: BLE001
        return {"announce": False, "list": False, "missing": [], "known": False}


def _log(src: Path, origin_tag: str, target: Path, title: str) -> None:
    """Best effort — a logging failure must never lose a finding."""
    try:
        import decision_log
        decision_log.record("finding", "sent", "finding", root=src,
                            action=f"finding → {target.name}: {title[:60]}",
                            path=str(target),
                            reason=f"cross-workspace finding from {origin_tag}, "
                                   f"routed as a note (PH16-T22 rule A)")
    except Exception:  # noqa: BLE001
        pass


# ── Reading, in the workspace that received them ───────────────────────────

def inbox_notes(root: Path | None = None) -> list[Path]:
    base = Path(root).resolve() if root else workspace_root()
    inbox = base / INBOX
    return sorted(inbox.glob("*.md")) if inbox.is_dir() else []


def _origin_of(note: Path) -> str:
    # Two spellings, because two writers exist. `send()` emits "Origin workspace:";
    # a note an agent composes by hand reads "**From:** `@tag`" (PH16-T25 — the first
    # real received note was hand-written and rendered as "(unknown origin)"). Backticks
    # are stripped so the tag prints the same either way.
    for line in note.read_text(encoding="utf-8").splitlines():
        m = re.search(r"\*\*(?:Origin workspace|From):\*\*\s*`?(@?[\w.-]+)`?", line)
        if m:
            return m.group(1)
    return "(unknown origin)"


def _title_of(note: Path) -> str:
    first = note.read_text(encoding="utf-8").splitlines()[:1]
    if not first:
        return note.stem
    # The heading is "Finding from @x — title"; the origin is printed beside this,
    # so repeating it here reads as "from @x — Finding from @x — …".
    return re.sub(r"^#\s*(Finding from \S+\s+—\s*)?", "", first[0]).strip()


# ── CLI ────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd")

    s = sub.add_parser("send", help="file a finding into another governed workspace")
    s.add_argument("--to", required=True, help="@tag from the routes file, or a path")
    s.add_argument("--title", required=True)
    s.add_argument("--desc", required=True)
    s.add_argument("--repro", default="", help="reproduction, carried as text")
    s.add_argument("--repro-file", default="", help="read the reproduction from a file")
    s.add_argument("--test", default="", help="suggested regression test (a suggestion)")
    s.add_argument("--origin", default="", help="override the origin @tag")

    ls = sub.add_parser("list", help="findings this workspace has received")
    ls.add_argument("--root", default=None)

    sh = sub.add_parser("show", help="print one received note")
    sh.add_argument("name")
    sh.add_argument("--root", default=None)

    args = ap.parse_args(argv)

    if args.cmd == "list":
        notes = inbox_notes(args.root)
        if not notes:
            print("📭 no findings received (.ai/inbox/ is empty).")
            return EXIT_OK
        print(f"📥 {len(notes)} finding(s) received:")
        for n in notes:
            print(f"   • {n.name}\n     from {_origin_of(n)} — {_title_of(n)}")
        print("\n   Read one: just finding-show <name>")
        return EXIT_OK

    if args.cmd == "show":
        for n in inbox_notes(args.root):
            if n.name == args.name or n.stem == args.name:
                print(n.read_text(encoding="utf-8"))
                return EXIT_OK
        print(f"❌ no received finding named {args.name!r}", file=sys.stderr)
        return EXIT_USAGE

    if args.cmd != "send":
        ap.print_help()
        return EXIT_USAGE

    repro = args.repro
    if args.repro_file:
        try:
            repro = Path(args.repro_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"❌ cannot read --repro-file: {exc}", file=sys.stderr)
            return EXIT_USAGE

    try:
        out = send(args.to, args.title, args.desc, repro=repro, test=args.test,
                   origin=args.origin)
    except SendError as exc:
        print(f"🛑 finding NOT sent — {exc}", file=sys.stderr)
        print("   Nothing was written. A finding is only ever a note; if the target "
              "cannot receive one, do not edit it instead.", file=sys.stderr)
        return EXIT_NO_TARGET
    except Exception as exc:  # noqa: BLE001
        print(f"🛑 finding NOT sent — {exc}", file=sys.stderr)
        return EXIT_NO_TARGET

    print(f"📤 finding filed in {out['target']}")
    print(f"   issue: {out['issue']}")
    print(f"   note:  {out['note']}")
    print("   Nothing else in that workspace was touched.")

    warning = _reachability_lines(out["target"], out["reader"])
    if warning:
        # Deliberately not a refusal and deliberately not a non-zero exit. The note is
        # durable and surfaces on upgrade, so blocking would destroy information to
        # avoid a delay — and would rebuild the trap `@jobscraper` filed against
        # `policy_hook.py`: a block with no runnable exit.
        print("\n".join(warning))
    else:
        print("   Its next session picks this up in its own budget.")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
