---
last_verified: 2026-08-20T06:21:29Z
ttl_days: 30
confidence: bootstrap
freshness: fresh
age_days: 0
version: 3.9.0
updated: 2026-08-20 11:53 IST
revisions: 2
updated_basis: git
---

# 🗺️ MEMORY INDEX — read me FIRST, then open the one file you need

> **RETRIEVAL-FIRST RULE.** For any question about this workspace, consult this map
> and open the *one* authoritative file it points to. **Do NOT grep or scan the repo**
> to rediscover where something lives — that search is the context waste this index
> exists to kill. Fall back to searching only when the map has no pointer — then add one.
>
> Docs are **indexed here, never pasted** into the memory bank — one source of truth,
> no drift. Links use `[[file#anchor]]` and are validated by `just doctor` (a dangling
> link is a build failure). This file is **workspace-owned**: no upgrade will overwrite
> it, so keep it current as the workspace grows.

## Current state (what is happening now)
- **Current phase, open tasks, last session** → [[.ai/memory-bank/activeContext.md]]
- **What prior sessions did (history)** → [[.ai/memory-bank/progress.md]]
- **Open bugs, risks, limitations** → [[.ai/memory-bank/knownIssues.md]]
- **Resume a session from one file** → `.ai/handover/latest.md` (regenerate with `just handover`)
- **What booting this workspace costs in context** → `just tokens` (per-file estimate vs the ~150k budget)

## How this workspace works (stable reference)
- **What this workspace IS, and for whom** → [[.ai/memory-bank/projectbrief.md]]
- **Tech stack, tooling, environment constraints** → [[.ai/memory-bank/techContext.md]]
- **Recurring architectural + agent patterns** → [[.ai/memory-bank/systemPatterns.md]]
- **Why each decision was made (ADRs)** → [[.ai/memory-bank/decisions.md]]

## Governance (the protocol)
- **Canonical protocol — start here** → [[AGENTS.md]]
- **Blast radius + policy rules (machine-readable)** → `.ai/policy.yaml`
- **Every gate and policy verdict recorded** → `just decisions`

## Source code (the other half of retrieval)
- **Which module does what, and how to run it** → `.ai/codemap.md` — the source-side twin
  of this file. Regenerate with `just codemap`; never hand-edit it.

> **TODO (workspace owner):** add one row per domain this workspace owns, so a new
> session can find it without grepping. Add `#anchor` precision once a file is long
> enough that the whole file is no longer the right retrieval unit.
