"""Light-bounded packet AC observer — default independent review resolution."""

from __future__ import annotations

from typing import Literal

from universal_logging import get_logger

from .packet_review_surface import (
    FOOTER_BY_SURFACE,
    NEGATIVE_SPACE_BY_SURFACE,
    classify_review_surface,
    has_durable_deliverable_and_halt,
    packet_has_production_files_expected,
)

logger = get_logger(__name__)

_PRODUCTION_CODE_HINTS = ("services/", "libs/")
_PATH_SIM_ADMIT_GATE = "path-sim-admit-gate"
GENERATE_LANE_AC_OBSERVER_FOOTER = FOOTER_BY_SURFACE["source"]

Contract = Literal["light-bounded", "pure-mechanical", "implement"]


def _instruction_text(
    *,
    packet_text: str | None,
    message_text: str | None,
    packet_path: str | None,
) -> str | None:
    if packet_text is not None and packet_text.strip():
        return packet_text
    if message_text is not None and message_text.strip():
        return message_text
    if packet_path:
        return packet_path
    return None


def instruction_mentions_production_code(
    text: str | None,
    packet_path: str | None,
) -> bool:
    for candidate in (text, packet_path):
        if not candidate:
            continue
        if any(hint in candidate for hint in _PRODUCTION_CODE_HINTS):
            return True
    return False


def read_lb_packet_text(packet_path: str) -> str | None:
    from .handoff import _resolve_packet_file, _workspaces_root

    packet_file = _resolve_packet_file(_workspaces_root().resolve(), packet_path)
    if packet_file is None:
        return None
    return packet_file.read_text(encoding="utf-8", errors="replace")


def resolve_auto_review_child(
    *,
    contract: str,
    auto_review_child: bool | None,
    packet_text: str | None = None,
    message_text: str | None = None,
    packet_path: str | None = None,
) -> tuple[bool, bool]:
    """Return (effective auto_review_child, defaulted).

    Source order for the predicate: packet body → message → packet_path.

    ``None`` means the caller expressed no preference and is the only state the
    light-bounded production-code default may fill. An explicit ``False`` is a
    caller opt-out and wins over that default: a caller-supplied value that
    silently loses to a server default is indistinguishable from the flag not
    working at all, and leaves the opt-out lane unreachable.
    """
    requested = bool(auto_review_child)
    if contract != "light-bounded":
        return requested, False
    if auto_review_child is False:
        return False, False

    instruction = _instruction_text(
        packet_text=packet_text,
        message_text=message_text,
        packet_path=packet_path,
    )
    if not instruction_mentions_production_code(instruction, packet_path):
        return requested, False

    if requested:
        return True, False
    return True, True


def prepare_lb_auto_review_for_generate(
    *,
    contract: Contract,
    auto_review_child: bool | None,
    packet_path: str | None,
    message_text: str | None,
) -> tuple[bool, bool, str | None]:
    """Resolve effective review flag and optional early packet body for generate."""
    early_packet_text: str | None = None
    if contract == "light-bounded" and packet_path is not None:
        early_packet_text = read_lb_packet_text(packet_path)
    effective, defaulted = resolve_auto_review_child(
        contract=contract,
        auto_review_child=auto_review_child,
        packet_text=early_packet_text,
        message_text=message_text,
        packet_path=packet_path,
    )
    return effective, defaulted, early_packet_text


def normalize_dispatch_lane(lane: str | None) -> str | None:
    """Normalize path-sim thread slugs to the canonical admit-gate lane token."""
    if lane is None:
        return None
    stripped = lane.strip()
    if not stripped:
        return None
    if stripped == _PATH_SIM_ADMIT_GATE:
        return _PATH_SIM_ADMIT_GATE
    if stripped.startswith("path-sim-"):
        return _PATH_SIM_ADMIT_GATE
    return stripped


def _dispatch_lane_from_source_ref(source_ref: str | None) -> str | None:
    if not source_ref:
        return None
    try:
        from .stargate_cortex_reader import StargateCortexReader

        entity = StargateCortexReader().entity_get(source_ref, intent="full")
    except Exception:
        return None
    if not entity:
        return None
    lane = (entity.get("attributes") or {}).get("dispatch_lane")
    if isinstance(lane, str):
        return normalize_dispatch_lane(lane)
    return None


def _dispatch_lane_from_thread_slug(dispatch_thread_id: str | None) -> str | None:
    if not dispatch_thread_id:
        return None
    return normalize_dispatch_lane(dispatch_thread_id)


def resolve_dispatch_lane_for_generate(
    *,
    dispatch_lane: str | None,
    source_ref: str | None,
    dispatch_thread_id: str | None,
) -> str | None:
    """Resolve dispatch_lane from param, source_ref todo attrs, or thread slug."""
    if dispatch_lane:
        normalized = normalize_dispatch_lane(dispatch_lane)
        if normalized:
            return normalized
    from_ref = _dispatch_lane_from_source_ref(source_ref)
    if from_ref:
        return from_ref
    return _dispatch_lane_from_thread_slug(dispatch_thread_id)


def planning_review_spawn_suppressed(
    *,
    contract: str,
    review_surface: str | None,
    dispatch_lane: str | None,
    packet_text: str | None,
) -> bool:
    """Return True when generate-lane AC observer spawn should be suppressed."""
    if contract != "light-bounded":
        return False
    if packet_has_production_files_expected(packet_text):
        return False
    if review_surface == "sidecar":
        return True
    if dispatch_lane == _PATH_SIM_ADMIT_GATE and has_durable_deliverable_and_halt(
        packet_text
    ):
        return True
    return False


def stamp_lb_review_spawn_fields(
    *,
    contract: Contract,
    early_packet_text: str | None,
    dispatch_lane: str | None,
    source_ref: str | None,
    dispatch_thread_id: str | None,
    request_id: str | None = None,
) -> tuple[str | None, str | None, bool]:
    """Persist review_surface, dispatch_lane, and suppress_review_spawn at prepare."""
    if contract != "light-bounded":
        return None, None, False

    resolved_lane = resolve_dispatch_lane_for_generate(
        dispatch_lane=dispatch_lane,
        source_ref=source_ref,
        dispatch_thread_id=dispatch_thread_id,
    )
    if early_packet_text and early_packet_text.strip():
        review_surface = classify_review_surface(early_packet_text)
    else:
        review_surface = "unknown"
        logger.warning(
            "lb review spawn: review_surface unknown at prepare "
            "(request_id=%s execution_lane=%s)",
            request_id,
            resolved_lane,
        )
    suppress = planning_review_spawn_suppressed(
        contract=contract,
        review_surface=review_surface,
        dispatch_lane=resolved_lane,
        packet_text=early_packet_text,
    )
    return review_surface, resolved_lane, suppress


def validate_generate_contract_packet_rules(
    *,
    request_id: str,
    contract: Contract,
    packet_path: str | None,
    read_only: bool,
) -> None:
    from .admission import FrontierEndpointError

    if contract == "implement" and packet_path is None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason="contract=implement requires packet_path",
            status_code=422,
        )
    if read_only and contract == "implement":
        raise FrontierEndpointError(
            request_id=request_id,
            field="read_only",
            reason="read_only=true is incompatible with contract=implement",
            status_code=422,
        )
    if contract == "pure-mechanical" and packet_path is not None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=(
                "contract=pure-mechanical is packet-free; use contract=implement "
                "or light-bounded for packet-based dispatches"
            ),
            status_code=422,
        )


def build_generate_lane_reviewer_prompt(
    *,
    packet_text: str | None,
    staged_draft_body: str,
    reasoning_trace_body: str,
) -> str:
    """Build generate-lane reviewer prompt with surface-specific rubric."""
    from .densify_candidate_ready import build_reviewer_prompt

    surface = classify_review_surface(packet_text or "")
    prompt = build_reviewer_prompt(
        staged_draft_body=staged_draft_body,
        reasoning_trace_body=reasoning_trace_body,
        negative_space=NEGATIVE_SPACE_BY_SURFACE[surface],
    )
    footer = FOOTER_BY_SURFACE[surface]
    return f"{prompt}\n\n{footer}"
