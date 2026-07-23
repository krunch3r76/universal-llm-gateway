"""POST /close/* — life close-verb stage/draft/check/commit/handoff."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field, model_validator
from universal_logging import get_logger

from ..close_draft.check import run_close_check
from ..close_draft.commit import execute_commit
from ..close_draft.constants import DEFAULT_CHECKLIST
from ..close_draft.store import (
    cap_exceeded,
    create_draft,
    get_draft,
    stamp_check_state,
    update_draft_fields,
)
from ..close_draft.validate import (
    coalesce_draft_fields,
    reject_graph_write_keys,
    validate_stage_args,
)
from ..db import cortex_conn, query
from ..dispatch_ops._session_todo_reconciliation import open_todos_in_entity_ids
from ..events_close import (
    close_check_completed,
    close_draft_opened,
    close_draft_updated,
    close_handoff_upserted,
)
from ..routes.session_handoff import _upsert_session_handoff_impl

router = APIRouter(prefix="/close", tags=["close"])
logger = get_logger("cortex-api.close")


class StageRequest(BaseModel):
    session_id: str
    agent: str
    prior_session_id: str | None = None


class DraftRequest(BaseModel):
    """Draft patch — nested ``fields`` and/or flat ALLOWED_FIELD_KEYS aliases."""

    model_config = ConfigDict(extra="allow")

    session_id: str
    fields: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _fold_flat_field_aliases(self) -> DraftRequest:
        extras = dict(self.__pydantic_extra__ or {})
        merged, unknown = coalesce_draft_fields(nested=self.fields, flat=extras)
        if unknown:
            raise ValueError(
                f"Unknown draft field(s) at top level: {unknown}. "
                "Put close fields under 'fields' or use known keys "
                "(summary, session_summary_md, …)."
            )
        self.fields = merged
        if self.__pydantic_extra__ is not None:
            self.__pydantic_extra__.clear()
        return self


class CheckRequest(BaseModel):
    session_id: str


class CommitRequest(BaseModel):
    """Commit uses the checked draft only — summary belongs on draft, not here."""

    model_config = ConfigDict(extra="forbid")

    session_id: str
    checked_revision: int


class HandoffRequest(BaseModel):
    session_id: str
    handoff_prompt: str
    handoff_source_path: str | None = None


def _raise_from_error(err: dict[str, Any], code: int = 422) -> None:
    raise HTTPException(status_code=code, detail=err)


def _known_entity_ids(conn: object, entity_ids: list[str]) -> list[str]:
    if not entity_ids:
        return []
    placeholders = ",".join("?" * len(entity_ids))
    rows = query(
        conn,
        f"SELECT id FROM entities WHERE id IN ({placeholders})",
        tuple(entity_ids),
    )
    return [r["id"] for r in rows]


@router.post("/stage")
def close_stage(body: StageRequest) -> dict[str, Any]:
    err = validate_stage_args(session_id=body.session_id, agent=body.agent)
    if err:
        _raise_from_error(err)
    exceeded, oldest = cap_exceeded(body.agent)
    with cortex_conn() as conn:
        existing = get_draft(conn, body.session_id)
        if existing is None:
            if exceeded:
                _raise_from_error(
                    {
                        "reason": "close_draft.cap_exceeded",
                        "detail": (
                            f"Agent {body.agent!r} has {len(oldest)}+ uncommitted drafts "
                            f"(cap). Oldest: {oldest}"
                        ),
                        "oldest_drafts": oldest,
                    }
                )
            draft = create_draft(
                conn,
                session_id=body.session_id,
                agent=body.agent,
                prior_session_id=body.prior_session_id,
            )
            conn.commit()
            close_draft_opened(
                session_id=body.session_id,
                agent=body.agent,
                revision=1,
            )
        else:
            if existing.get("committed_at"):
                _raise_from_error(
                    {"reason": "close_draft.already_committed", "detail": "Session closed"},
                    409,
                )
            draft = existing
        entity_ids = (draft.get("fields") or {}).get("entity_ids") or []
        known: list[str] = []
        if isinstance(entity_ids, list):
            known = _known_entity_ids(conn, entity_ids)
        open_todos = open_todos_in_entity_ids(entity_ids if isinstance(entity_ids, list) else [])

    return {
        "session_id": body.session_id,
        "prior_session_id": body.prior_session_id
        or (draft.get("fields") or {}).get("prior_session_id"),
        "draft_revision": draft["revision"],
        "known_state": {
            "entity_ids_known": known,
            "open_todos_in_entity_ids": open_todos,
        },
        "checklist": DEFAULT_CHECKLIST,
    }


@router.post("/draft")
def close_draft(body: DraftRequest) -> dict[str, Any]:
    reject = reject_graph_write_keys(body.fields)
    if reject:
        _raise_from_error(reject)
    with cortex_conn() as conn:
        draft = get_draft(conn, body.session_id)
        if draft is None:
            _raise_from_error(
                {"reason": "close_draft.not_found", "detail": "Call stage first"},
                404,
            )
        if draft.get("committed_at"):
            _raise_from_error(
                {"reason": "close_draft.already_committed", "detail": "Draft immutable"},
                409,
            )
        updated = update_draft_fields(
            conn, session_id=body.session_id, patch=body.fields
        )
        if updated is None:
            _raise_from_error(
                {"reason": "close_draft.already_committed", "detail": "Draft immutable"},
                409,
            )
        revision, fields = updated
        conn.commit()
    close_draft_updated(
        session_id=body.session_id,
        agent=str(draft["agent"]),
        revision=revision,
    )
    return {"session_id": body.session_id, "draft_revision": revision, "fields": fields}


@router.post("/check")
def close_check(body: CheckRequest) -> dict[str, Any]:
    with cortex_conn() as conn:
        draft = get_draft(conn, body.session_id)
        if draft is None:
            _raise_from_error({"reason": "close_draft.not_found"}, 404)
        if draft.get("committed_at"):
            _raise_from_error({"reason": "close_draft.already_committed"}, 409)
        result = run_close_check(
            session_id=body.session_id,
            agent=str(draft["agent"]),
            fields=draft["fields"],
            revision=int(draft["revision"]),
        )
        if result["status"] == "PASS":
            stamp_check_state(
                conn,
                session_id=body.session_id,
                checked_revision=int(draft["revision"]),
                status="PASS",
                report=result["report"],
            )
            result["checked_revision"] = int(draft["revision"])
            conn.commit()
    close_check_completed(
        session_id=body.session_id,
        agent=str(draft["agent"]),
        revision=int(draft["revision"]),
        status=result["status"],
        gap_count=len(result["report"].get("gaps") or []),
    )
    return result


@router.post("/commit", status_code=status.HTTP_201_CREATED)
def close_commit(body: CommitRequest) -> dict[str, Any]:
    result = execute_commit(
        session_id=body.session_id, checked_revision=body.checked_revision
    )
    sc = result.pop("status_code", None)
    if "error" in result and not result.get("already_closed"):
        raise HTTPException(status_code=int(sc or 422), detail=result)
    return result


@router.post("/handoff")
def close_handoff(body: HandoffRequest) -> dict[str, Any]:
    with cortex_conn() as conn:
        draft = get_draft(conn, body.session_id)
        if draft is None or not draft.get("committed_at"):
            _raise_from_error(
                {
                    "reason": "close_draft.not_committed",
                    "detail": "handoff requires committed session",
                },
                422,
            )
    payload: dict[str, Any] = {
        "session_id": body.session_id,
        "handoff_prompt": body.handoff_prompt,
    }
    if body.handoff_source_path:
        payload["handoff_source_path"] = body.handoff_source_path
    result = _upsert_session_handoff_impl(payload)
    if "error" in result:
        _raise_from_error(result, int(result.get("status_code") or 422))
    close_handoff_upserted(
        session_id=body.session_id,
        journal_row_id=int(result.get("journal_row_id") or 0),
    )
    return result
