"""impresario_ear_gather_v1 — tape, JSONL, continuity, bounded CDP enrichment."""

from __future__ import annotations

import asyncio
import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

_CDP_BUDGET_S = 3.0


async def gather_impresario_ear_context(
    *,
    root_thread: str,
    surface: str = "cursor",
    cdp_probe: Any | None = None,
    tape_fetch: Any | None = None,
) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "root_thread": root_thread,
        "surface": surface,
        "tape": {},
        "jsonl": {},
        "continuity": {},
        "cdp": {"attempted": False, "ok": False},
    }
    if tape_fetch is not None:
        try:
            bundle["tape"] = await asyncio.to_thread(
                tape_fetch, root_thread, harvest=True, channel="all"
            )
        except Exception as exc:  # noqa: BLE001
            bundle["tape"] = {"error": type(exc).__name__}
    else:
        try:
            from systems.continuity.tape_read import fetch_tape_envelope

            bundle["tape"] = await fetch_tape_envelope(
                root_thread, harvest=True, channel="all"
            )
        except Exception as exc:  # noqa: BLE001
            bundle["tape"] = {"error": type(exc).__name__}

    try:
        from bus_watch.ide_budget import measure_ide_tab

        bundle["jsonl"] = await asyncio.to_thread(
            measure_ide_tab, root_thread, register="attended"
        )
    except Exception as exc:  # noqa: BLE001
        bundle["jsonl"] = {"error": type(exc).__name__}

    bundle["continuity"] = {"checkpoint_present": True}

    probe = cdp_probe
    if probe is None:
        async def _default_probe() -> dict[str, Any]:
            from cursor_bridge.glass_services_js import probe_glass_composer_budget_js

            return await asyncio.wait_for(
                asyncio.to_thread(probe_glass_composer_budget_js, root_thread),
                timeout=_CDP_BUDGET_S,
            )

        probe = _default_probe

    bundle["cdp"]["attempted"] = True
    try:
        cdp_out = await probe()
        bundle["cdp"] = {"attempted": True, "ok": True, "payload": cdp_out}
    except Exception as exc:  # noqa: BLE001
        bundle["cdp"] = {
            "attempted": True,
            "ok": False,
            "error": type(exc).__name__,
        }
    return bundle


class ImpresarioEarGatherHandler(BaseHandler):
    step_type = "impresario_ear_gather_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        outputs = getattr(context, "outputs", {}) or {}
        resolve = (outputs.get("resolve") or {}).json or {}
        if resolve.get("skip"):
            return StepOutput(raw="{}", json={"skip": True})
        root = str(resolve.get("root_thread") or "")
        options = dict(getattr(context, "options", {}) or {})
        surface = str(options.get("surface") or "cursor")
        cdp_probe = options.get("_cdp_probe")
        tape_fetch = options.get("_tape_fetch")
        payload = await gather_impresario_ear_context(
            root_thread=root,
            surface=surface,
            cdp_probe=cdp_probe,
            tape_fetch=tape_fetch,
        )
        return StepOutput(raw=json.dumps(payload, default=str), json=payload)
