---
name: "Workspace Plan Protocol"
description: "Read and evolve .ai/plan.md — the workspace's statement of intent, written by the user in plain English (PH9-T01/T07)."
---

# Workspace Plan Skill

`.ai/plan.md` is the workspace's statement of intent, **written by the user in plain English**.
`AGENTS.md` governs *what* a session does and *whether it is safe*; this file is the only one
that says *why the workspace exists*. Load this skill whenever you touch `.ai/plan.md` itself —
scaffolding it, agreeing it, rewriting it, or discussing it with the user.

```bash
just plan-workspace              # scaffold .ai/plan.md, or report what blocks agreement
just plan-workspace --check      # pure read; exit 1 unless agreed and complete
just plan-workspace --agree      # draft → agreed (refuses an incomplete plan)
just plan-workspace --generate   # write .ai/docs/prd.md from the agreed goals
```

Five sections: **What I want** (the user's own words) · **Proposed additions** (the AI's, each
`- [ ]` until the user marks it `[x]` accepted or `[~]` declined) · **Goals** · **Non-goals** ·
**Constraints**.

- **The AI rewrites "What I want" for clarity; the core must survive, provably.** The user writes
  in whatever form is fastest for them — the plan should not leave them reading their own raw
  typing. So the AI polishes it, **and the original stays verbatim underneath in a collapsed
  block**, so "the core remained" is something you can check rather than something the AI
  asserts. A rewrite is proposed and accepted before it replaces anything; `--agree` itself never
  rewrites.
  *(Superseded the original "the AI never rewrites this section" rule on 2026-08-05 at the
  user's direction. The rule it replaces was protecting intent at the cost of leaving the plan
  unreadable; keeping the verbatim original is what buys both.)*
- **Agreement is a declaration, not a phrase.** `status:` is read from YAML frontmatter only —
  the word "agreed" in the prose never satisfies it. Same rule for goals: a goal is a *list item
  starting with* `G<n>`, so prose mentioning G1 is not a goal. This is the fifth
  mention-vs-declaration case in this repo, designed structurally up front rather than fixed
  after.
- **No document is generated from an unagreed plan.** `--generate` refuses while the status is
  not `agreed`, while any proposal is unresolved, or when `.ai/docs/prd.md` exists and this tool
  did not write it — a hand-written PRD is never overwritten.
- **What this does not bind:** that the plan is any good, or still true. Keeping it honest
  against the code is PH9-T02's audit.

**Every workspace speaks for itself.** The plan, its goals, its progress and its effort forecast
are computed *inside* each workspace by scripts this kernel deploys — never centrally on its
behalf. `fleet-goals` collects what each workspace says about itself; it does not reach in and do
the arithmetic. The kernel passes the law; each workspace obeys it locally. Same shape as the
gate: `gate_check.py` runs in the workspace, and `fleet-status` reports its verdict rather than
recomputing it.

## The rewrite, the discussion, and the chain (PH9-T07)

```bash
just plan-workspace --rewrite "<clarified text>"   # STAGES it — the plan is untouched
just plan-workspace --accept-rewrite               # applies it, archiving the original
just plan-workspace --reject-rewrite               # drops it
just plan-workspace --original                     # the user's verbatim words, always retrievable
just plan-discuss                                  # the open decisions, as labelled options
just plan-finalize                                 # agree → version → docs, in one run
```

- **Propose is not accept.** A rewrite lands in `.ai/plan.rewrite.md` and replaces nothing until
  the user explicitly accepts. Show them the staged text and ask — their own words are the one
  thing in this repo no revert reconstructs.
- **The archived original is the *first* original, permanently.** A second rewrite carries the
  existing `<details>` block through untouched rather than archiving the previous polish;
  otherwise every accepted rewrite moves the record one paraphrase further from what the user
  actually wrote.
- **`--agree` never rewrites**, and neither does `--finalize`.
- **Ask with `plan-discuss`, never in open prose.** It computes the actual open decisions
  (staged rewrite · empty sections · unresolved proposals · ready-to-agree) as labelled options —
  feed them to `AskUserQuestion`. An agreed, unblocked plan returns nothing to ask.
- **`plan-finalize` stops at the first refusal** and reports the steps it did *not* attempt as
  skipped rather than omitting them. The version step is advisory: it proposes a bump and never
  stamps one.
