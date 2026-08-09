"""Bare-/new compose ensure + dual-id compose_attested emit (arc 6928).

Keeps ``chat_session_hygiene.goto_fresh_compose`` thin: Cowork/Chat ensure
plus Event Service observation live here so hygiene stays delete/classify-focused.
"""

from __future__ import annotations

from typing import Any


async def ensure_bare_new_compose(
    page,
    *,
    ensure_cowork_auto: bool = True,
    stargate_execution_id: str = "",
    satellite_execution_id: str = "",
) -> dict[str, Any]:
    """Run Chat/Cowork ensure on bare ``/new`` and emit compose attest both arms.

    Returns the ensure result dict. Caller raises structured setup errors when
    ``ok`` is false. Emit is best-effort and never overrides fail-closed ensure.
    """
    from claude_bundles.chat_cowork_mode import ensure_chat_compose, ensure_cowork_auto
    from claude_bundles.events_compose_attest import emit_compose_attested_from_result

    if ensure_cowork_auto:
        result = await ensure_cowork_auto(page)
    else:
        result = await ensure_chat_compose(page)
    emit_compose_attested_from_result(
        result,
        surface="bare_new",
        execution_id=stargate_execution_id,
        satellite_execution_id=satellite_execution_id,
    )
    return result
