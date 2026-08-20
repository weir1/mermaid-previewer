---
name: "Off-Plan Work"
description: "Name work that is not in .ai/plan.md, with its price, before starting it (PH9-T05)."
---

# Off-Plan Work Skill

**When the user asks for work that is not in `.ai/plan.md`, say so *before* starting it — with
its price — and let them choose.** Silently absorbing a mid-session ask is how a workspace ends
up in endless building with no idea what it cost. Load this skill whenever a request doesn't
obviously belong to an existing goal.

```bash
just off-plan "<the user's request, in their words>"    # notice, or silence
just off-plan "<request>" --add | --once | --drop        # record their choice
```

- **Run it on a work request that does not obviously belong to a goal.** It is a pure read and
  prints nothing when the request is in-plan or is just a question, so a needless run is free.
  It classifies conservatively — ambiguous text is *not* work, and `--kind work` forces the call
  when you already know better.
- **Show the notice and ask, as an interactive choice** (`AskUserQuestion`), never as an idle
  prose question — the same rule as every other decision in this workspace. Then run `--add` /
  `--once` / `--drop` with what they picked.
- **`--add` grows the plan** (a new `G<n>` written by `plan_workspace.add_goal()`, the plan's
  only writer); `--once` and `--drop` leave it byte-identical. **All three are recorded** in
  `.ai/decision-log/` — a one-off that never entered the plan is exactly the scope growth that
  otherwise disappears from the record.
- **The price is `effort_forecast.price_tag(1)` verbatim**, never re-derived. Too little history
  to price → the notice still fires, carrying the refusal: the scope grew either way.
- **What this does not bind:** that the classifier is right. It is stated word-overlap over each
  goal's title (Dice, threshold `MATCH_THRESHOLD = 0.5`), and every notice prints the closest
  goal and the score so a wrong call is arguable rather than silent. A heavily paraphrased
  in-plan request can read as off-plan — one line to dismiss, chosen deliberately over a notice
  that cries wolf and gets tuned out.
