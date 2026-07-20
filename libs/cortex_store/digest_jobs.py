"""Async digest job queue — CRUD, dedupe, state machine, tick."""

from __future__ import annotations

import threading
import uuid
from typing import Any

from universal_logging import get_logger

from .db import cortex_conn, json_decode, json_encode
from .digest_ledger import (
    compute_entry_content_sha256,
    lookup,
    lookup_effective_watermark,
)
from .events_digest import (
    digest_job_enqueued,
    digest_job_harvested,
    digest_job_parked,
    digest_job_parsed,
    digest_job_staged,
    digest_job_submitted,
    digest_job_verified,
)
from .journal_digest_extract_cdp import (
    PROMPT_REV_SOFT_V2,
    cdp_model,
    run_cdp_extract_for_job,
)

logger = get_logger("cortex-api.digest_jobs")

_TERMINAL_STATES = frozenset({"STAGED", "PARKED"})
_OPEN_STATES = frozenset(
    {"ENQUEUED", "SUBMITTED", "HARVESTED", "PARSED", "VERIFIED", "VERIFY_BLOCKED"}
)
_LANE_LOCK = threading.Lock()

_JSON_FIELDS = frozenset({"claims_json", "verify_verdicts"})


def _decode_row(row: Any) -> dict[str, Any]:
    out = dict(row)
    for field in _JSON_FIELDS:
        if field in out and out[field] is not None:
            out[field] = json_decode(out[field], fallback={} if field == "verify_verdicts" else None)
    return out


def _find_open_job(
    conn,
    *,
    entry_anchor: str,
    content_sha256: str,
    prompt_rev: str,
) -> dict[str, Any] | None:
    placeholders = ",".join("?" * len(_OPEN_STATES))
    row = conn.execute(
        f"""
        SELECT * FROM digest_jobs
        WHERE entry_anchor = ? AND content_sha256 = ? AND prompt_rev = ?
          AND state IN ({placeholders})
        ORDER BY created_at DESC LIMIT 1
        """,
        (entry_anchor, content_sha256, prompt_rev, *_OPEN_STATES),
    ).fetchone()
    return _decode_row(row) if row else None


def enqueue_extract(
    *,
    journal_entity_id: str,
    entry_anchor: str,
    entry_text: str,
    journal_uri: str | None = None,
    kind: str = "extract",
) -> dict[str, Any]:
    """Consult ledger watermark + open-job dedupe; INSERT ENQUEUED when needed."""
    content_sha = compute_entry_content_sha256(entry_text)
    prompt_rev = PROMPT_REV_SOFT_V2
    model = cdp_model()

    with cortex_conn() as conn:
        prior = lookup(conn, journal_entity_id, entry_anchor, content_sha)
        if prior is not None:
            return {
                "status": "skipped",
                "reason": "watermark_match",
                "ledger_id": prior["id"],
                "content_sha256": content_sha,
            }

        open_job = _find_open_job(
            conn,
            entry_anchor=entry_anchor,
            content_sha256=content_sha,
            prompt_rev=prompt_rev,
        )
        if open_job is not None:
            return {
                "status": "deduped",
                "job_id": open_job["job_id"],
                "state": open_job["state"],
            }

        effective = lookup_effective_watermark(conn, journal_entity_id, entry_anchor)
        if (
            effective is not None
            and effective.get("content_sha256") != content_sha
            and kind == "extract"
        ):
            kind = "revision_extract"

        job_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO digest_jobs (
                job_id, kind, journal_entity_id, journal_uri, entry_anchor,
                entry_text, content_sha256, prompt_rev, model, lane, state
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'cowork', 'ENQUEUED')
            """,
            (
                job_id,
                kind,
                journal_entity_id,
                journal_uri,
                entry_anchor,
                entry_text,
                content_sha,
                prompt_rev,
                model,
            ),
        )
        conn.commit()

    digest_job_enqueued(
        job_id=job_id,
        journal_entity_id=journal_entity_id,
        entry_anchor=entry_anchor,
        kind=kind,
    )
    return {
        "status": "enqueued",
        "job_id": job_id,
        "kind": kind,
        "state": "ENQUEUED",
        "content_sha256": content_sha,
    }


def job_status(job_id: str) -> dict[str, Any]:
    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT * FROM digest_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
    if row is None:
        return {"error": "job_not_found", "job_id": job_id}
    job = _decode_row(row)
    return {"status": "ok", "job": job}


def retry_job(job_id: str) -> dict[str, Any]:
    with cortex_conn() as conn:
        row = conn.execute(
            "SELECT state FROM digest_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return {"error": "job_not_found", "job_id": job_id}
        if row["state"] != "PARKED":
            return {"error": "job_not_parked", "job_id": job_id, "state": row["state"]}
        conn.execute(
            """
            UPDATE digest_jobs
            SET state = 'ENQUEUED', last_error = NULL,
                updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
            WHERE job_id = ?
            """,
            (job_id,),
        )
        conn.commit()
    return {"status": "retried", "job_id": job_id, "state": "ENQUEUED"}


def _update_job(conn, job_id: str, **fields: Any) -> None:
    if not fields:
        return
    cols = ", ".join(f"{key} = ?" for key in fields)
    values = list(fields.values()) + [job_id]
    conn.execute(
        f"UPDATE digest_jobs SET {cols}, updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now') WHERE job_id = ?",
        values,
    )


def _park_job(conn, job: dict[str, Any], reason: str, error: str | None = None) -> None:
    _update_job(
        conn,
        str(job["job_id"]),
        state="PARKED",
        last_error=error or reason,
    )
    digest_job_parked(
        job_id=str(job["job_id"]),
        journal_entity_id=str(job["journal_entity_id"]),
        entry_anchor=str(job["entry_anchor"]),
        reason=reason,
    )


def _try_claim_enqueued(conn, job_id: str) -> bool:
    """Atomically move ENQUEUED → SUBMITTED; False when another tick claimed first."""
    cur = conn.execute(
        """
        UPDATE digest_jobs
        SET state = 'SUBMITTED',
            updated_at = strftime('%Y-%m-%dT%H:%M:%SZ', 'now')
        WHERE job_id = ? AND state = 'ENQUEUED'
        """,
        (job_id,),
    )
    return cur.rowcount == 1


def _pick_job(conn) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT * FROM digest_jobs
        WHERE state IN ('ENQUEUED', 'SUBMITTED', 'HARVESTED', 'PARSED', 'VERIFIED', 'VERIFY_BLOCKED')
        ORDER BY created_at ASC LIMIT 1
        """
    ).fetchone()
    return _decode_row(row) if row else None


def _advance_job(conn, job: dict[str, Any]) -> dict[str, Any]:
    state = job["state"]
    job_id = str(job["job_id"])

    if state == "ENQUEUED":
        if not _try_claim_enqueued(conn, job_id):
            conn.commit()
            return {"job_id": job_id, "status": "claim_lost", "state": "ENQUEUED"}

        if job.get("kind") == "revision_extract":
            _park_job(conn, job, "revision_extract_async_v0")
            conn.commit()
            return {"job_id": job_id, "state": "PARKED", "reason": "revision_extract_async_v0"}

        row = conn.execute(
            "SELECT * FROM digest_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        job = _decode_row(row)
        state = job["state"]

    if state == "SUBMITTED":
        result = run_cdp_extract_for_job(job)
        if result.get("park_reason"):
            attempt = int(job.get("attempt") or 0) + 1
            if attempt >= 2:
                _park_job(conn, job, str(result["park_reason"]), str(result.get("error")))
            else:
                _update_job(
                    conn,
                    job_id,
                    attempt=attempt,
                    last_error=str(result.get("error")),
                    state="ENQUEUED",
                )
            conn.commit()
            return {"job_id": job_id, "error": result.get("error"), "park_reason": result.get("park_reason")}

        if "error" in result:
            attempt = int(job.get("attempt") or 0) + 1
            if attempt >= 2:
                reason = str(result.get("park_reason") or result["error"])
                _park_job(conn, job, reason, str(result["error"]))
            else:
                _update_job(
                    conn,
                    job_id,
                    attempt=attempt,
                    execution_id=result.get("execution_id"),
                    last_error=str(result["error"]),
                    state="ENQUEUED",
                )
            conn.commit()
            return {"job_id": job_id, "error": result["error"]}

        _update_job(
            conn,
            job_id,
            state="PARSED",
            execution_id=result.get("execution_id"),
            prompt_uri=result.get("prompt_uri"),
            prompt_sha256=result.get("prompt_sha256"),
            archive_uri=result.get("archive_uri"),
            harvest_sha256=result.get("harvest_sha256"),
            claims_json=json_encode(result.get("claims_json")),
        )
        digest_job_submitted(job_id=job_id, execution_id=str(result.get("execution_id")))
        digest_job_harvested(job_id=job_id, archive_uri=str(result.get("archive_uri")))
        digest_job_parsed(
            job_id=job_id,
            claim_count=len((result.get("claims_json") or {}).get("claims", [])),
        )
        conn.commit()
        job = _decode_row(
            conn.execute("SELECT * FROM digest_jobs WHERE job_id = ?", (job_id,)).fetchone()
        )
        state = job["state"]

    if state == "PARSED":
        from .dispatch_ops.ops_digest import finish_claim_batch_for_job

        finish = finish_claim_batch_for_job(job)
        if finish.get("error") == "verify_blocked":
            _update_job(conn, job_id, state="VERIFY_BLOCKED", last_error="verify_blocked")
            conn.commit()
            return finish
        if "error" in finish:
            _park_job(conn, job, str(finish.get("code") or finish["error"]))
            conn.commit()
            return finish

        digest_job_verified(
            job_id=job_id,
            claim_count=len((finish.get("claims_json") or {}).get("claims", [])),
        )
        _update_job(
            conn,
            job_id,
            state="STAGED",
            verify_verdicts=json_encode(finish.get("verify_verdicts")),
            staging_batch_id=finish.get("staging_batch_id"),
        )
        digest_job_staged(
            job_id=job_id,
            ledger_id=int(finish["ledger_id"]),
            staging_batch_id=str(finish.get("staging_batch_id")),
        )
        conn.commit()
        return {"job_id": job_id, "state": "STAGED", **finish}

    if state == "VERIFY_BLOCKED":
        from .dispatch_ops.ops_digest import finish_claim_batch_for_job

        finish = finish_claim_batch_for_job(job)
        if "error" in finish:
            return finish
        _update_job(
            conn,
            job_id,
            state="STAGED",
            verify_verdicts=json_encode(finish.get("verify_verdicts")),
            staging_batch_id=finish.get("staging_batch_id"),
        )
        digest_job_staged(
            job_id=job_id,
            ledger_id=int(finish["ledger_id"]),
            staging_batch_id=str(finish.get("staging_batch_id")),
        )
        conn.commit()
        return {"job_id": job_id, "state": "STAGED", **finish}

    return {"job_id": job_id, "state": state, "status": "noop"}


def tick_jobs(*, limit: int = 1) -> dict[str, Any]:
    """Single-flight lane lock; advance up to *limit* jobs."""
    results: list[dict[str, Any]] = []
    acquired = _LANE_LOCK.acquire(blocking=False)
    if not acquired:
        return {"status": "lane_busy", "results": results}

    try:
        for _ in range(max(1, limit)):
            with cortex_conn() as conn:
                job = _pick_job(conn)
                if job is None:
                    break
                outcome = _advance_job(conn, job)
                if outcome.get("status") == "claim_lost":
                    break
                results.append(outcome)
                if outcome.get("state") == "STAGED" or outcome.get("park_reason"):
                    continue
                break
    finally:
        _LANE_LOCK.release()

    return {"status": "ok", "results": results, "count": len(results)}
