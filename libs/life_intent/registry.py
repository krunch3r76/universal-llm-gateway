"""Load and validate cortex.life-intent/v1 registry from life_intent_v1.yaml."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_VALID_URGENCIES = frozenset({"normal", "soon"})
_REQUIRED_VERB_FIELDS = frozenset(
    {
        "lane",
        "creates_work_item",
        "entity_kind",
        "density_triage_at_birth",
        "required_skills_seed",
        "priority_from_urgency",
    }
)


@dataclass(frozen=True)
class VerbSpec:
    name: str
    lane: str
    creates_work_item: bool
    entity_kind: str | None
    density_triage_at_birth: str | None
    required_skills_seed: tuple[str, ...]
    priority_from_urgency: dict[str, str]


@dataclass(frozen=True)
class HardOutPattern:
    pattern: str
    code: str
    detail: str


@dataclass(frozen=True)
class LifeIntentRegistry:
    version: str
    context_id: str
    verbs: dict[str, VerbSpec]
    refuse_list: frozenset[str]
    hard_out_patterns: tuple[HardOutPattern, ...]
    intent_schema: dict[str, Any]
    urgency_values: frozenset[str]

    def verb_names(self) -> frozenset[str]:
        return frozenset(self.verbs.keys())

    def render_verb_enum(self) -> list[str]:
        return sorted(self.verbs.keys())

    def render_intent_input_schema(self) -> dict[str, Any]:
        """JSON Schema for propose intent fields; verb enum is registry-backed."""
        return {
            "type": "object",
            "required": ["verb", "subject", "detail"],
            "properties": {
                "verb": {"type": "string", "enum": self.render_verb_enum()},
                "subject": {"type": "string", "minLength": 3, "maxLength": 120},
                "detail": {"type": "string", "minLength": 10, "maxLength": 2000},
                "refs": {"type": "array", "items": {"type": "string"}},
                "urgency": {
                    "type": "string",
                    "enum": sorted(self.urgency_values),
                    "default": "normal",
                },
            },
        }


def _registry_path() -> Path:
    for env in ("ULG_WORKSPACE_ROOT", "WORKSPACE_ROOT", "PROJECT_ROOT"):
        raw = os.environ.get(env)
        if raw:
            candidate = Path(raw).expanduser() / "config" / "cortex" / "life_intent_v1.yaml"
            if candidate.is_file():
                return candidate.resolve()
    return (
        Path(__file__).resolve().parents[2] / "config" / "cortex" / "life_intent_v1.yaml"
    )


def _require_mapping(data: object, label: str) -> dict[str, Any]:
    if not isinstance(data, dict):
        raise ValueError(f"{label} must be a mapping")
    return data


def _parse_verb(name: str, raw: object) -> VerbSpec:
    spec = _require_mapping(raw, f"verbs.{name}")
    missing = _REQUIRED_VERB_FIELDS - set(spec.keys())
    if missing:
        raise ValueError(f"verbs.{name} missing fields: {sorted(missing)}")
    lane = spec.get("lane")
    if not lane or not isinstance(lane, str):
        raise ValueError(f"verbs.{name}.lane required")
    creates = spec.get("creates_work_item")
    if not isinstance(creates, bool):
        raise ValueError(f"verbs.{name}.creates_work_item must be bool")
    entity_kind = spec.get("entity_kind")
    if entity_kind is not None and not isinstance(entity_kind, str):
        raise ValueError(f"verbs.{name}.entity_kind must be string or null")
    density = spec.get("density_triage_at_birth")
    if density is not None and not isinstance(density, str):
        raise ValueError(f"verbs.{name}.density_triage_at_birth must be string or null")
    seed_raw = spec.get("required_skills_seed")
    if not isinstance(seed_raw, list):
        raise ValueError(f"verbs.{name}.required_skills_seed must be a list")
    priority_raw = _require_mapping(
        spec.get("priority_from_urgency"), f"verbs.{name}.priority_from_urgency"
    )
    if set(priority_raw.keys()) != _VALID_URGENCIES:
        raise ValueError(f"verbs.{name}.priority_from_urgency must cover normal and soon")
    return VerbSpec(
        name=name,
        lane=str(lane),
        creates_work_item=creates,
        entity_kind=entity_kind,
        density_triage_at_birth=density,
        required_skills_seed=tuple(str(s) for s in seed_raw),
        priority_from_urgency={str(k): str(v) for k, v in priority_raw.items()},
    )


def load_registry(path: Path | None = None) -> LifeIntentRegistry:
    """Load registry; fail closed on malformed or duplicate entries."""
    registry_path = path or _registry_path()
    if not registry_path.is_file():
        raise FileNotFoundError(f"life intent registry not found: {registry_path}")

    raw = yaml.safe_load(registry_path.read_text(encoding="utf-8"))
    data = _require_mapping(raw, "life_intent_v1")

    version = data.get("version")
    context_id = data.get("context_id")
    if not version or not context_id:
        raise ValueError("version and context_id are required")

    intent_schema = _require_mapping(data.get("intent_schema"), "intent_schema")
    urgency_field = _require_mapping(intent_schema.get("urgency"), "intent_schema.urgency")
    urgency_values = frozenset(str(v) for v in urgency_field.get("values") or [])

    verbs_raw = _require_mapping(data.get("verbs"), "verbs")
    if not verbs_raw:
        raise ValueError("verbs must be non-empty")
    verbs: dict[str, VerbSpec] = {}
    for name, verb_raw in verbs_raw.items():
        if name in verbs:
            raise ValueError(f"duplicate verb: {name}")
        verbs[name] = _parse_verb(name, verb_raw)

    refuse_raw = data.get("refuse_list")
    if not isinstance(refuse_raw, list) or not refuse_raw:
        raise ValueError("refuse_list must be a non-empty list")
    refuse_list = frozenset(str(item).lower() for item in refuse_raw)

    hard_out_raw = data.get("hard_out_patterns") or []
    if not isinstance(hard_out_raw, list):
        raise ValueError("hard_out_patterns must be a list")
    hard_out_patterns: list[HardOutPattern] = []
    for idx, entry in enumerate(hard_out_raw):
        spec = _require_mapping(entry, f"hard_out_patterns[{idx}]")
        hard_out_patterns.append(
            HardOutPattern(
                pattern=str(spec["pattern"]),
                code=str(spec.get("code") or "hard_out"),
                detail=str(spec["detail"]),
            )
        )

    return LifeIntentRegistry(
        version=str(version),
        context_id=str(context_id),
        verbs=verbs,
        refuse_list=refuse_list,
        hard_out_patterns=tuple(hard_out_patterns),
        intent_schema=intent_schema,
        urgency_values=urgency_values or _VALID_URGENCIES,
    )
