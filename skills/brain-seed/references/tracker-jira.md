# Shaping Jira tasks for the brain

Purpose: how to fill the task door's `item` from a Jira ticket, with the field names the tracker's connection returns. Every mapping is as the tracker's connection returns it; **when a field is absent, leave it out** and never guess it. The item's full shape is in `subagent-briefs.md`, brief 4.

| Field | Where it comes from |
|---|---|
| `source` | `jira` |
| `id` (**permanent**) | the issue's numeric `id`, as a string. The key can change when an issue moves between projects; the id does not |
| `key` (readable) | the issue key, `PROJ-123` |
| `url` | the issue's browse address on the site |
| `project_key` | the project key (`PROJ`) |
| `type` | the issue type name, as the tracker returns it |
| `kind` | Bug becomes `issue`; Story or Epic becomes `requirement`; anything else becomes `task` |
| `status_category` | from `statusCategory.key`: `new` becomes `todo`, `indeterminate` becomes `in_progress`, `done` becomes `done`; a done issue whose resolution is Won't Do, Won't Fix, Duplicate, Cannot Reproduce or Declined becomes `cancelled` |
| `status` | the status name |
| account id | the `accountId` of the reporter, assignees and each comment author, never the display name. `is_bot` is true for an app account |
| dates | `created_at` from `created`, `closed_at` from `resolutiondate`, both as ISO-8601 |
| `parent` | the parent issue's numeric id, and its `kind` mapped with the `kind` row above, as for the item itself (the door accepts only `task`, `issue` or `requirement`) |
| linked pull requests | the development panel's pull request links, when the connection returns them |
| key form in pull requests | the key itself, `PROJ-123`, as a whole token in a title, branch or description (what `ticket_links.py` matches) |

Notes:

- Jira descriptions arrive as Atlassian document format or wiki markup; render them to plain text.
- `author.login`, each assignee's `login` and each comment's author are **account ids**, so that one person is one identity across the cards and an admin can erase by it. Never display names.
- `description` and every comment body are whole and in order. Do not shorten, summarise, reword or correct.
- `id` is the permanent id and never the readable key: the brain keys the card by it, so a renamed key does not make a second card.
