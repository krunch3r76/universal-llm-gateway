"""Association envelope + story_id election for ULG story wire (spec Bind 2)."""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from universal_logging import get_logger

logger = get_logger(__name__)

PURPOSE_UNSTATED = "(unstated)"
ASKED_BY_UNRESOLVED = "(unresolved: no from_agent or caller_agent)"
DEFAULT_TRIGGER_PURPOSE = "operator-proxy"

_INTENT_RE = re.compile(r"^intent:\s*(.+)$", re.MULTILINE | re.IGNORECASE)
_CHARTER_PACKET_RE = re.compile(
    r"(?:^|[/\\])charter-runner/(\d+)-w(\d+)\.md(?:$|[?#])",
    re.IGNORECASE,
)


def extract_purpose(body: str | None) -> str:
    """Return the ``intent:`` line value or visible thin fallback."""
    text = (body or "").strip()
    if not text:
        return PURPOSE_UNSTATED
    match = _INTENT_RE.search(text)
    if match is None:
        return PURPOSE_UNSTATED
    value = match.group(1).strip()
    return value or PURPOSE_UNSTATED


def resolve_asked_by(
    *,
    from_agent: str | None = None,
    caller_agent: str | None = None,
) -> str:
    """Resolve asked_by — never silently null; record explicit failure."""
    if from_agent and from_agent.strip():
        return from_agent.strip()
    if caller_agent and caller_agent.strip():
        return caller_agent.strip()
    return ASKED_BY_UNRESOLVED


def _story_id_from_dispatch_id(dispatch_id: str | None) -> str | None:
    if not dispatch_id:
        return None
    if len(dispatch_id) > 9 and dispatch_id[-9] == "-":
        suffix = dispatch_id[-8:]
        if len(suffix) == 8 and all(c in "0123456789abcdef" for c in suffix):
            return dispatch_id[:-9]
    return None


def _story_id_from_charter_packet(packet_path: str | None) -> str | None:
    if not packet_path:
        return None
    match = _CHARTER_PACKET_RE.search(packet_path.replace("\\", "/"))
    if match is None:
        return None
    return f"{match.group(1)}#{match.group(2)}"


def elect_story_id(
    *,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    call_id: str | None = None,
    packet_path: str | None = None,
    charter_root: str | None = None,
    window_index: int | None = None,
) -> str:
    """Elect the first correlation identifier in the causal chain (spec Bind 2).

    Priority:
    1. CLI consult ``call_id``
    2. Charter-origin ``{root}#{window_index}`` (explicit or packet_path)
    3. Stargate admit ``request_id`` (explicit or derived from dispatch_id)
    4. ``dispatch_id`` (nested auto and other paths that mint only dispatch_id)
    """
    if call_id and call_id.strip():
        return call_id.strip()
    if charter_root and charter_root.strip() and window_index is not None:
        return f"{charter_root.strip()}#{window_index}"
    charter_from_packet = _story_id_from_charter_packet(packet_path)
    if charter_from_packet:
        return charter_from_packet
    if request_id and request_id.strip():
        return request_id.strip()
    derived = _story_id_from_dispatch_id(dispatch_id)
    if derived:
        return derived
    if dispatch_id and dispatch_id.strip():
        return dispatch_id.strip()
    return "(unresolved: no story_id source)"


@dataclass(frozen=True, slots=True)
class AssociationEnvelope:
    story_id: str
    asked_by: str
    purpose: str


def build_association_envelope(
    *,
    purpose_body: str | None = None,
    from_agent: str | None = None,
    caller_agent: str | None = None,
    request_id: str | None = None,
    dispatch_id: str | None = None,
    call_id: str | None = None,
    packet_path: str | None = None,
    charter_root: str | None = None,
    window_index: int | None = None,
) -> AssociationEnvelope:
    return AssociationEnvelope(
        story_id=elect_story_id(
            request_id=request_id,
            dispatch_id=dispatch_id,
            call_id=call_id,
            packet_path=packet_path,
            charter_root=charter_root,
            window_index=window_index,
        ),
        asked_by=resolve_asked_by(
            from_agent=from_agent,
            caller_agent=caller_agent,
        ),
        purpose=extract_purpose(purpose_body),
    )


def stamp_association_fields(
    payload: dict[str, Any],
    envelope: AssociationEnvelope,
) -> dict[str, Any]:
    """Add additive optional association fields without mutating required keys."""
    payload["story_id"] = envelope.story_id
    payload["asked_by"] = envelope.asked_by
    payload["purpose"] = envelope.purpose
    return payload


def safe_emit_observation(emit_fn: Callable[[], None], *, label: str) -> None:
    """Failure-isolated observability emit — never takes down the work path."""
    try:
        emit_fn()
    except Exception:
        logger.exception("story-wire observation emit failed: %s", label)
