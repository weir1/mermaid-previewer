# CLAUDE.md — God Mode AI OS workspace

**The canonical protocol is `AGENTS.md`, imported below. Read and follow it fully.**

@AGENTS.md

---
## Claude-Code notes
- `.claude/settings.json` runs `just session-start` on session start — read its briefing.
- Skills in `.claude/skills/`; commands `/start-task`, `/start-with-memory`, `/log-session`.
- Every script is cwd-relative; operate from the workspace root (`$CLAUDE_PROJECT_DIR`).
- No commit / push / TickTick-sync without passing the validation gate AND user approval.
