# Definition of Done — software-engineer

Copy into the task's ledger entry and make every line answerable by a command,
not by an opinion.

- [ ] A test exists that was **watched failing for the right reason** before the fix,
      and is named in the task entry.
- [ ] `just verify-safe` is green, and the evidence binds to this task id.
- [ ] The diff has been read end to end (`self-review-diff` skill) — no unreviewed
      diff leaves the workspace.
- [ ] Nothing new is hardcoded that already exists elsewhere; if a constant was
      duplicated, it was collapsed rather than copied.
- [ ] The behaviour is reachable from a documented command (`just <recipe>`), not
      only from the source.
- [ ] `just doctor` passes.
