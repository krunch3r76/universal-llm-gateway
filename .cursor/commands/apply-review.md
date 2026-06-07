No-BC refactor. Breaking changes allowed.

**Load**: `@async-verification_ws` `@patterns_ws` `@core_ws`

## Workspace Extensions

**Blocking Scan (FIRST)**: Run `@async-verification_ws.mdc#non-blocking` checks before any other work.

**Non-Blocking Verification**:
- `publish_nowait()` for event publish on request path
- ¬latency loops
- ∀ I/O async

## Goals
Remove compat layers | Rename/reshape minimal surface | Explicit errors (¬fallbacks) | SRP (split multi-responsibility) | Extract pure helpers | Isolate I/O | Simplify async | PRUNE obsolete | Sync HTTP surface

## Sequence

1. **SRP SPLIT**: handler >80 SLOC → split | module ≥3 responsibilities → directory split | helpers have I/O docstrings
2. New signatures → update call sites
3. Delete dead paths
4. Inline/extract logic
5. Explicit error/async (timeouts, cancellation)
6. Reference check (imports/call sites/exports)
7. HTTP surface: routes, models, status, headers, OpenAPI
8. Tests ONLY: high-risk (external API, concurrency, serialization, security)
9. Fix imports, docs, lint

## Output

| Section | Content |
|---------|---------|
| Breaking Changes | Removed/changed APIs |
| Removed Dead Code | Functions/classes deleted |
| HTTP Surface | OpenAPI diff |
| Updated Call Sites | Migration |
| Staged Removals | Future cleanup |
| TODOs | Manual decisions |
| External Impact | Risk + mitigation |
