"""Minimal shared browser governance contract (C0).

Runtime mode readers, unknown-safe supervision envelopes, sanitized UI banner
classification, and CDP-embeddable banner scan JS for R1/S1 consumers.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping, Sequence
from typing import Any, Literal

DEFAULT_WARN_MEM_AVAILABLE_MIB = 6144
DEFAULT_WARN_SWAP_FREE_MIB = 2048
DEFAULT_WARN_DISK_FREE_GIB = 50.0

_OFF_VALUES = frozenset({"off", "0", "false", "no", "disabled"})
_KEEPALIVE_OFF_VALUES = frozenset({"off", "0", "false", "none", "disabled"})

EXCLUDED_MESSAGE_ANCESTOR_SELECTOR: str = (
    '[data-testid*="message" i], [data-message-author-role], '
    "[data-testid='assistant-message'], "
    "[data-testid='user-message'], .sand-message-prose, .markdown, .prose, "
    "article, pre, code, [data-is-streaming], [contenteditable='true'], "
    "textarea, input, form"
)

_RE_COMPUTER_UNRESPONSIVE = re.compile(
    r"computer became unresponsive|box host failed|"
    r"forever[- ]?box.*(?:failed|unresponsive|offline)",
    re.IGNORECASE,
)
_RE_RENDERER_CRASHED = re.compile(
    r"aw, snap!|renderer process crashed|page unresponsive",
    re.IGNORECASE,
)
_RE_RATE_OR_USAGE_LIMIT = re.compile(
    r"usage limit reached|you've hit your limit|rate limit exceeded",
    re.IGNORECASE,
)
_RE_SESSION_EXPIRED = re.compile(
    r"session expired|sign in again|authentication expired",
    re.IGNORECASE,
)

_RE_EMAIL = re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")
_RE_BEARER = re.compile(r"Bearer\s+\S+", re.IGNORECASE)
_RE_URL_QUERY = re.compile(r"(https?://[^\s?#]+)(?:\?[^\s]*)?", re.IGNORECASE)
_RE_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{32,}\b")
_RE_LONG_B64 = re.compile(r"\b[A-Za-z0-9+/]{40,}={0,2}\b")
_RE_C0_C1_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _strip_env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip()


def _is_off_value(raw: str, *, extra_off: frozenset[str] | None = None) -> bool:
    lowered = raw.strip().lower()
    if lowered in _OFF_VALUES:
        return True
    if extra_off and lowered in extra_off:
        return True
    return False


def _normalize_display_tokens(displays: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    out: list[str] = []
    for item in displays:
        token = str(item).strip().lstrip(":")
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return tuple(out)


def supervision_gate_mode() -> Literal["off", "observe"]:
    """Read ULG_SUPERVISION_GATE_MODE at call time (default observe)."""
    raw = _strip_env("ULG_SUPERVISION_GATE_MODE", "observe")
    if _is_off_value(raw):
        return "off"
    return "observe"


def supervision_poll_mode() -> Literal["off", "observe"]:
    """Read ULG_SUPERVISION_POLL_MODE at call time (default observe)."""
    raw = _strip_env("ULG_SUPERVISION_POLL_MODE", "observe")
    if _is_off_value(raw):
        return "off"
    return "observe"


def census_enabled() -> bool:
    """Read ULG_BROWSER_CENSUS_ENABLED at call time (default enabled)."""
    raw = _strip_env("ULG_BROWSER_CENSUS_ENABLED", "1")
    return not _is_off_value(raw)


def admission_preflight_mode() -> Literal["off", "warn"]:
    """Admission preflight mode; off when census disabled or env disables warn."""
    if not census_enabled():
        return "off"
    raw = _strip_env("ULG_BROWSER_ADMISSION_PREFLIGHT", "warn")
    if _is_off_value(raw):
        return "off"
    return "warn"


def warn_mem_available_mib() -> float:
    raw = _strip_env("ULG_BROWSER_WARN_MEM_MIB", str(DEFAULT_WARN_MEM_AVAILABLE_MIB))
    try:
        return float(raw)
    except ValueError:
        return float(DEFAULT_WARN_MEM_AVAILABLE_MIB)


def warn_swap_free_mib() -> float:
    raw = _strip_env("ULG_BROWSER_WARN_SWAP_MIB", str(DEFAULT_WARN_SWAP_FREE_MIB))
    try:
        return float(raw)
    except ValueError:
        return float(DEFAULT_WARN_SWAP_FREE_MIB)


def warn_disk_free_gib() -> float:
    raw = _strip_env("ULG_BROWSER_WARN_DISK_GIB", str(DEFAULT_WARN_DISK_FREE_GIB))
    try:
        return float(raw)
    except ValueError:
        return float(DEFAULT_WARN_DISK_FREE_GIB)


def grok_keepalive_displays(configured_displays: Sequence[str]) -> tuple[str, ...]:
    """Resolve Grok keepalive display ids from env or configured defaults."""
    raw = os.environ.get("ULG_GROK_KEEPALIVE_DISPLAYS")
    if raw is None or not raw.strip():
        return _normalize_display_tokens(configured_displays)
    stripped = raw.strip()
    if _is_off_value(stripped, extra_off=_KEEPALIVE_OFF_VALUES):
        return ()
    if stripped.lower() == "secondary_off":
        return tuple(
            token
            for token in _normalize_display_tokens(configured_displays)
            if token == "9"
        )
    parts = [p.strip().lstrip(":") for p in stripped.split(",")]
    return _normalize_display_tokens(parts)


def sanitize_ui_banner_text(raw: str | None, *, max_chars: int = 160) -> str | None:
    if raw is None:
        return None
    text = _RE_C0_C1_CONTROL.sub("", str(raw))
    text = " ".join(text.split())
    if not text:
        return None
    text = _RE_EMAIL.sub("<redacted-email>", text)
    text = _RE_BEARER.sub("Bearer <redacted>", text)

    def _url_keep_base(match: re.Match[str]) -> str:
        return match.group(1)

    text = _RE_URL_QUERY.sub(_url_keep_base, text)
    text = _RE_LONG_HEX.sub("<redacted-hex>", text)
    text = _RE_LONG_B64.sub("<redacted-token>", text)
    if len(text) > max_chars:
        text = text[:max_chars]
    return text or None


def classify_ui_banner_text(text: str | None) -> tuple[str | None, str | None]:
    sanitized = sanitize_ui_banner_text(text)
    if not sanitized:
        return (None, None)
    if _RE_COMPUTER_UNRESPONSIVE.search(sanitized):
        return ("computer_unresponsive", sanitized)
    if _RE_RENDERER_CRASHED.search(sanitized):
        return ("renderer_crashed", sanitized)
    if _RE_RATE_OR_USAGE_LIMIT.search(sanitized):
        return ("rate_or_usage_limit", sanitized)
    if _RE_SESSION_EXPIRED.search(sanitized):
        return ("session_expired", sanitized)
    return (None, None)


def unscanned_supervision(
    *,
    surface: str,
    reason: str,
    visibility_state: str = "unknown",
) -> dict[str, Any]:
    return {
        "mode": supervision_gate_mode(),
        "surface": surface,
        "scanned": False,
        "ui_error": {
            "scanned": False,
            "present": None,
            "kind": None,
            "summary": None,
        },
        "auth_state": "unknown",
        "visibility_state": visibility_state,
        "app_health": None,
        "reason": reason,
    }


def build_supervision_envelope(
    *,
    surface: str,
    auth_state: str = "unknown",
    visibility_state: str = "unknown",
    ui_banner: Mapping[str, Any] | None = None,
    app_health: Mapping[str, Any] | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    if supervision_gate_mode() == "off":
        return unscanned_supervision(
            surface=surface,
            reason=reason or "gate_mode_off",
            visibility_state="unknown",
        )

    ui_error: dict[str, Any]
    ui_banner_scanned = bool(ui_banner and ui_banner.get("scanned"))
    if ui_banner is None or not ui_banner_scanned:
        ui_error = {
            "scanned": False,
            "present": None,
            "kind": None,
            "summary": None,
        }
    else:
        kind = ui_banner.get("kind")
        if isinstance(kind, str):
            kind_str: str | None = kind
        else:
            kind_str = None
        if kind_str is None and ui_banner.get("present"):
            classified_kind, classified_summary = classify_ui_banner_text(
                ui_banner.get("summary") if isinstance(ui_banner.get("summary"), str) else None
            )
            kind_str = classified_kind
            summary_raw = classified_summary or ui_banner.get("summary")
        else:
            summary_raw = ui_banner.get("summary")
        summary = sanitize_ui_banner_text(
            summary_raw if isinstance(summary_raw, str) else None
        )
        present = bool(kind_str or ui_banner.get("present"))
        ui_error = {
            "scanned": True,
            "present": present,
            "kind": kind_str,
            "summary": summary,
        }

    observed = (
        ui_banner_scanned
        or auth_state != "unknown"
        or visibility_state != "unknown"
        or app_health is not None
    )

    return {
        "mode": supervision_gate_mode(),
        "surface": surface,
        "scanned": observed,
        "ui_error": ui_error,
        "auth_state": auth_state,
        "visibility_state": visibility_state,
        "app_health": dict(app_health) if app_health is not None else None,
        "reason": reason,
    }


# Self-contained IIFE for CDP Runtime.evaluate(returnByValue=True).
SUPERVISION_BANNER_SCAN_JS: str = f"""(() => {{
  const EXCLUDED = {EXCLUDED_MESSAGE_ANCESTOR_SELECTOR!r};
  const patterns = [
    {{ kind: 'computer_unresponsive', re: /computer became unresponsive|box host failed|forever[- ]?box.*(?:failed|unresponsive|offline)/i }},
    {{ kind: 'renderer_crashed', re: /aw, snap!|renderer process crashed|page unresponsive/i }},
    {{ kind: 'rate_or_usage_limit', re: /usage limit reached|you've hit your limit|rate limit exceeded/i }},
    {{ kind: 'session_expired', re: /session expired|sign in again|authentication expired/i }},
  ];
  const selectors = [
    "[role='alert']", "[role='status']", "[role='dialog']", "[aria-live]",
    "[data-sonner-toast]", "header", "[class*='banner']", "[class*='toast']",
    "[class*='error']",
  ].join(', ');
  const isExcluded = (el) => {{
    if (!el || !el.closest) return true;
    try {{
      if (el.matches && el.matches(EXCLUDED)) return true;
      if (el.closest(EXCLUDED)) return true;
      if (el.querySelector && el.querySelector(EXCLUDED)) return true;
    }} catch (_e) {{ return true; }}
    return false;
  }};
  const nodes = document.querySelectorAll(selectors);
  for (const el of nodes) {{
    if (isExcluded(el)) continue;
    if (!(el.offsetWidth || el.offsetHeight || el.getClientRects().length)) continue;
    if (el.children && el.children.length > 6) continue;
    const text = (el.innerText || el.textContent || '').replace(/\\s+/g, ' ').trim();
    if (!text || text.length > 300) continue;
    for (const {{ kind, re }} of patterns) {{
      if (re.test(text)) {{
        return {{
          scanned: true,
          present: true,
          kind,
          summary: text.slice(0, 512),
        }};
      }}
    }}
  }}
  return {{ scanned: true, present: false, kind: null, summary: null }};
}})()"""
