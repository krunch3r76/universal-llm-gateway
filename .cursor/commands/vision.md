Capture a forward-looking capability or roadmap item from the current conversation.

**Workspace**: Load `@vision_ws.mdc` if exists for domain list and indexing.

## What This Is

`docs/vision/{slug}.md` captures what becomes possible when planned work lands —
architectural opportunities, new workflows, capability unlocks. These are not
problems to fix or empirical findings; they describe the future shape of the system.

## When to Use

Invoke when a thread has surfaced:
- A **capability unlock**: "when X lands, Y becomes possible"
- A **new workflow** enabled by planned infrastructure
- An **architectural opportunity** that depends on future work
- A **design direction** worth preserving across sessions

¬ use for: problems with solutions (use `/journal-entry`), empirical findings
or architectural properties already confirmed (use `/insight`), task tracking.

## Instructions

### 1. Synthesize

Read the current conversation. Identify:
- **Vision**: what becomes possible (the capability or workflow)
- **Prerequisite**: what must land first (the enabling work)
- **Architecture**: how it integrates with existing systems
- **Value**: why this matters (what it replaces or enables)
- **Domain**: infer from workspace context

### 2. Check for Existing Entry

Read existing docs in `docs/vision/`. If a doc already covers the same
capability area:
- **Update** it: append new sections, extend architecture
- Do NOT create a duplicate

### 3. Create or Update the Entry

**New entry**: write `docs/vision/{slug}.md`. Prose-first, architecture-focused.

```markdown
# {Title}

## {Vision / Concept}
{What becomes possible and why it matters}

## {Architecture / Approach}
{How it integrates — diagrams, protocol sketches}

## {Phases / Integration / Examples}
{Concrete steps, code snippets, integration points}
```

**Existing entry**: append new sections or extend existing ones.

### 4. Workspace Post-Steps (if `@vision_ws.mdc` exists)

Follow workspace-specific post-steps (RAG indexing, etc.).

## Rules

- The agent synthesizes — do not ask the user to fill in sections
- One file per capability area. Multi-session refinements stay in one file.
- Slug should be descriptive: `cloud-model-snapshot-replay`, not `idea-1`
- ¬ create vision docs for trivial observations or already-implemented features
- Keep vision docs evergreen — when the prerequisite lands, update or retire
