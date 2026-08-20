---
name: "Validation Gate Rule"
description: "Non-bypassable policy enforcing Memory Bank reads and validation gates before any side effects."
priority: critical
---

# Policy: Memory Bank Boot Order & Validation Gate

This rule establishes the single source of truth for agent context and strictly forbids unauthorized external side-effects (e.g., TickTick syncs, deployments, outbound mutating API calls).

## 1. Mandatory Boot Order
Before any task planning, system design, or code editing begins, you **MUST** read the following files to establish project context:
- `.ai/memory-bank/projectbrief.md`
- `.ai/memory-bank/activeContext.md`

## 2. Mandatory Validation Command
Before progressing to any external side-effect step, you **MUST** run the local validation suite:
- **Command:** `just verify-safe`
- **Output:** This command generates or updates the `evidence.json` file in the `.ai/memory-bank/` directory.

## 3. Absolute Side-Effect Embargo

**Run `just gate`. It is the check — do not re-implement it by reading `evidence.json` yourself.**
`scripts/gate_check.py` is the single implementation of this contract; `doctor`, `session-start`,
`just push`, `just commit-all` and `just tt-sync` all call it and refuse on a non-zero exit.

You are **STRICTLY FORBIDDEN** from executing any external side effects (including but not limited to syncing tasks to TickTick, pushing to remote repositories, or deploying) unless all of the following conditions are met — exactly what `just gate` verifies:
- `evidence.json` exists and parses.
- The status indicates success: `status: "passed"` **and** `exit_code: 0`.
- The `pipeline` is `safe` or `release`. **`onboarding` is a bootstrap placeholder, not proof** — it is written with `status: "unverified"` at onboarding and the gate stays closed until a real run replaces it. A workspace that has never run `just verify-safe` has a CLOSED gate, by design.
- The `task_id` field matches the task you are executing **if** one is active. `evidence-pack.sh` derives `task_id` from the first `(In Progress)` task in `activeContext.md`; it is an empty string for maintenance sessions with no active task, which is acceptable.
- The evidence is **fresh**: `validated_at` (equivalently `timestamp`) is *after* your most recent modification to the codebase. Any working-tree change invalidates prior evidence — re-run `just verify-safe`. Freshness is **computed** against the newest tracked working-tree change (bookkeeping the gate itself writes — evidence, session/decision logs, handover — is excluded).
- The active task, **if it is marked `[complex]`, has a written plan** (PH16-T28). Checked *first*, before evidence is even read: a plan is a precondition on having started, not a property of the proof. This is why the plan requirement now stops the irreversible act rather than only the credit for it — PH16-T27 deployed to 45 workspaces and was refused by `work-done` afterwards, so its **Rollback** section was authored after the last write. A non-`[complex]` task and a maintenance session with no active task are unaffected, and a workspace whose `plan.py` cannot be read opens rather than blocks.

### Overrides are visible, never silent
Where an override exists (`ticktick_sync.py --override-gate "<reason>"`), it requires a written reason and appends an entry to `.ai/decision-log/`. There is no silent bypass. Using it without the user's explicit instruction is a protocol violation.

### Evidence schema (produced by `scripts/evidence-pack.sh`)
```json
{
  "timestamp": "…", "validated_at": "…", "pipeline": "safe",
  "commit": "…", "status": "passed", "exit_code": 0,
  "task_id": "PH4-T01", "message": "…", "spec_hashes": { … }
}
```
A machine-readable schema lives at `.ai/schemas/evidence.schema.json` and is validated by `just verify-safe`.

If the validation fails, is missing, or is stale, you must report the failure and propose remediation. You may not bypass this check under any circumstances.
