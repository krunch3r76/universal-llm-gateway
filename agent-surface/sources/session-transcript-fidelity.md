<!-- target:* -->
# Session Transcript Fidelity

## Core Invariant (Post Server-Side Refactor)

**Invariant**: ∀ `session_summary_md` passed to a session-close call: the
structural layer is a **synthesis artifact** (decisions, files modified,
continuation state) — NOT a transcript, NOT a verbatim record.

The verbatim layer is no longer agent-authored. The session-close backend reads
the agent-transcripts JSONL at `transcript_jsonl_path` and assembles
`### User` / `### {Assistant}` blocks server-side. The agent supplies only the
path and the kilobyte-scale structural layer.

### Dual-Layer Doctrine — Where Each Layer Comes From

| Layer | Source | Format |
|---|---|---|
| Verbatim — `### User` / `### {Assistant}` blocks | Backend reads JSONL, assembles deterministically | `## Turn N — <topic>` + `### User` + `### {Assistant}` |
| Structural — `## Session Summary` block | Agent composes; server appends verbatim | `## Session Summary` heading + decisions / files / open items / continuation pointer |

The two layers are written to one file; the verbatim layer is the bulk, the
structural layer fits in a few KB.

Server-side enforcement (rejection reasons on close):
- `transcript.hollow` — assembled verbatim has zero User-voice blocks. Usually
  means the path points at a tool-only record set or the wrong session
  directory.
- `transcript.missing_structure` — composed transcript below the minimum
  character threshold or no headings.
- `session_summary.invalid` — `session_summary_md` is empty or lacks the
  `## Session Summary` heading.
- `transcript_jsonl.invalid` — path missing, outside the transcripts root, or
  JSONL parse error.

## What Goes in `session_summary_md` (Required Skeleton)

```markdown
## Session Summary

**Continues:** <prior-session-id>  (if continuation — MANDATORY when applicable)

**Decisions:**
1. <what was decided + why; rejected alternatives>
2. ...

**Files modified:**
- path/to/file.py

**Open items:**
- <follow-up>

**Attachments:** (optional)

**Journal:** <session-id>
**Transcript:** <session-id>
```

Kilobytes, not tens of kilobytes. If you find yourself pasting verbatim
user/assistant turns into `session_summary_md` you are doing the verbatim
layer's job — STOP and trust the server.

## Named Sections (Optional Sibling H2 Blocks)

The structural layer is not limited to the `## Session Summary` skeleton.
`session_summary_md` may carry **additional sibling H2 sections** after
`## Session Summary` — appendices, references, sidecar indexes, an expanded
summary, etc. The server preserves them verbatim: session-close appends
`session_summary_md` to the verbatim layer with only a trailing newline trim,
and the heading normalizer rewrites **only** the `## Session Summary` line (it
explicitly does NOT match bare `## Summary` or any other sibling heading). No
sibling H2 is clobbered, reordered, or dropped.

### Contract

- **`## Session Summary` MUST be present and MUST be the first H2.** It is the
  anchor the server normalizer and the hollow guard key off. Everything else
  rides along after it, untouched.
- **Sanctioned optional sections** (emit only when they carry real content —
  do not pad):

  | Section | Purpose |
  |---|---|
  | `## Summary` | Expanded prose summary, when the skeleton's terse `**Decisions:**` list is insufficient. Distinct from `## Session Summary` (the required anchor). |
  | `## Appendix A: <title>`, `## Appendix B: <title>`, … | Self-contained reference material produced this session (e.g. a query list, a command transcript, a derivation). Lettered + titled. |
  | `## Appendices` | Container form when grouping several short appendices under H3 subsections instead of separate H2s. |
  | `## References` / `## Sidecars` | Pointers to durable artifacts: closure sidecars, entity IDs, spec paths, external URIs. |

- **Ordering**: `## Session Summary` first; optional sections after, in the
  order above (Summary → Appendices → References/Sidecars) when more than one
  is present. Ordering is a readability convention, not a server gate.
- **Normalizer edge case**: If the literal `## Session Summary` is absent, the
  server promotes the *first* heading anywhere in the document that reads as a
  session-summary variant — including one buried in an appendix. Always emit
  the literal anchor first so promotion cannot land mid-document.
- **Still kilobyte-scale.** Appendices are for *synthesis* artifacts the next
  session needs (a query list, a decision derivation) — NOT a second copy of
  the verbatim turns. Canary A (verbatim leakage) applies to every section, not
  just the skeleton.

## Self-Check Gate (Required Before Calling session-close)

These checks run against `session_summary_md` ONLY — the agent-authored half.
The verbatim layer is the server's problem.

### Canary A — Verbatim leakage
Does `session_summary_md` contain `### User` or `### Assistant` blocks? If yes
— you're pasting verbatim turns into the structural layer. Strip them. The
server assembles the verbatim layer from the JSONL.

### Canary B — Action-log smell
Does `session_summary_md` contain phrases like "I then read…", "Next, I
posted…", "The agent dispatched…"? Move those into `decisions[]` or
`open_items[]` as outcomes ("Decision: …", "Open: …"). Action-log narrative
belongs in journal text, not the on-disk transcript.

### Canary C — Heading present
Does `session_summary_md` start with the **literal** `## Session Summary`
(exact: H2, capital S/S, single spaces)? Emit that exact string — do **not**
hand-roll a near-miss.

The server **normalizes** common near-misses rather than rejecting them and
rides an advisory `session_summary_heading_normalized` warning on success.
Normalized cases — all still better emitted as the literal:

| You wrote | Server does |
|---|---|
| `## Session Summary` (literal) | accepts as-is (no warning) |
| `# Session Summary` / `### Session Summary` | rewrites heading level → literal |
| `## Session Summary:` (trailing colon) | strips → literal |
| `##  Session   Summary` (extra spaces) | collapses → literal |
| no recognizable heading, body present | **prepends** `## Session Summary` |
| bare `## Summary` (no "Session") | NOT matched → heading prepended above it |

Only a **genuinely empty / whitespace-only** `session_summary_md` is a hard
`session_summary.invalid` rejection. The literal is still the contract — the
normalizer is a safety net, not a license to be sloppy.

**Sibling sections do not affect this canary.** The normalizer matches only the
`## Session Summary` heading line; any additional H2 sections pass through
untouched. Ensure `## Session Summary` is the **first** H2 so the anchor is
unambiguous.

### Canary D — Continuation pointer
If this session continues a prior one (you remember a prior session id or the
boot card surfaced one), is `**Continues:**` set? If absent, the server emits
a post-close `prior_session_id_omitted` warning.

### Canary E — JSONL path sanity
Is `transcript_jsonl_path` the **most-recently-modified** session directory's
JSONL? Re-check if uncertain. Pointing at the prior session's JSONL →
`transcript.hollow` rejection OR a confused transcript on disk.

If all five canaries pass → proceed to close the session.

## Truncation Protocol (Long Sessions)

Length is the server's problem, not the agent's. A 60-turn session produces a
60-turn verbatim layer; the backend reads the JSONL once and writes the
composed markdown atomically. Agent context overhead is bounded by the size of
`session_summary_md` (a few KB), not by the verbatim transcript.

If you ever see context-pressure errors during session close: the agent is
doing too much. Check that you are passing the PATH, not echoing the
transcript content.

## Post-Close Review Pattern

When dispatching a reviewing agent to review a session:

- Pass the **transcript file path**, not the session summary, as input.
- The review agent reads the assembled transcript from the file system.
- The session-close response's `content_hash` lets the reviewer detect
  tampering or accidental rewrite — quote it in the review packet.

## What Goes Where

| Content | Where it belongs |
|---|---|
| The actual JSONL with user/assistant turns | Backend reads it; agent passes the PATH only |
| Verbatim user messages | server-assembled into `### User` blocks (do NOT pre-paste) |
| Verbatim assistant prose | server-assembled into `### {Assistant}` blocks |
| Decisions made | `summary` field + `decisions[]` array + `**Decisions:**` list in `session_summary_md` |
| Files changed | `**Files modified:**` list in `session_summary_md` |
| Open items | `open_items[]` array + `**Open items:**` list in `session_summary_md` |
| The narrative of "what happened" | `summary` field ONLY |

## Anti-Patterns

| Bad | Good |
|---|---|
| Constructing the transcript by walking the context window | Pass `transcript_jsonl_path` and let the server assemble |
| Pasting `### User\n...\n### Assistant\n...` into `session_summary_md` | Keep `session_summary_md` to the `## Session Summary` skeleton (+ optional named sections) |
| Putting an appendix *before* `## Session Summary` | `## Session Summary` first; appendices as sibling H2s after it |
| Hand-rolling `## Session Summary` twice to fake an appendix | One anchor heading; add `## Appendix A: …` / `## References` as distinct siblings |
| Reading the transcript file after close to "verify" | Quote `content_hash` from the response payload — the hash IS the confirmation |
| Action-log paragraphs in `session_summary_md` | Convert to `decisions[]` / `open_items[]` outcome bullets |

## Relationship to Other Rules

- Session-close protocol rules — this rule supplies the structural-layer
  authorship discipline that the protocol assumes.
- Provenance discipline — `content_hash` from the response payload is the
  response-payload evidence; do not bridge unknown into "done" with a
  fabricated success line.
<!-- /target:* -->
