"""Identity resolution ladder for warm CSE followup on attached CDP lanes.

Reads ``cdp_registry`` snapshots only — never registers lanes, opens profiles,
or navigates to CSE URLs. Fail-closed typed errors; ``ambiguous_identity``
returns candidate rows for disambiguation. Identity omitted invokes the attended
operator resolver (``attended_operator``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from claude_bundles import cdp_registry
from claude_bundles.cse_provenance import resolve as resolve_provenance
from claude_bundles.cse_url import normalize_cse_url
from playwright.async_api import async_playwright

from cdp_ask.attended_operator import (
    AttendedResolveRefused,
    AttendedResolveSuccess,
    resolve_attended_operator,
)
from cdp_ask.execution_store import ExecutionStore
from cdp_ask.followup_events import (
    cdp_ask_attended_refused,
    cdp_ask_attended_resolve,
)
from cdp_ask.followup_events import (
    emit as emit_followup_event,
)
from cdp_ask.models import (
    FollowupCandidateInfo,
    FollowupProjectAskRequest,
    FollowupProjectAskResponse,
    TargetBinding,
)

_CLI_ESCAPE = "scripts/cortex/cowork_chat_followup.py"
_HORIZON = "v1 requires an attached lane; post-deregister reattach is horizon"
_REGISTRY_SOURCE = "cse-session-registry"


def lane_not_attached_detail() -> str:
    """Human hint for missing/detached lanes including the SSH CLI escape path."""
    return (
        f"{_HORIZON}. CLI escape: {_CLI_ESCAPE} "
        "(list-lanes → curl :port/json/list → SSH paste)."
    )


def fail_followup(
    error: str,
    *,
    detail: str | None = None,
    candidates: list[FollowupCandidateInfo] | None = None,
    send_verified: bool = False,
    **extra: Any,
) -> FollowupProjectAskResponse:
    """Build a typed ``ok=false`` followup response with optional detail fields."""
    return FollowupProjectAskResponse(
        ok=False,
        error=error,
        detail=detail,
        candidates=candidates,
        send_verified=send_verified,
        **extra,
    )


def identity_keys(
    req: FollowupProjectAskRequest,
) -> tuple[str | None, str | None, str | None, str | None]:
    """Return normalized identity keys used by the shared provenance resolver."""
    chat = (req.chat_url or "").strip() or None
    reg = (req.registration_id or "").strip() or None
    exe = (req.execution_id or "").strip() or None
    cdp = (req.cdp_url or "").strip() or None
    return chat, reg, exe, cdp


def identity_supplied(req: FollowupProjectAskRequest) -> bool:
    """True when any explicit identity or port override is present."""
    chat_url, registration_id, execution_id, cdp_url = identity_keys(req)
    return any((chat_url, registration_id, execution_id, cdp_url))


@dataclass(frozen=True)
class FollowupCandidate:
    registration_id: str
    chat_url: str
    holder: str
    purpose: str | None
    cdp_url: str
    target_binding: TargetBinding = "explicit"
    provenance: dict[str, Any] | None = None

    def as_info(self) -> FollowupCandidateInfo:
        return FollowupCandidateInfo(
            registration_id=self.registration_id,
            chat_url=self.chat_url,
            holder=self.holder,
            purpose=self.purpose,
            cdp_url=self.cdp_url,
            source=_REGISTRY_SOURCE,
            provenance=self.provenance,
        )


async def scan_lane_cse_urls(reg: cdp_registry.Registration) -> list[str]:
    """List normalized ``/cowork/cse_`` page URLs currently open on one attached lane."""
    pw = await async_playwright().start()
    try:
        browser = await pw.chromium.connect_over_cdp(reg.cdp_url)
        if not browser.contexts:
            return []
        ctx = browser.contexts[0]
        urls: list[str] = []
        for page in ctx.pages:
            url = page.url or ""
            if "/cowork/cse_" in url:
                urls.append(normalize_cse_url(url))
        return urls
    finally:
        await pw.stop()


async def resolve_execution_registration(
    execution_id: str,
    store: ExecutionStore,
) -> str | None:
    """Map a satellite execution to its registry host when the record carries one."""
    rec = await store.get(execution_id)
    if rec is None or not rec.registration_id:
        return None
    return rec.registration_id


def _registry_pairs_for_chat_url(
    chat_url: str,
    *,
    cdp_url: str | None = None,
) -> list[FollowupCandidate]:
    """Registry rows whose durable ``chat_url`` matches (optional ``cdp_url`` filter)."""
    target = normalize_cse_url(chat_url)
    out: list[FollowupCandidate] = []
    for lane in cdp_registry.list_active():
        if cdp_url and lane.cdp_url != cdp_url:
            continue
        bound = cdp_registry.chat_url_for_registration(lane.registration_id)
        if not bound or normalize_cse_url(bound) != target:
            continue
        out.append(
            FollowupCandidate(
                registration_id=lane.registration_id,
                chat_url=bound,
                holder=lane.holder,
                purpose=lane.purpose,
                cdp_url=lane.cdp_url,
                target_binding="explicit",
                provenance=resolve_provenance(
                    chat_url=bound, registration_id=lane.registration_id
                ),
            )
        )
    return out


def _refused_to_followup(
    refused: AttendedResolveRefused,
) -> FollowupProjectAskResponse:
    """Map attended resolver refusal to followup envelope."""
    extra: dict[str, Any] = {}
    if refused.candidates:
        extra["candidates"] = [
            FollowupCandidateInfo(
                registration_id=c["registration_id"],
                chat_url=c["chat_url"],
                holder="",
                purpose=c.get("purpose"),
                cdp_url=c.get("cdp_url"),
                source=_REGISTRY_SOURCE,
                provenance=c.get("provenance"),
            )
            for c in refused.candidates
        ]
    return fail_followup(refused.code, **extra)


def _emit_attended_outcome(
    outcome: AttendedResolveSuccess | AttendedResolveRefused,
) -> None:
    if isinstance(outcome, AttendedResolveSuccess):
        emit_followup_event(
            cdp_ask_attended_resolve(
                registration_id=outcome.registration_id,
                cdp_url=outcome.cdp_url,
                chat_url=outcome.chat_url,
                purpose=outcome.purpose,
                source=outcome.source,
            )
        )
        return
    emit_followup_event(
        cdp_ask_attended_refused(
            code=outcome.code,
            candidates_considered=outcome.candidates_considered or None,
            candidate_count=len(outcome.candidates) if outcome.candidates else None,
        )
    )


def _resolve_attended_binding() -> (
    tuple[FollowupCandidate | None, FollowupProjectAskResponse | None, str | None]
):
    """Identity-omitted path: attended resolver bind or refuse."""
    outcome = resolve_attended_operator()
    _emit_attended_outcome(outcome)
    if isinstance(outcome, AttendedResolveRefused):
        return None, _refused_to_followup(outcome), None
    lane = cdp_registry.list_active()
    holder = "cdp-ask-satellite"
    for reg in lane:
        if reg.registration_id == outcome.registration_id:
            holder = reg.holder
            break
    candidate = FollowupCandidate(
        registration_id=outcome.registration_id,
        chat_url=outcome.chat_url,
        holder=holder,
        purpose=outcome.purpose,
        cdp_url=outcome.cdp_url,
        target_binding="resolver",
    )
    return candidate, None, "attended_resolver"


async def discover_candidates(
    req: FollowupProjectAskRequest,
    store: ExecutionStore,
) -> tuple[list[FollowupCandidate], str | None, str | None]:
    """Discover attached targets and retain their evidence-bearing provenance for followup."""
    chat_url, registration_id, execution_id, cdp_url = identity_keys(req)
    if not any((chat_url, registration_id, execution_id)):
        return [], None, None

    mapped_reg: str | None = None
    resolution_path: str | None = None
    if execution_id and not registration_id:
        mapped_reg = await resolve_execution_registration(execution_id, store)
        if mapped_reg is None:
            return [], None, execution_id
        registration_id = mapped_reg
        resolution_path = "execution_id"

    if chat_url:
        registry_matches = _registry_pairs_for_chat_url(chat_url, cdp_url=cdp_url)
        if len(registry_matches) > 1:
            return registry_matches, "chat_url", execution_id
        if len(registry_matches) == 1:
            chosen = registry_matches[0]
            lane = next(
                (
                    r
                    for r in cdp_registry.list_active()
                    if r.registration_id == chosen.registration_id
                ),
                None,
            )
            if lane is None:
                return [], resolution_path or "chat_url", execution_id
            try:
                urls = await scan_lane_cse_urls(lane)
            except Exception:
                return [], resolution_path or "chat_url", execution_id
            if normalize_cse_url(chat_url) in urls:
                return [chosen], "chat_url", execution_id
            return [], resolution_path or "chat_url", execution_id

    lanes = list(cdp_registry.list_active())
    if cdp_url:
        lanes = [lane for lane in lanes if lane.cdp_url == cdp_url]
    if registration_id:
        scan_lanes = [lane for lane in lanes if lane.registration_id == registration_id]
        if not scan_lanes:
            return [], resolution_path or "registration_id", execution_id
        if not resolution_path:
            resolution_path = "registration_id"
    elif mapped_reg:
        scan_lanes = [lane for lane in lanes if lane.registration_id == mapped_reg]
        if not scan_lanes:
            return [], resolution_path or "execution_id", execution_id
    else:
        scan_lanes = lanes

    candidates: list[FollowupCandidate] = []
    for lane in scan_lanes:
        try:
            urls = await scan_lane_cse_urls(lane)
        except Exception:
            continue
        for url in urls:
            if chat_url and normalize_cse_url(chat_url) != url:
                continue
            if req.purpose and lane.purpose != req.purpose:
                continue
            candidates.append(
                FollowupCandidate(
                    registration_id=lane.registration_id,
                    chat_url=url,
                    holder=lane.holder,
                    purpose=lane.purpose,
                    cdp_url=lane.cdp_url,
                    target_binding="explicit",
                )
            )

    if chat_url and not resolution_path:
        resolution_path = "chat_url"
    return candidates, resolution_path, execution_id


def conflicting_keys(
    req: FollowupProjectAskRequest,
    chosen: FollowupCandidate,
    *,
    mapped_reg: str | None,
) -> bool:
    """True when supplied identity keys disagree on the chosen lane target."""
    chat_url, registration_id, execution_id, _cdp_url = identity_keys(req)
    if chat_url and registration_id and chosen.registration_id != registration_id:
        if stale_registration_id_conflict(req, chosen):
            return False
        return True
    if (
        chat_url
        and execution_id
        and mapped_reg
        and chosen.registration_id != mapped_reg
    ):
        return True
    if (
        registration_id
        and execution_id
        and mapped_reg
        and mapped_reg != registration_id
    ):
        return True
    return False


def stale_registration_id_conflict(
    req: FollowupProjectAskRequest,
    chosen: FollowupCandidate,
) -> bool:
    """True when chat_url uniquely identifies ``chosen`` but ``registration_id`` is stale.

    Waive only when exactly one registry candidate matches the supplied ``chat_url``
    **and** that candidate's ``(cdp_url, chat_url)`` is unique among active registrations.
    """
    chat_url, registration_id, execution_id, cdp_url = identity_keys(req)
    if not chat_url or not registration_id:
        return False
    if chosen.registration_id == registration_id:
        return False
    if execution_id:
        return False
    matches = _registry_pairs_for_chat_url(chat_url, cdp_url=cdp_url)
    if len(matches) != 1:
        return False
    return matches[0].registration_id == chosen.registration_id


async def resolve_followup_target(
    req: FollowupProjectAskRequest,
    store: ExecutionStore,
) -> tuple[
    FollowupCandidate | None,
    FollowupProjectAskResponse | None,
    str | None,
    TargetBinding | None,
]:
    """Resolve to a single attached CSE target or return a typed error response."""
    if not identity_supplied(req):
        target, err, path = _resolve_attended_binding()
        binding: TargetBinding | None = target.target_binding if target else None
        return target, err, path, binding

    chat_url, registration_id, execution_id, cdp_url = identity_keys(req)

    mapped_reg = None
    if execution_id and not registration_id:
        mapped_reg = await resolve_execution_registration(execution_id, store)
        if mapped_reg is None:
            return (
                None,
                fail_followup("lane_not_attached", detail=lane_not_attached_detail()),
                None,
                None,
            )

    if chat_url:
        registry_matches = _registry_pairs_for_chat_url(chat_url, cdp_url=cdp_url)
        if len(registry_matches) > 1:
            infos = [c.as_info() for c in registry_matches]
            code = "ambiguous_attended"
            return (
                None,
                fail_followup(code, candidates=infos),
                "chat_url",
                None,
            )

    candidates, resolution_path, _ = await discover_candidates(req, store)
    if not candidates:
        if chat_url:
            return None, fail_followup("cse_not_found_on_lane"), resolution_path, None
        return (
            None,
            fail_followup("lane_not_attached", detail=lane_not_attached_detail()),
            resolution_path,
            None,
        )

    if len(candidates) > 1:
        infos = [c.as_info() for c in candidates]
        code = (
            "ambiguous_attended"
            if chat_url and len(_registry_pairs_for_chat_url(chat_url, cdp_url=cdp_url)) > 1
            else "ambiguous_identity"
        )
        return (
            None,
            fail_followup(code, candidates=infos),
            resolution_path,
            None,
        )

    chosen = candidates[0]
    if conflicting_keys(req, chosen, mapped_reg=mapped_reg):
        infos = [c.as_info() for c in candidates]
        return (
            None,
            fail_followup("ambiguous_identity", candidates=infos),
            resolution_path,
            None,
        )

    binding = "explicit"
    if cdp_url and not any((chat_url, registration_id, execution_id)):
        binding = "explicit"
    return chosen, None, resolution_path or "chat_url", binding
