"""Pure model-request matching for claude.ai CDP picker (no Playwright).

Prediction list = try-first hints only; live UI remains availability SOT
(operator a24691 / a24692).
"""

from __future__ import annotations

import re

from effort_vocabulary import EFFORT_TOKENS, to_picker_suffix

_LEAVE = frozenset({"leave", "none", "current"})
_EFFORT_TOKENS = EFFORT_TOKENS - {"none", "minimal"}  # picker/family strip set

# Pipeline prediction — try-first labels only. Miss ⇒ live UI discovery (SOT).
# Operator-authorized 2026-07-16; keep in sync with common picker SKUs.
PREDICTED_MODEL_LABELS: tuple[str, ...] = (
    "Opus 5",
    "Sonnet 5",
    "Haiku 4.5",
    "Fable 5",
)


def sealed_ask_default_effort(family: str) -> str | None:
    """Sealed-ask wire default when effort unset.

    Opus/Fable → High (operator 2026-07-16, a25255). Sonnet → Extra
    (operator 2026-08-25); Max remains an explicit option.
    """
    key = (family or "").strip().lower()
    if key.startswith("sonnet"):
        return "extra"
    if key.startswith("opus") or key.startswith("fable"):
        return "high"
    return None


# Bare dispatch aliases → canonical picker wire (team_dispatch ``cdp/fable``).
_PICKER_FAMILY_ALIASES: dict[str, str] = {
    "fable": "fable-5",
}


def normalize_picker_request(model: str) -> str:
    """Strip ``cdp/<picker>`` and canonicalize bare aliases for UI selection.

    Examples: ``cdp/fable`` → ``fable-5``, ``cdp/opus-5`` → ``opus-5``.
    """
    key = (model or "opus-5").strip()
    if "/" in key:
        provider, picker = key.split("/", 1)
        if provider == "cdp" and picker:
            key = picker
    alias = _PICKER_FAMILY_ALIASES.get(key.strip().lower())
    if alias:
        return alias
    return key


def compose_cdp_model_with_effort(
    model: str, reasoning_effort: str | None
) -> str:
    """Fold ``reasoning_effort`` into ``cdp/<family>-<effort>`` for the picker.

    Bare ``cdp/opus-5`` + ``reasoning_effort=max`` must become ``cdp/opus-5-max``
    so ``select_model`` attests Max — otherwise sealed-ask defaults Opus to High
    (operator observation 2026-08-03). Effort already on the model string wins.
    Unknown / empty effort leaves ``model`` unchanged. ``xhigh`` → ``extra``.
    """
    raw = (model or "").strip()
    if not raw:
        return raw
    effort = to_picker_suffix(reasoning_effort)
    if effort is None or effort not in _EFFORT_TOKENS:
        return raw
    picker = normalize_picker_request(raw)
    family, existing = parse_model_request(picker)
    if existing or family in _LEAVE:
        return raw
    if "/" in raw and raw.split("/", 1)[0] == "cdp":
        return f"cdp/{family}-{effort}"
    return f"{family}-{effort}"


def parse_model_request(requested: str) -> tuple[str, str | None]:
    """Split ``requested`` into (family_key, effort|None).

    Trailing effort tokens are stripped. Leave-tokens return as-is.
    Opus High defaulting is applied by ``select_model`` (sealed-ask policy),
    not here — callers may suppress or override effort.
    ``xhigh`` normalizes to picker suffix ``extra``.
    """
    key = (requested or "opus-5").strip().lower()
    if key in _LEAVE:
        return key, None
    # Keep version segments (opus-5, haiku-4.5); hyphen/space/underscore are separators.
    norm = re.sub(r"[\s_]+", "-", key)
    parts = [p for p in norm.split("-") if p]
    effort: str | None = None
    while parts and parts[-1] in _EFFORT_TOKENS:
        raw_token = parts.pop()
        effort = to_picker_suffix(raw_token) or raw_token
    family = "-".join(parts) if parts else key
    return family, effort


def family_pattern(family_request: str) -> re.Pattern[str]:
    """Build a UI-label regex from a short name (``sonnet-5`` → ``sonnet\\s*5``)."""
    s = (family_request or "").strip().lower().replace("_", "-")
    chunks = re.findall(r"[a-z]+|\d+(?:\.\d+)*", s)
    if not chunks:
        return re.compile(re.escape(s), re.I)
    parts: list[str] = []
    for i, chunk in enumerate(chunks):
        parts.append(re.escape(chunk))
        if i < len(chunks) - 1:
            parts.append(r"[\s\-_.]*")
    return re.compile("".join(parts), re.I)


def match_model_request(family_request: str, labels: list[str]) -> str | None:
    """Pick the best live radio label for ``family_request``, or None."""
    pat = family_pattern(family_request)
    scored: list[tuple[int, str]] = []
    for label in labels:
        text = (label or "").strip()
        if not text:
            continue
        m = pat.search(text)
        if not m:
            continue
        # Prefer longer match spans, then shorter labels (tighter family radio).
        scored.append((m.end() - m.start(), text))
    if not scored:
        return None
    scored.sort(key=lambda t: (-t[0], len(t[1])))
    return scored[0][1]


def match_effort_qualified_radio(
    family: str,
    labels: list[str],
    *,
    effort: str | None,
) -> str | None:
    """Prefer a live radio whose label already attests family+effort (a:30693).

    Cowork exposes High/Extra/Max as first-class radios. Clicking that SKU is
    the destination; ``effort-menu-trigger`` is optional. ``None`` means fall
    back to family match + submenu.
    """
    if not effort:
        return None
    hits = [
        text
        for raw in labels
        if (text := (raw or "").strip())
        and label_satisfies_request(family, text, effort=effort)
    ]
    if not hits:
        return None
    hits.sort(key=len)
    return hits[0]


def label_satisfies_request(
    requested: str,
    label: str,
    *,
    effort: str | None = None,
) -> bool:
    """True when ``label`` attests the requested family (+ effort when required).

    Effort rungs ``max`` / ``high`` / ``extra`` are exclusive: a Max request
    must not pass on a High label, and a High request must not pass on Max or
    Extra High (friction 24969).
    """
    family, parsed_effort = parse_model_request(requested)
    if effort is None:
        effort = parsed_effort
    if family in _LEAVE:
        return True
    text = (label or "").strip()
    if not text or not family_pattern(family).search(text):
        return False
    if effort == "max":
        return bool(re.search(r"Max", text, re.I))
    if effort == "high":
        if not re.search(r"High", text, re.I):
            return False
        # Extra High / Max must not satisfy a plain High request.
        if re.search(r"Extra", text, re.I) or re.search(r"Max", text, re.I):
            return False
        return True
    if effort == "extra":
        return bool(re.search(r"Extra", text, re.I))
    return True


def is_leave_request(requested: str) -> bool:
    return (requested or "").strip().lower() in _LEAVE


def family_nested_in_more_models(family: str) -> bool:
    """True when the live Cowork picker nests the family under More models."""
    return (family or "").strip().lower().startswith("fable")
