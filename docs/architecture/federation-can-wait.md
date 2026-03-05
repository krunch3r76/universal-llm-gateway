# Stargate Federation: Deferred Capabilities & Risks

**Document Type:** Documented Deferrals  
**Status:** CAN WAIT — Post-Phase-1 Backlog  
**Date:** 2026-01-06  
**Scope:** Items explicitly NOT in Phase 1 with documented assumptions and risks

---

## Purpose

This document catalogs capabilities intentionally deferred from Phase 1. Each item includes:

- **What is deferred** — The capability or protection not implemented
- **Assumption** — What Phase 1 relies on for this to be safe
- **Failure Mode** — What happens if the assumption breaks
- **Why Acceptable** — Justification for short-term deferral
- **Trigger** — Signal or threshold that forces implementation
- **Target Phase** — When this should be addressed

**Critical Rule:** This document does NOT:
- Redefine or weaken Phase 1 invariants
- Introduce new protocol semantics
- Create hidden coupling to Phase 1 logic
- Require refactors of Phase 1 components

---

## 1. Async & State Deferrals

### 1.1 E3 BACKPRESSURE Policy

| Attribute | Value |
|-----------|-------|
| **Finding** | F8 |
| **What** | `RateLimitedEventSource` supports BACKPRESSURE policy, but using it on WS receive path can stall sender |
| **Assumption** | Phase 1 uses DROP_OLDEST exclusively; BACKPRESSURE never used on ingress |
| **Failure Mode** | Slow consumer stalls WebSocket → triggers reconnect storm → amplifies load |
| **Why Acceptable** | DROP_OLDEST is safe default; telemetry is eventually consistent anyway |
| **Trigger** | BACKPRESSURE policy exposed in config AND used by operators |
| **Target Phase** | Phase 2 — add warning/guard or isolate in separate task |

**Risk Level:** 🟡 Medium

---

## 2. Security Deferrals

### 2.1 require_tls:false Override

| Attribute | Value |
|-----------|-------|
| **Finding** | F12 |
| **What** | Per-remote TLS override allows plaintext federation traffic |
| **Assumption** | Only used on trusted, isolated networks (e.g., Golem internal proxy) |
| **Failure Mode** | API keys transmitted in plaintext → credential theft, replay attacks, traffic manipulation |
| **Why Acceptable** | Golem's HTTP proxy doesn't support TLS passthrough; private cluster only |
| **Trigger** | Federation traffic crosses untrusted networks OR multi-tenant deployment |
| **Target Phase** | Phase 3 — require mTLS or signed tokens for non-TLS paths |

**Risk Level:** 🔴 High (if misused)

**Operational Guidance:** 
- Document that `require_tls: false` is ONLY for isolated networks
- Add startup warning when TLS disabled
- Consider connection audit logging

### 2.2 API Key Redaction Verification

| Attribute | Value |
|-----------|-------|
| **Finding** | F13 |
| **What** | API key redaction relies on global redaction list being correctly configured |
| **Assumption** | `X-Federation-Key` and `api_key` fields added to `universal_logging` redaction list |
| **Failure Mode** | Keys leak via logs, traces, or error responses |
| **Why Acceptable** | Redaction list is straightforward; implementation verified during Phase 1 |
| **Trigger** | Any key appearing in logs during security audit |
| **Target Phase** | Phase 1 (implementation) — Phase 2 (automated verification) |

**Risk Level:** 🟡 Medium

**Implementation Note:** Phase 1 MUST add keys to redaction list; Phase 2 adds automated test.

### 2.3 Path Prefix Allow-List Bypass

| Attribute | Value |
|-----------|-------|
| **Finding** | F15 |
| **What** | Path prefix matching may be bypassed with URL encoding (e.g., `%2F`) |
| **Assumption** | Path is normalized before allow-list check by FastAPI/Starlette |
| **Failure Mode** | Encoded traversal patterns bypass Remote mode restrictions |
| **Why Acceptable** | FastAPI normalizes paths by default; attack requires framework bug |
| **Trigger** | Bypass test fails on new framework version OR CVE in path handling |
| **Target Phase** | Phase 2 — add bypass tests to CI |

**Risk Level:** 🟢 Low

**Testing Required:**
```python
def test_path_bypass_attempts():
    """Ensure path normalization prevents bypass."""
    bypass_attempts = [
        "/v1%2Fchat%2Fcompletions",
        "/v1/../api/v1/federation/inference",
        "/api/v1/federation/inference/../../v1/chat/completions",
    ]
    for path in bypass_attempts:
        # All should be rejected or resolved to allowed paths
        pass
```

---

## 3. Scalability Deferrals

### 3.1 Network Cost Penalty

| Attribute | Value |
|-----------|-------|
| **Finding** | F22 |
| **What** | Scoring function does not account for network latency/cost between Stargates |
| **Assumption** | All Remotes on low-latency LAN; RTT ~1ms |
| **Failure Mode** | Routes to high-latency or partitioned Remotes; poor user experience |
| **Why Acceptable** | Phase 1 is private cluster on same network |
| **Trigger** | WAN federation OR Remotes on different availability zones |
| **Target Phase** | Phase 3 — add RTT measurement and penalty to scoring |

**Risk Level:** 🟢 Low (Phase 1 scope)

**Future Implementation:**
```python
# Add to scoring function
network_cost_penalty = rtt_ms * config.scoring.rtt_weight
score -= network_cost_penalty
```

### 3.2 Queue Byte Budgeting

| Attribute | Value |
|-----------|-------|
| **Finding** | F24 |
| **What** | Telemetry queues are bounded by event count, not bytes |
| **Assumption** | Average telemetry event < 10KB; 100 events ≈ 1MB |
| **Failure Mode** | OOM from large payloads (e.g., 100 events × 100KB = 10MB) |
| **Why Acceptable** | MAX_MODELS_PER_GATEWAY=100 caps largest field |
| **Trigger** | Queue memory exceeds 10MB OR OOM observed |
| **Target Phase** | Phase 3 — byte-budgeted buffering |

**Risk Level:** 🟢 Low

**Monitoring:** Add metric `federation_telemetry_queue_bytes{remote_id}`

### 3.3 HTTP Pool Isolation

| Attribute | Value |
|-----------|-------|
| **Finding** | F25 |
| **What** | Inference and control plane share HTTP connection pool |
| **Assumption** | Small cluster; streams don't saturate connections |
| **Failure Mode** | Long-running streams exhaust pool; health checks blocked → false T0 |
| **Why Acceptable** | Phase 1 is small cluster with limited concurrency |
| **Trigger** | Health check latency spikes OR false unreachable detection |
| **Target Phase** | Phase 3 — separate pools for health/control vs inference |

**Risk Level:** 🟡 Medium (at scale)

**Future Implementation:**
```python
self._inference_pool = httpx.AsyncClient(...)
self._control_pool = httpx.AsyncClient(limits=httpx.Limits(
    max_connections=10,
    max_keepalive_connections=5,
))
```

### 3.4 Multi-Master Scaling

| Attribute | Value |
|-----------|-------|
| **What** | Single Master is potential bottleneck and SPOF |
| **Assumption** | Phase 1 cluster is small (2-5 Remotes); single Master sufficient |
| **Failure Mode** | Master failure kills entire cluster; throughput limited by single node |
| **Why Acceptable** | MVP scope; restart policies provide recovery |
| **Trigger** | > 10 Remotes OR high-availability requirement |
| **Target Phase** | Phase 4 — shared state (Redis), load-balanced Masters |

**Risk Level:** 🟡 Medium

**Phase 1 Mitigation:**
- Deploy Master on reliable hardware
- Use systemd restart policies
- Implement graceful degradation (503 with retry hints)

---

## 4. Reliability Deferrals

### 4.1 Health Endpoint Semantics

| Attribute | Value |
|-----------|-------|
| **Finding** | F27 |
| **What** | `/healthz` returns 200 for "degraded" (Master has no gateways) |
| **Assumption** | Load balancer treats 200 as "healthy for inference routing" |
| **Failure Mode** | Degraded Master receives traffic → 503 cascade |
| **Why Acceptable** | Phase 1 has single Master; LB not strictly necessary |
| **Trigger** | Load-balanced Master deployment OR cascading 503s |
| **Target Phase** | Phase 2 — add `/readyz` for inference readiness |

**Risk Level:** 🟡 Medium

**Future Implementation:**
```python
@app.get("/healthz")
def healthz():
    """Liveness: is the process running?"""
    return {"status": "healthy"}

@app.get("/readyz")
def readyz():
    """Readiness: can we handle inference?"""
    if not has_reachable_gateways():
        return Response(status_code=503, content={"status": "not_ready"})
    return {"status": "ready"}
```

### 4.2 Telemetry Trust Verification

| Attribute | Value |
|-----------|-------|
| **Finding** | F30 |
| **What** | Master trusts telemetry from Remotes without verification |
| **Assumption** | Remotes are trusted (private cluster, controlled deployment) |
| **Failure Mode** | Malicious Remote sends false telemetry → traffic steering attack |
| **Why Acceptable** | Phase 1 is private cluster with trusted operators |
| **Trigger** | Public node onboarding OR untrusted operators |
| **Target Phase** | Phase 5 — signed telemetry with blockchain verification |

**Risk Level:** 🟢 Low (Phase 1) / 🔴 High (public network)

**Future Implementation:** See VISION.md Phase 3 — cryptographic node identity

### 4.3 Unreachable False Negatives

| Attribute | Value |
|-----------|-------|
| **Finding** | F32 |
| **What** | Long GC pause could trigger false unreachable detection |
| **Assumption** | 10s unreachable threshold is longer than typical GC pause |
| **Failure Mode** | False T0 marking → capacity loss during transient pause |
| **Why Acceptable** | 10s threshold is generous; ping/pong provides liveness signal |
| **Trigger** | False positives observed OR high-memory workloads |
| **Target Phase** | Phase 3 — two-signal policy (require N consecutive failures) |

**Risk Level:** 🟢 Low

**Future Implementation:**
```python
# Require 2 consecutive failures before T0
consecutive_failures: dict[str, int] = {}

def check_unreachable(remote_id: str) -> bool:
    if is_stale(remote_id):
        consecutive_failures[remote_id] = consecutive_failures.get(remote_id, 0) + 1
        return consecutive_failures[remote_id] >= 2
    consecutive_failures[remote_id] = 0
    return False
```

### 4.4 E2 Cross-Lib Import

| Attribute | Value |
|-----------|-------|
| **Finding** | F33 |
| **What** | `CorrelatedRequestTracker` needs ID generation; importing from `universal_protocol` creates dependency |
| **Assumption** | Transport may import from protocol without circular dependency issues |
| **Failure Mode** | Circular import or tight coupling between libs |
| **Why Acceptable** | ID generator is simple; can inject via constructor if needed |
| **Trigger** | Circular import error OR lib restructuring |
| **Target Phase** | Phase 2 — inject ID generator via constructor |

**Risk Level:** 🟢 Low

**Current Approach:**
```python
# Phase 1: Direct import (if no circular issues)
from universal_protocol import generate_request_id

# Phase 2: Injection pattern (if needed)
class CorrelatedRequestTracker:
    def __init__(self, id_generator: Callable[[], str], ...):
        self._id_generator = id_generator
```

---

## 5. Documentation Deferrals

### 5.1 JSON Schema Validation

| Attribute | Value |
|-----------|-------|
| **What** | Telemetry messages not validated against formal JSON Schema |
| **Assumption** | Code-level dataclasses provide sufficient validation |
| **Failure Mode** | Schema drift between sender and receiver; silent failures |
| **Why Acceptable** | Single-codebase in Phase 1; tight integration testing |
| **Trigger** | Multi-team development OR external federation participants |
| **Target Phase** | Phase 2 — add `universal_protocol/schemas/` with validation |

**Risk Level:** 🟢 Low

**Future Location:** `universal_protocol/schemas/federation_telemetry.schema.json`

---

## 6. Future Phase Mapping

| Item | Current Risk | Trigger Condition | Target Phase |
|------|--------------|-------------------|--------------|
| E3 BACKPRESSURE | 🟡 Medium | BACKPRESSURE policy used on ingress | Phase 2 |
| require_tls:false | 🔴 High | Untrusted network deployment | Phase 3 |
| API Key Redaction Verify | 🟡 Medium | Security audit | Phase 2 |
| Path Prefix Bypass | 🟢 Low | Framework CVE | Phase 2 |
| Network Cost Penalty | 🟢 Low | WAN federation | Phase 3 |
| Queue Byte Budgeting | 🟢 Low | Queue > 10MB | Phase 3 |
| HTTP Pool Isolation | 🟡 Medium | Health check latency spikes | Phase 3 |
| Multi-Master | 🟡 Medium | > 10 Remotes | Phase 4 |
| Health Endpoint Semantics | 🟡 Medium | Load-balanced deployment | Phase 2 |
| Telemetry Trust | 🔴 High | Public node onboarding | Phase 5 |
| Unreachable False Neg | 🟢 Low | False positives observed | Phase 3 |
| E2 Cross-Lib | 🟢 Low | Circular import | Phase 2 |
| JSON Schema Validation | 🟢 Low | Multi-team development | Phase 2 |

---

## 7. Risk Matrix

### By Risk Level

| Level | Count | Items |
|-------|-------|-------|
| 🔴 High | 2 | require_tls:false (misuse), Telemetry Trust (public) |
| 🟡 Medium | 5 | E3 BACKPRESSURE, API Key Redaction, Pool Isolation, Multi-Master, Health Semantics |
| 🟢 Low | 6 | Path Bypass, Network Cost, Queue Bytes, Unreachable False Neg, E2 Cross-Lib, JSON Schema |

### By Phase

| Phase | Items |
|-------|-------|
| Phase 2 | E3 BACKPRESSURE, API Key Redaction Verify, Path Prefix Bypass, Health Semantics, E2 Cross-Lib, JSON Schema |
| Phase 3 | require_tls:false hardening, Network Cost, Queue Bytes, Pool Isolation, Unreachable False Neg |
| Phase 4 | Multi-Master |
| Phase 5 | Telemetry Trust (signed proofs) |

---

## 8. Monitoring for Trigger Conditions

### Metrics to Add

```python
# Telemetry queue size (byte estimation)
federation_telemetry_queue_estimated_bytes{remote_id}

# Health check latency (pool isolation trigger)
federation_health_check_latency_ms{remote_id}

# False unreachable detections
federation_unreachable_false_positive_total{remote_id}

# TLS override usage
federation_tls_disabled_connections_total{remote_id}
```

### Alerts

| Alert | Condition | Action |
|-------|-----------|--------|
| TelemetryQueueHigh | queue_bytes > 5MB | Investigate backpressure |
| HealthCheckSlow | p99 > 500ms | Consider pool isolation |
| TLSDisabledInProduction | tls_disabled > 0 AND env=prod | Security review |
| UnreachableFlapHigh | false_positives > 10/hour | Tune threshold or two-signal policy |

---

## 9. Post-MVP Roadmap Alignment

| VISION Phase | Federation Capability |
|--------------|----------------------|
| Phase 2 (Week 3-4) | JSON Schema, Health semantics, E3 guard |
| Phase 3 (Week 5-6) | Network cost, Pool isolation, TLS hardening |
| Phase 4 (Week 7-8) | Multi-Master, Byte budgeting |
| Phase 5 (Week 9-10) | Signed telemetry, Trusted public nodes |

---

## 10. Implementation Notes

### When Implementing Deferred Items

1. **Do not modify Phase 1 invariants** — Deferred items extend, not replace
2. **Add feature flags** — Allow rollback if issues arise
3. **Update both documents** — Move item from CAN WAIT to MUST SHIP when implemented
4. **Verify no regressions** — Run full Phase 1 test suite

### When Adding New Deferrals

1. Document in this file with full risk assessment
2. Add to risk matrix and phase mapping
3. Define clear trigger condition
4. Add monitoring if applicable

---

*Document: CAN WAIT — Deferred Capabilities*  
*Status: Documented Backlog*  
*Date: 2026-01-06*
