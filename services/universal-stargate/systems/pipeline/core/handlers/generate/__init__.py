"""
Generic generate handler package (shadow of former generate.py).

Re-exports ``GenericGenerateHandler`` as the sole public surface so existing
``from ..generate import GenericGenerateHandler`` (handlers/builtin/__init__.py)
and ``from .handlers.generate import GenericGenerateHandler``
(test_generate_model_selection.py) imports continue to work after the
package-shadow split. Importing this package triggers ``@register_handler``
registration of ``GenericGenerateHandler`` with the DomainRouter via
``registry.py`` as a side effect of importing ``handler``.

Internal layout (all submodules are package-private):

- ``handler`` — GenericGenerateHandler class
- ``model_resolution`` — 5-tier primary model resolution chain
- ``avoid_models`` — avoid_models_from binding resolution
- ``routing_errors`` — ProxyClientError annotation for routing-layer suppression
- ``fence`` — markdown fence stripping for cloud JSON responses
- ``prompt_context`` — template context assembly + user prompt rendering
- ``provenance`` — source-provenance extraction + claim provenance injection
- ``invoke`` — per-invocation execution-config + StepOutput assembly
"""

from .handler import GenericGenerateHandler

__all__ = ["GenericGenerateHandler"]
