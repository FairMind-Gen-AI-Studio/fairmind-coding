# Sub-agent briefs for brain-seed

Purpose: the seven briefs the skill hands to small-model sub-agents (`model: "haiku"`, general-purpose). Each sub-agent reads only through the source it is told to use, and writes JSON only to the file path it is given. `<work>` is the run's work directory the skill names. Paste a brief, fill the angle brackets, and add nothing else.

Common to all seven:

- Read only what the brief names. Do not open anything outside the named spaces, and do not follow a link out of them.
- Write one JSON file to the path given, and print only a one-line count when done. No prose in the file.
- When a field is absent in the source, leave the key out. Never guess a value.
- Never use display names where an account id exists.

## 1. Document inventory

Task: list the documents inside `<the named spaces>` using the `<Notion|Confluence|Google Drive|GitHub>` connection. The documentation files kept in the project's own repositories are not listed by a sub-agent: `repo_docs.py list` does it. **Do not read page bodies.** Titles, links, ids and edit markers only. Notes for the source are in `<plugin root>/skills/brain-seed/references/document-sources.md`.

Write `<work>/inventory-documents.json`, a JSON list of rows:

```
{
  "url": "<the link the connection returned, as returned>",
  "page_id": "<the source's own id for the page>",
  "source": "notion|confluence|google_drive|github|web",
  "site": "<Confluence site host, when the source is Confluence>",
  "repo": "<owner/name, GitHub only>",
  "path": "<path inside the repository, GitHub only>",
  "ref": "<branch or commit, GitHub only; required with repo and path, the same for every row>",
  "local_path": "<path of a file with no address, in place of url>",
  "title": "<one line>",
  "edited": "<the source's edit marker, see document-sources.md>",
  "space": "<the space, folder or repository it was found in>",
  "hint": "stable | minutes | draft | unknown"
}
```

`url` may be absent when the connection returns only an id; keep `page_id` and `source` then. `edited` is left out when the source has no marker. `hint` is a guess from the title and place only (manuals, architecture and specifications are `stable`; meeting notes are `minutes`).

## 2. Task inventory

Task: list the tasks of each of `<the tracker projects, lists or epics>` that were **updated inside the last `<N>` days** (for a project or a list; an epic is not cut by the window, see below), through the tracker's connection, with `<plugin root>/skills/brain-seed/references/tracker-<tracker>.md` open for the field names. An epic is itself a task, given by its id or link: list the epic and its whole subtree, whatever the dates of the tasks in it: the window of days does not cut an epic's subtree (ClickUp: its subtasks at every level; Jira: the child issues of the epic and their subtasks; Linear: the sub-issues at every level), and give each row its `parent` (`{"id", "kind"}`) when the tracker names one. A task found under two roots is one row. Read titles and fields; do not open comments.

Write `<work>/inventory-tasks.json`, a JSON list of rows:

```
{
  "source": "jira|clickup|linear|azure_devops|notion",
  "id": "<the permanent id>",
  "key": "<the readable key, when the tracker has one>",
  "title": "<one line>",
  "url": "<the task's own address>",
  "type": "<the tracker's own type name>",
  "updated": "<ISO-8601 updated time>",
  "parent": {"id": "<the parent task's permanent id>", "kind": "<its kind>"},
  "pull_request_urls": ["<pull requests the tracker itself links to>"],
  "cited_links": ["<links to documents found in the task text>"]
}
```

## 3. Document summariser

Task: for each row given (`<rows>`: the `file` path, `link`, `source`, `title`), open the document through its connection, read it, and write the row's `file` as:

```
{"link": "<unchanged>", "source": "<unchanged>", "title": "<one line>", "summary": "<two or three sentences>", "version": "<the page's edit marker>", "source_visibility": "public|private|restricted"}
```

Rules:

- `version` is the page's edit marker as the connection reports it (`document-sources.md` names it per source): leave the key out when the connection gives none. Never invent one.
- The summary says what the document is evidence for: what a later reader could cite it for. Two or three sentences, written in your own words, no quotation of long passages.
- As a precaution, describe a relative path or a local address in words rather than writing it out in the title or the summary. The hosting edge in front of the platform refuses some text patterns before they reach it, and its rules can change.
- Do not copy the link into the summary. Do not include secrets, tokens or personal data you came across.
- A row marked `local: true` is a file with no address: also write `"content"`, the whole text of the file, never shortened.
- `source_visibility` is who the source says can read the page (see `document-sources.md`): leave the key out when the connection does not say. Never infer it.
- A row marked `git: true` is a file kept in a repository, already copied to the `read_path` you are given. Read only that file: no link, no connection, nothing else. Write `{"title": "<one line>", "summary": "<two or three sentences, at most 1000 characters>"}` to the row's `file` and nothing else; if the write form refuses the summary and you are asked to rewrite it, keep it within the limit named; the row already carries where the file is kept.
- A document you cannot open: write `{"link": "<unchanged>", "error": "<why>"}` and move on.

## 4. Task reader

Task: for each row given, read the ticket **and its comments** through the tracker's connection, and leave what you read in a directory `<work>/tasks/parts/<source>-<id>/`, following `<plugin root>/skills/brain-seed/references/tracker-<tracker>.md` (the skill gives you the plugin root path). A script (`ticket_card.py assemble`) turns the directory into the raw ticket and a second one into the card; you write no summary, you shape nothing, and you never type a ticket's text inside JSON. The directory holds:

- `ticket.json`, the fields, with no text in it:

```
{
  "source": "...", "id": "<permanent id>", "key": "...", "url": "...",
  "project_key": "...", "kind": "issue|requirement|task", "type": "...",
  "title": "...", "description_file": "description.txt",
  "status_category": "todo|in_progress|done|cancelled (null only for a Notion page)", "status": "<the source's own word>",
  "visibility": "public|private|restricted",
  "author": {"login": "<account id>", "is_bot": false},
  "assignees": [{"login": "<account id>", "is_bot": false}],
  "created_at": "<ISO-8601>", "closed_at": "<ISO-8601>",
  "comments": [{"id": "...", "author": {"login": "<account id>", "is_bot": false}, "created_at": "<ISO-8601>", "body_file": "comment-1.txt"}],
  "parent": {"id": "...", "kind": "..."},
  "pull_request_urls": ["<the tracker's own links>"],
  "commit_shas": ["..."]
}
```

- `description.txt`, the description, and one `comment-<n>.txt` per comment (named by `body_file`): each a plain text file holding that one text, character for character as the tracker's connection returned it.

Rules:

- `id` is the permanent id, never the readable key. Leave a field out rather than guess it.
- `assignees` has at most 20 entries, `pull_request_urls` at most 50 and `commit_shas` at most 250: a list over its limit makes the script leave the task out, so cut nothing and say so in your count line.
- Comments are listed oldest first and every one gets a file, each whole: the version of the task is computed from all of them. A comment whose author you cannot identify has `"author": null`. A comment with no text still has a `body_file`, naming an empty file: a comment without one makes the whole ticket be left out.
- A text file is a copy, not a rewrite. Do not render rich text, convert markup, summarise, shorten, correct, re-wrap or tidy anything; if the connection returns markup, the file holds the markup. The version of a task is computed from these files, so a copy that differs from the last run's changes the version and the task is summarised again.
- Never use display names. A person is identified by the tracker's account id.

## 5. Card writer

Task: `<reading file>` is the local reading file of one task or pull request (`read.md`), or `<list>` names several items, each with its reading file and the `words.json` path beside it. Read each reading file, and write each `<dir>/words.json` and nothing else:

```
{"summary": "<1 to 1000 characters>", "rationale_points": [{"text": "<1 to 300 characters>", "file_path": "<optional>"}]}
```

Rules:

- The summary says what the work is and why it was done, and where it stands or how it ended. A rationale point is one reason, objection or agreement that a later reader would want to know; at most five.
- `file_path` appears only on a pull request's point that is about one of its changed files or the file of one of its review threads, spelled exactly as the reading file spells it: as the changed-files line lists it, or as the thread's heading names it, without its line number. A path with a directory added or dropped names another file and is refused. A task has no `file_path`.
- Write in your own words. Quote nothing: a text that repeats twelve or more words in a row from the reading file, or a whole short paragraph or comment of it, or that looks like a credential, is refused by the tool that stages the card.
- Refer to people by role ("the author", "a reviewer", "the reporter"), never by name, login or account id.
- Leave out secrets, tokens, internal addresses and personal data you came across.
- Do not add `implements` or `resolves`, and no other key.
- The reading files and `words.json` paths you were given are the only files you open and the only files you write. Never list, search or glob the work directory, never open another item's files, even to compare or to check, and write no other file: no draft, temporary or backup copy.
- To check your work, read back the `words.json` files you wrote and nothing else. The session checks each one when it stages the card and hands back what needs fixing.
- Your count line says how many `words.json` files you wrote. Count them again before you print it.

## 6. Reference proposer

Task: `<batch>` is a list of pull requests, each with a `read` path and a `proposal` path. The project's tracker is `<tracker>` (one of `jira`, `clickup`, `linear`, `azure_devops`, `notion`): label every reference with it, and leave out a reference to any other system. For each pull request, read the file at `read` and list every ticket of that tracker it refers to, in whatever form the team writes one: `PROJ-123`, `ENG-42`, a bare ClickUp id, `AB#123`, a link to a Notion page, a link to a ticket. Write the file at `proposal`, a JSON list with one entry for each reference:

```
{"tracker": "<tracker>", "id": "<the id>", "span": "<the words that hold it>"}
```

What `id` is, by tracker:

- `jira`: the issue key, `PROJ-123`.
- `linear`: the identifier, `ENG-123`.
- `azure_devops`: the number alone. `AB#123` has the id `123`, and the span is `AB#123`.
- `clickup`: the task id, `9hx2k7q1z`, or the workspace's custom id as written. In `CU-9hx2k7q1z` the id is `9hx2k7q1z` and the span keeps the `CU-`.
- `notion`: the page id, 32 hexadecimal characters, with or without dashes, as it is written at the end of the page's link; never the title that comes before it.

Rules:

- `span` is copied character for character from ONE line of the file: no line break inside it, nothing corrected, re-spaced or shortened. It contains the whole `id`.
- `id` is the id exactly as written in the span, by the rules above. Never normalise it, never add a prefix the text does not have, never turn a key into another form.
- When you cannot tell that a reference belongs to `<tracker>`, leave it out.
- A ticket is a reference only when the pull request works on it, discusses it or gives it as the reason for the change. An id that appears only as an entry of a list the change generates, regenerates or reproduces (an index, a changelog, a report or a table built from the tracker), or of a list of the entries such a change adds or removes, is not a reference: leave it out. A pull request whose change is such a list refers to no ticket, unless its title or description names one as the reason for it.
- Write `[]` when the file names no ticket. Never guess: a number that might be a ticket, a version or a count is not one without words that say so.
- Write exactly the `proposal` path given for each pull request, one file each, and nothing else. Print only a one-line count when done.

## 7. Cited-ticket reader

Task: `<lookup>` lists ticket ids by tracker (`<work>/refs/lookup.json`). Read each id through the tracker's connection, by id, with `<plugin root>/skills/brain-seed/references/tracker-<tracker>.md` open for the field names. An id may be a readable key or a permanent id: look it up as the tracker allows. For each ticket found, also read its parent, and the parent's parent, up to the top. Do not open comments and do not read the description.

Write `<work>/cited-tasks.json`, a JSON list of rows in the same shape as brief 2 (`source`, `id` as the permanent id, `key` when the tracker has one, `title`, `url`, `type`, `updated`, and `parent` when the tracker names one), one row for each ticket and each ancestor, a ticket met twice written once. `pull_request_urls` and `cited_links` are left out.

Rules:

- An id of a tracker you have no connection to has no row. A ticket the tracker does not hold, or that you cannot open, has no row. Never invent a row and never fill a field from the id.
- Write no text beyond the fields of the row: no description, no comment, no summary.
- The window of days does not apply: read the ticket whatever its date.
- Write the file even when no ticket was found (`[]`). Print only a one-line count when done.
