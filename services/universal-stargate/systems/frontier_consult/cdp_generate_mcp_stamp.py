"""CDP generate MCP default stamping — parity with handoff enrich.

Handoff ``enrich_handoff_packet`` stamps ``LIFE/CORTEX MCP: ON`` and
``CODE/VORTEX MCP: OFF`` on life/web receivers when Block 5 lacks an explicit
split. CDP generate must apply the same contract before
``stage_cdp_prompt_with_skills`` seals the prompt (friction a:32088).
"""

from __future__ import annotations

from dataclasses import dataclass

from claude_bundles.cdp_model_endpoint import CDP_REPLY_FROM
from claude_bundles.cdp_model_endpoint_staging import CdpStagingError, read_prompt_text

from .handoff_packet_enrich import _packet_tag_body, _replace_packet_tag_body
from .handoff_web_mcp_default import apply_web_mcp_default


@dataclass(frozen=True, slots=True)
class CdpMcpStampResult:
    """Outcome of optional MCP default stamping before CDP prompt staging."""

    body: str | None
    stamped: bool
    source_label: str | None


def stamp_cdp_packet_mcp_default(
    *,
    prompt_text: str | None = None,
    prompt_uri: str | None = None,
    packet_path: str | None = None,
    sidecar_ref: str | None = None,
) -> CdpMcpStampResult:
    """Stamp Block 5 life-on/code-off when the packet lacks an explicit split.

    Returns ``body`` only when stamping changed bytes — callers should pass it as
    ``prompt_text`` and omit path/uri inputs so staging does not re-read unstamped
    sources.
    """
    try:
        raw = read_prompt_text(
            prompt_text=prompt_text,
            prompt_uri=prompt_uri,
            packet_path=packet_path,
            sidecar_ref=sidecar_ref,
        )
    except CdpStagingError:
        return CdpMcpStampResult(body=None, stamped=False, source_label=None)

    stamped_text, changed = apply_web_mcp_default(
        raw,
        to_agent=CDP_REPLY_FROM,
        current_body=_packet_tag_body(raw, "mcp_capabilities"),
        replace_body=_replace_packet_tag_body,
    )
    if not changed:
        return CdpMcpStampResult(body=None, stamped=False, source_label=None)

    label = _prompt_source_label(
        prompt_text=prompt_text,
        prompt_uri=prompt_uri,
        packet_path=packet_path,
        sidecar_ref=sidecar_ref,
    )
    return CdpMcpStampResult(body=stamped_text, stamped=True, source_label=label)


def _prompt_source_label(
    *,
    prompt_text: str | None,
    prompt_uri: str | None,
    packet_path: str | None,
    sidecar_ref: str | None,
) -> str | None:
    if prompt_text is not None and str(prompt_text).strip():
        return "prompt_text"
    for label, candidate in (
        ("prompt_uri", prompt_uri),
        ("sidecar_ref", sidecar_ref),
        ("packet_path", packet_path),
    ):
        if candidate and str(candidate).strip():
            return f"{label}:{str(candidate).strip()}"
    return None


def publish_cdp_packet_enriched(
    *,
    request_id: str,
    source_label: str | None,
    web_mcp_stamped: bool,
) -> None:
    """Emit observability when CDP generate stamps Block 5 MCP defaults."""
    if not web_mcp_stamped:
        return
    from .events import FrontierCdpPacketEnriched

    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is not None:
            event_bus.publish_from_sync(
                FrontierCdpPacketEnriched(
                    request_id=request_id,
                    packet_path=source_label or "prompt_text",
                    to_agent=CDP_REPLY_FROM,
                    web_mcp_stamped=True,
                )
            )
    except Exception:
        return
