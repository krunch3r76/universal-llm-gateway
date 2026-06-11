"""Handoff thread creation for POST /api/v1/team/handoff.

Separates the pointer-body construction and agent-bus thread creation from
route.py (thin layer) and service.py (generate-path orchestrator).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from agent_seat.profiles import load_profiles
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .admission import FrontierEndpointError

logger = get_logger(__name__)

_POINTER_MAX_LINES = 25


@dataclass(frozen=True, slots=True)
class ValidatePacketResult:
    warnings: list[str]
    frontmatter_source_ref: str | None = None


# Packet admission lint (incident threads 1296/1297 — non-conformant packets
# accepted because handoff did not validate on-disk structure). Canonical
# block contract: project .cursor/rules/architecture-handoff-protocol.mdc
# § "The Six Required Blocks". Tags are case-sensitive canonical XML.
_REQUIRED_PACKET_TAGS: tuple[str, ...] = (
    "<scope>",
    "<invariants>",
    "<task_guidance>",
    "<corpus>",
    "<output_format>",
)
# MCP-capable manual seats must additionally carry <mcp_capabilities> — they
# investigate via tools, not the inlined corpus alone (Block 5).
_MCP_CAPABILITIES_TAG = "<mcp_capabilities>"


def _mcp_packet_seats() -> frozenset[str]:
    """Manual handoff seats with an MCP tool surface — packets to these seats
    must carry <mcp_capabilities> (they investigate via tools)."""
    return frozenset(
        f"{family}-{platform}"
        for (family, platform), profile in load_profiles().items()
        if profile.manual_handoff
        and profile.tool_surface == "mcp"
    )


_PROTOCOL_HINT = (
    "Author per project .cursor/rules/architecture-handoff-protocol.mdc "
    "§ The Six Required Blocks (skeleton: "
    "docs/agent-guides/skills/handoff-packet-authoring.md)."
)
# MCP ``fs(workspaces)`` paths are workspaces-relative; Stargate may set
# ``PROJECT_ROOT`` to either ``/mnt/torus/projects`` or the ULG repo root.
_ULG_REPO_DIRNAME = "universal-llm-gateway"


def _workspaces_root() -> Path:
    """Sandbox root for packet resolution.

    ``PROJECT_ROOT`` on Stargate is often the ULG repo checkout, while callers
    still pass MCP-style paths prefixed with ``universal-llm-gateway/``.
    """
    return Path(os.environ.get("PROJECT_ROOT") or "/mnt/torus/projects")


def _path_contained_in(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _packet_path_variants(packet_path: str) -> tuple[str, ...]:
    """Workspaces-relative and repo-relative spellings of the same packet."""
    rel = packet_path.lstrip("/")
    prefix = f"{_ULG_REPO_DIRNAME}/"
    if rel.startswith(prefix):
        return (rel, rel[len(prefix) :])
    return (rel,)


def _resolve_packet_file(root: Path, packet_path: str) -> Path | None:
    """Resolve *packet_path* to an on-disk file under *root*, or None."""
    root = root.resolve()
    for variant in _packet_path_variants(packet_path):
        candidate = (root / variant).resolve()
        if not _path_contained_in(candidate, root):
            continue
        if candidate.is_file():
            return candidate
    return None


def validate_packet(
    *,
    request_id: str,
    packet_path: str,
    to_agent: str,
    handoff_contract: str,
    workspaces_root: Path | None = None,
    source_ref: str | None = None,
) -> ValidatePacketResult:
    """Reject a handoff whose on-disk packet is missing or non-conformant.

    Admission predicate (FOL):
      admit(packet) ⟺ file_exists(packet)
                      ∧ _REQUIRED_PACKET_TAGS ⊆ tags(packet)
                      ∧ (to_agent ∈ _mcp_packet_seats() ⟹ <mcp_capabilities> ∈ packet)
                      ∧ (handoff_contract == "implement" ⟹ acceptance ∈ <task_guidance>)

    Every rejection cites the missing element(s) and the canonical protocol
    paths — never a bare 422. ``workspaces_root`` is injectable for tests.
    """
    warnings: list[str] = []
    root = (workspaces_root or _workspaces_root()).resolve()
    for variant in _packet_path_variants(packet_path):
        probe = (root / variant).resolve()
        if not _path_contained_in(probe, root):
            raise FrontierEndpointError(
                request_id=request_id,
                field="packet_path",
                reason=(
                    f"packet_path {packet_path!r} resolves outside the workspaces "
                    "sandbox; traversal rejected"
                ),
                status_code=422,
                code="handoff_packet_invalid",
            )

    candidate = _resolve_packet_file(root, packet_path)
    if candidate is None:
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=(
                f"Packet file not found at workspaces path {packet_path!r}. "
                f"Write the six-block packet before calling handoff. {_PROTOCOL_HINT}"
            ),
            status_code=422,
            code="handoff_packet_missing",
        )

    text = candidate.read_text(encoding="utf-8", errors="replace")

    missing = [tag for tag in _REQUIRED_PACKET_TAGS if tag not in text]
    if to_agent in _mcp_packet_seats() and _MCP_CAPABILITIES_TAG not in text:
        missing.append(_MCP_CAPABILITIES_TAG)
    if missing:
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=(
                f"Packet {packet_path!r} missing required block(s): "
                f"{', '.join(missing)}. {_PROTOCOL_HINT}"
            ),
            status_code=422,
            code="handoff_packet_invalid",
        )

    if handoff_contract == "implement":
        guidance = _extract_block(text, "task_guidance")
        haystack = guidance if guidance is not None else text
        if "acceptance" not in haystack.lower():
            raise FrontierEndpointError(
                request_id=request_id,
                field="packet_path",
                reason=(
                    f"implement handoff packet {packet_path!r} has no acceptance "
                    "criteria in <task_guidance> (expected the word 'acceptance' "
                    f"or an <acceptance ...> tag). {_PROTOCOL_HINT}"
                ),
                status_code=422,
                code="handoff_packet_missing_acceptance",
            )

        from implement_admission.admission_read import frontmatter_value
        from implement_admission.drift_gates import (
            check_bound_source_ref,
            check_frontmatter_source_ref,
        )
        from implement_admission.source_ref import SourceRefError, parse_source_ref

        fm_ref = frontmatter_value(text, "source_ref")
        if fm_ref is not None:
            try:
                parse_source_ref(fm_ref)
            except SourceRefError:
                fm_ref = None

        gate_a = check_bound_source_ref(
            source_ref=source_ref,
            packet_frontmatter_source_ref=fm_ref,
        )
        if gate_a.action == "reject":
            raise FrontierEndpointError(
                request_id=request_id,
                field="source_ref",
                reason=(
                    "implement handoff requires a bound source_ref per unified "
                    f"admission §8 wire. {_PROTOCOL_HINT}"
                ),
                status_code=422,
                code="handoff_missing_source_ref",
            )

        gate_a2 = check_frontmatter_source_ref(packet_frontmatter_source_ref=fm_ref)
        if gate_a2.action == "reject":
            raise FrontierEndpointError(
                request_id=request_id,
                field="packet_path",
                reason=(
                    "implement handoff packet must declare source_ref in frontmatter "
                    f"per unified admission §8. {_PROTOCOL_HINT}"
                ),
                status_code=422,
                code="handoff_packet_missing_source_ref",
            )
        if gate_a2.action == "warn":
            warnings.append("drift_gate.a2.miss: packet frontmatter lacks source_ref")

        return ValidatePacketResult(warnings=warnings, frontmatter_source_ref=fm_ref)

    return ValidatePacketResult(warnings=warnings)


def check_contract_ambiguity(
    *,
    request_id: str,
    packet_path: str,
    contract_source: str,
    workspaces_root: Path | None = None,
) -> None:
    """Reject default consult when packet task_guidance carries acceptance criteria."""
    if contract_source != "default":
        return

    root = (workspaces_root or _workspaces_root()).resolve()
    candidate = _resolve_packet_file(root, packet_path)
    if candidate is None:
        return

    text = candidate.read_text(encoding="utf-8", errors="replace")
    guidance = _extract_block(text, "task_guidance")
    haystack = guidance if guidance is not None else text
    if "acceptance" not in haystack.lower():
        return

    raise FrontierEndpointError(
        request_id=request_id,
        field="contract",
        reason=(
            "packet has acceptance criteria but no explicit contract — pass "
            "contract=implement or add `contract:` front-matter. "
            f"{_PROTOCOL_HINT}"
        ),
        status_code=422,
        code="handoff_contract_ambiguous",
    )


def _extract_block(text: str, tag: str) -> str | None:
    """Return the inner body of ``<tag>…</tag>`` (case-sensitive), or None."""
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, flags=re.DOTALL)
    return match.group(1) if match else None


_POINTER_TEMPLATE = """\
{subject}

Read the packet:
  fs(sandbox="workspaces", op="read", path="{packet_path}")

The packet contains all six required blocks:
  <scope>, <invariants>, <task_guidance>, <mcp_capabilities>,
  <output_format>, <corpus>

Reply on this thread with findings. Use <need> only as last resort."""

# One-line contract annotation appended to the default pointer template after
# the subject. Skipped when the caller overrides pointer_body.
_CONTRACT_LINES: dict[str, str] = {
    "consult": (
        "Contract: consult — review, revise, expand, or dialectic; return "
        "findings, risks, and recommendations."
    ),
    "implement": (
        "Contract: bound implementation — follow packet acceptance criteria "
        "and quality gates."
    ),
}


def build_pointer_body(
    *,
    request_id: str,
    packet_path: str,
    subject: str,
    pointer_body: str | None,
    handoff_contract: str,
    materialization_fallback: bool = False,
) -> str:
    """Return the bus turn body.

    Uses caller override if given, else the standard handoff-dispatchers.mdc
    pointer template with a one-line ``Contract:`` annotation derived from
    ``handoff_contract``. Enforces ≤ _POINTER_MAX_LINES lines on the final body
    regardless of which path produced it.
    """
    if pointer_body is not None:
        body = pointer_body
    else:
        contract_line = _CONTRACT_LINES.get(handoff_contract, "")
        body = _POINTER_TEMPLATE.format(
            subject=f"{subject}\n{contract_line}" if contract_line else subject,
            packet_path=packet_path,
        )
        if materialization_fallback:
            body += (
                "\n\nFallback: re-read via source_ref frontmatter if packet "
                "absent locally."
            )
    lines = body.splitlines()
    if len(lines) > _POINTER_MAX_LINES:
        raise FrontierEndpointError(
            request_id=request_id,
            field="pointer_body",
            reason=(
                "pointer body exceeds 25 lines; agent-bus is a table of "
                "contents, not a content carrier"
            ),
            status_code=422,
        )
    return body


def _slug_from_subject(subject: str) -> str:
    """Derive a kebab slug from a human subject string."""
    slug = subject.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug[:50].strip("-") or "handoff"


async def create_handoff_thread(
    *,
    request_id: str,
    to_agent: str,
    subject: str,
    pointer_body: str,
    caller_agent: str | None,
    tags: list[str] | None,
    handoff_contract: str,
) -> str:
    """POST to agent-bus /threads/with-turn; return thread_id.

    Token handling mirrors service.py lines 198–222: require AGENT_BUS_TOKEN
    (or ALLOW_UNSET_AGENT_BUS_TOKEN=true local bypass); if absent and no bypass,
    raise FrontierEndpointError(field="thread", status_code=503).
    Transport errors are translated to 502/503 FrontierEndpointError.
    """
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=(
                "AGENT_BUS_TOKEN not configured; handoff requires agent-bus access. "
                "Set AGENT_BUS_TOKEN in the Stargate environment, or "
                "ALLOW_UNSET_AGENT_BUS_TOKEN=true for explicit local bypass."
            ),
            status_code=503,
        )

    slug = _slug_from_subject(subject)
    from_agent = caller_agent or "dispatch"
    contract_tag = f"contract:{handoff_contract}"
    if tags is None:
        effective_tags: list[str] = [
            f"agent:{to_agent}",
            "type:handoff",
            contract_tag,
        ]
    else:
        # Append the contract tag to caller-supplied tags (do not replace them).
        effective_tags = list(tags)
        if contract_tag not in effective_tags:
            effective_tags.append(contract_tag)

    payload: dict[str, Any] = {
        "slug": slug,
        "from": from_agent,
        "to": to_agent,
        "subject": subject,
        "body": pointer_body,
        "status": "open",
        "after_turn": 0,
        "tags": effective_tags,
    }

    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.post(
                "/threads/with-turn", headers=headers, json=payload
            )
    except httpx.HTTPError as exc:
        logger.error(
            "handoff agent-bus transport error: request_id=%s error=%s",
            request_id,
            exc,
        )
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Agent-bus unreachable: {exc}",
            status_code=503,
        ) from exc

    if resp.status_code >= 400:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=(
                f"Agent-bus thread creation failed: HTTP {resp.status_code} — "
                f"{resp.text[:200]}"
            ),
            status_code=502,
        )

    result: dict[str, Any] = resp.json()
    try:
        return str(result["thread"]["id"])
    except (KeyError, TypeError) as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Agent-bus 2xx response malformed: {exc}",
            status_code=502,
        ) from exc
