# Sub-agent briefs for brain-seed

Purpose: the four briefs the skill hands to small-model sub-agents (`model: "haiku"`, general-purpose). Each sub-agent reads only through the source it is told to use, and writes JSON only to the file path it is given. `<work>` is the run's work directory the skill names. Paste a brief, fill the angle brackets, and add nothing else.

Common to all four:

- Read only what the brief names. Do not open anything outside the named spaces, and do not follow a link out of them.
- Write one JSON file to the path given, and print only a one-line count when done. No prose in the file.
- When a field is absent in the source, leave the key out. Never guess a value.
- Never use display names where an account id exists.

## 1. Document inventory

Task: list the documents inside `<the named spaces>` using the `<Notion|Confluence|Google Drive|GitHub>` connection. **Do not read page bodies.** Titles, links, ids and edit markers only. Notes for the source are in `<plugin root>/skills/brain-seed/references/document-sources.md`.

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

Task: list the tasks of `<tracker project>` that were **updated inside the last `<N>` days**, through the tracker's connection, with `<plugin root>/skills/brain-seed/references/tracker-<tracker>.md` open for the field names. Read titles and fields; do not open comments.

Write `<work>/inventory-tasks.json`, a JSON list of rows:

```
{
  "source": "jira|clickup|linear|azure_devops",
  "id": "<the permanent id>",
  "key": "<the readable key, when the tracker has one>",
  "title": "<one line>",
  "url": "<the task's own address>",
  "type": "<the tracker's own type name>",
  "updated": "<ISO-8601 updated time>",
  "pull_request_urls": ["<pull requests the tracker itself links to>"],
  "cited_links": ["<links to documents found in the task text>"]
}
```

## 3. Document summariser

Task: for each row given (`<rows>`: the `file` path, `link`, `source`, `title`), open the document through its connection, read it, and write the row's `file` as:

```
{"link": "<unchanged>", "source": "<unchanged>", "title": "<one line>", "doc_kind": "spec", "summary": "<two or three sentences>"}
```

Rules:

- `doc_kind` is exactly one of `spec`, `design`, `blueprint`, `manual`, `wiki`, `note`, `adr_export`. Never any other value.
- The summary says what the document is evidence for: what a later reader could cite it for. Two or three sentences, written in your own words, no quotation of long passages.
- As a precaution, describe a relative path or a local address in words rather than writing it out in the title or the summary. The hosting edge in front of the platform refuses some text patterns before they reach it, and its rules can change.
- Do not copy the link into the summary. Do not include secrets, tokens or personal data you came across.
- A row marked `local: true` is a file with no address: also write `"content"`, the whole text of the file, never shortened.
- A document you cannot open: write `{"link": "<unchanged>", "error": "<why>"}` and move on.

## 4. Task shaper

Task: for each row given, read the ticket **and its comments** through the tracker's connection, and write the row's `file` as exactly the door's `item`, following `<plugin root>/skills/brain-seed/references/tracker-<tracker>.md` (the skill gives you the plugin root path):

```
{
  "source": "...", "id": "<permanent id>", "key": "...", "url": "...",
  "project_key": "...", "kind": "issue|requirement|task", "type": "...",
  "title": "...", "description": "<whole>",
  "status_category": "todo|in_progress|done|cancelled", "status": "...",
  "author": {"login": "<account id>", "is_bot": false},
  "assignees": [{"login": "<account id>", "is_bot": false}],
  "created_at": "<ISO-8601>", "closed_at": "<ISO-8601>",
  "comments": [{"id": "...", "author": {"login": "<account id>", "is_bot": false}, "body": "<whole>", "created_at": "<ISO-8601>"}],
  "comment_count": 0,
  "parent": {"id": "...", "kind": "..."},
  "pull_request_urls": ["<the tracker's own links>"],
  "commit_shas": ["..."]
}
```

Rules:

- `id` is the permanent id, never the readable key. Leave a field out rather than guess it.
- `assignees` has at most 20 entries. `pull_request_urls` has at most 50.
- `comments` are oldest first, at most 99, each whole. Set `comment_count` to the real total when more exist than you wrote.
- Rich text is rendered to plain text without rewording. Do not summarise, shorten or correct anything.
- The door accepts 500,000 characters of description plus comments. If the total is over, drop trailing comments and set `comment_count`; never cut a comment in the middle.
- Never use display names. A person is identified by the tracker's account id.
