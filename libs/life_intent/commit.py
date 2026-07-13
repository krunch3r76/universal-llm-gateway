"""Commit apply path — entity seed, packet materialization, single scout dispatch."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from .packet_fill import entity_seed_payload, fill_recon_packet, slug_from_subject
from .proposal_store import (
    PROPOSAL_KIND_LIFE_INTENT,
    StoredProposal,
    claim_proposal,
    commit_reject_code,
    get_proposal,
)

_CORTEX_TIMEOUT = 15.0
_RECON_MODEL = "cursor/grok-4.5"


@dataclass(frozen=True)
class CommitReject:
    code: str
    detail: str


@dataclass(frozen=True)
class CommitResult:
    committed: bool
    entity_id: str | None
    dispatch_ref: str
    reply_thread: str
    proposal_id: str


def commit_live_enabled() -> bool:
    return os.environ.get("LIFE_INTENT_COMMIT_LIVE", "").strip() == "1"


def _workspaces_root() -> Path:
    raw = os.environ.get("PROJECT_ROOT") or os.environ.get("WORKSPACE_ROOT") or "."
    return Path(raw).expanduser().resolve()


def _write_packet(packet_text: str, slug: str) -> str:
    root = _workspaces_root()
    rel = f"universal-llm-gateway/tmp/reviews/life-intent-{slug}-recon-packet.md"
    dest = root / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(packet_text, encoding="utf-8")
    return rel


def _cortex_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    payload = {"tool": tool, "arguments": arguments}
    with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_TIMEOUT) as client:
        resp = client.post("/dispatch", json=payload)
        resp.raise_for_status()
        body = resp.json()
        if isinstance(body, dict) and body.get("error"):
            raise RuntimeError(str(body["error"]))
        return body if isinstance(body, dict) else {"result": body}


def _create_entity(seed: dict[str, Any]) -> str:
    entity_id = str(seed["id"])
    args: dict[str, Any] = {
        "id": entity_id,
        "type": seed["type"],
        "name": seed["name"],
        "source_uri": seed.get("source_uri"),
        "attributes": seed.get("attributes") or {},
    }
    _cortex_dispatch("entity_create", args)
    return entity_id


def _create_context_edge(entity_id: str, thread_id: str) -> None:
    _cortex_dispatch(
        "relationship_create",
        {
            "from_entity": entity_id,
            "to_entity": thread_id,
            "relationship_type": "evidence_for",
        },
    )


async def _fire_recon_dispatch(
    *,
    request_id: str,
    packet_path: str,
    subject: str,
    reply_thread: str,
) -> str:
    from systems.frontier_consult.cursor_sdk_generate import (  # noqa: I001
        dispatch_cursor_sdk_generate,
    )

    result = await dispatch_cursor_sdk_generate(
        request_id=request_id,
        role="cursor-sdk",
        model=_RECON_MODEL,
        subject=subject,
        caller_agent="web-anthropic",
        contract="light-bounded",
        packet_path=packet_path,
        message_text=None,
        reuse_thread=None,
        bus_lifecycle="persistent",
        parent_dispatch_thread_id=reply_thread,
    )
    dispatch_ref = str(result.get("dispatch_thread_id") or result.get("thread_id") or request_id)
    return dispatch_ref


def validate_commit(proposal_id: str) -> CommitReject | StoredProposal:
    if not commit_live_enabled():
        return CommitReject(
            "commit_gated",
            "Commit is gated until imprint F1 evidence and F2 Arm-A clearance.",
        )
    row = get_proposal(proposal_id)
    code = commit_reject_code(row)
    if code:
        return CommitReject(code, f"Proposal cannot be committed: {code}")
    assert row is not None
    if row.kind != PROPOSAL_KIND_LIFE_INTENT:
        return CommitReject(
            "foreign_proposal_kind",
            "Proposal is not a life-intent proposal.",
        )
    return row


async def apply_commit(
    proposal_id: str,
    *,
    reply_thread: str | None = None,
) -> CommitResult | CommitReject:
    """Apply frozen proposal — at most one entity and one scout dispatch."""
    if not commit_live_enabled():
        return CommitReject(
            "commit_gated",
            "Commit is gated until imprint F1 evidence and F2 Arm-A clearance.",
        )

    row, reject_code = claim_proposal(proposal_id)
    if reject_code:
        return CommitReject(reject_code, f"Proposal cannot be committed: {reject_code}")
    assert row is not None

    normalized = row.normalized_intent
    slug = slug_from_subject(normalized["subject"])
    packet_text = fill_recon_packet(normalized)
    packet_path = _write_packet(packet_text, slug)

    entity_id: str | None = None
    seed = entity_seed_payload(normalized)
    if seed is not None:
        entity_id = _create_entity(seed)

    request_id = uuid.uuid4().hex[:12]
    thread = reply_thread or f"agent-bus:life-intent-{slug}"
    dispatch_ref = await _fire_recon_dispatch(
        request_id=request_id,
        packet_path=packet_path,
        subject=normalized["subject"],
        reply_thread=thread,
    )

    if entity_id and dispatch_ref:
        try:
            _create_context_edge(entity_id, dispatch_ref)
        except Exception:
            pass

    return CommitResult(
        committed=True,
        entity_id=entity_id,
        dispatch_ref=dispatch_ref,
        reply_thread=thread,
        proposal_id=proposal_id,
    )
