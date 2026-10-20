"""What separates a question about the session from a design question.

One vocabulary for both `AskUserQuestion` hooks: the one that asks the brain
before the person (`brain_answer_hook`) and the one that records the person's
answer (`brain_record_answer_hook`). A question this module calls session-shaped
("continue?", "which file?", yes/no, options that are paths) is never sent to
the brain to be answered and never recorded as a decision, so the two hooks must
read it the same way: change the vocabulary here, never in a copy.

Stdlib only, so both hooks can import it on their cheap path.
"""

from __future__ import annotations

import re

__all__ = ["SESSION_LABELS", "SESSION_QUESTION_PATTERNS", "is_session_shaped",
           "normalize_label"]

#: Labels that answer the session, not a design question. Compared after
#: `normalize_label`.
SESSION_LABELS = frozenset({
    "yes", "no", "ok", "okay", "continue", "proceed", "go ahead", "stop",
    "cancel", "abort", "skip", "retry", "try again", "wait", "later", "not now",
    "done", "approve", "reject", "keep going",
})

#: Questions about the session itself, matched on the casefolded, stripped
#: question text. The going-on verbs count only where they ask whether to go on:
#: the question is the verb alone (after "shall we", "can I", ...), or it closes
#: on "OK to <verb>"; either may end in "now", "anyway", "later", "please",
#: "(y/n)", "[y/n]" or "with ...". Anywhere else they name a design topic
#: ("Which retry policy ...?", "How many times should we retry?"), and that
#: question must reach the brain. Accepted limit: "Should we retry with backoff?" reads like
#: "Do we continue with the refactor?" and is treated as session-shaped. That
#: is the safe direction: the question is asked and nothing is recorded.
SESSION_QUESTION_PATTERNS = (
    r"^(?:(?:shall|should|can|could|may|do|will|would) (?:we|i) )?"
    r"(?:continue|proceed|go ahead|keep going|retry|try again)"
    r"(?:(?: now| anyway| later)?(?:,? please)?\??(?: [(\[]y/n[)\]])?\??$| with\b)"
    r"|\b(?:ok|okay|fine|alright) to (?:continue|proceed|go ahead|keep going|retry|try again)"
    r"(?:(?: now| anyway| later)?(?:,? please)?\??(?: [(\[]y/n[)\]])?\??$| with\b)",
    r"\b(shall|should) i\b",
    r"\b(do|would) you (still |also )?want( me)? to\b",
    r"\bwant me to\b",
    r"\bready to\b",
    r"\bwhich (file|files|directory|directories|folder|branch|worktree|commit|line)\b",
    r"\b(what|did|do) you mean\b",
)

_PARENTHETICAL_RE = re.compile(r"\s*\(.*\)$")
_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")


def normalize_label(label):
    text = label.casefold().strip()
    text = _PARENTHETICAL_RE.sub("", text).strip()
    return text.rstrip(".!?:").strip()


def is_session_shaped(question_text, labels):
    """True when the question is about the session, by its option labels or by
    its text. `labels` is the list of offered label strings (possibly empty)."""
    if any(normalize_label(label) in SESSION_LABELS for label in labels):
        return True
    if any("/" in label or "\\" in label or _FILE_EXTENSION_RE.search(label)
           for label in labels):
        return True
    folded = question_text.casefold().strip().strip("*_`'\"").strip()
    return any(re.search(pattern, folded) for pattern in SESSION_QUESTION_PATTERNS)
