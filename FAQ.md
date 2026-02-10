# FAQ

## Where are event logs stored?

**Stargate**: `/tmp/stargate-events/current.jsonl`
**Gateway**: `/tmp/_universal-gateway-events/current.jsonl`

Format: JSONL (one event per line), automatic rotation at 50MB (3 files max).

## How does `deploy-gpu-relay.sh` configure event logs?

| Component | Process Type | Logs Location |
|-----------|--------------|---------------|
| Master Stargate (localhost) | Native | Host: `/tmp/stargate-events/` |
| Relay Stargate (jupiter) | Native | jupiter: `/tmp/stargate-events/` |
| Edge Stargate (localhost) | Container | In-container: `/tmp/stargate-events/` |
| Edge Stargate (jupiter) | Container | In-container: `/tmp/stargate-events/` |
| Edge Gateway (localhost) | Container | In-container: `/tmp/_universal-gateway-events/` |
| Edge Gateway (jupiter) | Container | In-container: `/tmp/_universal-gateway-events/` |

**Config:** All use YAML `debug_events.persistence` (enabled by default)  
**Container logs:** Ephemeral unless mounted to host

### Access container logs

```bash
# View
docker exec edge-localhost tail -f /tmp/stargate-events/current.jsonl
docker exec edge-localhost tail -f /tmp/_universal-gateway-events/current.jsonl

# Copy
docker cp edge-localhost:/tmp/stargate-events/current.jsonl ./tmp/
docker cp edge-localhost:/tmp/_universal-gateway-events/current.jsonl ./tmp/
```
