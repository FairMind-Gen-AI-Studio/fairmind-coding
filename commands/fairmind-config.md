---
description: Show or change this repo's plugin policy — the brain write-back at a loop's human gate and ambient session capture, each on/off/unset, with the platform's central force always winning over the local choice
allowed-tools: Bash(python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/_insights_session.py:*), Read
---

# fairmind-config

Two features can be turned on or off for this repository: `brain` (the
decisions and issues a closed loop proposes to the company brain) and `ambient`
(background session capture to Agentic Insights). The layers are checked in
this order:

1. **Central** — a policy the Fairmind platform pushed for this Fairmind
   project (or the company default), cached locally with a 7-day offline
   window. When set it **wins over both layers below**.
2. **Local file** — `.fairmind-insights.json` at the repo root, the
   committable per-repo choice.
3. **Default** — brain on; ambient capture OFF — it is opt-in, and only an
   explicit `"ambient_capture": true` (or a central force) turns it on.

`/fairmind-config` is the one command that reads and writes this: `status`
(default) reports the effective answer per feature and which layer decided it;
`<feature> <on|off|unset>` changes the **local** layer only — the central layer is never
writable from here, and the verb refuses outright when the feature being set
is centrally forced.

## Usage

```bash
/fairmind-config
/fairmind-config status
/fairmind-config brain on
/fairmind-config brain off
/fairmind-config brain unset
/fairmind-config ambient on
/fairmind-config ambient off
/fairmind-config ambient unset
```

No argument is the same as `status`. `brain`'s own key in the local file is
`brain`; ambient's is `ambient_capture`, but the command line and every status
line say `ambient`.

⚠️ **The two differ on what `unset` means, and the verb says which it did.** For
`brain`, `unset` REMOVES the key and an absent key means on. For `ambient` the
local default is off, so its `unset` writes `false` — the default, said out
loud — rather than removing anything.

## `status` (default)

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/_insights_session.py --insights-status
```

The output carries several sections; the one this command is about is the
`Plugin policy:` block near the top:

```
Plugin policy:
  brain write-back: on — default
  ambient capture: off — repo file
  central policy cache: none (no successful fetch recorded for this checkout)
```

**Digest it, do not paste it raw.** Tell the user the effective state and its
source, in plain language:

- `centrally forced (project <id>)` / `centrally forced (company default)` —
  the platform decided; the local file's own value (if any) is being
  overridden and cannot change this from here.
- `repo file` — `.fairmind-insights.json` at the repo root decided: any shape
  other than an explicit `"ambient_capture": true` reads as off, fail-closed.
- `default` — neither layer spoke: brain is on, and ambient capture is off
  (not opted in).

Also report the `central policy cache:` line as-is (translated to prose) —
`none`, `fetched <iso> (fresh)`, or `fetched <iso> (stale — …)`. A stale or
absent cache means a central force from a previous fetch is no longer
binding and the local file (or default) is what actually applies right now.

**One thing this line cannot tell you, so do not imply otherwise:** if
`ambient capture` shows `off` with source `centrally forced (project X)`, the
same status output's ambient section separately states `(gate: forced_off)`
next to why nothing is being captured — that confirms the same fact, it is
not a second signal to reconcile.

## `brain <on|off|unset>` / `ambient <on|off|unset>`

```bash
python3 "${CLAUDE_PLUGIN_ROOT}"/scripts/_insights_session.py --set-policy <brain|ambient> <on|off|unset>
```

Run with your cwd inside the repository whose policy you're changing.

**Exit codes.** `0` = wrote. `2` = every refusal, including an argparse usage
error (a value outside `on|off|unset`, a missing operand) — argparse's own
exit code happens to be 2 as well, so non-zero always means nothing was
written. An unexpected `OSError` propagates as a traceback and exit `1` by
design — a writer that fails must fail loudly, not report success it didn't
earn.

**Channels.** The success report goes to **stdout**; every refusal goes to
**stderr**. Capture and show both — do not redirect stderr away or read only
stdout, because on a refusal the stderr text is the entire message the user
needs.

**Three refusals are possible, in this order, and all leave the file
byte-identical:**

1. **The feature is centrally forced.** The message names the forcing project
   (`project <id>`) or `the company default` when the cache carries no
   project id, and says the change has to happen on the Fairmind platform and
   is picked up on the next session after a successful fetch.

   ⚠️ **Relay this verbatim. Never work around it.** Do not hand-edit
   `.fairmind-insights.json` to get the outcome the refusal just declined to
   give you, and do not touch or delete the policy cache to force a
   re-fetch — the whole point of a central force is that the local machine
   does not get to decide it. Tell the user exactly what the refusal said
   and that the change belongs on the platform.

2. **The file exists and does not parse as a JSON object** (unparseable
   bytes, a JSON array, a JSON scalar). Message contains "does not parse as
   a JSON object." House rule: a config that fails to parse is an attempt at
   a decision, so the verb will not clobber it — tell the user to fix or
   delete the file by hand, then run the command again.

3. **A `brain` op only**, when the file already carries `ambient_capture` as
   something other than a boolean (`"true"`, `0`, `null`, `[]`, `{}`).
   Message contains "not a boolean." The plugin already reads that shape as
   capture OFF; freezing it to boolean `false` behind a command about the
   brain would erase the evidence of whatever the non-boolean value meant. An
   **ambient** op is never refused for it: `/fairmind-config ambient on` or
   `ambient off` is the documented way *out* of that state. If you see this,
   run the ambient command first, then retry the one you wanted.

**What a successful write does:**

- `brain on` / `brain off` → sets `"brain": true` / `false`.
- `brain unset` → removes the `brain` key entirely (an absent key means on).
- `ambient on` → sets `"ambient_capture": true`.
- `ambient off` → sets `"ambient_capture": false`.
- `ambient unset` → sets `"ambient_capture": false` (the local default is
  off, with no file and in a file without the key alike, so `unset` here is
  written identically to `off`, with a note explaining why).

Unknown keys (`consent`, `event_skeleton`, anything a future slice adds)
survive with their values and order; only the formatting is normalized to
2-space indent with a trailing newline.

**Output.** The verb prints `Wrote <path>: <feature> <value>.`, then any
notes, then a blank line, then the exact same `Plugin policy:` block
`status` prints. Relay that block the same way `status` does above — do not
issue a second `--insights-status` call after a write, the one call already
told you the new state.

## Notes

- `brain` and `ambient` are the only feature words; `on`, `off`, `unset` are
  the only values. This is a closed vocabulary by design — do not invent a
  third feature or a fourth value even if asked, and say so if someone
  requests one.
- ⚠️ **What `brain off` does not stop.** It stops the loop proposing decisions
  and issues to the company brain. The same decision rows keep leaving through
  the Agentic Insights lane, which has no switch — so `brain off` is not
  "these decisions stay on this machine". Say that plainly when someone turns
  it off for that reason; the plugin README's data-flow section is the
  authority on what leaves a machine.
- Outside a git work tree, `--set-policy` refuses cleanly (exit 2, nothing
  written) rather than guessing a repository root.
- This script does no network I/O on either verb. The central cache it reads
  is refreshed elsewhere (the session-start sweep); `/fairmind-config` only
  ever reads the last fetch that happened to land, or writes the local file.
