<!-- target:* -->
# Federation Architecture

**Distributed routing** across network-isolated Gateways (gateway domain).

## Canonical Principles
> Relay = stateless forwarding, not containment
> Topology = reachability, not ownership
> Master = pure orchestrator, no Gateway

## Roles
| Role | exec | Gateway |
|------|------|---------|
| Master | ✗ | None |
| Relay | ✗ | None |
| Edge | ✓ | Required |

**Invariant**:
```
∀ Stargate: role ∈ {Master, Relay, Edge}
Master: execution_capable = false ∧ gateway = null
Edge: execution_capable = true ∧ has_local_gateway
∀ Edge: ∃ path Master → Edge
```

## Separation
| Component | Plane | Authority |
|-----------|-------|-----------|
| Master | Control | Advisory |
| Remote | Relay | None |
| Gateway | Execution | Authoritative |

## Connection
```
Remote ─WebSocket→ Master (telemetry)
Remote ←HTTP─── Master (requests)
```

## Critical Invariants
| ID | Rule | Symptom |
|----|------|---------|
| FED-01 | One mode per Stargate | Multiple modes |
| FED-08 | ping_interval ≤ 30s | WS timeout |
| FED-11 | max_reconnect ≤ 30s | Prolonged outage |
| REM-01 | path ∈ ALLOWED ∨ 403 | Security bypass |

## Anti-Patterns
❌ Missing telemetry wiring
❌ FederatedGatewayManager without `start()`
❌ Unbounded backoff (>30s)
❌ Remote orchestrates loads independently

## Config
Federation config lives under the Stargate config file's `federation:` block.
Env values use `${VAR_NAME}` substitution, fail-fast on missing values.
<!-- /target:* -->
