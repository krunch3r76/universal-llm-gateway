"""Parse §4 propagation YAML blocks from authored CLOSEOUT markdown."""

from __future__ import annotations

import re
from typing import Any

import yaml

_PROPAGATION_HEADING_RE = re.compile(
    r"(?im)^##\s+propagation(?:\s*\([^)]*\))?\s*$"
)
_FENCED_YAML_RE = re.compile(
    r"```(?:ya?ml)?\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)
_PROOF_CLASS_VALUES = frozenset({"process_live", "client_visible", "served_artifact"})


def _normalize_row(raw: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    service = raw.get("service")
    if not isinstance(service, str) or not service.strip():
        return None
    code_ref = raw.get("code_ref")
    if code_ref is not None and (not isinstance(code_ref, str) or not code_ref.strip()):
        return None
    return raw


def parse_propagation_yaml_document(yaml_text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse a YAML document containing ``propagation: [...]``.

    Returns ``(rows, flags)``. Rows missing ``proof_class`` are flagged, not defaulted.
    """
    flags: list[str] = []
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return [], [f"propagation_yaml_parse_error:{type(exc).__name__}"]

    if not isinstance(data, dict):
        return [], ["propagation_yaml_not_mapping"]

    raw_rows = data.get("propagation")
    if not isinstance(raw_rows, list):
        return [], ["propagation_yaml_missing_list"]

    rows: list[dict[str, Any]] = []
    for index, item in enumerate(raw_rows):
        normalized = _normalize_row(item)
        if normalized is None:
            flags.append(f"propagation_row_{index}_invalid_shape")
            continue
        proof_class = normalized.get("proof_class")
        if not isinstance(proof_class, str) or not proof_class.strip():
            flags.append(f"propagation_row_{index}_missing_proof_class")
            continue
        if proof_class.strip() not in _PROOF_CLASS_VALUES:
            flags.append(f"propagation_row_{index}_unknown_proof_class:{proof_class}")
            continue
        rows.append(normalized)
    return rows, flags


def extract_propagation_yaml_block(markdown: str) -> str | None:
    """Return fenced YAML under a ``## propagation`` heading, if present."""
    heading = _PROPAGATION_HEADING_RE.search(markdown)
    if heading is None:
        match = _FENCED_YAML_RE.search(markdown)
        if match and "propagation:" in match.group("body"):
            return match.group("body")
        return None
    tail = markdown[heading.end() :]
    match = _FENCED_YAML_RE.search(tail)
    if match is None:
        return None
    body = match.group("body")
    return body if "propagation:" in body else None


def propagation_block_present(markdown: str) -> bool:
    """True when markdown carries an authored ``## propagation`` YAML block."""
    return extract_propagation_yaml_block(markdown) is not None


def parse_propagation_block(markdown: str) -> tuple[list[dict[str, Any]], list[str]]:
    """Parse §4 propagation rows from closeout markdown."""
    yaml_text = extract_propagation_yaml_block(markdown)
    if not yaml_text:
        return [], []
    return parse_propagation_yaml_document(yaml_text)


def propagation_rows_from_markdown_sources(
    *sources: str | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Scan markdown sources in order; first non-empty §4 block wins."""
    for source in sources:
        if not source or not source.strip():
            continue
        rows, flags = parse_propagation_block(source)
        if rows or flags:
            return rows, flags
    return [], []


__all__ = [
    "extract_propagation_yaml_block",
    "parse_propagation_block",
    "parse_propagation_yaml_document",
    "propagation_block_present",
    "propagation_rows_from_markdown_sources",
]
