<!-- target:* -->
# YAML Field Naming

**Self-documenting pipeline YAML fields.**

## Invariant

∀ domain field in pipeline YAML: the field name describes its **effect**, not its implementation mechanism.

A YAML author should understand what a field does without reading handler source code.

## Examples

| ❌ Implementation-leaked | ✅ Self-documenting | Why |
|---|---|---|
| `context_prompt_ref` | `system_prompt_ref` | It becomes the system prompt — "context" is vague |
| `pre_assess_action` | `initial_action` | It's the initial action — "pre_assess" is a handler implementation detail |

## Enforcement

A handler's config parser validates renamed fields at load time via an
old-name→new-name mapping table. Using an old name raises a clear error with a
migration message pointing to the new name.

∀ handler rename: add old→new mapping to the handler's config parser so stale YAML
fails loudly with a fix instruction, not silently.

## When Adding New Domain Fields

1. Name describes the field's observable effect from the YAML author's perspective
2. If the name requires a comment to explain what it does, rename it
3. No framework jargon (`pre_assess`, `context`, `dispatch`, `callback`)
4. Prefer: `system_prompt_ref`, `initial_action`, `assess_handler`, `terminal_action`
<!-- /target:* -->
