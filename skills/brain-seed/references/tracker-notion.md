# Shaping Notion pages for the brain

Purpose: how to fill the ticket directory (`ticket.json` and its text files) that `ticket_card.py assemble` and `shape` turn into the task door's card, from a page of a Notion database that the team uses as a tracker (tasks, requirements, a backlog), with the field names the Notion connection returns. Every mapping is as the connection returns it; **when a field is absent, leave it out** and never guess it. The directory's full shape is in `subagent-briefs.md`, brief 4.

A Notion database has no fixed schema: the property names below are the usual ones, and the person's own database may call them something else. Use the property that plainly plays the role, and leave the field out when none does.

| Field | Where it comes from |
|---|---|
| `source` | `notion` |
| `id` (**permanent**) | the page's id as a lowercase dashed UUID (`8-4-4-4-12`). The same page is spelled with and without dashes by different calls; the brain keys the card by the dashed form, so always send that |
| `key` | leave it out: a Notion page has no readable key, and a bare title would match too much in a pull request |
| `url` | the page's own address |
| `type` | the database's own name for the kind of page when it has one (`page` otherwise) |
| `kind` | a bug or incident type becomes `issue`; a requirement, story, epic or feature type becomes `requirement`; anything else becomes `task` |
| `status` | the Status (or Stage, State) property's option name, verbatim: it is the source's own word, and it is part of the page's version |
| `status_category` | only when the whole option name plainly means one of `todo` (not started, backlog, to do), `in_progress` (in progress, in review, doing), `done` (done, shipped, complete) or `cancelled` (cancelled, won't do). A name that is not plainly one of these (`Backlog Grooming`, `Blocked by legal`) is **null**: write `null` (or leave the key out), keep the name in `status`, and the brain applies its default: `todo`, or `accepted` for a `requirement`. The card keeps the word and the write answer carries `status_defaulted: true`. Never map it by guesswork |
| account id | `notion:<user id>`, the Notion user's id as a lowercase dashed UUID (`8-4-4-4-12`, the form of a page id) prefixed with `notion:`, for the creator, each person in an assignee property and each comment author, never the display name or the email. `is_bot` is true for a bot user |
| dates | `created_time` for `created_at`, as ISO-8601; leave `closed_at` out unless the database has a plain completion date property |
| `visibility` | leave it out: the connection does not report a page's sharing plainly, and it is never inferred |
| `parent` | the page a Parent or Relation property points to: its id (dashed UUID), and its `kind` mapped with the `kind` row above, as for the page itself (the door accepts only `task`, `issue` or `requirement`). A page with no such property has no parent: leave `parent` out |
| linked pull requests | the pull request addresses found in a URL or relation property for pull requests, when there is one |

Notes:

- `comments` are the page's discussion comments, oldest first, each with its author (`notion:<user id>`), its `created_at` and its whole text. The page body is the `description`; keep it as the connection returns it; do not render or convert it.
- The description (`description.txt`) and every comment body (one `comment-<n>.txt` each) are whole and in order. Do not shorten, summarise, reword or correct: the version of the page is computed from them, and they stay in a local reading file; they are never sent.
- A page the connection cannot read (a sign-in is asked for) is skipped, not guessed.
