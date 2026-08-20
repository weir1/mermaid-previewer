#!/usr/bin/env python3
"""second_opinion.py — `just second-opinion <diff|plan|claim>` (PH20-T10).

`self-review-diff` is the *same session* reviewing its own work — the
vacuous-test class hit 5+ recorded instances, every one green under that
same-author review and caught only by a manual mutation check afterward
(`.ai/memory-bank/knownIssues.md`, 2026-08-09). A reviewer that shares the
author's reasoning shares the author's blind spot.

This spawns a **structurally fresh** `claude -p` subprocess — a brand-new OS
process with its own new session id, started with nothing but the prompt this
module builds. It does not "not look at" the originating session's chat
history; it has no channel through which that history could ever reach it.
That is the guarantee, and it is why the test for it never has to trust a
promise: `build_prompt()` is a pure function of `(target, repo_state)`, and a
test can assert byte-for-byte that its output contains only what those two
arguments carried in.

## What the reviewer can see — and can't do

The prompt carries the target content (verbatim) plus a few factual repo-state
fields (HEAD sha, branch, active task). No tools are granted — not even Read —
so the reviewer cannot go browsing the tree, cannot act, and its judgment is
provably confined to what this module handed it. Composes with, and does not
replace, `self-review-diff`. It also runs `--bare` — see `_invoke_claude()`
for why that is load-bearing, not just leaner: a non-bare nested `claude`
process in a governed workspace fires this repo's own `SessionStart` hook and
silently resets `.ai/session-state.json`, wiping whatever work credit the
*outer* session had just earned. Found live, self-inflicted, during this
task's own build.

## Binding, the same way self_review.py binds

A verdict is only as good as knowing it still applies. The record stores the
sha256 of the exact target content reviewed; `check()` recomputes it and
refuses a stale match — same contract as `self_review.check()`, deliberately,
so both review paths behave the same way to whatever reads them (`just
close git-push` does not consume this one, but nothing stops it from later
composing the same way).

## Decision-logged, tagged for `just audit`

A recorded verdict is also appended to `.ai/decision-log/` — `kind="review"`,
`source="second-opinion"` — the same log every policy/gate verdict lands in,
so `just decisions` / `just audit` can filter on `source` and tell a
second-opinion verdict apart from a self-review closure.

Usage (the command is optional and defaults to "review" — `just second-opinion
<diff|plan|claim>` is the common case; `status`/`check` need it explicit):
  second_opinion.py diff                            # the current session diff
  second_opinion.py .ai/plans/PH9-T01.md             # any existing file, verbatim
  second_opinion.py "the fix handles X"              # a free-text claim
  second_opinion.py diff --model claude-haiku-4-5-20251001
  second_opinion.py status <target>                  # is it covered by a matching record?
  second_opinion.py check <target>                   # exit 6 if not

Exit codes: 0 ok · 1 usage · 5 the reviewer subprocess failed/timed out ·
            6 target not covered by any matching verdict.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

RECORD_DIRNAME = Path(".ai") / "reviews"
DEFAULT_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_TIMEOUT_S = 180
EXIT_SUBPROCESS = 5
EXIT_UNCOVERED = 6
VERDICTS = ("pass", "pass-with-findings", "fail")
PASSING = ("pass", "pass-with-findings")

# No tool access at all: the target content is already inline in the prompt,
# and blocking Agent/Task prevents the reviewer from recursively spawning
# further subagents (cost + recursion control), matching "it sees only the
# diff/plan/claim handed to it" from the task's own framing.
DISALLOWED_TOOLS = ("Write", "Edit", "Bash", "Read", "Glob", "Grep",
                    "WebFetch", "WebSearch", "Agent", "Task", "NotebookEdit")


def ws_root() -> Path:
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _git(root: Path, *args: str) -> str:
    try:
        return subprocess.check_output(["git", "-C", str(root), *args],
                                       stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:  # noqa: BLE001
        return ""


# ── Resolving the target ────────────────────────────────────────────────────
def resolve_target(arg: str, root: Path) -> dict:
    """`{kind, label, content}` — never raises; an unreadable target is
    reported through `content is None`, not an exception."""
    if arg == "diff":
        try:
            import self_review
        except Exception as exc:  # noqa: BLE001
            return {"kind": "diff", "label": "session diff", "content": None,
                    "error": f"self_review.py unavailable ({exc})"}
        base = self_review.resolve_base(root)["base"]
        text = self_review.session_diff(root, base)
        if not text.strip():
            return {"kind": "diff", "label": "session diff", "content": None,
                    "error": "the session diff is empty — nothing to review"}
        return {"kind": "diff", "label": f"session diff (base {base[:8]})", "content": text}

    candidate = Path(arg)
    if not candidate.is_absolute():
        candidate = root / candidate
    if candidate.is_file():
        try:
            text = candidate.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            return {"kind": "plan", "label": arg, "content": None, "error": str(exc)}
        rel = arg
        try:
            rel = str(candidate.resolve().relative_to(root.resolve()))
        except ValueError:
            pass
        return {"kind": "plan", "label": rel, "content": text}

    # Neither "diff" nor an existing file: a free-text claim.
    if not arg.strip():
        return {"kind": "claim", "label": "", "content": None,
                "error": "empty claim — nothing to review"}
    return {"kind": "claim", "label": arg[:80], "content": arg}


def repo_state(root: Path) -> dict:
    """Factual, non-conversational context — never the session's own reasoning."""
    try:
        import decision_log
        task = decision_log.active_task(root)
    except Exception:  # noqa: BLE001
        task = ""
    return {
        "workspace": root.name,
        "head": _git(root, "rev-parse", "--short", "HEAD") or "unknown",
        "branch": _git(root, "rev-parse", "--abbrev-ref", "HEAD") or "unknown",
        "active_task": task or "(none declared)",
    }


# ── The prompt — pure function of its two arguments ────────────────────────
def build_prompt(target: dict, state: dict) -> str:
    """Everything the reviewer sees. No chat history is a parameter here, so
    none can appear in the output — that is the whole guarantee, and it is
    what the isolation test checks."""
    return f"""You are an independent second-opinion reviewer. You have not seen, and
have no access to, any conversation that produced the material below — you
are judging it cold, on its own content, exactly as a fresh reader would.

## Repo state
- workspace: {state['workspace']}
- HEAD: {state['head']} (branch {state['branch']})
- active task: {state['active_task']}

## Target ({target['kind']}: {target['label']})
```
{target['content']}
```

## Your job
Judge the target on its own merits: correctness, whether it does what it
claims, and anything a careful reviewer would flag. You have no tools — judge
only what is shown above.

Reply with ONLY a single JSON object, no prose before or after it, no markdown
fences:
{{"verdict": "pass" | "pass-with-findings" | "fail",
  "findings": ["<severity>: <specific, falsifiable issue>", ...],
  "note": "<one sentence — what you actually checked>"}}
"pass" carries no findings. "fail" or "pass-with-findings" must carry at
least one specific finding — not a vague concern."""


def _extract_json(text: str) -> dict | None:
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            return json.loads(fenced.group(1))
        except json.JSONDecodeError:
            pass
    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError:
            pass
    return None


def _invoke_claude(prompt: str, root: Path, *, model: str, timeout: int) -> dict:
    """Run the fresh subprocess. `{ok, verdict, findings, note, raw, error}`.

    Isolated for testability: every test that exercises the surrounding logic
    monkeypatches this one function rather than spawning a real subprocess —
    the prompt-construction guarantee is checked directly on `build_prompt()`,
    not by inspecting a live call.

    **`--safe-mode` is not optional.** Found live during this task's own
    build: running this from inside a God-Mode-governed workspace (this
    kernel included) spawns a genuinely new `claude` process in the same
    directory, which fires `.claude/settings.json`'s `SessionStart` hook —
    and that hook calls `session_budget.py start`, which (correctly, per its
    own contract: a `--source startup` invocation IS a new session) resets
    `.ai/session-state.json` and silently wiped this task's *own* just-earned
    PH20-T11 credit mid-session. `--safe-mode` disables hooks (and
    CLAUDE.md/skills/plugins/MCP) while leaving auth, model selection and
    built-in tools untouched — unlike `--bare`, tried first and reverted: it
    also skips keychain reads, which broke authentication entirely in this
    OAuth/keychain environment ("Not logged in"), live-confirmed. This is the
    same collision class already filed in `knownIssues.md` (two concurrent
    CLI sessions clobbering shared state), just self-inflicted by this tool
    instead of coincidental — scoped here by never letting the reviewer touch
    the hook at all, since fixing `session-state.json`'s shared-file design
    is out of this task's scope.
    """
    cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model, "--safe-mode",
           "--disallowedTools", " ".join(DISALLOWED_TOOLS)]
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"ok": False, "error": f"reviewer timed out after {timeout}s"}
    except OSError as exc:
        return {"ok": False, "error": f"could not run `claude`: {exc}"}
    try:
        envelope = json.loads(r.stdout)
    except (json.JSONDecodeError, ValueError):
        return {"ok": False, "error": "reviewer did not return the expected JSON envelope",
                "raw_stdout": r.stdout[-2000:]}
    if envelope.get("is_error"):
        return {"ok": False, "error": f"reviewer errored: {envelope.get('result', '?')}"}
    verdict = _extract_json(str(envelope.get("result", "")))
    if not verdict or verdict.get("verdict") not in VERDICTS:
        return {"ok": False, "error": "reviewer's reply did not parse to a valid verdict",
                "raw_result": str(envelope.get("result", ""))[:2000]}
    return {"ok": True, "verdict": verdict.get("verdict"),
           "findings": list(verdict.get("findings") or []),
           "note": str(verdict.get("note") or ""),
           "cost_usd": envelope.get("total_cost_usd"),
           "session_id": envelope.get("session_id"), "raw": envelope}


# ── Records ──────────────────────────────────────────────────────────────────
def read_records(root: Path) -> list[dict]:
    directory = root / RECORD_DIRNAME
    if not directory.is_dir():
        return []
    out = []
    for path in directory.glob("second-opinion-*.json"):
        try:
            data = json.loads(path.read_text(errors="replace"))
        except Exception:  # noqa: BLE001
            continue
        if isinstance(data, dict) and data.get("content_sha256"):
            data["_path"] = str(path.relative_to(root))
            out.append(data)
    return sorted(out, key=lambda d: str(d.get("recorded_at", "")), reverse=True)


def record(root: Path, target: dict, outcome: dict) -> dict:
    now = datetime.now(timezone.utc)
    sha = hashlib.sha256(target["content"].encode("utf-8")).hexdigest()
    entry = {
        "recorded_at": now.isoformat(),
        "reviewer": "second-opinion",
        "target_kind": target["kind"],
        "target_label": target["label"],
        "content_sha256": sha,
        "verdict": outcome["verdict"],
        "note": outcome["note"],
        "findings": outcome["findings"],
        "model": outcome.get("model", ""),
        "cost_usd": outcome.get("cost_usd"),
        "session_id": outcome.get("session_id"),
    }
    directory = root / RECORD_DIRNAME
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"second-opinion-{now.strftime('%Y-%m-%d-%H%M%S')}-{sha[:12]}.json"
    path.write_text(json.dumps(entry, indent=2) + "\n")
    entry["_path"] = str(path.relative_to(root))
    return entry


def check(root: Path, target: dict) -> dict:
    """`{ok, reason, review}` — is `target`'s CURRENT content covered by a
    matching, passing verdict? Same content-hash binding as self_review."""
    if target.get("content") is None:
        return {"ok": False, "reason": target.get("error", "target unreadable"), "review": None}
    sha = hashlib.sha256(target["content"].encode("utf-8")).hexdigest()
    matches = [r for r in read_records(root) if r.get("content_sha256") == sha]
    if not matches:
        return {"ok": False,
                "reason": f"no second-opinion review covers this content (sha {sha[:12]}).",
                "review": None}
    passing = [r for r in matches if r.get("verdict") in PASSING]
    if not passing:
        return {"ok": False, "review": matches[0],
                "reason": f"reviewed, verdict {matches[0].get('verdict')!r}: "
                          f"{matches[0].get('note') or 'no note'}"}
    return {"ok": True, "review": passing[0],
           "reason": f"reviewed at {passing[0].get('recorded_at')} — verdict "
                     f"{passing[0].get('verdict')}."}


def _log_decision(root: Path, target: dict, outcome: dict) -> None:
    try:
        import decision_log
        decision_log.record("review", outcome["verdict"], "second-opinion", root,
                            action=f"second-opinion {target['kind']}", reason=outcome["note"])
    except Exception:  # noqa: BLE001
        pass  # decision_log.record() never raises by contract; this is belt-and-suspenders


# ── CLI ──────────────────────────────────────────────────────────────────────
def run_review(root: Path, arg: str, *, model: str, timeout: int) -> dict:
    target = resolve_target(arg, root)
    if target.get("content") is None:
        return {"ok": False, "code": 1, "reason": target.get("error", "nothing to review")}
    state = repo_state(root)
    prompt = build_prompt(target, state)
    outcome = _invoke_claude(prompt, root, model=model, timeout=timeout)
    if not outcome.get("ok"):
        return {"ok": False, "code": EXIT_SUBPROCESS, "reason": outcome.get("error", "unknown error")}
    outcome["model"] = model
    entry = record(root, target, outcome)
    _log_decision(root, target, outcome)
    return {"ok": True, "code": 0, "entry": entry, "target": target}


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    # `command` is optional and defaults to "review" — `just second-opinion
    # <target>` is the common case; only `status`/`check` need it spelled out.
    # (A claim whose ENTIRE text happens to be exactly "status" or "check"
    # would need `review` spelled explicitly — a documented, narrow edge case.)
    if argv and argv[0] not in ("review", "status", "check"):
        argv = ["review", *argv]

    ap = argparse.ArgumentParser(
        description="Fresh-context adversarial review of a diff, a file, or a claim.")
    ap.add_argument("command", choices=["review", "status", "check"])
    ap.add_argument("target", help='"diff" · an existing file path · or a free-text claim')
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_S)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)
    root = ws_root()

    if args.command == "review":
        result = run_review(root, args.target, model=args.model, timeout=args.timeout)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        elif result["ok"]:
            e = result["entry"]
            print(f"  ✅ Second opinion recorded — {e['_path']}")
            print(f"     verdict {e['verdict']} · {result['target']['kind']} "
                  f"{result['target']['label']!r}")
            print(f"     {e['note']}")
            for f in e["findings"]:
                print(f"     • {f}")
        else:
            print(f"  🛑 {result['reason']}", file=sys.stderr)
        return result["code"]

    target = resolve_target(args.target, root)
    v = check(root, target)
    if args.json:
        print(json.dumps(v, indent=2, default=str))
    elif v["ok"]:
        print(f"  ✅ COVERED — {v['reason']}")
    else:
        print(f"  🛑 NOT COVERED — {v['reason']}")
    if args.command == "check":
        return 0 if v["ok"] else EXIT_UNCOVERED
    return 0


if __name__ == "__main__":
    sys.exit(main())
