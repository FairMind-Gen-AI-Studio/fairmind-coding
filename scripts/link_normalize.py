#!/usr/bin/env python3
"""One canonical link per document, so a re-run updates a card instead of
making a second one.

The brain keys a document by the link exactly as sent. A page is reached by
many spellings (a title slug, a peek view, a tracking parameter, the files tab),
so this maps each to one link per source: Notion, Confluence, Google Drive,
GitHub and the plain web. Nothing here calls a service: the link is built from
the URL it is given, and from a page id only when there is no URL.

    link_normalize.py --in INVENTORY.json --out DOCUMENTS.json
    link_normalize.py --url U [--source S] [--page-id I] [--site H]

Batch input is a JSON list of rows `{url?, page_id?, source?, site?, repo?,
path?, ref?, local_path?, ...}`; a GitHub row built from `repo` and `path`
needs its `ref`; a row with only `local_path` (a file with no address) becomes
`local:<path>` with `source: "local"` and `local: true`; every other key (title, edited, space, hint) is passed
through. A web link keeps its query except tracking parameters and anything
that carries a credential (a token, a key, a signature, a pre-signed URL's
fields). A GitHub link is rebuilt from the whole remainder after `blob` or
`tree`, so a branch name with a slash costs nothing: the link is the same
path, lower-cased only in owner and repository. The output keeps the row and adds `source`, `link`, `page_id`, `form`
(page, database, space, tiny, display, published, folder, blob, tree, wiki,
web), `built` (the link was made from an id, not from a URL), `needs_id` (the
link is a short or display form whose page id is still unknown) and `slug`
(`d` plus sixteen hex digits of the link's hash). Rows sharing a link collapse
into the first; a row that cannot be linked is listed in `errors` and kept out
of the file. stdout is one JSON document.

Exit codes: 0 ok; 2 usage, or a single `--url` that cannot be linked; 3 the
input is unreadable or not a list; 5 the output cannot be written. stdlib only.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from urllib.parse import unquote, urlsplit

from _fm_ignore import makedirs_ignored
from _plugin_policy import _atomic_write_json

SOURCES = ("notion", "confluence", "google_drive", "github", "web")
_ALIASES = {"gdrive": "google_drive", "google-drive": "google_drive",
            "drive": "google_drive", "googledrive": "google_drive"}
_HEX32_TAIL = re.compile(r"([0-9a-fA-F]{32})$")
_UUID = re.compile(r"([0-9a-fA-F]{8})-([0-9a-fA-F]{4})-([0-9a-fA-F]{4})-"
                   r"([0-9a-fA-F]{4})-([0-9a-fA-F]{12})")
_CONF_PAGE = re.compile(
    r"/(?:spaces/[^/]+/)?(?:pages|blog/\d{4}/\d{2}/\d{2})/"
    r"(?:edit-v2/|edit/)?(\d+)(?:/|$)")
_DRIVE_FILE = re.compile(r"/d/(?!e/)([A-Za-z0-9_-]{10,})")
_DRIVE_PUBLISHED = re.compile(r"/d/e/[A-Za-z0-9_-]+")
_DRIVE_ID = re.compile(r"^[A-Za-z0-9_-]{10,}$")
_DRIVE_FOLDER = re.compile(r"/folders/([A-Za-z0-9_-]{10,})")
_TRACKING = re.compile(
    r"^(utm_[a-z0-9_]+|gclid|dclid|gbraid|wbraid|fbclid|msclkid|yclid|mc_cid"
    r"|mc_eid|_hsenc|_hsmi|mkt_tok|igshid)$", re.I)
# A query parameter that carries a credential (a token, a signature, a
# pre-signed URL's fields) never reaches a card: the link is stored and shown.
# The name is percent-decoded, split into words on '_', '-', '.' and camelCase
# boundaries and lowercased; it is a credential when a WORD is a credential
# word (`api_key`, `accessToken`, `X-Amz-Signature`, `sig`, `code`), so a
# spelling variant cannot slip through while a name that merely contains one
# (`keyword`, `design`, `session_title`) stays what identifies a document. The
# trade-off is deliberate: a link identified ONLY by a credential word loses it
# and merges with its siblings, which is cheaper than storing a secret.
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_NAME_WORDS = re.compile(r"[_\-.\s]+")
_SECRET_WORDS = frozenset((
    "token", "secret", "password", "passwd", "pwd", "passphrase", "sig",
    "signature", "auth", "oauth", "authorization", "jwt", "bearer", "apikey",
    "key", "credential", "credentials", "code", "sid", "sessionid",
    "jsessionid", "phpsessid"))
_SECRET_PAIRS = frozenset((("session", "id"),))
_SECRET_PREFIXES = (("x", "amz"), ("x", "goog"))
_SECRET_EXACT = frozenset(("sv", "se", "sp", "sr", "spr"))
# Names that identify a document and are kept even though a word above would
# match part of them.
_IDENTIFYING = re.compile(r"^(id|pageid|page_id|doc|docid|doc_id|p|v|gid|tab|page"
                          r"|sheet|fileid|file_id|resourcekey_id)$")


def _credential_name(name):
    name = unquote(name)
    flat = name.lower().replace("-", "_")
    if _IDENTIFYING.match(flat):
        return False
    words = [w for w in _NAME_WORDS.split(_CAMEL.sub("_", name).lower()) if w]
    return (flat in _SECRET_EXACT
            or any(w in _SECRET_WORDS for w in words)
            or any(pair in _SECRET_PAIRS for pair in zip(words, words[1:]))
            or tuple(words[:2]) in _SECRET_PREFIXES)


_NOTION_ID = re.compile(r"^[0-9a-f]{32}$")


def document_slug(link):
    return "d" + hashlib.sha256(link.encode("utf-8")).hexdigest()[:16]


class Unlinkable(Exception):
    pass


def _result(source, link, page_id=None, form="web", built=False, needs_id=False):
    return {"source": source, "link": link, "page_id": page_id, "form": form,
            "built": built, "needs_id": needs_id}


def _query(parts):
    pairs = {}
    for piece in parts.query.split("&"):
        if "=" in piece:
            key, value = piece.split("=", 1)
            pairs.setdefault(key, value)
    return pairs


def _split(url):
    if not isinstance(url, str):
        return None
    try:
        parts = urlsplit(url.strip())
        parts.port  # noqa: B018 - raises on a bad port
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    return parts


def _host(parts):
    port = parts.port
    host = parts.hostname.lower()
    default = {"http": 80, "https": 443}[parts.scheme.lower()]
    return host + (":%d" % port if port and port != default else "")


def detect_source(url):
    parts = _split(url)
    if parts is None:
        return None
    host = parts.hostname.lower()
    if host == "notion.so" or host.endswith((".notion.so", ".notion.site")):
        return "notion"
    if host.endswith(".atlassian.net") and parts.path.startswith("/wiki"):
        return "confluence"
    if parts.path.endswith("/pages/viewpage.action"):
        return "confluence"
    if host in ("docs.google.com", "drive.google.com"):
        return "google_drive"
    if host in ("github.com", "www.github.com", "raw.githubusercontent.com"):
        return "github"
    return "web"


def _notion_id(value):
    if not isinstance(value, str):
        return None
    plain = value.strip().replace("-", "").lower()
    return plain if _NOTION_ID.match(plain) else None


def _notion_from_segment(segment):
    found = _HEX32_TAIL.search(segment)
    if found:
        return found.group(1).lower()
    uuid = None
    for uuid in _UUID.finditer(segment):
        pass
    return "".join(uuid.groups()).lower() if uuid else None


def _notion(row):
    parts = _split(row["url"]) if row.get("url") else None
    page_id, form = None, "page"
    if parts is not None:
        query = _query(parts)
        page_id = _notion_id(query.get("p"))
        if page_id is None:
            segments = [s for s in parts.path.split("/") if s]
            page_id = _notion_from_segment(segments[-1]) if segments else None
            if page_id and "v" in query and "p" not in query:
                form = "database"
    built = False
    if page_id is None:
        page_id = _notion_id(row.get("page_id"))
        built = page_id is not None
    if page_id is None:
        raise Unlinkable("no Notion page id in the link or the page id")
    return _result("notion", "https://www.notion.so/" + page_id, page_id,
                   form, built)


def _viewpage(scheme, host, prefix, page_id):
    return "%s://%s%s/pages/viewpage.action?pageId=%s" % (
        scheme, host, prefix, page_id)


def _confluence(row):
    page_id = _digits(row.get("page_id"))
    url = row.get("url")
    parts = _split(url) if url else None
    if parts is None:
        site = str(row.get("site") or "").strip().lower()
        if not site or page_id is None:
            raise Unlinkable("a Confluence page needs a URL, or a site and a "
                             "page id")
        prefix = "/wiki" if site.endswith(".atlassian.net") else ""
        return _result("confluence",
                       _viewpage("https", site, prefix, page_id), page_id,
                       "page", True)
    host, path = _host(parts), parts.path.rstrip("/")
    atlassian = parts.hostname.lower().endswith(".atlassian.net")
    scheme = "https" if atlassian else parts.scheme.lower()
    found = _CONF_PAGE.search(path)
    from_url = None
    prefix = "/wiki" if atlassian or path.startswith("/wiki") else ""
    if found:
        from_url = found.group(1)
        if not atlassian and not path.startswith("/wiki"):
            prefix = path[:found.start()]
    elif _query(parts).get("pageId", "").isdigit():
        from_url = _query(parts)["pageId"]
        if path.endswith("/pages/viewpage.action"):
            prefix = path[:-len("/pages/viewpage.action")]
            if atlassian:
                prefix = "/wiki"
    if from_url:
        return _result("confluence",
                       _viewpage(scheme, host, prefix, from_url), from_url,
                       "page")
    form = None
    if re.search(r"(?:^|/)x/[^/]+$", path):
        form = "tiny"
    elif re.search(r"(?:^|/)display/[^/]+/[^/]+", path):
        form = "display"
    elif re.search(r"/spaces/[^/]+(?:/overview)?$", path):
        form = "space"
    if form is None:
        if page_id is not None:
            return _result("confluence",
                           _viewpage(scheme, host, prefix, page_id),
                           page_id, "page", True)
        raise Unlinkable("no Confluence page id in the link")
    if form == "space":
        return _result("confluence", "%s://%s%s" % (scheme, host, path),
                       None, "space")
    if page_id is not None:
        return _result("confluence",
                       _viewpage(scheme, host, prefix, page_id), page_id,
                       form, True)
    return _result("confluence", "%s://%s%s" % (scheme, host, path), None,
                   form, False, True)


def _digits(value):
    text = str(value).strip() if value is not None else ""
    return text if text.isdigit() else None


def _drive(row):
    parts = _split(row["url"]) if row.get("url") else None
    if parts is not None:
        host, path = parts.hostname.lower(), parts.path
        folder = _DRIVE_FOLDER.search(path)
        if folder:
            return _result("google_drive",
                           "https://drive.google.com/drive/folders/"
                           + folder.group(1), folder.group(1), "folder")
        if _DRIVE_PUBLISHED.search(path):
            return _result("google_drive",
                           "https://%s%s" % (host, path.rstrip("/")), None,
                           "published")
        found = _DRIVE_FILE.search(path)
        file_id = found.group(1) if found else _query(parts).get("id")
        if file_id and _DRIVE_ID.match(file_id):
            return _result("google_drive",
                           "https://drive.google.com/open?id=" + file_id,
                           file_id, "page")
    given = str(row.get("page_id") or "").strip()
    if _DRIVE_ID.match(given):
        return _result("google_drive", "https://drive.google.com/open?id="
                       + given, given, "page", True)
    raise Unlinkable("no Google Drive file id in the link or the page id")


def _github(row):
    parts = _split(row["url"]) if row.get("url") else None
    owner = name = kind = ref = rest = None
    if parts is not None:
        segments = [s for s in parts.path.split("/") if s]
        if parts.hostname.lower() == "raw.githubusercontent.com":
            if len(segments) >= 4:
                owner, name = segments[0], segments[1]
                tail = segments[2:]
                if tail[:2] == ["refs", "heads"] and len(tail) > 3:
                    tail = tail[2:]
                ref, rest, kind = tail[0], "/".join(tail[1:]), "blob"
        elif len(segments) >= 2:
            owner, name = segments[0], segments[1]
            if len(segments) >= 5 and segments[2] in ("blob", "tree"):
                kind, ref = segments[2], segments[3]
                rest = "/".join(segments[4:])
            elif len(segments) >= 4 and segments[2] in ("blob", "tree"):
                kind, ref, rest = segments[2], segments[3], ""
            elif len(segments) >= 3 and segments[2] == "wiki":
                kind, rest = "wiki", "/".join(segments[3:])
            elif len(segments) == 2:
                kind, ref, rest = "repo", "", ""
            else:
                kind, rest = "web", "/".join(segments[2:])
    built = False
    if owner is None:
        repo = str(row.get("repo") or "")
        path = str(row.get("path") or "").strip("/")
        if not re.match(r"^[^/\s]+/[^/\s]+$", repo) or not path:
            raise Unlinkable("a GitHub document needs a URL, or repo and path")
        owner, name = repo.split("/", 1)
        ref = str(row.get("ref") or "").strip()
        if not ref:
            raise Unlinkable("a GitHub document built from repo and path needs "
                             "its ref: the same branch or commit for every row, "
                             "or one file becomes two cards")
        kind, rest, built = "blob", path, True
    base = "https://github.com/%s/%s" % (owner.lower(), name.lower())
    if kind in ("blob", "tree"):
        link = "%s/%s/%s%s" % (base, kind, ref, "/" + rest.rstrip("/") if rest else "")
        return _result("github", link, None, kind, built)
    if kind == "wiki":
        return _result("github", base + "/wiki" + ("/" + rest if rest else ""),
                       None, "wiki")
    if kind == "repo":
        return _result("github", base, None, "tree")
    return _result("github", base + "/" + rest, None, "web")


def _web(row):
    parts = _split(row["url"]) if row.get("url") else None
    if parts is None:
        raise Unlinkable("not an http or https link")
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    kept = [p for p in parts.query.split("&")
            if p and not _TRACKING.match(p.split("=", 1)[0])
            and not _credential_name(p.split("=", 1)[0])]
    scheme = parts.scheme.lower()
    link = "%s://%s%s" % (scheme, _host(parts), path)
    if kept:
        link += "?" + "&".join(kept)
    return _result("web", link, None, "web")


_BUILDERS = {"notion": _notion, "confluence": _confluence,
             "google_drive": _drive, "github": _github, "web": _web}


def _repo_relative(path):
    """A local file is named by a path the person can recognise inside the
    repository, never by one that reveals the machine: an absolute path, a home
    directory, a drive, share or URI (`file://`, `smb://`), a backslash, or a
    `..` segment is refused."""
    text = path.strip()
    return not (text.startswith(("/", "~", "\\"))
                or re.match(r"^[A-Za-z][A-Za-z0-9+.-]*:", text)
                or "\\" in text or ".." in text.split("/"))


def normalize_row(row):
    """-> (output row, None) or (None, reason)."""
    if not isinstance(row, dict):
        return None, "a row must be an object"
    local = row.get("local_path")
    if (isinstance(local, str) and local.strip() and not row.get("url")
            and not row.get("page_id")):
        if not _repo_relative(local):
            return None, ("a local file is named by a repo-relative path "
                          "(no absolute, home, drive or `..` path); ask the "
                          "person for the path inside the repository")
        out = dict(row)
        out.update(_result("local", "local:" + local.strip(), None, "local",
                           True), local=True)
        out["slug"] = document_slug(out["link"])
        return out, None
    given = str(row.get("source") or "").strip().lower().replace(" ", "_")
    source = _ALIASES.get(given, given)
    if source not in SOURCES:
        source = detect_source(row.get("url"))
    if source is None:
        return None, "no link, and no source to build one from"
    try:
        made = _BUILDERS[source](row)
    except Unlinkable as err:
        return None, str(err)
    out = dict(row)
    out.update(made)
    out["slug"] = document_slug(made["link"])
    return out, None


def _write_json(path, document):
    try:
        makedirs_ignored(os.path.dirname(path) or ".")
        _atomic_write_json(path, document, prefix=".fm-links-", mode=0o644)
    except OSError as err:
        print("link_normalize: cannot write %s: %s"
              % (path, err.strerror or err.__class__.__name__), file=sys.stderr)
        return False
    return True


def _batch(args):
    try:
        with open(args.input, encoding="utf-8") as handle:
            rows = json.load(handle)
    except (OSError, ValueError):
        print("link_normalize: cannot read %s as JSON" % args.input,
              file=sys.stderr)
        return 3
    if not isinstance(rows, list):
        print("link_normalize: %s must hold a JSON list" % args.input,
              file=sys.stderr)
        return 3
    documents, errors, urls = [], [], {}
    for index, row in enumerate(rows):
        out, reason = normalize_row(row)
        if out is None:
            errors.append({"index": index, "reason": reason})
            continue
        first = out["link"] not in urls
        urls.setdefault(out["link"], []).append(
            row.get("url") or row.get("page_id") or out["link"])
        if first:
            documents.append(out)
    duplicates = [{"link": link, "urls": found}
                  for link, found in urls.items() if len(found) > 1]
    if not _write_json(args.out, documents):
        return 5
    print(json.dumps({"documents": len(documents), "duplicates": duplicates,
                      "errors": errors, "out": args.out}, ensure_ascii=False))
    return 0


def _single(args):
    row = {key: value for key, value in (
        ("url", args.url), ("source", args.source), ("page_id", args.page_id),
        ("site", args.site)) if value}
    out, reason = normalize_row(row)
    if out is None:
        print("link_normalize: %s" % reason, file=sys.stderr)
        return 2
    print(json.dumps(out, ensure_ascii=False))
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(prog="link_normalize.py")
    parser.add_argument("--in", dest="input")
    parser.add_argument("--out")
    parser.add_argument("--url")
    parser.add_argument("--source")
    parser.add_argument("--page-id")
    parser.add_argument("--site")
    try:
        args = parser.parse_args(argv)
    except SystemExit as stop:
        return stop.code if isinstance(stop.code, int) else 2
    if args.input and args.out and not args.url:
        return _batch(args)
    if (args.url or args.page_id) and not args.input:
        return _single(args)
    print("link_normalize: pass --in and --out, or --url (or --source with "
          "--page-id)", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
