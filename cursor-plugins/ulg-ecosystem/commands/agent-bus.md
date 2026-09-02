Fetch all unread agent-bus turns addressed to cursor and act on each sequentially.

All operations use `scripts/agent-bus` (direct UDS) — NOT `CallMcpTool`.
This avoids extension-host freezes on large payloads over SSH remote.

## Instructions

### 1. Fetch All Unread Turns

```bash
scripts/agent-bus fetch-unread --to cursor --mark-read
```

### 2. Handle Result

**If no turns returned** (`turns: []`): report "No unread turns for cursor."

**If turns exist**: process each turn in order (oldest first). For each turn:
- Read the turn body and act on the instructions.
- The turn's `thread` field identifies the conversation thread.
- The turn's `from` field identifies who sent it.
- The turn's `body` contains the full message with instructions.
- Reply (step 3) for each turn before moving to the next.

**Check `_thread_info` before acting**: the response now always includes a
`_thread_info` block. If `has_earlier_turns: true`, fetch the full thread
before acting — the visible turns are missing context:

```bash
scripts/agent-bus fetch --thread THREAD_ID --unread --context 3 --mark-read
```

This fetches all unread turns plus 3 turns of prior context in one call.
If `_thread_info.has_earlier_turns` is still true after this, the thread is
long and the notice field contains the suggested `--last N` to widen further.

#### 2b. Section Operations for Large Documents

When a turn references a large structured document (spec, phase doc, runbook),
use `fs(op="md_list", …)` / `fs(op="md_read", …)` for targeted access instead
of whole-file reads. **Sandbox routing**: `sandbox="context"` for `tasks/` files,
`sandbox="cortex"` for `/data/files`, `sandbox="workspaces"` for files under
`/mnt/torus/projects/{repo}/...` (paths must include repo prefix).

### 3. Reply

After completing the work, post a reply turn.

For replies with markdown, backticks, or special characters (most replies),
write the body to a temp file first, then use `--body-file`:

1. Write body content to `/tmp/agent-bus-reply.md` using the Write tool
2. Post with:

```bash
scripts/agent-bus reply \
  --thread THREAD_ID \
  --to SENDER \
  --subject "SHORT_SUMMARY" \
  --body-file /tmp/agent-bus-reply.md \
  --after-turn TURN_NUMBER
```

Default message bodies remain briefings. Put long analysis, specs, and reviews
in a sidecar markdown file and link it from the turn. If the recipient is a web
agent that specifically needs inline long-form content, add `--allow-long-body`
to `post` or `reply`; this is an explicit override, not the default path.

For short plain-text replies, `--body` works inline:

```bash
scripts/agent-bus reply \
  --thread THREAD_ID \
  --to SENDER \
  --subject "SHORT_SUMMARY" \
  --body "Acknowledged. Working on it." \
  --after-turn TURN_NUMBER
```

Replace `THREAD_ID`, `SENDER`, `TURN_NUMBER` with values from the fetched turn.
`--after-turn` is the `turn_number` of the turn being replied to.
Optionally add `--supersedes-turn TURN_NUMBER` when this reply replaces an earlier turn (marks that turn as stale).

**Acknowledgement-only replies**: If the inbound turn is purely informational or
confirmatory and your reply is only an acknowledgement (no work requested, no
follow-up expected), reply and then close the thread immediately. Prefer this
over leaving behind open "thanks/acknowledged" threads.

When reporting to the user, cite `turn_number` (in-thread sequence), not `id` (global row ID).
Example: "Reply posted to thread 033, turn 2" — NOT "turn 82".

### 3a. Starting a New Thread

To create a **new** thread (auto-assigned numeric ID) and post the first turn:

```bash
scripts/agent-bus post \
  --slug "my-topic-slug" \
  --to RECIPIENT \
  --subject "SHORT_SUMMARY" \
  --body-file /tmp/agent-bus-reply.md
```

The response includes the auto-assigned thread ID. Use `reply` for subsequent
turns on that thread.

**NEVER** use `reply` to create a new thread — it requires a `--thread` ID and
will not auto-assign one. Always use `post` for new threads.

### 3b. Push reminder

After replying, apply `agent-bus-push-reminder_ulg.mdc`. **Default: CDP /
operator-proxy / converse continuity wakes web** — report status only; **¬** ask
the operator to push merely because an open thread expects web next. Human push
reminder only when autonomous CDP wake is unavailable or broken. Do not fork the
decision table into this command.

## Variants

| Invocation | Behavior |
|---|---|
| `/agent-bus` | Fetch all unread turns to cursor, act on each sequentially |
| `/agent-bus {thread}` | Fetch all unread turns in a specific thread, act on each; retitle tab `{id} {slug}` |
| `/agent-bus {thread} --all` | Fetch ALL turns in a specific thread (read and unread); retitle tab `{id} {slug}` |
| `/agent-bus --peek` | Fetch but do NOT mark read or act — just show the turn |
| `/agent-bus --status` | Show thread list |

### IDE tab (thread-scoped resume) — Step 0

Attended Cursor IDE only — headless seats / agent-only continuity ⇒ no-op.

`∀` `/agent-bus {n}` ∨ `/agent-bus {n} --all` in Cursor IDE: **first determinate
action** (before acting on turns) = `cursor-app-control rename_chat` to exactly
`{n} {slug}` — slug from `thread_get` / fetch `_thread_info` (truncate ≤200).
**¬** bare `{n}` · slug alone · turn `subject`. Verify slug matches
`thread_get.slug`; tool unavailable ⇒ state once, continue. That invocation
**is** the rename ask — `rename_chat`'s "only when the user asks" gate is
already satisfied; do not wait for a second “rename the tab.” Sticky
re-injection is idempotent. ¬ inbox `/agent-bus` (multi-thread) · ¬ `--peek` ·
¬ `--status`. ¬ Mission/Objective as the title (slug is the tab; Mission stays
spoken). Same bind as operator-posture `resume <n>`. CHECKPOINT authoring uses
`. {id} {slug}` instead — see `checkpoint-discipline` / operator-posture Rule 3.

### `/agent-bus {thread}`

`--context` is opt-in, not the default (friction 21762 — an unconditional
`--context 3` compounds with oversized turn bodies into expensive reads on
long root threads). Check `_thread_info` first, widen only if needed:

```bash
scripts/agent-bus fetch --thread {thread} --unread --mark-read
```

If the response's `_thread_info.has_earlier_turns` is true, widen with the
suggested window (same check-then-widen pattern as Step 2 above):

```bash
scripts/agent-bus fetch --thread {thread} --unread --context 3 --mark-read
```

### `/agent-bus {thread} --all`

```bash
scripts/agent-bus fetch --thread {thread} --mark-read
```

### `/agent-bus --peek`

```bash
scripts/agent-bus fetch --to cursor --last 1 --unread --compact
```

### `/agent-bus --status`

```bash
scripts/agent-bus threads --status active
```

## Direct CLI Reference

```bash
# Fetch (inbox or thread)
scripts/agent-bus fetch-unread --to cursor --mark-read
scripts/agent-bus fetch --thread 049 --unread --context 3 --mark-read
scripts/agent-bus fetch --thread 049 --last 5
scripts/agent-bus fetch --thread 049 --last 1 --compact

# Post (new thread — auto-assigns numeric ID)
scripts/agent-bus post --slug "my-topic" --to web --subject "Title" --body-file /tmp/body.md
scripts/agent-bus post --slug "my-topic" --to web --subject "Title" --body "Short body"

# Reply (existing thread only)
scripts/agent-bus reply --thread 049 --to web --subject "done" --body-file /tmp/reply.md --after-turn 6
scripts/agent-bus reply --thread 049 --to web --subject "ack" --body "Acknowledged." --after-turn 6

# Threads
scripts/agent-bus threads
scripts/agent-bus threads --status closed

# Update thread
scripts/agent-bus update-thread --thread 049 --status closed --summary "Entity resolution shipped"
```

## Thread Closure Protocol

**Invariant**: closed threads MUST have `unread_count == 0`.

When closing a thread, `update-thread --status closed` automatically marks all
turns as read. This keeps the closed-thread list free of stale unread noise.

### Self-closing note (common)

Agent posts a closing note to itself (e.g. "Verified — closing") that no one
needs to read. Close the thread — auto-mark-read handles it:

```bash
scripts/agent-bus reply --thread THREAD_ID --to SENDER --subject "Verified — closing" \
  --body "Confirmed. Closing." --after-turn TURN_NUMBER
scripts/agent-bus update-thread --thread THREAD_ID --status closed --summary "..."
```

Alternatively, mark the reply itself read immediately:

```bash
scripts/agent-bus reply --thread THREAD_ID --to SENDER --subject "Verified — closing" \
  --body "Confirmed. Closing." --after-turn TURN_NUMBER --mark-read
```

### Closing note for another agent

If the note IS intended for the recipient, do NOT close the thread. The
recipient reads the turn and closes:

```bash
# Sender: post note, leave thread open
scripts/agent-bus reply --thread THREAD_ID --to RECIPIENT --subject "Summary for review" \
  --body "..." --after-turn TURN_NUMBER

# Recipient: read the turn, then close
scripts/agent-bus fetch --thread THREAD_ID --last 1 --mark-read
scripts/agent-bus update-thread --thread THREAD_ID --status closed --summary "..."
```

### Acknowledgement-only reply (default)

If the inbound turn needed no action beyond a short acknowledgement, reply and
close the thread in the same pass:

```bash
scripts/agent-bus reply --thread THREAD_ID --to SENDER --subject "Acknowledged" \
  --body "Acknowledged." --after-turn TURN_NUMBER
scripts/agent-bus update-thread --thread THREAD_ID --status closed --summary "Acknowledgement sent; no further action required."
```

Use this when the thread is effectively complete after the acknowledgement.
Do not use it if the recipient still needs to read your substantive reply.
Closed-thread acks to web: **no** operator push reminder (see §3b).

### Bug / friction tickets (`type:bug`) — auto-close on handler closeout

**Invariant**: ∀ agent-bus thread tagged `type:bug` (or filed as a friction ticket
with subject `Friction: …` / body pointing at `cortex:notes/system/tickets/…`):
the **handler closes the thread in the same pass** after posting the closeout
reply. The filing seat does not need to read the reply; do not leave the thread
open for web to "ratify" or act on.

| Signal | Handler action after closeout reply |
|---|---|
| `type:bug` tag, or friction-ticket shape | `update-thread --status closed` immediately |
| Closeout complete (triage + fix, or investigate→spec, or ack-only) | Close — no push reminder |
| Deferred work discovered | Label `## Secondary findings` in the closeout; spin a **new** thread/todo if pursued — do not keep the bug thread open |

**Anti-pattern**: posting a substantive closeout to `claude-web` and leaving the
thread open "for ratification" — that forces an operator push for noise. Bug
tickets are fire-and-forget from the filing seat's perspective once the handler
reports.

```bash
scripts/agent-bus reply --thread THREAD_ID --to SENDER --subject "Closeout — …" \
  --body-file /tmp/agent-bus-reply.md --after-turn TURN_NUMBER
scripts/agent-bus update-thread --thread THREAD_ID --status closed \
  --summary "Bug closeout: {one-line root cause + disposition}"
```

### Opting out of auto-mark-read

In rare cases where you need to close without marking turns read:

```bash
scripts/agent-bus update-thread --thread THREAD_ID --status closed --summary "..." --no-mark-read
```
