#!/usr/bin/env python3
"""
doctor.py — God Mode AI OS health check.  Run: `just doctor`

Verifies BOTH AI entrypoints (Claude + Antigravity) and the core invariants,
so you can tell at a glance whether a workspace is fully dual-AI governed.
Exits non-zero if any check FAILs.

## Why `run_checks()` is separate from `render()` (PH16-T01)

The verdict and its rendering used to be one function, which meant the only way
to ask "is this workspace healthy" was to run the script and parse its output.
`health.py` needs the *verdict* at session open — with counts, not a wall of
text — so the checks now return a list and `render()` prints it. The printed
output and the exit code are unchanged; this is a shape change, not a policy one.

`run_checks()` is re-entrant on purpose: it builds its own list per call rather
than appending to a module global. Two calls in one process must not report the
double of everything, and that is asserted rather than assumed.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_links  # noqa: E402


def _ws_root() -> Path:
    try:
        t = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                    stderr=subprocess.DEVNULL, text=True).strip()
        if t:
            return Path(t)
    except Exception:
        pass
    return Path(".").resolve()


ROOT = _ws_root()
HOME = Path.home()

# (level, label, detail) — level ∈ {"OK", "WARN", "FAIL"}
Check = tuple[str, str, str]


def run_checks(root: Path | None = None) -> list[Check]:
    """Every health check, as data. Prints nothing; never raises for a FAIL."""
    # Local, not the module global: the body below is unchanged from when `ROOT`
    # was that global, and shadowing it here is what makes an explicit root work
    # without touching thirty call sites.
    ROOT = Path(root) if root is not None else _ws_root()
    checks: list[Check] = []

    def ok(label, detail=""):   checks.append(("OK", label, detail))
    def warn(label, detail=""): checks.append(("WARN", label, detail))
    def fail(label, detail=""): checks.append(("FAIL", label, detail))

    # ── Claude entrypoints ────────────────────────────────────────────────
    (ok if (ROOT / "AGENTS.md").exists() else fail)(
        "root AGENTS.md (canonical protocol)", "" if (ROOT / "AGENTS.md").exists() else "missing")
    claude = ROOT / "CLAUDE.md"
    if claude.exists():
        (ok if "@AGENTS.md" in claude.read_text() else warn)(
            "CLAUDE.md imports @AGENTS.md")
    else:
        fail("CLAUDE.md missing", "Claude has no workspace entrypoint")
    (ok if (HOME / ".claude" / "CLAUDE.md").exists() else warn)(
        "~/.claude/CLAUDE.md (global fleet preamble)")

    settings = ROOT / ".claude" / "settings.json"
    if settings.exists():
        try:
            s = json.loads(settings.read_text())
            (ok if "SessionStart" in s.get("hooks", {}) else warn)(
                ".claude/settings.json SessionStart hook")
        except Exception as e:  # noqa: BLE001
            fail(".claude/settings.json invalid JSON", str(e))
    else:
        warn(".claude/settings.json missing", "no auto session-start for Claude")

    # ── Symlink mirrors ───────────────────────────────────────────────────
    for rel, label in [
        (".agents/AGENTS.md", ".agents/AGENTS.md → canonical"),
        (".claude/skills/validation-gate/SKILL.md", ".claude/skills/validation-gate"),
        (".claude/commands/start-task.md", "/start-task command"),
        (".claude/commands/start-with-memory.md", "/start-with-memory command"),
        (".claude/commands/log-session.md", "/log-session command"),
    ]:
        (ok if (ROOT / rel).exists() else fail)(label, "" if (ROOT / rel).exists() else "broken/missing")

    # ── Antigravity / Gemini ──────────────────────────────────────────────
    (ok if (HOME / ".gemini" / "GEMINI.md").exists() else warn)(
        "~/.gemini/GEMINI.md (Antigravity fleet rules)")

    # ── Memory bank ───────────────────────────────────────────────────────
    mb = ROOT / ".ai" / "memory-bank"
    # PH8-T02: which files are required is a property of the workspace's ROLE, not
    # of the OS. This used to be a literal list here and a second one in
    # session_start.py; both now read the one source. A workspace that declares no
    # `role_pack:` resolves to `software-engineer`, whose schema is the same seven
    # names in the same order — so nothing changes for the 46 existing workspaces.
    #
    # Degrades rather than crashing: an unreadable or unknown role WARNS and the
    # check continues. The harder case — `role_registry` itself unimportable — is
    # handled in the except below, and deliberately does NOT invent a list.
    role_note = ""
    try:
        import role_registry as _rr
        required = _rr.required_memory(ROOT)
        prohibited = _rr.prohibited_memory(ROOT)
        _role = _rr.resolve(ROOT)
        role_note = f"role: {_role['id']}"
        if not _role["ok"]:
            warn("role pack declaration", _role["error"])
        elif _role["error"]:
            warn("role packs", _role["error"])
    except Exception as exc:  # noqa: BLE001
        # No second literal list here, deliberately. `role_registry` owns the
        # fallback (its BUILTIN_REQUIRED); if the module itself is unreachable,
        # doctor cannot KNOW what this workspace requires, and a list invented at
        # this point would be a guess wearing the costume of a check. Say so and
        # check nothing — a named gap beats a confident wrong answer.
        required, prohibited = [], []
        role_note = ""
        fail("memory bank schema unavailable",
             f"role_registry could not be loaded ({exc}) — cannot determine which "
             "files this workspace requires")
    if required:
        missing = [f for f in required if not (mb / f"{f}.md").exists()]
        (fail if missing else ok)(
            f"memory bank complete ({len(required)} files, {role_note})",
            "missing: " + ", ".join(missing) if missing else "")

    # A prohibited file present is a WARNING, never a failure. The file is harmless;
    # failing would turn a green workspace red the instant it declared a role, which
    # punishes migration at precisely the moment it should be easy.
    present_prohibited = [f for f in prohibited if (mb / f"{f}.md").exists()]
    if present_prohibited:
        warn("memory bank carries files this role prohibits",
             ", ".join(f"{f}.md" for f in present_prohibited)
             + " — safe to delete once its content is moved")

    # Context budget — hot memory files should stay inside the budget the context actually
    # charges. The file-level number is the symptom; PH16-T05 added the cause underneath it,
    # so this reports WHY a file is fat and names the specific entries eating it.
    #
    # **Charged in chars since PH16-T19, reported in both.** This check counted lines for as
    # long as it existed, and lines were a proxy that broke: `knownIssues.md` read as a mild
    # 297/200 while costing ~38k tokens — a quarter of the session budget, ~5× what the line
    # count implied. The line figure is still printed beside the char one, because every
    # earlier run of this check printed it and a verdict that changes has to show which
    # measure moved. Over on EITHER unit is over budget; nothing is being relaxed.
    oversized = []
    # The budget comes from `archive_memory` or not at all. A literal here would be a second
    # copy of a constant this repo has already been bitten by duplicating — so when the module
    # cannot be imported this degrades to the line half and SAYS so, rather than inventing a
    # number that would silently disagree with the real one the day either constant moves.
    try:
        import archive_memory as _am
        char_budget = _am.MAX_CHARS
    except Exception:  # noqa: BLE001
        char_budget = None
    for f in required:
        p = mb / f"{f}.md"
        if p.exists():
            text = p.read_text()
            n, ch = len(text.splitlines()), len(text)
            if n > 200 or (char_budget is not None and ch > char_budget):
                oversized.append(f"{f}.md={ch:,}ch/≈{ch // 4 // 1000}k tok ({n} ln)")
    label = ("memory files within context budget (≤200 lines; char budget unavailable — "
             "archive_memory could not be imported)" if char_budget is None else
             f"memory files within context budget (≤{char_budget:,} chars ≈ "
             f"{char_budget // 4 // 1000}k tokens, ≤200 lines)")
    (warn if oversized else ok)(
        label,
        "oversized → run `just archive-memory`: " + ", ".join(oversized)
        if oversized else "")

    # ── Per-entry budget + archiver blindness (PH16-T05) ──────────────────
    # A warning that has been true for weeks stops being read. These two are
    # different: `blind` means archive_memory.RULES can no longer see the file's
    # shape (the tool is broken and says nothing is wrong), `unarchived` means
    # closed history is sitting in a hot file because nobody ran the archiver.
    # Both are FAILs because both are fixable; being over budget on genuinely live
    # state stays a warn above, since only a human can decide what to cut.
    try:
        import entry_budget as _entry_budget
        eb = _entry_budget.check(ROOT)
        blind, unarchived, unexcused = eb["blind"], eb["unarchived"], eb["unexcused"]
        if blind:
            detail = "; ".join(
                f"{f['file']} ({f['unexplained_lines']} of {f['lines']} lines unaccounted for)"
                for f in eb["files"] if f["blind"])
            fail("archive-memory can see every hot memory file",
                 f"{detail} — over budget, and no rule in archive_memory.RULES claims that "
                 f"content as either history or declared-live. The file's shape has drifted "
                 f"from its rule; widen the rule.")
        elif unarchived:
            movable = ", ".join(f"{f['file']} (−{f['lines'] - f['archived_lines']} ln)"
                                for f in eb["files"] if f["unarchived"])
            fail("hot memory files hold no unarchived history",
                 f"{movable} → run `just archive-memory --apply`")
        elif unexcused:
            # PH27-T03 — this used to be indistinguishable from the generic "oversized"
            # warn above, which reads as "a human must decide what to cut". For a file
            # whose rule declares no shape as ever live (`pure_history`), that reading is
            # wrong: at the archiver's floor there is nothing left to edit AWAY, only a
            # block the archiver can see but never selects. `doctor` now says so — the
            # same verdict `test_progress_md_is_actually_within_the_line_budget` reaches,
            # reusing `entry_budget.check()`'s own `unexcused` rather than a second one.
            stuck = "; ".join(f"{f['file']} ({f['lines']}/{f['budget']} ln at its floor)"
                              for f in eb["files"] if f["unexcused"])
            fail("hot memory files with no live-state excuse stay within budget",
                 f"{stuck} — archive-memory is already at its floor and the file is still "
                 f"over. This file's rule declares no live state, so the overage is a block "
                 f"the archiver can see but cannot select (commonly an undated heading), "
                 f"not a human editing decision.")
        else:
            ok("archive-memory has moved everything it can")
        worst = eb["oversized_entries"]
        if worst:
            named = "; ".join(f"{e['file']} “{e['head'][:38]}…” {e['lines']} ln"
                              for e in worst[:3])
            warn(f"no single memory entry exceeds its share ({len(worst)} over)", named)
        else:
            ok("no single memory entry exceeds its share of the file budget")
    except Exception as e:  # noqa: BLE001
        warn("entry budget check unavailable", str(e)[:80])

    # ── The prose and the ledger agree about what is done (PH16-T41) ──────
    # A FAIL, and visible at every boot, because the failure mode is a session
    # READING `activeContext.md`, seeing "Complete", and skipping work the gate
    # will still refuse to credit. Two live specimens existed when this shipped —
    # one of them filed as an issue a session earlier and still unfixed, which is
    # the argument for a check rather than another paragraph.
    try:
        import task_ledger as _task_ledger
        disagree = _task_ledger.disagreements(ROOT)
        if disagree:
            named = "; ".join(f"{d['task']} ({d['kind']})" for d in disagree[:4])
            more = f" (+{len(disagree) - 4} more)" if len(disagree) > 4 else ""
            fail("prose and ledger agree on what is done",
                 f"{named}{more} — {disagree[0]['detail']}")
        else:
            ok("prose and ledger agree on what is done")
    except Exception as e:  # noqa: BLE001
        warn("prose/ledger agreement check unavailable", str(e)[:80])

    # ── No same-date ordinal collision in any hot memory file (PH27-T09) ──
    # A FAIL, not a WARN: found live in `progress.md` (PH27-T08) — two same-day
    # blocks `proves_older` cannot tell apart mean `select()` may archive either
    # one depending on which end of the file it starts from. Until this shipped,
    # the only way to learn a collision existed was to run the full ~146s suite;
    # `ordinal_collisions()` reuses `archive_memory.RULES`/`split_blocks` and
    # defines no rule of its own.
    try:
        import archive_memory as _archive_memory
        collisions = _archive_memory.ordinal_collisions(ROOT)
        if collisions:
            named = "; ".join(
                f"{c['file']} {c['date']} ordinal={c['ordinal']}" for c in collisions[:4])
            more = f" (+{len(collisions) - 4} more)" if len(collisions) > 4 else ""
            fail("no same-date ordinal collision in hot memory files",
                 f"{named}{more} — {collisions[0]['detail']}")
        else:
            ok("no same-date ordinal collision in hot memory files")
    except Exception as e:  # noqa: BLE001
        warn("ordinal collision check unavailable", str(e)[:80])

    # Memory INDEX present + retrieval graph has no dangling [[links]]
    (ok if (mb / "INDEX.md").exists() else warn)(
        "memory INDEX.md (retrieval map)",
        "" if (mb / "INDEX.md").exists() else "run god-upgrade to add it")
    link_errors = check_links.validate(ROOT, check_links.default_scan(ROOT))
    (fail if link_errors else ok)(
        "memory [[links]] all resolve",
        "; ".join(link_errors) if link_errors else "")

    # ── Evidence + policy schema ──────────────────────────────────────────
    r = subprocess.run(["python3", str(ROOT / "scripts" / "validate_os.py")],
                       capture_output=True, text=True)
    (ok if r.returncode == 0 else fail)("evidence.json + policy.yaml valid",
                                        "" if r.returncode == 0 else r.stdout.strip().replace("\n", " "))
    # Single source of truth for the gate — doctor must not re-implement it.
    # (It used to read only `status`, so an onboarding stub reported ✅.)
    g = subprocess.run(["python3", str(ROOT / "scripts" / "gate_check.py"), "--json"],
                       capture_output=True, text=True)
    try:
        verdict = json.loads(g.stdout)
        (ok if verdict["open"] else fail)("validation gate", verdict["reason"])
    except Exception:  # noqa: BLE001
        fail("validation gate", "gate_check.py did not return a verdict")

    # ── Code map (PH7-T07) ────────────────────────────────────────────────
    # Absent is a WARN, stale is a FAIL. 38 workspaces have no codemap, and
    # failing for its absence would turn every doctor in the fleet red the day
    # this deploys — a red doctor everywhere is a red doctor nowhere. Having one
    # is opting in; a stale one is a real defect. Same asymmetry PH7-T05 set for
    # the test runner. The verdict is delegated, never recomputed here.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import codemap as _codemap
        cm = _codemap.check(ROOT)
        if not cm["exists"]:
            warn("codemap absent", "run `just codemap` — source has no retrieval index")
        elif cm["stale"]:
            fail("codemap out of date", cm["reason"] + " → run `just codemap`")
        else:
            ok("codemap matches the source tree")
    except Exception as e:  # noqa: BLE001
        warn("codemap check unavailable", str(e)[:80])

    # ── knownIssues per-issue index (PH27-T13) ─────────────────────────────
    # Same WARN/FAIL asymmetry as the codemap above, for the same reason: no
    # workspace has this until it opts in by running `just issues-index` once.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import issues_index as _issues_index
        ii = _issues_index.check(ROOT)
        if not ii["exists"]:
            warn("knownIssues index absent", "run `just issues-index` — no per-issue index yet")
        elif ii["stale"]:
            fail("knownIssues index out of date", ii["reason"])
        else:
            ok("knownIssues index matches knownIssues.md")
    except Exception as e:  # noqa: BLE001
        warn("knownIssues index check unavailable", str(e)[:80])

    # ── Phase ledger structure (PH19-T01) ─────────────────────────────────
    # Absent is silent (a workspace with no `## PHASE` headings at all — every
    # onboarded workspace except the kernel today — has nothing to check),
    # same asymmetry as the codemap/guide checks above: only a heading that
    # EXISTS and is malformed is a FAIL.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import phase_ledger as _phase_ledger
        pc = _phase_ledger.check(ROOT)
        if not pc["headings"]:
            pass  # nothing to check here
        elif pc["ok"]:
            ok(f"phase ledger structurally sound ({len(pc['headings'])} heading(s))")
        else:
            # Four independent defects share this check, so both the headline
            # and the detail are built from whichever actually fired — a fixed
            # headline reported "id collision" with empty detail whenever the
            # PH19-T04 conditions were the only ones tripped.
            n = len(pc["bad_headings"])
            parts, kinds = [], []
            if n:
                kinds.append(f"{n} bad heading(s)")
                parts.append(", ".join(f"line {b['line']} missing {'/'.join(b['missing'])}"
                                       for b in pc["bad_headings"][:3]))
            if pc["collisions"]:
                kinds.append("id collision")
                parts.append("id collision(s): "
                             + ", ".join(c["task"] for c in pc["collisions"]))
            if pc.get("unheaded_phases"):
                kinds.append(f"{len(pc['unheaded_phases'])} unheaded phase(s)")
                parts.append("tasks but no `## PHASE` heading: "
                             + ", ".join(f"PHASE {x}" for x in pc["unheaded_phases"]))
            if pc.get("misordered_headings"):
                kinds.append(f"{len(pc['misordered_headings'])} misordered heading(s)")
                parts.append("not ascending: "
                             + ", ".join(f"v{m['version']} after v{m['after']}"
                                         for m in pc["misordered_headings"][:3]))
            fail(f"phase ledger — {'; '.join(kinds)}", "; ".join(parts))
    except Exception as e:  # noqa: BLE001
        warn("phase ledger check unavailable", str(e)[:80])

    # ── Protocol line budget (PH19-T01 Slice 3) ───────────────────────────
    # AGENTS.md § CONTEXT BUDGET holds every memory file to <=200 lines and
    # was itself exempt from that rule -- this closes the self-exemption.
    # Same asymmetry as codemap/guide above: absence is not-applicable
    # (owned by the separate "root AGENTS.md" check), only an over-budget
    # file that EXISTS is a FAIL.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import protocol_budget as _protocol_budget
        pb = _protocol_budget.check(ROOT)
        if not pb["exists"]:
            pass  # nothing to check here
        elif pb["ok"]:
            ok(f"AGENTS.md within its own {pb['budget']}-line budget ({pb['lines']} lines) "
               f"and {pb['token_budget']:,}-token budget ({pb['tokens']:,} tokens)")
        else:
            # Two independent ceilings (PH24-T01) -- a file can blow either on
            # its own, so both are named rather than only whichever is checked
            # first, and FAILs (not warns): an unenforced ceiling is not a
            # ceiling, the same reasoning as the line-only check it extends.
            top = ", ".join(f"{s['title']} ({s['lines']} ln, {s['tokens']:,} tok)"
                             for s in pb["candidates"][:3])
            reasons = []
            if pb["over_by"]:
                reasons.append(f"{pb['lines']} lines, {pb['over_by']} over its "
                                f"{pb['budget']}-line budget")
            if pb["tokens_over_by"]:
                reasons.append(f"{pb['tokens']:,} tokens, {pb['tokens_over_by']:,} over its "
                                f"{pb['token_budget']:,}-token budget")
            fail("AGENTS.md over its own line/token budget",
                 "; ".join(reasons) + f" — skill-extraction candidates: {top}")
    except Exception as e:  # noqa: BLE001
        warn("protocol budget check unavailable", str(e)[:80])

    # ── Doc stamps (PH22-T07) ─────────────────────────────────────────────
    # Every doc states the OS version it is true for, when it last changed and
    # how many times -- all derived from git, so a disagreement means somebody
    # typed one. FAIL rather than warn: a stamp nothing enforces is exactly the
    # hand-maintained field this replaced.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import doc_stamp as _doc_stamp
        drift = _doc_stamp.check(ROOT)
        total = len(_doc_stamp.documents(ROOT))
        if not total:
            pass  # a workspace with no documents has nothing to stamp
        elif not drift:
            ok(f"doc stamps current — {total} doc(s) agree with git")
        else:
            named = ", ".join(d["path"] for d in drift[:3])
            fail(f"{len(drift)} of {total} doc(s) unstamped or drifted",
                 f"{named}{' …' if len(drift) > 3 else ''} — run `just doc-stamps --apply`")
    except Exception as e:  # noqa: BLE001
        warn("doc stamp check unavailable", str(e)[:80])

    # ── Usage guide (PH15-T02) ────────────────────────────────────────────
    # Same asymmetry as the codemap above, for the same reason: `doc/` is not
    # deployed to child workspaces, so absence is a WARN and only a guide that
    # DISAGREES with the justfile is a FAIL. The verdict is delegated.
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import guide_check as _guide
        g = _guide.check(ROOT)
        if not g["guide_present"]:
            warn("usage guide absent", f"{g['total']} recipe(s) described for no human")
        elif not g["ok"]:
            n = len(g["undocumented"]) + len(g["phantom"])
            fail("usage guide out of date",
                 f"{n} recipe(s) disagree with doc/USER_GUIDE.md → run `just guide-check`")
        else:
            ok(f"usage guide documents all {g['total']} recipes")
    except Exception as e:  # noqa: BLE001
        warn("usage guide check unavailable", str(e)[:80])

    # ── Tooling ───────────────────────────────────────────────────────────
    for tool, label, req in [("just", "just runner", True), ("git", "git", True),
                             ("tt", "tt CLI (TickTick reminders)", False),
                             ("pre-commit", "pre-commit", False)]:
        if shutil.which(tool):
            ok(f"{label} available")
        else:
            (fail if req else warn)(f"{label} not found")

    # ── Version + remote ──────────────────────────────────────────────────
    osv = ROOT / ".ai" / "os_version.json"
    if osv.exists():
        try:
            ok(f"OS version v{json.loads(osv.read_text()).get('version', '?')}")
        except Exception:  # noqa: BLE001
            warn("os_version.json unreadable")
    else:
        warn("os_version.json missing", "run god-upgrade to stamp")

    # PH16-T25 — the version STRING cannot express staleness: it is hand-edited at
    # phase boundaries and copied into every target verbatim, so a workspace fifteen
    # files behind reads identically to a current one. The digest compares content.
    #
    # WARN, never FAIL, and deliberately: every workspace in the fleet is stale right
    # now, so a FAIL here would close ~38 gates in one deploy — which is the very
    # failure this task exists to remove, wearing a different hat. Escalating is a
    # separate staged decision after the fleet is upgraded (cf. PH6-T21).
    try:
        sys.path.insert(0, str(ROOT / "scripts"))
        import deploy_digest as dd
        rep = dd.self_check(ROOT)
        if rep["status"] == "unstamped":
            warn("deployed digest not stamped",
                 "pre-PH16-T25 onboarding — run `god-upgrade .` to record what is deployed")
        elif rep["status"] == "clean":
            ok(f"deployed content matches its stamp ({rep['digest'][:12]}…)")
        else:
            gone = f", {len(rep['changed'])} missing" if rep["changed"] else ""
            warn(f"deployed content has drifted from its stamp{gone}",
                 "run `just deployed-digest` — then `god-upgrade .` to re-sync")
    except Exception as e:  # noqa: BLE001
        warn("deployed digest check unavailable", str(e)[:80])

    try:
        rem = subprocess.check_output(["git", "remote", "-v"], stderr=subprocess.DEVNULL, text=True).strip()
        (ok if rem else warn)("git remote configured")
    except Exception:  # noqa: BLE001
        warn("not a git repo")

    return checks


def counts(checks: list[Check]) -> tuple[int, int]:
    """(fails, warns) — the two numbers the session opening reports."""
    return (sum(1 for c in checks if c[0] == "FAIL"),
            sum(1 for c in checks if c[0] == "WARN"))


def render(checks: list[Check], root: Path | None = None) -> int:
    """Print the report; return the exit code (1 if anything FAILed)."""
    root = Path(root) if root is not None else ROOT
    icon = {"OK": "✅", "WARN": "⚠️ ", "FAIL": "❌"}
    print("\n" + "═" * 54)
    print(f"  🩺 GOD MODE DOCTOR — {root.name}")
    print("═" * 54)
    for level, label, detail in checks:
        line = f"  {icon[level]} {label}"
        if detail:
            line += f"  — {detail}"
        print(line)
    n_fail, n_warn = counts(checks)
    print("─" * 54)
    print(f"  {len(checks)} checks · {n_fail} fail · {n_warn} warn")
    if n_fail:
        print("  ❌ Not fully healthy — fix FAILs above (try: god-upgrade .)")
    elif n_warn:
        print("  ⚠️  Healthy with warnings.")
    else:
        print("  ✅ Fully dual-AI healthy.")
    print("═" * 54 + "\n")
    return 1 if n_fail else 0


def main() -> int:
    return render(run_checks(ROOT), ROOT)


if __name__ == "__main__":
    sys.exit(main())
