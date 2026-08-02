---
name: add-mcp-tool
description: Add a new primary tool to user-vortex — implement under services/mcp-server/tools/, register in server.py, promote via config/mcp/canonical.yaml, sync_restart.
trigger_match_terms: ["add-mcp-tool", "add_mcp_tool", "add", "new", "primary", "mcp", "tool", "pipelines-rag-mcp", "user-vortex", "implement", "under", "services"]
---

## Related skills

- architecture-invariants
- mcp-surface-change

# Add MCP Tool

## Checklist (all steps required — do not skip)

### 1. Implement the tool

Create or modify a file under `services/mcp-server/tools/`.

- Tool function decorated with `@mcp.tool()` (via FastMCP)
- Register via a `register_*_tools(mcp: FastMCP)` function (same pattern as all other tool files)
- Docstring: include workflow guidance — when to use this tool over alternatives
- Follow `uds-only-transport.mdc`: use `make_async_client` + `transport_utils` constants
- Follow `mcp-tool-relay-inv.mdc`: thin HTTP relay to a REST endpoint, ¬business logic
- **REST endpoint is the descriptor source of truth.** Per skill
  `architecture-invariants` § OpenAPI authorship
  (`[universal:rest:openapi]` + MCP-wrapper corollary): the underlying FastAPI
  route's Pydantic request/response models + signature ARE the contract.
  The MCP tool's argument schema, response shape, and docstring SHOULD track
  the route's `/openapi.json` entry — ¬ define a parallel schema at the MCP
  layer. If the REST endpoint does not yet exist, build it first (with
  `response_model=`, `status_code=`, `summary=`, docstring) so the
  auto-generated `/openapi.json` is informative; the MCP tool then mirrors it.
- Carve-out: if the tool wraps a local vendor-CLI subprocess (e.g.
  `manage`), the `[universal:mcp:subprocess]` carve-out applies instead of the
  thin-relay rule. See architecture-invariants for the full discipline.
- **Adversarial-caller / failure-returns-success lens (assertion 21250).** Before
  shipping any param-signature change — especially on a *unified-dispatch* tool
  (`fs`, `files`, `markdown`) where one shared signature spans many ops — model the
  *confused* caller, not just the correct one: (a) for every op, what does it do with
  a param it does NOT consume? An accepted-but-ignored param MUST 422, never silently
  no-op (a no-op'd selector turns a destructive miss into a success response). (b) Does
  any optional selector default to a *destructive* target (e.g. empty `section` ==
  file preamble)? Destructive ops MUST NOT have a silent destructive default — require
  the selector (422 when omitted). Correctness review alone will pass these because
  each local piece is correct; the bug is emergent at the param × op cross-product.

### 2. Call `register_*_tools` from `server.py`

In `services/mcp-server/server.py`, inside `_build_server()`, add:

```python
from tools.<module> import register_<name>_tools
# ...
register_<name>_tools(mcp)
```

### 3. Promote to primary via `canonical.yaml` ← CRITICAL — this is the step most likely to be missed

**Invariant**: ∀ new MCP tool that should be visible in the Cursor tool catalog
(i.e. callable by agents directly, not via `dispatch`): the tool's entry in
`config/mcp/canonical.yaml` MUST include `mcp_claude` in `seat_visibility`.

`_PRIMARY_TOOLS` in `services/mcp-server/server.py` is derived at boot from
`derive_claude_manifest()` reading `canonical.yaml` — do NOT add names to a
hardcoded set. Edit the registry instead.

For a new tool not yet in `canonical.yaml`, add an entry following the existing
schema (domain, seat_visibility, etc.) using the ruamel.yaml round-trip pattern
from skill `mcp-surface-change` § Step 2:

```python
from ruamel.yaml import YAML
from pathlib import Path

yaml = YAML()
yaml.preserve_quotes = True
data = yaml.load(Path('config/mcp/canonical.yaml'))

for tool in data['tools']:
    if tool['domain'] == '<your_domain>':
        sv = tool['seat_visibility']
        if 'mcp_claude' not in sv:
            idx = sv.index('mcp_grok') if 'mcp_grok' in sv else len(sv)
            sv.insert(idx, 'mcp_claude')

yaml.dump(data, Path('config/mcp/canonical.yaml'))
```

**Detection**: after `sync_restart`, run:
```bash
docker logs mcp-server 2>&1 | grep "Overflow tools"
```
Any tool listed there is NOT primary — add `mcp_claude` to its `seat_visibility`
in `canonical.yaml` if it should be primary.

### 4. Quality gate

```bash
source ~/.venvs/universal/bin/activate
cd /mnt/torus/projects/universal-llm-gateway
ruff check services/mcp-server/tools/<module>.py services/mcp-server/server.py
ruff format --check services/mcp-server/tools/<module>.py services/mcp-server/server.py
python -m compileall -q services/mcp-server/tools/<module>.py services/mcp-server/server.py
```

### 5. Deploy

Per `mcp-lifecycle_ws.mdc`: source edits require `sync_restart` (cached rebuild, ~20s).

Ask the user to run:
```bash
./scripts/sync-and-restart-mcp.sh
```

### 6. Verify the tool is primary (not in overflow)

After restart:
```bash
docker logs mcp-server 2>&1 | grep -E "Tool pruning|Overflow tools"
```

Expected:
- `Tool pruning: N primary ...` — N should have increased by 1
- `Overflow tools` line should NOT include your new tool name

### 7. Refresh Cursor descriptors and reload

`sync-and-restart-mcp.sh` runs `refresh-cursor-mcp-descriptors` automatically.
After it completes, tell the user to reload the Cursor window:
`Ctrl+Shift+P → Developer: Reload Window`

Verify the descriptor file exists:
```bash
ls ~/.cursor/projects/mnt-torus-projects-universal-llm-gateway/mcps/user-vortex/tools/<tool_name>.json
```

## Common Mistakes

| Mistake | Consequence | Fix |
|---|---|---|
| Skip `_PRIMARY_TOOLS` entry | Tool invisible to agents; no error at startup (pre-fix) | Add name to `_PRIMARY_TOOLS` in `server.py` |
| Use `frontier_dispatch` as an example for routing | `boot` param removed — use `team_dispatch` (role) or `frontier_dispatch` (model-required) | Read `tools/frontier.py` |
| Direct DB or import in tool body | Violates `mcp-tool-relay-inv.mdc` | Make it a thin HTTP relay via `make_async_client` |
| Forget `sync_restart` (use plain restart) | Image not rebuilt; old code runs | `sync_restart` for MCP always (source is baked into image) |
