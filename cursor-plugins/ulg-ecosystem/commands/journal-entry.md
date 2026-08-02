Create or update a problem/solution journal entry from the current conversation.

**Workspace**: Load `@journal-entry_ws.mdc` if exists for domain list and indexing.

## What This Is

`tasks/journal/{slug}.md` captures problem → diagnosis → solution narratives
that persist across sessions. Entries are problem-centric (one file per problem,
not per date).

## When to Use

Invoke when a thread has identified a **problem worth remembering**: a bug with
a non-obvious root cause, an architectural decision with rejected alternatives,
a multi-step diagnosis that future sessions should not repeat.

¬ use for: trivial fixes, typos, one-off clarifications, preference changes,
empirical findings or architectural properties (use `/insight`), forward-looking
capabilities or roadmap items (use `/vision`).

## Instructions

### 1. Synthesize

Read the current conversation. Identify:
- **Problem**: what was observed (the symptom)
- **Root cause**: why it happened (if known — leave blank if still investigating)
- **Solution**: what was decided or implemented (or "planned" if fix not yet landed)
- **Alternatives considered**: approaches rejected and why
- **Affected files**: which source files are involved (¬ `tmp/` — ephemeral)
- **Domain**: infer from workspace context

### 2. Check for Existing Entry

Read `tasks/journal/index.yaml`. If an entry already exists for the same problem:
- **Update** it: append to Timeline, update Status/Root Cause/Solution as needed
- Do NOT create a duplicate

### 3. Create or Update the Entry

**New entry**: write `tasks/journal/{slug}.md`:

```markdown
# {title}

- **Opened**: YYYY-MM-DD (unix: {unix_timestamp})
- **Resolved**: — (or YYYY-MM-DD when resolved)
- **Status**: open | resolved
- **Domain**: {domain}
- **Files**: {comma-separated paths}

## Problem
{What was observed}

## Root Cause
{Why it happened — or blank if unknown}

## Timeline
- YYYY-MM-DD: {event}

## Solution
{What was decided/implemented — or "(planned)" if not yet committed}

## Alternatives Considered
{Other approaches rejected and why — or "None yet" if early}
```

**Existing entry**: add Timeline entries, update Status/Root Cause/Solution.
Do not rewrite existing content — append.

### 4. Update Index

Add or update the entry in `tasks/journal/index.yaml`:

```yaml
- slug: {slug}
  summary: "{one-line summary}"
  status: {open|resolved}
  domain: {domain}
  opened: YYYY-MM-DD
  opened_ts: {unix_timestamp}
  files:
    - {path/to/file1}
    - {path/to/file2}
```

### 5. Workspace Post-Steps (if `@journal-entry_ws.mdc` exists)

Follow workspace-specific post-steps (architecture doc check, RAG indexing, etc.).

## Rules

- The agent synthesizes — do not ask the user to fill in the template
- One file per problem. Multi-day investigations stay in one file.
- Slug should be descriptive: `vllm-timings-missing`, not `bug-1`
- ¬ create entries for trivial work
- ¬ list `tmp/` paths in `files:` — ephemeral planning docs are not stable source
