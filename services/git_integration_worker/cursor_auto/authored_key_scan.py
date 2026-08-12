"""Vocabulary-free authored-key scan for DIRECTIVE bodies (7119 §0)."""

from __future__ import annotations

import re

import yaml

from implement_admission.propagation_block_parser import (
    extract_propagation_yaml_block,
    propagation_block_present,
)

_LINE_KEY_RE = re.compile(r"^[ \t]*([a-z_][a-z0-9_]*)[ \t]*:(.*)$")
_FENCE_RE = re.compile(r"^([ \t]*)(```|~~~)")

# Keys consumed by contract gates — not PropagationRow fields; never "dropped".
_GATE_KEYS = frozenset({"effects_expected", "scope", "propagation", "contract", "type"})


def _yaml_mapping_keys(yaml_text: str) -> dict[str, str]:
    """Return top-level YAML keys under ``propagation:`` list items."""
    values: dict[str, str] = {}
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return values
    if not isinstance(data, dict):
        return values
    rows = data.get("propagation")
    if not isinstance(rows, list):
        return values
    for item in rows:
        if not isinstance(item, dict):
            continue
        for key, raw in item.items():
            if not isinstance(key, str):
                continue
            if raw is None:
                continue
            values[key] = str(raw).strip()
    return values


def _top_level_line_keys(body: str, *, skip_yaml_region: bool) -> dict[str, str]:
    """Line-start keys outside fenced blocks (and outside propagation YAML when flagged)."""
    values: dict[str, str] = {}
    in_fence = False
    yaml_block = extract_propagation_yaml_block(body) if skip_yaml_region else None
    yaml_start = body.find(yaml_block) if yaml_block else -1
    yaml_end = yaml_start + len(yaml_block) if yaml_block and yaml_start >= 0 else -1

    for line in (body or "").splitlines():
        fence = _FENCE_RE.match(line)
        if fence:
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        match = _LINE_KEY_RE.match(line)
        if match is None:
            continue
        key = match.group(1).lower()
        pos = body.find(line)
        if skip_yaml_region and yaml_start >= 0 and yaml_end > yaml_start:
            if yaml_start <= pos < yaml_end:
                continue
        tail = match.group(2).strip()
        if tail.startswith("`"):
            continue
        values[key] = tail
    return values


def scan_authored_body_keys(body: str) -> tuple[dict[str, str], dict[str, str], bool]:
    """Scan authored keys at shorthand top level and inside ``## propagation`` YAML.

    Returns ``(top_level, yaml_block, duplicate_conflict)``. *duplicate_conflict*
    is True when the same PropagationRow field name appears in both regions.
    """
    text = body or ""
    yaml_text = extract_propagation_yaml_block(text)
    yaml_values = _yaml_mapping_keys(yaml_text) if yaml_text else {}
    top_values = _top_level_line_keys(text, skip_yaml_region=bool(yaml_text))

    row_fields = {k for k in yaml_values} & {k for k in top_values}
    duplicate = bool(row_fields)
    merged = {**top_values, **yaml_values}
    return top_values, yaml_values, duplicate


def authored_keys_for_parity(body: str) -> tuple[frozenset[str], dict[str, str], bool]:
    """Authored keys minus gate-only tokens; values keyed lowercase."""
    top, yaml_part, duplicate = scan_authored_body_keys(body)
    merged_values = {**top, **yaml_part}
    keys = frozenset(k for k in merged_values if k not in _GATE_KEYS)
    if propagation_block_present(body):
        pass  # ``propagation`` heading is structural, not a row field
    return keys, merged_values, duplicate


__all__ = [
    "authored_keys_for_parity",
    "scan_authored_body_keys",
]
