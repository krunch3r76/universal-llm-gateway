---
name: skill-document-writing
description: On any task to author, revise, retire, or compress a SKILL.md; run a SkillReducer-shaped compression pass; draft/critique an agent_skill entity; respond to skill_binding audit; or resolve trigger overlap — read frontier-model-instructions, then this skill (cortex SOT), then cortex://notes/system/references/skill-compression-workflow-map.md when compressing.
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

### Ingest + audit (steady state — binding)

**Invariant:** after any edit to a declared companion list, run **`python scripts/cortex/ingest_skills.py`**. That command syncs workspace `.cursor/skills/*/SKILL.md` **and** cortex `agent-skills/*.md` declared lists to the entity `related_skills` attribute **and** seeds matching `references` edges. On attribute⟷edge drift, update the declared list and run ingest again — never the archived prose miner.

| Surface | Contract |
|---|---|
| `ingest_skills.py` | Declared frontmatter or `## Related skills` → `attributes.related_skills` + `references` edges; `--check` flags drift |
| `detect_agent_skill_related_skills_no_relationship` | Warning when attribute⟷edge drift; finding text points at `ingest_skills.py` |
| `archive/bootstrap_skill_sot_prose_miner.py` | **Archived** — one-time F5 bootstrap / prose-mining recovery only; on drift, declare + `ingest_skills.py` |

After editing a companion list (workspace stub **or** cortex SOT):

```bash
python scripts/cortex/ingest_skills.py
python scripts/cortex/ingest_skills.py --check
```
