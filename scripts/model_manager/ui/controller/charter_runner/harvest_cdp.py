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
    executor.setdefault("reviewer_model", "cdp/opus-5")
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
    record = provenance_from_cdp_harvest(parsed)
    if record is None:
        return None
    uri = write_consult_provenance(record, root_id=root_id)
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
        "consultant_family": record.consultant_family,
        "consultant_substrate": record.consultant_substrate,
        "cortex_mirror": uri,
        "consultant_model": record.consultant_model,
    }


__all__ = ["maybe_harvest_cdp_consult_provenance"]
