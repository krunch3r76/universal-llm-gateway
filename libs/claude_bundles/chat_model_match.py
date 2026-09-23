"""Pure model-request matching for claude.ai CDP picker (no Playwright).

Prediction list = try-first hints only; live UI remains availability SOT
(operator a24691 / a24692).
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

from effort_vocabulary import EFFORT_TOKENS, to_picker_suffix

_LEAVE = frozenset({"leave", "none", "current"})
_EFFORT_TOKENS = EFFORT_TOKENS - {"none", "minimal"}  # picker/family strip set

# Pipeline prediction — try-first labels only. Miss ⇒ live UI discovery (SOT).
# Operator-authorized 2026-07-16; keep in sync with common picker SKUs.
PREDICTED_MODEL_LABELS: tuple[str, ...] = (
    "Opus 5",
    "Sonnet 5",
    "Haiku 4.5",
    "Fable 5.1",
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
# Fable 5.1 launched 2026-09-01 (Anthropic ``claude-fable-5-1``, same headline
# $/M as Fable 5 — cache reads only). Bare alias tracks the recommended
# current release; pin ``cdp/fable-5`` explicitly for the prior generation.
_PICKER_FAMILY_ALIASES: dict[str, str] = {
    "fable": "fable-5.1",
}


def normalize_picker_request(model: str) -> str:
    """Strip ``cdp/<picker>`` and canonicalize bare aliases for UI selection.

    Examples: ``cdp/fable`` → ``fable-5.1``, ``cdp/opus-5`` → ``opus-5``.
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


def compose_cdp_model_with_effort(model: str, reasoning_effort: str | None) -> str:
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


_OTHER_FAMILY_WORDS = ("opus", "sonnet", "haiku", "fable")
_CHIP_FAMILY = re.compile(r"\b(fable|opus|sonnet|haiku)\b", re.I)
_CHIP_EFFORT = re.compile(r"\b(max|extra|high|medium|low)\b", re.I)
_GLUED_SUBTITLE = re.compile(r"\d(?:\.\d+)?(?=[A-Za-z])")


def radio_own_name(label: str) -> str:
    """Model-name line of a picker radio.

    Cowork puts the subtitle on the next line. ``textContent`` drops that
    break, so a title and a subtitle become one string.
    """
    text = (label or "").strip()
    if not text:
        return ""
    return text.splitlines()[0].strip()


def menu_label_glued(label: str) -> bool:
    """True when a version token is glued to the following subtitle word."""
    return bool(_GLUED_SUBTITLE.search(label or ""))


def _family_word(family: str) -> str:
    match = re.match(r"[a-z]+", (family or "").strip().lower())
    return match.group(0) if match else ""


def _names_other_family(family: str, label: str) -> bool:
    """True when ``label`` also names a different model family.

    A parent menuitemradio concatenates every row. That string matches the
    requested family and the already-selected one.
    """
    mine = _family_word(family)
    low = (label or "").lower()
    return any(other != mine and other in low for other in _OTHER_FAMILY_WORDS)


def prefer_model_name_index(family: str, rows: list[dict[str, str]]) -> int | None:
    """Index of the radio whose own label is the model name.

    Parent groups match a family substring and sort first in the DOM.
    ``locator.first`` then clicks that group; the hit lands on the
    already-selected row's effort control. Sibling versions stay in DOM
    order so an Opus 5.5 row ahead of an older Opus row is unchanged.
    A glued subtitle loses to the same family's name-only label.
    """
    pat = family_pattern(family)
    candidates: list[int] = []
    for index, row in enumerate(rows):
        own = radio_own_name(str(row.get("own") or ""))
        if not own or _names_other_family(family, own) or not pat.search(own):
            continue
        candidates.append(index)
    if not candidates:
        return None
    named = [
        index
        for index in candidates
        if not menu_label_glued(str(rows[index].get("own") or ""))
    ]
    return (named or candidates)[0]


def index_for_named_radio(rows: list[dict[str, str]], label: str) -> int | None:
    """Index of the radio whose own or full label equals ``label``.

    Exact equality skips a parent whose text merely contains the label.
    The shortest full text wins when a name and a glued copy both exist.
    """
    wanted = (label or "").strip()
    if not wanted:
        return None
    hits = [
        index
        for index, row in enumerate(rows)
        if str(row.get("full") or "") == wanted or str(row.get("own") or "") == wanted
    ]
    if not hits:
        return None
    hits.sort(key=lambda index: len(str(rows[index].get("full") or "")))
    return hits[0]


def family_attested(requested: str, label: str) -> bool:
    """True when ``label`` names the requested family, effort ignored.

    Effort is a second step. A chip that only changed effort on the previous
    family does not attest the click.
    """
    family, _effort = parse_model_request(requested)
    if family in _LEAVE:
        return True
    return bool(family_pattern(family).search(label or ""))


def matched_label_is_chip(matched: str, chip: str) -> bool:
    """True when the chip text contains the radio's own model name."""
    name = radio_own_name(matched)
    if not name or not (chip or "").strip():
        return False
    return name.lower() in chip.lower()


def effort_only_chip_change(before: str, after: str) -> bool:
    """True when the chip kept its family and only the effort token moved."""
    before_family = _CHIP_FAMILY.search(before or "")
    after_family = _CHIP_FAMILY.search(after or "")
    if (
        not before_family
        or not after_family
        or before_family.group(1).lower() != after_family.group(1).lower()
    ):
        return False
    before_effort = _CHIP_EFFORT.search(before or "")
    after_effort = _CHIP_EFFORT.search(after or "")
    before_token = before_effort.group(1).lower() if before_effort else ""
    after_token = after_effort.group(1).lower() if after_effort else ""
    return before_token != after_token


def select_no_attest_status(
    *,
    requested: str,
    before: str,
    after: str,
    matched: str | None,
    path: str,
    available: list[str] | None = None,
    effort: dict | None = None,
    as_of: str | None = None,
) -> dict:
    """Status for a click that did not leave the chip on the requested model.

    Menu glue, a matched label that is not the chip, and an effort-only chip
    change stay separate fields. Callers branch on ``step`` instead of parsing
    them out of an unverified stall.
    """
    labels = list(available or [])
    matched_text = matched or ""
    glued = menu_label_glued(matched_text) or any(
        menu_label_glued(item) for item in labels
    )
    return {
        "ok": False,
        "value": "select_no_attest",
        "step": "select_no_attest",
        "before": before,
        "after": after,
        "matched": matched,
        "requested": requested,
        "path": path,
        "as_of": as_of or datetime.now(UTC).isoformat(),
        "source": "cdp.model_select",
        "scope": requested,
        "epoch": path,
        "menu_label_glued": glued,
        "matched_is_chip": matched_label_is_chip(matched_text, after),
        "effort_only_chip_change": effort_only_chip_change(before, after),
        "available_models": labels,
        "effort": effort,
    }
