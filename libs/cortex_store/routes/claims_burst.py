"""POST /claims/burst — vocabulary-grounded claim retrieval (salience slice 3)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status

from ..claims_burst import burst_claims
from ..db import cortex_conn, query
from ..models.claims_burst import ClaimsBurstRequest, ClaimsBurstResponse

router = APIRouter(tags=["claims"])


@router.post(
    "/claims/burst",
    response_model=ClaimsBurstResponse,
    status_code=status.HTTP_200_OK,
    operation_id="postClaimsBurst",
    summary="Burst claims by controlled action vocabulary",
)
def post_claims_burst(body: ClaimsBurstRequest) -> ClaimsBurstResponse:
    """Return scope assertions matching vocabulary with enrich-on-read predicates.

    Read-only: never backfills ``predicate_form`` on stored assertion rows.
    """
    from predicate_form.action_vocabulary import ACTION_VOCAB_V0

    invalid_vocab = [term for term in body.vocabulary if term not in ACTION_VOCAB_V0]
    if invalid_vocab:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "unknown_vocabulary_terms",
                "invalid": invalid_vocab,
                "allowed": sorted(ACTION_VOCAB_V0),
            },
        )

    with cortex_conn() as conn:
        placeholders = ",".join("?" for _ in body.scope_entity_ids)
        existing = query(
            conn,
            f"SELECT id FROM entities WHERE id IN ({placeholders})",
            tuple(body.scope_entity_ids),
        )
        found = {row["id"] for row in existing}
        missing = [eid for eid in body.scope_entity_ids if eid not in found]
        if missing:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "entity_not_found", "entity_ids": missing},
            )
        return burst_claims(conn, body)
