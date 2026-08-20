#!/usr/bin/env python3
"""
validate_os.py — schema/policy validator for the God Mode AI OS gate.

Runs inside pre-commit and `just verify-safe`/`just validate`. No external
dependencies required (uses PyYAML only if present).

Checks:
  1. .ai/policy.yaml loads as valid YAML and has a top-level 'rules' key.
  2. .ai/memory-bank/evidence.json contains every key required by
     .ai/schemas/evidence.schema.json, with correct primitive types.
  3. none of the OS's own always-rewritten records is tracked by git (PH16-T10).

Exits non-zero on any failure so the validation gate blocks side effects.
"""

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import check_links  # noqa: E402
import policy_engine  # noqa: E402
import run_tests  # noqa: E402
import workspace_declare  # noqa: E402

# ── The OS's own generated records (PH16-T10) ────────────────────────────────
# Written by God Mode tooling on every run, never by a human, and per-machine.
# Tracking one is not untidiness, it is a gate that can never open: the
# `godmode-tests` pre-commit hook is `always_run: true` and rewrites the test
# record on every invocation, and pre-commit reports ANY hook that mutates a
# *tracked* file as `Failed` regardless of exit code. Measured live 2026-08-12
# in 4 workspaces whose suites passed while `verify-safe` recorded FAILURE.
#
# `.gitignore` alone cannot fix this: git keeps tracking a file it already
# tracks. So the deployer ships the ignore entry (onboard_project.sh Step 7,
# derived from the kernel's own .gitignore) and this check catches the
# already-committed case the entry arrives too late for.
#
# The test record's path comes from the module that writes it rather than a
# second copy of the string — a restated list is the defect this fixes.
GENERATED_RECORDS = (
    run_tests.RECORD_PATH,                        # .ai/memory-bank/test-run.json
    run_tests.LOG_PATH,                           # .ai/last-test-run.log (PH24-T08)
    ".ai/session-state.json",                     # the live session budget
    ".ai/memory-bank/ticktick_sync_state.json",   # what was already pushed
)


def tracked_generated_records(root: Path | str = ".") -> list[str]:
    """Which generated records this workspace tracks — one message each.

    Read from git's own index (`ls-files`), never from `.gitignore`: the whole
    point is the state where the file is ignored *and* tracked. A directory
    that is not a git repo, or a record that does not exist, reports nothing —
    absence is not a defect.
    """
    root = Path(root)
    found: list[str] = []
    for rec in GENERATED_RECORDS:
        try:
            proc = subprocess.run(
                ["git", "-C", str(root), "ls-files", "--error-unmatch", rec],
                capture_output=True, text=True)
        except Exception:  # noqa: BLE001 — no git binary: nothing to report
            return found
        if proc.returncode == 0:
            found.append(
                f"{rec} is tracked by git, and God Mode tooling rewrites it on every "
                "run — pre-commit reports the hook that rewrites it as Failed for "
                "mutating a tracked file, so `verify-safe` records FAILURE however the "
                f"tests did. Remedy: `git rm --cached {rec}` (the file stays on disk).")
    return found


def _ws_root() -> Path:
    import subprocess
    try:
        top = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:
        pass
    return Path(".").resolve()


def _validate_policy_structure(data: dict) -> list[str]:
    """Structural check against policy.schema.json shape (dependency-light —
    no jsonschema lib). Ensures the engine can trust what it reads."""
    errs: list[str] = []
    if not isinstance(data, dict) or "rules" not in data:
        return ["policy.yaml: missing top-level 'rules' key"]
    fs = (data.get("rules") or {}).get("filesystem") or {}
    for section in ("autonomous_allowed", "approval_required", "always_blocked"):
        entries = fs.get(section)
        if entries is None:
            continue
        if not isinstance(entries, list):
            errs.append(f"policy.yaml: rules.filesystem.{section} must be a list")
            continue
        for idx, entry in enumerate(entries):
            if not isinstance(entry, dict) or "paths" not in entry:
                errs.append(f"policy.yaml: {section}[{idx}] missing 'paths'")
                continue
            if not isinstance(entry["paths"], list) or not entry["paths"]:
                errs.append(f"policy.yaml: {section}[{idx}].paths must be a non-empty list")
            if "operations" in entry and not isinstance(entry["operations"], list):
                errs.append(f"policy.yaml: {section}[{idx}].operations must be a list")
    return errs


def main() -> int:
    root = _ws_root()
    errors: list[str] = []

    # 1. policy.yaml — parse (yaml or bundled reader) + structural validation
    pol = root / ".ai" / "policy.yaml"
    if pol.exists():
        try:
            data = policy_engine.load_policy(pol)
            errors.extend(_validate_policy_structure(data))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"policy.yaml: {exc}")

    # 1b. memory retrieval graph — no dangling [[links]]
    errors.extend(f"link: {e}" for e in check_links.validate(root, check_links.default_scan(root)))

    # 2. evidence.json vs schema
    ev = root / ".ai" / "memory-bank" / "evidence.json"
    sc = root / ".ai" / "schemas" / "evidence.schema.json"
    if ev.exists() and sc.exists():
        try:
            e = json.loads(ev.read_text())
            s = json.loads(sc.read_text())
        except Exception as exc:  # noqa: BLE001
            errors.append(f"evidence/schema JSON parse: {exc}")
        else:
            for key in s.get("required", []):
                if key not in e:
                    errors.append(f"evidence.json: missing required field '{key}'")
            type_map = {"exit_code": int, "status": str, "task_id": str,
                        "validated_at": str, "timestamp": str}
            for k, t in type_map.items():
                if k in e and not isinstance(e[k], t):
                    errors.append(f"evidence.json: '{k}' must be {t.__name__}")

    # 3. workspace.yaml — absent is "undeclared" (not a failure); present and
    #    malformed is a real defect, same asymmetry as policy.yaml above.
    wc = workspace_declare.check(root)
    if not wc["ok"]:
        errors.extend(wc["errors"])

    # 4. a generated record in git's index is a gate that can never open
    errors.extend(f"tracked record: {e}" for e in tracked_generated_records(root))

    if errors:
        print("❌ OS validation failed:")
        for er in errors:
            print(f"   - {er}")
        return 1
    print("✅ OS validation passed (policy.yaml structure + evidence schema + memory links + "
          "workspace.yaml + no tracked generated records)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
