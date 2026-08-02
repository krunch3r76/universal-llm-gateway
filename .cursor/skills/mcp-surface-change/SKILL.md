---
trigger_match_terms: ["mcp-surface-change", "mcp_surface_change", "edit", "canonical.yaml", "mcp", "surface", "pipelines-rag-mcp", "updating", "change", "adding", "removing", "tools"]
description: 'When updating config/mcp/canonical.yaml for MCP surface change — add/remove tools or domains, promote overflow→primary. Read BEFORE edit.'
---

# Skill: MCP Surface Change

**Trigger**: ∀ time an MCP tool or domain is added to, removed from, or re-scoped in `config/mcp/canonical.yaml` — any surface expansion or contraction.

---

## Context

`config/mcp/canonical.yaml` is the single source of truth for the `/mcp` dispatcher manifest: `derive_claude_manifest()` in `services/mcp-server/_derive.py`, derived at server boot. `services/mcp-server/server.py` builds `_PRIMARY_TOOLS` from that manifest and runs forward + inverse coherence checks at startup (`run_startup_tool_coherence_checks`).

MCP source and the registry are baked into the container image (no source bind-mounts). The deploy verb is `manage(action="sync_restart", service="mcp")` — a cached `--refresh-source` rebuild + restart (~20s). Editing the registry without deploying produces a split-brain between the YAML and the running manifest.

---

## Step 1 — Identify affected domains/tools and the exposure decision

1. Determine which domains or individual tools are being added, removed, or re-scoped.
2. Identify which `seat_visibility` tokens are affected: `mcp`, `mcp_claude`.
3. **Exposure decision for every new `@mcp.tool`** — exactly one of:
   - **First-class**: add a `canonical.yaml` entry (flat + dispatcher call shapes, `seat_visibility`).
   - **Intentional overflow**: add the tool name to `INTENTIONAL_OVERFLOW` in `services/mcp-server/_coherence_allowlist.py` with a "why" comment.
   The CI gate `test_derive_coherence.py::test_ci_invariant_no_coherence_drift` fails when a registered tool is in neither place.
4. If adding a domain: confirm a dispatcher wrapper exists in `services/mcp-server/tools/` or inline in `_build_server()` — a primary domain without a wrapper raises `RuntimeError` at boot.
5. If removing a domain: confirm no active callers depend on the dispatcher tool name.

---

## Step 2 — Update `seat_visibility` in `canonical.yaml` (use ruamel.yaml, ¬ sed)

Use the ruamel.yaml round-trip pattern (preserves comments, flow-style lists, ordering):

```python
from ruamel.yaml import YAML
from pathlib import Path

yaml = YAML()
yaml.preserve_quotes = True
data = yaml.load(Path('config/mcp/canonical.yaml'))

for tool in data['tools']:
    if tool['domain'] == '<target_domain>':
        sv = tool['seat_visibility']
        if 'mcp_claude' not in sv:
            sv.append('mcp_claude')

yaml.dump(data, Path('config/mcp/canonical.yaml'))
```

For bulk edits across many tools, use `scripts/phase-d-registry-edit.py` as the template.

Verify after edit:
```bash
grep -c "mcp_claude" config/mcp/canonical.yaml   # expected: N entries
grep "seat_visibility" config/mcp/canonical.yaml | head -5   # spot-check format
```

If the edit touches `team_dispatch` fol_descriptor role clauses (rosters, seat-maps, enums): those lines are gen-gated — regenerate with `scripts/gen-mcp-dispatch-role-docs`, never hand-edit them.

---

## Step 3 — Re-run derivation checks

```bash
cd /mnt/torus/projects/universal-llm-gateway
source ~/.venvs/universal/bin/activate
```

```python
import sys; sys.path.insert(0, 'services/mcp-server')
from pathlib import Path
from _derive import derive_claude_manifest
cm = derive_claude_manifest(Path('config/mcp/canonical.yaml'))
print(f'MCP domains: {len(cm)} domains')
print('Domains:', sorted(e["domain"] for e in cm))
```

Expected:
- ≤ 24 domains (D3 cap — `RuntimeError` above)
- floor present: {cortex, agent_bus, fs, dispatch} ⊆ domains (`RuntimeError` if breached)

The exact domain count is asserted in `test_derive.py::test_derive_claude_manifest_count` — update that hardcoded count when domains change.

---

## Step 4 — Run tests

```bash
cd services/mcp-server
python -m pytest test_derive.py -v
python -m pytest test_derive_coherence.py -v
python -m pytest test_route_claude.py -v
```

All must pass. If `test_ci_invariant_no_coherence_drift` fails, the new tool is in neither `canonical.yaml` nor `INTENTIONAL_OVERFLOW` — complete Step 1.3. If `test_derive_claude_manifest_count` fails, update the hardcoded count in the test.

When dispatch-role clauses were touched, also run:
```bash
scripts/gen-mcp-dispatch-role-docs --check
python -m pytest services/mcp-server/test_gen_role_docs.py -v
```
`scripts/agent-surface-check` runs `registry-check` + the gen `--check` as the combined CI entry.

---

## Step 5 — Deploy via sync_restart (¬ rebuild) + wait_healthy

```python
# Via MCP manage tool:
manage(action="sync_restart", service="mcp")
manage(action="wait_healthy", service="mcp", timeout=120)
```

`sync_restart` performs the cached `--refresh-source` rebuild that bakes the new `canonical.yaml` into the image. ∀ agent: `manage(action="rebuild", service="mcp")` is FORBIDDEN — the full `--no-cache --pull` rebuild is the heavy path; route dependency, base-image, or Dockerfile changes through the TUI: `./manage` → Services → Build Image. Do NOT start the server manually.

`wait_healthy` is necessary but insufficient: if the build step fails, the controller restarts the old image and health still returns 200. Verify the deploy actually landed:

```bash
docker images universal-mcp-server --format "Created: {{.CreatedAt}}"   # ≤ ~5 min ago
docker inspect mcp-server --format '{{.State.StartedAt}}'              # after restart initiation
```

`/health` carries the deploy stamp from `_deploy_stamp.py`: `deploy_mode: "source_synced"` + a fresh `source_synced_at` confirm synced source, not a stale image.

---

## Step 6 — Verify boot shadow log

After healthy, confirm the manifest boot event via the Event Service:

```text
observability(operation="raw_sql", params={"sql":
  "SELECT ts, payload FROM events WHERE signal='mcp.server.claude.manifest.boot' ORDER BY ts DESC LIMIT 1"})
observability(operation="raw_sql", params={"sql":
  "SELECT ts FROM events WHERE signal='mcp.oauth.server.started' ORDER BY ts DESC LIMIT 1"})
```

Expected on `mcp.server.claude.manifest.boot`:
- `domain_count`: matches the post-change domain count
- `names_sha256`: stable across restarts iff `canonical.yaml` unchanged

`mcp.oauth.server.started` `ts` must postdate the restart. If `domain_count` is wrong, the container is running a stale image — re-run Step 5 verification.

---

## Invariants (FOL)

∀ surface change: canonical.yaml edit ⟹ sync_restart ∧ wait_healthy ∧ deploy-stamp + shadow-log verified.
∀ agent: ¬ manage(rebuild, mcp) — TUI Build Image only.
∀ registered tool T: T ∈ canonical.yaml ∨ T ∈ INTENTIONAL_OVERFLOW ∨ CI failure.
∀ MCP domain D added: D ∈ {dispatcher wrappers in _build_server()} ∨ RuntimeError at boot.
|MCP domains| ≤ 24 ∨ RuntimeError (D3 cap).
{cortex, agent_bus, fs, dispatch} ⊆ MCP domains ∨ RuntimeError (floor assertion).
∀ team_dispatch roster clause in canonical.yaml: gen-gated by scripts/gen-mcp-dispatch-role-docs — ¬ hand-edit.

---

## Reference files

| File | Purpose |
|---|---|
| `config/mcp/canonical.yaml` | Registry — single SOT for the `/mcp` manifest |
| `services/mcp-server/_derive.py` | `derive_claude_manifest()`, coherence validators |
| `services/mcp-server/server.py` | `_PRIMARY_TOOLS` derived from the manifest; startup coherence checks |
| `services/mcp-server/_coherence_allowlist.py` | `INTENTIONAL_OVERFLOW` allowlist for deliberately-overflow tools |
| `services/mcp-server/_deploy_stamp.py` | `/health` deploy stamp (`deploy_mode`, `source_synced_at`) |
| `services/mcp-server/test_derive.py` | Manifest derivation tests (count, cap, floor, op_skills) |
| `services/mcp-server/test_derive_coherence.py` | Forward/inverse coherence + CI drift gate |
| `services/mcp-server/test_route_claude.py` | MCP route integration tests |
| `services/mcp-server/test_gen_role_docs.py` | Dispatch-role advertisement drift falsifier |
| `scripts/gen-mcp-dispatch-role-docs` | Regenerates gen-gated role clauses (`--check` = CI gate) |
| `scripts/agent-surface-check` | Combined CI entry: registry-check + gen --check |
| `scripts/phase-d-registry-edit.py` | Template for bulk seat_visibility edits (ruamel.yaml) |
