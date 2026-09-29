#!/usr/bin/env python3
"""
audit_run_meta.py — run-identity capture for a `/harness-audit` run.

The audit engine (`harness_audit.py`) knows nothing about repo identity — its
`summary.json` is pure criteria/pillar/dimension data, on purpose (T6/T7:
identity is not a criterion). This script fills that gap: it writes a small
`run-meta.json` alongside the engine's own output, carrying exactly the four
fields the Agentic Insights flush needs to attribute a summary to a repo and
a moment in time:

  repo_name   — basename of the git toplevel (not the raw --repo argument,
                which may be a subdirectory or a relative path)
  git_remote  — the `origin` remote, normalized (see `normalize_git_remote`
                below), or JSON null when the repo has no `origin`
  commit_sha  — `git rev-parse HEAD`
  executed_at — ISO-8601 UTC, second precision, tz-aware

Fail-closed on a non-git `--repo`: nothing is written (not even a partial or
temp file) and the process exits 1 with a message on stderr — a caller that
does not check the exit code must not be able to mistake "no meta" for "meta
with empty fields".

The write itself is atomic (`tempfile.mkstemp` in the `--out` directory, then
a single `os.replace`), mirroring `loop_ledger.py`'s `_write_rows`: a reader
of `run-meta.json` must never observe a half-written file, and a crash mid-
write must never leave a stray temp file behind.
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

# PCF-28: one writer for the consumer-repo ignore entry, asked by every site
# that can create `.fairmind/`. A stdlib-only LEAF module — importing it
# costs nothing else — reached the way `_loop_ledger` and `ambient_digest`
# already make their own dir importable.
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
from _fm_ignore import makedirs_ignored  # noqa: E402

DEFAULT_OUT = os.path.join(".fairmind", "audit", "run-meta.json")

# scp-like shorthand, e.g. `git@GitHub.com:Org/Repo.git` — no `scheme://`,
# an optional `user@`, then `host:path`. Deliberately only matched when the
# URL has no `://` at all (an ssh:// URL also contains a `:` before the host
# but is handled by its own branch below).
_SCP_LIKE_RE = re.compile(r"^(?:[^@/]+@)?([^:/]+):(.+)$")


def normalize_git_remote(url):
    """Normalize a git remote URL to a canonical `https://host/path` form.

    Pinned behavior (see the CLI docstring / work package for the full
    table): strips embedded credentials (`user[:pass]@`), converts scp-like
    (`git@host:owner/repo`) and `ssh://` remotes to `https://`, lowercases
    the HOST only (path case is preserved — GitHub org/repo names are
    case-sensitive on disk even though the host is not), strips a single
    trailing `.git`, and never leaves a trailing slash. `None`/empty input
    maps to `None` (the "no remote" case).
    """
    url = (url or "").strip()
    if not url:
        return None

    if "://" not in url:
        m = _SCP_LIKE_RE.match(url)
        if m:
            host, path = m.groups()
            url = f"https://{host}/{path}"
        else:
            # Not scp-like and not a recognized scheme — treat as a bare
            # host/path and prefix a scheme so urlsplit can parse it.
            url = f"https://{url}"

    parts = urlsplit(url)
    netloc = parts.netloc
    if "@" in netloc:
        netloc = netloc.rsplit("@", 1)[1]  # strip user[:pass]@
    netloc = netloc.lower()  # host is case-insensitive; a git remote's port is numeric, so lower() leaves it intact

    path = parts.path.rstrip("/")
    if path.endswith(".git"):
        path = path[: -len(".git")]
    path = path.rstrip("/")

    return urlunsplit(("https", netloc, path, "", ""))


def _run_git(repo, *args):
    """Run `git -C repo <args>`, returning the completed process. Never
    raises on a non-zero exit — callers decide what that means."""
    return subprocess.run(
        ["git", "-C", repo] + list(args),
        capture_output=True, text=True,
    )


def _iso_now():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _atomic_write_json(out_path, data):
    """Write `data` as JSON to `out_path` atomically: mkstemp in the same
    directory, write, `os.replace` onto the target. The parent directory is
    created if missing. On any error the temp file is removed and the
    exception re-raised — never a partial target file."""
    directory = os.path.dirname(os.path.abspath(out_path))
    makedirs_ignored(directory)
    fd, tmp = tempfile.mkstemp(prefix=".run-meta.", suffix=".tmp", dir=directory)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=2, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, out_path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


def collect_run_meta(repo):
    """Gather the four contract fields for `repo`. Raises RuntimeError with a
    human-readable message if `repo` is not a git work tree, or if HEAD
    cannot be resolved (e.g. a repo with zero commits) — both are fail-closed
    conditions, never partially reported."""
    toplevel_proc = _run_git(repo, "rev-parse", "--show-toplevel")
    if toplevel_proc.returncode != 0:
        raise RuntimeError(
            f"--repo {repo!r} is not a git work tree: {toplevel_proc.stderr.strip()}"
        )
    toplevel = toplevel_proc.stdout.strip()
    repo_name = os.path.basename(os.path.normpath(toplevel))

    sha_proc = _run_git(repo, "rev-parse", "HEAD")
    if sha_proc.returncode != 0:
        raise RuntimeError(
            f"cannot resolve HEAD in {repo!r}: {sha_proc.stderr.strip()}"
        )
    commit_sha = sha_proc.stdout.strip()

    remote_proc = _run_git(repo, "remote", "get-url", "origin")
    git_remote = normalize_git_remote(remote_proc.stdout.strip()) \
        if remote_proc.returncode == 0 else None

    return {
        "repo_name": repo_name,
        "git_remote": git_remote,
        "commit_sha": commit_sha,
        "executed_at": _iso_now(),
    }


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Capture run-identity metadata (repo, remote, commit, "
                    "timestamp) for a /harness-audit run."
    )
    parser.add_argument("--repo", default=".", help="path to the git repo (default: '.')")
    parser.add_argument("--out", default=DEFAULT_OUT,
                         help=f"output JSON path (default: {DEFAULT_OUT})")
    args = parser.parse_args(argv)

    try:
        data = collect_run_meta(args.repo)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    _atomic_write_json(args.out, data)
    return 0


if __name__ == "__main__":
    sys.exit(main())
