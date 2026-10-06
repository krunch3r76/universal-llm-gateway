"""Scan house threads and unread TOC for open consults."""

from __future__ import annotations

from typing import Any, override

from maestro_induct.parse import is_consult_turn, parse_consult_turn
from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from . import _clients


class MaestroInductFetchConsultsHandler(BaseHandler):
    step_type = "maestro_induct_fetch_consults_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        resolve = _clients.step_output_json(context.outputs, "resolve")
        if resolve.get("invalid_root"):
            return StepOutput(raw="", json={"skipped": True})
        deadline = resolve.get("deadline_epoch")
        enum = _clients.step_output_json(context.outputs, "enumerate_lanes")
        lane_ids: list[str] = enum.get("lane_ids") or [str(resolve.get("root"))]
        house_threads = list(dict.fromkeys(lane_ids))
        open_consults: list[dict[str, Any]] = []
        errors: list[dict[str, Any]] = []
        truncated_meta: dict[str, Any] = {}

        for tid in house_threads:
            payload, status = await _clients.bus_get(
                "/turns",
                params={"thread": tid, "to": "web-anthropic", "unread": "true"},
                deadline_epoch=deadline,
            )
            if status == 504:
                err = {
                    "kind": "deadline_exceeded",
                    "section": "open_consults",
                    "threads": house_threads,
                }
                errors.append(err)
                break
            for turn in payload.get("turns") or []:
                if turn.get("read_at") is not None:
                    continue
                if not is_consult_turn(turn):
                    continue
                item = parse_consult_turn(turn)
                open_consults.append(item)
                if "error" in item:
                    errors.append({**item["error"], "section": "open_consults", "item": f"{tid}#{item.get('turn')}"})

        open_consults.sort(
            key=lambda c: (str(c.get("created_at") or ""), str(c.get("thread")), int(c.get("turn") or 0)),
            reverse=True,
        )

        outside: list[str] = []
        payload, status = await _clients.bus_get(
            "/turns/unread-toc",
            params={"to": "web-anthropic", "limit": 200},
            deadline_epoch=deadline,
        )
        if status == 504:
            err = {"kind": "deadline_exceeded", "section": "open_consults", "threads": ["toc"]}
            errors.append(err)
        else:
            hset = set(house_threads)
            if payload.get("truncated"):
                truncated_meta["open_consults_outside_house"] = True
            for row in payload.get("threads") or []:
                thread = str(row.get("thread"))
                subj = str(row.get("last_subject") or "")
                if thread in hset:
                    continue
                if not subj.startswith("CONSULT_PENDING"):
                    continue
                outside.append(f"{thread}#{row.get('latest_turn_number')}")
        return StepOutput(
            raw="",
            json={
                "open_consults": open_consults,
                "open_consults_outside_house": outside,
                "truncated_meta": truncated_meta,
                "errors": errors,
            },
        )
