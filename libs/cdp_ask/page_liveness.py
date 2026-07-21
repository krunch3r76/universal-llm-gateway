"""CDP page harvest and liveness observation for the dual-completion watcher."""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any


async def _page_harvest(cdp_url: str) -> dict[str, Any] | None:
    """Return full ``harvest_assistant`` state for *cdp_url*, or ``None`` on CDP failure."""
    from claude_bundles.chat_reply_wait import harvest_assistant
    from claude_bundles.skills_ui_panel import connect_cdp, pick_chat_page

    pw = None
    try:
        pw, _browser, ctx, _page0 = await connect_cdp(cdp_url)
        page = await pick_chat_page(ctx)
        return await harvest_assistant(page)
    except Exception:
        return None
    finally:
        if pw is not None:
            await pw.stop()


@dataclass
class LadderCallbacks:
    """Optional hooks for dual-completion ladder updates during ``run_execution``.

    ``on_liveness`` receives ``(streaming, stop, tool_pause, observed_at)`` after each
    successful ``harvest_assistant`` sample while the watcher runs. Advisory only — must
    not gate ``on_turn_idle``, ``on_content_proof``, or stall classification.
    """

    on_turn_idle: Callable[[], Awaitable[None]] | None = None
    on_content_proof: Callable[[str, str], Awaitable[None]] | None = None
    on_archiving: Callable[[], Awaitable[None]] | None = None
    on_liveness: Callable[[bool, bool, bool, float], Awaitable[None]] | None = None
    abort_check: Callable[[], Awaitable[bool]] | None = None


def page_idle_from_state(state: dict[str, Any]) -> bool:
    """Derive turn-idle from a harvest triple — any active signal means not idle."""
    return not (
        state.get("streaming")
        or state.get("stop")
        or state.get("tool_pause")
    )


async def content_proof_watcher(
    *,
    targets: list[tuple[Path, str]],
    cdp_url: str,
    callbacks: LadderCallbacks,
    min_bytes: int,
    sha256_file: Callable[[Path], str],
    poll_s: float = 2.0,
) -> None:
    """Poll CDP harvest, invoke liveness hooks, and advance the dual-completion ladder."""
    turn_idle_sent = False
    content_proof_sent = False
    while True:
        if callbacks.abort_check and await callbacks.abort_check():
            return
        state = await _page_harvest(cdp_url)
        if state is not None:
            if callbacks.on_liveness:
                await callbacks.on_liveness(
                    bool(state.get("streaming")),
                    bool(state.get("stop")),
                    bool(state.get("tool_pause")),
                    time.time(),
                )
            idle = page_idle_from_state(state)
        else:
            idle = False
        if idle and not turn_idle_sent and callbacks.on_turn_idle:
            turn_idle_sent = True
            await callbacks.on_turn_idle()
        if idle and turn_idle_sent and not content_proof_sent:
            for path, uri in targets:
                try:
                    if not path.is_file() or path.stat().st_size < min_bytes:
                        continue
                except OSError:
                    continue
                if callbacks.on_content_proof:
                    content_proof_sent = True
                    await callbacks.on_content_proof(uri, sha256_file(path))
                break
        await asyncio.sleep(poll_s)
