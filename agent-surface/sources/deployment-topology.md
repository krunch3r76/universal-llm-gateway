<!-- target:* -->
# Deployment Topology

## Roles

| Role | Process | Gateway | Function |
|------|---------|---------|----------|
| Master | Host | None | Orchestrates, routes to Edges |
| Relay | Host | None | Bridges Master ↔ network-isolated Edge |
| Edge | Container (network-isolated) | Colocated | Executes inference |

Mode detection: presence of an edge gateway manager on the process ⟹ Edge

## Standard Topology

```
Master (host)
  ├─ local edge (Unix socket) → Edge-localhost (container) → Gateway
  └─ remotes (TCP) → Relay → Edge-remote (UDS) → Gateway
```

## Port Reference

| Port role | Accessible to | Notes |
|------|---------------|-------|
| Stargate (host, external) | Clients, agents, curl | Sole external endpoint |
| Gateway (container loopback) | Edge Stargate only, via localhost inside the container | Never reachable from host or clients |
| git-integration worker (host loopback) | Stargate git proxy, MCP git_* tools | Host-managed process; not containerized |

**Host workers (default deploy)**: the git-integration worker is **not**
containerized and **not** systemd-managed by default. Optional unit files exist
for operator-installed shapes only.

## Remote Access

Remote nodes are SSH-accessible from the workspace when configured.

```bash
ssh <user>@<remote-host> tail -50 <events-log-path> | jq -c '.'      # remote events
ssh <user>@<remote-host> docker logs --tail 100 <edge-container>     # remote edge logs
```

## Config

| File | Purpose |
|------|---------|
| Master config file | Generated on first start, never overwritten |
| Per-node env file | NODE_ID, MODEL_PATH, federation keys |
| Local env override | MODEL_PATH_ROOT |

## Investigation

**Step 0 — ALWAYS read events first**:
```bash
tail -50 <events-current.jsonl> | jq -c '.'             # events (START HERE)
```

Then proceed with structural checks:
```bash
lsof -i:<gateway-port>,<stargate-port>                               # ports
docker ps --filter "name=edge"                                       # containers
curl -s "http://localhost:<stargate-port>/v1/models?include_sources=true" | jq .  # model sources
tail -f <master-log-path>                                            # master logs
docker logs -f <edge-container>                                      # edge logs
```

## Empty `/v1/models` — start here

1. **Events**: filter events for `federation`-prefixed signals
2. `?include_sources=true` → check `total_sources` (0 = no gateways connected)
3. Master logs: "No gateways available" → federation/telemetry issue
4. Edge logs: registry, catalog loading → Gateway not reporting models
5. Activated-contexts config → filtering reduces list to empty

## Live Topology Snapshot

A topology command prints current topology as YAML and writes it to a cached
file. Agents should read that cached file for current deployment state
(master/edge/remote status, model count, ASCII diagram) rather than re-probing
live services on every query.

Auto-refresh: a background UI rebuilds the snapshot periodically, keeping it
fresh for agents during active sessions. A manual refresh command re-probes
services and rewrites the file.
<!-- /target:* -->
