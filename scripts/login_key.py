"""One spelling of a person's login, the way the brain keys it.

The brain keys a conversation participant `<source>:<login>` and selects an
erasure on that key, so the writer, the erased check and the redaction must
compute the same bytes the brain does. This module is that computation, stdlib
only, and `tests/test_login_key.py` holds it to the brain's own function.

The rules, in order:

* control characters and invalid UTF-8 are scrubbed (a tab, a newline and a
  carriage return survive the scrub and then fail the whitespace check);
* the source is stripped and lower-cased; it must not be blank, hold `:` or
  hold whitespace;
* the login is stripped and casefolded; a blank login or one with whitespace
  inside, or longer than 100 characters, has no key;
* for the source `notion` only, a leading `notion:` is dropped and a login
  that is exactly 32 hex digits, with or without a dash at each of the standard
  places, becomes a lowercase dashed UUID;
* any other source keeps its login as sent, a `github:` prefix included.
"""

import re
import uuid

MAX_LOGIN_LENGTH = 100
PREFIXED_SOURCE = "notion"

_CONTROLS = {i: "�" for i in range(0x20) if i not in (0x09, 0x0A, 0x0D)}
_NOTION_ID = re.compile(
    r"[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?"
    r"[0-9a-fA-F]{12}")


def sanitize(value):
    """Text with control bytes and invalid UTF-8 replaced, as the brain does."""
    text = "" if value is None else value if isinstance(value, str) \
        else str(value)
    cleaned = text.encode("utf-8", errors="replace").decode(
        "utf-8", errors="replace")
    return cleaned.translate(_CONTROLS)


def notion_id(value):
    """A Notion id as a lowercase dashed UUID, or None when it is not one."""
    if not isinstance(value, str) or not _NOTION_ID.fullmatch(value.strip()):
        return None
    return str(uuid.UUID(value.strip()))


def source_of(source):
    """The source as the brain spells it. Raises ValueError when it is not a
    platform name."""
    platform = sanitize(source).strip().lower()
    if not platform or ":" in platform \
            or any(c.isspace() for c in platform):
        raise ValueError("source %r must be a non-empty platform name with "
                         "no ':' or whitespace" % (source,))
    return platform


def login_name(source, login):
    """The login half of the key. Raises ValueError like the brain does."""
    platform = source_of(source)
    name = sanitize(login).strip().casefold()
    if platform == PREFIXED_SOURCE:
        name = name.removeprefix(platform + ":")
        name = notion_id(name) or name
    if not name or any(c.isspace() for c in name):
        raise ValueError("login must be non-empty and contain no whitespace")
    if len(name) > MAX_LOGIN_LENGTH:
        raise ValueError("login must be at most %d characters"
                         % MAX_LOGIN_LENGTH)
    return name


def login_key(source, login):
    """`<source>:<login>`, the one spelling of a participant."""
    return "%s:%s" % (source_of(source), login_name(source, login))


def match_key(source, login):
    """A key to compare logins by, never raising. A login the brain would
    refuse for the whitespace inside it compares as a reading file spells it,
    with `_` for the whitespace; any other refusal compares by the scrubbed,
    stripped, casefolded text under a marker no real key carries."""
    for text in (login, re.sub(r"\s+", "_", sanitize(login).strip())):
        try:
            return login_key(source, text)
        except ValueError:
            pass
    return "?:" + sanitize(login).strip().casefold()


def wire_login(source, login):
    """A login as it travels to the brain. A Notion user travels as
    `notion:<id>`; any other login is the scrubbed, stripped text as sent.
    Raises ValueError for a login the brain would refuse."""
    name = login_name(source, login)
    if source_of(source) == PREFIXED_SOURCE:
        return "%s:%s" % (PREFIXED_SOURCE, name)
    return sanitize(login).strip()
