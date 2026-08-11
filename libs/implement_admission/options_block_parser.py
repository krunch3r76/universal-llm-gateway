"""Parse ``## options`` / ``options:`` YAML blocks from DIRECTIVE bodies."""

from __future__ import annotations

import re
from typing import Any

import yaml

_OPTIONS_HEADING_RE = re.compile(r"(?im)^##\s+options(?:\s*\([^)]*\))?\s*$")
_FENCED_YAML_RE = re.compile(
    r"```(?:ya?ml)?\s*\n(?P<body>.*?)\n```",
    re.DOTALL | re.IGNORECASE,
)
_INLINE_OPTIONS_RE = re.compile(
    r"(?im)^options:\s*\n(?P<body>(?:[ \t].*\n?)*)"
)
_TOP_LEVEL_OPTIONS_KEY_RE = re.compile(r"(?im)^options:\s*")


def options_block_present(markdown: str) -> bool:
    """True when the body carries an ``## options`` block or YAML ``options:`` key."""
    return extract_options_yaml_block(markdown) is not None


def extract_options_yaml_block(markdown: str) -> str | None:
    """Return YAML text for an options block, if present."""
    text = markdown or ""
    heading = _OPTIONS_HEADING_RE.search(text)
    if heading is not None:
        tail = text[heading.end() :]
        match = _FENCED_YAML_RE.search(tail)
        if match is not None:
            body = match.group("body")
            if _TOP_LEVEL_OPTIONS_KEY_RE.search(body):
                return body
        inline = _INLINE_OPTIONS_RE.search(tail)
        if inline is not None:
            return "options:\n" + inline.group("body")
    match = _FENCED_YAML_RE.search(text)
    if match is not None:
        body = match.group("body")
        if _TOP_LEVEL_OPTIONS_KEY_RE.search(body):
            return body
    inline = _INLINE_OPTIONS_RE.search(text)
    if inline is not None:
        return "options:\n" + inline.group("body")
    return None


def _normalize_option_rows(raw_options: Any) -> tuple[list[tuple[str, dict[str, Any]]], str | None]:
    if isinstance(raw_options, list):
        rows: list[tuple[str, dict[str, Any]]] = []
        for index, item in enumerate(raw_options):
            if not isinstance(item, dict):
                return [], f"options_row_{index}_invalid_shape"
            opt_id = item.get("id")
            if not isinstance(opt_id, str) or not opt_id.strip():
                return [], f"options_row_{index}_missing_id"
            rows.append((opt_id.strip(), item))
        return rows, None
    if isinstance(raw_options, dict):
        rows = []
        for key, item in raw_options.items():
            if not isinstance(key, str) or not key.strip():
                return [], "options_dict_invalid_key"
            if not isinstance(item, dict):
                return [], f"options_{key.strip()}_invalid_shape"
            rows.append((key.strip(), item))
        return rows, None
    return [], "options_not_list_or_mapping"


def parse_options_yaml_document(yaml_text: str) -> tuple[list[tuple[str, dict[str, Any]]], str | None]:
    """Parse a YAML document containing ``options:`` rows.

    Returns ``(rows, parse_error)``. ``parse_error`` is ``None`` on success.
    """
    try:
        data = yaml.safe_load(yaml_text)
    except yaml.YAMLError as exc:
        return [], f"options_yaml_parse_error:{type(exc).__name__}:{exc}"

    if not isinstance(data, dict):
        return [], "options_yaml_not_mapping"

    raw_options = data.get("options")
    if raw_options is None:
        return [], "options_yaml_missing_list"

    if isinstance(raw_options, list) and not raw_options:
        return [], None

    return _normalize_option_rows(raw_options)


def parse_options_block(markdown: str) -> tuple[list[tuple[str, dict[str, Any]]], str | None, bool]:
    """Parse options rows from a DIRECTIVE body.

    Returns ``(rows, parse_error, block_present)``.
    """
    yaml_text = extract_options_yaml_block(markdown)
    if yaml_text is None:
        return [], None, False
    rows, error = parse_options_yaml_document(yaml_text)
    return rows, error, True


__all__ = [
    "extract_options_yaml_block",
    "options_block_present",
    "parse_options_block",
    "parse_options_yaml_document",
]
