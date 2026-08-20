---
version: 3.7.0
updated: 2026-08-18 16:06 IST
revisions: 25
updated_basis: git
---
# God Mode AI OS — Context Workspace Protocol (CANONICAL)

> **This is the single source of truth for how every AI agent operates in this workspace.**
> It is read automatically by all agents at session start. DO NOT skip any step.
>
> **How each AI reaches this file:**
> - **Claude** (Code / claude.ai) → `CLAUDE.md` imports it via `@AGENTS.md`, and `.claude/settings.json` runs `just session-start` on session start.
> - **Antigravity / Gemini CLI** → global `~/.gemini/GEMINI.md` is a ~10-line **pointer** at this file, not a copy of it (PH6-T14 collapsed the 239-line fork that had drifted into contradicting it).
> - **AGENTS.md-aware tools** → read this file directly at the repo root.
>
> Keep this file canonical. `.agents/AGENTS.md` is a symlink to it. Do not fork the content.

## WORKSPACE IDENTITY
- **Name:** mermaid-previewer
- **Tag:** `@mermaidpreviewer`
- **Telegram Tag:** `@mermaidpreviewer`
- **TickTick Tag:** `mermaid-previewer_workspace`
- **Routes File:** `~/.gemini/m_protocol_routes.json`
- **Role:** A **product workspace** — it is **governed by** the God Mode AI OS, not the source of it.
- **Kernel:** `/Users/moind/Documents/Context`. That is where the OS is developed and where `scripts/onboard_project.sh` lives; this workspace **receives** the protocol, scripts and policies from it and has no `onboard_project.sh` of its own. **A kernel fix made here does not propagate — it must be ported to the kernel or the next `god-upgrade` reverts it.**

## ⚠️ CRITICAL: WORKSPACE IDENTITY RESOLUTION (Anti-Drift Rule)

**Exists because of a confirmed bug: agents have drifted to the wrong workspace by reading the Active Document instead of the workspace root.** Resolution order: (1) the directory containing this `AGENTS.md` / `git rev-parse --show-toplevel` — `$CLAUDE_PROJECT_DIR` for Claude, the `Workspace URI` for Antigravity; (2) fall back to the Active Document path **only** if (1) is absent; (3) never let the Active Document override the root. Before writing any file, reading memory bank files, or running `just`, confirm the target/cwd starts with the workspace root — **all scripts are `cwd`-relative**, so running from the wrong directory operates on the wrong workspace. When in doubt: the workspace root wins. Always.

## MANDATORY SESSION START CHECKLIST

Run these EVERY time a new conversation starts in this workspace:

1. `just session-start` — OS version drift, memory freshness, evidence.json status, recent AI_CHANGELOG, Telegram @mermaidpreviewer notes, pending TickTick tasks (Claude: the SessionStart hook runs this automatically — read its output). Memory STALE → `just freshen-memory` first.
2. Read `.ai/memory-bank/activeContext.md` — the **working set**: current phase, the open tasks, and what the last sessions did. The full task ledger (every task, DoD, `Goal:`, named test) is `.ai/docs/tasks.md`; since PH16-T09 this file carries only tasks that are still open, and the task you mark `(In Progress)` **here** is what binds `evidence.json` to your work (`task_ledger.active_task()` reads this file and no other).
3. Read `.ai/memory-bank/progress.md` (what prior sessions did).
4. Read the last 40 lines of `AI_CHANGELOG.md`.
5. Telegram notes found for @mermaidpreviewer → summarize, ask the user what to do.
6. `(Pending)` tasks found → show the list, ASK which to push to TickTick. NEVER auto-sync, NEVER invent tasks — see the `ticktick-protocol` skill.

## MANDATORY SESSION END — RUN THE PIPELINE; DO NOT RE-DERIVE ITS ORDER (PH23-T02)

**The closure order is executed, not remembered.** Its steps invalidate one another out of
sequence, and the observed session stated the correct order at minute 55 and violated it at
minute 65 (kernel-only doc: Context/doc/CLOSURE_AUDIT_BRIEFING.md). This section names *recipes*: the prose
sequence is what failed, so restating it here would be the bug, not the fix.

```bash
just closure-status                      # the board: what is done/stale/pending + the ONE next action
# → write the session's docs FIRST (see "content before proof"), then:
just session-end --summary "what changed" # freshens memory, session log, changelog rotation
just prep-close                          # everything a machine can settle, in one idempotent pass
just review-diff && just self-review pass "what you actually checked"
just ship "what this session did"        # commit → push → close git-push (needs user approval first)
just close docs && just close issues     # peers of the push; `ship` cannot run them for you
just handover "<the next step>"
```

- **Unsure what you have already done? Ask `just closure-status`, don't reconstruct it** (`--dag`
  renders the graph, `--next` prints one step id). That uncertainty at 100k tokens is what cost
  the observed session four `--amend` cycles.
- **The gap at the self-review is deliberate.** `prep` stops before it, `ship` starts after it —
  a review nobody read certifies nothing. How to review → `self-review-diff` skill.
- **Content before proof.** Every tracked-file write — `activeContext.md`, `progress.md`,
  `AI_CHANGELOG.md`, `knownIssues.md` included — lands **before** `prep-close`; the review hash
  covers everything that would be pushed. The `close docs` / `close issues` *records* write only
  gitignored state, so they void nothing and are safe after `ship` — which is also why they carry
  no edge in the graph and **no blocker will ever name them. `ship` cannot run them for you.**
- **Push stays `[Destructive/Dependency]`.** `ship` enforces the open gate; only the user gives
  approval. Ask before running it.

## ⏳ SESSION WORK BUDGET (HARD LIMIT: 2 + 3)

**One session does at most 2 work tasks, then 3 closure tasks — then STOP and hand over.** This keeps each session short, high-signal, and well under the context limit, and protects the user's session/usage from being burned on one sprawling chat.

- **2 WORK tasks** — real tasks from `.ai/docs/tasks.md` / `activeContext.md`. After each, run `just work-done "PH#-T##"`.
- **3 CLOSURE tasks** — `git-push` · `docs` · `issues`. **Run them with the pipeline, not by hand** — see MANDATORY SESSION END above. Their order is declared in `closure_status.STEPS` and read by `just closure-status`; it is deliberately not repeated here, because a second copy of it is how this went wrong (PH23-T02).
- **Spent the 2 work slots → do not start a third.** Finish closure, `just handover "<next step>"`, tell the user to start fresh. **Exception:** only exceed 2+3 with the user's explicit authorization for a larger scope this session.

`just budget` shows the live count; `just session-start` starts/resumes and displays it. **The counter survives a resume (PH7-T09)** — `SessionStart` also fires on resume/compaction, so `start` resets only on a demonstrably new session (`--source startup|clear` or an empty counter); a resume/compaction/unrecognised source **preserves** it, and any credit-discarding reset is decision-logged. Reset deliberately: `just budget-reset --force`. Preview without touching state: `just session-start --no-reset`.

## 🎚️ GOVERNANCE PROFILE (PH26-T01) — the cap above is per-workspace, and it is DECLARED

**`just profile`** says how much ceremony this workspace runs. `full` is the default and is everything above; `lite` gives a product workspace in a build sprint more work slots and closure in two commands (`just wrap` → self-review → `just land`). **A profile moves how many tasks you START and how many commands closure costs — never a gate:** `verify-safe`, the suite, the brief, `plan-before-code` and the pre-push self-review are required in every profile, `KNOBS` is a closed set, and a test fails the day a pack declares anything outside it. **Declared, never inferred** — absent → `full`, unknown name → refused *by name* and `full`, expired → `full` with the date; every unresolved case takes the stricter side. Built because `@zenithos`'s first day logged five correct, authorised `override work-done` records before 08:19: a control whose normal outcome is an override stops differing from one that is off. → **`governance-profile` skill**

## 🥇 QUALITY OVERRIDES BUDGET (precedence rule — read with the two budgets above)

**The 2+3 session budget and the ~150k context budget are circuit breakers against sprawl. They cap how many tasks you *start* — never how well you do each one. When budget and quality conflict, quality wins. Always.**

- **Never rush, truncate, or skip validation to fit a task into the budget.** Correctness and quality are non-negotiable; the counters are not.
- **A work task counts against the budget ONLY when it meets its Definition of Done and passes its validation gate.** A half-done or rushed task does not advance the counter — and is not shipped. This removes any incentive to declare a task "done" just to move the count.
- **If a task is bigger than one slot, do it right:** finish the slice that is correct and verified, run `just handover "<the rest>"`, and stop. Splitting a large task across sessions is expected and fine. Degrading quality to hit the count is a protocol violation.
- **The budget's real purpose** is to keep sessions short, high-signal, and lean — not to act as a productivity quota. If two tasks genuinely need more room, hand over cleanly rather than cramming.

## 🧠 CONTEXT BUDGET (stay under ~150k tokens)

Big context = slow, expensive, lower-quality sessions. Keep it lean:

- **Know what you already spent.** `just session-start` prints the boot set's estimated cost; `just tokens` breaks it down per file and names anything over the hot-file budget. Estimated, not tokenised (±15%, calibrated against a real tokeniser) — enough to answer "am I near the limit?", not exact accounting.
- **RETRIEVAL-FIRST for memory — read `.ai/memory-bank/INDEX.md` first.** It maps every topic → the one authoritative file (`[[file#anchor]]`). **Do NOT grep or scan the repo to rediscover where something lives** — that search is the waste the index exists to kill; fall back to searching only when the index has no pointer (then add one). Links are validated by `just doctor`. Docs are *indexed*, never pasted into memory (one source of truth, no drift).
- **RETRIEVAL-FIRST for source — read `.ai/codemap.md` first** (PH7-T07), the source-side twin of `INDEX.md`: one generated row per module (path · purpose · `just` recipe · entry point). **Regenerate with `just codemap`; never hand-edit it** — every field is derived from the source. `just doctor` **FAILS** when the map drifts from the tree, naming what changed.
- **Do NOT scan the whole workspace.** Read the memory bank + `.ai/handover/latest.md` — they hold the full context by design; boot from the handover rather than re-deriving it by exploring.
- **Hot memory files ≤ ~200 lines.** `just archive-memory` moves closed history into `.ai/memory-bank/archive/` (dry run by default, `--apply` writes, nothing is ever deleted — the hot file keeps a `[[link]]` to what moved). `just doctor` warns when a file is oversized.
- **The 2+3 budget caps growth** — a session that stops on time never bloats. For claude.ai web / Antigravity chat (no hook), paste `just session-brief`.

## 🗣️ INTERACTIVE DECISIONS (don't stall — ask as a choice)

When you genuinely need the user to decide (a real fork you can't resolve from the code, memory, or sensible defaults), **present it as a concrete multiple-choice question in the interactive UI — do not end your turn with an open-ended prose question and go idle.**

- **Claude Code:** the `AskUserQuestion` tool (labelled options, recommended-first), batched rather than one stop at a time. **Antigravity / Gemini:** the equivalent prompt, else a numbered menu with a recommended default.
- **Still respect the gate:** interactive ≠ autonomous. External side effects and `[Destructive/Dependency]` actions still require explicit approval — just collect it *as a decision*, not an idle stop.
- **Don't over-ask.** If a sensible default exists, take it and say so. Reserve *blocking* questions for decisions whose answer actually changes what you do next.
- **The turn never ends idle (PH22-T06).** The moment this section long missed is having just **answered** — no decision pending, so the turn simply ended and the operator re-typed the momentum every time. Offer the obvious next move as a labelled choice: a finding to file, a task to pick up, closure to run. **Offering is not blocking** — that is precisely how this coexists with "don't over-ask": an offer is cheap and dismissable, a block interrupts. Where the next step is unambiguous and already authorised, **do it and say so** instead of asking. Work credited with closure unrun makes closure the default offer (PH22-T05).
- **Unenforced by construction, and it says so:** no script can compute *"was that a sensible next move?"*, so unlike `gate_check.py` or `self_review.py` this rule holds because it is read, not because it is checked — do not imply a check that does not exist. Second clause, same origin: timestamps you state **in prose are IST**, not UTC. The changelog rule governs what you write; this governs what you *say*.

## 🤝 COMPLETE HANDOVER

At session end (or when the budget is reached), run:
```bash
just handover "the next step to pick up"
```
This writes `.ai/handover/latest.md` — full context in one file (phase, what this session did, ongoing/next tasks ≤2, open issues, git HEAD, boot steps) — so the next session (Claude *or* Antigravity) resumes instantly without re-scanning. The next AI reads that file **first**.

## VALIDATION GATE (before any external side effect)

Before syncing to TickTick, pushing to git, deploying, or any outbound mutation:

1. Run `just verify-safe` → regenerates `.ai/memory-bank/evidence.json`. (At session end you get this from `just prep-close`, which ends on it — don't run the closure steps by hand.)
2. Run **`just gate`** → the single check. Exit 0 = open, 1 = blocked with the reason. **Do not hand-inspect `evidence.json`** — `scripts/gate_check.py` is the only implementation of this contract (status · exit_code · pipeline ∈ {safe,release} · computed freshness · task match · **a `[complex]` task's written plan**, PH16-T28 — checked first, so the plan stops the irreversible act and not merely the credit for it).
3. If the gate is closed → **stop**, report the printed reason, propose remediation. Do not bypass.

`just push`, `just commit-all` and `just tt-sync` call the gate themselves and refuse when it is closed — the embargo is enforced, not merely stated. A newly onboarded workspace starts with the gate **closed** (`status: "unverified"`, `pipeline: "onboarding"`) until a real `just verify-safe` run replaces the placeholder. Where an override exists it demands a written reason and logs to `.ai/decision-log/`; there is no silent bypass.

**Branch policy (ADR-021):** commit and push to `main`, gated on an open validation gate **and** explicit user approval. No auto-push, no auto-commit.

## 🛠️ ENGINEERING SKILL PACK (how to do the work, not just how to be safe)

The OS governs *safety* through gates and policy. **Craft and workflow detail live here** — twelve procedures in `.agents/skills/` (mirrored to `.claude/skills/`, deployed fleet-wide by `onboard_project.sh`). They load on demand, so they cost no context until used — this is also where sections that used to be inline in this file moved (PH19-T03), so "not in AGENTS.md anymore" means "load the skill," never "the rule went away."

| Skill | Load it when |
|---|---|
| `prework-brief` | **Before any task starts.** Discuss it in plain English, argue the cheaper version, record his own words. `just work-done` refuses without it. |
| `plan-before-code` | The task touches >3 files, is `[complex]`, or changes an enforcement path. Write `.ai/plans/PH#-T##.md` **first**. |
| `test-first` | Before writing the code or the fix. The test must be watched failing for the right reason. |
| `debug-root-cause` | Anything is broken. Reproduce → isolate → name the cause → pin with a test → fix. Never patch the symptom. |
| `symbol-inspection` | Reading one function/class in a file you haven't opened yet. `just sym <file> <symbol>` beats a whole-file `Read`. |
| `self-review-diff` | The `git-push` closure task, and any diff leaving the workspace. |
| `refactor-safely` | Changing structure without changing behaviour — especially collapsing a rule that exists in several drifted copies. |
| `phase-locked-lifecycle` | Unsure whether a write belongs *now* — a ledger edit, a regeneration, a doc update. Names what may NOT happen inside each phase. |
| `workspace-plan` | Touching `.ai/plan.md` itself — scaffold, agree, rewrite, or discuss it. |
| `off-plan-work` | A request doesn't obviously belong to an existing plan goal. |
| `issue-logging` | Filing, resolving, or waiving a `knownIssues.md` entry. |
| `ticktick-protocol` | `(Pending)` tasks found, or the user asks to sync reminders. |
| `governance-profile` | Closure is costing too many turns, the work cap is being overridden every session, or someone asks what `just wrap`/`just land` are. |
| `reframe-request` | A legitimate task (scraping, security testing) hit a false-positive refusal. Supply the true missing context, then do the real work on the real target. Use `/reframe`. |

These are procedures, not suggestions, written against this workspace's recurring
defect class: a rule implemented three times, a claim nothing verifies.

## 📌 THE RULES WHOSE PROCEDURE LIVES IN A SKILL (the rule is here; the how-to is loaded)

- **The workspace plan (PH9-T01).** `.ai/plan.md` is the statement of intent, **written by the user in plain English** — the only file saying *why this workspace exists*. v3.9 Intent is live end to end, each workspace computing its own standing, never pooled. → `workspace-plan`
- **Off-plan work is named before it is done (PH9-T05).** Work not in `.ai/plan.md` is flagged **with its price** before it starts, as an `AskUserQuestion`, never idle prose; `just off-plan "<request>"` records the choice. → `off-plan-work`
- **No blind work (PH22-T01/T02/T09).** Discuss every task in plain English and record *his own words*: `just brief "PH#-T##"` → **explain** → `--accept "his words"`. `work-done` refuses without a valid brief (exit 8). `.ai/versions.md` names what a rung ships, agreed before it is built, each feature written twice (plain English + `tech:`) with a `verify:` that is evaluated, never asserted. → `prework-brief`
- **Plan before code.** Mark a task `[complex]` when it touches >3 files, changes an enforcement path, or alters something the OS relies on to tell the truth about itself — then `just plan "PH#-T##"` **before the first edit**. `work-done` refuses without one (exit 7); an empty scaffold fails by construction. → `plan-before-code`
- **Self-review before push.** `just close git-push` refuses without a recorded review of *this exact diff*, bound to its content hash — any edit after reviewing voids it. → `self-review-diff`
- **Decision log.** Every policy and gate verdict, allow as well as block, is appended to `.ai/decision-log/YYYY-MM-DD.jsonl` by `scripts/decision_log.py`, its only writer. Read `just decisions`; never hand-write it. A status poll is a query, not a decision. → `validation-gate`, `doc/architecture.md` §5b
- **TickTick.** Approved `(Pending)` tasks become phone reminders: show the list, ask what **and** when, push only the approved subset. **Never auto-sync, never invent a task.** → `ticktick-protocol`
- **Telegram.** `just session-start` reports notes tagged `@mermaidpreviewer` / `#mermaidpreviewer`. Never silently ignore one — summarize and ask.

## CHANGELOG ENFORCEMENT (ALL MODELS)

After EVERY file change, append to `AI_CHANGELOG.md`:
```
## [YYYY-MM-DD HH:MM IST]
**Files Changed:** path/to/file1, path/to/file2
**Summary:** What changed and why
```
No exceptions.

## 🐛 ISSUE LOGGING PROTOCOL — every bug names the test that proves it fixed (PH7-T06)

"Note this issue" / "log this bug" → `just note-issue "TITLE" "DESC" "tests/x.py::C::test_y"` — every entry names its regression test, `(none yet)` written explicitly when there isn't one yet. `just resolve-issue` refuses unless the named test exists **and** the real test runner collects it; `just issue-waive` is the only way out, and it's decision-logged. Full protocol → **`issue-logging` skill**.

## BLAST RADIUS (AUTONOMOUS vs APPROVAL REQUIRED)

| Blast Radius | Examples | Can AI act? |
|---|---|---|
| `[Docs Only]` | Edit .md files, update memory bank | ✅ Autonomous |
| `[Safe Refactor]` | Edit scripts, fix bugs, freshen memory | ✅ Autonomous |
| `[Destructive/Dependency]` | Delete files, add packages, push to git | ❌ Ask user first |

Machine-readable rules: `.ai/policy.yaml`. Match paths + operations against `autonomous_allowed` / `approval_required`; `always_blocked` is unconditional.

## 🏷️ EVERY DOC SAYS WHAT IT IS TRUE FOR (PH22-T07)

Every doc carries `version` · `updated` · `revisions` · `updated_basis` in frontmatter — **derived from git by `scripts/doc_stamp.py`, never typed**, the same rule as `evidence.json`: a hand-kept counter is a lie with a schedule. `just doc-stamps --apply` during closure; `just doctor` FAILS on drift; exemptions are declared with a reason in `doc_stamp.EXEMPT`. Only `revisions`/`updated_basis` are checkable — `updated`/`version` are refreshed, not verified, because stamping is itself a change (module docstring has the argument).

## REFERENCE DOCS (READ WHEN NEEDED)

Guide `doc/AI_MEMORY_GUIDE.md` · Architecture `doc/architecture.md` · Setup `doc/workspace_setup_guide.md` · Policy `.ai/policy.yaml` · Rules `.agents/rules/` · Workflows `.agents/workflows/` · Skills `.agents/skills/`
