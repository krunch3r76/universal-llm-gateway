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

`∀ prose/bus/skills: ¬personal_name(Claude)` — naming preference order
(best → worst): `web-anthropic` → `anthropic` → `claude.ai` (**least
preferred** — product label only when the third-party UI itself is the
subject). Bus `from`/`to` use endpoint addresses (`web-anthropic`,
`cursor`), never `claude` as a name. Model artifact strings and permanent
aliases remain.

Do not load this skill for turn closure. Substrate already projects seat→family
on `seeded_by`; bus addressing carries `from_agent`/`to`.
<!-- /target:* -->
