"""B8 — harvest ``cdp/opus-*`` consult provenance during window closeout."""

from __future__ import annotations

from universal_logging import get_logger

from . import bus_client

logger = get_logger(__name__)


async def maybe_harvest_cdp_consult_provenance(
    *,
    root_id: str,
    window_index: int,
    worker_thread: str,
    worker_turns: list[dict],
    admission_meta: dict,
) -> dict[str, str] | None:
    """Parse ``cdp/opus-*`` harvest and write consult provenance when applicable."""
    mode = str(admission_meta.get("admission_mode") or "").strip().lower()
    if mode != "consult":
        return None
    from .consult_lane import (
        parse_cdp_consult_harvest,
        provenance_from_cdp_harvest,
        write_consult_provenance,
    )
    from .window_log import worker_transcript_path

    executor: dict = {}
    transcript = worker_transcript_path(worker_thread)
    if transcript.is_file():
        text = transcript.read_text(encoding="utf-8", errors="replace")
        if "reviewer_model=" in text or "model=cdp/" in text:
            for line in text.splitlines():
                if line.startswith("seat=") or line.startswith("model="):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        executor[parts[0].strip()] = parts[1].strip()
    root_turns: list[dict] = []
    try:
        root_turns = await bus_client.fetch_turns(root_id)
    except Exception:  # noqa: BLE001 — root fetch must not abort B8
        logger.exception(
            "charter-runner cdp harvest root fetch failed root=%s", root_id
        )
    parsed = parse_cdp_consult_harvest(
        worker_turns,
        executor=executor,
        worker_thread=worker_thread,
        delivery_turns=root_turns,
        root_id=root_id,
    )
    if parsed is None or parsed.escape_path:
        return None
    if not substantive_reply_body(parsed.harvest_text):
        return None
    substrate = str(admission_meta.get("consultant_substrate") or "").strip()
    record = provenance_from_cdp_harvest(
        parsed,
        consultant_substrate=substrate,
    )
    if record is None:
        return None
    todo_ref = str(
        admission_meta.get("source_ref")
        or admission_meta.get("implement_source_ref")
        or ""
    ).strip()
    uri = write_consult_provenance(
        record,
        root_id=root_id,
        source_ref=todo_ref or None,
    )
    _maybe_commit_todo_keyed_record(
        admission_meta=admission_meta,
        record=record,
        root_id=root_id,
        todo_ref=todo_ref,
    )
    logger.info(
        "charter-runner cdp consult provenance root=%s window=%s model=%s verdict=%s",
        root_id,
        window_index,
        record.consultant_model,
        record.verdict,
    )
    return {
        "consult_thread": record.consult_thread,
        "verdict": record.verdict,
        "consultant_model": record.consultant_model,
        "consultant_effort": record.consultant_effort,
        "consultant_substrate": record.consultant_substrate,
        "cortex_mirror": uri,
    }


def _maybe_commit_todo_keyed_record(
    *,
    admission_meta: dict,
    record: object,
    root_id: str,
    todo_ref: str,
) -> None:
    """Call the sole todo-keyed writer when harvest has a full payload."""
    if not todo_ref:
        return
    todo = todo_ref if todo_ref.startswith("todo:") else f"todo:{todo_ref}"
    from implement_admission.consult_provenance_record import (
        commit_todo_consult_provenance,
    )

    evidence = getattr(record, "evidence_uri", None)
    payload = {
        "todo": todo,
        "consult_thread": getattr(record, "consult_thread", ""),
        "verdict": getattr(record, "verdict", ""),
        "adjudication_assertion_id": admission_meta.get("adjudication_assertion_id"),
        "consultant_model": getattr(record, "consultant_model", ""),
        "consultant_effort": getattr(record, "consultant_effort", None),
        "consultant_substrate": getattr(record, "consultant_substrate", ""),
        "archive_uri": str(admission_meta.get("archive_uri") or evidence or ""),
        "archive_sha256": str(admission_meta.get("archive_sha256") or ""),
        "satellite_execution_id": str(admission_meta.get("satellite_execution_id") or ""),
        "stargate_execution_id": str(admission_meta.get("stargate_execution_id") or ""),
        "written_by": "charter_runner.harvest_cdp",
        "charter_root_id": root_id,
    }
    commit_todo_consult_provenance(payload)


__all__ = ["maybe_harvest_cdp_consult_provenance"]
