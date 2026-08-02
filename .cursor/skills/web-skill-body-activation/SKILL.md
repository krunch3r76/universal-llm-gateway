---
name: web-skill-body-activation
description: "On web-anthropic required-gate/decision points: use verified inline-authoritative packet skills or activate server skills with canonical slug. Not description-match; not Channel-A rebuild."
applicable_agents: ["web-anthropic"]
trigger_match_terms: ["web-skill-body-activation", "web_skill_body_activation", "skill body", "activation", "use the", "web-anthropic", "/mnt/skills", "Channel-A", "description-match", "self-fetch", "required-gate"]
related_skills: ["lead-seat-boot", "required-skills-pickup", "skill-document-writing"]
---

# Web Skill-Body Activation

web-anthropic only. Locked mechanism: `decision:web-seat-skill-body-delivery` (a23708 / a23707).

## FOL pipeline

```text
required_gate ∨ decision_point
  ⇒ if inline_authoritative_packet(slug)
       verify(slug ∧ source_uri ∧ revision ∧ sha256 ∧ affordance_framing)
       ⇒ apply(inline_body)
     else
       resolve(slug ∈ in_context_skill_inventory)
       ⇒ emit("Use the {slug} skill")
       ⇒ seat_self_fetch(body)
       ; invocation_fail ⇒ fallback(/mnt/skills/{slug}/SKILL.md)
```

## Invariant

`∀ required_gate ∨ decision_point on web-anthropic:
activate(skill) ⇔ verified_inline_body ∨ Use_the_{slug}_skill`.

For inline-authoritative web packets, the packet body is the skill delivery and
`Use the {slug} skill` must not be duplicated. Otherwise, `Use the {slug} skill`
triggers genuine body fetch. Passive name/description match does **not**. Bare
slug mention without verb `use` is insufficient.

## Activation (primary)

1. If the packet contains `skill-inline:{slug}`, verify its source, revision,
   digest, and affordance framing; apply that body and do not self-fetch it.
2. Otherwise resolve `{slug}` against the **in-context skill inventory**
   (Customize→Skills / boot gate list / packet `required_skills`) — ¬ blind walk
   of `/mnt/skills`.
3. Emit **Use the `{slug}` skill** and apply the fetched body before proceeding.

## Forbidden

| Forbidden | Why |
|---|---|
| Rely on description-match / name-alone to push bodies | Passive match does not deliver body |
| Put inline code skill plus `Use the <slug> skill` in one packet | Dual channel is rejected |
| Rebuild Channel-A `cortex_brief` body-inject | Retired — do not restore |
| Blind `/mnt/skills` browse as discovery | Inventory is in-context; path walk is not the resolver |
| Treat bare slug mention as activation | Turn-5 probe: no verb ⇒ no fetch |

## Fallback (invocation fail only)

`invocation_fail ∨ body_absent_after_Use ⇒ view|/mnt/skills/{slug}/SKILL.md` (or `fs` read of that path).

Fallback is discipline when primary self-fetch fails — **not** the default activation path. Do not open with `/mnt/skills` when `Use the {slug} skill` is available.

## Out of scope (deferred — a23711)

- Other verbs (`load` / `read` / `invoke`) — untested vs confirmed `use` for server delivery
- Cursor / MCP seat explicit-invocation probe

Do not encode those verbs as equivalents until stamped.

## Evidence

`decision:web-seat-skill-body-delivery` · a23708 · a23707 · agent-bus:4888 · parent checkpoint wave 4 (agent-bus:4885).
