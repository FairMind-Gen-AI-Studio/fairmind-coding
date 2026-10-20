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
* The server is stateless. An `Mcp-Session-Id` is echoed when `initialize`
  issues one and is never required.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request

__all__ = ["BrainMcpError", "call_tool", "plugin_version"]

#: Largest response body read, in bytes. A body past it is an error.
_MAX_RESPONSE_BYTES = 262144

_PROTOCOL_VERSION = "2025-06-18"
_READ_CHUNK = 65536


class BrainMcpError(Exception):
    """Any failure of the exchange. The message is a short reason only."""


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


def _post(url, headers, body, deadline, *, want_id=None):
    """One POST. Returns (response headers, parsed JSON-RPC object or None)."""
    timeout = _remaining(deadline)
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST")
    try:
        resp = _OPENER.open(req, timeout=timeout)
    except BrainMcpError:
        raise
    except urllib.error.HTTPError as err:
        try:
            err.close()
        except Exception:  # noqa: BLE001
            pass
        raise BrainMcpError(f"http status {err.code}") from None
    except Exception:  # noqa: BLE001 - URLError, OSError, HTTPException, ValueError
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


def _result_of(message):
    if "error" in message:
        raise BrainMcpError("rpc error")
    result = message.get("result")
    if not isinstance(result, dict):
        raise BrainMcpError("malformed result")
    return result


def _exchange(server, name, arguments, deadline):
    url = server["url"]
    base = dict(server.get("headers") or {})
    base.update({"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})

    hdrs, init = _post(url, base, {
        "jsonrpc": "2.0", "id": 1, "method": "initialize",
        "params": {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {},
                   "clientInfo": {"name": "fairmind-coding",
                                  "version": plugin_version() or "unknown"}}},
        deadline, want_id=1)
    init_result = _result_of(init)

    follow = dict(base)
    session = hdrs.get("Mcp-Session-Id")
    if session:
        follow["Mcp-Session-Id"] = session
    protocol = init_result.get("protocolVersion")
    if isinstance(protocol, str) and protocol:
        follow["MCP-Protocol-Version"] = protocol

    _post(url, follow, {"jsonrpc": "2.0", "method": "notifications/initialized"},
          deadline)
    _, reply = _post(url, follow, {
        "jsonrpc": "2.0", "id": 2, "method": "tools/call",
        "params": {"name": name, "arguments": arguments}},
        deadline, want_id=2)
    result = _result_of(reply)
    if result.get("isError"):
        raise BrainMcpError("tool error")
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


def call_tool(server, name, arguments, *, deadline):
    """Call MCP tool `name` on `server` and return its result as a dict.

    `server` is `{"url": str, "headers": {str: str}}`; `deadline` a
    `time.monotonic()` instant. Raises `BrainMcpError` on every failure,
    including the deadline."""
    _remaining(deadline)
    box = {}

    def work():
        try:
            box["value"] = _exchange(server, name, arguments, deadline)
        except BrainMcpError as err:
            box["error"] = err
        except Exception:  # noqa: BLE001
            box["error"] = BrainMcpError("failed")

    worker = threading.Thread(target=work, daemon=True)
    worker.start()
    worker.join(max(0.0, deadline - time.monotonic()))
    if worker.is_alive():
        raise BrainMcpError("deadline")
    if "error" in box:
        raise box["error"]
    return box["value"]
