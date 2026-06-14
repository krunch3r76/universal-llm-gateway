"""Closeout validation and bounded delivery helpers for cursor-sdk worker dispatches."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from implement_admission.closeout_models import EvidenceUris, ImplementCloseout
from implement_admission.spec import CloseoutStatus
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

# Must stay aligned with ``libs/agent_bus_store/turns_models`` bus invariants.
MAX_TURN_BODY_CHARS = 8_000
_WORKSPACES_REPO = "universal-llm-gateway"
_SIDECAR_DIR = "tmp/reviews/closeouts"

_IMPLEMENT_PREAMBLE = (
    "Execute this task NOW using your tools. Make the code/file changes the packet "
    "specifies. If you are blocked, reply with `status: blocked` and the specific "
    "reason. Do NOT reply with an acknowledgement-only message.\n\n"
    "Before any fs write: read fs(cortex, agent-skills/architecture-invariants.md) and "
    "fs(cortex, agent-skills/ulg-architecture.md); also load any additional cortex "
    "skills named in <invariants>. Engineering-discipline rules (SLOC, scope, logging) "
    "auto-load via setting_sources; the architecture layer (topology_ws, event contracts, "
    "domain routing) is description-gated and does NOT reliably attach without these reads."
)

_THREAD_BINDING_TEMPLATE = (
    "Your agent-bus reply thread for this dispatch is `{thread_id}`. Post your "
    "closeout ONLY to this thread."
)

_CONTRACT_FRONTMATTER_RE = re.compile(
    r"^contract:\s*(\S+)\s*$",
    re.IGNORECASE | re.MULTILINE,
)


@dataclass(frozen=True)
class SdkRunOutcome:
    body: str
    status: str
    duration_ms: int
    tool_call_count: int


@dataclass(frozen=True)
class CloseoutDelivery:
    body: str
    sidecar_ref: str
    sidecar_path: Path
    full_result_bytes: int


def count_tool_calls(turns: list) -> int:
    total = 0
    for turn in turns:
        steps = getattr(getattr(turn, "turn", None), "steps", ()) or ()
        total += sum(1 for step in steps if getattr(step, "type", "") == "toolCall")
    return total


def degraded_implement_reason(outcome: SdkRunOutcome) -> str | None:
    """Return a machine reason when an implement closeout must not claim success."""
    if outcome.status != "finished":
        return f"run_status={outcome.status}"
    if outcome.tool_call_count == 0:
        return "zero_tool_calls"
    return None


def infer_contract_from_text(text: str) -> str | None:
    match = _CONTRACT_FRONTMATTER_RE.search(text)
    if not match:
        return None
    return match.group(1).strip().lower()


_SOURCE_REF_FRONTMATTER_RE = re.compile(
    r"^source_ref:\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE
)
_WORK_ITEM_KEY_RE = re.compile(
    r"^(?:todo|plan|plan_phase|packet):\s*(\S+)\s*$", re.IGNORECASE | re.MULTILINE
)
_WORK_ITEM_SCHEMES = ("todo:", "plan:", "plan_phase:", "packet:", "agent-bus:")


def extract_source_ref_from_packet(text: str) -> str | None:
    """Canonical work-item source_ref from packet frontmatter, or None.

    Prefers an explicit ``source_ref:`` line; else a ``todo:``/``plan:``/
    ``plan_phase:``/``packet:`` frontmatter line (value is already
    scheme-qualified, e.g. ``todo: todo:x``). Returns None when no
    scheme-qualified work-item ref is present (e.g. message dispatch).
    """
    for pattern in (_SOURCE_REF_FRONTMATTER_RE, _WORK_ITEM_KEY_RE):
        match = pattern.search(text)
        if match:
            ref = match.group(1).strip()
            if ref.startswith(_WORK_ITEM_SCHEMES):
                return ref
    return None


def _thread_binding_sentence(dispatch_thread_id: str) -> str:
    return _THREAD_BINDING_TEMPLATE.format(thread_id=dispatch_thread_id)


def resolve_prompt_preamble(
    *,
    handoff_contract: str | None,
    prompt_preamble: str | None,
    inferred_contract: str | None,
    dispatch_thread_id: str | None = None,
) -> str:
    contract = (handoff_contract or inferred_contract or "consult").lower()
    if prompt_preamble:
        preamble = prompt_preamble.strip()
    elif contract == "implement":
        preamble = _IMPLEMENT_PREAMBLE
    else:
        preamble = ""

    if contract == "implement" and dispatch_thread_id:
        binding = _thread_binding_sentence(dispatch_thread_id)
        preamble = f"{preamble}\n\n{binding}" if preamble else binding

    if preamble:
        return f"{preamble}\n\n"
    return ""


def _sidecar_rel_path(dispatch_id: str) -> str:
    return f"{_SIDECAR_DIR}/{dispatch_id}.md"


def sidecar_workspaces_ref(dispatch_id: str) -> str:
    return f"workspaces://{_WORKSPACES_REPO}/{_sidecar_rel_path(dispatch_id)}"


def _full_result_text(outcome: SdkRunOutcome, degraded_reason: str | None) -> str:
    if degraded_reason:
        return f"status: degraded\nreason: {degraded_reason}\n\n{outcome.body}"
    return outcome.body


def _write_sidecar(source_repo: Path, dispatch_id: str, content: str) -> Path:
    path = source_repo / _sidecar_rel_path(dispatch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _map_closeout_status(degraded_reason: str | None) -> CloseoutStatus:
    """Map the worker's degraded_reason to an ImplementCloseout status.

    None              -> COMPLETE (clean finished run with tool calls)
    "run_status=..."  -> FAILED   (the SDK run itself did not finish)
    anything else     -> PARTIAL  (ran but degraded, e.g. "zero_tool_calls")
    """
    if degraded_reason is None:
        return CloseoutStatus.COMPLETE
    if degraded_reason.startswith("run_status="):
        return CloseoutStatus.FAILED
    return CloseoutStatus.PARTIAL


def build_implement_closeout_body(
    *,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    sidecar_ref: str,
    result_bytes: int,
    thread_id: str,
    work_item_ref: str | None,
) -> str:
    """Build a compact, valid ImplementCloseout JSON turn body.

    The full Composer result lives in the sidecar; this body carries only the
    minimal closeout fields Stargate's trigger_closeout_from_turn requires plus a
    one-line metadata summary. Always small (< 500 chars).

    ``source_ref`` is the canonical work-item ref (the lifecycle target); the
    sidecar is evidence, not identity, so it lands in ``evidence_uris``. When no
    work-item ref exists (message dispatch) ``sidecar_ref`` is the required-field
    fallback — Stargate /closeout then rejects it as unresolvable (a deliberate
    no-op; nothing to reconcile).
    """
    duration_s = outcome.duration_ms / 1000.0
    summary = (
        f"dispatch {dispatch_id}: {outcome.tool_call_count} tool calls, "
        f"{duration_s:.1f}s, {result_bytes}B -> sidecar"
    )
    if degraded_reason:
        summary = f"{summary} (degraded: {degraded_reason})"
    closeout = ImplementCloseout(
        status=_map_closeout_status(degraded_reason),
        summary=summary,
        source_ref=work_item_ref or sidecar_ref,
        evidence_uris=EvidenceUris(
            artifact_paths=[sidecar_ref],
            bus_threads=[thread_id],
            dispatch_ids=[dispatch_id],
        ),
    )
    body = json.dumps(closeout.model_dump(mode="json"), separators=(",", ":"))
    # Unreachable invariant guard: a minimal closeout is always well under the cap.
    assert len(body) <= MAX_TURN_BODY_CHARS, (
        f"structured closeout body exceeded {MAX_TURN_BODY_CHARS} chars "
        f"(len={len(body)}); dispatch_id={dispatch_id}"
    )
    return body


def build_closeout_idempotency_key(
    *, execution_id: str, thread_id: str, turn_number: int | None
) -> str:
    return f"implement-closeout:{execution_id}:{thread_id}:{turn_number}"


def build_closeout_trigger_payload(
    *, body_json: str, source_ref: str, idempotency_key: str
) -> dict:
    return {
        "closeout": json.loads(body_json),
        "source_ref": source_ref,
        "idempotency_key": idempotency_key,
    }


async def emit_implement_closeout_trigger(
    *, body_json: str, source_ref: str, idempotency_key: str
) -> None:
    """Fire-and-forget POST of the closeout to Stargate's /closeout ingress.

    Never raises: bus-delivery success is decoupled from trigger success.
    """
    payload = build_closeout_trigger_payload(
        body_json=body_json, source_ref=source_ref, idempotency_key=idempotency_key
    )
    try:
        async with make_async_client(DEFAULT_STARGATE_URL, timeout=10.0) as client:
            resp = await client.post("/api/v1/implement/closeout", json=payload)
        if resp.status_code >= 400:
            logger.warning(
                "implement-closeout trigger rejected: status=%s key=%s body=%s",
                resp.status_code,
                idempotency_key,
                resp.text[:300],
            )
        else:
            logger.info("implement-closeout trigger accepted: key=%s", idempotency_key)
    except Exception as exc:  # never propagate
        logger.warning(
            "implement-closeout trigger transport error: key=%s err=%s",
            idempotency_key,
            exc,
        )


def _extract_turn_number(body: object) -> int | None:
    if isinstance(body, dict):
        if isinstance(body.get("turn_number"), int):
            return body["turn_number"]
        turn = body.get("turn")
        if isinstance(turn, dict) and isinstance(turn.get("turn_number"), int):
            return turn["turn_number"]
    return None


def prepare_closeout_delivery(
    *,
    source_repo: Path,
    dispatch_id: str,
    outcome: SdkRunOutcome,
    degraded_reason: str | None,
    thread_id: str,
    work_item_ref: str | None,
) -> CloseoutDelivery:
    """Write the full Composer result to a sidecar and return a structured closeout body.

    The turn body is a compact ImplementCloseout JSON object (schema_version 1) so that
    Stargate's trigger_closeout_from_turn can fire pipeline:implement-closeout. The full
    result text is written only to the sidecar (bounded-body invariant from friction-17390).
    """
    full_text = _full_result_text(outcome, degraded_reason)
    sidecar_path = _write_sidecar(source_repo, dispatch_id, full_text)
    sidecar_ref = sidecar_workspaces_ref(dispatch_id)
    result_bytes = len(full_text.encode("utf-8"))
    body = build_implement_closeout_body(
        dispatch_id=dispatch_id,
        outcome=outcome,
        degraded_reason=degraded_reason,
        sidecar_ref=sidecar_ref,
        result_bytes=result_bytes,
        thread_id=thread_id,
        work_item_ref=work_item_ref,
    )
    return CloseoutDelivery(
        body=body,
        sidecar_ref=sidecar_ref,
        sidecar_path=sidecar_path,
        full_result_bytes=result_bytes,
    )


def format_delivery_fallback_body(
    *,
    status_code: int,
    sidecar_ref: str,
    result_bytes: int,
) -> str:
    return (
        f"status: delivery_failed\n"
        f"bus_status_code: {status_code}\n"
        f"result_bytes: {result_bytes}\n"
        f"sidecar: {sidecar_ref}\n\n"
        "Closeout turn rejected by agent-bus. Full Composer result in sidecar."
    )


def resolve_run_outcome_label(degraded_reason: str | None) -> str:
    return "degraded" if degraded_reason else "ok"


def resolve_completion_outcome(*, run_outcome: str, delivery_ok: bool) -> str:
    if not delivery_ok:
        return "delivery_failed"
    return run_outcome
