# Shaping ClickUp tasks for the brain

Purpose: how to fill the ticket directory (`ticket.json` and its text files) that `ticket_card.py assemble` and `shape` turn into the task door's card, from a ClickUp ticket, with the field names the tracker's connection returns. Every mapping is as the tracker's connection returns it; **when a field is absent, leave it out** and never guess it. The directory's full shape is in `subagent-briefs.md`, brief 4.

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
| `status` | the status name, verbatim (the source's own word: it is part of the task's version) |
| account id | the numeric user id as a string, for the creator, assignees and each comment author, never the username. `is_bot` is false unless the account is an integration |
| dates | millisecond epochs: convert to ISO-8601 for `created_at` and `closed_at` (`date_closed`, else `date_done`) |
| `visibility` | `private` when the space is private; otherwise leave it out. Never infer it. Through the connector, a private list also counts. The token reader detects only a private space (see its limitations below) |
| `parent` | the parent task's id, and its `kind` mapped with the `kind` row above, as for the item itself (the door accepts only `task`, `issue` or `requirement`). A top-level task has no parent: leave `parent` out |
| linked pull requests | the task's linked GitHub pull requests when the connection returns them |

Notes:

- Use synthetic ids such as `9hx2k7q1z` in examples you show the person; the real id is read from the task.
- `author.login`, each assignee's `login` and each comment's author are **account ids**, so that one person is one identity across the cards and an admin can erase by it. Never display names.
- The description (`description.txt`) and every comment body (one `comment-<n>.txt` each) are whole and in order. Do not shorten, summarise, reword or correct: the version of the task is computed from them, and they stay in a local reading file; they are never sent.
- `id` is the permanent id and never the readable key: the brain keys the card by it, so a renamed key does not make a second card.
- `status_category` reads what the name means before the type because a team can put a status in any type group: a board can keep `on hold` among the done statuses and `todo` among the custom ones. The door records a requirement in progress as implemented and one to do as accepted, so a parked feature must land as `todo`, never as in progress or done.
- The meaning is the whole name's, never a word found inside it: `reopened` is not `open` (work that came back; its meaning is unclear, so it goes by its type), `unblocked` is not `blocked`, and `awaiting deployment` is finished work waiting to ship, not work waiting to start, so it becomes `done`.

## Through the token reader

When the person's `CLICKUP_API_TOKEN` is set and ClickUp answers it, `scripts/clickup_read.py` fills these directories instead of a reader sub-agent. It lists the chosen roots (`space:`, `folder:`, `list:`, `task:`), reads each task and its comments through the REST API, and copies the description and every comment exactly as ClickUp returned them, so a task's version is the same in every run. The script's own docstring has the command lines, the summaries and the exit codes; the meaning of what it writes is here.

**Rows** (`list`, `get`; brief 2's shape): `source` (`clickup`), `id`, `key` (the custom task id, when set), `title` (one line), `url`, `type` (the custom task type's name; absent for the plain task type and for an id the team's table does not name), `updated` (ISO-8601 UTC, absent when ClickUp gave no date), `parent` (`{"id"}`, plus `kind` once the mapping holds the parent's type; `get` only) and `cited_links` (`list` only: the http(s) links of the description, without repeats, in order, documents only; ClickUp task links, ClickUp attachments, pull request pages and GitHub commit links are left out). `pull_request_urls` is never written: ClickUp's API does not return the links its GitHub integration shows. A task found under two roots is one row; the same responses give byte-identical files.

**Vocabulary** (`clickup-vocabulary.json`, schema `brain-seed-clickup-vocabulary/1`): the distinct, sorted `statuses` (`{status, status_type}`) and `types` (names, or `{custom_item_id}` for an id the table does not name) of every task read, the parents of those tasks included. The agent maps each name.

**Mapping** (`.fairmind/brain-seed/clickup-mapping.json`, schema `brain-seed-clickup-mapping/1`), per project: `statuses` (`{status, status_type, status_category}`), `types` (`{type | custom_item_id, kind}`) and `agent_accounts` (numeric user ids as text). Status names compare in Unicode normal form, case-folded and whitespace-normalised, with `status_type` case-folded as part of the key. A plain task (custom type id 0) is kind `task` without asking. The key `agent_accounts` is written once the person has answered the question about integration accounts, even as `[]`; its presence is what `map` prints as `agent_accounts_asked`. The script only looks decisions up.

**Limitations and reuse**

- It cannot judge what a status name or a task type means. The agent decides each name once, following the `status_category` and `kind` rows above; a name the mapping lacks is reported as `pending` (and a task that uses it is `unmapped` and left out of `read`) until the agent decides it. Nothing in this file is applied by the script on its own.
- People are numeric account ids. An account the mapping names as an integration, and any automation (an id that is not a positive number), is `is_bot: true`.
- `visibility` is `private` only when the task's space is private: ClickUp shows no private flag on a folder or a list, so a private list inside a public space is not detected.
- The linked GitHub pull requests the integration shows in ClickUp are not in the API's answer, so `pull_request_urls` is never written; the pull request addresses written in the task's own text are still found by `ticket_links.py merge`.
- An epic given as a task root may be named by its permanent id or by its custom task id; the script tries each form with every team of the token.
- `list` and `get` keep every task they download in `clickup-tasks.raw.json` (`{id: task}`) beside their output; `read` and `get` take it with `--raw` and download only the tasks it lacks.
