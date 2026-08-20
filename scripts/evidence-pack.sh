#!/bin/bash
# evidence-pack.sh — verification evidence generator for the God Mode AI OS.
#
# Usage: evidence-pack.sh [pipeline_type] [exit_code] [precommit_log] [tests_log]
#   pipeline_type : "safe" (default) | "release" | "onboarding"
#   exit_code     : exit status of the preceding validation step (default 0)
#   precommit_log : captured output of the pre-commit stage (optional, PH16-T35)
#   tests_log     : captured output of the test stage (optional, PH16-T35)
#
# Emits .ai/memory-bank/evidence.json using the schema the validation gate
# (.agents/rules/00-validation-gate.md + skills/validation-gate) actually checks:
#   status · exit_code · task_id · validated_at (+ timestamp alias) · commit · pipeline · spec_hashes
#
# Steps:
#   1. Resolve workspace root from git (cwd-safe)
#   2. Validate workspace bootstrap gate (all required memory files present)
#   3. Derive the active task_id from activeContext.md (for gate task-match)
#   4. Compute spec hashes (prd.md, roadmap.md) for freshness locking
#   5. Write evidence.json reflecting the real exit code
#   6. On PASS only: delegate TickTick sync to ticktick_sync.py

set -uo pipefail

PIPELINE_TYPE=${1:-"safe"}
EXIT_CODE=${2:-0}
PRECOMMIT_LOG=${3:-""}
TESTS_LOG=${4:-""}
TIMESTAMP=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

# ── 1. cwd-safe workspace root ─────────────────────────────────────────────────
ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
cd "$ROOT" || exit 1

COMMIT_HASH=$(git rev-parse --short HEAD 2>/dev/null || echo "pre-commit")

mkdir -p .ai/memory-bank .ai/session-log .ai/decision-log

# PH16-T35: the cause fragment spliced into a FAILED evidence file — `failed_stage`
# and `failed_checks`. Empty for a passing run, which has nothing to explain.
#
# Delegated to scripts/failure_digest.py, never parsed here: PH16-T32 removed this
# file's own `grep` copy of the declaration rule precisely because a shell-side second
# implementation made the "one implementation" claim false in the file that writes the
# evidence. The same mistake is not re-made one field over.
CAUSE_FRAGMENT=""
compute_cause() {
  # args: status. A PASSING run has nothing to explain. The REPAIR_REQUIRED path is the
  # case that makes this a status test and not an exit-code test: it writes status=failed
  # with EXIT_CODE=0, and keying off the exit code alone left it with no `failed_stage` —
  # which `gate_check.failure_cause` would then report as "written by an older pack",
  # blaming the reader's version for a cause this pack knew perfectly well.
  [ "$1" = "passed" ] && return 0
  if [ "$EXIT_CODE" -eq 0 ]; then
    CAUSE_FRAGMENT=$',\n  "failed_stage": "workspace",\n  "failed_checks": ["memory bank"]'
    return 0
  fi
  if [ -f "scripts/failure_digest.py" ] && command -v python3 >/dev/null 2>&1; then
    CAUSE_FRAGMENT=$(python3 scripts/failure_digest.py \
        --precommit "$PRECOMMIT_LOG" --tests "$TESTS_LOG" 2>/dev/null \
      | python3 -c 'import json,sys
try:
    d = json.load(sys.stdin)
except Exception:
    d = {}
if not d:
    sys.exit(0)
out = [("failed_stage", d.get("failed_stage", "unknown")),
       ("failed_checks", d.get("failed_checks", []))]
# PH24-T13 — the reason travels with the name. Omitted entirely when empty, so an
# older runner that prints no REASON lines leaves no misleading empty object behind.
if d.get("failed_reasons"):
    out.append(("failed_reasons", d["failed_reasons"]))
if d.get("note"):
    out.append(("failure_note", d["note"]))
print("".join(",\n  %s: %s" % (json.dumps(k), json.dumps(v, ensure_ascii=False))
                for k, v in out))' 2>/dev/null)
  fi
  if [ -z "$CAUSE_FRAGMENT" ]; then
    # Named, never silent — and never "no cause, therefore fine". A diagnostic that
    # cannot run leaves the run failing and says the cause is unknown.
    CAUSE_FRAGMENT=$',\n  "failed_stage": "unknown",\n  "failed_checks": []'
    echo "⚠️  scripts/failure_digest.py unavailable — the failing stage could NOT be named."
    echo "   Evidence records the failure with failed_stage=unknown; the gate stays closed."
  fi
}

write_evidence() {
  # args: status  message
  local STATUS="$1" MSG="$2"
  compute_cause "$STATUS"
  cat > .ai/memory-bank/evidence.json <<EOF
{
  "timestamp": "$TIMESTAMP",
  "validated_at": "$TIMESTAMP",
  "pipeline": "$PIPELINE_TYPE",
  "commit": "$COMMIT_HASH",
  "status": "$STATUS",
  "exit_code": $EXIT_CODE,
  "task_id": "$TASK_ID",
  "message": "$MSG"$CAUSE_FRAGMENT,
  "spec_hashes": {
    "prd.md":     { "hash": "$PRD_HASH",     "verified_at": "$TIMESTAMP" },
    "roadmap.md": { "hash": "$ROADMAP_HASH", "verified_at": "$TIMESTAMP" }
  }
}
EOF
}

# ── 3. Active task id (for gate task-match) ───────────────────────────────────
# ONLY an explicitly (In Progress) task. Empty means "maintenance run, covering no
# specific task" — which is the honest answer when nobody declared one.
#
# The id must come from a task DECLARATION — a list bullet whose content starts
# with the id — never from prose that merely mentions one. Two forgeries were
# found here on 2026-08-01, both the same confusion at different scopes:
#
#   1. File scope: a fallback to `grep -oE 'PH[0-9]+-T[0-9]+' … | head -1` took
#      the first id ANYWHERE in the file, and stamped evidence "PH5-T03" because
#      the "Last Session:" summary paragraph mentioned it.
#   2. Line scope: filtering to lines containing "(In Progress)" then taking the
#      first id on them still matched prose — a session summary describing the
#      workflow ("declare (In Progress) → verify-safe → claim") contains both the
#      marker and a task id, so the narrative about the rule forged evidence for
#      the rule.
#
# Both were harmless while nothing read task_id, and became forged credentials
# the moment PH7-T02 made work credit depend on it. Empty is the honest answer:
# it means "maintenance run, covering no specific task".
#
# PH16-T32 — this DELEGATES to `task_ledger.py --active` instead of implementing
# the rule again in `grep`. It carried its own copy until 2026-08-15, which made
# `task_ledger`'s "the one implementation of this rule" untrue in the one place
# that actually writes `task_id`: a fix to the Python rule would not have reached
# the file it governs. The Python side also names an orphaned marker — a
# declaration whose `(In Progress)` sits on the next line, binding nothing — on
# stderr, so it is reported HERE, before ~80s of verification is spent on evidence
# that covers no task, rather than at credit time when it cannot be acted on.
TASK_ID=""
if [ -f ".ai/memory-bank/activeContext.md" ]; then
  if [ -f "scripts/task_ledger.py" ] && command -v python3 >/dev/null 2>&1; then
    TASK_ID=$(python3 scripts/task_ledger.py --active || true)
  else
    # Named, never silent: a partial deploy must not look like "no task declared".
    echo "⚠️  scripts/task_ledger.py unavailable — the active task could NOT be resolved."
    echo "   Evidence is recorded as a maintenance run (task_id=none); work credit will refuse it."
    echo "   Run 'just fleet-upgrade --apply' from the kernel to fix this."
  fi
fi

# ── 4. Spec freshness hashes (computed early so failure evidence has them too) ─
PRD_HASH=""
ROADMAP_HASH=""
if [ -f ".ai/docs/prd.md" ]; then
  PRD_HASH=$(sed 's/[[:space:]]*$//' .ai/docs/prd.md | shasum -a 256 | cut -d' ' -f1)
fi
if [ -f ".ai/docs/roadmap.md" ]; then
  ROADMAP_HASH=$(sed 's/[[:space:]]*$//' .ai/docs/roadmap.md | shasum -a 256 | cut -d' ' -f1)
fi

# ── 2. Workspace Bootstrap Gate ────────────────────────────────────────────────
# PH24-T19: resolved through role_registry.required_memory() — the SAME one source
# doctor.py and session_start.py already call — instead of a fourth literal copy
# here. A role pack that PROHIBITS a file (e.g. executive-coach prohibits
# techContext.md) must not be told that file is REQUIRED; the old literal list did
# exactly that, and deadlocked every role-pack workspace against this gate (file
# present -> fails the role's own prohibition check; file absent -> REPAIR_REQUIRED
# here — neither branch could ever pass). No fallback literal is kept here on
# purpose: a second copy of the list is the defect this removes, so an unresolved
# list fails loudly instead of quietly reintroducing one.
REQUIRED_FILES=()
if [ -f "scripts/role_registry.py" ] && command -v python3 >/dev/null 2>&1; then
  while IFS= read -r line; do
    [ -n "$line" ] && REQUIRED_FILES+=("$line")
  done < <(python3 scripts/role_registry.py --required-memory-files)
fi
if [ "${#REQUIRED_FILES[@]}" -eq 0 ]; then
  write_evidence "failed" "REPAIR_REQUIRED — scripts/role_registry.py unavailable, the required-memory list could not be resolved."
  echo "⚠️  scripts/role_registry.py unavailable — the required-memory list could NOT be resolved."
  echo "   Evidence recorded as FAILED (not a silent pass). Run 'just fleet-upgrade --apply' from the kernel to fix this."
  exit 0
fi
MISSING=""
for f in "${REQUIRED_FILES[@]}"; do
  [ -f "$f" ] || MISSING="$MISSING $f"
done
if [ -n "$MISSING" ]; then
  write_evidence "failed" "REPAIR_REQUIRED — missing memory files:$MISSING"
  echo "❌ Workspace REPAIR_REQUIRED — missing:$MISSING"
  echo "   Evidence recorded as FAILED. Side effects blocked."
  exit 0
fi

# ── Honor the preceding step's exit code ──────────────────────────────────────
if [ "$EXIT_CODE" -ne 0 ]; then
  write_evidence "failed" "Validation step failed (exit $EXIT_CODE) — gate blocked."
  echo "❌ Evidence recorded FAILURE (exit $EXIT_CODE). Side effects blocked."
  exit 0
fi

# ── 5. Success evidence ───────────────────────────────────────────────────────
write_evidence "passed" "All machine-enforced gates successfully cleared."
echo "✅ Evidence pack generated at .ai/memory-bank/evidence.json (task_id=${TASK_ID:-none})"

# NOTE: TickTick is intentionally NOT synced here. Syncing is an external side
# effect that MUST be user-approved — the AI shows the (Pending) list, the user
# picks which to push, then runs `just tt-sync "<ids>" "<reminder>"`. See AGENTS.md.
