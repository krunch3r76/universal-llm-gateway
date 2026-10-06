"""Backward-scan checkpoint turns on the house thread."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.parse import (
    CP_MAX_PAGES,
    CP_PAGE_SIZE,
    CP_TIP_LAST,
    next_checkpoint_page,
    parse_checkpoint_residue,
    pick_checkpoint_turn,
)
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductFetchCheckpointHandler(BaseHandler):
    step_type = "maestro_induct_fetch_checkpoint_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root"):
            return StepOutput(raw="", json={"skipped": True})
        root = str(resolve.get("root"))
        deadline = resolve.get("deadline_epoch")
        lowest = 10**9
        found_turn: int | None = None
        scanned_down_to = lowest

        params = {
            "thread": root,
            "last": CP_TIP_LAST,
            "compact": "true",
            "include_superseded": "true",
        }
        payload, status = await _clients.bus_get("/turns", params=params, deadline_epoch=deadline)
        if status == 504:
            err = {"kind": "deadline_exceeded", "section": "checkpoint"}
            return StepOutput(raw="", json={"error": err, "errors": [err]})
        turns = payload.get("turns") or []
        found_turn = pick_checkpoint_turn(turns)
        if turns:
            lowest = min(int(t.get("turn_number") or lowest) for t in turns)

        pages = 0
        while found_turn is None and pages < CP_MAX_PAGES:
            page = next_checkpoint_page(lowest_seen=lowest, page_size=CP_PAGE_SIZE)
            if page is None:
                break
            a, length = page
            params = {
                "thread": root,
                "after_turn": a,
                "last": length,
                "compact": "true",
                "include_superseded": "true",
            }
            payload, status = await _clients.bus_get(
                "/turns", params=params, deadline_epoch=deadline
            )
            pages += 1
            if status == 504:
                err = {
                    "kind": "deadline_exceeded",
                    "section": "checkpoint",
                    "scanned_down_to": lowest,
                }
                return StepOutput(raw="", json={"error": err, "errors": [err]})
            page_turns = payload.get("turns") or []
            if page_turns:
                # Cursor is the window floor. min(turn) stalls when a gap
                # makes every returned turn sit above that floor.
                lowest = a + 1
                scanned_down_to = lowest
                found_turn = pick_checkpoint_turn(page_turns)
            else:
                scanned_down_to = a + 1
                break

        if found_turn is None:
            if pages >= CP_MAX_PAGES:
                err = {
                    "kind": "checkpoint_scan_cap_reached",
                    "scanned_down_to": scanned_down_to,
                }
            else:
                err = {
                    "kind": "checkpoint_not_found",
                    "scanned_down_to": scanned_down_to if scanned_down_to < 10**9 else 1,
                }
            return StepOutput(raw="", json={"error": err, "errors": [err]})

        by, st = await _clients.bus_get(
            "/turns/by-number",
            params={"thread": root, "turn_number": str(found_turn)},
            deadline_epoch=deadline,
        )
        if st >= 400:
            err = {"kind": "checkpoint_fetch_failed", "message": str(by)}
            return StepOutput(raw="", json={"error": err, "errors": [err]})
        body = str(by.get("body") or "")
        residue = parse_checkpoint_residue(body)
        out = {
            "turn": found_turn,
            "subject": by.get("subject"),
            "created_at": by.get("created_at"),
            **residue,
        }
        return StepOutput(raw="", json=out)
