---
name: "Test First"
description: "Write the failing test before the fix, so the test is proven to detect the thing it claims to detect."
---

# Test-First Skill

A test written *after* the code passes tells you almost nothing: it was authored by looking
at an implementation that already worked, so it is shaped to agree with it. A test written
*before* has been observed to fail for the right reason — which is the only evidence that it
can ever catch a regression.

## The loop

1. **Write the test.** Name it after the *property*, not the function:
   `test_evidence_naming_no_task_is_not_proof`, not `test_verify_work_claim_3`.
2. **Run it. Watch it fail.** Read the failure message. If it fails for a different reason
   than you expect (import error, typo, fixture bug) the test is not yet testing anything.
   For steps 2–3, `just test-fast` is the loop to iterate on — it selects only the test
   modules reachable from what changed (PH25-T02), so watching red-then-green costs seconds,
   not the full suite's ~2 minutes. It has false negatives by construction (static import
   analysis misses dynamic imports and reflection) and is **never a gate** — step 4 still
   runs everything.
3. **Write the minimum code** to make it pass.
4. **Run the whole suite** — `just test`. A green new test with a broken old one is a
   regression you just wrote.
5. **Then** refactor, with the tests holding the behaviour still (see `refactor-safely`).

## What to test in this codebase

The kernel's job is to refuse things. So the tests that matter most are the **refusals**:

- **The negative case first.** For every "X is enforced", the primary test is the input that
  must be *rejected*. Every forgery found in this repo (a forged `passed/onboarding` gate,
  `work-done` accepting any string, a prose mention parsed as a declaration) passed its
  positive test and had no negative one.
- **The error path is not the success path.** Assert that a failed tool call, a missing file,
  and a malformed record all produce a *refusal* — not the same value as "nothing to do".
- **Fail-closed under damage.** Corrupt the record, delete the dependency, break the log:
  the verdict must stay a refusal and the exit code must not change.
- **The counter/state does not move on a refusal.** Assert the side effect *did not happen*,
  not merely that the exit code was non-zero.

## Isolation is part of correctness

Tests here write decision-log entries, evidence, and session state. **Inject a temp root;
never let a test touch the live `.ai/`.** This is not tidiness — one unpatched run appended
four fabricated verdicts to the real decision log, and because that log is append-only by
contract they are still there, skewing `just audit`.

```python
self.root = Path(tempfile.mkdtemp())
real, mod._ws_root = mod._ws_root, lambda: self.root
self.addCleanup(lambda: setattr(mod, "_ws_root", real))
```

Where a suite could plausibly leak, pin it: assert the live log's mtimes are unchanged.

## Degraded paths: mock the seam, never break the file (PH23-T04)

Testing a fallback by breaking a real file on disk and restoring it afterwards costs more
than it looks:

- it moves working-tree mtimes, which **invalidates evidence freshness** and buys a full
  re-run of the suite you were in the middle of;
- the write is invisible to `write_journal` (it is not a `Write`/`Edit` tool call), so
  `commit_scope` will not attribute it and `commit-all` may exclude the restore;
- "restored afterwards" assumes you get there. A rate limit or a crash between the break
  and the restore leaves the workspace broken and the next session debugging your test.

So: **patch the seam.** `unittest.mock.patch` the import, the reader, or the subprocess —
or copy the module into the temp root this skill already mandates and break the *copy*.

```python
with mock.patch.object(mod, "read_record", side_effect=OSError("unreadable")):
    out = mod.render()
self.assertIn("not run", out)   # the degrade is OBSERVED, not assumed
```

**And assert the degrade reaches the operator.** Taking the fallback and *reporting* it are
two different things, and only the second is worth anything:

- an error string assigned to a variable that nothing ever prints is a silent degrade;
- a parser that returns `[]` when its input is missing is a silent degrade, because the
  caller cannot tell it apart from "there was nothing to find".

For every fallback branch, name the line where the operator learns about it. If you cannot
name one, the path is silent and the test that "covers" it is covering nothing. Real
instance: `failure_digest` returned `failed_checks: []` for **every** test failure in 46
workspaces because `--quiet` starved it, and that rendered as "cause not recorded" —
indistinguishable from evidence predating the feature. Nothing on screen was ever wrong.

### Mutation testing is not fault injection

Breaking your own new code to check that your new test goes red is a different act, and it
is **required** here — a test never watched failing proves nothing, and there is no mock for
"can this test fail?". It has its own rules, because it does touch real source:

1. Mutate and restore **in the same command**, so no failure path leaves the mutation behind.
2. Restore from a copy taken in that same command (`cp x /tmp/x.bak` … `cp /tmp/x.bak x`),
   never by re-editing by hand from memory.
3. Treat the tree as dirty afterwards: the restore is a write, so **re-run the gate** before
   attesting to anything. Do this during construction, never after `verify-safe`.

## Conventions

- stdlib `unittest`, files at `tests/test_<module>.py`, run by `just test` and by pre-commit
  inside `just verify-safe`. No network, no fixtures outside the temp root.
- Group by property with a class per behaviour; the class docstring says which finding or
  task the group pins.
- A bug fix ships **with the test that reproduces it** — see `debug-root-cause`.
