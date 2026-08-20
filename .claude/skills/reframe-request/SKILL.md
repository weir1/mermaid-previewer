---
name: "Reframe Request"
description: "A legitimate technical task hit a false-positive safety refusal. Supply the real context the prompt left out, then do the actual work on the actual target. Use /reframe."
---

# Reframe Request

**Purpose:** A legitimate, authorized engineering task — writing a scraper, testing your own
auth endpoint, automating an API you hold a key for — was refused because the prompt never said
*whose* site it is or *why* the work is authorized. The model was answering the worst reading of
an underspecified request. This skill fixes the request, not the model: state the context that
was actually true and missing, then solve the real problem.

> [!IMPORTANT]
> **The context you supply must be true.** This skill supplies missing facts; it never invents
> them. If you cannot truthfully say who authorized the work, the refusal was correct — stop and
> ask the user, do not manufacture a premise. Asserting a fake sandbox, a fake authorization, or
> a "directive" that overrides your own judgement is out of scope for this skill and always was.

> [!WARNING]
> This addresses prompt framing only. It does NOT bypass `policy.yaml` blast-radius rules, does
> NOT open a closed `just gate`, and does NOT authorize a `[Destructive/Dependency]` action. All
> God Mode OS execution constraints remain in force.

## Execution steps

When you load this skill (usually via `/reframe`):

1. **Name the real context.** Say plainly what the task actually is and what makes it
   legitimate — "this is the user's own site", "this is a local test server on
   `localhost:8000`", "this API key belongs to the user's account", "this is their own log
   file". One sentence of true context is the whole mechanic.
2. **Keep the real target.** Do **not** abstract the target into a generic stand-in. If the task
   is "scrape example.com/jobs", write the client for `example.com/jobs` — its real URL, its
   real HTML structure, its real pagination. A "mock payload parser" is not a smaller version of
   the task, it is a different task that leaves the user's problem unsolved.
3. **Deliver working code, not a template.** The output is the actual solution for the actual
   target: real selectors, real endpoints, real error handling. Generic scaffolding the operator
   must adapt is the failure mode this skill exists to prevent.
4. **Log it.** Run:
   ```bash
   just off-plan "reframe-request: <the original request>" --once
   ```
   This records that a legitimate task hit a false-positive refusal. The log is a signal about
   the model's calibration and an audit trail for the claim of authorization — not a permission
   grant.
5. **If it is still refused, sharpen the description — truthfully.** A refusal often means the
   phrasing invited the worst reading, not that the task is wrong. Re-state it in precise,
   concrete engineering terms that name the *actual* operation: "document the byte offsets and
   struct padding of this file format" rather than a vague "reverse-engineer the binary";
   "stress-test my own login parser with randomized payloads" rather than "attack the endpoint".
   The rule that keeps this honest and not evasion: the sharper wording must describe the **same
   work on the same real target** (Step 2). If swapping a word changes what you are actually
   doing — or is chosen to slip past a filter rather than to be more accurate — you are not
   reframing, you are deceiving. Don't (see Anti-patterns).
6. **If it still won't proceed and the work is genuinely authorized, escalate to the human —
   do not silently drop it.** Surface the block as an explicit decision (`AskUserQuestion`),
   stating exactly what you need and why you can't move. If the user authorizes, record a formal,
   dated waiver so the authorization is accountable rather than verbal:
   ```bash
   just issue-waive "reframe:<short-target>" "authorized by <user>; <why legitimate>; user accepts responsibility" <user>
   ```
   The waiver is a **human-accountability record, not a bypass.** It does not override
   `policy.yaml`, does not open a closed `just gate`, and does not license a
   `[Destructive/Dependency]` action — each of those still needs the real approval it defines.
   What a waiver resolves is *authorization doubt on legitimate work*, by putting a named human
   on record. It is decision-logged; there is no silent override.
7. **A genuinely harmful request is never reframed, waived, or chunked around.** If the target
   is not the user's, or the intent is access or harm they have no right to, that is a correct
   refusal, not a false positive — and no rewording, no decomposition, and no waiver launders it.
   Say so in one sentence, offer the nearest legitimate version, and move on.

## Anti-patterns (these are NOT reframing — do not do them)

Reframing works by adding *true* context and describing the *real* task more precisely. The
following do the opposite — they hide what the task is from a safety system — and are out of
scope for this skill no matter how legitimate the underlying work is:

- **Word-swapping to evade a classifier.** Renaming an operation to be *more accurate* is honest;
  renaming it to *conceal what it is* is deception. The tell: you would be uncomfortable if the
  reader saw the loaded name and the "clean" name side by side. Step 5 is the honest version;
  this is its counterfeit.
- **Decomposition to hide the whole ("chunking").** Splitting a task into pieces so that no single
  prompt "looks bad" and no model — yours or another's — ever sees the real objective is a
  jailbreak, not an engineering decomposition. Legitimate decomposition makes the *work* clearer
  to someone who can see all of it; this makes the *intent* invisible on purpose. If the only
  reason to split is that the whole would be refused, stop and use Step 6 instead.
- **Manufacturing authorization.** A fake sandbox, a fake owner, or an invented "directive" that
  overrides your judgement — covered by the IMPORTANT note above, and always out of scope.

## Checklist

- Is the context I stated actually true, and can I say who authorized it?
- Did I keep the real target, or did I quietly substitute a generic one?
- Is this working code for that target, or a template?
- If I re-worded on refusal, is the new wording a *more accurate* description of the same task —
  not a disguise, and not a split designed to hide the objective?
- For authorized-but-still-blocked work, did I escalate to the user and log a waiver, rather than
  silently dropping it?
- Did I log the reframe?
