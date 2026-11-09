# Shaping Azure DevOps tasks for the brain

Purpose: how to fill the ticket directory (`ticket.json` and its text files) that `ticket_card.py assemble` and `shape` turn into the task door's card, from a Azure DevOps ticket, with the field names the tracker's connection returns. Every mapping is as the tracker's connection returns it; **when a field is absent, leave it out** and never guess it. The directory's full shape is in `subagent-briefs.md`, brief 4.

| Field | Where it comes from |
|---|---|
| `source` | `azure_devops` |
| `id` (**permanent**) | the work item id, as a string |
| `key` (readable) | leave it out: the number is the id, and a bare number would match any `#314` or `line 314` in a pull request |
| `url` | the work item's own address |
| `project_key` | `System.TeamProject` |
| `type` | `System.WorkItemType`, as the tracker returns it |
| `kind` | Bug, or Issue (an impediment), becomes `issue`; Epic, Feature, User Story, Product Backlog Item or Requirement becomes `requirement`; anything else becomes `task` |
| `status_category` | from the state category: Proposed becomes `todo`; In Progress and Resolved become `in_progress`; Completed becomes `done`; Removed becomes `cancelled` |
| `status` | `System.State` |
| account id | the identity id (a GUID) of the creator, assignees and each comment author, never the display name or the email. `is_bot` is true for a service identity |
| dates | `System.CreatedDate`, and `Microsoft.VSTS.Common.ClosedDate` for `closed_at`, as ISO-8601 |
| `visibility` | the project's own word: `public` becomes `public`, `private` becomes `private`, `organization` becomes `restricted`; otherwise leave it out |
| `parent` | the parent work item's id, and its `kind` mapped with the `kind` row above, as for the item itself (never the work item type name: the door accepts only `task`, `issue` or `requirement`) |
| linked pull requests | the `ArtifactLink` relations of type pull request, when the connection returns them |

Notes:

- Descriptions are HTML; keep the description as the connection returns it; do not render or convert it.
- `author.login`, each assignee's `login` and each comment's author are **account ids**, so that one person is one identity across the cards and an admin can erase by it. Never display names.
- The description (`description.txt`) and every comment body (one `comment-<n>.txt` each) are whole and in order. Do not shorten, summarise, reword or correct: the version of the task is computed from them, and they stay in a local reading file; they are never sent.
- `id` is the permanent id and never the readable key: the brain keys the card by it, so a renamed key does not make a second card.
