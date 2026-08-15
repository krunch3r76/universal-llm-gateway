# CSE provenance identity chain

This guide documents how Cowork CSE identity (`chat_url`) joins registry host
receipts to agent-bus lane lineage without inference.

## Authorities

| Layer | Authority | What it proves |
| --- | --- | --- |
| Registry JSONL | URL → host receipt | Which registration held a normalized CSE URL at bind time |
| Agent-bus SQLite | Lane lineage | Current `parent_thread`, `lane_role`, and `association_id` for a lane thread |
| CSR / resolvers | Projection | Typed `current`, `historical`, `unregistered_cse`, `unresolved`, or `conflict` views |

Event Service `cdp.provenance.*` signals are observability mirrors only. Never
use them as the join authority.

## Stored episode fields

Journal episodes retain baseline fields (`lane_thread`, `parent_thread`,
`lane_role`) plus durable lineage metadata:

- `lineage_state` — `claimed`, `unresolved`, or `proven`
- `association_id` — bus proof id (proven rows only)
- `lineage_observed_at` — reader timestamp on enrichment or overlay

`lane_thread` at bind is a **registry claim**. `parent_thread` and `lane_role`
are copied only from an explicit `lineage=` mapping supplied by the proof writer
(hub enrichment). A claim never becomes proof by elapsed time or naming.

## Receipt versus proof

**Registry host receipt** (`bind_session_address`, dormant episodes):

- Appends an immutable JSONL episode; `lane_thread` carries the registry claim
- Sets `lineage_state=unresolved` when `lane_thread` is empty
- Emits `cdp.provenance.unresolved` with `reason=lane_less_bind` for lane-less binds
- Does **not** refuse the bind

**Bus lineage proof** (hub `enrich_request_provenance` only):

- Reads `GET /threads/{id}/lane-current` via MCP relay (`cse_lineage_reader`)
- Appends a supersede-linked episode with `lineage_state=proven`,
  `association_id`, and `lineage={"parent_thread": ..., "lane_role": ...}`
- Never mutates prior journal bytes

## Nested host and lineage state

Host `state` (current/historical/unregistered_cse/conflict) and nested
`lineage_state` (claimed/unresolved/proven) are separate axes. A listable host
with missing bus lineage is `state=current` plus
`lineage_state=unresolved|claimed`. URL lookups with no journal episode are
`state=unregistered_cse` (silent — no unresolved event). Registered episodes
with missing or unreachable bus proof remain `state=current|historical` while
`lineage_state=unresolved`.

## Inference prohibition

The following must **never** become bus proof without an evidence-bearing
`association_id`:

- Registry `lane_thread` / `parent_thread` (Chrome-host claim)
- Port, profile, or page-scan observations
- Shadow URL sightings on foreign ports
- Historical lane associations or closed threads
- Event Service payloads

Satellite code (`libs/cdp_ask`) must not import `agent_bus_store.db` or call
`get_current_lane` directly. Hub MCP tools relay read-only lineage.

## Resolution states

| Host state | Meaning |
| --- | --- |
| `current` | Latest episode for the URL; registration still listable |
| `historical` | Retained episode; registration released — no proven/current lane fields |
| `unregistered_cse` | No episode for the URL — silent lookup (no unresolved event) |
| `conflict` | Multiple hosts or registrations compete for one URL |

Nested `lineage_state` on registered episodes: `claimed`, `unresolved`, or
`proven`. Missing or unreachable bus proof surfaces as `lineage_state=unresolved`
with typed `reason` values (`lane_lineage_missing`, `lane_lineage_none`,
`lane_lineage_unreachable`) — not as a top-level host `state`.

Dense resolve payloads expose `lane_thread_claim`, `parent_thread_claim`,
`lane_role_claim` from the latest non-proven receipt, and `lane_thread_proven`,
`parent_thread_proven`, `lane_role_proven`, and `association_id` only when the
selected episode is proven with an association id.

## Key modules

- `libs/claude_bundles/cse_provenance.py` — append/read episodes
- `libs/claude_bundles/cse_provenance_resolve.py` — typed resolve
- `services/mcp-server/tools/agent_bus/cse_provenance_enrich.py` — sole proven writer
- `services/mcp-server/tools/agent_bus/cse_lineage_reader.py` — relay adapter
- `libs/cdp_ask/attended_conflict.py` — purpose-filtered URL conflict sweep

## Verification

```bash
"$HOME/.venvs/universal/bin/pytest" libs/claude_bundles/test_cse_provenance.py -q
"$HOME/.venvs/universal/bin/pytest" services/mcp-server/tools/agent_bus/test_cse_lineage_reader.py -q
"$HOME/.venvs/universal/bin/pytest" services/mcp-server/tools/agent_bus/test_cse_provenance_enrich.py -q
```
