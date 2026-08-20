# Reframe (Reframe Request)

**Description:** A legitimate technical task hit a false-positive safety refusal. Supplies the
real context the prompt left out, then does the actual work on the actual target.

**When to use:** The AI refused to write a script, run a test, or complete a valid task because a
noun in the request ("scrape", "bypass", "hack", a site name) read as risky in isolation, even
though the work is authorized — your own site, your own key, your own test box.

**Action:**
1. Load the `.agents/skills/reframe-request/SKILL.md` skill.
2. Re-state the last request with the true, missing context: who owns the target and what
   authorizes the work.
3. Solve it for the **real** target — real URLs, real selectors, real endpoints. No generic
   stand-ins, no templates.
4. Log the reframe: `just off-plan "reframe-request: <request>" --once`.

**Not what this does:** it does not assert a fake sandbox or a fake authorization, does not
override `policy.yaml` or a closed `just gate`, and does not turn a genuinely harmful request
into an allowed one. If the true context cannot be stated truthfully, the refusal stands.
