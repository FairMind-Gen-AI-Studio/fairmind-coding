# Shaping Linear tasks for the brain

Purpose: how to fill the task door's `item` from a Linear ticket, with the field names the tracker's connection returns. Every mapping is as the tracker's connection returns it; **when a field is absent, leave it out** and never guess it. The item's full shape is in `subagent-briefs.md`, brief 4.

| Field | Where it comes from |
|---|---|
| `source` | `linear` |
| `id` (**permanent**) | the issue's UUID |
| `key` (readable) | the `identifier`, `ENG-123` |
| `url` | the issue's own address |
| `project_key` | the team key (`ENG`) |
| `type` | leave it out unless the workspace uses issue types |
| `kind` | a Bug label becomes `issue`; a Feature or Story label becomes `requirement`; anything else becomes `task` |
| `status_category` | from `state.type`: `triage`, `backlog` and `unstarted` become `todo`; `started` becomes `in_progress`; `completed` becomes `done`; `canceled` becomes `cancelled` |
| `status` | the state name |
| account id | the user's `id` for the creator, assignee and each comment author, never the name or email. `is_bot` is true for an app user |
| dates | `createdAt`, and `completedAt` or `canceledAt` for `closed_at`, as ISO-8601 |
| `parent` | the parent issue's id, and its `kind` mapped with the `kind` row above, as for the item itself (the door accepts only `task`, `issue` or `requirement`) |
| linked pull requests | the GitHub attachments on the issue |
| key form in pull requests | the identifier, in any case, because branches are lower case (`eng-123-fix-login`); what `ticket_links.py` matches |

Notes:

- Linear descriptions are markdown; keep them as returned, without rewording.
- `author.login`, each assignee's `login` and each comment's author are **account ids**, so that one person is one identity across the cards and an admin can erase by it. Never display names.
- `description` and every comment body are whole and in order. Do not shorten, summarise, reword or correct.
- `id` is the permanent id and never the readable key: the brain keys the card by it, so a renamed key does not make a second card.
