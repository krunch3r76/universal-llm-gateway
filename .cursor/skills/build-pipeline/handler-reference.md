# Handler API Reference

Thin agent-facing digest. **Stale when** `systems/pipeline/core/handlers/builtin/base.py`, `core/handlers/protocol.py`, or `user_handlers.py` load contract change.

Live registration/load **SOT** = module docstrings on `user_handlers.py` + `DomainRouter` + exemplar `handlers/__init__.py`. Prefer those over this file when they disagree.

---

## Registration (required shape)

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter

def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(domain, step_type, HandlerClass)
```

| Invariant | Detail |
|---|---|
| Signature | `register_handlers(router)` — DomainRouter injection |
| Register API | `router.register_domain_handler_class(domain, step_type, cls)` |
| `step_type` attr | Must equal the registration key exactly |
| Version suffix | Domain step types use `*_v{N}` (or `*_v{N}_{M}`) to avoid cross-version collision |
| Forbidden | Free-function `register_domain_handler_class` from `handlers/registry.py` — does not exist |

Loader discovers `handlers/__init__.py` under `pipelines/{domain}/{variant}/` (or configured user-handlers roots) and calls `register_handlers(router)`.

Exemplars: `pipelines/assertion_enrichment/v1/handlers/__init__.py`, `pipelines/predicate_extract/v1/handlers/__init__.py`.

---

## Handler class contract

Subclass `BaseHandler` (`systems/pipeline/core/handlers/builtin/base.py`):

| Obligation | Notes |
|---|---|
| `step_type: str` | Class attr = registration key |
| `async def execute(self, step, context) -> StepOutput` | Required |
| Prefer `_render_prompt` / `_call_model` | Model alias resolution + concurrency-safe `ModelCallResult` |

Useful helpers on `BaseHandler`: `_render_prompt`, `_call_model`, `_build_generation_params`, `_resolve_model_pool`, `_resolve_model_alias`.

Do **not** emit pipeline lifecycle events from handlers — `DAGExecutor` owns those. Exception: opaque intra-step loops may write recorder-only events (see `pipeline_ws`).

---

## Options-driven vs chat-bound

| Shape | Inputs | Typical return |
|---|---|---|
| Chat / generate | `handler_inputs` → `sourceNs` / prior steps | `StepOutput` text/json for next step |
| Options-driven | `context.options` (from MCP `options={…}`) | Side-effect + writeback; may skip when idempotent |

Options-driven exemplars read `context.options` and call cortex (`assertion_get` / `assertion_update`) without `handler_inputs` on every step.

---

## Layout

```
pipelines/{domain}/
  models.yaml
  v1/
    {domain}-v1.yaml
    prompts.yaml          # optional for pure side-effect steps
    handlers/
      __init__.py         # register_handlers(router)
      *.py                # Handler classes
```

`pipelines.local/` + README_AI/CONTRACTS/PROVENANCE scaffolding is optional/experimental — thin `pipelines/{domain}/v1/` is the migration default.

---

## Further reading

- QUICKSTART §3 — handler sketch
- `user_handlers.py` — load / variant-scoped routing invariants
- `DomainRouter` — resolution order `(domain, variant, step_type)` → shared → generic
