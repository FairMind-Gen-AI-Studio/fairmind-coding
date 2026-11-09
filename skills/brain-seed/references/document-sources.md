# Document sources for brain-seed

Purpose: for each place a document can live, what to ask the person for, which answer field is the page id and which is the edit marker, and which `source` value to pass to the document door as its `system`. Each section is read by the inventory sub-agent and by the main session.

Common to all sources:

- **Inventory reads only listings and metadata, never bodies.** The summariser sub-agent opens a document only after the person ticked it.
- The edit marker feeds the ledger: a document is sent again only when its marker changed. A document with no marker is sent once and then left alone.
- Link spellings are normalised by `<plugin root>/scripts/link_normalize.py`, which builds a link from the page id only when the answer carries none. The brain keys a document by the link exactly as sent, so one spelling per page is what makes a re-run update a card instead of making a second one.
- Nothing outside the spaces the person named is listed or read.
- `source_visibility` is who the source itself says can read the page: `public` (anyone on the internet), `private` (only the people it is shared with, or the members of the space) or `restricted` (limited to part of the workspace or organisation). It is written on the summariser's file only when the connection reports it plainly, and is otherwise left out. It is never inferred from the space's name or the page's content.

## Notion

- **Ask for:** a page tree (the root page, whose children are listed) or a database.
- **Page id:** the page's `id` in the answer. **Edit marker:** `last_edited_time`.
- **`source` to pass:** `notion`.
- **`source_visibility`:** a page published to the web is `public`; otherwise leave it out, because the connection does not report a page's sharing plainly.
- The link is the page's address as the connection returns it; the normaliser drops the title slug, the view parameters and tracking parameters. Built from the id only when the answer has no address.

## Confluence

- **Ask for:** the site host (for example the company's `atlassian.net` site) and a space key, or a root page.
- **Page id:** the numeric page `id`. **Edit marker:** `version.number`.
- **`source` to pass:** `confluence`.
- **`source_visibility`:** a space the connection reports as publicly accessible is `public`; a personal or restricted space is `private`; otherwise leave it out.
- Keep `site` on the inventory row: it lets the normaliser build `/wiki/...?pageId=` when only an id is known. A short (`/x/...`) or `display` form link whose id is unknown is marked `needs_id`; ask the sub-agent for the id rather than sending the short form.

## Google Drive

- **Ask for:** a folder, or named files. Documents, slides and PDFs are fine; skip spreadsheets of raw data.
- **File id:** the file's `id`. **Edit marker:** `modifiedTime`.
- **`source` to pass:** `google_drive`.
- **`source_visibility`:** a file shared with anyone who has the link is `public`; one shared only with named people or groups is `private`; one shared with the whole domain is `restricted`; otherwise leave it out.
- The normaliser reduces every form (edit, view, preview, open) of one file to a single link by its file id.

## GitHub

- **Ask for:** repositories and paths (a `docs/` directory, a wiki, a design file), as `owner/name` plus a path.
- **Id:** none beyond the path. **Edit marker:** the file's blob sha.
- **`source` to pass:** `github`.
- **`source_visibility`:** the repository's own word: `public` is `public`, `private` is `private`, an internal repository is `restricted`.
- Give the inventory row `repo`, `path` and `ref` when there is no URL; the normaliser builds a blob link from them.
- **The project's repositories are a source of their own.** The documentation files kept under `docs/`, `specs/`, `adr/` and `design/` at the root of every repository of the project are listed by `repo_docs.py list`, never by a sub-agent, and read through `repo_docs.py read` at the commit the listing names. A file is filed as a reference card with a git location (repository, path, commit, branch, blob id, the repository's visibility, a summary written here and none of the file's text), with the arguments `seed_ledger.py next` hands out, which `artifact_cards.py` builds in the same form `brain-add-document` uses. It is confirmed on arrival; it is not a proposal. Its edit marker is the blob id.
- A GitHub file of a repository that is not one of the project's is filed here by its link, like any other page, and lands as a proposal. This skill does not look for files that left a repository.
- A file of one of the project's repositories is never filed as a page by its link. It is a repository file, listed by `repo_docs.py list` and filed as the card above; a link to a file outside the folders that script lists is left out of the plan, and the person is told this skill does not file it. Working artifacts kept under the repository's artifacts path are listed with the rest. The brain identifies a repository file by the repository it resolves and the path; this skill binds the repository by the project's catalog id or name and `brain-add-document` by the checkout's, so whether the two meet in one card is not established here.
- Nor does this skill look for cards retired when a pull request closed unmerged. Retiring a card whose path is gone happens at a push to the default branch, through `brain-add-document`; a retired card comes back only by its file being filed again there after the person's yes, and the ask names that effect.

## Web

- **Ask for:** the page URLs. Nothing is crawled; only the pages named are read.
- **Id:** none. **Edit marker:** none, so a web page is sent once and left alone on later runs unless the person asks for everything again.
- **`source` to pass:** none. Leave `source` out of the call; the card records the system as `web`.
- **`source_visibility`:** leave it out: a page that anyone can open is not thereby declared public by its owner.
- The normaliser lower-cases the host, drops tracking parameters and a trailing slash, and keeps other query parameters.
