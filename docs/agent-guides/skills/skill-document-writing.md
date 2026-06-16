---
name: skill-document-writing
description: On any task to decide whether to author, revise, or retire a SKILL.md, write or critique a skill document, draft an agent_skill entity description, respond to a skill_binding audit-gate finding, or resolve trigger overlap between two skills, read frontier-model-instructions first for the universal prose discipline, then read this skill.
---

# Skill Document Writing

**SOT (canonical):** `cortex://agent-skills/skill-document-writing.md`

```
fs(sandbox="cortex", op="md_read", path="agent-skills/skill-document-writing.md")
```

Do not maintain a second long-form copy here — the cortex file owns the full
authoring lifecycle (decision gate, L1/L2/L3 disclosure, skill_binding,
registration ritual, versioning, supersession, critique checklist).

## Related skills (declared companion list)

Skill→skill companions are **declared**, not scraped from prose. The graph
layer mirrors this list; `scripts/cortex/ingest_skills.py` syncs it to the
`agent_skill` entity on re-ingest.

### Authoring convention

Add **one** of:

1. **Frontmatter** (preferred when the list is stable):

```yaml
related_skills: ["architecture-invariants", "ulg-architecture"]
```

2. **`## Related skills` section** (bare slugs, one per bullet):

```markdown
## Related skills

- architecture-invariants
- ulg-architecture
```

Rules:

- Values are **bare slugs** (`^[a-z0-9-]+$`) — not `agent_skill:` ids, not free prose.
- Directional companions → seeded as `references` relationships at the graph layer.
- Symmetric sibling pairs (rare) → `related_to` only when genuinely bidirectional
  (see thread 2011: build-pipeline↔refine-pipeline, multi-model-review↔review-task-guidance).
- Do **not** use `requires` or `depends_on` for skill→skill links — those types
  are reserved for execution-gating manifests on project/plan/todo entities.
- Do not infer companions by scraping the body — declared list only.

### Ingest + audit

| Surface | Contract |
|---|---|
| `ingest_skills.py` | Parses frontmatter or `## Related skills` → `attributes.related_skills`; `--check` flags drift |
| `detect_agent_skill_related_skills_no_relationship` | Warning when attribute⟷`references`/`related_to` edges drift |

After editing a workspace `.cursor/skills/*/SKILL.md` companion list, run:

```bash
python scripts/cortex/ingest_skills.py
python scripts/cortex/ingest_skills.py --check
```
