# Quality gates — deferred reference

Load when code changes, bugfix completion, deploy claims, error-shape changes, or Docker rebuild decisions.

## SLOC and compile gates

Existing files ≤400 SLOC; new files ≤300 SLOC; exceeded ⇒ split before commit.  
∀ touched file: ¬(pre ≤400 ∧ post > 400). Yellow→red modularize scan ⇒ extract before recommit.

```bash
python -m compileall -q {files}
ruff check && ruff format --check
ruff check --select=UP --fix
scripts/modularize scan {files}
```

∀ function: typed params + return. Remove deprecated/unused imports (F401); ¬comment-out dead code.

## Bug-class sweep (`[quality:bug-class-sweep]`)

∀ bugfix B on pattern P in file F: declare_complete(B) ⇒ swept(scope(service(F) ∪ lib(F)), P).  
Bad class: single-file wrong dict-key fix with identical pattern surviving in sibling modules (silent zero-result / dead fallback).

## Exception and default telemetry

∀ caught exception: emit event (WARN+) ∨ re-raise; `except CancelledError` MUST re-raise.  
Bare logger.warning/error outside event-transport carve-out ⇒ review finding.  
∀ default: user-configured ∨ emit ERROR-level event; agents ¬invent defaults.

## Error envelope (`[quality:error-envelope]`)

Shape `{code, message, source, retryable, data}` via `ProtocolError` from `universal_protocol`.  
Forbidden: nested `{"error": {...}}`, unstructured capacity 503s.

## Docker cache (`[docker]`)

`--no-cache` = disable layer reuse + `--pull`; ¬global `docker builder prune -af`.  
Prefer scoped `docker buildx prune --builder <workspace-service-builder>`.
