"""entity_get dispatch argument surface — unknown keys and include_body semantics."""

from __future__ import annotations

from typing import Any

_ENTITY_GET_ACCEPTED_KEYS: frozenset[str] = frozenset(
    {
        "entity_id",
        "entity_ids",
        "include_edges",
        "edge_limit",
        "include_compaction_pointers",
        "intent",
        "include_superseded",
        "debug",
        "top_k",
        "resolve_aliases",
        "raw_id",
        "section",
        "full_body",
        "include_body",
    }
)


def _dispatch_param_warnings(
    dropped_keys: list[str],
    *,
    op: str,
    accepted: frozenset[str],
) -> list[dict[str, str]]:
    accepted_list = ", ".join(sorted(accepted))
    return [
        {
            "category": "dispatch",
            "field": key,
            "message": (
                f"Key {key!r} is not accepted by {op} and was discarded. "
                f"Accepted keys: {accepted_list}."
            ),
        }
        for key in sorted(dropped_keys)
    ]


def attach_entity_get_param_warnings(
    result: dict[str, Any],
    *,
    dropped_keys: list[str],
    include_body: bool | None,
    entity_type: str | None,
    intent: str,
) -> dict[str, Any]:
    warnings: list[dict[str, str]] = []
    if dropped_keys:
        warnings.extend(
            _dispatch_param_warnings(
                dropped_keys, op="entity_get", accepted=_ENTITY_GET_ACCEPTED_KEYS
            )
        )
    if include_body is not None and entity_type != "runbook" and intent == "card":
        warnings.append(
            {
                "category": "dispatch",
                "field": "include_body",
                "message": (
                    "include_body applies only to intent=card on type=runbook "
                    f"entities; ignored for type={entity_type!r}."
                ),
            }
        )
    if not warnings:
        return result
    existing = result.get("validation_warnings") or []
    result["validation_warnings"] = [*existing, *warnings]
    return result
