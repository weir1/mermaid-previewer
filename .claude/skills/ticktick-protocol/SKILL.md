---
name: "TickTick Reminder Protocol"
description: "Turn approved (Pending) tasks into phone reminders — ask what and when, never auto-sync."
---

# TickTick Protocol Skill

TickTick is where the user's **future planned tasks** become phone reminders so they can pick
the project back up. This is the whole point — a task with no reminder is useless here. Load
this skill at session start when `(Pending)` tasks are found, or whenever the user asks to sync.

**The flow (ask → pick → push-with-reminder):**
1. At session start (or when the user asks), show the `(Pending)` tasks from `activeContext.md`.
2. **ASK two things:** *"Which of these do you want in TickTick?"* and *"When should it remind
   you?"* (e.g. "tomorrow 9am", "friday 6pm").
3. Push only the approved subset **with a reminder**:
   `just tt-sync "PH4-T01,PH4-T03" "tomorrow 9am"`

**Hard rules:**
- **NEVER** auto-sync. `evidence-pack.sh` does **not** sync — syncing is always an explicit,
  user-approved `just tt-sync`.
- **ONLY** push tasks marked `(Pending)` with a stable `PH#-T##` ID + 3–7 indented subtasks.
  Never invent tasks.
- **Always attach a reminder** (`--due`, or a per-task `@due:` token) — otherwise TickTick won't
  notify and the task is dead weight. A per-task token overrides the batch reminder:
  `- PH4-T02: Ship the roadmap doc (Pending) @due:friday 6pm @prio:high`
- **Tag:** `context_workspace` (Inbox, no project flag). **Idempotency:**
  `ticktick_sync_state.json` (re-push with `--force` to update a reminder).

Subtasks are sent as one comma-joined `-s` value (the `tt` CLI requirement) so all 3–7 land, not
just one.
