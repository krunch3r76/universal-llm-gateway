"""CDP generate deliverable-attest ingress.

A consumer claim is not proof; the server's own read is. The ingress re-reads the
``cortex://`` target and recomputes sha256 of on-disk source bytes before any
``finalize_cdp_generate`` call. A body that merely *asserts* ``written_sha256`` is
refused. Attest is an additional proof class (``content_proof_uri`` plus
server-verified sha), never a relaxation of ``has_proof``.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from claude_bundles.cdp_model_endpoint import CdpGenerateResult, picker_from_model_id
from cortex_store.files_path_normalize import normalize_cortex_files_path
from implement_admission.closeout_helpers import cortex_files_root
from pydantic import BaseModel, ConfigDict
from universal_protocol.errors import ProtocolError

from .cdp_generate_inflight_ledger import read_inflight_leg, terminal_event_exists
from .cdp_generate_reconcile import finalize_cdp_generate


class AttestDeliverableRequest(BaseModel):
    """Body for POST /api/v1/providers/cdp/generate/{execution_id}/attest."""

    model_config = ConfigDict(extra="forbid")

    content_proof_uri: str
    written_sha256: str
    attested_by: str


@dataclass(frozen=True, slots=True)
class AttestDeliverableResult:
    """Successful attest response fields."""

    ok: bool
    via: str
    proof_emitted: bool
    delivered: bool


class AttestConflictError(Exception):
    """Leg already terminal or finalize claim lost — HTTP 409."""

    def __init__(self, *, proof_emitted: bool, delivered: bool) -> None:
        self.proof_emitted = proof_emitted
        self.delivered = delivered


def _target_missing(
    *,
    execution_id: str,
    content_proof_uri: str,
    reason: str,
) -> ProtocolError:
    return ProtocolError(
        code="cdp_attest_target_missing",
        message="content_proof_uri is not an attestable cortex file",
        source="gateway",
        retryable=False,
        data={
            "execution_id": execution_id,
            "content_proof_uri": content_proof_uri,
            "reason": reason,
        },
    )


def _resolve_attest_file(
    *,
    execution_id: str,
    content_proof_uri: str,
) -> Path:
    """Return a jailed cortex file path or raise ``ProtocolError``."""
    uri = content_proof_uri.strip()
    if not uri.startswith("cortex://"):
        raise _target_missing(
            execution_id=execution_id,
            content_proof_uri=uri,
            reason="not_cortex_uri",
        )

    root = cortex_files_root()
    rel, norm_err = normalize_cortex_files_path(
        uri,
        root,
        field="content_proof_uri",
    )
    if norm_err is not None or rel is None:
        reason = "not_cortex_uri"
        norm_reason = str(norm_err.get("reason") if norm_err else "")
        if "outside_files_root" in norm_reason:
            reason = "path_escape"
        raise _target_missing(
            execution_id=execution_id,
            content_proof_uri=uri,
            reason=reason,
        )

    file_path = (root / rel).resolve()
    if not file_path.is_file():
        raise _target_missing(
            execution_id=execution_id,
            content_proof_uri=uri,
            reason="not_file",
        )
    return file_path


async def attest_cdp_generate_deliverable(
    *,
    execution_id: str,
    body: AttestDeliverableRequest,
) -> AttestDeliverableResult:
    """Attest a consumer-verified cortex deliverable into ``finalize_cdp_generate``.

    A consumer claim is not proof; the server's own read is. Re-reads the
    ``cortex://`` target, recomputes sha256 of on-disk bytes, then emits
    ``cdp.generate.proof`` with ``via=attest`` when the open leg matches.
    """
    leg = read_inflight_leg(execution_id)
    if leg is None:
        raise ProtocolError(
            code="cdp_attest_leg_not_open",
            message=f"No open inflight leg for execution_id={execution_id!r}",
            source="gateway",
            retryable=False,
            data={"execution_id": execution_id},
        )

    if leg.proof_emitted or terminal_event_exists(execution_id):
        raise AttestConflictError(
            proof_emitted=True,
            delivered=leg.delivered,
        )

    uri = body.content_proof_uri.strip()
    file_path = _resolve_attest_file(
        execution_id=execution_id,
        content_proof_uri=uri,
    )
    computed = hashlib.sha256(file_path.read_bytes()).hexdigest()
    claimed = body.written_sha256.strip().lower()
    if computed != claimed:
        raise ProtocolError(
            code="cdp_attest_sha_mismatch",
            message="written_sha256 does not match on-disk source bytes",
            source="gateway",
            retryable=False,
            data={
                "execution_id": execution_id,
                "content_proof_uri": uri,
                "claimed": claimed,
                "computed": computed,
            },
        )

    result = CdpGenerateResult(
        ok=True,
        body=f"deliverable attested: {uri} sha256={computed}",
        execution_id=leg.execution_id,
        satellite_execution_id=leg.satellite_execution_id,
        prompt_uri=leg.prompt_uri,
        picker_model=picker_from_model_id(leg.model_id),
        content_proof_uri=uri,
        content_proof_sha256=computed,
        archive_uri=None,
    )
    await finalize_cdp_generate(
        result=result,
        request_id=leg.request_id,
        thread_id=leg.thread_id,
        to_agent=leg.caller_agent or "dispatch",
        pointer_turn=leg.pointer_turn,
        via="attest",
        attested_by=body.attested_by,
    )

    leg_after = read_inflight_leg(execution_id)
    if leg_after is None or not leg_after.proof_emitted:
        raise AttestConflictError(
            proof_emitted=bool(leg_after and leg_after.proof_emitted),
            delivered=bool(leg_after and leg_after.delivered),
        )

    return AttestDeliverableResult(
        ok=True,
        via="attest",
        proof_emitted=True,
        delivered=leg_after.delivered,
    )
