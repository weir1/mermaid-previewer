#!/usr/bin/env python3
"""
session_hook.py — deliver the session briefing to BOTH audiences (PH9-T16).

PH9-T04 built the plain-English session opening and it reached nobody. The
Claude Code hooks documentation says why:

    "For most events, stdout is written to the debug log but not shown in the
     transcript. The exceptions are UserPromptSubmit, UserPromptExpansion, and
     SessionStart, where stdout is added as context that Claude can see and
     act on."

So `just session-start`'s output goes into the *model's* context and nowhere
else. The operator, who asked for the feature in the first place, never saw it —
and `G4` was auto-stamped "met" by a test suite that asserted what the function
prints and never once asked who receives it.

The only hook field that renders to a person is `systemMessage`. This adapter
emits both channels:

    systemMessage                        -> the human sees this (short opening)
    hookSpecificOutput.additionalContext -> the AI sees this (full briefing)

## Why an adapter instead of changing session_start.py

`session_start.py` is also run directly by a human (`just session-start`), where
plain text in a terminal is exactly right. Emitting JSON there would break that
and would give the module two output contracts. It stays untouched; this wraps
it for the hook channel only.

## The stdin hazard (the part most likely to break quietly)

A hook that reads stdin *consumes* the payload. `session_start.hook_source()`
reads stdin itself to learn whether this is a `startup` or a `resume` — and per
PH7-T09 that distinction decides whether the session budget is reset or
preserved. Running it as a subprocess leaves its stdin empty, which resolves to
"unknown", which is treated protectively as *not* a new session.

Getting that backwards would silently destroy earned budget credit. So the
payload is parsed once here and the value forwarded as `--source X`, which
`hook_source()` already prefers over stdin (`session_start.py:78`).

## Failure is not an option, degradation is

This runs at the start of every session in ~40 workspaces. Any failure falls
back to printing the briefing text plainly — exactly today's behaviour, which
still reaches the AI — and always exits 0. An invisible briefing is a bug; a
session start that dies is an outage.

Usage:
  session_hook.py                 # as Claude Code invokes it (payload on stdin)
  session_hook.py --no-reset      # render without touching budget state
"""

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

KNOWN_SOURCES = ("startup", "resume", "clear", "compact")
TIMEOUT_SECONDS = 120


def read_payload(stream=None, timeout: float = 0.2) -> str:
    """The raw hook payload from stdin, or "" — never blocking.

    Same discipline as `session_start.hook_source()`: never read a TTY, never
    read without `select()` saying data is ready, treat any failure as absent.
    A block here is a fleet-wide hang at session start.
    """
    stream = sys.stdin if stream is None else stream
    try:
        if stream is None or stream.closed or stream.isatty():
            return ""
        import select
        if not select.select([stream], [], [], timeout)[0]:
            return ""
        return stream.read() or ""
    except Exception:  # noqa: BLE001
        return ""


def payload_source(payload_text: str) -> str:
    """`source` from the hook payload, or "" when absent/unparseable.

    An unreadable payload means *unknown*, never a guessed "startup" — forging
    it would reset a budget counter that a resume is supposed to preserve.
    """
    try:
        obj = json.loads(payload_text or "{}")
        if not isinstance(obj, dict):
            return ""
        src = str(obj.get("source", "")).strip()
        return src if src in KNOWN_SOURCES else ""
    except Exception:  # noqa: BLE001
        return ""


def inner_argv(payload_text: str, argv) -> list:
    """Arguments for `session_start.py`, with the hook's `source` forwarded.

    An explicit `--source` already on argv wins and is not duplicated — the same
    precedence `hook_source()` itself applies, so the two never disagree.
    """
    args = list(argv)
    if "--source" in args:
        return args
    src = payload_source(payload_text)
    if src:
        args += ["--source", src]
    return args


def run_briefing(args, cwd: Path) -> tuple:
    """(stdout, ok). stderr is captured separately so it can never corrupt JSON."""
    proc = subprocess.run(
        [sys.executable, str(HERE / "session_start.py"), *args],
        capture_output=True, text=True, cwd=str(cwd), timeout=TIMEOUT_SECONDS,
    )
    out = proc.stdout or ""
    if proc.stderr:
        out += "\n[session_start stderr]\n" + proc.stderr
    return out, proc.returncode == 0


def human_message(cwd: Path) -> str:
    """The short opening, rendered for the person reading the terminal.

    Deliberately re-derived rather than sliced out of the briefing text: the
    briefing's layout is presentation, and parsing it back apart would make this
    channel break silently every time that layout changed.

    `plain_lines` (PH9-T17), not `render_open` — the human channel gets the
    goals named in his own plain English, while the AI channel keeps the
    technical rendering with ledger ids intact. Two audiences, two renderings.

    Health (PH16-T01) is composed here rather than inside `session_open`: that
    module is subprocess-called once per workspace by `fleet_goals.py`, so
    putting a live `doctor` run inside it would cost 38 doctor runs per
    `just fleet-goals`. Two producers, joined at the one call site that needs
    both. Health failing must not cost him the intent block, so it is wrapped
    separately.

    The running model (PH10-T01) joins the same way, for the same reason: a
    third independent producer, wrapped so its failure costs neither of the
    other two.
    """
    import session_open
    parts = ["\n".join(session_open.plain_lines(session_open.opening(cwd))).strip()]
    try:
        import health
        parts.append("\n".join(health.plain_lines(health.check(cwd))).strip())
    except Exception as exc:  # noqa: BLE001
        parts.append("🩺 IS THIS WORKSPACE HEALTHY?\n"
                     f"   The health check itself could not run ({exc}), so this is unknown.")
    try:
        import model_registry
        parts.append("\n".join(model_registry.plain_lines(model_registry.resolve_running(cwd))))
    except Exception:  # noqa: BLE001
        pass  # optional line — an unknown model is already the safe default
    return "\n\n".join(p for p in parts if p)


def main() -> int:
    args = [a for a in sys.argv[1:]]
    payload = read_payload()
    cwd = Path.cwd()

    briefing, ok = "", False
    try:
        briefing, ok = run_briefing(inner_argv(payload, args), cwd)
    except Exception as exc:  # noqa: BLE001
        briefing = f"(session briefing unavailable: {exc})"

    try:
        message = human_message(cwd)
    except Exception as exc:  # noqa: BLE001
        message = ""
        briefing += f"\n(session opening unavailable: {exc})"

    if not message.strip():
        # Nothing to show the human. Fall back to today's behaviour rather than
        # emitting an empty systemMessage: plain stdout still reaches the AI.
        print(briefing.strip() or "(session briefing unavailable)")
        return 0

    json.dump({
        "systemMessage": message,
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": briefing.strip(),
        },
    }, sys.stdout)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001
        # Last resort: never let a session start fail because of this adapter.
        print(f"(session briefing hook failed: {exc})")
        sys.exit(0)
