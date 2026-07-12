"""Per-statement shape validation for cortex.life/v1 patches."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from .registry import LifeVocabRegistry

_METADATA_KEYS = frozenset(
    {"@context", "@graph", "@version", "@vocab", "@id", "name", "description"}
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TYPED_ENTITY_ATTRS = frozenset({"name", "description", "aliases", "attributes"})


@dataclass(frozen=True)
class ShapeReject:
    statement_idx: int
    code: str
    detail: str


def _iter_statements(patch: dict[str, Any]) -> list[dict[str, Any]]:
    graph = patch.get("@graph")
    if graph is not None:
        if not isinstance(graph, list):
            raise ValueError("@graph must be a list")
        return [s for s in graph if isinstance(s, dict)]
    return [patch]


def _entity_ref_type(ref: str, registry: LifeVocabRegistry) -> str | None:
    if ":" not in ref:
        return None
    prefix = ref.split(":", 1)[0]
    return prefix if prefix in registry.entity_types else None


def _check_domain_range(
    *,
    registry: LifeVocabRegistry,
    pred_name: str,
    spec_domain: str | None,
    spec_range: str | None,
    subject_id: str,
    object_ref: str | None,
    statement_idx: int,
) -> list[ShapeReject]:
    rejects: list[ShapeReject] = []
    if spec_domain:
        subject_type = _entity_ref_type(subject_id, registry)
        if subject_type and subject_type != spec_domain:
            rejects.append(
                ShapeReject(
                    statement_idx,
                    "domain_mismatch",
                    f"{pred_name}: subject {subject_id!r} expected domain {spec_domain!r}",
                )
            )
    if spec_range and object_ref:
        object_type = _entity_ref_type(object_ref, registry)
        if object_type and object_type != spec_range:
            rejects.append(
                ShapeReject(
                    statement_idx,
                    "range_mismatch",
                    f"{pred_name}: object {object_ref!r} expected range {spec_range!r}",
                )
            )
    return rejects


def _check_literal_value(
    *,
    pred_name: str,
    literal_type: str | None,
    enum_values: tuple[str, ...],
    value: object,
    statement_idx: int,
) -> list[ShapeReject]:
    if literal_type == "string":
        if not isinstance(value, str) or not value.strip():
            return [
                ShapeReject(
                    statement_idx,
                    "literal_type",
                    f"{pred_name}: expected non-empty string",
                )
            ]
        return []
    if literal_type == "date":
        if not isinstance(value, str) or not _DATE_RE.match(value):
            return [
                ShapeReject(
                    statement_idx,
                    "literal_type",
                    f"{pred_name}: expected ISO date YYYY-MM-DD",
                )
            ]
        return []
    if literal_type == "enum":
        if not isinstance(value, str) or value not in enum_values:
            return [
                ShapeReject(
                    statement_idx,
                    "literal_type",
                    f"{pred_name}: expected one of {list(enum_values)}",
                )
            ]
        return []
    return [
        ShapeReject(
            statement_idx,
            "literal_type",
            f"{pred_name}: unknown literal_type {literal_type!r}",
        )
    ]


def _object_ref(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("@id"), str):
        return value["@id"]
    return None


def shape_check_patch(
    patch: dict[str, Any],
    registry: LifeVocabRegistry,
) -> list[ShapeReject]:
    """Validate patch statements; return structured rejects (empty if valid)."""
    rejects: list[ShapeReject] = []
    context = patch.get("@context")
    if context != registry.context_id:
        rejects.append(
            ShapeReject(
                0,
                "unknown_predicate",
                f"@context must be {registry.context_id!r}, got {context!r}",
            )
        )
        return rejects

    try:
        statements = _iter_statements(patch)
    except ValueError as exc:
        return [ShapeReject(0, "cardinality", str(exc))]

    if not statements:
        return [ShapeReject(0, "cardinality", "patch has no statements")]

    for idx, stmt in enumerate(statements):
        subject = stmt.get("@id")
        if not subject or not isinstance(subject, str):
            rejects.append(
                ShapeReject(idx, "cardinality", "statement missing @id subject")
            )
            continue

        typing_key = "@type" if "@type" in stmt else ("a" if "a" in stmt else None)
        predicate_keys = [
            k
            for k in stmt
            if k not in _METADATA_KEYS
            and k not in {"@type", "a"}
            and not k.startswith("@")
        ]

        for key in predicate_keys:
            if registry.is_refused(key):
                rejects.append(
                    ShapeReject(
                        idx,
                        "refused_op",
                        f"refused cortex op or predicate: {key}",
                    )
                )
                continue
            spec = registry.predicate_for_key(key)
            if spec is None:
                rejects.append(
                    ShapeReject(
                        idx,
                        "unknown_predicate",
                        f"out-of-vocabulary predicate: {key}",
                    )
                )
                continue
            if spec.klass == "relationship":
                obj_ref = _object_ref(stmt[key])
                if obj_ref is None:
                    rejects.append(
                        ShapeReject(
                            idx,
                            "range_mismatch",
                            f"{key}: object must be entity ref string or {{@id}}",
                        )
                    )
                else:
                    rejects.extend(
                        _check_domain_range(
                            registry=registry,
                            pred_name=key,
                            spec_domain=spec.domain,
                            spec_range=spec.range,
                            subject_id=subject,
                            object_ref=obj_ref,
                            statement_idx=idx,
                        )
                    )
            elif spec.klass == "literal":
                rejects.extend(
                    _check_literal_value(
                        pred_name=key,
                        literal_type=spec.literal_type,
                        enum_values=spec.enum_values,
                        value=stmt[key],
                        statement_idx=idx,
                    )
                )

        if typing_key:
            entity_type = stmt.get(typing_key)
            if not isinstance(entity_type, str) or entity_type not in registry.entity_types:
                rejects.append(
                    ShapeReject(
                        idx,
                        "range_mismatch",
                        f"typing: {entity_type!r} not in allowed entity_types",
                    )
                )
            extra = [k for k in stmt if k in _TYPED_ENTITY_ATTRS]
            if not extra and len(predicate_keys) == 0:
                pass  # type-only create is valid

        rel_or_lit = [k for k in predicate_keys if registry.predicate_for_key(k)]
        if typing_key and rel_or_lit:
            rejects.append(
                ShapeReject(
                    idx,
                    "cardinality",
                    "statement cannot combine @type with relationship/literal predicates",
                )
            )

    return rejects
