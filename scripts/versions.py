#!/usr/bin/env python3
"""
versions.py — a version names the working features it ships, in language he can read (PH22-T02).

`.ai/plan.md` G20: *"...with plain-English feature-to-version contracts agreed
before work begins."*

`version_plan.py` (PH9-T03) stopped a version number being **asserted** — it
derives a proposal from the plan's goals rather than letting an agent type one.
But a goal is an *outcome* ("every workspace has a plan its owner wrote"), not a
feature list. So nothing anywhere states which working features belong to
`v0.1.0`: two people reading that number picture different software, no request
can be told it belongs to a later rung because later rungs have no contents, and
scope creep has nothing to violate.

## The bilingual rule, and why the parser is shaped around it

The operator's requirement, given 2026-08-15 (e) while accepting PH22-T01's own
brief:

    "Plain english is needed in every workspaace with tech language because half
     the time i dont even know what ai is building or what its doing."

A feature list written only in technical language is not a contract he agreed
to — it is one he was shown. So a feature carries **both** halves, and the
plain-English half is *the checkbox bullet's own text*, with the technical detail
demoted to a `tech:` sub-bullet. That shape is deliberate: the readable line is
the thing the parser keys on, so it cannot be the thing that gets omitted when
someone is in a hurry. Writing it the other way round — technical bullet, plain
sub-bullet — would make his half the optional one.

What no parser can catch: a `tech:` line and a bullet that are *both* jargon.
`--check`'s message says what the plain line is for; the `prework-brief` skill
carries the instruction. Stated here rather than pretended solved.

## Nothing is verified by a boolean

There is no `verified: true` to set. Every form is evaluated at read time:

* `verify: test <ref>`      — asked of `run_tests.collects()`, the real runner
* `verify: artefact <path>` — the file is stat'd
* `verify: attest <date> "<what>" expires <N>d` — compared against today

A self-declared completion flag is the single most common defect in this repo
(`evidence.json`, `knownIssues.md` and PH15's whole phase exist because of it),
and a contract that could be satisfied by editing one word would inherit it.

The attestation is the weakest of the three and is knowingly so: for non-software
rungs — 20 proposals sent, a document delivered — his word is the only evidence
that exists. Making it **expire** is the one honest control available, so stale
proof cannot keep a rung marked done indefinitely.

## Absence is honest; a hollow contract is not

No workspace has a contract yet, and writing rungs on his behalf is exactly what
this task forbids — the contract's whole value is that he agreed to it first. So
a **missing** `.ai/versions.md` is not an error. A file that exists and declares
a rung with no features **is**: that is a contract shaped like agreement with
nothing in it.

## The kernel keeps its own ladder — and now also says what each rung ships

`version_plan.is_kernel()` already distinguishes the kernel, whose rungs are
owned by `.ai/os_version.json` + `phase_ledger.next_stampable()` (PH19-T01). A
second mechanism here would give this repo two answers to "what version is it",
which is the drift `doc/VERSIONING.md` was written to end.

**PH22-T08 narrowed that exemption instead of lifting it.** The original form —
skip the kernel entirely — meant the kernel could never adopt the thing it tells
43 workspaces to adopt, and it did not: this file's engine shipped with zero
users, in every workspace including this one. So the kernel may now hold a
contract, under one constraint that keeps the original reasoning intact:

    the contract states what each rung SHIPS.
    it never states what version the repo IS.

`.ai/os_version.json` remains the only authority for the number, and a rung named
in the kernel's contract that is not on that ladder is refused **by name**
(`ladder_rungs()`). One ladder, with a readable view added — enforced by a check
rather than by an exemption, because an exemption is what produced the shelfware.

The cross-check is **kernel-only**, deliberately. A product workspace's
`os_version.json` records which *kernel* version is deployed there, not its own
product version; checking `v0.1.0` against that would refuse every real contract
on the fleet.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

CONTRACT = ".ai/versions.md"

RUNG_RE = re.compile(r"^##\s+(?P<name>\S+)\s*(?:—|-|–)?\s*(?P<title>[^(]*?)\s*"
                     r"(?:\((?P<status>[^)]*)\))?\s*$")
FEATURE_RE = re.compile(r"^\s*-\s*\[(?P<mark>[ xX])\]\s*(?P<text>.+?)\s*$")
SUB_RE = re.compile(r"^\s+-\s*(?P<key>tech|verify)\s*:\s*(?P<val>.+?)\s*$", re.I)
VALUE_RE = re.compile(r"^\s*\*\*Value:\*\*\s*(?P<val>.+?)\s*$", re.I)
ATTEST_RE = re.compile(r'^attest\s+(?P<date>\d{4}-\d{2}-\d{2})\s*'
                       r'(?:"(?P<what>[^"]*)")?\s*(?:expires\s+(?P<days>\d+)\s*d)?\s*$', re.I)

# A bullet whose readable half is really just a command line or a path. Not a
# jargon detector — no parser can be one — only the unambiguous cases: text that
# is entirely code-spanned, or has no lowercase prose word at all.
_CODE_ONLY = re.compile(r"^\s*`[^`]*`\s*$")
_PROSE_WORD = re.compile(r"\b[a-z]{3,}\b")

DEFAULT_EXPIRY_DAYS = 90


def ws_root() -> Path:
    here = Path.cwd().resolve()
    for cand in (here, *here.parents):
        if (cand / ".ai").is_dir():
            return cand
    return here


def contract_path(root: Path | None = None) -> Path:
    return (root or ws_root()) / CONTRACT


def is_kernel(root: Path) -> bool:
    """Delegated to the module that owns the question (`version_plan.py`)."""
    try:
        import version_plan
        return version_plan.is_kernel(root)
    except Exception:  # noqa: BLE001
        return (root / "scripts" / "onboard_project.sh").is_file()


# --------------------------------------------------------- the kernel ladder


def _norm(v: str) -> str:
    """`3.6.0` and `v3.6.0` are the same rung written two ways."""
    return "v" + v.strip().lstrip("vV").strip()


def ladder_rungs(root: Path | None = None) -> set[str]:
    """Every version the kernel's OS ladder knows about, read from the ladder.

    Two sources, both already authoritative and neither re-listed here:
    `.ai/os_version.json`'s current stamp plus its `changelog` (what has
    shipped), and the phase ledger's heading targets (what is planned). A
    contract may legally name the rung currently being built, which by
    definition is not yet in the changelog — that is what the second source is
    for.

    Hardcoding the set instead would be one more copy of the ladder to keep
    true, and it would go stale at the very next stamp.
    """
    root = root or ws_root()
    out: set[str] = set()

    try:
        import json
        data = json.loads((root / ".ai" / "os_version.json").read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a missing ladder is not a crash; it is an empty set
        data = {}
    if isinstance(data.get("version"), str):
        out.add(_norm(data["version"]))
    for line in data.get("changelog") or []:
        m = re.match(r"^\s*v?(\d+\.\d+\.\d+)", str(line))
        if m:
            out.add(_norm(m.group(1)))

    try:
        import phase_ledger
        for h in phase_ledger.parse_headings(root=root):
            if h.get("version"):
                out.add(_norm(h["version"]))
    except Exception:  # noqa: BLE001 — same rule: absent ledger, fewer rungs, no crash
        pass

    return out


def unknown_rungs(doc: dict, root: Path) -> list[str]:
    """Rungs the contract names that the OS ladder does not. Kernel-only."""
    known = ladder_rungs(root)
    if not known:
        # No readable ladder means nothing to check against — refusing every
        # rung here would report a missing ladder as a bad contract, blaming
        # the wrong file.
        return []
    return [r["name"] for r in doc["rungs"] if _norm(r["name"]) not in known]


# ------------------------------------------------------------------- parsing


def _front_matter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end == -1:
        return {}
    out = {}
    for line in text[3:end].splitlines():
        if ":" in line:
            k, _, v = line.partition(":")
            out[k.strip()] = v.strip()
    return out


def parse(root: Path | None = None) -> dict:
    """Read `.ai/versions.md` into rungs and features. Pure read."""
    root = root or ws_root()
    path = contract_path(root)
    doc = {"exists": path.is_file(), "path": str(path), "meta": {}, "rungs": []}
    if not doc["exists"]:
        return doc

    text = path.read_text(encoding="utf-8", errors="replace")
    doc["meta"] = _front_matter(text)

    rung = None
    feature = None
    for raw in text.splitlines():
        m = RUNG_RE.match(raw)
        if m and not raw.startswith("###"):
            rung = {"name": m.group("name"), "title": (m.group("title") or "").strip(),
                    "status": (m.group("status") or "").strip(), "value": "",
                    "features": []}
            doc["rungs"].append(rung)
            feature = None
            continue
        if rung is None:
            continue
        mv = VALUE_RE.match(raw)
        if mv:
            rung["value"] = mv.group("val")
            continue
        mf = FEATURE_RE.match(raw)
        if mf:
            feature = {"plain": mf.group("text"), "done_mark": mf.group("mark").lower() == "x",
                       "tech": "", "verify": "", "verified": False,
                       "state": "unverified", "detail": ""}
            rung["features"].append(feature)
            continue
        ms = SUB_RE.match(raw)
        if ms and feature is not None:
            feature[ms.group("key").lower()] = ms.group("val")
    return doc


# -------------------------------------------------------------- verification


def _verify_test(ref: str, root: Path) -> tuple[bool, str, str]:
    try:
        import run_tests
        cr = run_tests.collects(ref, root=root)
    except Exception as exc:  # noqa: BLE001
        return False, "unverified", f"test runner unavailable ({exc})"
    if cr.get("collected"):
        note = "" if cr.get("verified", True) else " (runner could not confirm without running)"
        return True, "verified", f"test collects{note}"
    return False, "unverified", f"`{ref}` is not collected by the test runner"


def _verify_attest(spec: str, root: Path) -> tuple[bool, str, str]:
    m = ATTEST_RE.match(spec)
    if not m:
        return False, "unverified", ('attestation must read: attest YYYY-MM-DD "what" '
                                     'expires <N>d')
    try:
        when = datetime.strptime(m.group("date"), "%Y-%m-%d").date()
    except ValueError:
        return False, "unverified", f"unreadable date `{m.group('date')}`"
    days = int(m.group("days") or DEFAULT_EXPIRY_DAYS)
    age = (date.today() - when).days
    if age < 0:
        return False, "unverified", f"attested {abs(age)} day(s) in the future"
    if age > days:
        return False, "stale", (f"attested {age} days ago, expires after {days} days — "
                                "re-attest it or prove it another way")
    return True, "verified", f"attested {age} day(s) ago, valid for {days}"


def _verify_artefact(rel: str, root: Path) -> tuple[bool, str, str]:
    p = (root / rel).expanduser()
    if p.exists():
        return True, "verified", f"`{rel}` exists"
    return False, "unverified", f"`{rel}` does not exist"


def evaluate(feature: dict, root: Path) -> dict:
    """Resolve one feature's verification. Never trusts a stored boolean."""
    spec = (feature.get("verify") or "").strip()
    if not spec:
        feature.update(verified=False, state="unverified", detail="no `verify:` declared")
        return feature
    low = spec.lower()
    if low.startswith("test "):
        ok, state, detail = _verify_test(spec[5:].strip(), root)
    elif low.startswith("artefact ") or low.startswith("artifact "):
        ok, state, detail = _verify_artefact(spec.split(None, 1)[1].strip(), root)
    elif low.startswith("attest"):
        ok, state, detail = _verify_attest(spec, root)
    else:
        ok, state, detail = False, "unverified", (
            f"unrecognised verification `{spec[:40]}` — use test / artefact / attest")
    feature.update(verified=ok, state=state, detail=detail)
    return feature


def _resolved(root: Path) -> dict:
    doc = parse(root)
    for rung in doc["rungs"]:
        for f in rung["features"]:
            evaluate(f, root)
    return doc


def current_rung(root: Path | None = None) -> dict | None:
    """The rung named by front-matter `current:`, else the first unfinished one."""
    root = root or ws_root()
    doc = _resolved(root)
    if not doc["rungs"]:
        return None
    want = doc["meta"].get("current", "")
    for rung in doc["rungs"]:
        if want and rung["name"] == want:
            return rung
    for rung in doc["rungs"]:
        if not all(f["verified"] for f in rung["features"]):
            return rung
    return doc["rungs"][-1]


# -------------------------------------------------------------- validation


def _plain_half_missing(text: str) -> bool:
    """Is the checkbox bullet's own text a technical fragment rather than prose?"""
    t = text.strip()
    if _CODE_ONLY.match(t):
        return True
    return not _PROSE_WORD.search(re.sub(r"`[^`]*`", " ", t))


def validate(root: Path | None = None) -> dict:
    """Is this workspace's version contract real and readable? Pure read."""
    root = root or ws_root()
    path = contract_path(root)
    v = {"ok": False, "exists": path.is_file(), "kernel": is_kernel(root),
         "path": str(path), "rungs": 0, "reason": ""}

    # The kernel with NO contract is the PH22-T02 case and is answered the same
    # way: os_version.json is the whole answer, and saying which file owns the
    # number is more useful than a bare pass. A kernel contract that *exists* is
    # read like any other, plus the ladder cross-check below (PH22-T08).
    if v["kernel"] and not v["exists"]:
        v["ok"] = True
        v["reason"] = ("kernel: `.ai/os_version.json` owns this workspace's ladder "
                       "(phase_ledger.next_stampable, PH19-T01) — `.ai/versions.md` here "
                       "may state what each rung SHIPS, never what version the repo IS.")
        return v

    # Absence is honest. Writing rungs on his behalf is what this task forbids,
    # so "not agreed yet" must be a legal state, distinct from "agreed to nothing".
    if not v["exists"]:
        v["ok"] = True
        v["reason"] = (f"no {CONTRACT} yet — nothing is claimed. Run `just versions --scaffold`, "
                       "then agree the rungs with the operator before building them.")
        return v

    # `parse`, not `_resolved`: nothing below reads a feature's verification
    # *result*, only whether one was declared. Evaluating them here ran the test
    # collector once per feature — invisible in one workspace, and the reason a
    # 44-workspace roll-up could not afford to call this at all (PH22-T08).
    doc = parse(root)
    v["rungs"] = len(doc["rungs"])
    if not doc["rungs"]:
        v["reason"] = f"{CONTRACT} declares no rungs — a `## v0.1.0 — <title>` heading starts one."
        return v

    for rung in doc["rungs"]:
        if not rung["features"]:
            v["reason"] = (f"rung `{rung['name']}` declares no features. A version with no "
                           "features promises nothing and cannot be agreed to.")
            return v
        for f in rung["features"]:
            if _plain_half_missing(f["plain"]):
                v["reason"] = (
                    f"rung `{rung['name']}`: a feature's checkbox line is technical text "
                    f"(`{f['plain'][:48]}`) with no plain-English description. The bullet is "
                    "the half the operator reads — say what it DOES, and put what it IS on "
                    "the `tech:` line. A contract he cannot read is one he was shown, not "
                    "one he agreed to.")
                return v
            if not f["tech"].strip():
                v["reason"] = (f"rung `{rung['name']}`: feature `{f['plain'][:40]}` has no "
                               "`tech:` line. Both halves are required.")
                return v
            if not f["verify"].strip():
                v["reason"] = (f"rung `{rung['name']}`: feature `{f['plain'][:40]}` names no "
                               "`verify:` — a test, an artefact, or a dated attestation. "
                               "Nothing here is verified by assertion.")
                return v

    # Kernel-only: the contract may describe the ladder's rungs, never invent one.
    if v["kernel"]:
        stray = unknown_rungs(doc, root)
        if stray:
            v["reason"] = (
                f"rung(s) {', '.join(f'`{s}`' for s in stray)} are not on this kernel's "
                "ladder. `.ai/os_version.json` (plus the phase ledger's targets) is the "
                "only authority for which versions exist here — this file says what a "
                "rung SHIPS, it does not get to declare a new one. Name a rung the "
                "ladder already knows, or stamp it there first.")
            return v

    v["ok"] = True
    n = sum(len(r["features"]) for r in doc["rungs"])
    v["reason"] = f"{v['rungs']} rung(s), {n} feature(s), each readable and each with a check."
    return v


def bump_blocked(root: Path | None = None) -> dict:
    """Is a version bump refused because the current rung is not finished?

    No longer kernel-exempt (PH22-T08). The exemption was harmless while the
    kernel could not hold a contract; now that it can, keeping it would make
    `render()` print "nothing blocking" over a rung with an unproven feature —
    the OS lying about itself, which is the defect class this repo exists to
    police. A kernel with no contract still passes straight through, so nothing
    changes for a workspace that has not had the conversation.

    This tightens `version_plan`'s stamp path, and deliberately so: for the
    kernel it now agrees with `phase_ledger`'s own exit condition rather than
    adding a second, different reason to refuse.
    """
    root = root or ws_root()
    out = {"blocked": False, "rung": "", "unverified": [], "reason": ""}
    if not contract_path(root).is_file():
        return out
    rung = current_rung(root)
    if not rung:
        return out
    out["rung"] = rung["name"]
    pending = [f for f in rung["features"] if not f["verified"]]
    if not pending:
        out["reason"] = f"every feature in `{rung['name']}` is verified — the rung may be stamped."
        return out
    out["blocked"] = True
    out["unverified"] = [f["plain"] for f in pending]
    listed = "; ".join(f'{f["plain"]} ({f["state"]}: {f["detail"]})' for f in pending[:4])
    more = "" if len(pending) <= 4 else f" …and {len(pending) - 4} more"
    out["reason"] = (f"`{rung['name']}` still has {len(pending)} unverified feature(s): "
                     f"{listed}{more}")
    return out


# --------------------------------------------------------------- scaffold/cli


SCAFFOLD = """---
scheme: semver
current: v0.1.0
---

# Version contract — <workspace>

<!-- Agree these rungs with the operator BEFORE building them. Every feature is
     written twice: the checkbox line says what it DOES in plain English (this is
     the half he reads), the `tech:` line says what it IS. Every feature names how
     it will be proved:
       verify: test tests/test_x.py::Class::test_name
       verify: artefact path/to/output.json
       verify: attest 2026-08-15 "what you did" expires 90d
     Use `Edition 1` instead of `v0.1.0` for non-software workspaces. -->

## v0.1.0 — <the smallest thing that actually works> (in-progress)

**Value:** <what this rung lets you do that you cannot do today>

- [ ] <what it does, in plain English>
  - tech: <what it is — files, modules, approach>
  - verify: <test | artefact | attest>

## v0.2.0 — <the next rung> (planned)

**Value:** <what that one unlocks>

- [ ] <what it does, in plain English>
  - tech: <what it is>
  - verify: <test | artefact | attest>
"""


def scaffold(root: Path | None = None) -> Path:
    root = root or ws_root()
    path = contract_path(root)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SCAFFOLD, encoding="utf-8")
    return path


def render(root: Path) -> str:
    v = validate(root=root)
    if not v["exists"]:
        # Covers the kernel-without-a-contract case too, whose reason names
        # `os_version.json` as the owner of the number.
        return ("ℹ️  " if v["kernel"] else "📭 ") + v["reason"]
    if not v["ok"]:
        return f"❌ {v['reason']}"
    rung = current_rung(root)
    lines = [f"📦 current rung: {rung['name']} — {rung['title']}" if rung else "📦 no rung"]
    if rung:
        if rung["value"]:
            lines.append(f"   value: {rung['value']}")
        for f in rung["features"]:
            icon = {"verified": "✅", "stale": "⌛", "unverified": "⬜"}[f["state"]]
            lines.append(f"   {icon} {f['plain']}")
            lines.append(f"      {f['detail']}")
        done = sum(1 for f in rung["features"] if f["verified"])
        lines.append(f"   {done}/{len(rung['features'])} verified")
    b = bump_blocked(root=root)
    lines.append(("🚧 bump refused — " + b["reason"]) if b["blocked"]
                 else ("🟢 " + (b["reason"] or "nothing blocking")))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="the workspace's feature-to-version contract")
    ap.add_argument("--check", action="store_true", help="validate only; exit 1 if not ok")
    ap.add_argument("--scaffold", action="store_true", help="write a contract template")
    args = ap.parse_args()
    root = ws_root()

    if args.scaffold:
        path = scaffold(root)
        if is_kernel(root):
            print(f"📝 {path.relative_to(root)} — kernel: name only rungs that already exist on "
                  "`.ai/os_version.json`'s ladder. This file says what a rung SHIPS; it does "
                  "not declare a version.")
            return 0
        print(f"📝 {path.relative_to(root)} — agree the rungs with the operator before building.")
        return 0

    v = validate(root=root)
    if args.check:
        print(("✅ " if v["ok"] else "❌ ") + v["reason"])
        return 0 if v["ok"] else 1

    print(render(root))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
