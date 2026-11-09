"""A stdlib Streamable-HTTP MCP client for one tool call.

Hooks cannot ask the harness to call an MCP tool, and they run on a clock the
person is waiting on. This module performs the whole exchange itself
(`initialize`, `notifications/initialized`, `tools/call`) against the address
and credential of the project's Fairmind MCP entry, inside a deadline the caller
owns.

Rules that hold the shape of the file:

* Stdlib only, and it imports no other plugin module. A hook path pays for every
  import, and the delivery lanes' modules are far heavier than one request.
* The credential travels only to the host the entry named. A redirect raises
  instead of being followed: urllib's default handler rebuilds a redirected POST
  with every header, `Authorization` included.
* Nothing in an exception carries a header value or a response body.
* The deadline is a `time.monotonic()` instant and is checked immediately before
  EACH of the three requests, inside the worker thread. A caller that has given
  up on a stalled reply must not have its worker send `tools/call` afterwards: a
  server that records work on receipt would record it for a question the caller
  then asks the person.
* The server may be stateless: an `Mcp-Session-Id` is echoed when `initialize`
  issues one and is never required.
* A caller that makes many door calls to one server entry owns a `DoorSession`
  and passes it to `call_door`: it holds what the handshake negotiated (the
  session id and protocol version, never a credential), so the run pays for one
  handshake. Any failure of a call clears it, and a call the caller gave up on
  either never dispatches again or is reported as sent; it never stores into or
  clears the session afterwards. `call_tool` takes no session and handshakes
  every time.

Two public entry points share the exchange:

* `call_tool` is the hooks' door: one dict back, every failure a
  `BrainMcpError`, the server's words never in the exception. It is unchanged.
* `call_door` is for a caller that must act on the door's own answer (the seed
  sender). It returns a `DoorAnswer` for a tool result, an `isError` one
  included, and raises `BrainTransportError` for everything else. This is the
  one relaxation of the rules above: a tool-error TEXT is returned to the
  caller (bounded, never an exception message), scrubbed of the entry's
  credential in every form it could come back in. A transport error still
  carries only classified facts: the HTTP status, whether the hosting edge's
  HTML page (blocked or any other), a plain-text `upstream connect error` or a
  403 naming the write scope answered, whether `tools/call` may have reached
  the server (`sent`: the outcome is then unknown; a refused connection or a
  DNS failure provably sent nothing), and whether the deadline ended it.
  Classification reads at most `_ERROR_PREFIX_BYTES` of an error body and keeps
  none of it.
"""

from __future__ import annotations

import base64
import json
import os
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from collections import namedtuple
from urllib.parse import parse_qsl, unquote, urlsplit

__all__ = ["BrainMcpError", "BrainToolError", "BrainTransportError", "DoorAnswer",
           "DoorSession", "call_door", "call_tool", "plugin_version"]

#: Largest response body read, in bytes. A body past it is an error.
_MAX_RESPONSE_BYTES = 262144

#: Largest prefix of an error body read to classify it, in bytes.
_ERROR_PREFIX_BYTES = 8192
#: Largest tool-error text `call_door` hands back, in characters.
_TEXT_LIMIT = 16384

_PROTOCOL_VERSION = "2025-06-18"
_PROTOCOL_SHAPE = re.compile(r"\d{4}-\d{2}-\d{2}")
_READ_CHUNK = 65536


class BrainMcpError(Exception):
    """Any failure of the exchange. The message is a short reason only."""


class BrainToolError(BrainMcpError):
    """The server answered the call with an MCP error result (`isError`).

    A caller that must tell "the server refused the call" from "the server did
    not answer" catches this class first; every `except BrainMcpError` still
    catches it. Keep it a separate class rather than a message: the message of a
    `BrainMcpError` is a short reason only and is never compared."""


class BrainTransportError(BrainMcpError):
    """The exchange failed before the door answered with a tool result.

    Carries classified facts only, never a body or a header:

    * `edge_blocked`: the hosting edge's HTML error page with 403 and
      "Request blocked".
    * `write_scope`: a 403 whose non-HTML body names the missing write scope.
    * `sent`: `tools/call` may have reached the server, so the outcome is unknown
      and the caller must look again before it sends anything."""

    def __init__(self, reason, *, edge_blocked=False, write_scope=False, sent=False):
        super().__init__(reason)
        self.edge_blocked = edge_blocked
        self.write_scope = write_scope
        self.sent = sent


#: What `call_door` returns: `is_error` (the result carried `isError`), `data`
#: (the result's `structuredContent`, else its text parsed as a JSON object,
#: else None) and `text` (the error text, bounded; None for a success).
DoorAnswer = namedtuple("DoorAnswer", "is_error data text")


class _RefuseRedirects(urllib.request.HTTPRedirectHandler):
    """A redirect raises, so the credential never leaves the configured host."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise BrainMcpError("redirect refused")


#: The opener every request goes through. A module-level name so a test can
#: reach the one place the network is touched.
_OPENER = urllib.request.build_opener(_RefuseRedirects)


def plugin_version():
    """The sibling `../.claude-plugin/plugin.json` version, or None. Never raises."""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(here, "..", ".claude-plugin", "plugin.json"),
                  encoding="utf-8") as fh:
            version = json.load(fh).get("version")
    except Exception:  # noqa: BLE001 - a version is never worth a raised call
        return None
    return version if isinstance(version, str) and version else None


def _remaining(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise BrainMcpError("deadline")
    return remaining


def _read_body(resp, deadline, want_id):
    """The JSON-RPC object this request is waiting for.

    A JSON body is read to its end. An event stream is parsed as it arrives and
    the call returns the moment the object with `want_id` is complete, so a
    server that keeps the stream open is not waited on."""
    ctype = ""
    try:
        ctype = resp.headers.get_content_type()
    except Exception:  # noqa: BLE001
        pass
    streaming = ctype == "text/event-stream"
    total = 0
    buf = b""
    while True:
        _remaining(deadline)
        chunk = resp.read1(_READ_CHUNK)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_RESPONSE_BYTES:
            raise BrainMcpError("response too large")
        buf += chunk
        if streaming:
            found = _take_event(buf, want_id)
            if found is not None:
                return found
            # Keep only the unterminated tail; complete events were inspected.
            cut = max(buf.rfind(b"\n\n"), buf.rfind(b"\r\n\r\n"))
            if cut >= 0:
                buf = buf[cut:]
    if streaming:
        found = _take_event(buf + b"\n\n", want_id)
        if found is None:
            raise BrainMcpError("no matching reply")
        return found
    try:
        obj = json.loads(buf.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise BrainMcpError("unparseable body") from None
    if not isinstance(obj, dict):
        raise BrainMcpError("unparseable body")
    return obj


def _take_event(buf, want_id):
    """The first complete SSE event in `buf` whose JSON object has `want_id`."""
    text = buf.decode("utf-8", "replace").replace("\r\n", "\n")
    # The last piece is unterminated, or empty when the buffer ended on a blank
    # line; either way it is not a complete event.
    for raw in text.split("\n\n")[:-1]:
        data = [ln[5:].lstrip(" ") for ln in raw.split("\n") if ln.startswith("data:")]
        if not data:
            continue
        try:
            obj = json.loads("\n".join(data))
        except ValueError:
            continue
        if isinstance(obj, dict) and obj.get("id") == want_id:
            return obj
    return None


def _classify_error(err, facts):
    """Record on `facts` whether an error response was the edge's block page
    or a refusal naming the write scope, from at most a bounded prefix of its
    body. Nothing of the body is kept."""
    try:
        prefix = err.read(_ERROR_PREFIX_BYTES)
    except Exception:  # noqa: BLE001
        return
    try:
        ctype = err.headers.get_content_type()
    except Exception:  # noqa: BLE001
        ctype = ""
    text = prefix.decode("utf-8", "replace").lower()
    html = "html" in ctype or "<html" in text or "<!doctype html" in text
    facts["edge_blocked"] = html and err.code == 403 and "request blocked" in text
    facts["write_scope"] = (not html) and err.code == 403 and "write scope" in text


def _never_left(exc):
    """True when `exc` provably happened before any byte of the request left:
    a refused connection or a DNS failure. A reset, a timeout or a TLS error
    is not provable (the error may come from a write after bytes left) and
    stays unknown."""
    reason = exc.reason if isinstance(exc, urllib.error.URLError) else None
    return isinstance(reason, (ConnectionRefusedError, socket.gaierror))


def _post(url, headers, body, deadline, *, want_id=None, facts=None, gate=None):
    """One POST. Returns (response headers, parsed JSON-RPC object or None).

    `facts` (a dict) receives the classification of an HTTP error response and
    `gate` is called right before the request leaves (it raises to stop it);
    both are for `call_door` and change nothing for `call_tool`."""
    timeout = _remaining(deadline)
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    if gate is not None:
        gate()
    try:
        resp = _OPENER.open(req, timeout=timeout)
    except BrainMcpError:
        raise
    except urllib.error.HTTPError as err:
        if facts is not None:
            _classify_error(err, facts)
        try:
            err.close()
        except Exception:  # noqa: BLE001
            pass
        raise BrainMcpError(f"http status {err.code}") from None
    except Exception as exc:  # noqa: BLE001 - URLError, OSError, HTTPException, ValueError
        if facts is not None and _never_left(exc):
            facts["sent"] = False
        raise BrainMcpError("transport failure") from None
    try:
        status = getattr(resp, "status", 200)
        if not 200 <= status < 300:
            raise BrainMcpError(f"http status {status}")
        if want_id is None:
            return resp.headers, None
        try:
            return resp.headers, _read_body(resp, deadline, want_id)
        except BrainMcpError:
            raise
        except Exception:  # noqa: BLE001
            raise BrainMcpError("transport failure") from None
    finally:
        try:
            resp.close()
        except Exception:  # noqa: BLE001
            pass


def _result_of(message, door=False):
    # The door path reads `"error": null` as no error; the hooks' path keeps
    # treating any `error` key as one.
    errored = message.get("error") is not None if door else "error" in message
    if errored:
        raise BrainMcpError("rpc error")
    result = message.get("result")
    if not isinstance(result, dict):
        raise BrainMcpError("malformed result")
    return result


#: The headers a handshake adds for the requests that follow it.
_SESSION_ID = "Mcp-Session-Id"
_PROTOCOL = "MCP-Protocol-Version"


def _follow_headers(server, extras):
    """The headers of every request after `initialize`: the entry's own, the
    JSON-RPC ones and what the handshake negotiated."""
    headers = dict(server.get("headers") or {})
    headers.update({"Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream"})
    headers.update(extras)
    return headers


def _handshake(server, deadline, call=None):
    """`initialize` and `notifications/initialized`; returns what they
    negotiated (the session and protocol headers), for `_follow_headers`.
    `call` (a `_DoorCall`) is the door path's gate and fact sink."""
    url = server["url"]
    facts, gate = (call.facts, call.gate) if call else (None, None)

    hdrs, init = _post(url, _follow_headers(server, {}), {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
                   "clientInfo": {"name": "fairmind-coding",
                                  "version": plugin_version() or "unknown"}}},
        deadline, want_id=1, facts=facts, gate=gate)
    init_result = _result_of(init, call is not None)

    extras = {}
    session = hdrs.get(_SESSION_ID)
    if session:
        extras[_SESSION_ID] = session
    protocol = init_result.get("protocolVersion")
    # A door session keeps only a version shaped like one (MCP versions are
    # dates), so a server that echoes anything else never has it stored.
    if isinstance(protocol, str) and protocol and (
            call is None or _PROTOCOL_SHAPE.fullmatch(protocol)):
        extras[_PROTOCOL] = protocol

    _post(url, _follow_headers(server, extras),
          {"jsonrpc": "2.0", "method": "notifications/initialized"},
          deadline, facts=facts, gate=gate)
    return extras


def _tools_call(server, extras, name, arguments, deadline, call=None):
    facts = gate = None
    if call:
        facts, gate = call.facts, lambda: call.gate(tool=True)
    _, reply = _post(server["url"], _follow_headers(server, extras), {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}},
        deadline, want_id=2, facts=facts, gate=gate)
    return _result_of(reply, call is not None)


def _exchange(server, name, arguments, deadline):
    extras = _handshake(server, deadline)
    result = _tools_call(server, extras, name, arguments, deadline)
    if result.get("isError"):
        raise BrainToolError("tool error")
    structured = result.get("structuredContent")
    if isinstance(structured, dict):
        return structured
    try:
        content = result.get("content")
        parsed = json.loads(content[0]["text"])
    except (TypeError, KeyError, IndexError, ValueError):
        raise BrainMcpError("malformed result") from None
    if not isinstance(parsed, dict):
        raise BrainMcpError("malformed result")
    return parsed


def _texts(result):
    content = result.get("content")
    if not isinstance(content, list):
        return ""
    return "\n".join(b["text"] for b in content
                     if isinstance(b, dict) and isinstance(b.get("text"), str))


def _json_object(text):
    try:
        parsed = json.loads(text)
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


REDACTED = "[redacted]"


def _secrets_of(server):
    """Everything in the server entry a reply must never carry back: every
    header value, the token after a scheme word, the decoded user and password
    of a Basic login, the address's userinfo and its query values, each also in
    the forms Python's own messages give a string (`repr`, and `repr` of its
    bytes). Four characters at least: a shorter secret would mangle every
    message."""
    found = set()
    for value in (server.get("headers") or {}).values():
        if not isinstance(value, str):
            continue
        found.add(value.strip())
        match = re.match(r"^\s*(bearer|basic|token)\s+(\S.*)$", value, re.IGNORECASE)
        if match:
            found.add(match.group(2).strip())
            if match.group(1).lower() == "basic":
                try:
                    login = base64.b64decode(match.group(2).strip()).decode("utf-8", "replace")
                except ValueError:
                    continue
                found.add(login)
                found.update(part for part in login.partition(":")[::2] if part)
    try:
        parts = urlsplit(server["url"])
        for value in (parts.username, parts.password):
            if value:
                found.update((value, unquote(value)))
        found.update(value for _, value in parse_qsl(parts.query))
    except (KeyError, ValueError):
        pass
    forms = set()
    for secret in found:
        if len(secret) >= 4:
            forms.update((secret, repr(secret)[1:-1], repr(secret.encode("utf-8"))[2:-1]))
    return sorted((f for f in forms if len(f) >= 4), key=len, reverse=True)


def _scrub(value, secrets):
    if isinstance(value, str):
        for secret in secrets:
            value = value.replace(secret, REDACTED)
        return value
    if isinstance(value, dict):
        return {_scrub(k, secrets): _scrub(v, secrets) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v, secrets) for v in value]
    return value


class DoorSession:
    """What one handshake negotiated, kept by a caller that makes many
    `call_door` calls to ONE server entry (the headers it builds are that
    entry's). It holds the session id and protocol version only, never a
    credential. Pass it as `call_door(..., session=...)`; without one every
    call handshakes."""

    def __init__(self):
        self.extras = None


_FRESH_FACTS = {"edge_blocked": False, "write_scope": False, "sent": False}


class _DoorCall:
    """One `call_door`: the facts it reports, the caller's session and whether
    its worker is still wanted. Every dispatch, every store and the caller's
    giving up go through the one lock, so either the worker never dispatches
    after the caller gave up or the caller's snapshot says the call was sent."""

    def __init__(self, session):
        self.session = session
        self.live = True
        self.facts = dict(_FRESH_FACTS)
        self._lock = threading.Lock()

    def gate(self, tool=False):
        """Called right before a request leaves; raises once the caller gave
        up. For `tools/call` it records `sent` in the same critical section."""
        with self._lock:
            if not self.live:
                raise BrainMcpError("deadline")
            if tool:
                self.facts["sent"] = True

    def store(self, extras):
        with self._lock:
            if self.live:
                self.session.extras = extras

    def abandon(self):
        """The caller is done with this call: clear the session and return a
        snapshot of the facts."""
        with self._lock:
            self.live = False
            self.session.extras = None
            return dict(self.facts)


def _door_exchange(server, name, arguments, deadline, call):
    extras = call.session.extras
    if extras is None:
        extras = _handshake(server, deadline, call)
        call.store(extras)
    result = _tools_call(server, extras, name, arguments, deadline, call)
    text = _texts(result)
    structured = result.get("structuredContent")
    data = structured if isinstance(structured, dict) else _json_object(text)
    secrets = _secrets_of(server)
    if result.get("isError"):
        return DoorAnswer(True, _scrub(data, secrets), _scrub(text[:_TEXT_LIMIT], secrets))
    if data is None:
        raise BrainMcpError("malformed result")
    return DoorAnswer(False, _scrub(data, secrets), None)


def _within(deadline, work):
    """`work()` run in a daemon thread and its result returned. A
    `BrainMcpError` it raises is raised again, any other exception becomes
    `BrainMcpError("failed")`, and the deadline passing first raises
    `BrainMcpError("deadline")`."""
    _remaining(deadline)
    box = {}

    def run():
        try:
            box["value"] = work()
        except BrainMcpError as err:
            box["error"] = err
        except Exception:  # noqa: BLE001
            box["error"] = BrainMcpError("failed")

    worker = threading.Thread(target=run, daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()))
    if worker.is_alive():
        raise BrainMcpError("deadline")
    if "error" in box:
        raise box["error"]
    return box["value"]


def call_tool(server, name, arguments, *, deadline):
    """Call MCP tool `name` on `server` and return its result as a dict.

    `server` is `{"url": str, "headers": {str: str}}`; `deadline` a
    `time.monotonic()` instant. Raises `BrainMcpError` on every failure,
    including the deadline."""
    return _within(deadline, lambda: _exchange(server, name, arguments, deadline))


def call_door(server, name, arguments, *, deadline, session=None):
    """Call MCP tool `name` on `server` and return a `DoorAnswer`.

    `server` and `deadline` are as for `call_tool`. A tool result is returned,
    an `isError` one included (`is_error`, its text, its parsed object when it
    is one). Every other failure raises `BrainTransportError`, which says only
    what kind of failure it was and whether the call may have reached the server.

    With a `DoorSession` the handshake is made once and reused by the calls
    that follow; any failure of a call clears it, and there is no retry: a call
    that reached `tools/call` is `sent` whatever the answer was. Without one
    every call handshakes."""
    call = _DoorCall(session or DoorSession())
    try:
        return _within(deadline, lambda: _door_exchange(server, name, arguments, deadline, call))
    except BrainMcpError as err:
        raise BrainTransportError(str(err), **call.abandon()) from None
