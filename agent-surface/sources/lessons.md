<!-- target:* -->
# Lessons

Persistent correction tracking across sessions.

## Structure

```
tasks/lessons/
  index.md                        # TOC — scan this, filter by domain + triggers
  {domain}-{slug}.md              # One lesson per file, YAML frontmatter
```

**Naming**: `{domain}-{slug}.md` where domain matches the project's concern areas.

### Frontmatter Schema

Every lesson file has YAML frontmatter:

```yaml
---
domain: <project-specific domain>
severity: critical | advisory
triggers:
  - keyword or context string
---
```

| Field | Values | Purpose |
|---|---|---|
| `domain` | Project-specific (e.g. routing, tooling, api, data, infra) | Coarse filter by topic area |
| `severity` | `critical` = silent failure or cryptic error; `advisory` = best practice | Triage priority |
| `triggers` | List of keywords, file paths, commands, or contexts | Fine-grained relevance matching |

## On Session Start

1. Read `tasks/lessons/index.md`
2. Filter: **Domain** matches the current task area AND/OR **Triggers** overlap with files/concepts being touched
3. For **critical** lessons that match: read the full file
4. For **advisory** lessons that match: skim unless directly relevant

## On Correction

∀ user_correction:
1. Create `tasks/lessons/{domain}-{slug}.md` with frontmatter:
   ```markdown
   ---
   domain: {domain}
   severity: critical | advisory
   triggers:
     - {keyword relevant to when this applies}
     - {file path or command that triggers this}
   ---

   # <title>

   - **Pattern**: <what went wrong>
   - **Fix**: <correct approach>
   - **Context**: <when this applies>
   ```
2. Append row to `tasks/lessons/index.md`:
   ```markdown
   | {domain} | {severity} | {one-line summary} | {triggers, comma-separated} | [{filename}]({filename}) |
   ```

## Scope

- Domain-specific mistakes
- Recurring architectural misunderstandings
- Project convention violations caught by user

¬ record: typos, one-off clarifications, preference changes
<!-- /target:* -->
