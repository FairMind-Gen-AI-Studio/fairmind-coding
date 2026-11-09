---
name: brain-add-document
description: Use when a document worth keeping has come up in a session and should become something the company brain can cite - a standard the whole company follows, a design or blueprint you were given as a link, a specification you just wrote, an ADR export. It files the document in the brain with a summary YOU write, and refuses the cases that belong somewhere else. Also use right after publishing or writing a working artifact (a design note, a review, a report, an HTML page) and after a push that carries files under the artifacts path. Also use when asked to "put this in the brain", "remember this document", "record this standard", or when a decision you are recording rests on a document nobody has filed.
---

# Put a document where the brain can cite it

## Overview

A document the brain holds is one an agent can find later and quote a line from. The
expensive failure is not a missing document — it is a document that is *there* and
unfindable, because the one line describing it says nothing.

**That line is yours to write.** The door runs no model. Whatever you put in `summary`
is what a future search matches on and what a reader sees before deciding to open the
file; leave it out and the card carries the first 500 characters of the body, which for
a specification is its title page.

(The other direction exists and is not yours: a file someone uploads through Studio was
read by nobody, so the platform summarises it. That is why the summary is expected here
and produced there.)

**What leaves your machine when this skill writes.**
`mcp__Fairmind__Brain_tell` sends the document's title, the summary you wrote, the
repository name and project id, an agent label, and exactly one of: the document's **whole
body**, a link (with its version, the system it lives in and who may open it), or the id of a
file already uploaded — to the Fairmind platform your project's MCP entry points at. A document
kept in this repository goes through the same tool, by where the file lives in git
with no body at all. For that git form, everything that leaves is: the agent label, the intent
(a document), the project id, the title, the summary, the repository name, the path, the
previous path when the file was renamed, a marker that the path is gone when the file was
removed, the commit, the branch, the remote URL (without credentials), the blob id of the
file, the repository's visibility as the forge reports it, and the published URL when there is
one. The file's text is not among them. Unlike a decision, a document filed by link, by
`content` or by uploaded id **lands as a proposal**: it waits in the Review Queue until a
person confirms it. A document filed by its git location is the exception: its card is
confirmed on arrival, visible to the project's members at once, and rewritten in place
whenever you refresh it, with no Review Queue step; a card a person archived or rejected stays
closed and is answered `duplicate`. This is part of the **brain write-back**, and the plugin
README's *What leaves your machine* describes it in full under that name — alongside every
other route by which anything leaves.

**Announce at start:** "I'm using the brain-add-document skill to file <document> so the
brain can cite it."

## 0. Check the door is there

Confirm `mcp__Fairmind__Brain_tell` is in the tool list, by name. A server that predates the
platform release serving it answers `mcp__Fairmind__Studio_*` and `mcp__Fairmind__Insights_*`
normally and offers no such tool. If it is absent, say so and **stop** — do not reach for a
neighbouring door instead: a document recorded as a decision is a claim nobody made.

## 1. Decide whether it belongs here at all

Cases that look like this one; most belong somewhere else:

- **Source code.** A code file is already a FILE node in code ingestion, and the brain
  has no use for a summary of it. Cite the path instead.
- **A document kept in the repository** — a specification, an ADR, a design note, a
  working artifact an agent produced — is not a case to refuse. It is filed by the git
  form, the one `brain-seed` uses for the documentation files of a project's
  repositories: the card holds none of the file's text, so nothing drifts from the file,
  and it is what makes the file findable by meaning instead of by path. The file must be
  tracked, committed, unchanged since that commit and on a branch of the remote: when it
  is not, say so and stop, or file the person's link to it instead. A *working artifact*
  under the repository's artifacts path (the `artifacts` folder under `docs` by default)
  follows *Working artifacts* below; any other repository document is filed on request,
  as the `git` row of step 6 says.
- **A file someone uploaded through Studio.** It already has a card. Send its upload id as
  `source.document_id` here only to add the summary you wrote, and expect `proposed` rather
  than a silent edit when a person confirmed that card.
- **A decision, a requirement or an issue.** Those are told by `brain-record-decision` and
  `brain-new-requirement`. A document is the artefact; the claim it carries is the record. If
  what you want kept is "we decided X", tell the decision and put the document in its
  `documents`.
- **A project's brief, or the company's.** A `brief` sent here as content
  IS that scope's brief: the platform keeps one per scope, keyed on the project (or the
  company), so it would replace the brief readers are served — a draft rewritten in place, or a revision proposed
  against the one a person confirmed, superseding any revision already waiting — with none of
  the diff and scope checks a brief needs. Writing or regenerating a brief is `brain-onboard`'s
  generation mode; hand the request to that skill. A brief somebody handed you as a link or an
  uploaded file is an ordinary document: file it here and it is listed among the key
  documents.

## 2. Choose the scope, and notice the one thing this door can do

`project: "*"` means the whole company sees it. That is the case an upload cannot
express at all — an uploaded file always belongs to a project — so a coding standard, an
architecture principle or a company-wide template belongs here specifically, with `*` as the
project.

Pass the project's id when the document is about one product and would be noise elsewhere — the
`project_id` in `.fairmind/active-context.json`, which `/fairmind-connect` writes. Read it;
do not guess a project name.

## 3. Write the summary

**A link with a version may not need a summary from you.** Before you read it or ask for its
content, send the check of step 6: the link with its `version`, and no title and no summary.
`duplicate` means the card is current: report it and stop. Only `summary_needed: false`,
explicitly false, beside `changed` or `absent`, skips this step: a card of the same link, in this
project or another, already holds a summary of this version, so file the link in the **copy
form** of step 6 and write nothing. An answer without the key (an older server) or with
`summary_needed: true` means you write the summary, as below.

Two or three sentences, at most 1000 characters; the door refuses a longer text and never cuts
it. The test: **would someone who has not read the document know whether to open it?**

**You can only write this about a document you have read.** A link you were handed and
have not opened is not one: this skill does not fetch it, and a summary built from a
title is the unfindable card this skill exists to prevent. Ask for the content, or for a
description from someone who has read it, and say which of the two the summary rests on.

Say what the document IS and what it covers, in the document's own vocabulary — the
words a future search will be typed in. Name the parts it has, when they are what makes
it worth finding.

Do not:

- begin with "This document" — the card already says it is a document;
- evaluate it ("a thorough guide", "a useful reference");
- add anything the document does not say, including your reading of why it exists;
- summarise the topic instead of the artefact. "How authentication works" describes a
  subject; "the authentication design for the Studio API, covering token issue, refresh
  and the Cognito mapping" describes this document.

## 4. What it is evidence for is told by the record that rests on it

A document is not connected to anything by being filed: it is findable by search, and nothing
more. What it supports — the decision, requirement or issue that rests on it — is told by that
record, with the document in its `documents`. So when the person names the decision or
requirement a document supports, file the document here and hand the rest to
`brain-record-decision` or `brain-new-requirement`; a record told again with the document
added answers `duplicate` and merges the item. Leaving it unconnected is allowed and is a
choice.

## 5. Show what is about to leave, and ask

What you file waits in the Review Queue for a person to confirm (a git-form card of a file in
the repository is the exception: it is confirmed on arrival, see *Working artifacts*), but its title,
summary and body have left this machine the moment you call. So the question comes **before**
the call.

Show them, in a few lines: the title, the summary you wrote, **where it will be visible**
— the named project, or the whole company when the project is `*` — and **what travels**:
the document's whole body, only a link, or a reference to a file already uploaded. In the copy
form only the link and its version travel: the card takes the title and the summary the brain
already holds, so say that instead of showing a summary. Then wait for a yes. The check of step 3
is a call too, but it sends only the link and its version: a person who asked for this document to
be filed has said yes to it; when you started this skill on your own, ask before that check. Having been asked to "put this in the brain" is that yes for the document named,
and not for its scope: if you chose company-wide, say so and ask.

If you started this skill on your own — a document came up, a decision rests on one —
nobody has asked for anything yet. Ask.

For the files `push-diff` lists the order differs: the one ask for the batch comes before any
call to Fairmind, the check included, and it offers to show the titles and summaries first
(see *Working artifacts*).

## 6. Call it

Every call is `mcp__Fairmind__Brain_tell` with `intent: "document"`, `repository` (the bound
`repository_id`), `project` (the bound `project_id`, or `"*"` for the whole company), `title`
(one line naming the document), `summary` (yours, from step 3) and `agent`, plus exactly
**one** of these locations:

| Form | Location | When |
|---|---|---|
| link | `source: {"url": <where it lives>, "version": <its edit marker>, "system": <where it lives>, "visibility": <public\|private\|restricted>}` | the document lives elsewhere; nothing is fetched. `version` is the marker the source gives (a page's last-edited time, a file's version) and is left out when there is none; `system` names the tool that holds it; `visibility` is who the source itself says may open it, left out when it does not say and never inferred |
| git | built by `artifact_cards.py args` | a document kept in the repository (step 1). A working artifact: see *Working artifacts*. Any other repository document, once the person said yes (step 5): pipe its entry, with the committed file's blob id, the commit and branch that hold it and the `source_visibility` that `artifact_cards.py visibility` prints, to `artifact_cards.py args --mode check` and then to `artifact_cards.py args`, as *Working artifacts* describes them, and stop there: run neither `record` nor `push-diff` for it. Both are for the artifacts path, and a recorded path outside it is reported `gone` by the next `push-diff` on the default branch |
| upload | `source: {"document_id": <the uploaded file's id>}` | a file already uploaded to this project |
| inline | `content: <the document>` | you wrote it here and there is no link |

**Check first, when the document may be there already.** A link with its `version` and **no
title and no summary** asks the brain whether the document changed since it was filed: `duplicate` means
the card is current, `changed` that it is not, `absent` that the brain holds no card for it.
File it only on `changed` or `absent`. A call that carries a title or a summary is a filing, not a
check, so a title sent without a summary is refused. The check of step 3 is this call: do not send
it twice. Its answer holds for the version and the scope it was sent with: when the content you
were given is of another version, or the person changed the scope at step 5, check again with the
version and the scope you will file.

**Copy form**, only after a check that answered `summary_needed: false`: the link with its
`version`, `use_shared_summary: true`, and no `title` and no `summary`. The brain files the card
from the summary another card of the link holds for that version, in this project. Refused with
`code: summary_required` when that card is gone: nothing was stored or charged, so go back to
step 3 for the content and the summary, and show it and ask (step 5) before you send it.

**When the document can no longer be read**, send the
link with `"unreachable": true` in `source` and no title and no summary; the card is marked so.

The location forms are different claims about where the document is, and the door will not
guess: more than one, or none, is refused. A report that carries a field it does not take is
refused too; the plugin sends no document kind and no list of records the document supports.

## 7. Read what comes back

Report the `state` as the answer gives it, never as you expect it, with the `node_id`:

- `proposed` — filed, and waiting in the Review Queue. When a person had confirmed this card,
  your change is waiting for one: not a failure, and not done either — say so rather than
  reporting success. (A git-form card of a file in the repository, told through the same tool, is
  already confirmed and visible to the project's members, and a refresh rewrites it in place;
  one a person archived or rejected is answered `duplicate` and stays closed);
- `duplicate` — the same document was already there and **nothing changed**. Report it that
  way, never as written;
- `changed` or `absent` — the answer to a check, not a filing: act on it as step 6 says;
- `unreachable` — the card is marked as no longer readable;
- `conflict` — a different document holds this title. **Nothing was overwritten.** Show the
  person and, on a yes, send the report again with `revises` set to the `node_id` the answer
  returned;
- a refusal — quota, throttle, erasure fence, no such document, or more than one location.
  Each names itself; none of them is retried by repeating the call unchanged. A refusal that
  names a field of the report's **shape** means the plugin and the brain disagree on the
  report's shape: update the plugin, and do not send it again. An MCP error result on the tool
  means the brain may not offer `Brain_tell` yet, or the same disagreement.

## Working artifacts

Working with an agent produces documents: design notes, reviews, run reports, HTML pages.
Their durable copy is the repository, and a reference card is what lets the brain find them.
Everything that decides is in one script that does not depend on any particular coding agent;
a hook may call it for you, and when none does, you call it yourself:

```
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/artifact_cards.py <subcommand>
```

**Keeping the copy.** Keep a durable copy, in the repository's artifacts path, of every
artifact you judge worth keeping, and do it on your own. Ask the person when you are in
doubt. **Always ask in a public repository**: a document copied there is published by the next push.
`artifact_cards.py gate --file-path <path>` answers `pass` or `ask`: **any answer other than
`pass` means ask**, including an `error`, a non-zero exit, a timeout or no answer at all. A hook
may already ask before the write, and if you cannot tell that it did, run the gate yourself. A
repository whose visibility cannot be told counts as public. The artifacts path is the `artifacts`
folder under `docs` unless a config file lists others (`{"paths": ["design/notes/"]}`).
Commit `.fairmind-artifacts.json` at the repository root to set it for every clone;
`.fairmind/artifacts/config.json` is local to the checkout it sits in, is not committed, and overrides
it. A linked worktree reads both files in its own tree and in its main checkout, in this order: its own
local file, the main checkout's local file, its own committed file, the main checkout's committed file.

The gate sees writes made with the editor tools, not a copy made through the shell (`cp`,
a redirect, `git mv`). The push is the moment a file actually publishes, so
`artifact_cards.py push-gate` asks the same question for the commits about to be pushed (same
rule: anything other than `pass` means ask), and `artifact_cards.py install-git-hook` installs
it as a git `pre-push` hook that works for any agent and for a push from a terminal: git hands
that hook the URL it is about to push to and every ref update with the commit it sends (a tag
that alone holds an artifact commit included), and the hook measures exactly those.
`/fairmind-connect` offers it; how it stays current, and what it does where another tool owns
the hooks, is in the plugin README under `artifact-cards`.

**Every Bash command is read, and a git push it reads in full is measured, in a project that opted in.** The Claude Code hook hands each
Bash command to `push-gate --command "<the line>" --cwd <the directory it runs in>`. A line
is read command by command, inside a deliberately tiny grammar: commands joined by `;`, `&&`,
`||`, a pipe or a newline; quotes read as bash reads them, so inside single quotes everything is
text, and inside double quotes everything but `$`, a backtick and a backslash that escapes
something (a newline inside quotes is text, as in a commit message on several lines); no comment,
and outside quotes no `$`, no backtick, no backslash, no lone `&`, no `(` or `)` and no redirection other than a copy of one stream onto another
(`2>&1`) or `/dev/null`; each command a known read-only one (`ls`, `cat`, `grep`, `git status`,
`git log`, `git diff`, `git add`, `git commit` and a few more), a `cd` to a literal path (absolute
or starting with `./`, with no `~` and no `..`), or a literal `git push`, after `git -C <dir>` or not
(the same kind of path, relative or not), which is measured
in the directory it runs in. Reading stops at the first command outside the grammar: every push
before it is measured, and the rest of the line passes unread, whatever it mentions, because whether a
command publishes depends on what it runs and not on how it is spelled. So a push the line does not
spell out in full goes around this check: one after a command outside the grammar, one with an option
or a refspec the grammar does not read (`-c`, `--repo`, `--tags`, `--mirror`, `--follow-tags`, any
`--no-*` other than `--no-verify`; a glob, a variable, `tag <name>`, the matching refspec `:`), a
script, a make target or an alias, a library call such as `git.push()` inside `node -e` or
`python3 -c`, a command that builds a word at run time, and opening a pull request through the
GitHub CLI at a terminal, which pushes the head branch through git when the remote does not have
it yet (run without a terminal, as an agent runs it, it refuses to push and stops),
like the other ways round it named below. The git `pre-push` hook (`install-git-hook`) is what sees those:
git hands it the destination and the exact ref updates. A literal `git push` is measured against the remote it
goes to (a remote named by URL is that URL; an explicit `--remote`, then
`branch.<name>.pushRemote`, `remote.pushDefault`, the branch's own remote; when one of those
holds an address rather than a configured remote, the address is what is measured), never another
remote's copy of the branch: a branch the target does not have yet counts every commit none of
its refs reach, and a target that knows no refs at all counts the whole artifacts tree as
outgoing. A push it reads in full still asks when it runs
inside a git directory with no working tree (`.git`, a bare repository), when git's configuration
rewrites URLs (`url.*.insteadOf`, `pushInsteadOf`), gives a remote several URLs or chooses the refs
a bare push sends (`remote.<name>.push`, `remote.<name>.mirror`, `push.default=matching`). The
read-only commands are refused any option that writes a file (`git log --output`). A commit made earlier in the same
line (`git add -A; git commit ...; git push`) is counted by the files staged, unstaged or
untracked under the artifacts paths. Without `--command`, a bare `push-gate` measures the
checked-out branch; for any other push pass `--remote` and one `--refspec SRC:DST` per ref (or
`--all-branches`). A remote that is not on GitHub, or one reached through an SSH host alias or a
non-GitHub forge, has a visibility that cannot be told, so every push or write that carries an
artifact to it asks, always.

**This is a courtesy gate, not a security boundary.** It exists so that a push an agent spells
out in full is measured, not so that a push can be made impossible: `git push --no-verify`,
another `core.hooksPath`, `git send-pack`, a script the person runs, a program that git's configuration or the repository runs during an allowed git command (a hook
during `git add` or `git commit`, an external diff or textconv driver, `core.fsmonitor` or a clean
filter; any of them can write an artifact or push), the environment variable that
answers the git hook's prompt, and anything that edits the plugin all go around it. Where the
check has to hold, enforce it outside the agent's reach (branch protection and a server-side
check). A line the gate cannot read passes unchecked; the git `pre-push` hook is what sees a push
the line does not spell out. The gate applies only to a project that opted in: the repository (found by walking up from the
working directory, or from the written file, to the first `.git`) carries `.fairmind/active-context.json`,
`.fairmind/artifacts/config.json` or `.fairmind-artifacts.json`. A linked worktree counts as its main
checkout, so the marker there is enough; the artifacts paths there come from the same two files, local before committed and the worktree's own before the
main checkout's within each kind, so the main checkout's untracked `.fairmind/artifacts/config.json` beats the
worktree's committed `.fairmind-artifacts.json`. A submodule, a nested repository and a worktree of a bare
repository (silent whatever its parent directories carry, a bare repository stored as `<dir>/.git`
included, which is recognised by `core.bare` in its config file) are separate. The linked worktrees of a
checkout whose git directory lives elsewhere (`git init --separate-git-dir`) are a known gap: they are
not recognised as worktrees of it. Anywhere else the hook, `gate` and
`push-gate --command` pass silently and make no call to the forge. Opt-in is judged by the directory the
command starts in: `cd <opted-in repository> && git push` or `git -C <opted-in repository> push`, run from a
directory that did not opt in, is not seen; run from one that did, the push is measured in the repository
it names, opted in or not, and asks the forge for that repository's visibility when it carries files under
its artifacts path.

**Filing happens at push, not at commit**: a commit that exists only on this machine can be
read by nobody else, so the card names a commit the remote holds. After you push a
branch that carries files under the artifacts path, run `artifact_cards.py push-diff` with
`--remote R --ref R/B --branch B` for the ref the push sent (`--branch` takes a branch name,
never `HEAD`): nothing tells you a push carried artifacts, so look for yourself. Each entry says how one artifact
differs between the pushed tree and the local record of what was filed, so the list can also hold
artifacts never filed before, filings that failed and renames still to be finished:

| `action` | Meaning | What to send |
| --- | --- | --- |
| `new` | not filed from this checkout yet | a card |
| `changed` | the file's blob differs from the one last recorded here (a file declined as `new` and then edited comes back as this too) | a card, after the check: the platform is asked and a card is written only if none is current |
| `renamed` | git detects a rename; `renamed_from` names the old path | a card at the new path; the platform retires the old card and links the two, if it holds one |
| `gone` | a filed path left the default branch | the path-gone form, which archives the card, if the platform holds one |

**Limits of the list.** A file declined as `new` and later deleted or renamed is still offered as `gone` or `renamed`: when the platform holds no card for it, it answers that there is no such card and writes nothing, and the person may decline it. A decline is not offered again until the file changes, with two exceptions: a pure rename with an unchanged blob comes back as `renamed`, and a file deleted and added back with the same blob comes back as `new`.

When the report carries `default_branch_unknown: true`, neither the remote-tracking HEAD nor
the remote itself named its default branch (the remote may be unreachable): no `gone` is
reported and no branch name is guessed. Fix the remote (`git remote set-head <remote> -a`) and
run `push-diff` again.

`gone` is reported only for a push to the default branch: on any other branch the file still
exists on the default branch, so a deletion there is not reported and the card stays until a
push to the default branch reports `gone`.
Entries name the remote the push went to, so after a push to a fork the card names the fork,
where the commit it cites lives.

**Nothing is sent to Fairmind before the person has said yes.** Not a card, and not a check
either: the check is a call to the platform too. Listing is local, except that it may ask the
git remote for its default branch (`git ls-remote`) and GitHub for the repository's visibility
(`gh repo view`); neither is Fairmind. The order for a push is fixed:

1. **List.** `push-diff` names the entries. When the report carries `baseline_missing: true`
   (the record holds no row for this repository: a fresh clone, a linked worktree, a record of
   other repositories only, or one whose filed files were all removed), the artifacts the record does not hold
   come back as `new` (symlinks are not listed, and `truncated` means `--limit` cut the list):
   say so in the ask. Before the yes, do not call the platform once per file to find out which
   ones the brain already holds.
2. **Ask once for the whole batch**, as in this skill's step 5, before any call to the platform.
   Name the files (a `gone` entry archives its card, if the platform holds one: name those too). Say it is a
   reference card and name what leaves, as the list under *What leaves your machine* does: the
   file's text is not part of it. Offer to show the titles and summaries first. If the person
   asks to see them, write each title and summary now, locally, for every `new`, `changed` and
   `renamed` entry, from the committed file (`git show <commit>:<path>`, never the working-tree
   copy); show them with the files, and take the yes to cover that text, which items 3 and 4
   then send unchanged. If the person does not ask, say in the ask that the title and summary
   are free prose you will write from the committed file after the yes. Say too that filing a
   file whose card was retired earlier (for instance because its pull request was closed
   unmerged) files it again, which brings that card back.
   On a no, send nothing and pipe each entry to `artifact_cards.py record --declined` (below).
3. **After the yes, for each `new`, `changed` or `renamed` entry, check, then file only if the
   card is out of date.** A `gone` entry skips the check and goes straight to item 4. `artifact_cards.py args --mode check` (entry on stdin, plus `agent` and
   `project`, the `project_id` in `.fairmind/active-context.json`) gives the arguments for a
   check call: `Brain_tell` with `intent: document`, `source.git` and `source.version` and
   **no summary**. If it answers `duplicate`, the card is current: do not read or summarise
   the file, just `record` it (item 5 below), except in the restore flow of *Merge and
   close*, where `duplicate` means do not record. If it answers `absent`, the brain holds no
   card for the path: file it. An entry carrying `"resend": true` (a rename
   the platform could not finish retiring) skips the check: the check carries no rename and
   would answer that the card is current, so `args --mode check` refuses such an entry. When
   the check answers `changed`, use the title and summary the person already
   saw; when none were shown, write the summary exactly as in step 3, from the committed file:
   read it with `git show <commit>:<path>` using the entry's `commit` and `path`, never the
   working-tree copy, which may hold edits or another branch's text.
4. **File it.** Add `title` (the one shown, or one you write now), `summary` and, when the artifact is published
   somewhere, `published_url` to the entry; `artifact_cards.py args` (no `--mode`) prints
   the exact `Brain_tell` arguments for the git form, and for a `gone` entry the
   path-gone form. Pass them unchanged: never retype them, and never add `content`, `source.url`
   or `source.document_id` to a git-form call, which is refused. A card does not say what the
   document is evidence for: the decision or requirement that rests on it does, with the
   document as its related document (`brain-record-decision`, `brain-new-requirement`).
5. **Record it.** Only after the brain answered `confirmed`, `proposed` (a person had
   confirmed the card, so the change waits for one) or, for a `gone` entry, `retracted`,
   pipe the entry to `artifact_cards.py record`. That local state is what makes the next push-diff report
   only what changed since. A refused call is not recorded.
   For a `renamed` entry, read `renamed_from` in the answer before you record. When
   `renamed_from.archived` is true the old card is retired: record the entry as it is, and
   the old path's row goes. When `archived` is false and `retryable` is true the old card is
   still live: add `"retirement_pending": true` to the entry before you pipe it to `record`.
   The old path's row is then kept, and the next `push-diff` reports the same rename again,
   marked `"resend": true`: file it with `args` (no `--mode`) and send exactly what it prints,
   which carries `renamed_from`; never compose the rename yourself. The filing that finishes the
   archive clears the row. When `retry_recorded` is false, nothing on the new card remembers the
   rename, so only the same rename sent again retries it, which is what the resend is. An answer
   with `archived` false and no `retryable` (`exists` false: the old card was never there;
   `filed_again` true: the old card was written again since; or the old card is no longer
   active, such as a rejected one) needs no retry: record it as usual. If the old path's row is
   missing from the local state (deleted, or filed from another checkout), there is nothing to
   keep or resend: the retry is not offered, and the old card stays live until a `gone` for it.
   If the person declines, pipe each entry to `artifact_cards.py record --declined`: a `new`,
   `changed` or `renamed` entry is not offered again until the file changes (see the limits after the table). A declined `gone`
   entry keeps its row when a card was filed, because the platform retired nothing, so the
   deletion is asked about again whenever `push-diff` runs against the default branch (at once
   too; not on another branch, and not while the default branch is unknown); a decline of a
   `gone` entry for a file once declined as `new` drops its row, because this checkout holds no
   card for it, but a checkout with no record of its own (a fresh clone or a linked worktree)
   can hold such a row while another checkout filed a card, which a yes then archives. The old path of a
   declined `renamed` entry, a declined resend included, is a plain filed row again and comes
   back the same way as a deletion, which archives the old card, if the platform holds one, without linking it to the new path.

If `mcp__Fairmind__Brain_tell` is not in the tool list, or answers that it does not
accept `source.git`, the platform predates this form: say so and stop, as in step 0.

**Merge and close.** A card follows its pull request: when the pull request merges, the next
pull-request import moves the reference to the base branch, and when it closes without a
merge the cards are retired (retired, not deleted). A retired card is brought back only by its
file being filed again after the person's yes, and the ask says so; no seeding run does it. If
the file still exists on the default branch and the person says the pull request was closed
unmerged, run
`push-diff --ref <remote>/<default> --branch <default> --restore-from-branch <closed branch>`.
Without that flag, a path last filed from a branch that the default branch's history does not
contain is not reported when its blob differs, because the client cannot tell a closed pull
request from an open one; a filing that history does reach (its commit, or its blob after a
squash merge) is compared as usual. A path whose last filing came from
the named branch and whose blob differs on the default branch is then reported as `changed`;
file that entry through the list above, ask included (the check answers `changed`
for a retired card), then `record`. A path whose blob is identical on the default branch is not
reported: remove its
state row (`{"action":"gone","path":"<path>"}` piped to `artifact_cards.py record`) and run
`push-diff` again to get a `new` entry. If the check for that path answers
`duplicate`, the card is still live and not retired yet: do not `record` it (the
exception to item 3). Run
`brain-import-pull-requests` for the closed pull request first, then repeat. If the import
changes nothing and the check still answers `duplicate`, a person archived the
card: the platform keeps it archived and answers `duplicate` to a write. Do not `record`
it and do not repeat; tell the person the card stays archived by their choice.
After you merge or close one in this session, offer to run
`brain-import-pull-requests` for it; otherwise it happens at the next import.

**Not covered here.** A push made outside a session is filed at the next `push-diff`. An
artifact published outside this repository is filed only once a copy is committed under the
artifacts path. A push the gate cannot stop: `--no-verify`, `git send-pack`, a script, another
hooks path (see the courtesy-gate paragraph above).

## What this skill does not do

It does not fetch a link. The card records where the document lives, and a reader
follows it — so a link-only card can be found and cannot be quoted from. If a line of it
is ever going to be cited, pass `content`.
