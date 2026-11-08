<!-- frontmatter:skill
name: agent-identity-signoff
description: "RETIRED — endpoint provenance is always-on stub only; do not load this skill."
-->
<!-- target:* -->
# Endpoint Provenance — RETIRED skill

**Status:** `lifecycle=retired` (2026-07-11). Operational surface is the
always-on Cursor stub `.cursor/rules/agent-identity-signoff.mdc` only.

Doctrine archive: `decision:identity-doctrine-endpoint-provenance`.

## Binding invariant (retained)

`∀ assistant turn closure: ¬sign_off` — who-did-what is machine provenance
(`seeded_by`, `caller_agent`, `from_agent`, execution records, model strings),
never a signed or asserted identity.

`∀ boot/dispatch prompt: ¬assert_identity_at_agent` ("you are X" banned).

`∀ prose/bus/skills: ¬personal_name(Claude)` except work done on claude.ai (see naming order). Naming preference order
(best → worst): `web-anthropic` → `Claude` (work done on claude.ai) → `anthropic` → `claude.ai` (UI label only). Bus `from`/`to` use endpoint addresses (`web-anthropic`,
`cursor`), never `claude` as a name. Model artifact strings and permanent
aliases remain.

**Bus subjects / WIP / cover letters (recurrence — friction 24421):**
✓ `chain to Claude` · `web-anthropic final` ✗ `claude-web final` (runtime alias, not a name)
`claude.ai` only when the third-party product UI itself is the subject
(e.g. Customize Skills upload), never as a seat shorthand.

Do not load this skill for turn closure. Substrate already projects seat→family
on `seeded_by`; bus addressing carries `from_agent`/`to`.
<!-- /target:* -->
