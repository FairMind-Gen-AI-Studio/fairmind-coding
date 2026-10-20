# Document sources for brain-seed

Purpose: for each place a document can live, what to ask the person for, which answer field is the page id and which is the edit marker, and which `source` value to pass to the document door. Each section is read by the inventory sub-agent and by the main session.

Common to all sources:

- **Inventory reads only listings and metadata, never bodies.** The summariser sub-agent opens a document only after the person ticked it.
- The edit marker feeds the ledger: a document is sent again only when its marker changed. A document with no marker is sent once and then left alone.
- Link spellings are normalised by `<plugin root>/scripts/link_normalize.py`, which builds a link from the page id only when the answer carries none. The brain keys a document by the link exactly as sent, so one spelling per page is what makes a re-run update a card instead of making a second one.
- Nothing outside the spaces the person named is listed or read.

## Notion

- **Ask for:** a page tree (the root page, whose children are listed) or a database.
- **Page id:** the page's `id` in the answer. **Edit marker:** `last_edited_time`.
- **`source` to pass:** `notion`.
- The link is the page's address as the connection returns it; the normaliser drops the title slug, the view parameters and tracking parameters. Built from the id only when the answer has no address.

## Confluence

- **Ask for:** the site host (for example the company's `atlassian.net` site) and a space key, or a root page.
- **Page id:** the numeric page `id`. **Edit marker:** `version.number`.
- **`source` to pass:** `confluence`.
- Keep `site` on the inventory row: it lets the normaliser build `/wiki/...?pageId=` when only an id is known. A short (`/x/...`) or `display` form link whose id is unknown is marked `needs_id`; ask the sub-agent for the id rather than sending the short form.

## Google Drive

- **Ask for:** a folder, or named files. Documents, slides and PDFs are fine; skip spreadsheets of raw data.
- **File id:** the file's `id`. **Edit marker:** `modifiedTime`.
- **`source` to pass:** `google_drive`.
- The normaliser reduces every form (edit, view, preview, open) of one file to a single link by its file id.

## GitHub

- **Ask for:** repositories and paths (a `docs/` directory, a wiki, a design file), as `owner/name` plus a path.
- **Id:** none beyond the path. **Edit marker:** the file's blob sha.
- **`source` to pass:** `github`.
- Give the inventory row `repo`, `path` and `ref` when there is no URL; the normaliser builds a blob link from them.
- A file of **this checkout's own repository** that the code graph already holds is cited by path, not filed: filing it gives the same text two representations that drift apart. Check with `mcp__Fairmind__Code_tree` when unsure, and leave the row out of the plan.

## Web

- **Ask for:** the page URLs. Nothing is crawled; only the pages named are read.
- **Id:** none. **Edit marker:** none, so a web page is sent once and left alone on later runs unless the person asks for everything again.
- **`source` to pass:** none. Leave `source` out of the call; the card records the system as `web`.
- The normaliser lower-cases the host, drops tracking parameters and a trailing slash, and keeps other query parameters.
