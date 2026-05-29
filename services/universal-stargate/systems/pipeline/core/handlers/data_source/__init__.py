"""Built-in data_source_v1: load pipeline inputs from SQLite, RAG, or models.

This module provides the `DataSourceV1Handler` for loading structured data
into the pipeline. It supports three primary source types:

- `sqlite_query`: Executes a read-only SQL query against an allowed SQLite database.
  Requires `db_path` and `sql` in the step configuration.
- `rag_corpus_hints`: Retrieves RAG corpus hints based on configured scopes.
  Integrates with RAG configuration and property index for freshness checks.
- `available_models`: GET /v1/models?type=model; optional ``source`` from
  ``pipeline_options.mode``; ``model_pool`` override skips discovery.

This package is the package-shadow of the former ``data_source.py`` module
(unit 15 of the pipeline modularize overhaul). ``DataSourceV1Handler`` is the
sole public surface; importing this package triggers ``@register_handler``
registration as a side effect of importing ``handler``.

Internal layout (all submodules are package-private):

- ``handler`` — DataSourceV1Handler class (constants + validate + thin execute)
- ``allowlist`` — SQLite db_path allowlist + resolution
- ``freshness`` — tier-aware skip-fresh decision for RAG scopes
- ``bindings`` — handler_inputs binding resolution (lazy traverse_path)
- ``sqlite_source`` — ``sqlite_query`` runner
- ``rag_source`` — ``rag_corpus_hints`` runner
- ``models_source`` — ``available_models`` runner
"""

from .handler import DataSourceV1Handler

__all__ = ["DataSourceV1Handler"]
