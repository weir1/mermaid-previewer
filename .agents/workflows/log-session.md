Create or append a terse entry to `.ai/session-ledger.md`.

Rules:
- Never exceed 8 lines per session entry.
- Read `.ai/memory-bank/evidence.json` first.
- Refuse to write if evidence status is not `passed`.
- Include: timestamp, agent role, task, blast radius, git hash, outcome, next step.
- Do not summarize full reasoning or chat history.
