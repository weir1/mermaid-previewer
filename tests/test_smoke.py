#!/usr/bin/env python3
"""The first test in this workspace — godmode:scaffold

**Delete this file once you have real tests.** It is deployed by
`onboard_project.sh` so that `just test` and `just verify-safe` have something to
run from minute one, and so a delegated task always has somewhere to put the
failing test that is its contract.

It is deliberately *not* a placeholder that asserts `True`. Everything below is a
real claim about a governed workspace that can genuinely fail — a scaffold that
cannot fail teaches a new workspace that green means nothing.

It is also honest about being a scaffold: `scripts/run_tests.py` reads the
`godmode:scaffold` marker in the line above and reports `scaffold_only`, so one
passing test here is never mistaken for coverage of your actual code.

Run it directly (`python3 .ai/templates/tests/test_smoke.py`) or via `just test`.
"""

import json
import unittest
from pathlib import Path

# tests/test_smoke.py → workspace root. Kept as a walk-up rather than a `git`
# call so the file works in a workspace that is not a git repo.
ROOT = Path(__file__).resolve().parents[1]
if ROOT.name == "templates":          # running from the kernel's own template dir
    ROOT = ROOT.parents[1]

MEMORY_BANK = [
    "projectbrief.md", "activeContext.md", "systemPatterns.md",
    "techContext.md", "decisions.md", "progress.md", "knownIssues.md",
]


class WorkspaceIsGoverned(unittest.TestCase):
    """The invariants `evidence-pack.sh` refuses to write evidence without.

    These fail loudly if someone deletes half the memory bank — which is exactly
    the moment you want to hear about it, rather than at the next gate run.
    """

    def test_memory_bank_is_complete(self):
        missing = [f for f in MEMORY_BANK if not (ROOT / ".ai" / "memory-bank" / f).is_file()]
        self.assertEqual(missing, [], f"missing memory files: {missing} — run `just doctor`")

    def test_policy_declares_a_blast_radius(self):
        policy = ROOT / ".ai" / "policy.yaml"
        self.assertTrue(policy.is_file(), "no .ai/policy.yaml — this workspace is ungoverned")
        text = policy.read_text()
        for key in ("always_blocked", "approval_required", "autonomous_allowed"):
            self.assertIn(key, text, f"policy.yaml declares no `{key}` section")

    def test_evidence_is_json_and_states_a_status(self):
        """Evidence is machine-read by the gate. Malformed evidence must fail
        here, not silently at the moment someone tries to push."""
        ev = ROOT / ".ai" / "memory-bank" / "evidence.json"
        if not ev.is_file():
            self.skipTest("no evidence.json yet — run `just verify-safe` once")
        data = json.loads(ev.read_text())
        self.assertIn("status", data)
        self.assertIn(data["status"], {"passed", "failed", "unverified"},
                      f"unknown gate status: {data['status']!r}")


if __name__ == "__main__":
    unittest.main()
