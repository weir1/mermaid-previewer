# ─────────────────────────────────────────────────────────────────────────────
# GOD MODE AI OS — Context Workspace Justfile
# All commands available to the AI agent and to you (the user).
# ─────────────────────────────────────────────────────────────────────────────

# An argument VALUE is data, never shell source (PH7-T11).
#
# `just` expands `{{param}}` by pasting the value into the recipe body BEFORE the
# shell parses it, so a value carrying `;` or `$(…)` is EXECUTED and a multi-word
# value is split. Quoting does not help — `"{{msg}}"` still runs `$(…)`, and a
# literal `"` closes the quote. These recipes are how an agent writes free prose
# (issue text, session summaries, review notes, commit messages), so this is the
# highest-value place in the repo to get it right.
#
# With this setting on, arguments arrive as real shell arguments. Use `"$@"` for a
# variadic and `"$1"`, `"$2"` … for named params — NEVER `{{param}}` in a command.
# Pinned by tests/test_recipe_args.py.
set positional-arguments := true

# ── SESSION LIFECYCLE ────────────────────────────────────────────────────────

# Run at START of every AI session — audits memory, checks Telegram @context notes,
# lists Pending TickTick tasks for user approval (NEVER auto-syncs)
# Usage: just session-start             ← full briefing; starts/resumes the budget
#        just session-start --no-reset  ← briefing only, touches no state (PH7-T09)
session-start *args:
	@python3 scripts/session_lock.py require --action "session start" >/dev/null 2>&1 || true
	python3 scripts/session_start.py "$@"

# What the SessionStart hook actually emits (PH9-T16). The briefing has TWO
# audiences: `systemMessage` is the only field a human sees in the transcript,
# `hookSpecificOutput.additionalContext` is what the AI reads. Use this to check
# the human-visible half is really populated — the bug that hid G4 for two days.
# Usage: just session-hook            ← the raw JSON envelope
#        just session-hook --no-reset ← same, touching no budget state
session-hook *args:
	python3 scripts/session_hook.py "$@"

# Run at END of every session — freshens memory, writes session log, shows git status
# Usage: just session-end --summary "what changed this session"
session-end *args:
	python3 scripts/session_end.py "$@"

# ── MEMORY MANAGEMENT ───────────────────────────────────────────────────────

# Check all memory bank files for staleness (based on TTL frontmatter)
memory-decay:
	python3 scripts/memory_decay.py

# Freshen all memory bank files — resets last_verified to now (run after updating content)
freshen-memory:
	python3 scripts/freshen_memory.py

# Move closed history out of the hot memory files into .ai/memory-bank/archive/ (PH6-T01).
# DRY RUN BY DEFAULT — --apply writes. Nothing is deleted; the hot file gets a [[link]].
# Usage: just archive-memory  ·  just archive-memory --apply  ·  just archive-memory --all
archive-memory *args:
	python3 scripts/archive_memory.py "$@"

# Move CLOSED task blocks out of .ai/docs/tasks.md into .ai/docs/archive/tasks-PHASE-<N>.md
# (PH27-T01). DRY RUN BY DEFAULT — --apply writes. Nothing is deleted; task_ledger.find_task()/
# all_tasks() fall back to the archive, so a moved task is still fully resolvable.
# Usage: just archive-tasks  ·  just archive-tasks --apply
archive-tasks *args:
	python3 scripts/archive_tasks.py "$@"

# Rotate AI_CHANGELOG.md — archive entries >30 days old, refresh pinned summary block
rotate-changelog:
	python3 scripts/rotate_changelog.py

# ── VALIDATION & CI ─────────────────────────────────────────────────────────

# Default verification pipeline — runs pre-commit hooks + the test suite, then
# generates evidence.json. Captures the real exit code so evidence records pass/fail
# truthfully, and a FAILING TEST CLOSES THE GATE (PH7-T05).
#
# Why the tests run here and not only in pre-commit: onboarding never overwrites a
# workspace's own .pre-commit-config.yaml, so a hook added to the template today
# reaches only workspaces onboarded tomorrow. The justfile IS merged on upgrade, so
# this recipe is the lever that actually reaches the fleet.
# The grep avoids running the suite twice where the config already runs it — a
# workspace whose config has no test hook (all 38 of them) runs it standalone.
verify-safe:
	#!/usr/bin/env bash
	set -uo pipefail
	# PH16-T35: each stage's output is TEED to its own log so the evidence can name
	# which stage closed the gate and which checks inside it. `tee` keeps the operator's
	# live output identical — a diagnostic that silences the run it diagnoses is a
	# downgrade. The logs are temporary and never committed; only extracted check NAMES
	# reach evidence.json, because that file is tracked in 44 workspaces.
	LOGDIR=$(mktemp -d); trap 'rm -rf "$LOGDIR"' EXIT
	pre-commit run --all-files 2>&1 | tee "$LOGDIR/precommit.log"; EXIT=${PIPESTATUS[0]}
	# PH8-T03: the role-defined half. software-engineer (default, 45 workspaces)
	# takes the elif below UNCHANGED — ROLE_ID resolves to anything else and this
	# branch never triggers. Only a workspace declaring role_pack: executive-coach
	# runs domain_verifier.py instead of run_tests.py.
	ROLE_ID=""
	if [ -f scripts/role_registry.py ] && command -v python3 >/dev/null 2>&1; then
		ROLE_ID=$(python3 scripts/role_registry.py resolve --json 2>/dev/null | python3 -c "import json,sys; t=sys.stdin.read(); d=json.loads(t) if t.strip() else {}; print(d.get('id',''))" 2>/dev/null)
	fi
	if [ "$ROLE_ID" = "executive-coach" ]; then
		python3 scripts/domain_verifier.py 2>&1 | tee "$LOGDIR/tests.log"
		[ "${PIPESTATUS[0]}" -eq 0 ] || EXIT=1
	elif ! grep -q 'run_tests.py' .pre-commit-config.yaml 2>/dev/null; then
		if [ -f scripts/run_tests.py ]; then
			python3 scripts/run_tests.py --quiet 2>&1 | tee "$LOGDIR/tests.log"
			[ "${PIPESTATUS[0]}" -eq 0 ] || EXIT=1
		else
			# Not failing the gate: a partial deploy landing the justfile before
			# the script would otherwise close every gate in the fleet at once.
			echo "⚠️  scripts/run_tests.py not deployed — TESTS WERE NOT RUN."
			echo "   Run 'just fleet-upgrade --apply' from the kernel to fix this."
		fi
	fi
	bash scripts/evidence-pack.sh "safe" "$EXIT" "$LOGDIR/precommit.log" "$LOGDIR/tests.log"
	exit "$EXIT"

# Release-grade verification
verify-release:
	#!/usr/bin/env bash
	set -uo pipefail
	# PH16-T35 — see verify-safe: per-stage logs so a closed gate names its cause.
	LOGDIR=$(mktemp -d); trap 'rm -rf "$LOGDIR"' EXIT
	pre-commit run --all-files 2>&1 | tee "$LOGDIR/precommit.log"; EXIT=${PIPESTATUS[0]}
	# PH8-T03 — see verify-safe: role-defined domain check for executive-coach,
	# every other role's run_tests.py path unchanged.
	ROLE_ID=""
	if [ -f scripts/role_registry.py ] && command -v python3 >/dev/null 2>&1; then
		ROLE_ID=$(python3 scripts/role_registry.py resolve --json 2>/dev/null | python3 -c "import json,sys; t=sys.stdin.read(); d=json.loads(t) if t.strip() else {}; print(d.get('id',''))" 2>/dev/null)
	fi
	if [ "$ROLE_ID" = "executive-coach" ]; then
		python3 scripts/domain_verifier.py 2>&1 | tee "$LOGDIR/tests.log"
		[ "${PIPESTATUS[0]}" -eq 0 ] || EXIT=1
	elif ! grep -q 'run_tests.py' .pre-commit-config.yaml 2>/dev/null; then
		if [ -f scripts/run_tests.py ]; then
			python3 scripts/run_tests.py --quiet 2>&1 | tee "$LOGDIR/tests.log"
			[ "${PIPESTATUS[0]}" -eq 0 ] || EXIT=1
		else
			# Not failing the gate: a partial deploy landing the justfile before
			# the script would otherwise close every gate in the fleet at once.
			echo "⚠️  scripts/run_tests.py not deployed — TESTS WERE NOT RUN."
			echo "   Run 'just fleet-upgrade --apply' from the kernel to fix this."
		fi
	fi
	bash scripts/evidence-pack.sh "release" "$EXIT" "$LOGDIR/precommit.log" "$LOGDIR/tests.log"
	exit "$EXIT"

# Run this workspace's test suite. One runner for the whole fleet: stdlib unittest
# for `tests/test_*.py`, `npm test` where package.json declares one, and an honest
# "none" — never a pass — where there are no tests at all.
# Usage: just test              run them
#        just test --discover   count them without running anything
test *args:
	python3 scripts/run_tests.py "$@"

# Run only the tests this change could have broken — the TDD inner loop, so a
# one-line fix stops costing a full suite run. Selection is a static import
# graph: it reads `import x`, so it CANNOT see a test that shells out, reads a
# data file, or resolves a name at run time. It prints what it chose, what it
# skipped, and every changed path it could not link at all.
# ⚠️  NOT A GATE, by construction. `just verify-safe` runs everything and is the
#     only thing `just gate` reads. Never wire this into the gate: the moment it
#     can satisfy the gate, its accepted false negatives stop being acceptable.
# Usage: just test-fast                      changed vs HEAD, untracked included
#        just test-fast scripts/health.py    a specific change
#        just test-fast --list               report the selection, run nothing
test-fast *args:
	python3 scripts/test_impact.py "$@"

# Re-run ONLY the test(s) that failed on the last recorded run (PH27-T10) —
# reads .ai/last-test-run.log, re-runs exactly those dotted names. Confirming
# a one-line fix costs that test's own time, not the full ~2,500-test suite.
# ⚠️  NOT A GATE, same rule as test-fast: never touches evidence.json or the
#     last-run record. `just verify-safe`/`prep-close` still run everything.
test-failed *args:
	python3 scripts/run_tests.py --failed "$@"

# Sub-second ledger consistency check (PH27-T04) — activeContext.md prose vs
# tasks.md checkboxes, and orphaned (In Progress) markers nothing will bind to.
# Defines no rule of its own: a CLI surface over task_ledger's existing
# disagreements()/active_task_report()/orphan_warning(), not a second copy.
# ⚠️  NOT A GATE: `just verify-safe` still runs the full ledger tests; this is
#     a debug-loop accelerant, same as test-fast and test-failed.
check-ledger *args:
	python3 scripts/check_ledger.py "$@"

# Stamp AI_CHANGELOG.md (PH27-T07) — derives the IST header and the
# changed-path list from `git status --porcelain` so neither is retyped.
# The summary is still yours: just log "what changed and why"
log *args:
	python3 scripts/changelog_log.py "$@"

# Validate the memory retrieval graph — every [[file#anchor]] link must resolve
check-links:
	python3 scripts/check_links.py

# Validate OS invariants (policy.yaml structure + evidence schema + memory links)
validate:
	python3 scripts/validate_os.py

# THE validation gate — one check, used by doctor, session-start, and every side effect.
# Verifies evidence exists, passed, exit 0, from a safe/release pipeline, and is FRESH
# (newer than your newest working-tree change). Exit 0 = open, 1 = blocked.
# Usage: just gate  ·  just gate --json  ·  just gate --require-task PH5-T01
gate *args:
	python3 scripts/gate_check.py "$@"

# Preflight policy decision for a single operation — returns a JSON verdict + exit code.
# Usage: just policy-check --op write --path scripts/foo.py
policy-check *args:
	python3 scripts/policy_check.py "$@"

# Read the decision log — every policy + gate verdict the OS reached (PH6-T13).
# Usage: just decisions  ·  just decisions --tail 20  ·  just decisions --days 7 --json
decisions *args:
	python3 scripts/decision_log.py "$@"

# Per-session audit — joins the decision log to the session logs (PH5-T03).
# Read-only: shows autonomy rate, what was denied/blocked/overridden, and each
# session's outcome. Counting is delegated to decision_log.summarize (one counter).
# Usage: just audit  ·  just audit --days 7  ·  just audit --sessions 3  ·  just audit --json
audit *args:
	python3 scripts/audit.py "$@"

# Did each session actually follow AGENTS.md, from the record? (PH15-T03)
# Read-only: replays the decision log + session logs against ~9 obligations,
# each reported pass/fail/vacuous_pass/unobservable/excluded — never inferred
# from a session's own summary text. Reuses audit.py's session join.
# Usage: just protocol-score  ·  just protocol-score --sessions 5  ·  just protocol-score --json
protocol-score *args:
	python3 scripts/protocol_score.py "$@"

# Show last 5 session evaluation logs
session-summary:
	@ls -t .ai/session-log/*.json 2>/dev/null | head -5 | xargs -I{} sh -c 'echo "── {}"; cat {}; echo'

# ── FLEET (kernel only — deploys this OS to every governed workspace) ───────

# Fresh-context adversarial review (PH20-T10) — `self-review-diff` is the SAME
# session reviewing its own work; this spawns a brand-new `claude -p`
# subprocess with no tools and no channel back to this session's reasoning,
# so it judges the target cold. Composes with self-review, does not replace
# it. Verdict is bound to the target's content hash (same rule as
# self-review: edit it and the record no longer covers it) and
# decision-logged with source=second-opinion, distinguishable in `just audit`.
# Usage: just second-opinion diff                     ← the session diff
#        just second-opinion .ai/plans/PH9-T01.md      ← any file, verbatim
#        just second-opinion "the fix handles X"        ← a free-text claim
#        just second-opinion status <target>            ← is it covered?
#        just second-opinion check <target>             ← exit 6 if not
second-opinion *args:
	python3 scripts/second_opinion.py "$@"

# ── EFFECTIVENESS (both AIs) ─────────────────────────────────────────────────

# Health-check BOTH AI entrypoints (Claude + Antigravity) + core invariants
doctor:
	python3 scripts/doctor.py

# Is this workspace working RIGHT NOW? (PH16-T01) — the doctor verdict live,
# plus the last recorded test run, which is reported NOT RUN rather than green
# once anything in the tree has changed since it was taken. This is what the
# session briefing leads with; run it by hand any time you want it fresh.
# Exits 1 unless both are green. Usage: just health · --plain · --json
health *args:
	python3 scripts/health.py "$@"

# Stamp every doc with the OS version it is true for, when it last changed, and
# how many times — ALL DERIVED FROM GIT, never typed (PH22-T07). A hand-written
# revision counter is a lie with a schedule: nobody increments it, and the first
# stale value teaches every reader to ignore the field. `doctor` FAILS when a
# stamp disagrees with git. Run `--apply` during closure, before committing.
# Usage: just doc-stamps  ·  just doc-stamps --apply
doc-stamps *args:
	python3 scripts/doc_stamp.py "$@"

# ── CLOSURE PIPELINE (PH23-T02) ─────────────────────────────────────────────

# Run the whole closure DAG up to the review, in one ordered pass (PH23-T02):
# codemap → doc-stamps --apply → doctor (advisory) → verify-safe. Idempotent —
# every step it calls already is — so "did I already run this?" stops mattering.
# It ends with the tree harmonized and attested, which is the state in which
# recording a self-review actually binds. It does NOT review for you: a review
# nobody read certifies nothing, and that gap is deliberate.
# Then: just review-diff → just self-review pass "..." → just ship "message".
# Usage: just prep-close
prep-close:
	python3 scripts/closure_pipeline.py prep

# The other half: commit → push → close git-push, and it REFUSES unless
# everything a commit depends on is settled — asked of the DAG itself
# (`closure_status.blockers`), never a second hand-kept list. A voided review, a
# stale gate, a drifted stamp or an uncredited task each stop it before the
# commit. This is the brake the observed 68-minute session did not have: it kept
# committing over invalidated inputs and repairing with `--amend`, four times.
# A commit refused as PARTIAL (commit_scope exit 5) is reported with the paths it
# would leave behind and BOTH ways on, as `just ship` commands — the pipeline used
# to die telling you to "re-run with --all" while having no --all to re-run with,
# so three sessions running ended in a hand-typed `commit-all --all` (PH24-T12).
# Usage: just ship "what this session did"
#      · just ship "msg" --all             stage the whole tree, knowingly
#      · just ship "msg" --allow-partial   leave the unattributed paths out
ship message *args:
	python3 scripts/closure_pipeline.py ship "$@"

# ── THE `lite` PROFILE'S TWO-COMMAND CLOSURE (PH26-T01) ─────────────────────

# Half one: session-end → prep-close → print the diff under review, in ONE pass.
# Same DAG, same order, same phase-1 precondition as `prep-close` — the saving is
# turns, not checks. It STOPS at the review, exactly where `prep-close` stops.
# Only available where `.ai/workspace.yaml` declares a profile with fast closure
# (`just profile-set lite`); in `full` it refuses and names the way on.
# Then: read the diff → just self-review pass "..." → just land "msg" "next step".
# Usage: just wrap "what this session did"
wrap summary:
	python3 scripts/closure_pipeline.py wrap "$1"

# Half two: commit → push → close git-push → close docs → close issues → handover.
# Every refusal `just ship` makes, `land` makes — it delegates rather than
# re-implementing the precondition, so a voided review or a stale gate still stops
# it before the commit. What it adds after the push are the two closure records
# that write only gitignored state (which is why `ship` cannot run them and why
# nothing ever reminded anyone to) plus the handover the next session boots from.
# PUSH IS `[Destructive/Dependency]`: ask the user before running this.
# Usage: just land "what this session did" "the next step to pick up"
land message next="":
	python3 scripts/closure_pipeline.py land "$1" --next "$2"

# Where is this session in the closure pipeline? (PH23-T01) — the Andon board.
# Reads the live tree and prints every closure step as done / stale / pending,
# plus the ONE next action, plus any attestation that is already condemned by an
# unsettled input (a recorded review over a drifted codemap, a fresh gate over
# stale stamps). Read-only: it never repairs anything. Run it whenever you are
# unsure what you have already done — that uncertainty at 100k tokens is the
# thing that cost the observed session four amend cycles.
# The order it walks is DECLARED, in scripts/closure_status.py's STEPS, and a
# test pins that the declaration is a real topological order.
# Usage: just closure-status  ·  --json  ·  --next (prints one step id)
closure-status *args:
	python3 scripts/closure_status.py "$@"

# Regenerate .ai/codemap.md — module → purpose → entry point → the recipe that
# runs it, derived from the source and never hand-written (PH7-T07). Read it
# instead of grepping; it is the source-side twin of memory-bank/INDEX.md.
# `doctor` FAILS when it no longer matches the tree, naming what drifted.
# Usage: just codemap  ·  just codemap --check  ·  just codemap --json
codemap *args:
	python3 scripts/codemap.py "$@"

# Regenerate .ai/memory-bank/knownIssues-index.md — one compact line per issue
# (date, status, title, `path:line`) instead of the full 313-line file (PH27-T13).
# knownIssues.md itself is never touched — nothing moves, nothing is trimmed.
# `doctor` FAILS when the index drifts from the real file.
# Usage: just issues-index  ·  just issues-index --check  ·  just issues-index --json
issues-index *args:
	python3 scripts/issues_index.py "$@"

# One symbol's signature, docstring and body with exact line numbers — driven by
# `ast`, not regex — so inspecting one helper stops costing the whole file
# (PH25-T01). A name shared by two scopes is refused, not guessed: re-ask with
# the dotted form it lists, e.g. `ClassName.method`.
# Usage: just sym scripts/task_transition.py done  ·  just sym <file> <Class.method>
sym *args:
	python3 scripts/symbol_slice.py "$@"

# Portable Markdown briefing — paste into claude.ai web / Antigravity chat (no hooks there)
session-brief:
	python3 scripts/session_brief.py

# Cross-AI handoff — log to session-ledger + print a handoff prompt for the next AI
# Usage: just handoff "next step"
#        just handoff "next step" claude PH4-T10
handoff msg frm="claude" task="":
	python3 scripts/handoff.py "$1" --from "$2" --task "$3"

# ── SESSION BUDGET (work cap from `session_budget.work_max` + 3 closure) ─────

# Estimate the context cost of booting this workspace (AGENTS.md § CONTEXT BUDGET)
# Usage: just tokens  ·  just tokens --brief  ·  just tokens --json
tokens *args:
	python3 scripts/token_budget.py "$@"

# What THIS session has actually done since boot (PH14-T03): tool calls, distinct
# files touched, elapsed time, ranged-vs-whole-file read ratio — read from Claude
# Code's own transcript, on demand. Manual and read-only: nothing here blocks a
# read or auto-triggers a handover, and it degrades honestly ("not available")
# outside Claude Code. `just tokens` measures the boot set; this measures what
# happened after boot — the two are complementary, not overlapping.
context-status:
	python3 scripts/context_status.py

# Show the per-session work budget (2 work tasks + 3 closure tasks)
budget:
	python3 scripts/session_budget.py status

# Reset the budget counter for a new session (session_start does this automatically).
# REFUSES to discard credit you already earned unless you say --force (PH7-T09) —
# a mid-session reset destroys work credit and narrows the `close git-push` review.
# Usage: just budget-reset  ·  just budget-reset --force
budget-reset *args:
	python3 scripts/session_budget.py start "$@"

# Declare a task in progress — the title, [complex] marker and DoD are READ from
# .ai/docs/tasks.md, never retyped, so activeContext.md cannot desync from it
# (PH27-T02). Refuses (writing nothing) when another task is already in progress.
# Usage: just task-start "PH27-T02"
task-start *args:
	python3 scripts/task_transition.py start "$@"

# Finish a task: tick tasks.md, retire the declaration from activeContext.md, and
# record a budget-sized entry in progress.md — atomically, all three or none.
# This does NOT credit the work slot; run `just work-done` for that (PH27-T02).
# Usage: just task-done "PH27-T02" "what this task actually did"
task-done *args:
	python3 scripts/task_transition.py done "$@"

# Claim a completed WORK task (max 2/session) — REFUSES unless the task is declared
# with a DoD and the gate is open with evidence naming that task (PH7-T02).
# Usage: just work-done "PH5-T01"
#        just work-done "PH5-T01" "docs-only task, no runnable gate"   ← logged override
work-done id override="":
	#!/usr/bin/env bash
	set -uo pipefail
	ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
	python3 "$ROOT/scripts/session_lock.py" require --action "work-done $1" || exit 1
	if [ -n "$2" ]; then
	  python3 scripts/session_budget.py work "$1" --override "$2"
	else
	  python3 scripts/session_budget.py work "$1"
	fi

# Log a CLOSURE task: git-push | docs | issues  (3/session)
# `git-push` REFUSES unless a recorded self-review covers the session's diff (PH7-T04).
# Usage: just close git-push
#        just close git-push "no code changed, docs-only session"   ← logged override
close kind override="":
	#!/usr/bin/env bash
	set -uo pipefail
	if [ -n "$2" ]; then
	  python3 scripts/session_budget.py close "$1" --override "$2"
	else
	  python3 scripts/session_budget.py close "$1"
	fi

# ── GOVERNANCE PROFILE (PH26-T01) ───────────────────────────────────────────

# How much ceremony does THIS workspace run? `full` (the default, and what every
# workspace ran before profiles existed) is 2 work slots and step-by-step closure.
# `lite` is for a product workspace in a build sprint: more work slots, and closure
# collapsed to `just wrap` → self-review → `just land`.
# NO profile weakens a gate — verify-safe, the test suite, the pre-work brief,
# plan-before-code and the pre-push self-review are required in every one of them.
# A profile is DECLARED in .ai/workspace.yaml, never inferred from the repo.
# Usage: just profile                              the active one, and what it costs
#        just profile-set lite --until 2026-09-15  declare it, with an expiry
#        just profile-set full                     back to the kernel's discipline
profile *args:
	python3 scripts/workspace_profile.py show "$@"

profile-set name *args:
	python3 scripts/workspace_profile.py set "$@"

# ── PLAN BEFORE CODE (PH7-T03) ──────────────────────────────────────────────

# Scaffold .ai/plans/PH#-T##.md, or report on the plan already there.
# A task marked [complex] in the ledger cannot be credited without a filled plan.
# Usage: just plan "PH7-T03"           scaffold it (never overwrites)
#        just plan "PH7-T03" --check   validate only; exit 1 if unwritten
plan id *args:
	python3 scripts/plan.py "$@"

# ── PRE-WORK BRIEF (PH22-T01) ───────────────────────────────────────────────

# No task is credited on a discussion that never happened. Write the brief in
# plain English, discuss it, then record HIS OWN WORDS with --accept.
# A brief with an empty operator justification is refused: the AI must not be
# able to hold the discussion with itself.
# Usage: just brief "PH22-T01"                        scaffold it (never overwrites)
#        just brief "PH22-T01" --check                validate only; exit 1 if not ok
#        just brief "PH22-T01" --accept "his words"   record his justification
#        just brief --ratio                           governance-vs-product only
brief *args:
	python3 scripts/prework.py "$@"

# ── ROLE PACKS (PH8-T02) ────────────────────────────────────────────────────

# One OS, many kinds of workspace. Not every governed workspace is a codebase —
# @seduction, @life, @fuel and @leanmuscle run this kernel and hold protocols and
# logs, not source. A role pack (.ai/roles/<id>/) says which memory-bank files that
# KIND of workspace must have, what counts as destructive there, and what done
# means. A workspace declares one via `role_pack:` in .ai/workspace.yaml; omitting
# it resolves to software-engineer, i.e. exactly today's behaviour.
# Usage: just roles                 list the packs (✅ usable · 📝 draft)
#        just roles resolve         the active role here, and what it requires
#        just roles show <id>       one pack in full
roles *args:
	python3 scripts/role_registry.py "$@"

# ── VERSION CONTRACT (PH22-T02) ─────────────────────────────────────────────

# What working features belong to this version — agreed in plain English BEFORE
# the work, with the technical detail beside it. Nothing here is verified by
# assertion: a test is asked of the runner, an artefact is stat'd, an attestation
# expires. A bump is refused while the current rung holds an unverified feature.
# Usage: just versions              show the current rung and what remains
#        just versions --check      validate; exit 1 if unreadable or unproven
#        just versions --scaffold   write the template (agree it, then build it)
versions *args:
	python3 scripts/versions.py "$@"

# ── WORKSPACE PLAN (PH9-T01) ────────────────────────────────────────────────

# What this workspace is FOR — written by you in plain English, extended by the
# AI, agreed by you, and only then turned into docs.
# Usage: just plan-workspace              scaffold .ai/plan.md, or report status
#        just plan-workspace --check      validate only; exit 1 unless agreed
#        just plan-workspace --agree      draft → agreed (refuses if incomplete)
#        just plan-workspace --generate   write .ai/docs/prd.md from the goals
plan-workspace *args:
	python3 scripts/plan_workspace.py "$@"

# The open decisions on the plan, as labelled options — hand these to
# AskUserQuestion (or render the numbered menu). Never ask in open prose.
# Usage: just plan-discuss  ·  just plan-discuss --json
plan-discuss *args:
	python3 scripts/plan_workspace.py --discuss "$@"

# agree → version → docs in one run. Stops at the first refusal and says which
# steps it did not attempt. Proposes a version; never stamps one.
plan-finalize *args:
	python3 scripts/plan_workspace.py --finalize "$@"

# ── GOAL PROGRESS (PH9-T08) ─────────────────────────────────────────────────

# How far along is each goal — counted from the ledger and the credit record,
# never typed. `--unmapped` lists the tasks that declare no goal.
goals *args:
	python3 scripts/goal_progress.py "$@"

# ── SESSION INTENT (PH9-T04) ────────────────────────────────────────────────

# What this workspace is for, its standing, and why/impact for what's queued
# this session — leads every `session-start` briefing. `--full` also renders
# the full goal-progress + effort-forecast views (asked for, not pushed).
# Usage: just standing  ·  just standing --full
standing *args:
	python3 scripts/session_open.py "$@"

# ── WORKSPACE AUDIT (PH9-T02) ───────────────────────────────────────────────

# What this workspace actually is, before anyone asks what it should be — git
# activity, tests/docs presence, ledger totals, and goal completion where a
# plan already exists. Read-only. Use before scaffolding a plan for an
# existing workspace: audit first, then ask the end goal, then reconcile.
# Usage: just audit-workspace              plain-English report
#        just audit-workspace --json
#        just audit-workspace --standing   paste-ready "What I want" paragraph
audit-workspace *args:
	python3 scripts/workspace_audit.py "$@"

# ── EFFORT FORECAST (PH9-T09) ───────────────────────────────────────────────

# How many more sessions this plan needs, from measured velocity — refuses to
# guess when there isn't enough session history yet.
# Usage: just forecast              default 10-session window
#        just forecast --window 5
forecast *args:
	python3 scripts/effort_forecast.py "$@"

# ── VERSION PLAN (PH9-T03) ──────────────────────────────────────────────────

# The next version bump this workspace has earned, tied to its plan's goals —
# SemVer for code, editions for non-code. Proposes only; never writes a stamp.
# Usage: just version-plan
#        just version-plan --json
version-plan *args:
	python3 scripts/version_plan.py "$@"

# ── OFF-PLAN REQUESTS (PH9-T05) ─────────────────────────────────────────────

# Is this request additional to what is planned? Names it as ADDITIONAL, prices
# it in sessions, and offers add / one-off / drop. Read-only until you choose.
# Usage: just off-plan "add a telegram bot"           notice, or silence
#        just off-plan "..." --kind work              override the classifier
#        just off-plan "..." --add | --once | --drop  record the choice
off-plan *args:
	python3 scripts/off_plan.py "$@"

# ── LEDGER AUDIT (PH9-T14) ──────────────────────────────────────────────────

# Has a task the ledger calls pending quietly become true? For each open task,
# checks whether the artefacts its `DoD:` names already exist. Pure read — it
# reports the disagreement and never ticks the box. Exit 1 when it finds one.
# Usage: just ledger-audit          the ranked report
#        just ledger-audit --all    list every refusal, not the first 5
#        just ledger-audit --json   machine-readable
ledger-audit *args:
	python3 scripts/ledger_audit.py "$@"

# ── CONFORMANCE (PH15-T01) ──────────────────────────────────────────────────

# The mirror of ledger-audit: does a task the ledger calls DONE still hold? For
# each completed task, re-checks that the artefacts its `DoD:` names still exist
# and that any test it names is still collected. Four buckets, and only
# `verified` is a pass — a task nothing can check is never scored as passing.
# Pure read. Exit 1 only on disproof, so it can gate a release.
# Usage: just conformance             the report, broken first
#        just conformance --no-tests  artefacts only (skips runner probes)
#        just conformance --all       list every unverifiable task
#        just conformance --json      machine-readable
conformance *args:
	python3 scripts/conformance.py "$@"

# ── USAGE GUIDE CHECK (PH15-T02) ────────────────────────────────────────────

# Does doc/USER_GUIDE.md still describe the OS that exists? Fails when a recipe
# has no entry, and when an entry names a recipe that is gone. A recipe is
# documented by DECLARING itself as a table row's first cell — a mention in
# prose is not an entry. An absent guide is reported, not failed.
# Usage: just guide-check  ·  just guide-check --json
guide-check *args:
	python3 scripts/guide_check.py "$@"

# ── SESSION CLOSE (PH9-T06) ─────────────────────────────────────────────────

# What this session credited and how the goals moved — read-only inspection,
# independent of `session-end` (which prints the same report and, by default,
# writes any newly-met goals back to .ai/plan.md).
# Usage: just close-report            report only
#        just close-report --apply    also write back newly-met goals
close-report *args:
	python3 scripts/session_close.py "$@"

# ── SELF-REVIEW (PH7-T04) ───────────────────────────────────────────────────

# Show the session's own diff and whether a recorded review covers it.
# Usage: just self-review-status            (base = the session's start commit)
#        just self-review-status abc1234    (base = an explicit ref)
self-review-status base="":
	#!/usr/bin/env bash
	set -uo pipefail
	BASE="$1"
	python3 scripts/self_review.py status ${BASE:+--base "$BASE"}

# Print the exact text under review — read THIS, then record a verdict.
# Compact by default (PH27-T12): full hunks for source/script files, a
# `git diff --stat` line for generated/doc/data files — the reviewed content
# hash is unaffected either way, so this never narrows what the gate checks.
# Usage: just review-diff                  ← compact (default)
#        just review-diff "" full          ← full hunks for every file
#        just review-diff abc1234 full     ← full hunks, against an explicit base
review-diff base="" full="":
	#!/usr/bin/env bash
	set -uo pipefail
	BASE="$1"
	FULL="$2"
	ARGS=(diff)
	[ -n "$BASE" ] && ARGS+=(--base "$BASE")
	[ -n "$FULL" ] && ARGS+=(--full)
	python3 scripts/self_review.py "${ARGS[@]}"

# Record a self-review of the session's diff. Read `just review-diff` FIRST.
# The record is bound to the diff's content hash: any later edit voids it.
# Usage: just self-review pass "checked the gate paths + new tests; no side effects"
#        just self-review pass-with-findings "…" "medium: unbounded read in x.py:42"
#        just self-review fail "…" "high: close() bypasses the gate"
self-review verdict note finding="" tool="manual":
	#!/usr/bin/env bash
	set -uo pipefail
	ARGS=(--verdict "$1" --note "$2" --tool "$4")
	[ -n "$3" ] && ARGS+=(--finding "$3")
	python3 scripts/self_review.py record "${ARGS[@]}"

# Generate a COMPLETE handover (full context in one file) + end-of-session prompt
# Usage: just handover "next step"
handover msg frm="claude":
	python3 scripts/handover.py "$1" --from "$2"

# ── TICKTICK (ALWAYS ASK USER FIRST) ────────────────────────────────────────

# Sync approved (Pending) tasks to TickTick WITH a reminder — ONLY after user approval.
# AI MUST show the (Pending) list and ask which to push + when to be reminded BEFORE running.
# Usage:
#   just tt-sync                              → all Pending (use only after AI has asked)
#   just tt-sync "PH4-T01,PH4-T03"            → only these tasks
#   just tt-sync "PH4-T01" "tomorrow 9am"     → these tasks, reminder tomorrow 9am
#   just tt-sync "" "friday 6pm"              → all Pending, reminder friday 6pm
tt-sync ids="" due="":
	#!/usr/bin/env bash
	set -uo pipefail
	ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
	python3 "$ROOT/scripts/gate_check.py" --action "TickTick sync (external side effect)" || exit 1
	NAME=$(basename "$ROOT")
	TAG=$(echo "$NAME" | tr '[:upper:]' '[:lower:]' | tr ' ' '_')_workspace
	ARGS=()
	[ -n "$1" ] && ARGS+=(--only "$1")
	[ -n "$2" ] && ARGS+=(--due "$2")
	python3 "$ROOT/scripts/ticktick_sync.py" "$TAG" "$NAME" ${ARGS[@]+"${ARGS[@]}"}

# ── TELEGRAM ────────────────────────────────────────────────────────────────

# Show all unrouted notes in Received_Notes/ (global inbox)
check-inbox:
	python3 scripts/check_global_inbox.py

# ── ISSUE TRACKING ──────────────────────────────────────────────────────────

# Log an issue to .ai/memory-bank/knownIssues.md. Every entry carries a `test:`
# field — the regression test that proves it fixed, or a DECLARED absence
# `(none yet)`, which is what makes the gap countable (PH7-T06).
# Usage: just note-issue "Issue title" "Full description"
#        just note-issue "Title" "Desc" "tests/test_x.py::WidgetTest::test_it"
note-issue title description test="":
	#!/usr/bin/env bash
	set -uo pipefail
	if [ -n "$3" ]; then
	  python3 scripts/note_issue.py "$1" "$2" "$3"
	else
	  python3 scripts/note_issue.py "$1" "$2"
	fi

# Mark an issue Resolved — REFUSED unless a named regression test exists AND this
# workspace's own runner collects it (PH7-T06). Issues filed before 2026-08-08 are
# grandfathered. A refusal writes nothing.
# Usage: just resolve-issue "widget explodes" "tests/test_x.py::WidgetTest::test_it" "PH7-T06"
#        just resolve-issue "widget explodes" "" "PH7-T06"   ← use the ref already on the entry
resolve-issue match test="" by="":
	python3 scripts/note_issue.py resolve "$1" --test "$2" --by "$3"

# Waive the regression-test requirement for ONE issue — demands a written reason
# and lands in .ai/decision-log/. For entries that are notices, not defects.
# Usage: just issue-waive "37 workspaces" "a filed notice, no code change proposed" "PH7-T06"
issue-waive match reason by="":
	python3 scripts/note_issue.py resolve "$1" --no-test "$2" --by "$3"

# Open issues naming no regression test — the real gap, grandfathered ones flagged.
issues-gap:
	python3 scripts/note_issue.py --gap

# Send a finding to ANOTHER workspace — as a note, never as an edit (PH16-T22 rule A).
# Found a bug in the kernel (or in a peer) while working here? Do not write the fix
# into its tree: that binds its evidence to no task, closes its gate for whoever is
# working there, and voids its review hash. This files an issue in its knownIssues.md
# tagged with this workspace, and drops the reproduction as text in its .ai/inbox/.
# The PreToolUse guard denies the edit path, so this is the way through.
# Usage: just finding "@context" "doctor advertises a remedy onboarding never applies" "…"
#        just finding "@context" "title" "desc" "repro steps" "tests/x.py::C::test_y"
finding to title description repro="" test="":
	python3 scripts/finding.py send --to "$1" --title "$2" --desc "$3" \
	  --repro "$4" --test "$5"

# Findings OTHER workspaces sent here, waiting in .ai/inbox/. Read one with
# `just finding-show <name>`. Each needs a task in THIS workspace's ledger to act on.
findings:
	python3 scripts/finding.py list

# Print one received finding in full.
# Usage: just finding-show 2026-08-14-life-doctor-advertises-a-remedy.md
finding-show name:
	python3 scripts/finding.py show "$1"

# Am I running the current OS? (PH16-T25) — answered from CONTENT, not from the
# hand-typed version string. That string is copied into every workspace verbatim at
# deploy time, so a workspace fifteen files behind reads identically to a current one;
# measured on @jobscraper 2026-08-14, both said `3.5.0 / 2026-08-10` while one had no
# scripts/finding.py at all. With no argument: this workspace's own digest.
# Usage: just deployed-digest
#        just deployed-digest --compare /Users/moind/Documents/JobScraper
deployed-digest *args:
	python3 scripts/deploy_digest.py "$@"

# ── DELEGATION (PH10, Goal G8) ───────────────────────────────────────────────
# The operator's real constraint: a few Opus hours/day, abundant Gemini/Sonnet
# the rest of the time — "I can't trust 'em because they create something
# else." The contract replaces trust. models.yaml + model_registry.py (T01) is
# the first piece: know which model is running and what its tier may do.

# List the model registry — name, tier, cost, notes.
models:
	python3 scripts/model_registry.py list

# Self-report the running model for this session (no auto-detection is
# possible in this environment — see model_registry.py's module docstring).
models-set name:
	#!/usr/bin/env bash
	set -uo pipefail
	python3 scripts/model_registry.py set "$1"

# The resolved running model: declared name, tier, permitted/denied actions.
models-resolve:
	python3 scripts/model_registry.py resolve

# Scaffold a delegation contract at .ai/delegation/PH#-T##.md (never overwrites).
delegate task:
	#!/usr/bin/env bash
	set -uo pipefail
	python3 scripts/delegation.py "$1"

# Validate a delegation contract: refuses placeholders and an empty allowlist;
# RUNS the named test command and refuses if it already passes; on a genuine
# failure, records the failure (timestamp + output) into the contract.
delegate-check task:
	#!/usr/bin/env bash
	set -uo pipefail
	python3 scripts/delegation.py "$1" --check

# The leash (PH10-T03): is the active contract's allowlist currently satisfied?
# A no-op ("not enforced") whenever there is no active, complete contract.
leash-status:
	python3 scripts/leash.py allowlist

# The leash (PH10-T07): is the active contract's iteration limit still under
# budget? A no-op ("not enforced") whenever there is no active, complete
# contract. No override — re-scope the contract (raise the limit) instead.
leash-iterations:
	python3 scripts/leash.py iterations

# Excuse the CURRENT session diff's allowlist violation for a task — bound to
# that diff's own content hash, so any further edit silently voids it again.
leash-override task reason:
	#!/usr/bin/env bash
	set -uo pipefail
	python3 scripts/leash.py override "$1" "$2"

# The review handback (PH10-T04): record a verdict for the ACTIVE delegation
# contract, bound to the current diff's content hash. REFUSED unless the
# running model's resolved tier permits approve/reject — an executor cannot
# approve its own work. Required before `close git-push` on a delegated task.
# Usage: just delegate-review pass "checked the allowlist and the new tests"
#        just delegate-review fail "the diff touches a file outside scope"
delegate-review verdict note:
	#!/usr/bin/env bash
	set -uo pipefail
	python3 scripts/delegate_review.py "$1" "$2"

# Is the active delegation contract's diff covered by a passing review?
delegate-review-status:
	python3 scripts/delegate_review.py status

# The review handback's other half (PH10-T05): does a contract's static
# `planned_by:` line match the decision-log entry logged when it was
# scaffolded? corroborated / contradicted (flagged) / unverifiable — never
# a bare true/false. `just audit --by-model` is the companion: who actually
# carried the credited work, read from the log, not typed on a contract.
delegate-corroborate task:
	#!/usr/bin/env bash
	set -uo pipefail
	python3 scripts/attribution.py check-contract "$1"

# One paste-able boot set for a task (PH20-T04): its DoD/Goal, its plan (if
# filed), the codemap rows and INDEX.md pointers it actually names, and any
# open known issue naming it — assembled from existing sources, never a
# second index. Directly serves PH10: this is the onboarding artifact a
# delegation contract's executor needs, built once rather than reinvented
# per delegate.
# Usage: just context PH20-T04  ·  just context PH20-T04 --json
context *args:
	python3 scripts/context_pack.py "$@"

# ── MESH & NORTH-STAR (PH11/PH16, Goal G9) ───────────────────────────────────
# 38 isolated workspaces move him up ONE staged ladder — freelance income ->
# confidence -> US-remote + local AI jobs -> visa sponsorship (stage 1 exits
# at $1,000/month sustained 3 months). Kernel-only: the ladder itself.

# THIS workspace's own self-report (PH16-T04): its declared ladder stage
# (`.ai/plan.md`'s `ladder_stage:`, or "undeclared") + commits over the last
# N days (default 30). Deployed fleet-wide — every workspace answers for
# itself.
ladder-stage days="30":
	#!/usr/bin/env bash
	set -uo pipefail
	python3 scripts/ladder.py --days "$1"

# PH11-T01 Slice 1 (Goal G9): this workspace's mesh declaration — role,
# depends_on/serves, which ladder stage its work advances. Missing renders
# "undeclared", never a guessed role. Deployed fleet-wide.
workspace-check:
	python3 scripts/workspace_declare.py

# Write a .ai/workspace.yaml template — refuses to overwrite an existing one,
# same contract `just plan-workspace`'s scaffold already holds.
workspace-declare:
	python3 scripts/workspace_declare.py --scaffold

# ── SESSION LOCK (PH16-T22) ─────────────────────────────────────────────────

# Who currently holds this workspace? One writer per workspace: a second session
# writing here defeats the evidence↔task binding, the gate (its red test closes
# yours) and the self-review hash (its next keystroke voids your review), so
# `commit-all` and `work-done` require the lock.
# A stale lock (dead agent pid, or no heartbeat in 8h) is reclaimed automatically.
session-lock *args:
	python3 scripts/session_lock.py status "$@"

# Release the lock. Yours: just `just unlock`. Someone else's — a crashed session
# that never released it — needs `--force`, which is logged as the override it is.
unlock *args:
	python3 scripts/session_lock.py unlock "$@"

# ── GIT ─────────────────────────────────────────────────────────────────────

# Stage and commit the files THIS session touched — BLOCKED unless the gate is
# open, and unless THIS session holds the workspace lock (PH16-T22).
# Staging is path-scoped (rule D): `git add -A` swept a concurrent writer's edits,
# and work a previous session left behind, into this commit — which then claimed
# authorship of changes nobody here made or reviewed. A change is attributable
# when it was modified after this session took the lock; anything else (a deletion,
# an unreadable stat, an older edit) is NAMED and left for its author.
# Pass `--all` as the second argument for the old blanket behaviour.
# The gate is stale by definition if you changed files after the last `just verify-safe`,
# so the normal flow is: just verify-safe → just commit-all → just push.
# Whether to commit is commit_scope's verdict, not `git diff --cached`'s (PH16-T36):
# exit 3 = the index holds a path this run did not attribute — REFUSED, nothing staged;
# exit 4 = nothing attributable, so there is no commit to make (not an error);
# exit 5 = PARTIAL — it staged, but changed paths were left behind, so NO COMMIT is made
#          (PH23-T02). Naming the exclusions was the old answer and it was printing, which
#          is walked past: three recorded incidents, warning on screen each time, one of
#          them shipping enforcement code with its tests stripped. The staged paths stay
#          staged, so this is a stop and never a loss.
#          Deliberate exclusion: `just commit-all "msg" --allow-partial`.
# Asking the index instead is what let f4e7463 name two tasks and contain neither: a path
# left staged by an earlier tenure made `--cached` non-empty, so the guard against an empty
# commit passed and the commit shipped without a single file of either task.
commit-all message="[AI] chore: sync workspace state" scope="":
	#!/usr/bin/env bash
	set -uo pipefail
	ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
	python3 "$ROOT/scripts/session_lock.py" require --action "git commit" || exit 1
	python3 "$ROOT/scripts/gate_check.py" --action "git commit" || exit 1
	python3 "$ROOT/scripts/commit_scope.py" --root "$ROOT" --apply ${2:-}
	rc=$?
	[ $rc -eq 4 ] && exit 0
	[ $rc -eq 0 ] || exit $rc
	git commit -m "$1"

# Push current branch to origin — BLOCKED unless the gate is open, AND
# (PH10-T03) blocked when the running model's resolved tier denies "push"
# (an executor-tier session cannot push, even with an open gate).
# Branch policy (ADR-021): main + explicit user approval. No auto-push.
push override="":
	#!/usr/bin/env bash
	set -uo pipefail
	ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
	python3 "$ROOT/scripts/gate_check.py" --action "git push (external side effect)" || exit 1
	if [ -n "$1" ]; then
		python3 "$ROOT/scripts/leash.py" push --override "$1" || exit 1
	else
		python3 "$ROOT/scripts/leash.py" push || exit 1
	fi
	git push origin HEAD
