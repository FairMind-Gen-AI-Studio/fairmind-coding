#!/usr/bin/env python3
"""The one door through which this plugin posts on a pull request.

An agent posting with a person's `gh` credential is, to GitHub, that person.
Everything posted through here — a description, a comment, a review, a reply
to a review comment — therefore carries an invisible marker naming the agent
and the session that wrote it:

    <!-- fairmind-agent v=1 agent=<name> session=<sessionRef> -->

The format, the regex and what the marker does not prove are published in the
plugin README ("Agent signature on pull requests"); `MARKER_RE` below is that
regex, character for character.

Signing lives here and nowhere else. `tests/test_agent_signature.py` scans the
shipped plugins for any other posting call (`gh pr create|comment|review`,
`gh issue comment`, `gh pr|issue edit` with a body, `gh pr|issue close|reopen`
with a comment, `gh api` writes to a pull request, its description or its
threads, or with a `body` field, GraphQL comment/review mutations, GitHub MCP
write tools, HTTP clients writing to those endpoints) and fails on one, so a
new posting path has to come through this module to be signed. The scan
matches shapes: it catches the ways of posting it knows, not every way.

The session ref is the Claude Code session id — the same value the insights
plane records as `sessionId`/`owner_session`/`session_ref`, so a signed reply
joins the decisions its session recorded. It is read from
`CLAUDE_CODE_SESSION_ID` (set by the host in every Bash tool subprocess, equal
to the hook payload's `session_id`) and cleaned by the insights plane's own
`_insights_session._clean_session_id`. Without a usable id nothing is posted:
an unsigned post from an agent is the defect this module exists to prevent.

Usage (every subcommand posts only after signing; `--dry-run` prints instead):

    pr_post.py create  --title T --body-file F [--base B] [--head H] [--draft]
    pr_post.py edit    <pr> --body-file F
    pr_post.py comment <pr> --body-file F
    pr_post.py review  <pr> (--comment|--request-changes|--approve) --body-file F
    pr_post.py reply   <pr> <comment-id> --body-file F
    pr_post.py sign    [--body-file F]      # print the signed body, post nothing
    pr_post.py parse   [--body-file F]      # print the signature as JSON

Common options: `--repo OWNER/REPO` (posting subcommands), `--agent NAME`
(default `claude-code`), `--dry-run`. `--body-file -` reads standard input.

Exit codes: 0 posted (or printed); 1 `parse` found no signature; 2 usage or an
empty body; 3 cannot sign (no usable session id, or an invalid agent name);
127 `gh` not found; any other code is `gh`'s own.

stdlib only.
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import _insights_session  # noqa: E402  (owns the session-id cleaner)

MARKER_VERSION = 1

# Contract C3, verbatim. A consumer reads only the final non-blank line
# (`parse_marker`): a body may quote an earlier signed comment, or document
# this format, and neither of those signs it.
MARKER_RE = re.compile(
    r"<!--\s*fairmind-agent\s+v=(?P<v>\d+)\s+agent=(?P<agent>[^\s>]+)"
    r"\s+session=(?P<session>[^\s>]+)\s*-->"
)

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"
DEFAULT_AGENT = "claude-code"

# Narrower than the regex's `[^\s>]+`: what this module emits must also be free
# of anything that could read as markup, a path or an identity (no `@`, no `/`).
_AGENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")
_SESSION_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]*\Z")
_REPO_PART_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")

# Where the marker goes. It follows a blank line at column 0, which ends every
# list item, block quote and blank-terminated HTML block the text leaves open,
# and any fence inside one — so the only thing that can swallow it is a fenced
# code block left open OUTSIDE every container. `_open_fence` answers exactly
# that, following CommonMark's block structure far enough to tell the two
# apart: a column-0 closer appended after a fence that sits in a list item ends
# the item and OPENS a fence, which puts the marker in a code block. Tabs are
# expanded to a tab stop of 4 first, which is how CommonMark reads them for
# block structure; the regexes below see spaces only. `tests/test_agent_signature.py`
# pins the cases, each one checked against a CommonMark renderer.
_FENCE_RE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)$")
_ITEM_RE = re.compile(r" {0,3}([-+*]|(\d{1,9})[.)])(?: +|$)")
_BREAK_RE = re.compile(r" {0,3}(?:(?:\*[ \t]*){3,}|(?:-[ \t]*){3,}|(?:_[ \t]*){3,})$")
_HEADING_RE = re.compile(r" {0,3}#{1,6}(?:[ \t]|$)")
_UNDERLINE_RE = re.compile(r" {0,3}(?:=+|-+)[ \t]*$")
_QUOTE_RE = re.compile(r" {0,3}> ?")
_BLOCK_TAGS = (
    "address|article|aside|base|basefont|blockquote|body|caption|center|col|colgroup|dd"
    "|details|dialog|dir|div|dl|dt|fieldset|figcaption|figure|footer|form|frame|frameset"
    "|h[1-6]|head|header|hr|html|iframe|legend|li|link|main|menu|menuitem|nav|noframes|ol"
    "|optgroup|option|p|param|search|section|summary|table|tbody|td|tfoot|th|thead|title|tr"
    "|track|ul")
# CommonMark's seven HTML block kinds: (start, the pattern that ends the block
# or None when a blank line does, whether it may interrupt a paragraph).
_HTML_BLOCKS = (
    (re.compile(r" {0,3}<(?:script|pre|style|textarea)(?:[ \t>]|$)", re.I),
     re.compile(r"</(?:script|pre|style|textarea)>", re.I), True),
    (re.compile(r" {0,3}<!--"), re.compile(r"-->"), True),
    (re.compile(r" {0,3}<\?"), re.compile(r"\?>"), True),
    (re.compile(r" {0,3}<![A-Za-z]"), re.compile(r">"), True),
    (re.compile(r" {0,3}<!\[CDATA\["), re.compile(r"\]\]>"), True),
    (re.compile(r" {0,3}</?(?:%s)(?:[ \t/>]|$)" % _BLOCK_TAGS, re.I), None, True),
    (re.compile(r" {0,3}(?:<[A-Za-z][A-Za-z0-9-]*(?:\s+[A-Za-z_:][\w.:-]*"
                r"(?:\s*=\s*(?:[^\s\"'=<>`]+|'[^']*'|\"[^\"]*\"))?)*\s*/?>"
                r"|</[A-Za-z][A-Za-z0-9-]*\s*>)[ \t]*$"), None, False),
)

EXIT_NO_MARKER = 1
EXIT_USAGE = 2
EXIT_UNSIGNED = 3
EXIT_NO_GH = 127


class Unsigned(Exception):
    """Raised when a marker cannot be built; nothing may be posted."""


class UsageError(Exception):
    """Raised for an input this module refuses (empty body, flag-like id)."""


def session_ref(environ=None):
    """The session ref to sign with, or None when there is no usable one."""
    env = os.environ if environ is None else environ
    sid = _insights_session._clean_session_id(env.get(SESSION_ENV))
    return sid if sid and _SESSION_RE.match(sid) else None


def _require_session(environ):
    session = session_ref(environ)
    if session is None:
        raise Unsigned("no usable agent session id in %s — this helper posts "
                       "only from inside a Claude Code session" % SESSION_ENV)
    return session


def build_marker(agent, session):
    if not isinstance(agent, str) or not _AGENT_RE.match(agent):
        raise Unsigned("invalid agent name %r: letters, digits, '.', '_' or "
                       "'-', at most 64 characters" % (agent,))
    if not isinstance(session, str) or not _SESSION_RE.match(session):
        raise Unsigned("no usable session id")
    return "<!-- fairmind-agent v=%d agent=%s session=%s -->" % (
        MARKER_VERSION, agent, session)


def _marker_line(line):
    """The match when `line`, trailing whitespace aside, is exactly one marker."""
    return MARKER_RE.fullmatch(line.rstrip())


def parse_marker(text):
    """`{"v", "agent", "session"}` of the signature `text` carries, or None.

    The published consumer rule: only the final non-blank line signs, and only
    when it is exactly one marker with `v` equal to 1. A marker anywhere else —
    a quoted signed comment, a documented example, one with text typed after
    it, one indented into a code block — signs nothing."""
    found = _marker_line((text or "").rstrip().rpartition("\n")[2])
    if found is None or int(found.group("v")) != MARKER_VERSION:
        return None
    return {"v": MARKER_VERSION, "agent": found.group("agent"),
            "session": found.group("session")}


def _indent(text):
    return len(text) - len(text.lstrip(" "))


def _fence_run(text):
    """The backtick or tilde run of a fence `text` opens, or None."""
    m = _FENCE_RE.match(text)
    if not m or (m.group(1)[0] == "`" and "`" in m.group(2)):
        return None
    return m.group(1)


def _closes(text, run):
    m = _FENCE_RE.match(text)
    return bool(m and m.group(1)[0] == run[0] and len(m.group(1)) >= len(run)
                and not m.group(2).strip())


def _item_width(text, interrupting):
    """The content column, relative to `text`, of a list item `text` starts,
    or None. `interrupting`: the line would interrupt an open paragraph."""
    if _BREAK_RE.match(text):
        return None
    m = _ITEM_RE.match(text)
    if not m:
        return None
    after = text[m.end(1):]
    if interrupting and (not after.strip() or m.group(2) not in (None, "1")):
        return None
    spaces = _indent(after)
    return m.end(1) + (spaces if after.strip() and spaces <= 4 else 1)


def _html_block(text, interrupting):
    """`(end, closed)` for an HTML block `text` starts — the pattern that ends
    it (None: a blank line) and whether this line already does — or None."""
    for start, end, interrupts in _HTML_BLOCKS:
        m = start.match(text)
        if m and (interrupts or not interrupting):
            return end, bool(end and end.search(text, m.end()))
    return None


def _starts_block(text):
    """Does `text` start a block, rather than continue a paragraph lazily?"""
    return bool(_fence_run(text) or _BREAK_RE.match(text) or _HEADING_RE.match(text)
                or _QUOTE_RE.match(text) or _item_width(text, False)
                or _html_block(text, True))


def _open_fence(lines):
    """The line that closes a fence `lines` leave open outside every list item
    and block quote, or None (see the note above `_FENCE_RE`)."""
    fence = None        # (run, column) of an open fence; column 0 is the top level
    block = None        # (end, column) of an open HTML block; end None: a blank line
    items = []          # content columns of the open list items, outermost first
    empty = None        # index in `items` of an item opened by a bare marker
    quote_run = None    # run of a fence open inside a block quote
    paragraph = None    # where an open paragraph sits: None, "here" or "quote"
    for line in lines:
        indent, blank = _indent(line), not line.strip()
        if block is not None:
            end, col = block
            if col and not blank and indent < col:
                block = None            # the line ends the item, and the block in it
            elif end is None and blank:
                block = None
            else:
                if end is not None and end.search(line):
                    block = None
                continue
        if fence is not None:
            run, col = fence
            if not (col and not blank and indent < col):
                if _closes(line[col:], run):
                    fence = None
                continue
            fence = None                # the line ends the item, and the fence in it
        if blank:
            if empty is not None:
                del items[empty:]       # an item may begin with one blank line, not two
            paragraph = quote_run = empty = None
            continue
        empty = None
        depth = sum(1 for col in items if indent >= col)
        matched = depth == len(items) and paragraph != "quote"
        if not matched:
            if paragraph and not _starts_block(line[items[depth - 1] if depth else 0:]):
                continue                # a lazy continuation line
            del items[depth:]
        col = items[-1] if items else 0
        rest = line[col:]
        interrupting = matched and paragraph == "here"
        quote = _QUOTE_RE.match(rest)
        if quote is None:
            quote_run = None
        while True:
            if quote is not None:
                inner = rest[quote.end():]
                if quote_run:
                    if _closes(inner, quote_run):
                        quote_run = None
                    paragraph = None
                else:
                    quote_run = _fence_run(inner)
                    paragraph = "quote" if inner.strip() and not quote_run else None
                break
            if _indent(rest) >= 4:
                if not interrupting:
                    paragraph = None    # indented code
                break
            run = _fence_run(rest)
            if run:
                fence, paragraph = (run, col), None
                break
            html = _html_block(rest, interrupting)
            if html:
                block = None if html[1] else (html[0], col)
                paragraph = None
                break
            if interrupting and _UNDERLINE_RE.match(rest):
                paragraph = None        # a setext heading's underline
                break
            width = _item_width(rest, interrupting)
            if width is None:
                heading = _BREAK_RE.match(rest) or _HEADING_RE.match(rest)
                paragraph = None if heading else "here"
                break
            col += width
            items.append(col)
            rest, paragraph, interrupting = rest[width:], None, False
            if not rest.strip():
                empty = len(items) - 1
                break
            quote = _QUOTE_RE.match(rest)
    return fence[0] if fence and fence[1] == 0 else None


def _strip_trailing_markers(body):
    body = body.rstrip()
    while True:
        head, _, last = body.rpartition("\n")
        if not _marker_line(last):
            return body
        body = head.rstrip()


def sign_body(body, agent, session):
    """`body` with exactly one signature, as its final line.

    A marker already on the final line (a description being edited) is
    replaced rather than stacked; markers anywhere else are left alone, since
    one inside a code block is visible text. The marker follows a blank line so
    it renders as an HTML block, and a fence left open outside every container
    is closed first so the marker is not rendered as code."""
    marker = build_marker(agent, session)
    text = _strip_trailing_markers((body or "").replace("\r\n", "\n"))
    if not text.strip():
        raise UsageError("nothing to post: the body is empty")
    fence = _open_fence([line.expandtabs(4) for line in text.split("\n")])
    if fence:
        text += "\n" + fence
    return text + "\n\n" + marker


def _read_body(path):
    if path == "-":
        return sys.stdin.read()
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _write_temp(content, suffix):
    fd, path = tempfile.mkstemp(prefix="fm-pr-post-", suffix=suffix)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    return path


def _ref(value, what, digits=False):
    if not value or value.startswith("-") or (digits and not value.isdigit()):
        raise UsageError("invalid %s %r" % (what, value))
    return value


def _repo(value, host_allowed=True):
    parts = (value or "").split("/")
    if (len(parts) not in ((2, 3) if host_allowed else (2,))
            or not all(_REPO_PART_RE.match(p) for p in parts)):
        raise UsageError("invalid repository %r: expected OWNER/REPO" % (value,))
    return value


def _gh_argv(args, body_path):
    """The `gh` argv for a posting subcommand, reading the body from
    `body_path` (a JSON `{"body": …}` document for `reply`)."""
    repo = ["--repo", _repo(args.repo)] if args.repo else []
    if args.cmd == "create":
        if not args.title.strip():
            raise UsageError("a pull request needs a title")
        # `--flag=value`: a title that starts with "-" stays a value.
        argv = ["gh", "pr", "create", "--title=" + args.title,
                "--body-file", body_path] + repo
        if args.base:
            argv.append("--base=" + args.base)
        if args.head:
            argv.append("--head=" + args.head)
        if args.draft:
            argv.append("--draft")
        return argv
    if args.cmd in ("edit", "comment"):
        return ["gh", "pr", args.cmd, _ref(args.pr, "pull request"),
                "--body-file", body_path] + repo
    if args.cmd == "review":
        return ["gh", "pr", "review", _ref(args.pr, "pull request"),
                "--" + args.event, "--body-file", body_path] + repo
    if args.cmd == "reply":
        owner_repo = _repo(args.repo, host_allowed=False) if args.repo else "{owner}/{repo}"
        path = "repos/%s/pulls/%s/comments/%s/replies" % (
            owner_repo, _ref(args.pr, "pull request number", digits=True),
            _ref(args.comment_id, "comment id", digits=True))
        return ["gh", "api", "--method", "POST", path, "--input", body_path]
    raise UsageError("unknown subcommand %r" % (args.cmd,))


def post(args, environ=None):
    """Sign the body and run `gh`. Returns the process exit code."""
    signed = sign_body(_read_body(args.body_file), args.agent,
                       _require_session(environ))
    is_reply = args.cmd == "reply"
    content = json.dumps({"body": signed}) if is_reply else signed
    body_path = _write_temp(content, ".json" if is_reply else ".md")
    try:
        argv = _gh_argv(args, body_path)
        if args.dry_run:
            print(json.dumps({"argv": argv, "body": signed}, indent=2))
            return 0
        gh = shutil.which("gh")
        if gh is None:
            print("pr_post: `gh` is not on PATH", file=sys.stderr)
            return EXIT_NO_GH
        # stdin closed: the body was already read from it, and a gh prompt
        # must fail rather than wait on a shell nobody is typing into.
        return subprocess.run([gh] + argv[1:], stdin=subprocess.DEVNULL,
                              check=False).returncode
    finally:
        os.unlink(body_path)


def _parser():
    p = argparse.ArgumentParser(
        prog="pr_post.py",
        description="Post on a pull request with the agent signature.")
    sub = p.add_subparsers(dest="cmd", required=True)

    def posting(name, help_text):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--body-file", required=True,
                        help="file holding the text to post ('-' for stdin)")
        sp.add_argument("--repo", help="OWNER/REPO (default: the current repository)")
        sp.add_argument("--agent", default=DEFAULT_AGENT)
        sp.add_argument("--dry-run", action="store_true",
                        help="print the gh call and the signed body; post nothing")
        return sp

    create = posting("create", "open a pull request")
    create.add_argument("--title", required=True)
    create.add_argument("--base")
    create.add_argument("--head")
    create.add_argument("--draft", action="store_true")

    posting("edit", "replace a pull request's description").add_argument("pr")
    posting("comment", "comment on a pull request").add_argument("pr")

    review = posting("review", "submit a review")
    review.add_argument("pr")
    event = review.add_mutually_exclusive_group(required=True)
    for flag in ("comment", "request-changes", "approve"):
        event.add_argument("--" + flag, dest="event", action="store_const",
                           const=flag)

    reply = posting("reply", "reply to a review comment")
    reply.add_argument("pr")
    reply.add_argument("comment_id")

    for name, help_text in (("sign", "print the signed body; post nothing"),
                            ("parse", "print the signature a body carries, as JSON")):
        sp = sub.add_parser(name, help=help_text)
        sp.add_argument("--body-file", default="-")
        if name == "sign":
            sp.add_argument("--agent", default=DEFAULT_AGENT)
    return p


def main(argv=None, environ=None):
    args = _parser().parse_args(argv)
    try:
        if args.cmd == "parse":
            found = parse_marker(_read_body(args.body_file))
            if found is None:
                return EXIT_NO_MARKER
            print(json.dumps(found))
            return 0
        if args.cmd == "sign":
            print(sign_body(_read_body(args.body_file), args.agent,
                            _require_session(environ)))
            return 0
        return post(args, environ)
    except Unsigned as refused:
        print("pr_post: not posted, cannot sign: %s" % refused, file=sys.stderr)
        return EXIT_UNSIGNED
    except (UsageError, OSError) as refused:
        print("pr_post: not posted: %s" % refused, file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
