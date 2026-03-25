"""Built-in data_source_v1: load pipeline inputs from SQLite, RAG, or models.

This module provides the `DataSourceV1Handler` for loading structured data
into the pipeline. It supports three primary source types:

- `sqlite_query`: Executes a read-only SQL query against an allowed SQLite database.
  Requires `db_path` and `sql` in the step configuration.
- `rag_corpus_hints`: Retrieves RAG corpus hints based on configured scopes.
  Integrates with RAG configuration and property index for freshness checks.
- `available_models`: GET /v1/models?type=model; optional ``source`` from
  ``pipeline_options.mode``; ``model_pool`` override skips discovery.
"""
# ruff: noqa: E501

from __future__ import annotations

import contextlib
import json
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

import httpx
from model_id import ModelId
from universal_logging import get_logger

from ..execution.resolver import NamespaceResolver
from .protocol import PipelineContext, StepOutput
from .registry import register_handler

if TYPE_CHECKING:
    from ..schemas import StepConfig

logger = get_logger(__name__)

_DEFAULT_METADATA_DB = Path.home() / ".rag" / "store" / "rag_metadata.db"
_DEFAULT_CORTEX_DB = Path.home() / ".cortex" / "cortex.db"
_DEFAULT_TODOS_DB = Path.home() / ".cortex" / "todos.db"


def _expand_allowed_db(path_str: str) -> Path | None:
    """Expands a path string and resolves it, returning the path if it's in the allowlist.

    Args:
        path_str: The path string to expand and resolve.

    Returns:
        A `Path` object if the resolved path is in the allowed list, otherwise `None`.
    """
    raw = path_str.strip()
    if raw.startswith("~/"):
        p = Path.home() / raw[2:]
    else:
        p = Path(raw).expanduser()
    try:
        resolved = p.resolve()
    except OSError:
        return None
    allowed = {
        _DEFAULT_METADATA_DB.resolve(),
        _DEFAULT_CORTEX_DB.resolve(),
        _DEFAULT_TODOS_DB.resolve(),
    }
    if resolved not in allowed:
        logger.error("data_source_v1: rejected db path outside allowlist: %s", resolved)
        return None
    return resolved


def _should_skip_fresh_scope(
    *,
    skip_fresh: bool,
    mode: str,
    current_hash: str,
    stored: tuple[str, str, str] | None,
) -> bool:
    """Tier-aware skip when corpus hash is unchanged (see vocabulary pipeline plan)."""
    if not skip_fresh:
        return False
    if stored is None:
        return False
    files_hash, _, tier = stored
    if files_hash != current_hash:
        return False
    tier_norm = (tier or "local").strip().lower()
    if tier_norm not in ("local", "frontier"):
        tier_norm = "local"
    mode_norm = (mode or "local").strip().lower()
    if mode_norm == "local":
        return True
    if mode_norm == "frontier":
        return tier_norm == "frontier"
    return True


@register_handler
class DataSourceV1Handler:
    """Load structured data for downstream steps (SQLite or RAG corpus_hints)."""

    step_type = "data_source_v1"

    dependency_fields: ClassVar[tuple[str, ...]] = ()

    _VALID_SOURCE_TYPES = frozenset(
        {"sqlite_query", "rag_corpus_hints", "available_models"}
    )

    def validate(self, step: StepConfig) -> list[str]:
        st = step.get_domain_field("source_type", "")
        if not st:
            return [f"Step '{step.id}': data_source_v1 requires source_type"]
        if st not in self._VALID_SOURCE_TYPES:
            return [
                f"Step '{step.id}': unknown source_type {st!r} "
                f"(expected {' | '.join(sorted(self._VALID_SOURCE_TYPES))})"
            ]
        return []

    async def execute(self, step: StepConfig, context: PipelineContext) -> StepOutput:
        source_type = self._require_source_type(step)
        if source_type == "sqlite_query":
            payload = await self._run_sqlite(step, context)
        elif source_type == "available_models":
            payload = await self._run_available_models(step, context)
        else:
            payload = await self._run_rag_corpus_hints(step, context)
        raw = json.dumps(payload, ensure_ascii=False, indent=2)
        return StepOutput(raw=raw, json=payload)

    def _require_source_type(self, step: StepConfig) -> str:
        """Extracts and validates the 'source_type' from the step configuration.

        Args:
            step: The `StepConfig` object.

        Returns:
            The validated source type string.

        Raises:
            ValueError: If 'source_type' is missing or empty.
        """
        st = step.get_domain_field("source_type", "")
        if not st:
            raise ValueError(f"Step '{step.id}': data_source_v1 requires source_type")
        return st

    async def _run_sqlite(
        self, step: StepConfig, context: PipelineContext
    ) -> dict[str, Any]:
        """Executes a read-only SQLite query and returns the results.

        Args:
            step: The `StepConfig` containing 'db_path', 'sql', and optional 'params'.
            context: The `PipelineContext` for resolving bindings.

        Returns:
            A dictionary containing 'columns', 'rows', 'truncated', and 'row_count'.

        Raises:
            ValueError: If `db_path` or `sql` are missing, `sql` is not read-only,
                        `db_path` is disallowed, or parameter binding fails.
            sqlite3.Error: If a SQLite database error occurs during execution.
        """
        db_path_s = step.get_domain_field("db_path", "")
        sql = step.get_domain_field("sql", "")
        if not db_path_s or not sql:
            raise ValueError(
                f"Step '{step.id}': sqlite_query requires db_path and sql "
                "in step config"
            )
        sql_u = sql.strip().upper()
        if not sql_u.startswith("SELECT") and not sql_u.startswith("WITH"):
            raise ValueError(
                f"Step '{step.id}': sqlite_query allows read-only SELECT/WITH only"
            )
        resolved = _expand_allowed_db(db_path_s)
        if resolved is None:
            raise ValueError(f"Step '{step.id}': disallowed or invalid db_path")
        params: tuple[Any, ...] = ()
        resolver = NamespaceResolver(context)
        if step.handler_inputs.get("params"):
            params = self._resolve_binding(resolver, step, "params")
            if not isinstance(params, list | tuple):
                params = (params,)
            params = tuple(params)

        max_rows = int(step.get_domain_field("max_rows", 5000) or 5000)
        max_rows = min(max(max_rows, 1), 50_000)

        try:
            with contextlib.closing(
                sqlite3.connect(f"file:{resolved}?mode=ro", uri=True)
            ) as conn:
                conn.row_factory = sqlite3.Row
                cur = conn.execute(sql, params)
                rows = cur.fetchmany(max_rows + 1)
        except sqlite3.Error as e:
            logger.error("[%s] sqlite_query failed: %s", step.id, e, exc_info=True)
            raise ValueError(f"SQLite query failed for step '{step.id}': {e}") from e

        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        columns = list(rows[0].keys()) if rows else []
        out_rows = [dict(r) for r in rows]
        return {
            "columns": columns,
            "rows": out_rows,
            "truncated": truncated,
            "row_count": len(out_rows),
        }

    def _resolve_binding(
        self, resolver: NamespaceResolver, step: StepConfig, name: str
    ) -> Any:
        """Resolves a binding from `step.handler_inputs` using the provided resolver.

        Args:
            resolver: The `NamespaceResolver` to resolve the binding.
            step: The `StepConfig` containing the handler inputs.
            name: The name of the binding field to resolve (e.g., 'params').

        Returns:
            The resolved value of the binding.

        Raises:
            ValueError: If the binding is missing or resolution fails.
        """
        from ..execution.resolver import traverse_path

        binding = step.handler_inputs.get(name)
        if binding is None:
            raise ValueError(f"Step '{step.id}' missing handler_inputs.{name}")
        root = resolver.resolve(binding)
        return traverse_path(
            root,
            binding.field_path,
            step_name=step.id,
            field_name=name,
            binding_repr=str(binding),
            resolver=resolver,
        )

    async def _run_rag_corpus_hints(
        self, step: StepConfig, context: PipelineContext
    ) -> dict[str, Any]:
        """Retrieves RAG corpus hints, optionally filtering by scope and freshness.

        Args:
            step: The `StepConfig` object (not directly used for inputs, but for logging).
            context: The `PipelineContext` containing options like 'mode', 'skip_fresh', and 'scopes'.

        Returns:
            A dictionary containing 'scopes' (list of dictionaries with scope details),
            'mode', and 'skip_fresh' status. Includes an 'error' key if RAG config fails.
        """
        from services.rag.config import load_config
        from services.rag.corpus_hints import (
            compute_scope_files_hash,
            load_corpus_hints,
        )
        from services.rag.property_index import PropertyIndex

        opts = context.options
        mode = str(opts.get("mode") or "local").strip().lower()
        skip_fresh = opts.get("skip_fresh", True)
        if isinstance(skip_fresh, str):
            skip_fresh = skip_fresh.strip().lower() in ("1", "true", "yes")
        skip_fresh = bool(skip_fresh)

        filter_scopes = opts.get("scopes")
        filter_set: set[str] | None = None
        if filter_scopes is not None and isinstance(filter_scopes, list | tuple | set):
            filter_set = {str(s) for s in filter_scopes if s is not None and str(s)}

        hints_map = load_corpus_hints()
        try:
            config = load_config()
        except Exception as e:  # Catch a broader exception if other errors are possible
            logger.error(
                "[%s] rag_corpus_hints: load_config failed: %s",
                step.id,
                e,
                exc_info=True,
            )
            return {
                "scopes": [],
                "mode": mode,
                "skip_fresh": skip_fresh,
                "error": "rag_config_unavailable",
            }
        cs_map = {n: list(sdef.prefixes) for n, sdef in config.scopes.items()}
        descriptions = {
            n: getattr(sdef, "description", "") or ""
            for n, sdef in config.scopes.items()
        }

        idx = PropertyIndex()
        await idx.start()
        try:
            scopes_out: list[dict[str, Any]] = []
            skipped_empty_hint_scopes = 0
            for scope_name in sorted(cs_map.keys()):
                if filter_set is not None and scope_name not in filter_set:
                    continue
                text = hints_map.get(scope_name, "")
                terms = [t.strip() for t in text.split(",") if t.strip()]
                current_hash = compute_scope_files_hash(idx, cs_map[scope_name])
                stored = idx.get_scope_freshness(scope_name)
                if _should_skip_fresh_scope(
                    skip_fresh=skip_fresh,
                    mode=mode,
                    current_hash=current_hash,
                    stored=stored,
                ):
                    continue
                if not terms:
                    skipped_empty_hint_scopes += 1
                    continue
                scopes_out.append(
                    {
                        "scope": scope_name,
                        "description": descriptions.get(scope_name, ""),
                        "terms": terms,
                        "has_hints": bool(terms),
                        "files_hash": current_hash,
                    }
                )
        finally:
            await idx.stop()

        if skipped_empty_hint_scopes:
            logger.info(
                "[%s] rag_corpus_hints: skipped %d scope(s) with no corpus hints",
                step.id,
                skipped_empty_hint_scopes,
            )

        return {
            "scopes": scopes_out,
            "mode": mode,
            "skip_fresh": skip_fresh,
        }

    async def _run_available_models(
        self, step: StepConfig, context: PipelineContext
    ) -> dict[str, Any]:
        """GET /v1/models?type=model (& source=all iff mode=frontier).

        Options: model_pool (override), mode local|frontier, frontier_models.

        Returns: {"model_pool": [routing_key, ...], "mode": str}
        """
        opts = context.options
        override = opts.get("model_pool")
        if isinstance(override, list) and override:
            pool = [ModelId.parse(str(x).strip()) for x in override if str(x).strip()]
            if not pool:
                raise ValueError("model_pool override is empty after parsing")
            return {
                "model_pool": [m.routing_key for m in pool],
                "mode": "override",
            }

        mode = str(opts.get("mode") or "local").strip().lower()
        source = "all" if mode == "frontier" else None

        try:
            params: dict[str, str] = {"type": "model"}
            if source:
                params["source"] = source
            async with httpx.AsyncClient(timeout=30.0) as client:
                resp = await client.get(
                    "http://localhost:9999/v1/models",
                    params=params,
                )
                resp.raise_for_status()
                data = resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(
                "[%s] available_models: GET /v1/models failed with status %s",
                step.id,
                e.response.status_code,
                exc_info=True,
            )
            raise ValueError(
                f"Model discovery failed for step '{step.id}': HTTP status error"
            ) from e
        except httpx.RequestError as e:
            logger.error(
                "[%s] available_models: GET /v1/models failed with request error",
                step.id,
                exc_info=True,
            )
            raise ValueError(
                f"Model discovery failed for step '{step.id}': Request error"
            ) from e
        except Exception as e:  # Catch any other unexpected errors
            logger.error(
                "[%s] available_models: GET /v1/models failed with unexpected error",
                step.id,
                exc_info=True,
            )
            raise ValueError(
                f"Model discovery failed for step '{step.id}': Unexpected error"
            ) from e

        models: list[ModelId] = []
        seen_models: set[ModelId] = set()
        for entry in data.get("data", []):
            mid_str = entry.get("id", "")
            if not mid_str.strip():
                continue
            model = ModelId.parse(mid_str.strip())
            if model not in seen_models:
                models.append(model)
                seen_models.add(model)

        pool_ids: list[str] = [m.routing_key for m in models]

        if mode == "frontier":
            extra = opts.get("frontier_models") or []
            if isinstance(extra, list):
                for x in extra:
                    s = str(x).strip()
                    if s and s not in pool_ids:
                        pool_ids.append(s)

        if not pool_ids:
            raise ValueError(
                f"Step '{step.id}': available_models found no models "
                "(load gateway models or pass model_pool / frontier_models)"
            )

        return {"model_pool": pool_ids, "mode": mode}
