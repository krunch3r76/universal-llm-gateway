"""Scan life-facing response/registry strings for forbidden dispatch vocabulary."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml

from .registry import LifeIntentRegistry, load_registry
from .work_order import render_work_order

# Substring scan (case-insensitive). Colon forms match registry refuse_list
# (life_intent_v1.yaml); equals forms cover the parallel wire shape. Keep
# both — Sol F1: colon-only refuse_list was not mirrored here (a4917).
FORBIDDEN_TOKENS = (
    "dispatch",
    "team_dispatch",
    "op=",
    "op:",
    "role=",
    "role:",
    "contract=",
    "contract:",
    "model=",
    "model:",
    "cursor-sdk",
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


def _walk_payload_strings(node: Any) -> list[str]:
    """Collect every key name and string value from a JSON-like payload."""
    strings: list[str] = []
    if isinstance(node, dict):
        for key, value in node.items():
            strings.append(str(key))
            strings.extend(_walk_payload_strings(value))
    elif isinstance(node, list):
        for item in node:
            strings.extend(_walk_payload_strings(item))
    elif isinstance(node, str):
        strings.append(node)
    return strings


def collect_response_field_strings(payloads: Iterable[dict[str, Any]]) -> list[str]:
    strings: list[str] = []
    for payload in payloads:
        strings.extend(_walk_payload_strings(payload))
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
