"""Normalize dispatch-shape enricher examples to match inner handler signatures."""

from __future__ import annotations

import types
from collections.abc import Callable
from typing import Any, Union, get_args, get_origin, get_type_hints

RAG_OP_TOOL: dict[str, str] = {
    "search": "rag_search",
    "recon": "rag_recon",
    "list_mapped": "rag_list_mapped",
    "list_scopes": "rag_list_scopes",
    "coverage": "rag_coverage",
    "upsert_article": "rag_upsert_article",
    "delete_source": "rag_delete_source",
    "refresh_hints": "rag_refresh_corpus_hints",
    "orphaned_articles": "rag_orphaned_articles",
    "delete_directory": "rag_delete_directory",
}

RAG_OVERFLOW_HANDLERS: dict[str, Callable[..., Any]] = {}

_BOOL_TRUE = frozenset({"true", "1", "yes"})
_BOOL_FALSE = frozenset({"false", "0", "no"})


def resolve_dispatch_inner_handler(
    tool_name: str, op_name: str
) -> Callable[..., Any] | None:
    if tool_name == "agent_bus_read":
        from tools._agent_bus_read import AGENT_BUS_READ_OPS

        return AGENT_BUS_READ_OPS.get(op_name)
    if tool_name == "agent_bus":
        from tools.agent_bus import AGENT_BUS_OPS

        return AGENT_BUS_OPS.get(op_name)
    if tool_name == "rag":
        overflow = RAG_OP_TOOL.get(op_name)
        if overflow is None:
            return None
        return RAG_OVERFLOW_HANDLERS.get(overflow)
    return None


def normalize_dispatch_example_values(
    handler: Callable[..., Any], values: dict[str, Any]
) -> dict[str, Any]:
    hints = get_type_hints(handler, include_extras=True)
    out = dict(values)
    for key, raw in values.items():
        if key not in hints:
            continue
        out[key] = _coerce_for_hint(raw, hints[key])
    return out


def _union_members(annotation: Any) -> tuple[Any, ...]:
    origin = get_origin(annotation)
    if origin is Union or isinstance(annotation, types.UnionType):
        return get_args(annotation)
    return (annotation,)


def _strip_none(members: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(m for m in members if m is not type(None))


def _coerce_for_hint(value: Any, hint: Any) -> Any:
    members = _strip_none(_union_members(hint))
    if isinstance(value, str):
        for member in members:
            scalar = _scalar_type(member)
            if scalar is bool:
                parsed = _parse_bool_string(value)
                if parsed is not None:
                    return parsed
            if scalar is int:
                try:
                    return int(value)
                except ValueError:
                    continue
            if scalar is float:
                try:
                    return float(value)
                except ValueError:
                    continue
    if isinstance(value, list):
        for member in members:
            elem = _list_element_type(member)
            if elem is not None:
                return [_coerce_list_element(item, elem) for item in value]
    return value


def _scalar_type(annotation: Any) -> type[Any] | None:
    if get_origin(annotation) is not None:
        return None
    if annotation in (int, float, bool):
        return annotation
    return None


def _list_element_type(annotation: Any) -> Any | None:
    origin = get_origin(annotation)
    if origin is list:
        args = get_args(annotation)
        if len(args) == 1:
            return args[0]
    return None


def _coerce_list_element(item: Any, elem_type: Any) -> Any:
    if isinstance(item, str):
        scalar = _scalar_type(elem_type)
        if scalar is bool:
            parsed = _parse_bool_string(item)
            if parsed is not None:
                return parsed
        if scalar is int:
            try:
                return int(item)
            except ValueError:
                return item
        if scalar is float:
            try:
                return float(item)
            except ValueError:
                return item
    if isinstance(item, bool):
        return item
    return item


def _parse_bool_string(raw: str) -> bool | None:
    key = raw.strip().lower()
    if key in _BOOL_TRUE:
        return True
    if key in _BOOL_FALSE:
        return False
    return None