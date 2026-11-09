"""Brain_tell report building blocks shared by the answer hook, the loop's
brain write-back and the artifact cards.

Stdlib only and import-light on purpose: a hook imports this module on every
answered question, so it must stay a leaf (no ledger, no outbox, no network).
It never talks to the brain; it shapes text the door accepts and reads the
door's answer. Python 3.9 floor: no `match`, no `X | Y` outside annotations.

THE DOOR REFUSES TEXT, IT NEVER ALTERS IT. Every text field is checked after
the server's own sanitising: a control character, a lone surrogate, a newline
in a one-line field, or a length over the cap is a refusal, never a quiet
repair. The `fit_*` helpers therefore clean and fold BEFORE they cut, and the
title helpers fold to one line first so a question that wraps over three lines
and its one-line spelling give the same title and the same key.

A TITLE IS A KEY. The server slugifies a decision's, problem's, need's and
component's title into its natural key and refuses a slug over the cap rather
than cutting it (a cut would merge two titles onto one node). A title built
here is cut at a word to fit both the title cap and the slug cap; when a cut
would make two different inputs look alike, `fit_pair` ends the title with a
digest of the whole input so they keep two keys while a retry still converges.

THE OUTCOME VOCABULARY IS COPIED, NOT IMPORTED. `TELL_STATES` and
`TELL_REFUSAL_REASONS` mirror `app/utils/brain/tell.py` of project-context.
An unknown code reads `unknown`, which never settles a row and reads as a
refusal on the hook: a server that grows a code must not look like success.
"""

from __future__ import annotations

import hashlib
import re
from urllib.parse import quote, urlsplit

__all__ = [
    "DIRECTORIES_MAX",
    "FILES_MAX",
    "FUNCTIONS_MAX",
    "ITEM_KEY_SLUG_MAX",
    "KEY_SLUG_MAX",
    "PAIR_HEAD_MIN",
    "PATH_MAX",
    "REF_MAX",
    "TELL_REFUSAL_REASONS",
    "TELL_STATES",
    "TEXT_MAX",
    "TITLE_MAX",
    "clean_text",
    "fit_files",
    "fit_functions",
    "fit_pair",
    "fit_ref",
    "fit_tagged",
    "fit_text",
    "fit_title",
    "forge_blob_url",
    "git_document_report",
    "key_slug",
    "one_line",
    "tell_outcome",
]

TITLE_MAX = 256
TEXT_MAX = 1000
REF_MAX = 256
FILES_MAX = 100
FUNCTIONS_MAX = 100
PATH_MAX = 1024
DIRECTORIES_MAX = 10
# The slug caps leave room under the server's 128-character key for the
# prefix and the project id the key is assembled with.
KEY_SLUG_MAX = 120
ITEM_KEY_SLUG_MAX = 90
# The shortest head `fit_pair` keeps before it starts cutting the tail instead.
PAIR_HEAD_MIN = 40

_ELLIPSIS = "\u2026"
_SEPARATOR = " \u00b7 "
_DIGEST_CHARS = 12
_URL_MAX = 2048

_NON_SLUG = re.compile(r"[^a-z0-9]+")
_CONTROLS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
_SURROGATES = re.compile("[\ud800-\udfff]")
_FOLD = re.compile(r"\s*[\r\n\t]\s*")

# Closed vocabularies of the Brain_tell answer (`app/utils/brain/tell.py`).
TELL_STATES = (
    "confirmed", "proposed", "duplicate", "conflict",
    "retracted", "unreachable", "changed", "absent",
)
TELL_REFUSAL_REASONS = (
    "intent_unknown", "kind_not_registered", "field_required", "field_invalid",
    "field_too_long", "field_not_accepted", "field_not_for_intent",
    "fields_exclusive", "related_over_cap", "repository_unresolved",
    "node_unavailable", "node_kind_mismatch", "node_lookup_capped",
    "retract_not_allowed", "revise_not_allowed", "identity_exhausted",
)

# A refusal of the report's own words: resending the same report cannot help,
# and nothing is wrong with the plugin.
_CONTENT_REASONS = frozenset({
    "identity_exhausted", "node_unavailable", "node_kind_mismatch",
    "node_lookup_capped", "retract_not_allowed", "revise_not_allowed",
})
_CONTENT_TEXT_FIELDS = frozenset({"title", "summary"})
_DOCUMENT_ID_FIELD = re.compile(r"^(source|documents\[\d+\])\.document_id$")
_OWN_STATES = ("duplicate", "conflict", "retracted", "changed", "absent",
               "unreachable")


# --------------------------------------------------------------------- text

def clean_text(text):
    """`text` without the characters the door refuses, stripped; "" for a
    non-string. Controls below 0x20 other than tab, newline and carriage
    return, and lone surrogates, are dropped."""
    if not isinstance(text, str):
        return ""
    text = _SURROGATES.sub("", _CONTROLS.sub("", text))
    return text.strip()


def one_line(text):
    """`clean_text` with every run of whitespace that holds a newline, a
    carriage return or a tab folded to one space."""
    return _FOLD.sub(" ", clean_text(text))


def key_slug(text):
    """The slug the server builds a natural key from: lowercase, every run of
    other characters one `-`, no leading or trailing `-`."""
    return _NON_SLUG.sub("-", clean_text(text).lower()).strip("-")


def _title_fits(text, slug_max):
    return len(text) <= TITLE_MAX and len(key_slug(text)) <= slug_max


def _cut_at_word(text, fits, max_len=TITLE_MAX):
    """The longest prefix of `text` ending at a word, plus an ellipsis, for
    which `fits` holds; a hard cut when one word is longer. `text` itself when
    it already fits; None when nothing fits. `max_len` bounds the work: no
    prefix longer than it is tried."""
    if fits(text):
        return text
    stops = [m.start() for m in re.finditer(r"\s+", text) if m.start() < max_len]
    for stop in reversed(stops):
        candidate = text[:stop].rstrip() + _ELLIPSIS
        if candidate != _ELLIPSIS and fits(candidate):
            return candidate
    for end in range(min(len(text), max_len), 0, -1):
        candidate = text[:end].rstrip() + _ELLIPSIS
        if candidate != _ELLIPSIS and fits(candidate):
            return candidate
    return None


def fit_title(text, *, slug_max):
    """A one-line title within the title cap and `slug_max`, cut at a word.
    None when the text holds no letter or digit to key on."""
    folded = one_line(text)
    if not key_slug(folded):
        return None
    cut = _cut_at_word(folded, lambda value: _title_fits(value, slug_max))
    if cut is None or not key_slug(cut):
        return None
    return cut


def fit_text(text, limit):
    """`text` cleaned and cut at a word to `limit` characters, newlines kept
    (a summary or a question may span lines). None when nothing is left."""
    cleaned = clean_text(text)
    if not cleaned:
        return None
    return _cut_at_word(cleaned, lambda value: len(value) <= limit, limit)


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_CHARS]


def fit_pair(head, sep, tail, *, slug_max):
    """`head + sep + tail` as a one-line title within both caps. Neither part
    is ever dropped. One fixed cut order: whole; else the head cut at a word
    (down to PAIR_HEAD_MIN characters) with the tail whole; else the head at
    PAIR_HEAD_MIN and the tail cut. A cut title ends with a digest of the whole
    folded pair, so two pairs that differ only past the cut keep two titles
    and two keys, and a wrapped spelling of one pair hashes equal. A pair that
    fits whole carries no digest. None when it cannot be keyed."""
    first, second = one_line(head), one_line(tail)
    if not first or not second:
        return None
    whole = first + sep + second
    if _title_fits(whole, slug_max):
        return whole if key_slug(whole) else None
    tag = _SEPARATOR + _digest(whole)

    def fits_with(head_text, tail_text):
        return _title_fits(head_text + sep + tail_text + tag, slug_max)

    head_cut = None
    if len(first) > PAIR_HEAD_MIN:
        head_cut = _cut_at_word(first, lambda v: fits_with(v, second))
        if head_cut is not None and len(head_cut) < PAIR_HEAD_MIN:
            head_cut = None
    if head_cut is not None:
        return head_cut + sep + second + tag
    short_head = first if len(first) <= PAIR_HEAD_MIN else _cut_at_word(
        first, lambda v: len(v) <= PAIR_HEAD_MIN)
    if short_head is None:
        return None
    tail_cut = _cut_at_word(second, lambda v: fits_with(short_head, v))
    if tail_cut is None:
        return None
    return short_head + sep + tail_cut + tag


def fit_tagged(text, tag, *, slug_max):
    """`text + " \u00b7 " + tag` as a one-line title: the text is cut at a word
    to leave room, the tag never is. None when the tag alone does not fit or
    the text is blank."""
    folded = one_line(text)
    tag = one_line(tag)
    if not folded or not tag:
        return None
    suffix = _SEPARATOR + tag
    if not _title_fits(suffix.strip(), slug_max) or not key_slug(folded):
        return None
    cut = _cut_at_word(folded, lambda v: _title_fits(v + suffix, slug_max))
    if cut is None:
        return None
    return cut + suffix


def fit_ref(text):
    """A one-line routing reference within REF_MAX, or None when blank."""
    return fit_text(one_line(text), REF_MAX)


def fit_files(files, limit=FILES_MAX):
    """The first `limit` distinct non-blank paths of at most PATH_MAX
    characters, in the order given, so a retry sends the same list."""
    seen, kept = set(), []
    for item in files or ():
        if not isinstance(item, str):
            continue
        path = item.strip()
        if not path or len(path) > PATH_MAX or path in seen:
            continue
        seen.add(path)
        kept.append(path)
        if len(kept) == limit:
            break
    return kept


def fit_functions(functions, limit=FUNCTIONS_MAX):
    """The first `limit` valid function references, in the order given. A
    string is `<file>::<name>`; an object keeps `file_path`, `name` and a
    `start_line` only when it is a positive integer (never a bool)."""
    kept = []
    for item in functions or ():
        if isinstance(item, str):
            path, sep, name = item.partition("::")
            if sep and path.strip() and name.strip():
                kept.append(item)
        elif isinstance(item, dict):
            path, name = item.get("file_path"), item.get("name")
            if not (isinstance(path, str) and path.strip()
                    and isinstance(name, str) and name.strip()):
                continue
            entry = {"file_path": path, "name": name}
            line = item.get("start_line")
            if isinstance(line, int) and not isinstance(line, bool) and line >= 1:
                entry["start_line"] = line
            kept.append(entry)
        if len(kept) == limit:
            break
    return kept


# ------------------------------------------------------------------ forge url

_REMOTE_PATH = re.compile(r"^/?(?P<path>[^/\s]+(?:/[^/\s]+)+)/?$")
_SCP_REMOTE = re.compile(r"^(?:[^@/\s]+@)?(?P<host>[^:/\s]+):(?P<path>[^\s]+)$")


def forge_blob_url(git_remote, *, commit, path):
    """The forge's link to `path` at `commit`, or None when it cannot be
    derived. Built from the remote's host and repository path alone: an ssh
    remote's credentials and port never reach the link. GitHub and Gitea read
    `/blob/`, GitLab `/-/blob/`, Bitbucket Cloud `/src/`. The same rules as
    the server's `forge_blob_url`, pinned to it by a shared vectors fixture."""
    if not isinstance(git_remote, str) or not git_remote.strip():
        return None
    remote = git_remote.strip()
    scheme = "https"
    if "://" in remote:
        try:
            parts = urlsplit(remote)
            port = parts.port
        except ValueError:
            return None
        host = parts.hostname
        if parts.scheme in ("http", "https"):
            scheme = parts.scheme
            host = "%s:%s" % (host, port) if host and port else host
        repo_path = parts.path
    else:
        scp = _SCP_REMOTE.match(remote)
        host, repo_path = (scp.group("host"), scp.group("path")) if scp else (None, None)
    match = _REMOTE_PATH.match(repo_path or "")
    if not host or not match:
        return None
    repo = match.group("path")
    if repo.endswith(".git"):
        repo = repo[:-len(".git")]
    segment = "blob"
    lowered = host.lower()
    if "gitlab" in lowered:
        segment = "-/blob"
    elif lowered.split(":")[0] == "bitbucket.org":
        segment = "src"
    url = "%s://%s/%s/%s/%s/%s" % (scheme, lowered, repo, segment, commit,
                                   quote(path, safe="/"))
    return url if len(url) <= _URL_MAX else None


# -------------------------------------------------------------------- answers

def tell_outcome(reply):
    """What a Brain_tell answer means for the caller, one word:

    written (confirmed or proposed), duplicate, conflict, retracted, changed,
    absent, unreachable (the answers of a document check or a marked link);
    refused (the report's own words were refused: resending cannot help);
    mismatch (the plugin and the brain disagree on the report's shape);
    unresolved (no repository or project to file it under, or access denied);
    retry (the brain could not finish; the same report may be sent again);
    throttled (today's write budget is spent); unknown (anything else)."""
    if not isinstance(reply, dict):
        return "unknown"
    state = reply.get("state")
    if state in ("confirmed", "proposed"):
        return "written"
    if state in _OWN_STATES:
        return state
    status = reply.get("status")
    if status == "refused":
        return _refusal_outcome(reply.get("reason"), reply.get("field"))
    if status == "throttled":
        return "throttled"
    if status == "error":
        message = reply.get("message")
        if isinstance(message, str) and message.startswith("Access denied"):
            return "unresolved"
        return "unknown"
    if reply.get("retryable") is True:
        return "retry"
    return "unknown"


def _refusal_outcome(reason, field):
    if reason not in TELL_REFUSAL_REASONS:
        return "unknown"
    if reason == "repository_unresolved":
        return "unresolved"
    if reason in _CONTENT_REASONS:
        return "refused"
    if reason in ("field_invalid", "field_too_long"):
        if field in _CONTENT_TEXT_FIELDS:
            return "refused"
        if reason == "field_invalid" and isinstance(field, str) \
                and _DOCUMENT_ID_FIELD.match(field):
            return "refused"
    return "mismatch"


# ------------------------------------------------------------ document report

_VISIBILITIES = ("public", "private", "restricted")


def git_document_report(entry, *, mode="write"):
    """The Brain_tell arguments that file, check or retire the card of one
    file in a repository. `entry` carries `path`, `repository`, `git_remote`,
    `project`, `agent`, `blob_sha` and, to file it, `title`, `summary`,
    `commit`, `branch`, `source_visibility` and optionally `renamed_from` and
    `published_url`. Raises ValueError naming the missing or unusable field.

    write: the card of the file at that blob. check: the file and its blob
    alone (no summary, so the brain answers whether the card is current).
    gone: the file left the repository (no blob, no summary)."""
    if mode not in ("write", "check", "gone"):
        raise ValueError("mode must be write, check or gone")

    def need(name):
        value = entry.get(name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError("`%s` is required" % name)
        return value

    report = {
        "intent": "document",
        "repository": need("repository"),
        "git_remote": need("git_remote"),
        "project": need("project"),
        "agent": fit_ref(need("agent")),
    }
    git = {"path": need("path")}
    source = {"git": git}
    report["source"] = source
    if mode == "gone":
        git["path_gone"] = True
        return report
    source["version"] = need("blob_sha")
    if mode == "check":
        return report
    summary = clean_text(need("summary"))
    if len(summary) > TEXT_MAX:
        raise ValueError("the summary is %d characters; the limit is %d"
                         % (len(summary), TEXT_MAX))
    git["commit"] = need("commit")
    git["branch"] = need("branch")
    if entry.get("renamed_from"):
        git["renamed_from"] = need("renamed_from")
    if entry.get("published_url"):
        git["published_url"] = need("published_url")
    visibility = entry.get("source_visibility")
    if visibility is not None:
        if visibility not in _VISIBILITIES:
            raise ValueError("`source_visibility` must be public, private, "
                             "restricted or null")
        source["visibility"] = visibility
    title = fit_text(one_line(need("title")), TITLE_MAX)
    if title is None:
        raise ValueError("`title` is required")
    report["title"] = title
    report["summary"] = summary
    return report
