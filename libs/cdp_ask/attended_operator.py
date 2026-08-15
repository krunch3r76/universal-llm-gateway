"""Mission-operator attended CSE resolver — registry authority + liveness probe.

The unique active registration with ``purpose ∈ OPERATOR_PROXY_MISSION_PURPOSES``
and a bound ``chat_url`` is the attendance candidate set. Liveness verifies the
candidate on its own ``cdp_url`` only — it never selects among ports.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import urlparse

from claude_bundles import cdp_orphans, cdp_registry
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_provenance_resolve import is_host_listable
from claude_bundles.cse_url import normalize_cse_url
from claude_bundles.operator_proxy_mission import OPERATOR_PROXY_MISSION_PURPOSES

from cdp_ask.attended_conflict import (
    purpose_filtered_url_conflicts,
    shared_url_candidates,
)
from cdp_ask.attended_dormant import candidate_dict as dormant_candidate_dict
from cdp_ask.attended_dormant import dormant_candidates

_SOURCE = "cse-session-registry"
_CSE_MARKER = "/cowork/cse_"


@dataclass(frozen=True)
class AttendedCandidate:
    """One registry-registered mission-purpose lane with bound chat_url."""

    registration_id: str
    cdp_url: str
    chat_url: str
    purpose: str
    provenance: dict[str, Any] | None = None


@dataclass(frozen=True)
class LivenessProbe:
    live: bool
    checked_at: float
    page_urls_seen: tuple[str, ...] = ()


@dataclass(frozen=True)
class AttendedResolveSuccess:
    registration_id: str
    cdp_url: str
    chat_url: str
    purpose: str
    probe: LivenessProbe
    source: str
    shadow_urls: list[dict[str, Any]]
    provenance: dict[str, Any] | None = None
    conflict: dict[str, Any] | None = None


@dataclass(frozen=True)
class AttendedResolveDormant:
    """Attended seat whose Chrome host is released but reopenable by URL."""

    registration_id: str
    chat_url: str
    purpose: str
    source: str
    shadow_urls: list[dict[str, Any]]
    dormant_at: float | None = None
    provenance: dict[str, Any] | None = None
    reattachable: bool = True


@dataclass(frozen=True)
class AttendedResolveRefused:
    code: Literal["no_attended_cse", "ambiguous_attended", "attended_liveness_failed"]
    candidates_considered: int = 0
    candidates: list[dict[str, Any]] | None = None
    candidate: dict[str, Any] | None = None
    probe: LivenessProbe | None = None
    shadow_urls: list[dict[str, Any]] | None = None
    conflict: dict[str, Any] | None = None


AttendedResolveOutcome = (
    AttendedResolveSuccess | AttendedResolveDormant | AttendedResolveRefused
)


def _port_from_cdp_url(cdp_url: str) -> int | None:
    parsed = urlparse(cdp_url)
    if parsed.port is not None:
        return parsed.port
    if parsed.hostname in {"127.0.0.1", "localhost"} and not parsed.port:
        return None
    return None


def _holder_key(lane: Any) -> str:
    """Claim-only lane key for holder collapse — registry ``parent_thread``, not bus proof."""
    thread = str(getattr(lane, "parent_thread", None) or "").strip()
    if thread:
        return f"lane:{thread}"
    return f"unbound:{getattr(lane, 'registration_id', '')}"


def _is_better_holder(new: Any, old: Any) -> bool:
    """Prefer hop successor, else later ``started_at``."""
    new_kind = str(getattr(new, "mission_kind", None) or "")
    old_kind = str(getattr(old, "mission_kind", None) or "")
    if new_kind == "hop" and old_kind != "hop":
        return True
    if old_kind == "hop" and new_kind != "hop":
        return False
    return float(getattr(new, "started_at", 0) or 0) >= float(
        getattr(old, "started_at", 0) or 0
    )


def _mission_candidates() -> tuple[list[AttendedCandidate], int]:
    """Build purpose-filtered registry candidates collapsed to one holder per lane."""
    lanes = list(cdp_registry.list_active())
    purpose_filtered = [
        lane
        for lane in lanes
        if (lane.purpose or "").strip() in OPERATOR_PROXY_MISSION_PURPOSES
    ]
    holders: dict[str, Any] = {}
    for lane in purpose_filtered:
        chat_url = cdp_registry.chat_url_for_registration(lane.registration_id)
        if not chat_url:
            continue
        key = _holder_key(lane)
        prev = holders.get(key)
        if prev is None or _is_better_holder(lane, prev[0]):
            holders[key] = (lane, chat_url)
    candidates: list[AttendedCandidate] = []
    for lane, chat_url in holders.values():
        purpose = (lane.purpose or "").strip()
        candidates.append(
            AttendedCandidate(
                registration_id=lane.registration_id,
                cdp_url=lane.cdp_url,
                chat_url=chat_url,
                purpose=purpose,
                provenance=resolve_provenance(
                    registration_id=lane.registration_id,
                    host_listable=is_host_listable,
                ),
            )
        )
    return candidates, len(purpose_filtered)


def _probe_liveness(cdp_url: str, chat_url: str) -> LivenessProbe:
    """Return liveness for *chat_url* on the registered *cdp_url* port only."""
    checked_at = time.time()
    target_norm = normalize_cse_url(chat_url)
    port = _port_from_cdp_url(cdp_url)
    if port is None:
        return LivenessProbe(live=False, checked_at=checked_at)

    for live in cdp_orphans.probe_live_ports():
        if live.port != port:
            continue
        page_urls = live.page_urls
        for url in page_urls:
            if normalize_cse_url(url) == target_norm:
                return LivenessProbe(live=True, checked_at=checked_at)
        return LivenessProbe(
            live=False,
            checked_at=checked_at,
            page_urls_seen=tuple(page_urls),
        )
    return LivenessProbe(live=False, checked_at=checked_at)


def build_shadow_urls(
    candidates: list[AttendedCandidate],
    *,
    live_ports: list[cdp_orphans.LivePort] | None = None,
) -> list[dict[str, Any]]:
    """Diagnostic: same ``cse_`` URL on ports other than registered candidate ports."""
    registered_ports = {_port_from_cdp_url(c.cdp_url) for c in candidates}
    registered_ports.discard(None)
    registered_chat_urls = {normalize_cse_url(c.chat_url) for c in candidates}

    ports = live_ports if live_ports is not None else cdp_orphans.probe_live_ports()
    by_url: dict[str, set[int]] = {}
    for live in ports:
        for url in live.page_urls:
            if _CSE_MARKER not in url:
                continue
            norm = normalize_cse_url(url)
            if norm in registered_chat_urls and live.port not in registered_ports:
                by_url.setdefault(norm, set()).add(live.port)
            elif norm not in registered_chat_urls and _CSE_MARKER in url:
                if live.port not in registered_ports:
                    by_url.setdefault(norm, set()).add(live.port)

    return [
        {
            "chat_url": url,
            "ports_seen": sorted(ports_seen),
            "provenance": resolve_provenance(
                chat_url=url,
                host_listable=is_host_listable,
            ),
        }
        for url, ports_seen in sorted(by_url.items())
    ]


def _candidate_dict(c: AttendedCandidate) -> dict[str, Any]:
    return {
        "registration_id": c.registration_id,
        "cdp_url": c.cdp_url,
        "chat_url": c.chat_url,
        "purpose": c.purpose,
        "provenance": c.provenance,
    }


def _probe_dict(probe: LivenessProbe) -> dict[str, Any]:
    out: dict[str, Any] = {"live": probe.live, "checked_at": probe.checked_at}
    if probe.page_urls_seen:
        out["page_urls_seen"] = list(probe.page_urls_seen)
    return out


def _resolve_dormant(shadows: list[dict[str, Any]]) -> AttendedResolveOutcome | None:
    """Resolve attendance from dormant seats when no host is live; None when empty."""
    dormant = dormant_candidates()
    if not dormant:
        return None
    if len(dormant) > 1:
        return AttendedResolveRefused(
            code="ambiguous_attended",
            candidates=[dormant_candidate_dict(c) for c in dormant],
            shadow_urls=shadows,
        )
    sole = dormant[0]
    return AttendedResolveDormant(
        registration_id=sole.registration_id,
        chat_url=sole.chat_url,
        purpose=sole.purpose,
        source=_SOURCE,
        shadow_urls=shadows,
        dormant_at=sole.dormant_at,
        provenance=sole.provenance,
    )


def _conflict_for_url(
    chat_url: str,
    conflicts: list[dict[str, Any]],
) -> dict[str, Any] | None:
    target = normalize_cse_url(chat_url)
    for entry in conflicts:
        if normalize_cse_url(entry.get("chat_url") or "") == target:
            return entry
    return None


def resolve_attended_operator() -> AttendedResolveOutcome:
    """Resolve the attended mission-operator CSE — live, dormant, or refused.

    The result preserves registry provenance, liveness evidence, and shadow
    observations so callers cannot turn an ambiguous page scan into a bind. A
    released host is reported dormant rather than absent: the CSE URL is the
    durable identity, and an open tab is not what makes a seat attended.
    """
    url_conflicts = purpose_filtered_url_conflicts()
    candidates, purpose_filtered_count = _mission_candidates()
    if len(candidates) == 1:
        shared = shared_url_candidates(candidates[0].chat_url)
        conflict = _conflict_for_url(candidates[0].chat_url, url_conflicts)
        if shared is not None and len(shared) >= 2:
            live_ports = cdp_orphans.probe_live_ports()
            shadows = build_shadow_urls(candidates, live_ports=live_ports)
            return AttendedResolveRefused(
                code="ambiguous_attended",
                candidates=shared,
                shadow_urls=shadows,
                conflict=conflict,
            )

    live_ports = cdp_orphans.probe_live_ports()
    shadows = build_shadow_urls(candidates, live_ports=live_ports)

    if len(candidates) == 0:
        dormant = _resolve_dormant(shadows)
        if dormant is not None:
            return dormant
        return AttendedResolveRefused(
            code="no_attended_cse",
            candidates_considered=purpose_filtered_count,
            shadow_urls=shadows,
        )

    if len(candidates) > 1:
        conflict = None
        if candidates:
            conflict = _conflict_for_url(candidates[0].chat_url, url_conflicts)
        return AttendedResolveRefused(
            code="ambiguous_attended",
            candidates=[_candidate_dict(c) for c in candidates],
            shadow_urls=shadows,
            conflict=conflict,
        )

    sole = candidates[0]
    probe = _probe_liveness(sole.cdp_url, sole.chat_url)
    if not probe.live:
        return AttendedResolveRefused(
            code="attended_liveness_failed",
            candidate=_candidate_dict(sole),
            probe=probe,
            shadow_urls=shadows,
        )

    return AttendedResolveSuccess(
        registration_id=sole.registration_id,
        cdp_url=sole.cdp_url,
        chat_url=sole.chat_url,
        purpose=sole.purpose,
        probe=probe,
        source=_SOURCE,
        shadow_urls=shadows,
        provenance=sole.provenance,
        conflict=_conflict_for_url(sole.chat_url, url_conflicts),
    )


def success_to_http_body(outcome: AttendedResolveSuccess) -> dict[str, Any]:
    """Serialize a success with liveness and complete registry provenance evidence."""
    body: dict[str, Any] = {
        "registration_id": outcome.registration_id,
        "cdp_url": outcome.cdp_url,
        "chat_url": outcome.chat_url,
        "purpose": outcome.purpose,
        "probe": _probe_dict(outcome.probe),
        "source": outcome.source,
        "shadow_urls": outcome.shadow_urls,
        "provenance": outcome.provenance,
    }
    if outcome.conflict is not None:
        body["conflict"] = outcome.conflict
    return body


def dormant_to_http_body(outcome: AttendedResolveDormant) -> dict[str, Any]:
    """Serialize a dormant seat: attended, not live, reopenable by ``chat_url``.

    ``cdp_url`` is null on purpose — a caller must relaunch to obtain a port
    instead of reusing one that another host may now own.
    """
    return {
        "registration_id": outcome.registration_id,
        "cdp_url": None,
        "chat_url": outcome.chat_url,
        "purpose": outcome.purpose,
        "probe": {"live": False, "checked_at": time.time()},
        "dormant": True,
        "reattachable": outcome.reattachable,
        "dormant_at": outcome.dormant_at,
        "source": outcome.source,
        "shadow_urls": outcome.shadow_urls,
        "provenance": outcome.provenance,
    }


def refused_to_http_body(outcome: AttendedResolveRefused) -> dict[str, Any]:
    """Serialize a refusal with typed state, candidate, and shadow evidence."""
    body: dict[str, Any] = {"code": outcome.code, "shadow_urls": outcome.shadow_urls or []}
    if outcome.candidates_considered:
        body["candidates_considered"] = outcome.candidates_considered
    if outcome.candidates is not None:
        body["candidates"] = outcome.candidates
    if outcome.candidate is not None:
        body["candidate"] = outcome.candidate
    if outcome.probe is not None:
        body["probe"] = _probe_dict(outcome.probe)
    if outcome.conflict is not None:
        body["conflict"] = outcome.conflict
    return body


def refused_http_status(code: str) -> int:
    """Map a typed refusal code to its stable HTTP status."""
    return {
        "no_attended_cse": 404,
        "ambiguous_attended": 409,
        "attended_liveness_failed": 424,
    }[code]
