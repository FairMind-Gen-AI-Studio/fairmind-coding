# Shaping ClickUp tasks for the brain

Purpose: how to fill the task door's `item` from a ClickUp ticket, with the field names the tracker's connection returns. Every mapping is as the tracker's connection returns it; **when a field is absent, leave it out** and never guess it. The item's full shape is in `subagent-briefs.md`, brief 4.

| Field | Where it comes from |
|---|---|
| `source` | `clickup` |
| `id` (**permanent**) | the task `id` (for example `9hx2k7q1z`) |
| `key` (readable) | the custom task id when the workspace uses one, else none: leave `key` out |
| `url` | the task's own address |
| `project_key` | the list the task sits in (its id) |
| `type` | the task type name when the workspace has custom types, else leave it out |
| `kind` | a Bug task type becomes `issue`; Feature, Story, Requirement or Milestone becomes `requirement`; anything else becomes `task` |
| `status_category` | by what the **whole status name** means first, in any language, with case, emoji and punctuation ignored (`canceled` and `cancelled`, `won't`, `won’t` and `wont`, `on hold` and `on-hold` read alike), in this order: a cancelled or won't-do name becomes `cancelled`; a parked name (on hold, blocked, paused, waiting to start) becomes `todo`; a not-started name (to do, todo, backlog, open) becomes `todo`; a finished name (done, complete, completed, closed, shipped) becomes `done`, whatever group it sits in. A name whose meaning is unclear goes by the status `type`: `open` becomes `todo`, `custom` becomes `in_progress`, `closed` and `done` become `done` |
| `status` | the status name, verbatim |
| account id | the numeric user id as a string, for the creator, assignees and each comment author, never the username. `is_bot` is false unless the account is an integration |
| dates | millisecond epochs: convert to ISO-8601 for `created_at` and `closed_at` (`date_closed`, else `date_done`) |
| `parent` | the parent task's id, and its `kind` mapped with the `kind` row above, as for the item itself (the door accepts only `task`, `issue` or `requirement`). A top-level task has no parent: leave `parent` out |
| linked pull requests | the task's linked GitHub pull requests when the connection returns them |
| key form in pull requests | the custom task id, `CU-<id>`, and `#<id>` when the id has a letter (what `ticket_links.py` matches) |

Notes:

- Use synthetic ids such as `9hx2k7q1z` in examples you show the person; the real id is read from the task.
- `author.login`, each assignee's `login` and each comment's author are **account ids**, so that one person is one identity across the cards and an admin can erase by it. Never display names.
- `description` and every comment body are whole and in order. Do not shorten, summarise, reword or correct.
- `id` is the permanent id and never the readable key: the brain keys the card by it, so a renamed key does not make a second card.
- `status_category` reads what the name means before the type because a team can put a status in any type group: a board can keep `on hold` among the done statuses and `todo` among the custom ones. The door records a requirement in progress as implemented and one to do as accepted, so a parked feature must land as `todo`, never as in progress or done.
- The meaning is the whole name's, never a word found inside it: `reopened` is not `open` (work that came back; its meaning is unclear, so it goes by its type), `unblocked` is not `blocked`, and `awaiting deployment` is finished work waiting to ship, not work waiting to start, so it becomes `done`.
