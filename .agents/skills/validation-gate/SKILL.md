---
name: "Validation Gate Protocol"
description: "Operational procedure for running the validation suite and parsing evidence.json before executing side effects."
---

# Validation Gate Skill

This skill defines the operational procedure for the Validation Gate policy. You must use this skill whenever a workflow reaches the verification stage, prior to any external side effects.

## 1. Execution
To validate your changes, execute the following command in the workspace root:
```bash
just verify-safe
```

## 2. Verification Procedure

```bash
just gate        # exit 0 = open, 1 = blocked, with the reason printed
```

**Run the command. Do not hand-inspect `evidence.json`** — that is how the gate drifted into three
disagreeing implementations, one of which reported ✅ on a workspace that had never been validated.
`scripts/gate_check.py` is now the only implementation, and it verifies:

- `status` — strictly `"passed"`.
- `exit_code` — strictly `0`.
- `pipeline` — `safe` or `release`. **`onboarding` is a bootstrap placeholder and never opens the gate**; it carries `status: "unverified"`. A freshly onboarded workspace starts with the gate CLOSED.
- `task_id` — must match the task you are executing **if one is active**. Derived from the first `(In Progress)` task in `activeContext.md`; an empty string (maintenance session) is acceptable.
- `validated_at` (alias `timestamp`) — must be **newer than the newest tracked working-tree change**. This is computed, not trusted. If you edited anything after the last run, the gate is stale and you must re-run `just verify-safe`.
- **the plan**, if the active task is marked `[complex]` (PH16-T28) — checked *first*, before the evidence is read, because a plan is a precondition on having started rather than a property of the proof. `just plan "<id>"` is the remedy and the refusal names it. Nothing changes for a non-`[complex]` task or a maintenance session with no active task.

The full schema is documented in `.agents/rules/00-validation-gate.md` and enforced by `.ai/schemas/evidence.schema.json`.

## 3. Fail-Fast Behavior
If `evidence.json` is missing, malformed, stale, or indicates failure (e.g., `status: "failed"`), you must immediately trigger a fail-fast state:
- Stop the current workflow.
- Do not ask the user for permission to bypass.
- Do not queue background syncs.
- Do not invoke subagents whose purpose is external mutation.
- Report the specific reason for the failure (e.g., "Tests failed", "Evidence is stale") and propose a remediation plan.
