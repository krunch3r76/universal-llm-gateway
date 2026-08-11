"""Effort ladder + alias normalization + surface renderers."""

from __future__ import annotations

# Cursor / Auto wire + frontier portable intent (canonical).
WIRE_LADDER: tuple[str, ...] = ("low", "medium", "high", "xhigh", "max")

# CDP picker suffix tokens (UI Extra High maps to ``extra`` on the model id).
PICKER_LADDER: tuple[str, ...] = ("low", "medium", "high", "extra", "max")

# Provider-admission extras (OpenAI/Gemini surfaces); not picker/wire rungs.
PROVIDER_EXTENDED: frozenset[str] = frozenset({"none", "minimal"})

# Tokens recognized when stripping trailing effort from a model id / request.
EFFORT_TOKENS: frozenset[str] = frozenset(
    {*WIRE_LADDER, *PICKER_LADDER, *PROVIDER_EXTENDED}
)

_ALIASES: dict[str, str] = {
    "extra": "xhigh",
    "extra-high": "xhigh",
    "extra_high": "xhigh",
    "extrahigh": "xhigh",
}

_TESTIDS: dict[str, str] = {
    "low": "effort-option-low",
    "medium": "effort-option-medium",
    "high": "effort-option-high",
    "extra": "effort-option-xhigh",
    "xhigh": "effort-option-xhigh",
    "max": "effort-option-max",
}


def normalize_effort(raw: str | None) -> str | None:
    """Normalize a surface token to the wire ladder (or provider-extended).

    Unknown / empty → ``None``. Spaces become hyphens before alias lookup.
    """
    if raw is None:
        return None
    key = raw.strip().lower().replace(" ", "-")
    if not key:
        return None
    key = _ALIASES.get(key, key)
    if key in WIRE_LADDER or key in PROVIDER_EXTENDED:
        return key
    if key in PICKER_LADDER:
        return _ALIASES.get(key, key)
    return None


def to_wire(raw: str | None) -> str | None:
    """Canonical wire form (``xhigh``, never ``extra``)."""
    return normalize_effort(raw)


def to_picker_suffix(raw: str | None) -> str | None:
    """CDP model-id suffix: ``xhigh`` → ``extra``; other wire rungs identity."""
    wire = normalize_effort(raw)
    if wire is None:
        return None
    if wire == "xhigh":
        return "extra"
    if wire in PROVIDER_EXTENDED:
        return None
    return wire


def to_testid(raw: str | None) -> str | None:
    """Playwright ``data-testid`` for the effort menu option."""
    wire = normalize_effort(raw)
    if wire is None:
        return None
    # Accept picker ``extra`` directly as well as wire ``xhigh``.
    key = (raw or "").strip().lower()
    if key == "extra":
        return _TESTIDS["extra"]
    return _TESTIDS.get(wire)
