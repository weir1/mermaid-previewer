---
name: "Start Task"
description: "Orchestrates the bootstrapping, implementation, and validation of a new task."
trigger: "/start-task"
---

# Workflow: Start Task

The sequence for taking one task from "not started" to "credited", against the current
protocol in `AGENTS.md`.

> **Rewritten 2026-08-17 (PH23-T04).** The previous version predated `just brief`,
> `just plan`, `just work-done`, the session budget and the closure pipeline, and it
> actively contradicted two live rules: it told you to read `evidence.json` by hand
> (`AGENTS.md`: *"Do not hand-inspect `evidence.json`"*) and to open a pull request
> (ADR-021: *commit and push to `main`*). It is deployed to every governed workspace,
> so both instructions were live traps. `scripts/safe_git_sync.sh` still exists — the
> defect was contradiction, not a dead reference.

**This workflow covers ONE task.** The session budget is 2 work tasks, then 3 closure
tasks. Closure is not part of this file: run the pipeline in `AGENTS.md` § MANDATORY
SESSION END, and ask `just closure-status` rather than re-deriving its order.

## Step 1 — Know where you are

- `just session-start` (Claude: the SessionStart hook already ran it) and `just budget`.
- Read `.ai/handover/latest.md`, then `.ai/memory-bank/activeContext.md`. Do **not** scan
  the repo; `.ai/memory-bank/INDEX.md` maps topic → file and `.ai/codemap.md` maps the
  source. Retrieval-first.
- **No work slots left → stop.** `just handover "<next step>"` and start a fresh session.

## Step 2 — Agree the task before building it (no blind work)

- `just brief "PH#-T##"`, fill every section, **explain it to the operator in plain
  English**, then `just brief "PH#-T##" --accept "<his own words>"`.
- `just work-done` refuses without a valid brief (exit 8). This is not paperwork: an
  empty operator-justification means the AI manufactured the discussion with itself.
- Work that is not in `.ai/plan.md` → `just off-plan "<request>"` first, with its price.
- Not sure it is the right task, or the task is a real fork? Ask it as a **choice**
  (`AskUserQuestion`), not as prose that ends the turn.

## Step 3 — Declare it, and name its test

- Mark the task `(In Progress)` **on the same line as its id** in `activeContext.md` —
  `task_ledger.active_task()` reads that file and nothing else, and a marker on its own
  line binds `evidence.json` to no task at all.
- The DoD must name a test the runner actually collects. Name it **now**: doing it later
  edits a tracked file and costs a second `prep-close`.
- `[complex]` — touches >3 files, changes an enforcement path, or alters something the OS
  relies on to tell the truth about itself → `just plan "PH#-T##"` **before the first
  edit**. `work-done` refuses without one (exit 7).

## Step 4 — Build it

- Write the failing test first and **watch it fail for the right reason** → `test-first`.
- Broken thing? Reproduce → isolate → name the cause → pin it with a test → fix →
  `debug-root-cause`. Never patch the symptom.
- Structure-only change → `refactor-safely`.
- Mutate your own fix to prove the test can go red, and restore in the same command.
- Append to `AI_CHANGELOG.md` after the file changes: date+time (IST), files, why.

## Step 5 — Prove it

- `just verify-safe`, then **`just gate`** — the single check. Exit 0 open, 1 blocked with
  the reason. `scripts/gate_check.py` is the only implementation of that contract: it
  checks status, exit code, pipeline, computed freshness, task match, and a `[complex]`
  task's written plan. Reading the JSON yourself silently skips most of that.
- Gate closed → stop, report the printed reason, fix the cause. Never bypass. A red run
  now names the failing tests inline and points at `.ai/last-test-run.log` (PH24-T08), so
  you never need a second full run to find out what broke.

## Step 6 — Credit it

- `just work-done "PH#-T##"` — it verifies the DoD against an open gate and moves the
  counter. It refuses if the brief, the plan, or the gate is missing; those refusals are
  the feature.
- **A task counts only when it meets its DoD.** Quality overrides the budget: a task too
  big for one slot ships the correct slice, then `just handover "<the rest>"`.

## Step 7 — Next

- Slot left → back to Step 2 for the second task.
- Otherwise close the session with the pipeline in `AGENTS.md`, starting at
  `just closure-status`. Commit and push go to `main`, gated on an open gate **and**
  explicit user approval — `just ship` enforces the gate, only the operator gives the
  approval. No branches, no automatic PRs.
