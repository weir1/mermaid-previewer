#!/usr/bin/env python3
"""
off_plan.py — a request outside the plan is named as ADDITIONAL work, and priced.

`.ai/plan.md` G5: *"Nothing is silently added to the scope — a request outside
the plan is named as additional work, priced in sessions, and offered as add /
one-off / drop before work begins."*

Until now a mid-session ask was simply *done*. Nothing compared it against the
agreed plan, nothing said it was extra, nothing priced it, and nothing recorded
that the scope had grown. The operator's framing for this whole phase was
"a correct idea of my standing & how much energy I need to spend instead of
endless building" — and endless building is exactly what an unpriced, unrecorded
mid-session ask produces. `effort_forecast.price_tag()` (PH9-T10) and the goal
list (PH9-T08) both already existed and were correct; no path ever *showed* them
at the moment scope actually changed. This is that path.

## Two independent decisions, in this order

**1. Is it work at all?** A notice that fires on "what does `just gate` do?" is
noise, and noise gets tuned out — which kills the mechanism entirely. So a
leading interrogative ("what/why/how/is/does/should …") is a question, and an
imperative build verb ("add/build/implement/fix/ship/derive …") is work.
**Ambiguous text is deliberately classified as NOT work.** That bias is chosen,
not accidental: a false negative costs one unpriced request, while a false
positive on every stray sentence costs the mechanism its credibility, after
which it protects nothing at all. `--kind work` forces the classification when
the agent already knows better than the heuristic.

**2. Is it in the plan?** Token-overlap of the request against each goal's own
title, read from `plan_workspace.validate()["goals"]` — that module keeps sole
ownership of the plan's text, the rule `goal_progress.py` and
`effort_forecast.priced_proposals()` already follow. The score is
`overlap / len(goal tokens)`: *how much of the goal does this request cover*.
Above `MATCH_THRESHOLD` the request is in-plan and no notice fires.

Every notice **shows the closest goal and its score**, so a wrong call is
visible and arguable rather than silent — design law 2 (every number states its
basis) applied to a classifier's own confidence.

## What this deliberately is not

- **Not a `PreToolUse` hook.** A hook sees a tool call, not the user's request
  text; it would fire per-tool rather than per-ask, and it would make a
  heuristic classifier a *blocking* gate. A false positive here costs one
  ignorable line; there it wedges the session.
- **Not an LLM classifier.** No dependency budget, non-deterministic under test,
  useless offline. A stated heuristic that shows its score is more honest than a
  confident black box.
- **Not self-executing.** `resolve()` never runs itself. The whole point is that
  the operator chooses, so the choice is an argument, not an inference.

The price comes from `effort_forecast.price_tag(1)` verbatim and is never
re-derived here. When history is too thin to price, the notice still fires and
carries the refusal: the scope grew either way, we just cannot yet say by how
much. Suppressing the notice because the price is unknown would lose the actual
signal in order to protect a number.

Usage:
  off_plan.py "add a telegram bot"            # notice, or silence
  off_plan.py "..." --json
  off_plan.py "..." --kind work               # override the classifier
  off_plan.py "..." --add | --once | --drop   # resolve it (the only writes)
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Above this share of a goal's own words, a request is that goal's work rather
# than new scope. A judgement call, not a measurement — so it is a named
# constant, printed in every notice, and pinned by tests on both sides of the
# boundary rather than buried inside an expression. 0.5 = "covers half the goal".
MATCH_THRESHOLD = 0.5

CHOICES = ("add", "once", "drop")

# A leading interrogative makes the sentence a request for an *answer*, however
# much building it describes: "how would we add a codemap?" asks about the
# codemap, it does not ask for one.
_QUESTION_LEADS = {
    "what", "whats", "why", "how", "when", "where", "which", "who", "whom", "whose",
    "is", "are", "was", "were", "am", "do", "does", "did", "has", "have", "had",
    "should", "shall", "may", "might",
}

# Politeness wrapped around a real instruction. Stripped before the verb check
# so "can you add X" is not misread as a yes/no question — the single most
# common shape of a genuine mid-session work request.
_POLITE_PREFIXES = (
    "can you", "could you", "would you", "will you", "can we", "could we",
    "please", "lets", "let us", "i want you to", "i want", "i need you to",
    "i need", "we need", "id like you to", "id like", "i would like",
    "go ahead and", "now", "next", "also", "then",
)

# The imperative verbs a work request opens with. Deliberately a closed list:
# an open-ended "any verb counts" rule reclassifies half of ordinary English as
# scope growth, which is the false-positive failure this module cannot afford.
_WORK_VERBS = {
    "add", "build", "implement", "create", "make", "write", "ship", "fix",
    "refactor", "remove", "delete", "wire", "set", "setup", "install", "deploy",
    "migrate", "rename", "move", "update", "extend", "expose", "generate",
    "derive", "render", "record", "enforce", "teach", "port", "hook", "swap",
    "replace", "split", "collapse", "surface", "stamp", "publish", "sync",
    "automate", "integrate", "support", "handle", "harden", "instrument",
    "name", "define", "document", "index", "upgrade", "convert", "draft",
    "design", "patch", "cover", "wrap", "annotate", "scaffold", "bootstrap",
}
# Deliberately absent: "show", "print", "report", "check", "list", "explain".
# Each opens a *read* request ("show me the goals") far more often than a build
# one, and a read costs no scope. Missing a work verb costs one unpriced
# request that `--kind work` recovers; inventing one makes the notice fire on
# ordinary inspection, which is how the mechanism gets tuned out for good.

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "if", "then", "than", "that", "this",
    "these", "those", "of", "to", "in", "into", "on", "for", "from", "with",
    "by", "at", "as", "it", "its", "is", "are", "be", "been", "being", "was",
    "were", "am", "do", "does", "did", "has", "have", "had", "will", "would",
    "can", "could", "should", "shall", "may", "might", "must", "not", "no",
    "so", "we", "i", "you", "he", "she", "they", "them", "their", "our", "us",
    "my", "me", "him", "her", "his", "hers", "himself", "herself", "itself",
    "ourselves", "themselves", "yourself", "every", "each", "all", "any",
    "some", "more", "most", "other", "own", "same", "just", "very", "up",
    "down", "out", "over", "under", "again", "here", "there", "when", "where",
    "how", "what", "why", "which", "who", "whom", "whose", "let", "lets",
    "please", "also", "now", "next", "still", "one", "two",
}


def ws_root() -> Path:
    """Git top-level, falling back to cwd — the anti-drift rule, same as its peers."""
    try:
        top = subprocess.check_output(["git", "rev-parse", "--show-toplevel"],
                                      stderr=subprocess.DEVNULL, text=True).strip()
        if top:
            return Path(top)
    except Exception:  # noqa: BLE001
        pass
    return Path(".").resolve()


def _stem(word: str) -> str:
    """Crude suffix strip, so "versions"/"version" and "derive"/"derived" agree.

    Not linguistics — just enough that a goal written in the plural is matched
    by a request written in the singular. Over-stemming is harmless here: both
    sides pass through the identical function, so the comparison stays fair
    even where the stem itself is not a word.
    """
    w = word.lower().strip("'’")
    for suffix, keep in (("ies", 3), ("ing", 4), ("ed", 3), ("es", 4), ("s", 3), ("e", 4)):
        if w.endswith(suffix) and len(w) > keep:
            return w[: -len(suffix)] + ("y" if suffix == "ies" else "")
    return w


def _tokens(text: str) -> set[str]:
    """Content words of `text`, stemmed. Stopwords and punctuation dropped."""
    words = re.findall(r"[a-zA-Z][a-zA-Z0-9_'’-]*", text or "")
    return {_stem(w) for w in words if w.lower() not in _STOPWORDS and len(w) > 1}


def _strip_politeness(text: str) -> str:
    """Peel "can you" / "please" / "also" off the front, repeatedly."""
    out = text.strip()
    changed = True
    while changed:
        changed = False
        flat = re.sub(r"[^a-z\s]", "", out.lower()).strip()
        for prefix in _POLITE_PREFIXES:
            if flat == prefix or flat.startswith(prefix + " "):
                # Drop the same number of words from the original, keeping its case.
                out = " ".join(out.split()[len(prefix.split()):]).lstrip(",: ")
                changed = True
                break
    return out


def is_work(request: str) -> bool:
    """Is this an instruction to build something, or a question about it?

    Order matters: the interrogative check runs first, so "how would we add X"
    stays a question even though it contains a work verb. Anything that is
    neither clearly interrogative nor clearly imperative returns False — the
    deliberate bias documented at the top of this module.
    """
    text = (request or "").strip()
    if not text:
        return False

    first = re.sub(r"[^a-z]", "", text.split()[0].lower())
    if first in _QUESTION_LEADS:
        return False

    body = _strip_politeness(text)
    if not body:
        return False

    first = re.sub(r"[^a-z]", "", body.split()[0].lower())
    if first in _QUESTION_LEADS:
        return False
    return first in _WORK_VERBS


def goal_title(goal: dict) -> str:
    """The goal's *title*, without its success criterion.

    The plan's own convention is `- G1 — <title> — <how you will know it is
    done>`, and `plan_workspace.validate()` returns the whole line after the id.
    Scoring against that whole line was the first thing the live run broke:
    denominators of 10–22 tokens made every real goal unmatchable, and — worse —
    two goals of identical relevance scored differently purely because one
    author wrote a wordier criterion. A number whose basis shifts with prose
    length is exactly what design law 2 forbids, so the criterion is cut off
    here. A goal written without the separator keeps its whole text.
    """
    text = goal.get("title", "")
    return re.split(r"\s+[—–]\s+|\s+--?\s+", text, maxsplit=1)[0].strip() or text


def match_goal(request: str, goals: list[dict]) -> tuple[str | None, str, float]:
    """Closest goal to `request`: `(id, title, score)`.

    Dice coefficient — `2 × overlap / (|request| + |goal|)` — over the goal's
    *title* tokens. Plain coverage-of-the-goal was tried first and is worse in
    both directions: a two-word title ("Every workspace speaks for itself")
    matched at 0.5 on the single word "workspace", which would wave real scope
    growth through as in-plan. Dice charges the request for its own unmatched
    specificity, so "build a telegram bot for every workspace" scores 0.33 and
    still fires, while a genuine restatement of the goal scores high.

    It is still a word-overlap heuristic and will read a heavily paraphrased
    in-plan request as off-plan. That direction is the cheap one — one line the
    operator dismisses — and the notice prints the closest goal and the score,
    so the call is arguable instead of silent.
    """
    best_id, best_title, best_score = None, "", 0.0
    req = _tokens(request)
    if not req:
        return best_id, best_title, best_score
    for g in goals:
        title = goal_title(g)
        goal_tokens = _tokens(title)
        if not goal_tokens:
            continue
        score = 2 * len(req & goal_tokens) / (len(req) + len(goal_tokens))
        if score > best_score or best_id is None:
            best_id, best_title, best_score = g.get("id"), title, score
    return best_id, best_title, best_score


def notice(request: str, root: Path | None = None, kind: str | None = None,
           window: int | None = None) -> dict:
    """Should this request be named as additional work? **Pure read — never writes.**

    Kept strictly side-effect free (PH7-T09's standing lesson: a read path that
    writes is one hook re-fire away from corrupting state). The plan is only
    ever touched by `resolve()`, and only on an explicit choice.
    """
    root = root or ws_root()
    out = {"is_work": False, "off_plan": False, "notice": "", "best_match": None,
           "best_title": "", "score": 0.0, "price": None, "reason": "",
           "request": (request or "").strip()}

    out["is_work"] = True if kind == "work" else (False if kind == "question"
                                                  else is_work(request))
    if not out["is_work"]:
        out["reason"] = "not a work request — no scope to price."
        return out

    try:
        import plan_workspace
    except Exception as exc:  # noqa: BLE001
        out["reason"] = f"plan_workspace.py unavailable ({exc})."
        return out

    v = plan_workspace.validate(root=root)
    if not v["ok"]:
        # Without an agreed plan there is no "the plan" for this to be outside
        # of. Calling everything off-plan in that state would make the notice
        # fire on every request in an unplanned workspace — noise again.
        out["reason"] = v["reason"]
        return out

    out["best_match"], out["best_title"], out["score"] = match_goal(request, v["goals"])
    if out["score"] >= MATCH_THRESHOLD:
        out["reason"] = (f"in plan — covered by {out['best_match']} "
                         f"({out['score'] * 100:.0f}% overlap).")
        return out

    out["off_plan"] = True
    try:
        import effort_forecast
        out["price"] = (effort_forecast.price_tag(1, root=root, window=window)
                        if window else effort_forecast.price_tag(1, root=root))
    except Exception as exc:  # noqa: BLE001
        out["price"] = {"ok": False, "reason": f"cannot price — effort_forecast.py "
                                               f"unavailable ({exc}).",
                        "n_tasks": 1, "sessions": None, "basis": ""}
    out["reason"] = "off plan — additional work."
    out["notice"] = render(out)
    return out


def render(n: dict) -> str:
    """The notice text. Names it as ADDITIONAL, prices it, offers the three choices."""
    price = n.get("price") or {}
    if price.get("ok"):
        price_line = f"Price: ≈{price['sessions']} session(s) — {price['basis']}"
    else:
        price_line = f"Price: {price.get('reason', 'cannot price — no basis available.')}"

    if n.get("best_match"):
        near = (f"Closest goal {n['best_match']} — {n['best_title']} — "
                f"{n['score'] * 100:.0f}% overlap (match threshold {MATCH_THRESHOLD}).")
    else:
        near = f"No goal in the plan resembles it (match threshold {MATCH_THRESHOLD}), 0%."

    return "\n".join([
        "⚠️  THIS IS ADDITIONAL to what is planned.",
        f'    Request: "{n["request"]}"',
        f"    {near}",
        f"    {price_line}",
        "    Choose before work starts:",
        "      · add     — make it a goal in .ai/plan.md (the plan grows, on the record)",
        "      · one-off — do it now, plan unchanged (the scope grew anyway; it is logged)",
        "      · drop    — don't do it",
        f'      just off-plan "{n["request"]}" --add | --once | --drop',
    ])


def resolve(request: str, choice: str, root: Path | None = None) -> dict:
    """Record the operator's choice — the only path here that writes anything.

    `add` grows the plan through `plan_workspace.add_goal()`; `once` and `drop`
    leave it byte-identical. **All three are recorded** in `.ai/decision-log/`,
    including the declines: a one-off that was never added to the plan is
    precisely the kind of scope growth that otherwise vanishes from the record,
    and the declines are the denominator that makes the accepts meaningful.
    """
    root = root or ws_root()
    out = {"ok": False, "reason": "", "choice": choice, "goal_id": None,
           "request": (request or "").strip()}

    if choice not in CHOICES:
        out["reason"] = f"unknown choice {choice!r} — expected one of: {', '.join(CHOICES)}."
        return out
    if not out["request"]:
        out["reason"] = "nothing to resolve — the request is empty."
        return out

    if choice == "add":
        try:
            import plan_workspace
        except Exception as exc:  # noqa: BLE001
            out["reason"] = f"plan_workspace.py unavailable ({exc})."
            return out
        added = plan_workspace.add_goal(out["request"], root=root)
        if not added["ok"]:
            # A refused write is not a decision to record — nothing happened.
            out["reason"] = added["reason"]
            return out
        out.update(ok=True, goal_id=added["goal_id"], reason=added["reason"])
    else:
        out.update(ok=True, reason=("accepted as a one-off — plan unchanged."
                                    if choice == "once" else
                                    "dropped — not doing it, plan unchanged."))

    try:
        import decision_log
        decision_log.record(
            "scope", f"off_plan_{choice}", "off_plan", root=root,
            action=f"off-plan request: {choice}",
            reason=(f"{out['request']}"
                    + (f" → {out['goal_id']}" if out["goal_id"] else "")))
    except Exception:  # noqa: BLE001
        # Design rule 1 of the decision log: recording never breaks the caller.
        # The plan write already succeeded; losing the log line is the lesser harm.
        pass
    return out


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Name an off-plan request as additional work, price it, and record "
                    "the operator's choice.")
    ap.add_argument("request", help="the request, in the user's own words")
    ap.add_argument("--kind", choices=("work", "question"),
                    help="override the work/question classifier")
    ap.add_argument("--window", type=int, default=0, metavar="N",
                    help="session window used for the price (default: the forecast's own)")
    group = ap.add_mutually_exclusive_group()
    group.add_argument("--add", action="store_true", help="accept it into the plan as a goal")
    group.add_argument("--once", action="store_true", help="do it once; plan unchanged")
    group.add_argument("--drop", action="store_true", help="decline it; plan unchanged")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    root = ws_root()
    choice = "add" if args.add else "once" if args.once else "drop" if args.drop else None

    if choice:
        r = resolve(args.request, choice, root=root)
        if args.json:
            print(json.dumps(r, indent=2, ensure_ascii=False))
        else:
            print(("✅ " if r["ok"] else "🛑 ") + r["reason"])
        return 0 if r["ok"] else 1

    n = notice(args.request, root=root, kind=args.kind, window=args.window or None)
    if args.json:
        print(json.dumps(n, indent=2, ensure_ascii=False))
    elif n["notice"]:
        print(n["notice"])
    else:
        # Silence is the common case, and a silent tool teaches nobody why it
        # stayed silent — so the reason is printed, not the notice.
        print(f"✅ No notice — {n['reason']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
