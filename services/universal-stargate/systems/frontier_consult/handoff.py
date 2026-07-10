"""Handoff thread creation for POST /api/v1/team/handoff.

Separates the pointer-body construction and agent-bus thread creation from
route.py (thin layer) and service.py (generate-path orchestrator).
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import httpx
from agent_seat.profiles import load_profiles
from implement_admission.scheme_resolve import (
    parse_schemed_path,
    path_escapes_sandbox,
    resolve_schemed_packet,
    resolve_schemed_packet_file,
)
from implement_admission.scheme_resolve import (
    workspaces_root as _scheme_workspaces_root,
)
from transport_utils import DEFAULT_AGENT_BUS_URL, make_async_client
from universal_logging import get_logger

from .admission import FrontierEndpointError

logger = get_logger(__name__)


class PendingShellContention(Exception):  # noqa: N818
    """Raised by claim_and_post_pointer_turn when the agent-bus returns 409
    pending_shell_contention — another dispatch already claimed the pending shell."""


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

# Block 2 skill-refs the contract marks load-bearing for MCP-seat handoffs
# (architecture-handoff-protocol.mdc § Block 2 — "Read before findings"). The
# IDE does not auto-load the architecture layer and claude-web has no auto-load
# backstop at all, so omitting these refs lets a reviewer review blind to the
# universal invariants + ULG topology/lifecycle. Required unconditionally for
# every MCP-seat handoff (decision: 2026-06-11 operator).
_ARCH_SKILL_SLUGS: tuple[str, ...] = (
    "architecture-invariants",
    "ulg-architecture",
)
_REQUIRED_INVARIANT_SKILL_REFS: tuple[str, ...] = _ARCH_SKILL_SLUGS


def _packet_references_arch_skill_slug(text: str, slug: str) -> bool:
    """Canonical slug ref — platform-trigger name, legacy path, or workspaces stub.

    A display-name *stem* deliberately does NOT satisfy the gate; it is reported
    by _nonconforming_skill_ref_hint with the exact rewrite."""
    if f"agent_skill:{slug}" in text:
        return True
    if f"`{slug}`" in text:
        return True
    if f"agent-skills/{slug}" in text:
        return True
    return f".cursor/skills/{slug}/SKILL.md" in text


def _missing_arch_skill_refs(text: str) -> list[str]:
    return [
        ref
        for slug, ref in zip(
            _ARCH_SKILL_SLUGS, _REQUIRED_INVARIANT_SKILL_REFS, strict=True
        )
        if not _packet_references_arch_skill_slug(text, slug)
    ]


def _nonconforming_skill_ref_hint(text: str, missing_refs: list[str]) -> str | None:
    """Return a precise rewrite hint when the packet references a required skill
    in a non-canonical form instead of the slug.

    A display-name path (e.g. ``agent-skills/Architecture Invariants — Universal
    Layer.md``) names the right skill but does not match the canonical slug ref
    and does not resolve on disk (friction 16958). We recognize the intent by
    matching the slug stem with any separator run (space, hyphen, em/en dash) so
    the author gets told the exact verbatim string to substitute rather than
    re-discovering it through repeated 422s.
    """
    rewrites: list[str] = []
    for ref in missing_refs:
        basename = ref.rsplit("/", 1)[-1]
        stem_pattern = r"[\s\-\u2013\u2014]+".join(
            re.escape(part) for part in basename.split("-")
        )
        if re.search(stem_pattern, text, flags=re.IGNORECASE):
            rewrites.append(f"write exactly {ref!r}")
    if not rewrites:
        return None
    return (
        "A reference that names a required skill was found in a non-canonical "
        "form (e.g. a display-name path with spaces/dashes). Skill-refs must be "
        "the on-disk slug: " + "; ".join(rewrites) + "."
    )


def _mcp_packet_seats() -> frozenset[str]:
    """Manual handoff seats with an MCP tool surface — packets to these seats
    must carry <mcp_capabilities> (they investigate via tools)."""
    return frozenset(
        f"{family}-{platform}"
        for (family, platform), profile in load_profiles().items()
        if profile.manual_handoff and profile.tool_surface == "mcp"
    )


_PROTOCOL_HINT = (
    "Author per project .cursor/rules/architecture-handoff-protocol.mdc "
    "§ The Six Required Blocks (skeleton: "
    ".cursor/skills/handoff-packet-authoring/SKILL.md)."
)


def _workspaces_root() -> Path:
    """Sandbox root for packet resolution.

    ``PROJECT_ROOT`` on Stargate is often the ULG repo checkout, while callers
    still pass MCP-style paths prefixed with ``universal-llm-gateway/``.
    """
    return _scheme_workspaces_root(None)


def _resolve_packet_file(root: Path, packet_path: str) -> Path | None:
    """Resolve *packet_path* to an on-disk file under the scheme sandbox, or None."""
    return resolve_schemed_packet_file(
        packet_path,
        workspaces_root_override=root,
    )


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
    parsed = parse_schemed_path(packet_path)
    resolution = resolve_schemed_packet(packet_path, workspaces_root_override=root)
    if path_escapes_sandbox(parsed, sandbox_root=resolution.sandbox_root):
        sandbox = "cortex" if parsed.scheme == "cortex" else "workspaces"
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=(
                f"packet_path {packet_path!r} resolves outside the {sandbox} "
                "sandbox; traversal rejected"
            ),
            status_code=422,
            code="handoff_packet_invalid",
        )

    candidate = resolution.resolved_file
    if candidate is None:
        from implement_admission.share_uri_emit import to_share_uri

        tried = to_share_uri(
            "workspaces" if parsed.scheme != "cortex" else "cortex",
            parsed.rel_path or packet_path,
        )
        raise FrontierEndpointError(
            request_id=request_id,
            field="packet_path",
            reason=(
                f"Packet file not found at Share URI {tried!r} "
                f"(input {packet_path!r}). "
                f"Write the six-block packet before calling handoff. {_PROTOCOL_HINT}"
            ),
            status_code=422,
            code="handoff_packet_missing",
        )

    text = candidate.read_text(encoding="utf-8", errors="replace")

    from .diff_text_guard import assert_packet_free_of_diff_text

    assert_packet_free_of_diff_text(
        request_id=request_id,
        packet_path=packet_path,
        text=text,
    )

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

    if to_agent in _mcp_packet_seats():
        from .handoff_packet_enrich import has_densify_floor

        # P1: the densify floor binds ALL MCP seats, not just web. Validating it
        # only for the web receiver let cursor / future MCP packets bypass the
        # floor in validate_packet — currently masked because route-enrich runs
        # first, but the validator must enforce the contract independently rather
        # than rely on enrich ordering (decision:recon-lifecycle-phase-review §P1).
        if not has_densify_floor(text):
            raise FrontierEndpointError(
                request_id=request_id,
                field="packet_path",
                reason=(
                    f"Packet {packet_path!r} missing densify floor: "
                    "require ≥1 task-class skill ref (canonical slug) and, "
                    "when related_thread_ids is set, ≥1 agent_bus(fetch) "
                    f"step per upstream thread. {_PROTOCOL_HINT}"
                ),
                status_code=422,
                code="handoff_packet_missing_densify_floor",
            )
        if to_agent != _WEB_RECEIVER_AGENT:
            missing_refs = _missing_arch_skill_refs(text)
            if missing_refs:
                reason = (
                    f"Packet {packet_path!r} missing required architecture "
                    f"skill-ref(s): {', '.join(missing_refs)}. MCP-seat handoffs "
                    "must reference the universal invariant + ULG architecture "
                    "layers (Block 2 / Block 5) so the reviewer reads them before "
                    "findings. Acceptable forms: canonical slug (`architecture-invariants`), "
                    "`agent_skill:<slug>`, legacy path ('agent-skills/<slug>'), or "
                    "workspaces stub ('.cursor/skills/<slug>/SKILL.md') — not a "
                    "non-resolving display path; a recognizable display-name stem is "
                    "reported below with its exact rewrite. "
                    "Expected slugs in details.expected_refs. "
                )
                nonconforming = _nonconforming_skill_ref_hint(text, missing_refs)
                if nonconforming:
                    reason += nonconforming + " "
                reason += _PROTOCOL_HINT
                raise FrontierEndpointError(
                    request_id=request_id,
                    field="packet_path",
                    reason=reason,
                    status_code=422,
                    code="handoff_packet_missing_arch_skillrefs",
                    details={
                        "expected_refs": list(_REQUIRED_INVARIANT_SKILL_REFS),
                        "missing_refs": missing_refs,
                    },
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

# Platform-seat skill load reminder for MCP consult handoffs (claude-cursor).
# Web uses _WEB_CONSULT_PRIMING instead (2235).
_CONSULT_ARCH_READ = (
    "Before findings: load skills named in packet <invariants> by canonical slug "
    "(platform triggers on claude-cursor). Do not fs-read agent-skills/*.md for "
    "skill bodies on platform seats."
)

_WEB_CONSULT_PRIMING = (
    "Before findings: load skills named in packet <invariants> by canonical slug "
    "(platform triggers on claude-web). agent_bus(fetch) each related_thread_ids "
    "thread per packet <mcp_capabilities>. Do not fs-read agent-skills/*.md for "
    "skill bodies on platform seats."
)

_WEB_RECEIVER_AGENT = "claude-web"


def build_pointer_body(
    *,
    request_id: str,
    packet_path: str,
    subject: str,
    pointer_body: str | None,
    handoff_contract: str,
    materialization_fallback: bool = False,
    to_agent: str | None = None,
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
        if handoff_contract == "consult":
            priming = (
                _WEB_CONSULT_PRIMING
                if to_agent == _WEB_RECEIVER_AGENT
                else _CONSULT_ARCH_READ
            )
            body += f"\n\n{priming}"
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


_GENERATE_POINTER_SUMMARY_MAX_CHARS = 160


def extract_generate_pointer_summary(prompt_text: str) -> str | None:
    """Derive a one-line summary from a dispatch prompt for pointer turns.

    Returns the first non-empty line, clipped at a word boundary to
    ``_GENERATE_POINTER_SUMMARY_MAX_CHARS``. Never returns a multi-line or
    mid-word-truncated string. Provenance aid only — the dispatch thread is
    the authoritative prompt surface.
    """
    for raw_line in prompt_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if len(line) <= _GENERATE_POINTER_SUMMARY_MAX_CHARS:
            return line
        clipped = line[:_GENERATE_POINTER_SUMMARY_MAX_CHARS]
        head, _, _ = clipped.rpartition(" ")
        return f"{head or clipped}…"
    return None


def build_generate_dispatch_pointer(
    *,
    lane: str,
    contract: str,
    dispatch_thread_id: str | None,
    correlation_id: str,
    summary: str | None = None,
) -> str:
    """Short reference envelope for op=generate result-thread turn 1.

    Deliberately omits prompt text — the dispatch thread is the authoritative
    prompt surface (friction 22100). Mirrors the packet-path pointer form in
    ``cursor_sdk_generate`` ("SDK {contract} dispatch — see packet ...").
    """
    thread_ref = dispatch_thread_id or "unknown"
    lines = [
        f"{lane} {contract} generate dispatch — prompt on dispatch thread "
        f"`{thread_ref}` (correlation `{correlation_id}`).",
        "",
        "Read full prompt: "
        f"agent_bus(get, thread={thread_ref!r}, turn_number=<latest>)",
    ]
    if summary:
        lines += ["", f"Summary: {summary}"]
    return "\n".join(lines)


def _slug_from_subject(subject: str) -> str:
    """Derive a kebab slug from a human subject string."""
    slug = subject.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", slug)
    return slug[:50].strip("-") or "handoff"


async def admit_handoff_dispatch(
    *,
    request_id: str,
    thread_id: str,
    execution_id: str,
    pipeline_id: str,
    caller_agent: str | None,
) -> bool:
    """POST dispatch-admit; return True when a dispatch-link row was persisted."""
    token = os.getenv("AGENT_BUS_TOKEN", "").strip()
    allow_unset = os.getenv("ALLOW_UNSET_AGENT_BUS_TOKEN", "").strip().lower() in (
        "1",
        "true",
        "yes",
    )
    if not token and not allow_unset:
        return False

    payload = {
        "execution_id": execution_id,
        "pipeline_id": pipeline_id,
        "caller_agent": caller_agent,
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.post(
                f"/threads/{thread_id}/dispatch-admit",
                headers=headers,
                json=payload,
            )
            if resp.status_code not in (200, 201):
                _emit_dispatch_admit_failed(
                    execution_id=execution_id,
                    thread_id=thread_id,
                    status_code=resp.status_code,
                    error_preview=resp.text[:200],
                )
                return False
            return True
    except httpx.HTTPError as exc:
        logger.error(
            "handoff dispatch-admit transport error: request_id=%s thread=%s error=%s",
            request_id,
            thread_id,
            exc,
        )
        _emit_dispatch_admit_failed(
            execution_id=execution_id,
            thread_id=thread_id,
            status_code=0,
            error_preview=str(exc)[:200],
        )
        return False


def _emit_dispatch_admit_failed(
    *,
    execution_id: str,
    thread_id: str,
    status_code: int,
    error_preview: str,
) -> None:
    from systems.pipeline.core.events.delivery import AgentBusDispatchAdmitFailed

    try:
        from systems.proxy.dependencies import get_proxy

        proxy = get_proxy()
        event_bus = getattr(proxy, "event_bus", None)
        if event_bus is not None:
            event_bus.publish_from_sync(
                AgentBusDispatchAdmitFailed(
                    execution_id=execution_id,
                    thread=thread_id,
                    status_code=status_code,
                    error_preview=error_preview,
                )
            )
    except Exception:
        return


async def post_pointer_turn(
    *,
    request_id: str,
    thread_id: str,
    to_agent: str,
    subject: str,
    pointer_body: str,
    caller_agent: str | None,
) -> int:
    """POST a pointer turn onto an EXISTING thread (reuse_thread path). POST /turns.

    Returns the created turn number from the agent-bus response.
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
                "AGENT_BUS_TOKEN not configured; "
                "reuse_thread requires agent-bus access."
            ),
            status_code=503,
        )
    payload: dict[str, Any] = {
        "thread": thread_id,
        "from": caller_agent or "dispatch",
        "to": to_agent,
        "subject": subject,
        "body": pointer_body,
        "status": "open",
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.post("/turns", headers=headers, json=payload)
    except httpx.HTTPError as exc:
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
                f"Agent-bus turn post failed: "
                f"HTTP {resp.status_code} — {resp.text[:200]}"
            ),
            status_code=502,
        )
    try:
        payload = resp.json()
        turn_number = payload.get("turn_number")
        if turn_number is not None:
            return int(turn_number)
    except (TypeError, ValueError):
        pass
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            thread_resp = await client.get(
                f"/threads/{thread_id}", headers=headers
            )
        if thread_resp.status_code == 200:
            thread_payload = thread_resp.json()
            return int(thread_payload.get("turn_count") or 0)
    except (httpx.HTTPError, TypeError, ValueError):
        pass
    return 0


async def claim_and_post_pointer_turn(
    *,
    request_id: str,
    thread_id: str,
    to_agent: str,
    subject: str,
    pointer_body: str,
    caller_agent: str | None,
    execution_id: str,
    pipeline_id: str,
) -> None:
    """POST /threads/{id}/dispatch-claim-and-post — atomic claim + pointer turn.

    Raises PendingShellContention when the agent-bus returns 409
    pending_shell_contention. Raises FrontierEndpointError on transport or
    other HTTP errors.
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
                "AGENT_BUS_TOKEN not configured; "
                "claim_and_post_pointer_turn requires agent-bus access."
            ),
            status_code=503,
        )
    payload: dict[str, Any] = {
        "execution_id": execution_id,
        "pipeline_id": pipeline_id,
        "caller_agent": caller_agent,
        "from_agent": caller_agent or "dispatch",
        "to_agent": to_agent,
        "subject": subject,
        "body": pointer_body,
    }
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    try:
        async with make_async_client(DEFAULT_AGENT_BUS_URL, timeout=10.0) as client:
            resp = await client.post(
                f"/threads/{thread_id}/dispatch-claim-and-post",
                headers=headers,
                json=payload,
            )
    except httpx.HTTPError as exc:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Agent-bus unreachable: {exc}",
            status_code=503,
        ) from exc
    if resp.status_code == 409:
        detail = resp.json().get("detail", {})
        if isinstance(detail, dict) and detail.get("error") == (
            "pending_shell_contention"
        ):
            raise PendingShellContention(str(detail.get("message", "")))
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=f"Agent-bus claim conflict: {resp.text[:200]}",
            status_code=409,
        )
    if resp.status_code >= 400:
        raise FrontierEndpointError(
            request_id=request_id,
            field="thread",
            reason=(
                f"Agent-bus claim-and-post failed: "
                f"HTTP {resp.status_code} — {resp.text[:200]}"
            ),
            status_code=502,
        )


async def create_handoff_thread(
    *,
    request_id: str,
    to_agent: str,
    tag_agent: str | None = None,
    subject: str,
    pointer_body: str,
    caller_agent: str | None,
    tags: list[str] | None,
    handoff_contract: str,
    lifecycle_state: str | None = None,
    bus_lifecycle: Literal["persistent", "ephemeral"] | None = None,
) -> str:
    """POST to agent-bus /threads/with-turn; return thread_id.

    Token handling mirrors service.py lines 198–222: require AGENT_BUS_TOKEN
    (or ALLOW_UNSET_AGENT_BUS_TOKEN=true local bypass); if absent and no bypass,
    raise FrontierEndpointError(field="thread", status_code=503).
    Transport errors are translated to 502/503 FrontierEndpointError.

    ``tag_agent`` overrides the ``agent:*`` thread tag when the turn recipient
    (``to_agent``) is a scoped per-dispatch id (e.g. ``cursor-sdk:dispatch:{uuid}``)
    but the thread should remain filterable by family slug (e.g. ``cursor-sdk``).
    Defaults to ``to_agent`` when not supplied.
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
    from agent_bus_store.disposition import append_bus_lifecycle_tags

    contract_tag = f"contract:{handoff_contract}"
    if tags is None:
        effective_tags: list[str] = [
            f"agent:{tag_agent or to_agent}",
            "type:handoff",
            contract_tag,
        ]
    else:
        # Append the contract tag to caller-supplied tags (do not replace them).
        effective_tags = list(tags)
        if contract_tag not in effective_tags:
            effective_tags.append(contract_tag)
    # consult threads must survive delivery — default persistent so the
    # Stargate on-behalf close path cannot close them regardless of the
    # FrontierGenerateRequest's bus_lifecycle field (which is absent for
    # op=generate dispatches).  Callers may still override via bus_lifecycle.
    consult_default = "persistent" if handoff_contract == "consult" else "ephemeral"
    effective_tags = append_bus_lifecycle_tags(
        effective_tags,
        bus_lifecycle=bus_lifecycle or consult_default,
    )
    from agent_bus_store.close_on_read import append_close_on_read_marker

    effective_tags = append_close_on_read_marker(
        effective_tags,
        bus_lifecycle=bus_lifecycle or consult_default,
    )

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
    if lifecycle_state is not None:
        payload["lifecycle_state"] = lifecycle_state

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
