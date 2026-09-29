---
name: brain-add-document
description: Use when a document worth keeping has come up in a session and should become something the company brain can cite - a standard the whole company follows, a design or blueprint you were given as a link, a specification you just wrote, an ADR export. It files the document in the brain with a summary YOU write, says what the document is evidence FOR, and refuses the cases that belong somewhere else. Also use when asked to "put this in the brain", "remember this document", "record this standard", or when a decision you are recording rests on a document nobody has filed.
---

# Put a document where the brain can cite it

## Overview

A document the brain holds is one an agent can find later and quote a line from. The
expensive failure is not a missing document — it is a document that is *there* and
unfindable, because the one line describing it says nothing.

**That line is yours to write.** This door runs no model. Whatever you put in `summary`
is what a future search matches on and what a reader sees before deciding to open the
file; leave it out and the card carries the first 500 characters of the body, which for
a specification is its title page.

(The other direction exists and is not yours: a file someone uploads through Studio was
read by nobody, so the platform summarises it. That is why the summary is expected here
and produced there.)

**What leaves your machine when this skill writes.**
`mcp__Fairmind__Brain_add_document` sends the document's kind, its title, the summary you
wrote, and exactly one of: the document's **whole body**, a link, or the id of a file
already uploaded — plus the ids of the records it is evidence for and an agent label — to
the Fairmind platform your project's MCP entry points at. Unlike a requirement or a
decision, it is **filed, not proposed**: nothing on the platform holds it for a person. This
is part of the **brain write-back**, and the plugin README's *What leaves your machine*
describes it in full under that name — alongside every other route by which anything
leaves.

**Announce at start:** "I'm using the brain-add-document skill to file <document> so the
brain can cite it."

## 0. Check the door is there

Confirm `mcp__Fairmind__Brain_add_document` is in the tool list. It arrives with a later
platform release than the other `mcp__Fairmind__Brain_*` tools, so a connected server can
offer those and not this one. If it is absent, say so and **stop** — do not reach for a
neighbouring door instead: a document recorded as a decision is a claim nobody made.

## 1. Decide whether it belongs here at all

Four cases that look like this one and are not:

- **A file already in the repository.** In-repo markdown is already a FILE node in code
  ingestion. Filing it again gives the same text two representations that drift apart.
  Cite the path instead.
- **A file someone uploaded through Studio.** It already has a card. Pass its
  `document_id` here *only* to say what it is evidence FOR — and expect
  `revision_proposed` rather than a silent edit, because a person confirmed that card.
- **A decision, a requirement or an issue.** Those have their own doors
  (`mcp__Fairmind__Brain_record_decision`, `mcp__Fairmind__Brain_record_requirement`,
  `mcp__Fairmind__Brain_record_issue`). A document is the artefact; the claim it carries
  is the record. If what you want kept is "we decided X", record the decision and point
  it at the document.
- **A project's brief, or the company's.** The platform keeps one brief per scope, so a
  `brief` written here would replace that scope's brief — a draft rewritten in place, or a
  revision proposed against the one a person confirmed — with none of the diff and scope
  checks a brief needs. Writing or regenerating a brief is `brain-onboard`'s generation
  mode; hand the request to that skill.

## 2. Choose the scope, and notice the one thing this door can do

`project` LEFT OUT means the whole company sees it. That is the case an upload cannot
express at all — an uploaded file always belongs to a project — so a coding standard, an
architecture principle or a company-wide template belongs here specifically, with no
`project`.

Pass `project` when the document is about one product and would be noise elsewhere — the
`project_id` in `.fairmind/active-context.json`, which `/fairmind-connect` writes. Read it;
do not guess a project name.

## 3. Write the summary

Two or three sentences. The test: **would someone who has not read the document know
whether to open it?**

**You can only write this about a document you have read.** A link you were handed and
have not opened is not one: this skill does not fetch it, and a summary built from a
title is the unfindable card this skill exists to prevent. Ask for the content, or for a
description from someone who has read it, and say which of the two the summary rests on.

Say what the document IS and what it covers, in the document's own vocabulary — the
words a future search will be typed in. Name the parts it has, when they are what makes
it worth finding.

Do not:

- begin with "This document" — the card already says it is a document;
- evaluate it ("a thorough guide", "a useful reference");
- add anything the document does not say, including your reading of why it exists;
- summarise the topic instead of the artefact. "How authentication works" describes a
  subject; "the authentication design for the Studio API, covering token issue, refresh
  and the Cognito mapping" describes this document.

## 4. Say what it is evidence for

`evidences` is what turns a document from a file into something the brain can reason
with: it connects the artefact to the decision, requirement or issue it supports, so a
later question about that decision surfaces the document behind it.

Refs are `{'node_id': ...}`, `{'natural_key': ...}` or `{'decision_id': ...}`. Add
`'kind'` when the target is a requirement or an issue — without it the target is read as
a decision, which is what a document most often evidences.

Leaving it empty is allowed and is a choice: the document is then findable by search and
connected to nothing.

## 5. Show what is about to leave, and ask

Nothing on the far side holds this for a person: a requirement or a decision you record
arrives as a proposal somebody confirms, and a document is simply filed. So the person in
front of you is the only check there is, and it comes **before** the call.

Show them, in a few lines: the title, the summary you wrote, **where it will be visible**
— the named project, or the whole company when `project` is left out — and **what travels**:
the document's whole body, only a link, or a reference to a file already uploaded. Then wait
for a yes. Having been asked to "put this in the brain" is that yes for the document named,
and not for its scope: if you chose company-wide, say so and ask.

If you started this skill on your own — a document came up, a decision rests on one —
nobody has asked for anything yet. Ask.

## 6. Call it

```
mcp__Fairmind__Brain_add_document(
    doc_kind=<spec|design|blueprint|note|manual|minutes|email|wiki|interview|addendum|adr_export>,
    title=<one line naming the document>,
    summary=<yours, from step 3>,
    # exactly ONE of:
    content=<the document, when you wrote it here>,
    uri=<where it lives, when it lives elsewhere — nothing is fetched>,
    document_id=<a file already uploaded to this project>,
    evidences=[...],
    agent=<which agent you are>,
    project=<omit for company-wide>,
)
```

Exactly one of `content` / `uri` / `document_id`: passing none, or more than one, is
refused. They are three different claims about where the document is, and the door will
not guess.

## 7. Read what comes back

- an upsert result — filed;
- `revision_proposed` — a person had confirmed this card, so your change is waiting for
  one. Not a failure, and not done either: say so rather than reporting success;
- a refusal — quota, throttle, erasure fence, no such document, or more than one
  location. Each names itself; none of them is retried by repeating the call unchanged.

## What this skill does not do

It does not fetch a `uri`. The card records where the document lives, and a reader
follows it — so a link-only card can be found and cannot be quoted from. If a line of it
is ever going to be cited, pass `content`.
