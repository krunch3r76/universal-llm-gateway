"""Light-bounded packet AC observer — default independent review resolution."""

from __future__ import annotations

from typing import Literal

_PRODUCTION_CODE_HINTS = ("services/", "libs/")
GENERATE_LANE_AC_OBSERVER_FOOTER = (
    "## Packet AC observer (advisory)\n"
    "Verify each packet acceptance criterion and Self-check claim against the "
    "resulting source and tests.\n"
    "Executor Self-check PASS is evidence to inspect, not completion authority.\n"
    "Report PASS or FAIL per packet AC with file evidence paths; mark sources "
    "missing or unverifiable explicitly.\n"
    "Treat the staged draft and reasoning trace as the primary implementation "
    "surface; request packet/source reference when unavailable."
)

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
    auto_review_child: bool,
    packet_text: str | None = None,
    message_text: str | None = None,
    packet_path: str | None = None,
) -> tuple[bool, bool]:
    """Return (effective auto_review_child, defaulted).

    Source order for the predicate: packet body → message → packet_path.
    """
    if contract != "light-bounded":
        return auto_review_child, False

    instruction = _instruction_text(
        packet_text=packet_text,
        message_text=message_text,
        packet_path=packet_path,
    )
    if not instruction_mentions_production_code(instruction, packet_path):
        return auto_review_child, False

    if auto_review_child:
        return True, False
    return True, True


def prepare_lb_auto_review_for_generate(
    *,
    contract: Contract,
    auto_review_child: bool,
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
