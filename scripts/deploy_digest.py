#!/usr/bin/env python3
"""Answer "am I running the current OS?" from content, not from a typed number.

PH16-T25. `.ai/os_version.json` could not express staleness. `onboard_project.sh`
Step 11 copies the kernel's stamp verbatim into the target, and the kernel's stamp is
hand-edited at phase boundaries, so the field records *when someone last typed a
version* — never *what was deployed*. Measured 2026-08-14: the kernel and `@jobscraper`
both read `3.5.0 / 2026-08-10T13:08:58Z`, byte-identical, while `@jobscraper` carried no
`scripts/finding.py` at all. "Current" and "fifteen files behind" were the same reading.

That silence is what pushed a product workspace into policing the kernel's *working
tree* — with no knowable release to compare against, the only available reference was
the kernel's live editing surface, so a kernel session mid-edit closed that workspace's
gate and the only lever inside it was prose justifying someone else's uncommitted work.

THE MANIFEST IS NOT WRITTEN DOWN ANYWHERE. It is emitted by `safe_copy` itself: set
`GOD_MANIFEST_OUT` and every call appends the pair it ships. A hand-kept list, or a
parser reading the shell, would drift the first time a file is added to the deployer and
not to the list — and "a rule implemented twice, drifted once" is precisely the defect
class this workspace exists to police. Glob loops, conditional branches and call sites
added next year are covered without anyone remembering they exist.

Usage:
    just deployed-digest                    # this workspace's digest
    just deployed-digest --compare <path>   # is that workspace current? exit 1 if not
    python3 scripts/deploy_digest.py --json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

EXIT_OK = 0
EXIT_STALE = 1
EXIT_ERROR = 2

#: A deploy that ships fewer files than this is not a deploy; it is a broken emitter
#: reporting fleet-wide agreement while knowing nothing. Well under the real count (94
#: on 2026-08-14) so ordinary growth or removal never trips it.
MIN_PLAUSIBLE_FILES = 50

_CACHE: dict[str, list[str]] = {}


class ManifestError(RuntimeError):
    """The deployer could not be asked what it ships."""


def kernel_root() -> Path:
    return Path(__file__).resolve().parents[1]


def manifest(root: Path | str | None = None) -> list[str]:
    """Destination-relative paths the deployer copies byte-for-byte.

    Excludes everything it *merges* (`justfile`, `.ai/policy.yaml`) or renders per
    workspace (`AGENTS.md` carries the identity block) — not by an exclusion list here,
    but because those never travel through `safe_copy` in the first place. Verified by
    `tests/test_deploy_digest.py`, so this comment cannot quietly become false.
    """
    base = Path(root).resolve() if root else kernel_root()
    key = str(base)
    if key in _CACHE:
        return _CACHE[key]

    script = base / "scripts" / "onboard_project.sh"
    if not script.is_file():
        raise ManifestError(f"no deployer at {script} — cannot ask what ships")

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "probe"
        target.mkdir()
        out = Path(tmp) / "manifest.tsv"
        env = {**os.environ, "GOD_MANIFEST_OUT": str(out)}
        proc = subprocess.run(
            ["bash", str(script), str(target), "python", "--upgrade", "--dry-run"],
            capture_output=True, text=True, env=env, cwd=str(base))

        if not out.exists():
            raise ManifestError(
                "the deployer emitted no manifest — GOD_MANIFEST_OUT is not honoured by "
                f"safe_copy (exit {proc.returncode}): {proc.stderr.strip()[:300]}")

        # `onboard_project.sh` resolves its target with `cd && pwd -P`, so the paths it
        # writes are the fully-resolved form; strip that, not the path we handed it.
        probe = str(target.resolve())
        paths = set()
        for line in out.read_text(encoding="utf-8").splitlines():
            if "\t" not in line:
                continue
            dst = line.split("\t", 1)[1].strip()
            if dst.startswith(probe):
                paths.add(dst[len(probe):].lstrip("/"))

    if len(paths) < MIN_PLAUSIBLE_FILES:
        raise ManifestError(
            f"the deployer reported only {len(paths)} shipped file(s), below the "
            f"{MIN_PLAUSIBLE_FILES} floor — treat as a broken emitter, not a small deploy")

    _CACHE[key] = sorted(paths)
    return _CACHE[key]


def compute(root: Path | str, paths: list[str] | None = None) -> dict:
    """Digest one workspace's copies of the shipped set.

    A missing file contributes a sentinel rather than being skipped, so a workspace
    lacking `finding.py` digests differently from one that has it — skipping would make
    absence invisible, which is the exact failure being fixed.
    """
    base = Path(root).resolve()
    rels = paths if paths is not None else manifest()

    h = hashlib.sha256()
    missing, present = [], 0
    for rel in sorted(rels):
        f = base / rel
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        if f.is_file():
            h.update(hashlib.sha256(f.read_bytes()).hexdigest().encode("ascii"))
            present += 1
        else:
            h.update(b"<absent>")
            missing.append(rel)
        h.update(b"\n")

    return {
        "digest": h.hexdigest(),
        "files": len(rels),
        "present": present,
        "missing": missing,
    }


def compare(workspace: Path | str, kernel: Path | str | None = None) -> dict:
    """Is `workspace` running what the kernel currently ships?"""
    k = Path(kernel).resolve() if kernel else kernel_root()
    paths = manifest(k)
    mine = compute(workspace, paths)
    theirs = compute(k, paths)
    return {
        "workspace": str(Path(workspace).resolve()),
        "kernel": str(k),
        "current": mine["digest"] == theirs["digest"],
        "digest": mine["digest"],
        "kernel_digest": theirs["digest"],
        "files": mine["files"],
        "present": mine["present"],
        "missing": mine["missing"],
    }


def stamp(workspace: Path | str, kernel: Path | str | None = None) -> dict:
    """Record into `<workspace>/.ai/os_version.json` what was actually deployed.

    Called by `onboard_project.sh` Step 11 and by nothing else. The field is written by
    the deploy, never by hand — the same rule `evidence.json` already has, and for the
    same reason: a number a human can type is a number that can be wrong.

    Writes through a temp file and `os.replace` so an interrupted deploy leaves the
    previous stamp intact rather than a truncated file every later read must survive.
    """
    ws = Path(workspace).resolve()
    target = ws / ".ai" / "os_version.json"
    if not target.is_file():
        raise ManifestError(f"no os_version.json at {target} — nothing to stamp")

    k = Path(kernel).resolve() if kernel else kernel_root()
    rep = compute(ws, manifest(k))

    doc = json.loads(target.read_text(encoding="utf-8"))
    doc["deployed_digest"] = rep["digest"]
    doc["deployed_at"] = _utc_now()
    # The file list travels WITH the stamp because the deployer is kernel-only: a
    # deployed workspace has no `onboard_project.sh` to ask what ships, so without this
    # it could never recompute its own digest and the check would be permanently
    # "unavailable" everywhere except here. Carrying the list lets any workspace answer
    # "has anything changed since I was deployed?" entirely on its own — which is the
    # question that would have caught `run_tests.py` being silently reverted by a sync.
    doc["deployed_files"] = list(manifest(k))

    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, target)
    return rep


def _utc_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stamp_doc(workspace: Path | str) -> dict:
    f = Path(workspace).resolve() / ".ai" / "os_version.json"
    if not f.is_file():
        return {}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def stamped_digest(workspace: Path | str) -> str | None:
    """What this workspace's last deploy recorded, or None if never stamped."""
    return _stamp_doc(workspace).get("deployed_digest")


def stamped_manifest(workspace: Path | str) -> list[str] | None:
    """The file list its last deploy shipped — readable without the kernel."""
    files = _stamp_doc(workspace).get("deployed_files")
    return list(files) if isinstance(files, list) and files else None


def self_check(workspace: Path | str) -> dict:
    """Has this workspace's deployed content changed since it was stamped?

    Answerable with no kernel present, which is the point: the failure it catches is a
    sync quietly reverting a local fix, and that is discovered by a workspace looking at
    itself. `@jobscraper` lost a day to exactly that — an uncommitted sync restored the
    kernel's older `run_tests.py`, the suite ran 54 of 500 tests, and the gate reported
    red for a suite that was green.
    """
    ws = Path(workspace).resolve()
    recorded, files = stamped_digest(ws), stamped_manifest(ws)
    if recorded is None or files is None:
        return {"status": "unstamped", "digest": None, "stamped": recorded, "changed": []}

    now = compute(ws, files)
    if now["digest"] == recorded:
        return {"status": "clean", "digest": now["digest"], "stamped": recorded, "changed": []}
    return {"status": "drifted", "digest": now["digest"], "stamped": recorded,
            "changed": now["missing"], "files": len(files)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--compare", metavar="PATH",
                    help="report whether that workspace matches the kernel; exit 1 if not")
    ap.add_argument("--root", metavar="PATH",
                    help="digest that workspace's own copies (no comparison, always exit 0)")
    ap.add_argument("--stamp", metavar="PATH",
                    help="write the digest into that workspace's os_version.json (deploy only)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.stamp:
            rep = stamp(args.stamp)
            if args.json:
                print(json.dumps(rep, indent=2))
            else:
                print(f"{rep['digest']}")
            return EXIT_OK

        if args.root:
            rep = compute(args.root, manifest())
            print(json.dumps(rep, indent=2) if args.json else rep["digest"])
            return EXIT_OK

        if args.compare:
            rep = compare(args.compare)
            if args.json:
                print(json.dumps(rep, indent=2))
            elif rep["current"]:
                print(f"✅ current — {rep['files']} shipped file(s) match the kernel")
                print(f"   digest {rep['digest'][:16]}…")
            else:
                print(f"⚠️  STALE — {Path(rep['workspace']).name} does not match the kernel")
                print(f"   workspace {rep['digest'][:16]}… · kernel {rep['kernel_digest'][:16]}…")
                if rep["missing"]:
                    shown = ", ".join(rep["missing"][:5])
                    more = f" (+{len(rep['missing'])-5} more)" if len(rep["missing"]) > 5 else ""
                    print(f"   missing {len(rep['missing'])} file(s): {shown}{more}")
                print("   Fix: run `god-upgrade .` from inside that workspace.")
            return EXIT_OK if rep["current"] else EXIT_STALE

        rep = compute(kernel_root(), manifest())
        if args.json:
            print(json.dumps(rep, indent=2))
        else:
            print(f"📦 deployed digest {rep['digest'][:16]}… "
                  f"({rep['present']}/{rep['files']} shipped file(s) present)")
        return EXIT_OK

    except ManifestError as exc:
        print(f"❌ {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
