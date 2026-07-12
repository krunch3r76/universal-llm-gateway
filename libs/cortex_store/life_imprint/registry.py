"""Load and validate cortex.life/v1 vocabulary from life_vocab_v1.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_VALID_CLASSES = frozenset({"typing", "relationship", "literal"})
_REQUIRED_PRED_FIELDS = frozenset({"class", "cortex_op"})


@dataclass(frozen=True)
class PredicateSpec:
    name: str
    klass: str
    cortex_op: str
    aliases: tuple[str, ...] = ()
    domain: str | None = None
    range: str | None = None
    literal_type: str | None = None
    enum_values: tuple[str, ...] = ()
    allowlisted_attribute: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class LifeVocabRegistry:
    version: str
    context_id: str
    entity_types: frozenset[str]
    predicates: dict[str, PredicateSpec]
    refuse_list: frozenset[str]
    alias_to_predicate: dict[str, str]

    def predicate_for_key(self, key: str) -> PredicateSpec | None:
        name = self.alias_to_predicate.get(key)
        if name is None:
            return None
        return self.predicates.get(name)

    def is_refused(self, key: str) -> bool:
        return key in self.refuse_list


def _vocab_path() -> Path:
    for env in ("ULG_WORKSPACE_ROOT", "WORKSPACE_ROOT"):
        raw = os.environ.get(env)
        if raw:
            candidate = Path(raw).expanduser() / "config" / "cortex" / "life_vocab_v1.yaml"
            if candidate.is_file():
                return candidate.resolve()
    return Path(__file__).resolve().parents[3] / "config" / "cortex" / "life_vocab_v1.yaml"


def _require_mapping(data: object, label: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping")
    return data


def _parse_predicate(name: str, raw: object) -> PredicateSpec:
    spec = _require_mapping(raw, f"predicates.{name}")
    klass = spec.get("class")
    cortex_op = spec.get("cortex_op")
    if klass not in _VALID_CLASSES:
        raise ValueError(f"predicates.{name}.class invalid: {klass!r}")
    if not cortex_op or not isinstance(cortex_op, str):
        raise ValueError(f"predicates.{name}.cortex_op required")
    aliases = tuple(spec.get("aliases") or ())
    enum_raw = spec.get("enum_values") or []
    if enum_raw and not isinstance(enum_raw, list):
        raise ValueError(f"predicates.{name}.enum_values must be a list")
    return PredicateSpec(
        name=name,
        klass=str(klass),
        cortex_op=str(cortex_op),
        aliases=tuple(str(a) for a in aliases),
        domain=spec.get("domain"),
        range=spec.get("range"),
        literal_type=spec.get("literal_type"),
        enum_values=tuple(str(v) for v in enum_raw),
        allowlisted_attribute=spec.get("allowlisted_attribute"),
        description=spec.get("description"),
    )


def load_registry(path: Path | None = None) -> LifeVocabRegistry:
    """Load vocabulary; fail closed on malformed or duplicate entries."""
    vocab_path = path or _vocab_path()
    if not vocab_path.is_file():
        raise FileNotFoundError(f"life vocabulary not found: {vocab_path}")

    raw = yaml.safe_load(vocab_path.read_text(encoding="utf-8"))
    data = _require_mapping(raw, "life_vocab_v1")

    version = data.get("version")
    context_id = data.get("context_id")
    if not version or not context_id:
        raise ValueError("version and context_id are required")

    entity_types_raw = data.get("entity_types")
    if not isinstance(entity_types_raw, list) or not entity_types_raw:
        raise ValueError("entity_types must be a non-empty list")
    entity_types = frozenset(str(t) for t in entity_types_raw)

    predicates_raw = _require_mapping(data.get("predicates"), "predicates")
    predicates: dict[str, PredicateSpec] = {}
    alias_to_predicate: dict[str, str] = {}
    for name, pred_raw in predicates_raw.items():
        if name in predicates:
            raise ValueError(f"duplicate predicate: {name}")
        pred = _parse_predicate(name, pred_raw)
        missing = _REQUIRED_PRED_FIELDS - set(pred_raw.keys())
        if missing:
            raise ValueError(f"predicates.{name} missing fields: {sorted(missing)}")
        predicates[name] = pred
        alias_to_predicate[name] = name
        for alias in pred.aliases:
            if alias in alias_to_predicate:
                raise ValueError(f"duplicate predicate alias: {alias}")
            alias_to_predicate[alias] = name

    refuse_raw = data.get("refuse_list")
    if not isinstance(refuse_raw, list) or not refuse_raw:
        raise ValueError("refuse_list must be a non-empty list")
    refuse_list = frozenset(str(op) for op in refuse_raw)

    coding_dispatch = {"delegate", "dispatch"}
    if not coding_dispatch <= refuse_list:
        raise ValueError("coding/dispatch predicates must be in refuse_list")

    return LifeVocabRegistry(
        version=str(version),
        context_id=str(context_id),
        entity_types=entity_types,
        predicates=predicates,
        refuse_list=refuse_list,
        alias_to_predicate=alias_to_predicate,
    )


def render_jsonld_context(registry: LifeVocabRegistry) -> dict[str, object]:
    """Render JSON-LD @context view from the same SOT as validation."""
    terms: dict[str, object] = {
        "@version": registry.version,
        "@vocab": registry.context_id,
    }
    for name, pred in sorted(registry.predicates.items()):
        entry: dict[str, object] = {"@id": f"cortex:{name}"}
        if pred.description:
            entry["@description"] = pred.description
        terms[name] = entry
        for alias in pred.aliases:
            terms[alias] = {"@id": f"cortex:{name}"}
    return terms
