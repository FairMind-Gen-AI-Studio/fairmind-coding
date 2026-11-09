#!/usr/bin/env python3
"""
artifact_cards.py - decide when a working artifact needs a reference card in the
company brain, and build the exact arguments for filing it.

A working artifact is a document an agent produced while working (a design note,
a report, a plan, an HTML page) and kept in the repository, by default in
the `artifacts` folder under `docs`. The brain holds a reference card per artifact: a summary the
agent writes plus where the file lives (repository, path, commit, branch). This
script is the whole decision logic and is deliberately independent of any coding
agent: it takes plain arguments (and JSON on stdin), prints JSON on stdout, and
needs only `python3` and `git` (`gh` is optional, for the repository's
visibility). A hook, a skill procedure or a person at a terminal call the same
subcommands.

Subcommands
  config      The effective artifacts path list.
  status      One file: is it an artifact, tracked, filed, changed since filing.
  gate        One file about to be written: must the person be asked first
              (the repository is public, or its visibility cannot be told, or
              the answer cannot be worked out). Any answer other than `pass`
              means ask.
  push-gate   The same question for a push that has not happened yet: do the
              outgoing commits add or change artifacts in a public (or unknown)
              repository. `--command LINE --cwd DIR` answers for a shell line an
              agent is about to run: a literal `git push` it reads inside a tiny
              grammar is measured, and the rest of the line passes unchecked (a
              push the line does not spell out is the git hook's to see).
              `--shell` prints the reason alone, exit 3 on anything but a pass.
              A courtesy gate, not a security boundary.
  install-git-hook  Install a git pre-push hook that checks the URL and the ref
              updates git itself hands it, for any agent and for pushes made from
              a terminal. The hook runs a copy of this script kept in the git
              directory; --refresh keeps an installed copy in step with the plugin,
              --status reports without writing. Where another tool owns the hooks
              it writes none and prints the one line to add to theirs.
  pre-push    What that hook runs: the remote's name and URL as arguments, git's
              ref updates on stdin; every update is measured and the person is
              asked once. Exit 0 pushes, 3 refuses.
  visibility  The repository's visibility as the forge reports it.
  push-diff   What a push carried: new, changed, renamed and gone artifacts,
              against the local state file, with the commit and branch to name.
  args        Build the Brain_tell arguments for one entry (check, write or
              path-gone form). The agent supplies the title and the summary.
  blueprint   Build the report that files one checkout file as a linked document,
              and the related-document item that names the same card from a
              component report. The link names the last commit that changed the
              file on a branch of origin. Refuses (exit 3, the reason on stderr)
              a file that is untracked, edited since that commit, not on origin,
              or whose origin gives no link.
  record      Mark entries as filed in the local state file (`--declined`
              marks a new, changed or renamed entry as not to be filed, until its
              blob changes; a deletion whose card is still live is reported again
              by the next push-diff against the default branch). A renamed
              entry carrying `"retirement_pending": true` (the platform could not
              archive the old card) keeps the old path's row, so the next push-diff
              reports the same rename again, marked `"resend": true`: that entry is
              filed with `args` (no --mode) and never checked first.
  state       How many artifacts the state file says were filed.

Local files (under the repository's `.fairmind/artifacts/`)
  config.json  {"paths": ["some/dir/"]}   optional, local to the checkout it sits in (not committed)
The repository may also commit `.fairmind-artifacts.json` at its root, with the same
shape: it applies to every clone, and a local config.json overrides it. A linked worktree
reads both files in its own tree and in its main checkout, in this order: its own local file,
the main checkout's local file, its own committed file, the main checkout's committed file.
  filed.json   {"schema": "fm-artifacts-filed/2", "files": {key: {...}}}
               key is "owner/name<TAB>path" for a row filed for a repository, or the bare
               path for a row that names none; a file of schema /1 (bare paths) is still read.
The forge's visibility answer is kept for a few minutes in the user cache directory
(`$XDG_CACHE_HOME` or `~/.cache`, under `fairmind-coding/`), never in the repository.

Exit codes: 0 answered, 2 refused (the JSON on stdout carries `error`), 3 a blueprint
that cannot be linked (and `--shell`'s refusal).
Stdlib only. Nothing here talks to the network except an optional `gh` call and, when a
remote-tracking HEAD is unset, one `git ls-remote --symref <remote> HEAD`.
"""

import argparse
import filecmp
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
import _artifact_worktree  # noqa: E402  (stdlib-only leaf shared with the hook adapter)
from _artifact_worktree import linked_worktree_root  # noqa: E402

STATE_SCHEMA = "fm-artifacts-filed/2"
READABLE_SCHEMAS = ("fm-artifacts-filed/1", STATE_SCHEMA)
DEFAULT_PATHS = ["/".join(("docs", "artifacts")) + "/"]
STATE_DIR = _artifact_worktree.STATE_DIR
COMMITTED_CONFIG = _artifact_worktree.COMMITTED_CONFIG
SUMMARY_MAX = 1000
GIT_TIMEOUT_S = 30
GH_TIMEOUT_S = 8
# One budget for every remote together, under the Claude Code adapter's own limit, so
# that a slow forge turns into an `unknown` (an ask) here and never into silence there.
VISIBILITY_DEADLINE_S = 10
VISIBILITY_TTL_S = 300

# An artifact card is a reference: the file's text never rides in its arguments.
CONTENT_KEYS = ("content", "uri", "document_id")
# A card does not say what it is evidence for or what kind of document it is.
RETIRED_KEYS = ("doc_kind", "evidences")

VISIBILITY_MAP = {"public": "public", "private": "private", "internal": "restricted"}
# Most exposed first: when several remotes are candidates, the first word present wins.
EXPOSURE_ORDER = ("public", "unknown", "internal", "private")


class CliError(Exception):
    """A refusal the caller can read: printed as JSON, exit code 2."""


# --------------------------------------------------------------------------- git

def _run(argv, cwd, timeout=GIT_TIMEOUT_S, stdin=None, env=None):
    try:
        done = subprocess.run(argv, cwd=cwd, capture_output=True, text=True,
                              timeout=timeout, input=stdin, env=env)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return done.returncode, done.stdout, done.stderr


def _git(cwd, *args):
    return _run(["git", "-c", "core.quotepath=off", *args], cwd)


def toplevel(cwd):
    rc, out, _ = _git(cwd, "rev-parse", "--show-toplevel")
    if rc != 0 or not out.strip():
        raise CliError("not inside a git repository: %s" % cwd)
    return os.path.realpath(out.strip())


def _git_config(top, key):
    rc, out, _ = _git(top, "config", "--get", key)
    return out.strip() if rc == 0 else ""


def current_branch(top):
    rc, out, _ = _git(top, "symbolic-ref", "--short", "-q", "HEAD")
    return out.strip() if rc == 0 else ""


def _remotes(top):
    rc, out, _ = _git(top, "remote")
    return [r for r in out.split() if r] if rc == 0 else []


def _push_remote_name(top, branch):
    """The remote git itself would push `branch` to, in the order git-config(1) gives
    for pushes: `branch.<name>.pushRemote`, then `remote.pushDefault`, then
    `branch.<name>.remote`. "" when none of them is set."""
    candidates = []
    if branch:
        candidates.append(_git_config(top, "branch.%s.pushRemote" % branch))
    candidates.append(_git_config(top, "remote.pushDefault"))
    if branch:
        candidates.append(_git_config(top, "branch.%s.remote" % branch))
    for name in candidates:
        if name and name != ".":
            return name
    return ""


def remote_for(top, branch):
    """(name, fetch url) of the remote a branch pushes to: the configured one, else
    `origin`, else the first remote there is. ("", "") when there is none."""
    remotes = _remotes(top)
    name = _push_remote_name(top, branch)
    if name and name not in remotes:
        return name, name  # git pushes to the address it was given
    if name not in remotes:
        name = "origin" if "origin" in remotes else (remotes[0] if remotes else "")
    return name, (_git_config(top, "remote.%s.url" % name) if name else "")


def _push_url(top, name):
    """The address a push to remote `name` goes to: its push URL, else its URL."""
    if not name:
        return ""
    return _git_config(top, "remote.%s.pushurl" % name) or _git_config(top, "remote.%s.url" % name)


def _publish_targets(top, branch, only=None):
    """[(remote name, url)] a push from this branch could publish to: the remote
    the push names, else the one git would use (`_push_remote_name`), else every
    remote, because guessing `origin` would pass a fork setup whose only remote has
    another name."""
    remotes = _remotes(top)
    only = only or _push_remote_name(top, branch)
    if only:
        if only not in remotes:
            return [(only, only)]  # an address, not a configured remote: git pushes to it as is
        names = [only]
    else:
        names = remotes
    return [(n, _push_url(top, n)) for n in names]


_URL_RES = (
    re.compile(r"^(?:https?|ssh|git)://(?:[^@/]+@)?(?P<host>[^/:]+)(?::\d+)?/(?P<path>.+?)/?$"),
    re.compile(r"^(?:[^@/]+@)?(?P<host>[^/:]+):(?P<path>[^/].*?)/?$"),
)


def parse_remote(url):
    """(host, 'owner/name') from an https, ssh or scp-style remote, else None.
    Credentials in the URL are dropped, never carried."""
    for rx in _URL_RES:
        m = rx.match(url.strip())
        if m:
            path = m.group("path")
            if path.endswith(".git"):
                path = path[:-4]
            if path:
                return m.group("host"), path
    return None


# ----------------------------------------------------------------------- config

def normalize_prefix(value):
    if not isinstance(value, str) or not value.strip():
        raise CliError("an artifacts path must be a non-empty string")
    p = value.strip().replace("\\", "/")
    parts = [s for s in p.split("/") if s not in ("", ".")]
    if p.startswith("/") or ".." in parts or not parts:
        raise CliError("an artifacts path must be a relative directory inside the "
                       "repository: %r" % value)
    return "/".join(parts) + "/"


def _read_paths(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        paths = data["paths"]
        if not isinstance(paths, list) or not paths:
            raise ValueError("paths must be a non-empty list")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CliError("%s is not a valid artifacts config: %s" % (path, exc))
    return [normalize_prefix(p) for p in paths]


def load_config(top, override=None):
    """The artifacts paths: the command line, else this checkout's own
    `.fairmind/artifacts/config.json`, else (a linked worktree only) the main checkout's,
    else the repository's committed `.fairmind-artifacts.json` (the worktree's, then the
    main checkout's), else the default. The first one present wins; they are not merged."""
    if override:
        return [normalize_prefix(p) for p in override]
    for path in _artifact_worktree.config_files(top):
        if os.path.isfile(path):
            return _read_paths(path)
    return list(DEFAULT_PATHS)


def rel_to_repo(top, file_path):
    """A repo-relative POSIX path for `file_path`, or None when it is outside."""
    p = file_path if os.path.isabs(file_path) else os.path.join(top, file_path)
    # Resolve the directory part only: the file itself may not exist yet.
    head, tail = os.path.split(os.path.normpath(p))
    real = os.path.join(os.path.realpath(head), tail)
    rel = os.path.relpath(real, top)
    if rel == ".." or rel.startswith(".." + os.sep):
        return None
    return rel.replace(os.sep, "/")


def is_artifact(rel, prefixes):
    return rel is not None and any(rel.startswith(p) for p in prefixes)


# ------------------------------------------------------------------------ state

def state_path(top):
    return os.path.join(top, STATE_DIR, "filed.json")


def load_state(top):
    path = state_path(top)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        files = data["files"]
        if data.get("schema") not in READABLE_SCHEMAS or not isinstance(files, dict):
            raise ValueError("unexpected schema")
        for k, v in files.items():
            if not isinstance(v, dict) or not isinstance(v.get("source_version"), str):
                raise ValueError("bad row for %r" % k)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CliError("%s is not a valid state file: %s" % (path, exc))
    return files


def save_state(top, files):
    path = state_path(top)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".filed-", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"schema": STATE_SCHEMA, "files": files}, fh, indent=2,
                      sort_keys=True, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


_ROW_SEP = "\t"


def state_key(repository, path):
    """The state key of a path filed for a repository: a document's identity on the
    platform is its repository plus its path, so a fork's copy of a path is its own row.
    A row with no repository (an unparseable remote, or filed before the repository was
    kept) is keyed by the path alone."""
    return "%s%s%s" % (repository, _ROW_SEP, path) if repository else path


def _row_path(key):
    return key.split(_ROW_SEP, 1)[-1]


def state_view(files, repository):
    """{path: row} as the repository sees it: its own rows, over the rows that name none."""
    view = {k: v for k, v in files.items() if _ROW_SEP not in k}
    if repository:
        prefix = repository + _ROW_SEP
        view.update({k[len(prefix):]: v for k, v in files.items() if k.startswith(prefix)})
    return view


def push_repository(top, local, remote=None):
    """(repository 'owner/name', git remote URL) a push of `local` publishes to, or
    (None, None): after a push to a fork it is the fork."""
    if remote:
        url = _push_url(top, remote) or remote
    else:
        name, fetch_url = remote_for(top, local)
        url = _push_url(top, name) or fetch_url
    parsed = parse_remote(url) if url else None
    return (parsed[1], "https://%s/%s.git" % parsed) if parsed else (None, None)


# ------------------------------------------------------------------- visibility

def _visibility_of(top, url):
    """One remote's visibility word: public, private, internal or unknown."""
    parsed = parse_remote(url) if url else None
    if parsed is None:
        return "unknown"
    host, repo = parsed
    if host != "github.com":
        return "unknown"  # no other forge, and no SSH alias, is ever classified
    cached = _cached_visibility(repo)
    if cached:
        return cached
    rc, out, _ = _run(["gh", "repo", "view", repo, "--json", "visibility",
                       "-q", ".visibility"], top, timeout=GH_TIMEOUT_S)
    word = out.strip().lower() if rc == 0 else ""
    if word not in VISIBILITY_MAP:
        return "unknown"
    _remember_visibility(repo, word)
    return word


def _deadline():
    try:
        return max(0.1, float(os.environ.get("FM_ARTIFACTS_VISIBILITY_DEADLINE_S",
                                             VISIBILITY_DEADLINE_S)))
    except ValueError:
        return float(VISIBILITY_DEADLINE_S)


def _words_for(top, urls):
    """The visibility word of every url, asked in parallel under one total
    deadline. A remote that has not answered when the budget ends, or when another
    one already said `public`, is `unknown`: the caller asks the person for it."""
    words = {}
    lock = threading.Lock()

    def ask(url):
        try:
            word = _visibility_of(top, url)
        except Exception:  # noqa: BLE001 - a failing probe is an unanswerable remote
            word = "unknown"
        with lock:
            words[url] = word

    threads = [threading.Thread(target=ask, args=(u,), daemon=True) for u in urls]
    for t in threads:
        t.start()
    end = time.monotonic() + _deadline()
    while time.monotonic() < end:
        with lock:
            if len(words) == len(urls) or "public" in words.values():
                break
        time.sleep(0.02)
    with lock:
        return [words.get(u, "unknown") for u in urls]


def visibility(top, branch="", remote=None):
    """What the forge says about where a push from this branch would publish.

    `visibility` is one of public, private, internal, none (the repository has no
    remote at all, so nothing to be public), unknown (a remote exists and could not
    be asked: no `gh`, not signed in, not a GitHub host, or no answer within the
    deadline). When several remotes are candidates the most exposed answer wins:
    any public one makes it public, any unresolvable one makes it unknown.
    `source_visibility` is the brain's vocabulary (public | private | restricted |
    null)."""
    targets = _publish_targets(top, branch, remote)
    if not targets:
        return {"visibility": "none", "source_visibility": None}
    words = _words_for(top, [url for _, url in targets])
    for word in EXPOSURE_ORDER:
        if word in words:
            return {"visibility": word, "source_visibility": VISIBILITY_MAP.get(word)}
    return {"visibility": "unknown", "source_visibility": None}


def _visibility_ttl():
    try:
        return max(0, int(os.environ.get("FM_ARTIFACTS_VISIBILITY_TTL", VISIBILITY_TTL_S)))
    except ValueError:
        return VISIBILITY_TTL_S


def _visibility_cache_path():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "fairmind-coding", "artifact-visibility.json")


def _cached_visibility(target):
    """A definite answer the forge gave within the TTL, else None. Fail closed: a
    stale, future-dated, malformed or unreadable entry is no answer at all, so the
    forge is asked again, and if it cannot answer either the result is `unknown`,
    which asks the person. The cache holds the forge's word and nothing else."""
    ttl = _visibility_ttl()
    if not ttl:
        return None
    try:
        with open(_visibility_cache_path(), encoding="utf-8") as fh:
            row = json.load(fh)[target]
        if row["visibility"] in VISIBILITY_MAP and 0 <= time.time() - float(row["at"]) < ttl:
            return row["visibility"]
    except (OSError, ValueError, KeyError, TypeError):
        pass
    return None


def _remember_visibility(target, word):
    if not _visibility_ttl():
        return
    path = _visibility_cache_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            if not isinstance(data, dict):
                data = {}
        except (OSError, ValueError):
            data = {}
        data[target] = {"visibility": word, "at": time.time()}
        fd, tmp = tempfile.mkstemp(prefix=".vis-", dir=os.path.dirname(path))
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except OSError:
        pass


def needs_ask(vis):
    """Always ask in a public repository; an unanswerable visibility is treated
    as possibly public, because the cost of a wrong guess is a published file."""
    return vis["visibility"] in ("public", "unknown")


# --------------------------------------------------------------------- commands

def cmd_config(args):
    top = toplevel(args.repo)
    return {"repository_root": top, "paths": load_config(top, args.path_prefix),
            "state_file": os.path.relpath(state_path(top), top)}


def cmd_status(args):
    args.file = _from_cwd(args.file, args.repo)
    top = _repo_of_path(args.file, args.repo)
    prefixes = load_config(top, args.path_prefix)
    rel = rel_to_repo(top, args.file)
    out = {"path": rel, "is_artifact": is_artifact(rel, prefixes)}
    if not out["is_artifact"]:
        out["state"] = "not_artifact"
        return out
    exists = os.path.isfile(os.path.join(top, rel))
    rc, _, _ = _git(top, "ls-files", "--error-unmatch", "--", rel)
    tracked = rc == 0
    out.update({"exists": exists, "tracked": tracked})
    rc, blob, _ = _git(top, "hash-object", "--", rel) if exists else (1, "", "")
    out["blob_sha"] = blob.strip() if rc == 0 else None
    filed = state_view(load_state(top), push_repository(top, current_branch(top))[0]).get(rel)
    out["filed"] = filed
    if not exists:
        out["state"] = "missing"
    elif not tracked:
        out["state"] = "untracked"
    elif filed is None:
        out["state"] = "unfiled"
    elif filed["source_version"] == out["blob_sha"]:
        out["state"] = "filed_unchanged"
    else:
        out["state"] = "filed_changed"
    return out


def _repo_of_path(file_path, fallback):
    """The repository a file belongs to: for an absolute path, the one holding its
    nearest existing parent directory, whatever directory the session runs in;
    for a relative path, the session's repository."""
    if not os.path.isabs(file_path):
        return toplevel(fallback)
    d = os.path.dirname(os.path.normpath(file_path))
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    return toplevel(d)


def _visibility_why(vis):
    return ("is public" if vis["visibility"] == "public"
            else "could not be checked (is `gh` installed and signed in?)")


# What every ask says is at stake: writing a file into a checkout publishes nothing, the push does.
_PUBLISHED = "A push to a public repository publishes every working artifact it carries"


def _ask_reason(what, vis):
    return ("%s, and this repository %s. %s: confirm with the person before writing or pushing "
            "it." % (what, _visibility_why(vis), _PUBLISHED))


def _unclassifiable(top, branch, err, remote=None, path=None):
    """The write or push could not be classified (an unreadable config, a git
    failure). The cost of a wrong pass is a published file, so ask, unless the
    repository is known not to publish anywhere."""
    try:
        vis = visibility(top, branch, remote)
    except Exception:  # noqa: BLE001
        vis = {"visibility": "unknown", "source_visibility": None}
    if not needs_ask(vis):
        return {"decision": "pass", "reason": "unclassified_but_not_public", "path": path,
                "visibility": vis["visibility"], "error": str(err)}
    return {"decision": "ask", "path": path, "visibility": vis["visibility"],
            "error": str(err),
            "reason": _ask_reason("Whether %s is a working artifact could not be worked out (%s)"
                                  % (path or "this file", err), vis)}


def _from_cwd(path, cwd):
    """`path` as an absolute path: a relative one is relative to the directory the caller
    runs in, which may be a subdirectory of the repository or another repository."""
    return path if os.path.isabs(path) else os.path.join(os.path.abspath(cwd), path)


OPT_IN_MARKERS = (os.path.join(".fairmind", "active-context.json"),
                  os.path.join(".fairmind", "artifacts", "config.json"),
                  ".fairmind-artifacts.json")


def _has_marker(d):
    return any(os.path.isfile(os.path.join(d, m)) for m in OPT_IN_MARKERS)


def opted_in(start):
    """Whether the repository holding `start` carries a Fairmind marker. Walks up from the
    nearest existing directory to the first `.git` (inclusive) without running git, so a
    project that never opted in costs no process and no network call. A linked worktree
    also takes its main checkout's marker, which is usually untracked and so absent from
    the worktree. A marker in a parent repository, in a submodule's superproject, or above
    the repository root does not count."""
    d = os.path.abspath(start)
    while d and not os.path.isdir(d):
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    while True:
        if _has_marker(d):
            return True
        git = os.path.join(d, ".git")
        if os.path.isdir(git):
            return False
        if os.path.exists(git):
            root = linked_worktree_root(d)
            return root is not None and _has_marker(root)
        parent = os.path.dirname(d)
        if parent == d:
            return False
        d = parent


def _not_opted_in(**fields):
    return {"decision": "pass", "reason": "not_opted_in", **fields}


def cmd_gate(args):
    args.file_path = _from_cwd(args.file_path, args.repo)
    if not opted_in(os.path.dirname(os.path.normpath(args.file_path))):
        return _not_opted_in(path=None)
    try:
        top = _repo_of_path(args.file_path, args.repo)
    except CliError:
        return {"decision": "pass", "reason": "not_in_a_repository", "path": None}
    branch = current_branch(top)
    try:
        prefixes = load_config(top, args.path_prefix)
        rel = rel_to_repo(top, args.file_path)
        if not is_artifact(rel, prefixes):
            return {"decision": "pass", "reason": "not_artifact_path", "path": rel}
        vis = visibility(top, branch)
    except Exception as exc:  # noqa: BLE001 - any failure to classify is an ask
        return _unclassifiable(top, branch, exc, path=args.file_path)
    if needs_ask(vis):
        return {"decision": "ask", "path": rel, "visibility": vis["visibility"],
                "reason": _ask_reason("%s is a working artifact" % rel, vis)}
    return {"decision": "pass", "reason": "repository_not_public", "path": rel,
            "visibility": vis["visibility"]}


def cmd_visibility(args):
    top = toplevel(args.repo)
    return visibility(top, current_branch(top))


def push_destination(top, local, remote=None):
    """The remote a push of `local` goes to, by name: the one the push names (a URL is
    mapped to the configured remote that holds it), else the one git would use for
    pushes (`_push_remote_name`), else `origin`. "" when none can be named. A name or
    URL no remote of this repository matches is returned as given: nothing about it is
    known locally."""
    remotes = _remotes(top)
    remote = remote or _push_remote_name(top, local)
    if remote:
        if remote in remotes:
            return remote
        for name in remotes:
            # a push to a URL goes to that URL: it is the configured remote's only when it
            # is the address that remote pushes to (its push URL, else its URL)
            if remote == _push_url(top, name):
                return name
        return remote
    return "origin" if "origin" in remotes else ""


def _remote_ref_name(top, local, remote):
    """The branch a push of `local` updates on `remote` when no refspec names it: the
    branch's configured upstream name when `remote` is its remote, else its own name."""
    if local and remote and _git_config(top, "branch.%s.remote" % local) == remote:
        merge = _git_config(top, "branch.%s.merge" % local)
        if merge.startswith("refs/heads/"):
            return merge[len("refs/heads/"):]
    return local


def _tracking_ref(top, local, remote=None):
    """(ref, remote_branch): where the copy of this branch on the remote it is pushed
    to is known. Never another remote's copy."""
    dest = push_destination(top, local, remote)
    branch = _remote_ref_name(top, local, dest) or local
    if dest:
        ref = "%s/%s" % (dest, branch)
        if _git(top, "rev-parse", "-q", "--verify", "refs/remotes/%s" % ref)[0] == 0:
            return ref, branch
    return None, branch


def _tree(top, commit, prefixes):
    rc, out, err = _git(top, "ls-tree", "-r", "-z", commit, "--", *[p.rstrip("/") for p in prefixes])
    if rc != 0:
        raise CliError("git ls-tree failed: %s" % err.strip())
    files = {}
    for rec in out.split("\0"):
        if not rec:
            continue
        meta, _, path = rec.partition("\t")
        mode, kind, sha = meta.split()
        if kind == "blob" and mode != "120000":
            files[path] = sha
    return files


def _renames_since(top, old_commit, new_commit, prefixes):
    """{old_path: new_path} git detects between two commits, limited to the
    artifacts paths. Empty when the old commit is no longer reachable."""
    rc, _, _ = _git(top, "cat-file", "-e", "%s^{commit}" % old_commit)
    if rc != 0:
        return {}
    rc, out, _ = _git(top, "diff", "-M", "--name-status", "-z", old_commit,
                      new_commit, "--", *[p.rstrip("/") for p in prefixes])
    if rc != 0:
        return {}
    parts = [p for p in out.split("\0") if p != ""]
    renames, i = {}, 0
    while i < len(parts):
        status = parts[i]
        if status.startswith(("R", "C")) and i + 2 < len(parts):
            if status.startswith("R"):
                renames[parts[i + 1]] = parts[i + 2]
            i += 3
        else:
            i += 2
    return renames


def _is_default_branch(top, remote, branch):
    """Whether `branch` is the remote's default branch: what `refs/remotes/<remote>/HEAD`
    points at, else what the remote itself says its HEAD is. None when neither answers:
    a name is never guessed."""
    if not remote:
        return None
    rc, out, _ = _git(top, "symbolic-ref", "-q", "refs/remotes/%s/HEAD" % remote)
    prefix = "refs/remotes/%s/" % remote
    if rc == 0 and out.strip().startswith(prefix):
        return out.strip()[len(prefix):] == branch
    rc, out, _ = _run(["git", "ls-remote", "--symref", remote, "HEAD"], top, stdin="",
                      env={**os.environ, "GIT_TERMINAL_PROMPT": "0"})
    if rc == 0:
        for line in out.splitlines():
            if line.startswith("ref: refs/heads/") and line.endswith("\tHEAD"):
                return line[len("ref: refs/heads/"):-len("\tHEAD")] == branch
    return None


def cmd_push_diff(args):
    top = toplevel(args.repo)
    prefixes = load_config(top, args.path_prefix)
    local = current_branch(top)
    if args.ref:
        remote_branch = args.branch or local
        if remote_branch in ("HEAD", "@"):
            # HEAD names no remote branch: it is the branch checked out, or nothing.
            if not local:
                raise CliError("--branch HEAD cannot be resolved on a detached HEAD: name the branch")
            remote_branch = local
        ref = args.ref
    elif not local:
        return {"entries": [], "skipped": "detached_head"}
    else:
        ref, remote_branch = _tracking_ref(top, local, args.remote)
        if ref is None:
            return {"entries": [], "skipped": "no_remote_tracking_ref"}
    rc, out, err = _git(top, "rev-parse", "-q", "--verify", "%s^{commit}" % ref)
    if rc != 0:
        raise CliError("cannot resolve %s: %s" % (ref, err.strip()))
    commit = out.strip()
    tree = _tree(top, commit, prefixes)
    # The state file is per checkout, not per branch: a row only speaks about this
    # push when the commit it was filed at is part of the pushed history. A row filed
    # on another branch says nothing about a path that lives only there.
    ancestry = {}

    def reachable(path, row):
        """The row speaks about this push when the commit it was filed at is part of
        the pushed history, or when the blob it was filed with is: a squash merge
        rewrites the commit and keeps the content, and every branch cut after it
        lacks the filed commit for good."""
        c = row.get("commit")
        key = (c, path, row.get("source_version"))
        if key not in ancestry:
            ok = bool(c) and _git(top, "merge-base", "--is-ancestor", c, commit)[0] == 0
            if not ok and row.get("source_version"):
                rc, out, _ = _git(top, "log", "-1", "--format=%H",
                                  "--find-object=%s" % row["source_version"], commit, "--", path)
                ok = rc == 0 and bool(out.strip())
            ancestry[key] = ok
        return ancestry[key]

    repository, git_remote = push_repository(top, local, args.remote)
    everything = state_view(load_state(top), repository)
    # A row filed on this very branch speaks about it even when its commit is gone: an
    # amend or a rebase rewrote the history, and the blob it was filed with may be gone too.
    filed = {p: row for p, row in everything.items()
             if reachable(p, row) or row.get("branch") == remote_branch}

    default = _is_default_branch(top, args.remote or remote_for(top, local)[0], remote_branch)
    on_default = default is True
    restore_from = getattr(args, "restore_from_branch", None)
    entries, unchanged = [], 0
    new_paths = {}
    # A rename the platform could not finish retiring (the old card is still live) keeps
    # its old row, marked with the path it moved to: it is reported again, as that rename,
    # until a filing says the old card is archived.
    pending = {old: row["renamed_to"] for old, row in filed.items()
               if old not in tree and row.get("renamed_to") in tree}
    pending_targets = set(pending.values())
    for path, blob in sorted(tree.items()):
        row = filed.get(path)
        if path in pending_targets:
            continue  # reported below as the rename it is, never as unchanged or changed
        if row is None and path in everything:
            # Filed from a history this branch does not contain (another branch, or
            # a squash merge of it): the same blob is current. A different one is not
            # this push's to report. The client cannot tell a closed pull request from
            # an open one, so only an explicit restore (`--restore-from-branch`, naming
            # a branch the person knows is closed) reports it, on the default branch.
            if everything[path]["source_version"] == blob:
                unchanged += 1
            elif (on_default and restore_from
                  and everything[path].get("branch") == restore_from):
                entries.append({"action": "changed", "path": path, "blob_sha": blob})
        elif row is None:
            new_paths[path] = blob
        elif row["source_version"] != blob:
            entries.append({"action": "changed", "path": path, "blob_sha": blob})
        else:
            unchanged += 1

    # Filed paths that left the tree: a rename when git (or an identical blob)
    # says so, otherwise gone.
    claimed = {}
    for old, new in sorted(pending.items()):
        # `resend` marks a rename the platform answered but could not finish: the check
        # form carries no rename and would answer "current", so it is never used for it.
        entries.append({"action": "renamed", "path": new, "renamed_from": old,
                        "blob_sha": tree[new], "resend": True})
    missing = sorted(p for p in filed if p not in tree and p not in pending)
    by_blob = {}
    for p, b in new_paths.items():
        by_blob.setdefault(b, []).append(p)
    by_commit = {}
    for m in missing:
        c = filed[m].get("commit")
        if c:
            by_commit.setdefault(c, []).append(m)
    for c, olds in by_commit.items():
        detected = _renames_since(top, c, commit, prefixes)
        for old in olds:
            target = detected.get(old)
            if target in new_paths and target not in claimed.values():
                claimed[old] = target
    for old in missing:
        if old in claimed:
            continue
        twins = [p for p in by_blob.get(filed[old]["source_version"], [])
                 if p not in claimed.values()]
        if len(twins) == 1:
            claimed[old] = twins[0]
    for old, new in sorted(claimed.items()):
        entries.append({"action": "renamed", "path": new, "renamed_from": old,
                        "blob_sha": new_paths[new]})
    for new, blob in new_paths.items():
        if new not in claimed.values():
            entries.append({"action": "new", "path": new, "blob_sha": blob})
    # A deletion is reported only for a push to the default branch: on any other
    # branch the file still exists on the default branch, so a deletion there is not
    # reported and the card stays until a push to the default branch reports `gone`.
    if on_default:
        for old in missing:
            if old not in claimed:
                entries.append({"action": "gone", "path": old})
    order = {"new": 0, "changed": 1, "renamed": 2, "gone": 3}
    entries.sort(key=lambda e: (order[e["action"]], e["path"]))

    # No baseline: no state file, or one that holds no row for this repository.
    result = {"entries": [], "unchanged": unchanged, "commit": commit,
              "branch": remote_branch,
              "baseline_missing": not (os.path.isfile(state_path(top)) and everything)}
    if default is None:
        result["default_branch_unknown"] = True
    if args.limit is not None and len(entries) > args.limit:
        result["truncated"] = len(entries) - args.limit
        entries = entries[:args.limit]
    if entries:
        # The card names the remote this push published to: after a push to a fork it
        # is the fork, which is where the commit it cites lives.
        vis = visibility(top, local, args.remote)
        result["repository"] = repository
        result["git_remote"] = git_remote
        result["source_visibility"] = vis["source_visibility"]
        result["visibility"] = vis["visibility"]
        for e in entries:
            e.update({"commit": commit, "branch": remote_branch,
                      "repository": result["repository"], "git_remote": result["git_remote"],
                      "source_visibility": vis["source_visibility"]})
            if e["action"] == "gone":
                for k in ("commit", "branch", "source_visibility"):
                    e.pop(k)
    result["entries"] = entries
    return result


# ------------------------------------------------------------------------- args

def _need(payload, key, kind=str):
    v = payload.get(key)
    if not isinstance(v, kind) or (kind is str and not v.strip()):
        raise CliError("`%s` is required" % key)
    return v


def _tell():
    """The report builder, imported on use: the copy of this script that the git
    pre-push hook runs holds two files and serves only the gate modes, so a module
    level import of a third would break the hook."""
    import _tell_report
    return _tell_report


def build_args(payload, mode=None):
    if not isinstance(payload, dict):
        raise CliError("stdin must be one JSON object")
    bad = [k for k in CONTENT_KEYS if k in payload]
    if bad:
        raise CliError("an artifact card is a reference and never carries the "
                       "file's text or another location: remove %s" % ", ".join(bad))
    told_elsewhere = [k for k in RETIRED_KEYS if k in payload]
    if told_elsewhere:
        raise CliError("a card says only what happened to a file; what a document is "
                       "evidence for is told by the decision or requirement that rests "
                       "on it: remove %s" % ", ".join(told_elsewhere))
    action = payload.get("action")
    mode = mode or ("gone" if action == "gone" else "write")
    if mode not in ("check", "write", "gone"):
        raise CliError("mode must be check, write or gone")
    entry = {"path": _need(payload, "path"), "repository": _need(payload, "repository"),
             "agent": _need(payload, "agent"), "git_remote": _need(payload, "git_remote"),
             "project": _need(payload, "project")}
    if mode != "gone":
        entry["blob_sha"] = _need(payload, "blob_sha")
    if mode == "check" and payload.get("resend"):
        raise CliError("this entry resends a rename the platform could not finish: "
                       "skip the check, the check form carries no rename and would "
                       "answer that the card is current; file it with `args` (no --mode)")
    if mode == "write":
        summary = _need(payload, "summary")
        if len(summary) > SUMMARY_MAX:
            raise CliError("the summary is %d characters; the limit is %d"
                           % (len(summary), SUMMARY_MAX))
        if "source_visibility" not in payload:
            raise CliError("`source_visibility` is required (null when unknown)")
        if payload["source_visibility"] not in ("public", "private", "restricted", None):
            raise CliError("`source_visibility` must be public, private, restricted or null")
        entry["source_visibility"] = payload["source_visibility"]
        for key in ("summary", "title", "commit", "branch"):
            entry[key] = _need(payload, key)
        for key in ("renamed_from", "published_url"):
            if payload.get(key):
                entry[key] = _need(payload, key)
    try:
        return _tell().git_document_report(entry, mode=mode)
    except ValueError as exc:
        raise CliError(str(exc))


def cmd_args(args):
    return {"tool": "Brain_tell",
            "arguments": build_args(_read_stdin_json(), args.mode)}


class Refused(Exception):
    """A blueprint that cannot be linked: the reason is printed on stderr, exit 3."""


def _blueprint_commit(top, rel):
    """The last commit that changed `rel`, so an unchanged file gives the same link
    on every run. Raises Refused unless the file is tracked, clean, a regular file at
    that commit, and the commit is on a branch of `origin`."""
    if _git(top, "ls-files", "--error-unmatch", "--", rel)[0] != 0:
        raise Refused("%s is not tracked by git" % rel)
    rc, out, _ = _git(top, "log", "-1", "--format=%H", "--", rel)
    commit = out.strip()
    if rc != 0 or not commit:
        raise Refused("%s has no commit yet" % rel)
    if _git(top, "cat-file", "-t", "%s:%s" % (commit, rel))[1].strip() != "blob":
        raise Refused("%s is not a file" % rel)
    if _git(top, "diff", "--quiet", commit, "--", rel)[0] != 0:
        raise Refused("%s differs from its last commit: commit it, or use the person's "
                      "link instead" % rel)
    rc, out, _ = _git(top, "for-each-ref", "--contains", commit, "refs/remotes/origin")
    if rc != 0 or not out.strip():
        raise Refused("the commit that holds %s is on no branch of origin: push it first"
                      % rel)
    return commit


def cmd_blueprint(args):
    """The link-form report that files a checkout file as a document, and the
    related-document item that names the same card from a component report."""
    top = toplevel(args.repo)
    who = _read_stdin_json()
    if not isinstance(who, dict):
        raise CliError("stdin must be one JSON object")
    agent, project = _need(who, "agent"), _need(who, "project")
    tell = _tell()
    title = tell.fit_text(tell.one_line(args.title), tell.TITLE_MAX)
    if title is None:
        raise CliError("`--title` is required")
    try:
        with open(args.summary_file, encoding="utf-8") as fh:
            summary = tell.fit_text(fh.read(), tell.TEXT_MAX)
    except (OSError, UnicodeDecodeError) as exc:
        raise CliError("cannot read the summary file: %s" % exc)
    if summary is None:
        raise CliError("the summary file is empty")
    rel = rel_to_repo(top, args.path)
    if rel is None:
        raise CliError("%s is outside the repository" % args.path)
    origin = _git_config(top, "remote.origin.url")
    origin = re.split(r"[?#]", origin, maxsplit=1)[0] if origin else origin
    parsed = parse_remote(origin) if origin else None
    if not parsed:
        raise Refused("origin has no host and repository path to build a link from")
    commit = _blueprint_commit(top, rel)
    url = tell.forge_blob_url(origin, commit=commit, path=rel)
    if url is None:
        raise Refused("origin is not a remote a link can be built for")
    version = _git(top, "rev-parse", "%s:%s" % (commit, rel))[1].strip()
    link = urlsplit(url)
    report = {"intent": "document", "repository": parsed[1],
              "git_remote": "%s://%s/%s.git" % (link.scheme, link.netloc, parsed[1]),
              "project": project,
              "agent": tell.fit_ref(agent), "title": title, "summary": summary,
              "source": {"url": url, "version": version, "system": "git"}}
    return {"tool": "Brain_tell", "arguments": report,
            "item": {"url": url, "title": title, "summary": summary}}


def _had_card(row):
    """Whether a card was ever filed for a path whose state row this is: a row that is
    not declined records a filing, a declined one says so with `filed_before`."""
    return bool(row) and (bool(row.get("filed_before")) or not row.get("declined"))


def cmd_record(args):
    top = toplevel(args.repo)
    payload = _read_stdin_json()
    entries = payload.get("entries") if isinstance(payload, dict) and "entries" in payload else payload
    if isinstance(entries, dict):
        entries = [entries]
    if not isinstance(entries, list):
        raise CliError("stdin must be an entry or a list of entries")
    files = load_state(top)
    for e in entries:
        path = _need(e, "path")
        repository = e.get("repository")
        if e.get("action") == "gone":
            for key in [k for k in files if _row_path(k) == path
                        and (not repository or k in (path, state_key(repository, path)))]:
                # A no to retiring a card leaves it live, so its row stays and the
                # deletion is asked about again. A row that never had a card (a declined
                # new file) has nothing to retire: it goes either way.
                if args.declined and _had_card(files[key]):
                    continue
                del files[key]
            continue
        row = {"source_version": _need(e, "blob_sha"),
               "commit": _need(e, "commit"), "branch": _need(e, "branch")}
        key = state_key(repository, path)
        if args.declined:
            # The person said no to a card for this blob: stay quiet about it until
            # the file changes. Remember whether a card was ever filed for the path.
            row["declined"] = True
            if _had_card(files.get(key) or files.get(path)):
                row["filed_before"] = True
        files[key] = row
        old = e.get("renamed_from")
        if not old:
            continue
        if args.declined:
            # A declined rename retired nothing: the old card is still live, so its row
            # stays and the old path is reported as a deletion when the file is gone. A
            # declined resend stops being a pending rename: the old row is a plain one.
            for old_key in (state_key(repository, old), old):
                if old_key in files:
                    files[old_key].pop("renamed_to", None)
            continue
        if e.get("retirement_pending") is True:
            # The platform answered renamed_from.archived false, retryable true: the old
            # card is still live. Keep its row, marked, so push-diff offers the rename again.
            old_key = next((k for k in (state_key(repository, old), old) if k in files), None)
            if old_key:
                files[old_key]["renamed_to"] = path
        else:
            files.pop(state_key(repository, old), None)
            files.pop(old, None)
    save_state(top, files)
    return {"recorded": len(entries), **_state_counts(files)}


def _state_counts(files):
    declined = sum(1 for r in files.values() if r.get("declined"))
    # A kept old row stands for a card still to retire, not for a second artifact.
    retiring = sum(1 for r in files.values() if r.get("renamed_to") and not r.get("declined"))
    out = {"filed": len(files) - declined - retiring, "declined": declined}
    if retiring:
        out["retirement_pending"] = retiring
    return out


def cmd_state(args):
    return _state_counts(load_state(toplevel(args.repo)))


# -------------------------------------------------------------------- push-gate

_ZEROS = re.compile(r"^0+$")


def _commit_exists(top, ref):
    return _git(top, "cat-file", "-e", "%s^{commit}" % ref)[0] == 0


def _pushes_elsewhere_than_it_fetches(top, remote):
    """Whether `remote` pushes to an address other than the one its remote-tracking refs
    were fetched from: those refs then describe a different repository."""
    pushurl = _git_config(top, "remote.%s.pushurl" % remote)
    fetch = _git_config(top, "remote.%s.url" % remote)
    if not pushurl or pushurl == fetch:
        return False
    return not (parse_remote(pushurl) and parse_remote(pushurl) == parse_remote(fetch))


def _push_bases(top, head, remote, explicit, dst=None):
    """The commits the remote already holds that a push of `head` is measured against.
    Only the remote the push goes to counts, never another remote's copy: the one the
    caller already knows (`explicit`, the old value a git pre-push hook is handed), else
    that remote's copy of the branch, else, for a branch it does not have yet, the
    boundary of the commits that no ref of that remote reaches (git's own recipe).
    An empty list means nothing of the remote is known, so the whole history is new."""
    if explicit and not _ZEROS.match(explicit) and _commit_exists(top, explicit):
        return [explicit]
    if not remote or _pushes_elsewhere_than_it_fetches(top, remote):
        return []
    if dst and _git(top, "rev-parse", "-q", "--verify", "refs/remotes/%s/%s" % (remote, dst))[0] == 0:
        return ["refs/remotes/%s/%s" % (remote, dst)]
    rc, known, _ = _git(top, "for-each-ref", "--count=1", "refs/remotes/%s/" % remote)
    if rc != 0 or not known.strip():
        return []  # a remote with no known refs: the whole history is new, no need to list it
    rc, out, _ = _git(top, "rev-list", "--boundary", head, "--not", "--remotes=%s" % remote)
    if rc != 0:
        return []
    lines = out.splitlines()
    if not [l for l in lines if l and not l.startswith("-")]:
        return [head]  # every commit is already on the remote: nothing outgoing
    return sorted({l[1:] for l in lines if l.startswith("-")})


def _outgoing_artifacts(top, prefixes, head, bases):
    """Artifact paths the commits between `bases` and `head` add or change. A path is
    outgoing when it differs from every base: one that matches any of them is already
    held by the remote."""
    # every blob an outgoing commit adds or changes travels with the push, even one a
    # later outgoing commit deletes again
    touched = _touched_artifacts(top, prefixes, head, bases)
    if not bases:
        return sorted(touched | set(_tree(top, head, prefixes)))
    outgoing = None
    for base in bases:
        rc, out, err = _git(top, "diff", "-M", "--name-status", "-z", base, head,
                            "--", *[p.rstrip("/") for p in prefixes])
        if rc != 0:
            raise CliError("git diff failed: %s" % err.strip())
        parts = [x for x in out.split("\0") if x != ""]
        paths, i = set(), 0
        while i < len(parts):
            status = parts[i]
            if status.startswith(("R", "C")) and i + 2 < len(parts):
                paths.add(parts[i + 2])
                i += 3
            else:
                if not status.startswith("D") and i + 1 < len(parts):
                    paths.add(parts[i + 1])
                i += 2
        outgoing = paths if outgoing is None else outgoing & paths
    return sorted(outgoing | touched)


def _touched_artifacts(top, prefixes, head, bases):
    """Artifact paths that any commit between `bases` and `head` adds or changes, whatever
    the final tree holds. With no known base that is the whole history of `head`.
    `--full-history` keeps a side branch a merge discarded (`-s ours`, or one that added and
    deleted an artifact): git log's default simplification drops it, yet its commits travel
    with the push. `-c` lists what a merge commit itself introduces, which git log otherwise
    shows no diff for, without listing what it merely brings in from a parent."""
    cmd = ["log", "-z", "--format=", "--name-only", "--no-renames", "--diff-filter=AMCT",
           "--full-history", "-c", head]
    if bases:
        cmd += ["--not", *bases]
    rc, out, err = _git(top, *cmd, "--", *[p.rstrip("/") for p in prefixes])
    if rc != 0:
        raise CliError("git log failed: %s" % err.strip())
    return {x for x in re.split(r"[\0\n]", out) if x}


def _pending_artifacts(top, prefixes):
    """Artifact paths with staged, unstaged or untracked changes in the working tree:
    what a commit made later in the same shell line would carry. Ignored files count: a
    forced `git add` stages one whatever .gitignore says."""
    rc, out, err = _git(top, "status", "--porcelain", "-z", "--untracked-files=all",
                        "--ignored=matching", "--", *[p.rstrip("/") for p in prefixes])
    if rc != 0:
        raise CliError("git status failed: %s" % err.strip())
    parts = [x for x in out.split("\0") if x != ""]
    paths, i = [], 0
    while i < len(parts):
        rec = parts[i]
        xy, path = rec[:2], rec[3:]
        i += 1
        if "R" in xy or "C" in xy:
            i += 1  # the original path follows
        if xy.strip("D ") != "":  # a pure deletion sends no artifact out
            paths.append(path)
    return sorted(set(paths))


def _resolve(top, rev):
    rc, out, err = _git(top, "rev-parse", "-q", "--verify", "%s^{commit}" % rev)
    if rc != 0:
        raise CliError("cannot resolve %s: %s" % (rev, err.strip() or "unknown revision"))
    return out.strip()


def _local_branches(top):
    rc, out, _ = _git(top, "for-each-ref", "--format=%(refname:short)", "refs/heads")
    return [b for b in out.split() if b] if rc == 0 else []


def _push_targets(top, args, local):
    """[(head commit, bases, remote)] one per thing the push sends: each
    `--refspec SRC:DST`, or every local branch with `--all-branches`, or the one
    `--ref` (default HEAD) the git pre-push hook hands over. `remote` is the remote that
    thing goes to, resolved the way git resolves it for a push."""
    if args.all_branches:
        dest = push_destination(top, local, args.remote)
        targets = []
        for b in _local_branches(top):
            head = _resolve(top, b)
            targets.append((head, _push_bases(top, head, dest, None, b), dest))
        return targets
    if args.refspec:
        targets = []
        for spec in args.refspec:
            src, _, dst = spec.partition(":")
            if src in ("HEAD", "@") and local:
                src = local
                dst = local if dst in ("", "HEAD", "@") else dst
            dst = dst[len("refs/heads/"):] if dst.startswith("refs/heads/") else dst
            branch = src[len("refs/heads/"):] if src.startswith("refs/heads/") else src
            is_branch = _git(top, "rev-parse", "-q", "--verify", "refs/heads/%s" % branch)[0] == 0
            dest = push_destination(top, branch if is_branch else local, args.remote)
            head = _resolve(top, src)
            targets.append((head, _push_bases(top, head, dest, None, dst or branch), dest))
        return targets
    head = _resolve(top, args.ref or "HEAD")
    dest = push_destination(top, local, args.remote)
    return [(head, _push_bases(top, head, dest, args.base, _remote_ref_name(top, local, dest)), dest)]


def _most_exposed(answers):
    for word in EXPOSURE_ORDER:
        for a in answers:
            if a["visibility"] == word:
                return a
    return answers[0] if answers else {"visibility": "none", "source_visibility": None}


def cmd_push_gate(args):
    if getattr(args, "command", None) is not None:
        return _gate_command(args)
    if not os.path.isdir(args.repo):
        return _directory_unresolved(args.repo)
    try:
        top = toplevel(args.repo)
    except CliError:
        if args.shell or _inside_git_dir(args.repo):
            # a git directory with no working tree (inside `.git`, a bare repository) is
            # pushed from by git itself; and the pre-push hook only ever runs in a
            # repository, so for it "not a repository" means the gate could not look
            return _ask_line("it runs inside a git directory with no working tree, where the "
                             "outgoing artifacts cannot be located")
        return {"decision": "pass", "reason": "not_in_a_repository", "paths": []}
    local = current_branch(top)
    try:
        prefixes = load_config(top, args.path_prefix)
        targets = _push_targets(top, args, local)
        paths, outgoing_dests = set(), set()
        for head, bases, dest in targets:
            found = _outgoing_artifacts(top, prefixes, head, bases)
            paths |= set(found)
            if found:
                outgoing_dests.add(dest)
        loose = set()
        if args.pending:
            loose |= set(_pending_artifacts(top, prefixes))
        paths = sorted(paths | loose)
        if not paths:
            return {"decision": "pass", "reason": "no_artifacts_in_push", "paths": []}
        # a destination that receives nothing of it is not what exposes it; files no commit
        # holds yet go wherever the push goes
        dests = sorted({d for _, _, d in targets} if loose else outgoing_dests) or [""]
        vis = _most_exposed([visibility(top, local, d or None) for d in dests])
    except Exception as exc:  # noqa: BLE001 - any failure to classify is an ask
        return _unclassifiable(top, local, exc, remote=args.remote)
    shown = ", ".join(paths[:5]) + (" and %d more" % (len(paths) - 5) if len(paths) > 5 else "")
    if needs_ask(vis):
        return {"decision": "ask", "paths": paths, "visibility": vis["visibility"],
                "reason": _ask_reason("This push carries working artifacts (%s)" % shown, vis)}
    return {"decision": "pass", "reason": "repository_not_public", "paths": paths,
            "visibility": vis["visibility"]}


def _inside_git_dir(cwd):
    """True when `cwd` is in a git directory (`.git`, a bare repository) whether or not it
    has a working tree; False when there is no git directory at all."""
    return _git(cwd, "rev-parse", "--git-dir")[0] == 0


def _directory_unresolved(where):
    return _ask_line("the directory it runs in cannot be worked out before it runs: %s" % where)


# --------------------------------------------------------------- reading a shell line
#
# A `--command` line is what an agent is about to run in a shell. Nothing here tries to
# understand shell. A line is read command by command, inside a deliberately tiny grammar:
# commands joined by `;`, `&&`, `||`, a pipe or a newline, with no expansion, subshell, background,
# continuation or redirection to a file, each one a known command that cannot publish, a
# literal `cd`, or a literal `git push` with literal arguments, which is measured in the
# directory it runs in. Reading stops at the first command outside the grammar: every push
# before it is measured, and the rest of the line passes unchecked. Whether a command outside
# the grammar publishes depends on what it runs (a script, an alias, git's configuration), not
# on how it is spelled, so its text is not searched for words that look like a publish: a push
# the line does not spell out in full, or spells with an option or a refspec the grammar does
# not read, is the git pre-push hook's to see (`install-git-hook`), which git hands the
# destination and the exact ref updates. Nothing before a measured push can change what it
# sends or where, because the measure is taken before the line runs.
#
# This is a courtesy gate and not a security boundary: `git push --no-verify`, another
# `core.hooksPath`, `git send-pack`, a script the person runs themselves, a program git's
# configuration runs during an allowed command (hooks, diff and textconv drivers,
# `core.fsmonitor`, filters) and a model that edits the plugin all go around it. What it
# guarantees is that a git push an agent spells out in full in a Bash command is measured
# before it runs.

# Outside single quotes, one of these is shell syntax this gate does not read: expansion (`$`,
# backtick) and a backslash that escapes something (inside double quotes, one before a character
# it does not escape is text). So is a control character other than a tab or a newline. Inside
# single quotes everything is text, as bash reads it. The rest of the shell's syntax (`&`, `|`,
# `;`, `<`, `>`, parentheses, a newline) is read outside quotes only, where `_commands` takes it
# apart; inside quotes it is text.
_SYNTAX_UNREAD = frozenset("$`\\")
# The redirections read: a copy of one stream onto another, and /dev/null. None creates a file.
_REDIRECT = re.compile(r"(\d*>&\d+|(\d*>>?|&>|\d*<)[ \t]*/dev/null)(?=[ \t\n;&|]|$)")
# Commands that read and print. None of them publishes, none of them takes an option that
# runs another program, and none of them writes the file an option names (`tree -o`,
# `file -C` do, so they are not here).
_READ_ONLY = frozenset({"ls", "pwd", "cat", "head", "tail", "wc", "echo", "true", "false",
                        "grep", "which", "date", "whoami", "uname", "basename", "dirname",
                        "stat", "du", "df"})
# git subcommands that cannot publish and cannot change where a later push goes.
_GIT_LOCAL = frozenset({"status", "diff", "log", "show", "rev-parse", "ls-files", "ls-tree",
                        "describe", "blame", "shortlog", "add", "commit"})
# The only options of a literal `git push` this gate reads. Any other option, including every
# `--no-*` negation except `--no-verify`, makes the push unreadable, and it is not measured.
# `git log`, `diff` and `show` write the file `--output` names; git accepts any unique
# abbreviation of a long option, so every prefix of it counts.
_WRITES_A_FILE = re.compile(r"^--o(u(t(p(u(t)?)?)?)?)?(=.*)?$")
_PUSH_PLAIN = frozenset({"-u", "--set-upstream", "-f", "--force", "-v", "--verbose", "-q",
                         "--quiet", "--no-verify"})
_FORCE_LEASE = re.compile(r"^--force-with-lease(=[A-Za-z0-9_./:^~-]+)?$")
# A remote, a URL or a refspec written out in full: no glob, brace or variable character.
_PLAIN_WORD = re.compile(r"^[A-Za-z0-9_@./:+^~-]+$")


class _Unreadable(Exception):
    """The line is outside the grammar: reading stops at the command it is in."""


def _read_push(args, earlier):
    """The facts of one literal `git push` (the words after `push`), or _Unreadable."""
    info = {"dry": False, "delete": False, "all": False, "earlier": earlier}
    positional = []
    for a in args:
        if a in ("-n", "--dry-run"):
            info["dry"] = True
        elif a in ("-d", "--delete"):
            info["delete"] = True
        elif a == "--all":
            info["all"] = True
        elif a in _PUSH_PLAIN or _FORCE_LEASE.match(a):
            pass
        elif a.startswith("-"):
            raise _Unreadable("the push option %s is not one this gate reads" % a)
        elif (not _PLAIN_WORD.match(a) or a.startswith("~") or ":~" in a or a == "tag"):
            raise _Unreadable("the push argument %r is not a plain remote or refspec" % a)
        else:
            positional.append(a)
    info["remote"] = positional[0] if positional else None
    pairs, deletions = [], 0
    for spec in positional[1:]:
        body = spec.lstrip("+")
        src, colon, dst = body.partition(":")
        if not body or (colon and not src and not dst):
            # ":" is the "matching" refspec: it sends every local branch the remote has
            raise _Unreadable("the refspec %r is not a source and a destination" % spec)
        if not src:  # ":branch" deletes the remote branch
            deletions += 1
        else:
            pairs.append((src, dst if colon and dst else src))
    info["refspecs"] = pairs
    info["publishes"] = bool(not info["dry"] and not info["delete"] and (pairs or not deletions))
    return info


def _commands(command):
    """The line cut at its operators outside quotes: ([(the operator before the command, its
    text, where it starts)], where the cutting stopped). The cutting stops, at the start of the
    command it is in, on syntax this gate does not read; it reaches the end of the line
    otherwise. A redirection in `_REDIRECT` is dropped from the command's text.
    A newline outside quotes ends a command as `;` does, except right after `&&`, `||` or a
    pipe, where bash reads on to the command the operator still waits for."""
    parts, text, joint, start, quote, i = [], [], None, 0, None, 0
    while i < len(command):
        c = command[i]
        text_in_quotes = quote == "'" or (quote == '"' and c == "\\"
                                          and command[i + 1:i + 2] not in '"\\$`\n')
        if not text_in_quotes and (c in _SYNTAX_UNREAD or (c < " " and c not in "\t\n") or c == "\x7f"):
            return parts, start
        if quote:
            quote = None if c == quote else quote
        elif c in "'\"":
            quote = c
        else:
            at_word = not text or text[-1] in " \t\n"
            if at_word and c == "#":  # a comment
                return parts, start
            redirect = _REDIRECT.match(command, i) if at_word else None
            if redirect:
                text.append(" ")
                i = redirect.end()
                continue
            two = command[i:i + 2]
            op = two if two in ("&&", "||", ";;") else c if c in ";|" else ";" if c == "\n" else None
            if op == ";;":
                return parts, start
            if c == "\n" and joint in ("&&", "||", "|") and not "".join(text).strip():
                op = None
            if op:
                parts.append((joint, "".join(text), start))
                joint, text, i = op, [], i + len(op)
                start = i
                continue
            if c in "&<>()":
                return parts, start
        text.append(c)
        i += 1
    if quote:  # a quote that is never closed
        return parts, start
    parts.append((joint, "".join(text), start))
    return parts, len(command)


def _directory(base, word, cd=False):
    """The directory `cd word` (cd=True) or `git -C word` moves to from `base`, or _Unreadable.
    Not read: a `~` (expanded only when unquoted, and the quotes are gone by now), a `..`
    (bash's `cd` takes it by name, git's `-C` through a symlink), and a relative `cd` that does
    not start with `./`, which may follow CDPATH."""
    if (not word or word.startswith(("-", "~")) or ".." in word.split("/")
            or any(c in word for c in "*?[]{}")):
        raise _Unreadable("the directory %r is not a literal path" % word)
    if cd and not word.startswith(("/", "./")) and word != ".":
        raise _Unreadable("cd %s may follow CDPATH" % word)
    return os.path.normpath(os.path.join(base, word))


def _read_line(command, cwd):
    """The literal `git push`es of a line, as `_read_push` facts with the directories each may
    run in (`dirs`), in order, up to the first command outside the grammar: (pushes, the rest
    of the line from that command) with rest "" when the whole line was read."""
    if not isinstance(command, str):
        raise TypeError("there is no command text")
    parts, cut = _commands(command)
    # Where the shell may be after the last command, by its exit status. `&&` runs the next
    # command where it succeeded, `||` where it failed, `;` everywhere; a pipe runs every part
    # of it where the first one ran.
    ok, bad = {cwd}, set()
    pushes, commits = [], False
    for n, (joint, text, start) in enumerate(parts):
        nxt = parts[n + 1][0] if n + 1 < len(parts) else None
        if joint == "&&":
            runs, kept_ok, kept_bad = ok, set(), bad
        elif joint == "||":
            runs, kept_ok, kept_bad = bad, ok, set()
        elif joint != "|":
            runs, kept_ok, kept_bad = ok | bad, set(), set()
        try:
            words = shlex.split(text)
            if not words:
                if joint in (None, ";") and nxt in (None, ";"):
                    continue
                raise _Unreadable("it has an empty command")
            head, landed = words[0], runs
            if head == "cd":
                if len(words) != 2 or "|" in (joint, nxt):
                    raise _Unreadable("cd is read only with one directory and outside a pipe")
                landed = {_directory(d, words[1], cd=True) for d in runs}
            elif head == "git":
                where, rest = runs, words[1:]
                while rest[:1] == ["--no-pager"] or (rest[:1] == ["-C"] and len(rest) > 1):
                    if rest[0] == "-C":
                        where = {_directory(d, rest[1]) for d in where}
                        rest = rest[1:]
                    rest = rest[1:]
                sub = rest[0] if rest else ""
                if sub == "push":
                    pushes.append(dict(_read_push(rest[1:], earlier=commits), dirs=sorted(where)))
                elif sub not in _GIT_LOCAL:
                    raise _Unreadable("git %s is not on the short list of read-only commands" % sub)
                elif any(_WRITES_A_FILE.match(a) for a in rest[1:]):
                    raise _Unreadable("git %s writes the file its --output option names" % sub)
                commits = commits or sub in ("add", "commit")
            elif head not in _READ_ONLY:
                raise _Unreadable("%s is not on the short list of read-only commands" % head)
        except _Unreadable:
            return pushes, command[start:]
        ok, bad = kept_ok | landed, kept_bad | runs
    return pushes, command[cut:]


def _config_hides_destination(top, explicit_refspec):
    """Why the configured remotes cannot be trusted to say where a literal push goes, or
    "". git rewrites a URL (`url.<base>.insteadOf`, `.pushInsteadOf`), pushes to every URL
    of a remote that has several, and pushes the refs a `remote.<name>.push` or
    `push.default = matching` names when the push names none: this reader applies none of
    them, so each asks."""
    rc, out, _ = _git(top, "config", "--list", "-z")
    if rc != 0:
        return "the git configuration could not be read"
    counts = {}
    for rec in out.split("\0"):
        key, _, value = rec.partition("\n")
        key = key.lower()
        if re.match(r"^url\..+\.(push)?insteadof$", key):
            return "git rewrites remote URLs (%s)" % key.rsplit(".", 1)[-1]
        found = re.match(r"^remote\.(.+)\.(url|pushurl)$", key)
        if found:
            kind = (found.group(1), found.group(2))
            counts[kind] = counts.get(kind, 0) + 1
            if counts[kind] > 1:
                return "remote %s has several %ss" % kind
        if not explicit_refspec and re.match(r"^remote\..+\.mirror$", key) and (
                "\n" not in rec or value.strip().lower() in ("true", "yes", "on", "1")):
            return "a remote is configured as a mirror, so a bare push sends every ref"
        if not explicit_refspec and (re.match(r"^remote\..+\.push$", key)
                                     or (key == "push.default" and value.lower() == "matching")):
            return "the configuration chooses which refs a bare push sends"
    return ""


def _ask_line(why):
    """The ask for a line the gate read but could not measure: a push read in full whose
    destination or payload cannot be worked out, or the reader itself failing."""
    return {"decision": "ask", "paths": [], "visibility": "unknown", "error": why,
            "reason": "The artifact gate could not check what this command would send (%s). "
                      "%s: confirm with the person first." % (why, _PUBLISHED)}


def _gate_command(args):
    """push-gate for a raw shell line: it passes when every literal push the grammar reads is
    measured clean; what follows the first command outside the grammar is not read. A push runs
    in the directory a `cd` or `git -C` before it names, which is measured whether or not that
    repository opted in."""
    cwd = args.cwd or args.repo
    if not opted_in(cwd):
        return _not_opted_in(paths=[])
    try:
        pushes = _read_line(args.command, cwd)[0]
        measured = set()  # (push, repository): two directories of one repository measure alike
        for info, where in ((i, d) for i in pushes if i["publishes"] for d in i["dirs"]):
            if not os.path.isdir(where):
                return _directory_unresolved(where)
            try:
                top = toplevel(where)
            except CliError:
                if _inside_git_dir(where):
                    return _ask_line("it pushes from a git directory with no working tree")
                continue  # no git directory at all: a push from here fails by itself
            if (id(info), top) in measured:
                continue
            why = _config_hides_destination(top, bool(info["refspecs"]))
            if why:
                return _ask_line(why)
            ns = argparse.Namespace(
                repo=where, path_prefix=args.path_prefix, ref=None, base=None, command=None,
                remote=info["remote"], all_branches=info["all"], shell=False,
                refspec=None if info["all"] else ["%s:%s" % pair for pair in info["refspecs"]],
                pending=info["earlier"])
            answer = cmd_push_gate(ns)
            if answer.get("decision") != "pass":
                return answer
            measured.add((id(info), top))
    except Exception as exc:  # noqa: BLE001 - a line that cannot be measured is an ask
        return _ask_line("%s: %s" % (type(exc).__name__, exc))
    return {"decision": "pass", "paths": [],
            "reason": "checked" if measured else "nothing_measured"}


# --------------------------------------------------------------- git pre-push hook
#
# The hook is a thin shell file that runs a copy of this script kept in the repository's git
# directory (`<common dir>/fairmind/`), never the plugin's own path: a plugin installed from git
# lives in a directory named after its version, so a hook that recorded that path would keep
# running an old version after an update, then fail once the directory is pruned. The copy is
# refreshed from the plugin whenever the two differ (`install-git-hook --refresh`, which the
# plugin runs at session start), and only where the person installed it. Where another tool owns
# the hooks (`core.hooksPath`, as husky sets it) or someone else's pre-push hook is already there,
# nothing is written among the hooks: the copy is kept the same way, and `HOOK_LINE` is the one
# line to add to that hook, a no-op wherever the copy or python3 is missing.

HOOK_MARKER = "# fairmind-coding artifact push gate"
COPY_DIR = "fairmind"
COPIED = ("artifact_cards.py", "_artifact_worktree.py")
_COPY_CLI = "%s/%s" % (COPY_DIR, COPIED[0])
# `--git-common-dir` alone, resolved against the hook's working directory: a git older than
# `--path-format` (2.31) would echo that option back and the line would find no copy.
HOOK_LINE = ('f="$(git rev-parse --git-common-dir)/%s"; '
             'if [ -f "$f" ] && command -v python3 >/dev/null 2>&1; then '
             'python3 "$f" pre-push "$@" || exit $?; fi' % _COPY_CLI)

HOOK_TEMPLATE = """#!/bin/sh
%(marker)s (remove with: artifact_cards.py install-git-hook --uninstall)
# Runs the copy of artifact_cards.py kept in this git directory, which the plugin refreshes at
# the start of a session, so a pruned plugin directory cannot pull it away. git hands this hook the remote's name and the URL it
# pushes to, and every ref update on stdin; the copy measures them all and asks once for the
# whole push. Anything but a definite answer refuses: a missing copy, a missing python3 and a
# copy that fails are "could not check", never "nothing to check".
# A courtesy gate: `git push --no-verify`, another core.hooksPath, `git send-pack` and
# FM_ARTIFACTS_PUSH_OK=1 go around it.
here=$(cd "$(dirname "$0")/.." && pwd)
CLI="${FM_ARTIFACT_CARDS_CLI:-$here/%(copy)s}"
if updates=$(cat); then
  if [ -f "$CLI" ] && command -v python3 >/dev/null 2>&1; then
    printf '%%s\\n' "$updates" | python3 "$CLI" pre-push "$@"
    rc=$?
    case "$rc" in 0) exit 0 ;; 3) exit 1 ;; esac
    why="the artifact push gate failed (exit $rc)"
  else
    why="the artifact push gate cannot run: no python3, or no $CLI (run install-git-hook again)"
  fi
  printf '%%s\\n' "$updates" | while read -r lref lsha rref rsha; do
    case "$lsha" in *[!0]*) exit 1 ;; esac
  done && exit 0  # nothing but deletions: nothing is sent
else
  why="the ref updates git handed this hook could not be read"
fi
printf '%%s\\n' "$why; this push could carry working artifacts into a public repository." >&2
[ "$FM_ARTIFACTS_PUSH_OK" = 1 ] && exit 0
if (: </dev/tty) 2>/dev/null; then
  printf 'Push anyway? [y/N] ' >&2
  read -r answer </dev/tty
  case "$answer" in y|Y|yes|YES) exit 0 ;; esac
else
  echo "No terminal to ask on: set FM_ARTIFACTS_PUSH_OK=1 to push anyway." >&2
fi
exit 1
"""


def _hook_text():
    return HOOK_TEMPLATE % {"marker": HOOK_MARKER, "copy": _COPY_CLI}


def _ask_person(reasons):
    """Show why, and ask once: 0 pushes, 3 refuses. Without a terminal only
    FM_ARTIFACTS_PUSH_OK=1 lets the push through."""
    sys.stderr.write("\n".join(reasons) + "\n")
    if os.environ.get("FM_ARTIFACTS_PUSH_OK") == "1":
        return 0
    try:  # two streams: a terminal is not seekable, so one opened "r+" raises on it
        with open("/dev/tty", "w") as out, open("/dev/tty") as inp:
            out.write("Push anyway? [y/N] ")
            out.flush()
            answer = inp.readline().strip()
    except OSError:
        sys.stderr.write("No terminal to ask on: set FM_ARTIFACTS_PUSH_OK=1 to push anyway.\n")
        return 3
    return 0 if answer in ("y", "Y", "yes", "YES") else 3


def cmd_pre_push(args):
    """The git pre-push hook's work in one process: every ref update git hands over on stdin is
    measured against the destination, and the person is asked once for the whole push. The
    exit code is the answer, 0 to push and 3 to refuse; nothing escapes as an error."""
    reasons = []
    try:
        if sys.stdin is None or sys.stdin.isatty():
            # a hook manager that runs this with a terminal instead of git's ref lines
            reasons.append("the ref updates git hands a pre-push hook did not arrive on stdin "
                           "(lefthook needs `use_stdin: true`), so this push could not be checked")
            updates = []
        else:
            updates = [line.split() for line in sys.stdin.read().splitlines() if line.strip()]
        for fields in updates:
            if len(fields) != 4:
                reasons.append("a ref update git handed over could not be read: %s" % " ".join(fields))
                continue
            lref, lsha, _, rsha = fields
            if not lsha.strip("0"):
                continue  # an all-zero object name deletes the ref: nothing is sent
            ns = argparse.Namespace(repo=os.getcwd(), path_prefix=args.path_prefix, command=None,
                                    remote=args.url or args.remote_name, ref=lsha, base=rsha,
                                    refspec=None, all_branches=False, pending=False, shell=True)
            try:
                answer = cmd_push_gate(ns)
            except Exception as exc:  # noqa: BLE001 - a push that cannot be measured is an ask
                answer = {"reason": "%s: %s" % (type(exc).__name__, exc)}
            if answer.get("decision") != "pass":
                reasons.append("%s: %s" % (lref, answer.get("reason")
                                           or "the artifact push gate gave no usable answer"))
    except Exception as exc:  # noqa: BLE001
        reasons.append("the artifact push gate failed (%s: %s)" % (type(exc).__name__, exc))
    return _ask_person(reasons) if reasons else 0


def _common_dir(top):
    """The git directory a repository and all its linked worktrees share. `--git-common-dir`
    alone, resolved against `top`: a git older than `--path-format` echoes that option back."""
    rc, out, err = _git(top, "rev-parse", "--git-common-dir")
    if rc != 0 or not out.strip() or "\n" in out.strip():
        raise CliError("cannot locate the git directory: %s" % (err.strip() or out.strip()))
    return os.path.normpath(os.path.join(top, out.strip()))


def _hooks_owner(top):
    """Why the repository's hooks are not ours to write in, or "" when they are."""
    hooks_path = _git_config(top, "core.hooksPath")
    return "core.hooksPath is set to %s, so another tool owns the hooks" % hooks_path if hooks_path else ""


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read()
    except OSError:
        return None


def _replace_atomic(path, mode, fill):
    """Replace `path` whole or not at all, since two sessions may refresh it at once: `fill`
    writes the new content into a temporary file beside it."""
    fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), prefix=".fm-")
    os.close(fd)
    try:
        fill(tmp)
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def _write_hook(hook):
    def fill(tmp):
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(_hook_text())
    os.makedirs(os.path.dirname(hook), exist_ok=True)
    _replace_atomic(hook, 0o755, fill)


def _copy_is_current(copy_dir):
    try:
        return all(filecmp.cmp(os.path.join(copy_dir, name), os.path.join(_HERE, name), shallow=False)
                   for name in COPIED)
    except OSError:
        return False


def _write_copy(copy_dir):
    os.makedirs(copy_dir, exist_ok=True)
    if os.path.realpath(copy_dir) == os.path.realpath(_HERE):
        return  # running from the copy itself: there is nothing newer to take
    for name in COPIED:
        _replace_atomic(os.path.join(copy_dir, name), 0o644,
                        lambda tmp, src=os.path.join(_HERE, name): shutil.copyfile(src, tmp))


def cmd_install_git_hook(args):
    top = toplevel(args.repo)
    common = _common_dir(top)
    copy_dir = os.path.join(common, COPY_DIR)
    owner = _hooks_owner(top)
    hook = None if owner else os.path.join(common, "hooks", "pre-push")
    text = _read(hook) if hook else None
    ours = text is not None and HOOK_MARKER in text
    foreign = text is not None and not ours
    has_copy = os.path.isfile(os.path.join(copy_dir, COPIED[0]))
    current = has_copy and _copy_is_current(copy_dir)
    why = owner or ("%s is someone else's pre-push hook" % hook if foreign else "")
    if args.status:
        if why:
            state = "managed" if owner else "foreign"
        elif not ours:
            state = "absent"
        else:
            state = "installed" if text == _hook_text() and current else "outdated"
        status = {"state": state, "hook": hook, "copy": copy_dir if has_copy else None}
        if why:
            status.update(why=why, line=HOOK_LINE)
        return status
    if args.uninstall:
        removed = None
        if ours or (args.force and foreign):
            os.unlink(hook)
            removed = hook
        shutil.rmtree(copy_dir, ignore_errors=True)
        return {"removed": removed, "copy_removed": copy_dir if has_copy else None}
    if args.refresh:
        # Only what the person installed is kept in step, and never installed anew. The hook
        # goes before the copy, so a copy equal to the plugin's means a current hook: the
        # session-start check compares the copy alone.
        refreshed = []
        if ours and text != _hook_text():  # an older template, one that pinned the plugin's path included
            _write_hook(hook)
            refreshed.append(hook)
        if (has_copy or ours) and not current:
            _write_copy(copy_dir)
            refreshed.append(copy_dir)
        return {"refreshed": refreshed}
    _write_copy(copy_dir)
    if owner or (foreign and not args.force):
        return {"copied": copy_dir, "installed": None, "line": HOOK_LINE, "why": why}
    _write_hook(hook)
    return {"copied": copy_dir, "installed": hook}


def _read_stdin_json():
    try:
        return json.loads(sys.stdin.read())
    except ValueError as exc:
        raise CliError("stdin is not valid JSON: %s" % exc)


# ------------------------------------------------------------------------- main

def build_parser():
    ap = argparse.ArgumentParser(prog="artifact_cards.py", allow_abbrev=False,
                                 description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        p = sub.add_parser(name, help=help_, allow_abbrev=False)
        p.add_argument("--repo", default=os.getcwd(), help="a path inside the repository")
        p.add_argument("--path-prefix", action="append",
                       help="artifacts directory (repeatable); default from the checkout's config or the standard one")
        p.set_defaults(fn=fn)
        return p

    add("config", cmd_config, "effective artifacts paths")
    p = add("status", cmd_status, "state of one file")
    p.add_argument("--file", required=True)
    p = add("gate", cmd_gate, "must a write be confirmed first")
    p.add_argument("--file-path", required=True)
    p = add("push-gate", cmd_push_gate, "must a push be confirmed first")
    p.add_argument("--command", help="the shell line about to run: every literal git push the short "
                                     "grammar reads is measured, and the rest of the line passes "
                                     "unread (the other push options are then ignored)")
    p.add_argument("--cwd", help="the directory that line runs in (default: --repo)")
    p.add_argument("--ref", help="the commit being pushed (default HEAD)")
    p.add_argument("--base", help="the commit the remote already holds (default: the tracking ref)")
    p.add_argument("--remote", help="the remote name or URL being pushed to")
    p.add_argument("--refspec", action="append",
                   help="SRC[:DST] being pushed (repeatable); measured instead of HEAD")
    p.add_argument("--all-branches", action="store_true",
                   help="measure every local branch (git push --all)")
    p.add_argument("--pending", action="store_true",
                   help="also count artifact paths with staged, unstaged or untracked changes "
                        "(a commit made earlier in the same shell line carries them)")
    p.add_argument("--shell", action="store_true",
                   help="print only the reason; exit 3 on any answer but a pass")
    p = add("install-git-hook", cmd_install_git_hook, "install the pre-push gate")
    p.add_argument("--uninstall", action="store_true", help="remove the hook and the copy it runs")
    p.add_argument("--force", action="store_true", help="replace a pre-push hook that is not ours")
    p.add_argument("--status", action="store_true", help="report the hook and its copy; write nothing")
    p.add_argument("--refresh", action="store_true",
                   help="bring an installed hook and its copy in step with this script; never install")
    p = add("pre-push", cmd_pre_push, "the git pre-push hook's check: ref updates on stdin")
    p.add_argument("remote_name", nargs="?", help="the remote's name, as git hands it to the hook")
    p.add_argument("url", nargs="?", help="the URL the push goes to")
    add("visibility", cmd_visibility, "repository visibility")
    p = add("push-diff", cmd_push_diff, "artifacts a push carried")
    p.add_argument("--ref", help="commit-ish to read instead of the branch's remote-tracking ref")
    p.add_argument("--branch", help="remote branch name to report with --ref")
    p.add_argument("--remote", help="the remote the push went to (default: the one git pushes the branch to)")
    p.add_argument("--limit", type=int, help="report at most this many entries")
    p.add_argument("--restore-from-branch",
                   help="on the default branch, also report a path whose last filing came from this "
                        "branch (a closed pull request) and whose blob differs here")
    p = add("args", cmd_args, "Brain_tell arguments for one entry (JSON on stdin)")
    p.add_argument("--mode", choices=("check", "write", "gone"))
    p = add("blueprint", cmd_blueprint,
            "the report that files a checkout file as a linked document (JSON on stdin)")
    p.add_argument("path", help="the file, relative to the repository root")
    p.add_argument("--title", required=True)
    p.add_argument("--summary-file", required=True, help="a file holding the summary text")
    p = add("record", cmd_record, "mark entries filed (JSON on stdin)")
    p.add_argument("--declined", action="store_true",
                   help="mark them as declined: a new, changed or renamed entry is not reported again "
                        "until the file changes; a deletion of a filed card is reported again")
    add("state", cmd_state, "count of filed artifacts")
    return ap


def main(argv=None):
    ns = build_parser().parse_args(argv)
    try:
        result = ns.fn(ns)
    except CliError as exc:
        print(json.dumps({"error": str(exc)}, ensure_ascii=False))
        return 2
    except Refused as exc:
        print(str(exc), file=sys.stderr)
        return 3
    if ns.cmd == "pre-push":
        return result  # its exit code is the answer
    if getattr(ns, "shell", False):
        # only a definite pass is a pass: any other answer, malformed ones included, asks
        if result.get("decision") == "pass":
            return 0
        print(result.get("reason") or "the artifact push gate gave no usable answer: %r" % (result,))
        return 3
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
