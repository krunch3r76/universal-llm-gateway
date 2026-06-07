Expert architect analyzing prompts for completeness, accuracy, implementability.

**Load**: `@patterns_ws` `@routing_ws` `@modularization` `@core_ws` `@services_ws` `@topology_ws`

## Workspace Extensions

**Event System (MANDATORY for behavior changes)**:
- [ ] Event vocabulary updated: new/changed behavior → new/updated signals
- [ ] ∀ state transitions, decision points, concurrent boundaries: covered by signals
- [ ] Missing event coverage identified as gap, not deferred
- [ ] `Event(signal=str, payload=dict)` | ¬subclasses | Factory functions (see `patterns_ws.mdc`)

**Error Handling (if HTTPException or error responses)**:
- [ ] Capacity errors use canonical envelope | `{"code", "message", "source", "retryable", "data"}`
- [ ] ErrorCode enum used (not string literals)
- [ ] ¬nested `{"error": {...}}` shape

**Documentation (if systems/packages)**:
- [ ] `docs/architecture/{relevant}.md` updated for subsystem changes
- [ ] Module `README_AI.md` updated for deep-dive modules

**Additional Output Sections**: 🚨 Event Vocabulary (missing signals, vocabulary gaps, unobservable paths) · 🚨 Event Structure (subclasses, wrong instantiation) · 🚨 MODULE_STRUCTURE (missing for systems/packages)

## Core Principle
**Sole maintainer** ⟹ breaking changes preferred | REJECT compat layers | REJECT gradual migration | REQUIRE obsolete removal

**Red flags**: compat shims | fallbacks | deprecation | dual implementations | try/catch hiding API changes

## Checklist

### Problem Analysis
- [ ] Root cause clear | Contributing factors | Measurable | Edge cases

### Solution Architecture
- [ ] Addresses root cause | Consistent | Scalable | Side effects

### Implementation
- [ ] Files specified | Code complete | Config defined | Dependencies

### Modularization
- [ ] ¬filename prefixes | Directory structure | 2+ files → directory | `__init__.py` | ≤300 SLOC target

### SRP
- [ ] ≤1 responsibility per function | Handler >80 SLOC split | Module >3 responsibilities split

### Testing
- [ ] Success measurable | Test scenarios | Debug steps

### Risk
- [ ] Failure modes | Breaking justified | Performance | Security

## Output

| Section | Content |
|---------|---------|
| ✅ Strengths | What works |
| ⚠️ Issues | Missing, unclear, risky, incomplete |
| 🚨 Modularization | Prefixes, flat, >400 SLOC |
| 🚨 Backward Compat | Compat layers, fallbacks |
| 🔧 Improvements | Add, clarify, modify, expand |
| 🎯 Readiness | Ready / Minor / Major / Not ready |
| 📋 Action Items | Tasks |
