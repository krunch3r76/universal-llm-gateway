"""Sanitize DOM observation text, controls, and aria values."""

from __future__ import annotations

from typing import Any

from browser_governance.contract import sanitize_ui_banner_text

_CLIP_120 = 120


def _clip_str(value: Any, n: int) -> Any:
    if value is None:
        return None
    if not isinstance(value, str):
        return value
    return value[:n] if len(value) > n else value


def shape_text_block(raw: dict[str, Any] | None) -> dict[str, Any]:
    text = raw or {}
    page = str(text.get("page_text") or "")
    sanitized = sanitize_ui_banner_text(page, max_chars=500) or ""
    chars = int(text.get("chars") or len(page))
    cut = bool(text.get("cut")) or len(page) > 500
    if len(sanitized) > 500:
        sanitized = sanitized[:500]
        cut = True
    return {"page_text": sanitized, "chars": chars, "cut": cut}


def shape_aria_value(value: Any) -> str:
    if value is None:
        return ""
    shaped = sanitize_ui_banner_text(str(value), max_chars=120)
    return shaped if shaped is not None else ""


def shape_control(raw: dict[str, Any]) -> dict[str, Any]:
    ctrl = dict(raw)
    ctrl["text"] = shape_text_block(raw.get("text"))
    if raw.get("aria_label") is not None:
        ctrl["aria_label"] = shape_aria_value(raw.get("aria_label"))
    ctrl["role"] = _clip_str(raw.get("role"), _CLIP_120)
    return ctrl


def shape_node(raw: dict[str, Any]) -> dict[str, Any]:
    node = dict(raw)
    node["text"] = shape_text_block(raw.get("text"))
    node["controls"] = [shape_control(c) for c in (raw.get("controls") or [])]
    node["aria"] = {k: shape_aria_value(v) for k, v in (raw.get("aria") or {}).items()}
    node["role"] = _clip_str(raw.get("role"), _CLIP_120)
    node["data_testid"] = _clip_str(raw.get("data_testid"), _CLIP_120)
    return node


def shape_page_block(page: dict[str, Any]) -> dict[str, Any]:
    shaped = dict(page)
    if isinstance(shaped.get("title"), dict):
        shaped["title"] = shape_text_block(shaped["title"])
    if isinstance(shaped.get("url"), dict):
        shaped["url"] = shape_text_block(shaped["url"])
    return shaped
