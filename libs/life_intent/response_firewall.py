"""Scan life-facing response/registry strings for forbidden dispatch vocabulary."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from .registry import LifeIntentRegistry, load_registry
from .work_order import render_work_order

FORBIDDEN_TOKENS = (
    "dispatch",
    "team_dispatch",
    "op=",
    "role=",
    "contract=",
    "cursor-sdk",
)

_LIFE_FACING_RESPONSE_KEYS = frozenset(
    {"work_order", "questions", "context", "subject", "detail", "verb"}
)

_REGISTRY_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "cortex" / "life_intent_v1.yaml"
)


def forbidden_hits(text: str, *, tokens: Iterable[str] = FORBIDDEN_TOKENS) -> list[str]:
    lowered = text.lower()
    return [token for token in tokens if token in lowered]


def collect_registry_life_facing_strings(registry: LifeIntentRegistry) -> list[str]:
    """YAML life-facing copy excluding refuse_list entries (those name blocked tokens)."""
    raw = yaml.safe_load(_REGISTRY_PATH.read_text(encoding="utf-8"))
    data = raw.get("life_intent_v1", raw)
    strings: list[str] = [registry.context_id]
    for entry in data.get("hard_out_patterns") or []:
        if isinstance(entry, dict) and entry.get("detail"):
            strings.append(str(entry["detail"]))
    for verb, spec in (data.get("verbs") or {}).items():
        if isinstance(spec, dict) and spec.get("lane"):
            strings.append(str(spec["lane"]))
        strings.append(verb)
    return strings


def collect_work_order_strings(registry: LifeIntentRegistry) -> list[str]:
    samples = [
        {
            "verb": "investigate",
            "subject": "reminder timing",
            "detail": "Notifications arrive twice on weekday mornings.",
            "urgency": "normal",
        },
        {
            "verb": "fix",
            "subject": "login timeout",
            "detail": "Users see timeout after thirty seconds consistently.",
            "urgency": "soon",
        },
        {
            "verb": "build",
            "subject": "export feature",
            "detail": "Users need CSV export from the dashboard weekly.",
            "urgency": "normal",
        },
        {
            "verb": "change",
            "subject": "billing copy",
            "detail": "Update invoice footer language before next release.",
            "urgency": "normal",
        },
    ]
    return [render_work_order(intent, registry) for intent in samples]


def scan_texts(texts: Iterable[str]) -> dict[str, list[str]]:
    violations: dict[str, list[str]] = {}
    for text in texts:
        hits = forbidden_hits(text)
        if hits:
            violations[text] = hits
    return violations


def collect_response_field_strings(payloads: Iterable[dict[str, Any]]) -> list[str]:
    strings: list[str] = []
    for payload in payloads:
        for key, value in payload.items():
            if key == "rejects" and isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and isinstance(item.get("detail"), str):
                        strings.append(item["detail"])
                continue
            if key == "normalized_intent" and isinstance(value, dict):
                for sub_key, sub_val in value.items():
                    if sub_key in _LIFE_FACING_RESPONSE_KEYS and isinstance(sub_val, str):
                        strings.append(sub_val)
                continue
            if key not in _LIFE_FACING_RESPONSE_KEYS:
                continue
            if isinstance(value, str):
                strings.append(value)
            elif isinstance(value, list):
                strings.extend(item for item in value if isinstance(item, str))
    return strings


def assert_life_facing_firewall(
    *,
    response_payloads: Iterable[dict[str, Any]],
    registry: LifeIntentRegistry | None = None,
) -> None:
    reg = registry or load_registry()
    texts = [
        *collect_registry_life_facing_strings(reg),
        *collect_work_order_strings(reg),
        *collect_response_field_strings(response_payloads),
    ]
    violations = scan_texts(texts)
    if violations:
        sample = next(iter(violations.items()))
        raise AssertionError(
            f"life-facing firewall violation in {sample[0]!r}: {sample[1]}"
        )
