"""Post-timeout result retrieval for cursorbuild dispatches."""

from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any, Literal

from cursorbuild.constants import _SIDECAR_DIR
from cursorbuild.envelope import _envelope_rejected
from cursorbuild.fetch_result_decode import (
    first_record,
    last_record,
    result_envelope,
    started_metadata,
    summary,
    terminal_age_seconds,
    text_result,
)

_VALID_DISPATCH_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_RETENTION_SECONDS = int(
    os.getenv("CURSORBUILD_RESULT_RETENTION_SECONDS", str(7 * 24 * 60 * 60))
)


def _read_sidecar_records(sidecar_path: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with open(sidecar_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return records


async def fetch_result_op(
    dispatch_id: str,
    format: Literal["json", "text", "summary", "signals"] = "json",
) -> dict[str, Any]:
    if not _VALID_DISPATCH_ID.match(dispatch_id):
        return _envelope_rejected(
            dispatch_id,
            "read_only",
            "",
            None,
            None,
            "bad_dispatch_id",
            f"invalid dispatch_id: {dispatch_id!r}",
        )
    sidecar_path = str(_SIDECAR_DIR / f"{dispatch_id}.ndjson")
    if not os.path.isfile(sidecar_path):
        return _envelope_rejected(
            dispatch_id,
            "read_only",
            "",
            None,
            None,
            "result_not_found",
            f"no sidecar for dispatch_id={dispatch_id!r}",
        )
    records = await asyncio.get_running_loop().run_in_executor(
        None, _read_sidecar_records, sidecar_path
    )
    exit_record = last_record(records, "exit")
    if exit_record is None:
        return _envelope_rejected(
            dispatch_id,
            "read_only",
            "",
            None,
            None,
            "result_pending",
            "dispatch has no terminal exit record yet",
        )
    age = terminal_age_seconds(exit_record, sidecar_path)
    if age > _RETENTION_SECONDS:
        return _envelope_rejected(
            dispatch_id,
            "read_only",
            "",
            None,
            None,
            "result_expired",
            f"sidecar older than retention ({_RETENTION_SECONDS}s)",
        )
    started = first_record(records, "started")
    started_meta = started_metadata(started)
    envelope = result_envelope(
        dispatch_id=dispatch_id,
        sidecar_path=sidecar_path,
        records=records,
        started_meta=started_meta,
        exit_record=exit_record,
        result_format=format,
        retention_seconds=_RETENTION_SECONDS,
    )
    if format == "summary":
        return summary(envelope)
    if format == "text":
        return text_result(envelope)
    return envelope
