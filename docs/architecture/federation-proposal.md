# Stargate Federation: Network-Isolated Gateway Distribution

**Document Type:** Architecture Design Document  
**Date:** 2026-01-06  
**Status:** Approved for Implementation  
**Aligned With:** [VISION.md](../VISION.md) — 12-Week Tokenized Public Inference Network Roadmap  

---

## Table of Contents

| Section | Scope | Description |
|---------|-------|-------------|
| [1. Executive Summary](#1-executive-summary) | ALL | Problem, goals, scope separation |
| [2. System Overview](#2-system-overview) | ALL | Component definitions, current architecture |
| [3. Problem Statement](#3-problem-statement) | ALL | Security requirements, breaking changes |
| [4. Formal Problem Definition](#4-formal-problem-definition) | ALL | FOL definitions and invariants |
| [5. Solution: Stargate Federation](#5-solution-stargate-federation) | MVP | Core concept, modes, topology |
| [6. FederatedGateway Abstraction](#6-federatedgateway-abstraction) | MVP | Class design with error handling |
| [7. Failure Handling](#7-failure-handling) | MVP | Partial failures, recovery, staleness |
| [8. Security Model](#8-security-model) | MVP | Authentication, transport security |
| [9. Implementation Phases](#9-implementation-phases) | MVP | Phase 1-4 detailed specifications |
| [10. Diagrams](#10-diagrams) | MVP | Machine-parseable topology diagrams |
| [11. Consultation Response](#11-consultation-response) | MVP | Architecture validation, decisions |
| [12. Post-MVP: Hierarchical Federation](#12-post-mvp-hierarchical-federation) | POST-MVP | Multi-Master scaling |
| [13. Post-MVP: Tokenized Network](#13-post-mvp-tokenized-network) | POST-MVP | Blockchain integration |
| [14. Appendix](#14-appendix) | REF | Codebase structure, file locations |

---

## 1. Executive Summary

### 1.1 System

**Universal LLM Gateway** — a distributed inference routing system with:
- **Stargate**: Request coordinator/router (port 9999)
- **Gateway**: Inference orchestrator with model lifecycle (port 9998)
- **Worker**: Model execution engine (llama-cpp-python)

### 1.2 Problem

Gateway instances must run in network-isolated containers (`network_mode: none`), breaking direct HTTP routing from Stargate coordinators to remote Gateways.

### 1.3 Goals

```
GOAL_MVP:
  Design a federation protocol allowing a single Master Stargate to route 
  requests to Remote Stargates with local network-isolated Gateways.
  
  Scope: Single Master, N Remote Stargates, N isolated Gateways
  Timeline: Weeks 2-4 (VISION.md Phases 1-2)

GOAL_POST_MVP:
  Extend to hierarchical/multi-Master federation and public tokenized networks.
  
  Scope: Multi-Master, dynamic discovery, blockchain integration
  Timeline: Weeks 5-12 (VISION.md Phases 3-7)
```

### 1.4 Quick Reference

| Term | Definition |
|------|------------|
| Master Stargate | Receives client requests, makes routing decisions |
| Remote Stargate | Connects to local Gateway, exposes telemetry to Master |
| FederatedGateway | Abstraction wrapping Remote Stargate as Gateway for DecisionEngine |
| T0/T1/T2 | Feasibility tiers (Infeasible/Feasible/Feasible-with-eviction) |

---

## 2. System Overview

### 2.1 Component Definitions

```yaml
# COMPONENT_REGISTRY
components:
  - id: STARGATE
    role: Coordinator/Router
    port: 9999
    modes: [MASTER, REMOTE, STANDALONE]
    
  - id: GATEWAY
    role: Inference Orchestrator
    port: 9998  # or Unix socket
    isolation: network_mode_none
    
  - id: WORKER
    role: Inference Engine
    protocol: IPC (Unix Domain Socket)
    engine: llama-cpp-python
```

### 2.2 Communication Channels

```yaml
# CHANNEL_REGISTRY
channels:
  - id: CLIENT_TO_STARGATE
    protocol: HTTP/SSE
    direction: request-response
    
  - id: STARGATE_TO_GATEWAY
    protocol: HTTP | UNIX_SOCKET
    direction: request-response
    purpose: inference_forwarding, model_loading
    
  - id: GATEWAY_TO_STARGATE
    protocol: WebSocket
    direction: push
    purpose: telemetry (resources, model_state)
    
  - id: STARGATE_TO_STARGATE
    protocol: HTTP + WebSocket
    direction: bidirectional
    purpose: federation (request_forwarding, telemetry_aggregation)
    
  - id: GATEWAY_TO_WORKER
    protocol: UNIX_SOCKET (IPC)
    direction: bidirectional
    purpose: model_operations
```

### 2.3 Telemetry Protocol

```yaml
# MESSAGE_TYPES (Gateway → Stargate WebSocket)
messages:
  gateway_to_stargate:
    - type: INIT
      payload: { version, total_vram, total_ram, loaded_models, catalog }
      
    - type: RESOURCE_UPDATE
      payload: { available_vram_mb, available_ram_mb, loaded_models }
      
    - type: MODEL_LOADED
      payload: { model_id, vram_used, ram_used }
      
    - type: MODEL_UNLOADED
      payload: { model_id, freed_vram, freed_ram }
      
    - type: MODEL_BUSY
      payload: { model_id }
      
    - type: MODEL_IDLE
      payload: { model_id }
      
    - type: CATALOG_UPDATE
      payload: { models[] }

  stargate_to_gateway:
    - type: PONG
      payload: { }
      
    - type: QUERY
      payload: { type, params }
```

### 2.4 Routing Decision Engine

```yaml
# FEASIBILITY_TIERS
tiers:
  T0:
    symbol: ⊥
    condition: unhealthy OR no_capacity OR model_too_large
    action: SKIP_OR_QUEUE
    
  T1:
    symbol: ✓
    condition: (model_loaded AND has_capacity) OR free_resources_sufficient
    action: ROUTE_IMMEDIATELY
    
  T2:
    symbol: ◇
    condition: can_fit_after_evicting_idle_models
    action: EVICT_THEN_ROUTE
```

**Selection Invariant (FOL):**
```
∀ request r, ∃ decision d:
  d = select(r) ⟹
    (∃ gw: tier(gw, r) = T1) → d.gateway ∈ {gw | tier(gw, r) = T1}
  ∧ (∀ gw: tier(gw, r) ≠ T1 ∧ ∃ gw': tier(gw', r) = T2) → d.gateway ∈ {gw | tier(gw, r) = T2}
  ∧ (∀ gw: tier(gw, r) = T0) → d = QUEUE
```

---

## 3. Problem Statement

### 3.1 Security Requirement

```yaml
# ISOLATION_REQUIREMENT
gateway_container:
  network_mode: "none"  # No network access
  volumes:
    - /tmp/gateway.sock:/tmp/gateway.sock  # Unix socket only
    
rationale:
  - Gateways load arbitrary model files (attack surface)
  - Workers execute inference code (untrusted computation)
  - Network isolation prevents data exfiltration, lateral movement
```

### 3.2 Breaking Change

```yaml
# BROKEN_CAPABILITIES
with_network_mode_none:
  gateway_tcp_port: BLOCKED  # Cannot expose 9998
  stargate_http_to_gateway: BLOCKED  # Cannot forward HTTP
  gateway_websocket_to_stargate: BLOCKED  # Cannot initiate WS
  unix_socket: ALLOWED  # Filesystem-based, not network
```

### 3.3 Current Partial Solution

Unix socket transport exists for single-host:
```yaml
gateways:
  - name: localhost
    socket_path: /tmp/gateway.sock
```

**Limitation:** Cannot route to Gateways on remote hosts.

---

## 4. Formal Problem Definition

### 4.1 Definitions

```
SETS:
  S = {s₁, s₂, ..., sₙ}           -- Stargate instances
  G = {g₁, g₂, ..., gₘ}           -- Gateway instances
  W = {w₁, w₂, ..., wₖ}           -- Worker instances

FUNCTIONS:
  local: G → S
    -- Maps Gateway to its local Stargate (same host)
    
  reachable_tcp: S × S → {true, false}
    -- TCP/IP network-level reachability between Stargates
    -- NOTE: This is transport-layer reachability, not logical federation
    
  isolated: G → {true, false}
    -- Gateway network isolation status (Docker network_mode)
    
  mode: S → {MASTER, REMOTE, STANDALONE}
    -- Stargate operating mode
```

### 4.2 Constraints

```
-- All Gateways are network-isolated
CONSTRAINT_ISOLATION:
  ∀ g ∈ G: isolated(g) = true

-- All Stargates can reach each other via TCP
CONSTRAINT_STARGATE_REACHABILITY:
  ∀ sᵢ, sⱼ ∈ S: reachable_tcp(sᵢ, sⱼ) = true

-- Each Gateway has exactly one local Stargate
CONSTRAINT_LOCAL_OWNERSHIP:
  ∀ g ∈ G, ∃! s ∈ S: local(g) = s

-- Exactly one Master in federation
CONSTRAINT_SINGLE_MASTER:
  ∃! s ∈ S: mode(s) = MASTER
```

### 4.3 Problem Formalization

```
-- BROKEN: Cannot forward HTTP to remote Gateway
∀ g ∈ G, s ∈ S where s ≠ local(g):
  ¬http_forward(s, g, request)
  ∧ ¬ws_connect(g, s)

-- GOAL: Restore routing via permitted channels
∀ request r, ∃ g ∈ G:
  tier(g, r) ∈ {T1, T2} ⟹ 
    ∃ path: [client, s_master, ..., g]
    where ∀ hop ∈ path: permitted(hop)

-- PERMITTED_CHANNELS:
  TCP(sᵢ, sⱼ)   -- Stargate-to-Stargate (network)
  UNIX(s, g)    -- Stargate-to-local-Gateway (filesystem)
  
  ∀ sᵢ, sⱼ ∈ S: permitted(TCP(sᵢ, sⱼ)) = true
  ∀ s ∈ S, g ∈ G: permitted(UNIX(s, g)) ⟺ s = local(g)
```

---

## 5. Solution: Stargate Federation

> **SCOPE: MVP (Weeks 2-4)**

### 5.1 Core Concept

```yaml
# FEDERATION_ARCHITECTURE
layers:
  - name: Client Layer
    components: [Client]
    
  - name: Master Layer
    components: [Master Stargate]
    responsibilities:
      - Receive client requests
      - Aggregate telemetry from all sources
      - Make routing decisions (DecisionEngine)
      - Forward to local Gateway OR Remote Stargate
      
  - name: Remote Layer
    components: [Remote Stargate (N instances)]
    responsibilities:
      - Connect to local Gateway via Unix socket
      - Expose /ws/federation endpoint for Master
      - Accept proxied requests from Master
      - Forward requests to local Gateway
      
  - name: Gateway Layer
    components: [Gateway (N instances, isolated)]
    responsibilities:
      - Manage model lifecycle
      - Route to Workers via IPC
      - Emit telemetry to local Stargate
```

### 5.2 Stargate Modes

```yaml
# MODE_DEFINITIONS
modes:
  MASTER:
    receives_client_requests: true
    aggregates_telemetry: true
    makes_routing_decisions: true
    forwards_to: [local_gateway, remote_stargates]
    exposes_endpoints:
      - /v1/chat/completions
      - /v1/models
      - /ws/stargate  # For local Gateway telemetry
      
  REMOTE:
    receives_client_requests: false  # Only from Master
    connects_to_local_gateway: true
    exposes_telemetry_to_master: true
    exposes_endpoints:
      - /v1/chat/completions  # Proxied from Master
      - /ws/federation  # For Master to pull telemetry
      
  STANDALONE:
    description: Current default, no federation
    receives_client_requests: true
    direct_gateway_connections: true
```

### 5.3 Telemetry Aggregation

```
-- Telemetry sources at Master
telemetry_sources(master) = 
    direct_local_gateways ∪ telemetry_forwarded_from_remotes

-- Expanded definition
direct_local_gateways = {g | local(g) = master}
telemetry_forwarded_from_remotes = {(s, g) | s ∈ remotes ∧ local(g) = s}

-- Remote forwards Gateway telemetry to Master with source metadata
∀ msg from Gateway g where local(g) = s_remote:
  forward(s_remote, master, wrap(msg, source={stargate: s_remote, gateway: g}))
```

### 5.4 Request Routing Paths

```
-- Path determination
route_path(request r, Gateway g) =
  if local(g) = master then
    [master, g]                    -- Direct: Master → local Gateway
  else
    [master, local(g), g]          -- Federated: Master → Remote → Gateway
    
-- Request forwarding
forward_request(master, s_remote, request) =
  HTTP POST http://s_remote:9999/v1/chat/completions
  headers:
    X-Federation-Source: master.id
    X-Federation-Key: shared_secret
  body: request

forward_request(s_remote, g, request) =
  UNIX POST /tmp/gateway.sock/v1/chat/completions
  body: request
```

---

## 6. FederatedGateway Abstraction

> **SCOPE: MVP (Phase 3)**

### 6.1 Class Design

```python
"""
FederatedGateway wraps a Remote Stargate to appear as Gateway to DecisionEngine.

Invariants:
  - Appears identical to DirectGateway from DecisionEngine perspective
  - Telemetry cached from /ws/federation connection
  - Error states propagate as T0 tier classification
  
Error Handling:
  - REMOTE_UNREACHABLE: tier = T0, staleness_penalty applied
  - GATEWAY_UNHEALTHY: tier = T0, forward to next candidate
  - TELEMETRY_STALE: continue with penalty, allow recovery
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any

class ConnectionState(Enum):
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    RECONNECTING = "reconnecting"

@dataclass
class FederatedGatewayState:
    """Cached state from Remote Stargate telemetry."""
    available_vram_mb: int
    available_ram_mb: int
    total_vram_mb: int
    total_ram_mb: int
    loaded_models: frozenset[str]
    busy_models: frozenset[str]
    last_update_timestamp: float
    connection_state: ConnectionState

class FederatedGateway:
    """
    Wraps Remote Stargate to appear as Gateway to DecisionEngine.
    
    Attributes:
        remote_stargate_url: URL of Remote Stargate (http://host:9999)
        remote_stargate_name: Human-readable name
        local_gateway_name: Name of Gateway behind Remote
        state: Cached telemetry state
        staleness_threshold_seconds: Max age before staleness penalty
    """
    
    def __init__(
        self,
        remote_stargate_url: str,
        remote_stargate_name: str,
        local_gateway_name: str,
        staleness_threshold_seconds: float = 30.0,
    ):
        self.remote_stargate_url = remote_stargate_url
        self.remote_stargate_name = remote_stargate_name
        self.local_gateway_name = local_gateway_name
        self.staleness_threshold_seconds = staleness_threshold_seconds
        self._state: FederatedGatewayState | None = None
        self._ws_client: FederationWebSocketClient | None = None
    
    # =========================================================================
    # Properties (Gateway interface compatibility)
    # =========================================================================
    
    @property
    def available_vram_mb(self) -> int:
        """Available VRAM from cached telemetry."""
        if self._state is None:
            return 0  # No telemetry = no resources
        return self._state.available_vram_mb
    
    @property
    def available_ram_mb(self) -> int:
        """Available RAM from cached telemetry."""
        if self._state is None:
            return 0
        return self._state.available_ram_mb
    
    @property
    def loaded_models(self) -> frozenset[str]:
        """Loaded models from cached telemetry."""
        if self._state is None:
            return frozenset()
        return self._state.loaded_models
    
    @property
    def busy_models(self) -> frozenset[str]:
        """Busy models from cached telemetry."""
        if self._state is None:
            return frozenset()
        return self._state.busy_models
    
    @property
    def is_connected(self) -> bool:
        """True if WebSocket to Remote is connected."""
        return (
            self._state is not None 
            and self._state.connection_state == ConnectionState.CONNECTED
        )
    
    @property
    def is_stale(self) -> bool:
        """True if telemetry is older than threshold."""
        if self._state is None:
            return True
        age = time.time() - self._state.last_update_timestamp
        return age > self.staleness_threshold_seconds
    
    @property
    def staleness_seconds(self) -> float:
        """Seconds since last telemetry update."""
        if self._state is None:
            return float('inf')
        return time.time() - self._state.last_update_timestamp
    
    # =========================================================================
    # Error Handling
    # =========================================================================
    
    def get_feasibility_override(self) -> str | None:
        """
        Returns tier override for error conditions.
        
        Returns:
            "T0" if Remote unreachable or Gateway unhealthy
            None if no override (normal scoring applies)
        """
        if not self.is_connected:
            return "T0"  # Remote unreachable
        if self._state is None:
            return "T0"  # No telemetry received
        # Note: Staleness applies penalty, not T0 override
        return None
    
    def get_staleness_penalty(self) -> float:
        """
        Returns penalty to apply in scoring based on telemetry age.
        
        Returns:
            0.0 if fresh
            Linear penalty up to MAX_STALENESS_PENALTY based on age
        """
        MAX_STALENESS_PENALTY = 100.0
        if not self.is_stale:
            return 0.0
        age_ratio = min(self.staleness_seconds / self.staleness_threshold_seconds, 2.0)
        return MAX_STALENESS_PENALTY * (age_ratio - 1.0)
    
    # =========================================================================
    # Request Forwarding
    # =========================================================================
    
    async def forward_request(
        self,
        request: dict[str, Any],
        timeout: float = 600.0,
    ) -> dict[str, Any]:
        """
        Forward inference request to Remote Stargate.
        
        Args:
            request: Inference request payload
            timeout: Request timeout in seconds
            
        Returns:
            Response from Gateway (via Remote Stargate)
            
        Raises:
            FederationForwardError: If Remote unreachable or Gateway error
        """
        if not self.is_connected:
            raise FederationForwardError(
                f"Remote Stargate {self.remote_stargate_name} unreachable"
            )
        
        url = f"{self.remote_stargate_url}/v1/chat/completions"
        headers = {
            "X-Federation-Source": "master",
            "Content-Type": "application/json",
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(
                url,
                json=request,
                headers=headers,
                timeout=timeout,
            )
            response.raise_for_status()
            return response.json()
    
    async def forward_streaming_request(
        self,
        request: dict[str, Any],
        timeout: float = 600.0,
    ):
        """
        Forward streaming inference request to Remote Stargate.
        
        Yields:
            SSE chunks from Gateway (via Remote Stargate)
        """
        # Implementation follows existing StreamHandler pattern
        ...
    
    # =========================================================================
    # Telemetry Updates
    # =========================================================================
    
    def update_state(self, telemetry: dict[str, Any]) -> None:
        """Update cached state from telemetry message."""
        self._state = FederatedGatewayState(
            available_vram_mb=telemetry.get("available_vram_mb", 0),
            available_ram_mb=telemetry.get("available_ram_mb", 0),
            total_vram_mb=telemetry.get("total_vram_mb", 0),
            total_ram_mb=telemetry.get("total_ram_mb", 0),
            loaded_models=frozenset(telemetry.get("loaded_models", [])),
            busy_models=frozenset(telemetry.get("busy_models", [])),
            last_update_timestamp=time.time(),
            connection_state=ConnectionState.CONNECTED,
        )
    
    def mark_disconnected(self) -> None:
        """Mark Remote as disconnected."""
        if self._state:
            self._state = FederatedGatewayState(
                available_vram_mb=self._state.available_vram_mb,
                available_ram_mb=self._state.available_ram_mb,
                total_vram_mb=self._state.total_vram_mb,
                total_ram_mb=self._state.total_ram_mb,
                loaded_models=self._state.loaded_models,
                busy_models=self._state.busy_models,
                last_update_timestamp=self._state.last_update_timestamp,
                connection_state=ConnectionState.DISCONNECTED,
            )
```

### 6.2 Integration with DecisionEngine

```python
# Routing decision adaptation
def collect_gateway_candidates(self) -> list[Gateway | FederatedGateway]:
    """Collect all Gateways (local and federated) for routing."""
    candidates = []
    
    # Local Gateways (direct connection)
    for gateway in self.local_gateways:
        candidates.append(gateway)
    
    # Federated Gateways (via Remote Stargates)
    for federated in self.federated_gateways:
        # Check for feasibility override
        override = federated.get_feasibility_override()
        if override == "T0":
            # Still include in candidates for observability,
            # but DecisionEngine will score as infeasible
            pass
        candidates.append(federated)
    
    return candidates
```

---

## 7. Failure Handling

> **SCOPE: MVP**

### 7.1 Failure Scenarios

```yaml
# FAILURE_MATRIX
scenarios:
  - id: REMOTE_STARGATE_UNREACHABLE
    condition: TCP connection to Remote fails
    detection: WebSocket disconnect, HTTP timeout
    impact: All Gateways behind this Remote become T0
    recovery: Reconnect loop with exponential backoff
    staleness: Cached telemetry retained with penalty
    
  - id: REMOTE_STARGATE_REACHABLE_GATEWAY_UNHEALTHY
    condition: Remote reachable but its Gateway is down
    detection: Remote reports GATEWAY_SHUTDOWN message
    impact: Specific Gateway becomes T0
    recovery: Wait for Gateway recovery, Remote will report
    staleness: Gateway state updated via telemetry
    
  - id: TELEMETRY_STALE
    condition: No telemetry update within threshold
    detection: Timestamp comparison
    impact: Staleness penalty in scoring
    recovery: Continue with penalty, auto-recover on fresh telemetry
    staleness: Gradual degradation, not immediate T0
    
  - id: REQUEST_FORWARD_TIMEOUT
    condition: Forwarded request to Remote times out
    detection: HTTP client timeout
    impact: Request fails, client receives 504
    recovery: Retry to different Gateway if available
    staleness: Mark Remote as degraded for future requests
```

### 7.2 Failure Handling FOL

```
-- Remote Stargate failure
∀ s ∈ remotes, failure(s) ⟹
  ∀ g where local(g) = s:
    tier(g, r) = T0
    ∧ retain_cached_telemetry(g)
    ∧ apply_staleness_penalty(g)

-- Partial failure (Remote reachable, Gateway unhealthy)
∀ s ∈ remotes, g ∈ G where local(g) = s:
  reachable(master, s) ∧ ¬healthy(g) ⟹
    tier(g, r) = T0
    ∧ route_to_other_gateways(r)

-- Telemetry staleness
stale(s) ⟺ (now - last_message(s)) > STALENESS_THRESHOLD
stale(s) ⟹
  score_penalty(s, staleness_weight × staleness_seconds)
  ∧ ¬immediately_mark_T0(s)

-- Connection loss handling
ws_disconnected(master, s_remote) ⟹
  start_reconnect_loop(master, s_remote)
  ∧ emit(GATEWAY_STATE_CHANGED, {stargate: s_remote, connected: false})
  ∧ ∀ g where local(g) = s_remote: tier(g, r) = T0
```

### 7.3 Recovery Behavior

```yaml
# RECOVERY_CONFIGURATION
recovery:
  reconnect_interval_seconds: 5.0
  max_reconnect_attempts: 0  # 0 = infinite
  staleness_threshold_seconds: 30.0
  max_staleness_penalty: 100.0
  request_timeout_seconds: 600.0
  
# RECONNECT_LOOP
on_disconnect:
  - Mark all Gateways behind Remote as T0
  - Retain cached telemetry (for recovery)
  - Start reconnect loop
  
on_reconnect:
  - Receive fresh INIT message with current state
  - Clear staleness penalties
  - Mark Gateways as T1/T2 based on resources
  - Emit GATEWAY_STATE_CHANGED (connected)
```

---

## 8. Security Model

> **SCOPE: MVP**

### 8.1 Authentication

```yaml
# AUTHENTICATION_CONFIGURATION
federation_auth:
  # Master → Remote authentication
  method: api_key
  header_name: X-Federation-Key
  key_source: config/stargate_config.yaml
  
  # Request provenance tracking
  provenance_header: X-Federation-Source
  
  # Configuration example
  config:
    federation:
      mode: master
      api_key: "sk-federation-secret-key"
      remote_stargates:
        - name: jupiter
          url: http://jupiter:9999
          api_key: "sk-jupiter-key"  # Per-Remote key (optional)
```

### 8.2 Transport Security

```yaml
# TRANSPORT_SECURITY (MVP vs Post-MVP)
mvp:
  transport: plaintext HTTP/WS
  assumption: Trusted LAN
  rationale: Simplicity, low latency
  
post_mvp:
  transport: TLS/WSS
  certificates: Optional CA or self-signed
  rationale: Public network security
  implementation_notes:
    - Add TLS configuration to gateways.yaml
    - Use wss:// for WebSocket connections
    - Certificate pinning for known Remotes
```

### 8.3 Security Invariants

```
-- Federation authentication
∀ connection (master, s_remote):
  requires authenticated(master, s_remote)
  
authenticated(master, s_remote) ⟺
  api_key(master) ∈ allowed_keys(s_remote)

-- Request provenance
∀ request r from master to s_remote:
  header(r, "X-Federation-Source") = master.id
  ∧ header(r, "X-Federation-Key") = api_key(master, s_remote)

-- Gateway isolation maintained
∀ g ∈ G:
  exposed_ports(g) = ∅
  ∧ network_mode(g) = "none"
```

---

## 9. Implementation Phases

> **SCOPE: MVP**

### Phase 1: Remote Stargate Passthrough (Week 2)

```yaml
# PHASE_1_SPECIFICATION
goal: Remote Stargate accepts requests, forwards to local Gateway

config_changes:
  - file: config/stargate_config.yaml
    additions:
      federation:
        mode: remote  # or "master" or "standalone"
        local_gateway:
          socket_path: /tmp/gateway.sock

code_changes:
  - file: systems/federation/__init__.py
    description: New federation module
    
  - file: systems/federation/config.py
    description: Federation configuration dataclass
    
  - file: systems/federation/mode.py
    description: Mode detection and routing bypass
    
  - file: systems/proxy/routers/chat.py
    modification: Check federation mode before routing

behavior:
  if mode == "remote":
    skip_routing_logic()
    forward_to_local_gateway(request)
  else:
    normal_routing(request)

deliverable: Remote Stargates functional as Gateway proxies
```

### Phase 2: Federation WebSocket (Week 3)

```yaml
# PHASE_2_SPECIFICATION
goal: Master connects to Remote's /ws/federation endpoint

new_endpoints:
  - path: /ws/federation
    location: Remote Stargate
    purpose: Telemetry forwarding to Master
    
code_changes:
  - file: systems/federation/remote/handler.py
    description: WebSocket endpoint exposing local Gateway telemetry
    
  - file: systems/federation/master/client.py
    description: FederationWebSocketClient for Master → Remote connection
    
  - file: gateways/manager.py
    modification: Add federated gateway tracking

message_format:
  wrapped_telemetry:
    type: string  # Original message type
    source:
      stargate: string  # Remote Stargate name
      gateway: string   # Local Gateway name
    data: object  # Original message payload

deliverable: Master receives telemetry from all Remotes
```

### Phase 3: FederatedGateway Abstraction (Week 3-4)

```yaml
# PHASE_3_SPECIFICATION
goal: DecisionEngine treats FederatedGateway as regular Gateway

code_changes:
  - file: systems/federation/master/adapter.py
    description: FederatedGateway class (see Section 6)
    
  - file: gateways/types.py
    modification: Add FederatedGateway to Gateway union type
    
  - file: systems/routing/selection/decision/engine.py
    modification: Handle FederatedGateway in candidate collection
    
  - file: systems/routing/selection/decision/feasibility.py
    modification: Check for feasibility override

integration_points:
  - DecisionEngine.select() accepts FederatedGateway
  - Scoring applies staleness_penalty from FederatedGateway
  - Feasibility check respects get_feasibility_override()

deliverable: Unified routing across local and federated Gateways
```

### Phase 4: Request Forwarding Pipeline (Week 4)

```yaml
# PHASE_4_SPECIFICATION
goal: Full request lifecycle through federation

code_changes:
  - file: systems/federation/master/forwarder.py
    description: HTTP forwarding to Remote Stargates
    
  - file: systems/federation/remote/passthrough.py
    description: Request passthrough to local Gateway
    
  - file: systems/proxy/core/streaming/federation_handler.py
    description: SSE passthrough for streaming requests

request_flow:
  1. Client → Master Stargate (/v1/chat/completions)
  2. Master DecisionEngine selects FederatedGateway
  3. Master forwards to Remote Stargate (HTTP)
  4. Remote forwards to local Gateway (Unix socket)
  5. Response flows back through same path

streaming:
  - SSE passthrough works transparently
  - Master proxies SSE events from Remote
  - No modification to streaming content

error_propagation:
  - Gateway errors (4xx/5xx) → Remote → Master → Client
  - Timeouts → Master retries or returns 504
  - Remote unreachable → Master returns 503

deliverable: End-to-end inference through federation
```

---

## 10. Diagrams

> **SCOPE: MVP**

### 10.1 Layer 1: Node Topology

```
# NODE_TOPOLOGY
# Purpose: Show node types and their relationships
# Symbols: [NODE_TYPE:name:mode]

[CLIENT]
    │
    │ HTTP :9999
    ▼
[STARGATE:master:MASTER]
    │
    ├──────────────────────────────────────────┐
    │ TCP :9999                 TCP :9999       │ UNIX /tmp/gw.sock
    ▼                           ▼               ▼
[STARGATE:jupiter:REMOTE]  [STARGATE:saturn:REMOTE]  [GATEWAY:local]
    │                           │                      │
    │ UNIX socket               │ UNIX socket          │ IPC
    ▼                           ▼                      ▼
[GATEWAY:jupiter-gw]       [GATEWAY:saturn-gw]     [WORKER:local-w]
    │                           │
    │ IPC                       │ IPC
    ▼                           ▼
[WORKER:jupiter-w]         [WORKER:saturn-w]
```

### 10.2 Layer 2: Communication Flow

```
# COMMUNICATION_FLOW
# Purpose: Show message/request direction
# Arrows: ──► HTTP request, ◄── HTTP response, ═══► WebSocket

CLIENT
  │
  │ ──► POST /v1/chat/completions
  ▼
MASTER_STARGATE ═══════════════════════════════════════════════════►
  │               (pulls telemetry from all Remotes via WebSocket)
  │
  ├──► HTTP POST /v1/chat/completions ──► REMOTE_STARGATE_JUPITER
  │                                           │
  │                                           │──► UNIX forward ──► GATEWAY_JUPITER
  │                                           │                        │
  │                                           │◄── response ──────────◄┘
  │◄── response ─────────────────────────────◄┘
  │
CLIENT ◄── response
```

### 10.3 Layer 3: Telemetry Flow

```
# TELEMETRY_FLOW
# Purpose: Show telemetry aggregation
# Arrows: ═══► WebSocket message direction

GATEWAY_JUPITER ═══► STARGATE_JUPITER ═══► MASTER_STARGATE
                     (wraps with source)   (aggregates)

GATEWAY_SATURN ═══► STARGATE_SATURN ═══► MASTER_STARGATE
                    (wraps with source)   (aggregates)

GATEWAY_LOCAL ═══► MASTER_STARGATE
                   (direct, no wrap)

# Message transformation at Remote:
GATEWAY → Remote:
  { type: "resource_update", data: { available_vram_mb: 32000 } }

Remote → Master (wrapped):
  { type: "resource_update", 
    source: { stargate: "jupiter", gateway: "jupiter-gw" },
    data: { available_vram_mb: 32000 } }
```

### 10.4 Failure State Diagram

```
# FAILURE_STATES
# Purpose: Show state transitions on failure

                  CONNECTED
                     │
    ┌────────────────┼────────────────┐
    │                │                │
    ▼                ▼                ▼
WS_DISCONNECT   HTTP_TIMEOUT    GATEWAY_UNHEALTHY
    │                │                │
    │                │                │
    ▼                ▼                ▼
┌─────────────────────────────────────────────────┐
│              ALL_GATEWAYS_T0                    │
│  (behind this Remote marked infeasible)         │
└─────────────────────────────────────────────────┘
    │
    │ reconnect_loop
    ▼
RECONNECTING
    │
    │ success
    ▼
CONNECTED (receive INIT, clear T0)
```

---

## 11. Consultation Response

> **SCOPE: MVP Validation**

### 11.1 Architecture Validity ✅

The federation model correctly addresses the network isolation requirement:

- Local Gateways remain fully isolated (`network_mode: none`)
- Master Stargate communicates only with Remote Stargates over TCP
- Remote Stargates act as adapters to their local Gateways via Unix sockets
- `FederatedGateway` abstraction ensures DecisionEngine sees uniform interface
- Architecture is modular for post-MVP multi-Master expansion

### 11.2 Protocol Design Decisions

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Telemetry Direction | Master Pulls | Matches existing pattern, Master controls lifecycle |
| Authentication | API Keys | Minimal auth for trusted LAN, extensible to TLS |
| Failure Handling | Graceful Degradation | Staleness penalty, not immediate T0 |
| Load Coordination | Remote forwards only | Keeps Master simple, local autonomy |

### 11.3 Approved for Phase 1

**Proceed with Remote Stargate Passthrough Mode:**
- Implement `federation.mode` config option
- Remote Stargate forwards all requests to local Gateway
- Add `/ws/federation` endpoint stub

---

## 12. Post-MVP: Hierarchical Federation

> **SCOPE: POST-MVP (Weeks 5+)**

### 12.1 Multi-Master Topology

```
# HIERARCHICAL_TOPOLOGY
# Purpose: Show delegation to Sub-Masters

[CLIENT]
    │
    ▼
[STARGATE:master-a:MASTER]
    │
    ├──────────────────────────────────────────┐
    ▼                                          ▼
[STARGATE:sub-master-b:MASTER]         [STARGATE:sub-master-c:MASTER]
    │                                          │
    ├─────────────┬─────────────┐              ├─────────────┐
    ▼             ▼             ▼              ▼             ▼
[REMOTE:r1]  [REMOTE:r2]  [REMOTE:r3]    [REMOTE:r4]  [REMOTE:r5]
    │             │             │              │             │
    ▼             ▼             ▼              ▼             ▼
[GATEWAY]    [GATEWAY]    [GATEWAY]      [GATEWAY]    [GATEWAY]
```

### 12.2 Invariants Preserved

```
-- DecisionEngine sees uniform interface at every level
∀ level in hierarchy:
  candidates(level) = local_gateways(level) ∪ federated_gateways(level)
  ∧ ∀ fg ∈ federated_gateways(level): interface(fg) = interface(Gateway)

-- Telemetry aggregation cascades
telemetry_flow(master) = 
  telemetry(sub_masters) 
  where telemetry(sub_master) = telemetry(remotes(sub_master))

-- No Gateway ever exposed on network
∀ g ∈ G, ∀ hierarchy_level: exposed_ports(g) = ∅
```

---

## 13. Post-MVP: Tokenized Network

> **SCOPE: POST-MVP (Weeks 5-12)**
> **Reference:** [VISION.md](../VISION.md)

### 13.1 Cryptographic Node Identity (Phase 3)

```
∀ node n ∈ (S ∪ G):
  keypair(n) = (pk_n, sk_n)
  ∧ registered(pk_n, blockchain) = true
  ∧ ∀ msg from n: signed(msg, sk_n) ⟹ verifiable(msg, pk_n)
```

### 13.2 Signed Telemetry and Results (Phase 3)

```
∀ telemetry t from node n:
  valid(t) ⟺ verify_signature(t, pk_n)
  ¬valid(t) ⟹ score_penalty(n, INVALID_SIGNATURE)
```

### 13.3 Token Accounting (Phase 4-5)

```
∀ inference i completed by node n:
  proof(i) = sign(hash(request, response, timestamp), sk_n)
  ∧ submit(proof(i), escrow_contract)
  ∧ (client_approved(i) ∨ timeout(i)) ⟹ release_tokens(n)
```

### 13.4 Roadmap Alignment

| VISION Phase | Federation Milestone |
|--------------|---------------------|
| Phase 0 (Week 0-1) | MVP cluster with single Master confirmed |
| Phase 1 (Week 2) | Remote Stargate passthrough mode |
| Phase 2 (Week 3-4) | Federation telemetry, FederatedGateway abstraction |
| Phase 3 (Week 5-6) | Node identity, signed proofs |
| Phase 4 (Week 7-8) | Base blockchain integration, token testnet |
| Phase 5 (Week 9-10) | Escrowed work, dispute mechanism |
| Phase 6 (Week 11) | Public node onboarding MVP |
| Phase 7 (Week 12) | Token launch, public network |

---

## 14. Appendix

### 14.1 Codebase Structure

```
services/universal-stargate/
├── systems/
│   ├── federation/                 # NEW: Federation support
│   │   ├── __init__.py
│   │   ├── config.py              # FederationConfig dataclass
│   │   ├── mode.py                # Mode detection, routing bypass
│   │   ├── remote/                # Remote Stargate mode
│   │   │   ├── handler.py         # /ws/federation endpoint
│   │   │   └── passthrough.py     # Request passthrough to Gateway
│   │   └── master/                # Master Stargate mode
│   │       ├── client.py          # FederationWebSocketClient
│   │       ├── adapter.py         # FederatedGateway class
│   │       └── forwarder.py       # HTTP forwarding to Remotes
│   ├── proxy/                     # HTTP layer
│   │   ├── core/
│   │   │   ├── nonstreaming/     # Request forwarding
│   │   │   ├── streaming/        # SSE handling
│   │   │   └── control_plane/    # Model lifecycle
│   │   └── routers/              # API endpoints
│   └── routing/                   # Gateway selection
│       ├── selection/
│       │   └── decision/
│       │       ├── engine.py     # DecisionEngine
│       │       ├── feasibility.py # T0/T1/T2 tiers
│       │       └── scorer.py     # Utility scoring
│       └── queue/                # Request queuing
├── gateways/
│   ├── manager.py                # MultiGatewayManager
│   ├── routing.py                # GatewayRouter
│   ├── types.py                  # GatewayInstance, FederatedGateway
│   └── federation.py             # NEW: Federated gateway management
├── gateway_websocket/
│   ├── ws_client/                # WebSocket client
│   │   ├── connection.py         # Connection lifecycle
│   │   └── orchestrator.py       # Message handling
│   └── handler/                  # Message handlers
└── config/
    ├── stargate_config.yaml      # Main config (add federation section)
    └── gateways.yaml             # Gateway definitions
```

### 14.2 Configuration Schema

```yaml
# stargate_config.yaml additions
federation:
  # Mode: master | remote | standalone (default)
  mode: master
  
  # Master-specific configuration
  remote_stargates:
    - name: jupiter
      url: http://jupiter:9999
      api_key: sk-jupiter-key
      enabled: true
      
    - name: saturn
      url: http://saturn:9999
      api_key: sk-saturn-key
      enabled: true
  
  # Remote-specific configuration
  local_gateway:
    socket_path: /tmp/gateway.sock
    
  # Shared configuration
  staleness_threshold_seconds: 30.0
  reconnect_interval_seconds: 5.0
```

---

*Document Updated: 2026-01-06*  
*Status: Approved for Phase 1 Implementation*  
*Optimized for AI Agent Comprehension*
