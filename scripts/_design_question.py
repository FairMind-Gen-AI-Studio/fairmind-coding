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

__all__ = ["NOT_PATHS", "SESSION_LABELS", "SESSION_LABEL_PATTERNS", "SESSION_QUESTION_PATTERNS",
           "is_path_shaped", "is_session_shaped", "normalize_label"]

#: Labels that answer the session, not a design question. Compared after
#: `normalize_label`.
SESSION_LABELS = frozenset({
    "yes", "no", "ok", "okay", "continue", "proceed", "go ahead", "stop",
    "cancel", "abort", "skip", "retry", "try again", "wait", "later", "not now",
    "done", "approve", "reject", "keep going",
    "salta", "ferma", "annulla", "procedi", "continua", "riprova", "interrompi", "sì",
})

#: Label shapes that answer the session, matched on the normalized label. Each
#: is narrow on purpose: a design option that merely opens with a control verb,
#: a yes, "you" or "I" is a decision, and a question read as session-shaped is
#: never recorded. A control verb counts only on the run itself, the whole label
#: being the verb and "it", a demonstrative, or a determiner and a session noun
#: ("Skip this task", "Stop the run", "Salta questo task"; not "Retry the whole
#: batch"). A yes or a no counts when its gloss reports the person's own state
#: ("Yes, I'm in as Ardesia", "No, I only created the invite", "I did"; not "Yes, in
#: Redis", "Yes, I want Redis", "No, I would compute on demand"). A hand-off gives
#: the run itself to someone, the whole label or one half of it ("You run it",
#: "I do it", "You run it, I give the commands"; not "You run it nightly via
#: cron", "You keep it in this repo", "I store it in S3", nor "I servizi in un
#: monorepo", where "I" is the Italian article).
SESSION_LABEL_PATTERNS = (
    r"^(?:skip|stop|cancel|abort|retry|continue|proceed|wait|salta|ferma|annulla|interrompi"
    r"|riprova|procedi|continua) (?:it|them|(?:this|that|these|those|questo|questa|questi"
    r"|queste|quello|quella)(?: (?:runs?|tasks?|steps?|sessions?|loops?|phases?|one|sessione"
    r"|sessioni|fase|fasi|passo|passi|ciclo))?|(?:the|il|lo|la|i|gli|le) (?:runs?|tasks?"
    r"|steps?|sessions?|loops?|phases?|sessione|sessioni|fase|fasi|passo|passi|ciclo))$",
    r"^(?:yes|no)(?:[,.;:!]|\s[-\u2013\u2014])\s*i(?:['\u2019]m|\sam)\s(?:in(?:\sas\b|$)|ready\b|done\b|back\b|here\b|through\b|logged\b|signed\b|connected\b)",
    r"^(?:yes|no)(?:[,.;:!]|\s[-\u2013\u2014])\s*i(?:['\u2019]ve|\shave)\s(?:(?:already|just|only|now)\s)?(?:done|finished|created|signed|accepted|rotated|pushed|merged|installed|connected|logged|received|restarted|deployed)\b",
    r"^(?:yes|no)(?:[,.;:!]|\s[-\u2013\u2014])\s*i(?:\sonly|\sjust|\salready)\s(?:done|finished|created|signed|accepted|rotated|pushed|merged|installed|connected|logged|received|restarted|deployed)\b",
    r"^(?:yes|no)(?:[,.;:!]|\s[-\u2013\u2014])\s*i did(?: it)?$",
    r"^(?:you|i)(?:['\u2019]ll)? (?:run|do|execute|drive) it(?:$|\s*[,;\u2013\u2014-]\s*(?:you|i)(?:['\u2019]ll)?\s)",
)

#: Questions about the session itself, matched on the casefolded, stripped
#: question text. The going-on verbs count only where they ask whether to go on:
#: the question is the verb alone (after "shall we", "can I", ...), or it closes
#: on "OK to <verb>"; either may end in "now", "anyway", "later", "please",
#: "(y/n)", "[y/n]" or "with ...". Anywhere else they name a design topic
#: ("Which retry policy ...?", "How many times should we retry?"), and that
#: question must reach the brain. Accepted limit: "Should we retry with backoff?" reads like
#: "Do we continue with the refactor?" and is treated as session-shaped. That
#: is the safe direction: the question is asked and nothing is recorded.
#: "Which file?" and its kin ask where to work, unless the noun is the first half
#: of a design topic ("Which file format ...?", "Which branch strategy ...?",
#: "Which line of products ...?"). The topics are listed per noun, so one that is
#: not ("Which file watcher ...?", "Which commit message ...?", "Which file system
#: ...?") is still asked and not recorded, the same safe direction.
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
    r"\bwhich (?:files?\b(?! (?:formats?|naming|structures?|layouts?|organi[sz]ations?)(?![\w-]))"
    r"|(?:directory|directories|folder)\b(?! (?:structures?|layouts?|naming|organi[sz]ations?)(?![\w-]))"
    r"|branch\b(?! (?:strateg(?:y|ies)|models?|protection|polic(?:y|ies)|naming|workflows?)(?![\w-]))"
    r"|commit\b(?! (?:conventions?|polic(?:y|ies)|signing)(?![\w-]))"
    r"|line\b(?! (?:endings?|length|width|of (?:business|products?))(?![\w-]))"
    r"|worktree\b)",
    r"\b(what|did|do) you mean\b",
    # A status question about something the person did, by a closed list of status
    # verbs that name an action on the session (not run/get/check, which also open
    # design questions); one that offers alternatives ("... or ...") asks for a decision.
    r"^(?!.*\bor\b)(?:did|have|had) you (?:(?:already|ever|yet|just|finally) )?(?:sign(?:ed)? up|accept(?:ed)?"
    r"|push(?:ed)?|rotated?|install(?:ed)?|connect(?:ed)?|log(?:ged)? in|merged?"
    r"|deploy(?:ed)?|restart(?:ed)?|finish(?:ed)?|receive[d]?)\b",
    # brain-seed's own screens, by the wording its SKILL.md gives them: what a run
    # should learn from (screen 1) and which found documents it sends (screen 2).
    r"\bshould the brain learn from\?",
    r"\bwhich should go to the brain\?|^i found (?:the same )?\d+ (?:\w+ )?documents?\b",
)

_PARENTHETICAL_RE = re.compile(r"\s*\(.*\)$")
_FILE_EXTENSION_RE = re.compile(r"\.[A-Za-z0-9]{1,6}$")
_PROTOCOL_VERSION_RE = re.compile(r"HTTP/[1-3](?:\.[01])?")

#: Words that look like a file or a path and are not, matched exactly. The names
#: are the dotted products an agent most often offers in a design question.
NOT_PATHS = frozenset({
    "Next.js", "Node.js", "Nuxt.js", "Vue.js", "Chart.js", "D3.js", "Three.js",
    "three.js", "Express.js", "Ember.js", "Backbone.js", "Alpine.js", "Solid.js",
    "Moment.js", "Day.js", "Socket.IO", "ASP.NET", "ADO.NET", "VB.NET",
    "CI/CD", "A/B", "I/O", "UI/UX", "TCP/IP",
})


def normalize_label(label):
    text = label.casefold().strip()
    text = _PARENTHETICAL_RE.sub("", text).strip()
    return text.rstrip(".!?:").strip()


def is_path_shaped(label):
    """True when the label names a file or a directory: it holds a backslash, a
    word with a slash, or ends in a word with a file extension.

    The exceptions are closed, because a path read as a design option is the
    unsafe direction (the answer is recorded as a decision): a word in
    `NOT_PATHS` and an HTTP version ("HTTP/2"). Accepted limit: a product
    missing from `NOT_PATHS` ("Pixi.js", "gRPC/REST") and a label ending in a
    version ("Python 3.12", "v1.2") read as paths, and that question is asked
    and not recorded."""
    if "\\" in label:
        return True
    words = label.split()
    if any("/" in word and not _is_not_a_path(word) for word in words):
        return True
    return bool(words) and not _is_not_a_path(words[-1]) and bool(_FILE_EXTENSION_RE.search(words[-1]))


def _is_not_a_path(word):
    return word in NOT_PATHS or _PROTOCOL_VERSION_RE.fullmatch(word) is not None


def is_session_shaped(question_text, labels):
    """True when the question is about the session, by its option labels or by
    its text. `labels` is the list of offered label strings (possibly empty)."""
    normalized = [normalize_label(label) for label in labels]
    if any(label in SESSION_LABELS for label in normalized):
        return True
    if any(re.search(pattern, label) for label in normalized for pattern in SESSION_LABEL_PATTERNS):
        return True
    if any(is_path_shaped(label) for label in labels):
        return True
    folded = question_text.casefold().strip().strip("*_`'\"").strip()
    return any(re.search(pattern, folded) for pattern in SESSION_QUESTION_PATTERNS)
