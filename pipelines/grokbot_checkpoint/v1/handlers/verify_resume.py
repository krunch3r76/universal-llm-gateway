"""Read-only resume checks. No fs writes and no bus posts."""

from __future__ import annotations

import json
import re
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._clients import bus_get, file_sha256, fs_of


def _harvest_sha(body: str) -> str | None:
    match = re.search(r"messages_sha256:([0-9a-f]+)", body)
    return match.group(1) if match else None


def _harvest_provenance(body: str) -> str | None:
    match = re.search(r"provenance:([a-z]+)", body)
    return match.group(1) if match else None


async def run_verify_resume(
    options: dict[str, Any],
    fs: Any,
    *,
    get: Any = None,
) -> dict[str, Any]:
    get = get or bus_get
    checks: dict[str, bool] = {}
    card_uri = str(options.get("card_uri") or "")
    packet_sha = str(options.get("expected_card_sha256") or "")
    card_raw = fs.read(card_uri)
    live = file_sha256(card_raw) if card_raw is not None else ""
    body = card_raw.decode("utf-8") if card_raw else ""
    later = False
    if live != packet_sha and body:
        for line in body.splitlines():
            cells = [c.strip() for c in line.strip("|").split("|")]
            if len(cells) >= 4 and cells[3] == packet_sha:
                later = True
    checks["card_sha"] = live == packet_sha or later
    snap_uri = str(options.get("snapshot_uri") or "")
    snap_raw = fs.read(snap_uri) if snap_uri else None
    snap_live = file_sha256(snap_raw) if snap_raw else ""
    packet_snap = str(
        options.get("snapshot_sha256") or options.get("envelope_sha256") or ""
    )
    checks["snapshot_sha"] = snap_live == packet_snap
    messages_sha = ""
    provenance = ""
    if snap_raw:
        try:
            parsed = json.loads(snap_raw.decode("utf-8"))
        except json.JSONDecodeError:
            parsed = {}
        meta = parsed.get("meta") if isinstance(parsed, dict) else {}
        if isinstance(meta, dict):
            messages_sha = str(meta.get("messages_sha256") or "")
            provenance = str(meta.get("provenance") or "")
            capture = meta.get("capture")
            if isinstance(capture, dict) and str(capture.get("ref") or "").startswith(
                ("cortex://", "workspaces://")
            ):
                rel = str(capture["ref"])
                rel = rel.removeprefix("cortex://").removeprefix("workspaces://")
                cap_raw = fs.read(rel)
                checks["capture_sha"] = (
                    cap_raw is not None
                    and file_sha256(cap_raw) == capture.get("sha256")
                )
            else:
                checks["capture_sha"] = True
    lane = str(options.get("verify_lane") or "")
    listing, _status = await get(f"/threads/{lane}/turns")
    turns = listing.get("turns") if isinstance(listing, dict) else []
    cp_body = ""
    receipt = False
    successor = str(options.get("successor") or options.get("bus_identity") or "")
    fact_turn = int(options.get("fact_turn") or 0)
    if isinstance(turns, list):
        for turn in turns:
            if turn.get("subject") == "CHECKPOINT":
                cp_body = str(turn.get("body") or "")
            num = int(turn.get("turn_number") or 0)
            if (
                turn.get("subject") == "RECEIPT"
                and str(turn.get("from") or "") == successor
                and num > fact_turn
            ):
                receipt = True
    harvest = _harvest_sha(cp_body)
    checks["harvest_sha"] = bool(messages_sha) and harvest == messages_sha
    checks["provenance"] = bool(provenance) and _harvest_provenance(cp_body) == provenance
    checks["receipt"] = receipt
    return {"refused": None, "checks": checks, "writes": 0, "posts": 0}


class GrokbotCheckpointVerifyResumeHandler(BaseHandler):
    step_type = "grokbot_checkpoint_verify_resume_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options = dict(getattr(context, "options", {}) or {})
        payload = await run_verify_resume(options, fs_of(context))
        return StepOutput(raw="", json=payload)
