"""One pull request's URL in one spelling, so two links to it compare equal.

https (unless a non-default port is kept), a lower-cased host without its
default port, no query, fragment or trailing slash, and the path
cut after the pull request's number (GitHub `/pull/<n>`, GitLab
`/-/merge_requests/<n>`, Bitbucket `/pull-requests/<n>`, Azure DevOps
`/pullrequest/<n>`): a tracker links the files or commits tab as often as the
page itself. The path keeps its case for display; `pull_request_key` folds it,
because the brain matches these links without regard to case. stdlib only."""
from __future__ import annotations

import re
from urllib.parse import urlsplit, urlunsplit

_PAGE = re.compile(
    r"^(?P<page>.*?/(?:pull|-/merge_requests|pull-requests|pullrequest)/\d+)"
    r"(?:/.*)?$", re.IGNORECASE)


def canonical_pull_request_url(url):
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
        port = parts.port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    scheme = parts.scheme.lower()
    # A default port is dropped, as a browser does; another one names a
    # different server, so it keeps its scheme too.
    if port == {"http": 80, "https": 443}[scheme]:
        port = None
    host = parts.hostname.lower() + (":%d" % port if port else "")
    path = parts.path.rstrip("/")
    page = _PAGE.match(path)
    if page:
        path = page.group("page")
    return urlunsplit((scheme if port else "https", host, path, "", ""))


def pull_request_key(url):
    canonical = canonical_pull_request_url(url)
    return canonical.lower() if canonical else None
