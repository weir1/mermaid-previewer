Before doing anything:
1. Read `.ai/memory-bank/projectbrief.md`
2. Read `.ai/memory-bank/activeContext.md`
3. If either file has `freshness: stale` in the frontmatter, stop and report that memory must be revalidated.
4. Read `.ai/memory-bank/evidence.json` if the task claims prior verification.
5. Do not read archived files unless the current task explicitly requires historical context.

## Bootstrap Gate (NEW)
Before any autonomous execution or TickTick sync:
6. Verify ALL required files exist:
   - `.ai/memory-bank/projectbrief.md`
   - `.ai/memory-bank/activeContext.md`
   - `.ai/memory-bank/systemPatterns.md`
   - `.ai/memory-bank/techContext.md`
   - `.ai/memory-bank/decisions.md`
   - `.ai/memory-bank/progress.md`
   - `.ai/memory-bank/knownIssues.md`
   - `.ai/memory-bank/evidence.json`
7. If any file is missing → workspace is `REPAIR_REQUIRED`. STOP and ask user permission to bootstrap before doing anything else.
8. If all files exist and `evidence.json` is present → workspace is `VERIFIED`. Proceed.
