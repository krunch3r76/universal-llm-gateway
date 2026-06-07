Capture a discovery or architectural insight from the current conversation.

**Workspace**: Load `@insight_ws.mdc` if exists for domain list and indexing.

## What This Is

`tasks/discoveries/{slug}.md` captures empirical findings and design insights that
persist across sessions. Two types:

- **model_behavior**: empirical finding about a specific model's capabilities or limitations
- **insight**: architectural or design property discovered while building the system

Entries are facts to respect, not bugs to fix.

## When to Use

Invoke when a thread has surfaced:
- A **model behavior** worth remembering
- An **architectural insight** worth preserving
- An **empirical finding** that shapes how the system operates

¬ use for: problems with solutions (use `/journal-entry`), forward-looking capabilities
or roadmap items (use `/vision`), task tracking (use `/todo`).

## Instructions

### 1. Synthesize

Read the current conversation. Identify:
- **Type**: `model_behavior` | `insight`
- **Observation**: what was discovered (the core finding)
- **Evidence**: how it was confirmed (reproduction steps, A/B test, reasoning chain)
- **Operating rule**: what this means in practice (do X, avoid Y)
- **Domain**: infer from workspace context
- **Tags**: 3-6 lowercase keywords for semantic recall
- For `model_behavior`: also capture **Model**, **Runtime**, **Severity** (blocking | degraded | informational)
- For `insight`: also capture **Severity** as `architectural`

### 2. Check for Existing Entry

Read `tasks/discoveries/index.yaml`. If an entry already exists for the same finding:
- **Update** it: append new evidence, update status/operating rule
- Do NOT create a duplicate

### 3. Create or Update the Entry

**New entry**: write `tasks/discoveries/{slug}.md`:

```markdown
# {title}

- **Discovered**: YYYY-MM-DD
- **Type**: {model_behavior | insight}
- **Model**: {model ID, or ~ for insight type}
- **Runtime**: {runtime details, or ~ for insight type}
- **Status**: confirmed | suspected
- **Severity**: {blocking | degraded | informational | architectural}
- **Domain**: {domain}
- **Tags**: {comma-separated}

## Observation
{What was discovered — the core finding}

## Evidence
{How it was confirmed — reproduction steps, A/B test, reasoning}

## Operating Rule
{What this means in practice — do X, avoid Y}

## Context
{Why this matters to the project, how it connects to architecture}
```

**Existing entry**: append evidence, update operating rule. Do not rewrite existing content.

### 4. Update Index

Add or update the entry in `tasks/discoveries/index.yaml`.

### 5. Workspace Post-Steps (if `@insight_ws.mdc` exists)

Follow workspace-specific post-steps (engram docs, RAG indexing, etc.).

## Rules

- The agent synthesizes — do not ask the user to fill in the template
- One file per finding. Multi-session refinements stay in one file.
- Slug should be descriptive: `ministral-vision-structured-output`, not `finding-1`
- ¬ create entries for trivial observations
- ¬ list `tmp/` paths in files — ephemeral planning docs are not stable source
