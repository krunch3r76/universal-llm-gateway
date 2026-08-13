"""Commit apply path — entity seed, packet materialization, single scout dispatch."""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from .packet_fill import entity_seed_payload, fill_recon_packet, slug_from_subject
from .proposal_store import (
    PROPOSAL_KIND_LIFE_INTENT,
    StoredProposal,
    begin_apply,
    commit_reject_code,
    get_proposal,
    mark_completed,
    mark_failed,
    mark_indeterminate,
    record_dispatch_handle,
    record_entity,
    record_packet,
)

_CORTEX_TIMEOUT = 15.0
_RECON_MODEL = "cursor/grok-4.6"


class WorkerAdmissionIndeterminateError(Exception):
    """Worker call outcome uncertain — do not remint; mark indeterminate."""


@dataclass(frozen=True)
class CommitReject:
    code: str
    detail: str


@dataclass(frozen=True)
class CommitResult:
    committed: bool
    entity_id: str | None
    recon_ref: str
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


def _request_id_for(proposal_id: str) -> str:
    return hashlib.sha256(proposal_id.encode()).hexdigest()[:12]


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


def _entity_exists(entity_id: str) -> bool:
    try:
        body = _cortex_dispatch("entity_get", {"entity_id": entity_id})
    except Exception:
        return False
    if not isinstance(body, dict):
        return False
    if body.get("error"):
        return False
    return bool(body.get("id") or body.get("entity_id") or body.get("item"))


def _ensure_entity(seed: dict[str, Any]) -> str:
    entity_id = str(seed["id"])
    if _entity_exists(entity_id):
        return entity_id
    try:
        return _create_entity(seed)
    except Exception as exc:
        text = str(exc).lower()
        if "already exists" in text or "409" in text:
            return entity_id
        raise


def _create_context_edge(entity_id: str, thread_id: str) -> None:
    _cortex_dispatch(
        "relationship_create",
        {
            "from_entity": entity_id,
            "to_entity": thread_id,
            "relationship_type": "evidence_for",
        },
    )


async def _prepare_recon_handle(
    *,
    request_id: str,
    packet_path: str,
    subject: str,
    reply_thread: str,
    execution_id: str | None = None,
    dispatch_id: str | None = None,
) -> Any:
    from systems.frontier_consult.cursor_sdk_generate_prepare import (  # noqa: I001
        prepare_cursor_sdk_generate,
    )

    return await prepare_cursor_sdk_generate(
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
        execution_id=execution_id,
        dispatch_id=dispatch_id,
    )


async def _submit_prepared_handle(handle: Any) -> str:
    from systems.frontier_consult.cursor_sdk_generate import (  # noqa: I001
        dispatch_prepared_cursor_sdk,
    )
    from systems.frontier_consult.admission import FrontierEndpointError

    try:
        result = await dispatch_prepared_cursor_sdk(handle)
    except FrontierEndpointError as exc:
        code = str(getattr(exc, "code", "") or "")
        status = int(getattr(exc, "status_code", 0) or 0)
        if status == 599 or code in {
            "CURSOR_WORKER_UNREACHABLE",
            "CURSOR_WORKER_DISPATCH_FAILED",
        }:
            raise WorkerAdmissionIndeterminateError(str(exc)) from exc
        raise
    except TimeoutError as exc:
        raise WorkerAdmissionIndeterminateError(str(exc)) from exc

    dispatch_ref = str(
        result.get("dispatch_thread_id")
        or result.get("thread_id")
        or handle.thread_id
        or handle.request_id
    )
    return dispatch_ref


def validate_commit(proposal_id: str) -> CommitReject | StoredProposal:
    if not commit_live_enabled():
        return CommitReject(
            "commit_gated",
            "Commit is gated; set LIFE_INTENT_COMMIT_LIVE=1 to enable.",
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
    """Apply frozen proposal — at most one entity and one scout admission."""
    if not commit_live_enabled():
        return CommitReject(
            "commit_gated",
            "Commit is gated; set LIFE_INTENT_COMMIT_LIVE=1 to enable.",
        )

    row, reject_code = begin_apply(proposal_id)
    if reject_code:
        return CommitReject(reject_code, f"Proposal cannot be committed: {reject_code}")
    assert row is not None

    try:
        normalized = row.normalized_intent
        slug = slug_from_subject(normalized["subject"])

        packet_path = row.packet_path
        if packet_path is None:
            packet_path = _write_packet(fill_recon_packet(normalized), slug)
            record_packet(proposal_id, packet_path)

        entity_id = row.entity_id
        if entity_id is None:
            seed = entity_seed_payload(normalized)
            if seed is not None:
                entity_id = _ensure_entity(seed)
                record_entity(proposal_id, entity_id)

        thread = row.reply_thread or reply_thread or f"agent-bus:life-intent-{slug}"
        handle_data = row.dispatch_handle
        from systems.frontier_consult.cursor_sdk_generate_prepare import (
            handle_from_dict,
            handle_to_dict,
        )

        if handle_data is None:
            handle = await _prepare_recon_handle(
                request_id=_request_id_for(proposal_id),
                packet_path=packet_path,
                subject=normalized["subject"],
                reply_thread=thread,
            )
            handle_data = handle_to_dict(handle)
            record_dispatch_handle(proposal_id, handle_data, reply_thread=thread)
        else:
            handle = handle_from_dict(handle_data)

        if row.dispatch_ref is None:
            dispatch_ref = await _submit_prepared_handle(handle)
            record_dispatch_handle(
                proposal_id,
                handle_to_dict(handle),
                reply_thread=thread,
            )
            from .proposal_store import record_dispatch

            record_dispatch(proposal_id, dispatch_ref, thread)
            if entity_id and dispatch_ref:
                try:
                    _create_context_edge(entity_id, dispatch_ref)
                except Exception:
                    pass
        else:
            dispatch_ref = row.dispatch_ref

        mark_completed(proposal_id)
        return CommitResult(
            committed=True,
            entity_id=entity_id,
            recon_ref=dispatch_ref,
            reply_thread=thread,
            proposal_id=proposal_id,
        )
    except WorkerAdmissionIndeterminateError as exc:
        mark_indeterminate(proposal_id, repr(exc))
        return CommitReject(
            "commit_indeterminate",
            "Worker admission outcome uncertain; retry the same proposal_id "
            "to resume with the stored handle.",
        )
    except Exception as exc:
        mark_failed(proposal_id, repr(exc))
        return CommitReject(
            "commit_incomplete",
            "Commit interrupted before completion; retry the same proposal_id "
            "to resume.",
        )
