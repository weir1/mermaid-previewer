---
name: "Session Protocol Rule"
description: "Mandatory start/end session checklist. AI is blocked from autonomous side effects until session-start is run and all gates are cleared."
priority: critical
---

# Policy: Mandatory Session Protocol

This rule supplements the Validation Gate (`00-validation-gate.md`). It governs the **startup sequence** and **shutdown sequence** of every AI session in this workspace.

## 0. Workspace Identity Resolution (MUST CHECK FIRST)

**CONFIRMED BUG HISTORY:** Agents have drifted to wrong workspaces by using the Active Document path instead of the Workspace URI.

**Resolution Priority (strict order):**
1. Read the `Workspace URI` from the `user_information` block → this is the canonical workspace root.
2. Only fall back to Active Document if `user_information` has NO workspace URI.
3. NEVER allow the Active Document to override the Workspace URI.

**Before doing ANYTHING else**, verify:
- All file reads/writes are under `<workspace_root>/` (from Workspace URI)
- `just` commands are run with `Cwd` = workspace root
- Memory bank reads use `<workspace_root>/.ai/memory-bank/`



## 1. Session Start Gate

The FIRST thing the AI must do in any new conversation is:

```bash
just session-start
```

This command:
- Checks all memory bank files for staleness
- Validates `evidence.json`
- Scans `~/Documents/Notes/Telegram/Received_Notes/` for notes tagged `@context`
- Lists any `(Pending)` tasks from `activeContext.md` — for user's approval ONLY

**If the AI skips `just session-start`, it is operating with potentially stale context.** This is a protocol violation.

After running, the AI must:
1. **Read** `.ai/memory-bank/activeContext.md` — current task queue
2. **Read** `.ai/memory-bank/progress.md` — last session history
3. **Read** last 40 lines of `AI_CHANGELOG.md` to understand recent changes

## 2. Telegram Inbox Gate

The session start script will report any Telegram notes tagged `@context` or `#context`.

**Rule:** The AI MUST NOT ignore these notes. It must:
- Summarize what notes were found
- Ask the user: "I found X Telegram note(s) for this workspace. Want me to integrate them?"
- Only process/file them if user says yes

## 3. TickTick Approval Gate

The session start script will list any `(Pending)` tasks from `activeContext.md`.

**Rule:** The AI MUST ask: *"Here are the pending tasks I found. Which ones would you like me to push to TickTick?"*

The AI is **STRICTLY FORBIDDEN** from:
- Auto-syncing without user saying which tasks to push
- Inventing tasks that don't exist in `activeContext.md`
- Running `just tt-sync` unless user has approved the specific task list

## 4. AI_CHANGELOG Enforcement

The AI must append to `AI_CHANGELOG.md` after EVERY session where files were changed.
The log entry must include:
- Date + time in IST (not UTC)
- Files changed
- Why they were changed

**This applies to ALL AI models.** If a model does not maintain the changelog, it is violating this workspace's governance protocol.

## 5. Session End Gate

Before ending any session where work was done:

```bash
just session-end --summary "what changed"
```

This command:
- Freshens all memory bank `last_verified` timestamps
- Writes a session log JSON to `.ai/session-log/`
- Reports uncommitted git files

After running, the AI must:
1. Update `activeContext.md` — mark tasks (Complete), add new (Pending) items
2. Update `progress.md` — append today's dated entry
3. Commit via `just commit-all` if any files changed

## 6. Memory Staleness Recovery

If `just session-start` reports `freshness: stale` on any file:

```bash
just freshen-memory
```

Then re-read the file and update its content to reflect current reality before proceeding.

**Stale memory = unreliable context = wrong decisions.** Recover it immediately.
