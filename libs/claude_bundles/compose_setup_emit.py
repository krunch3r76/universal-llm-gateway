"""Bare-/new compose ensure + dual-id compose_attested emit (arc 6928).

Keeps ``chat_session_hygiene.goto_fresh_compose`` thin: compose v2 ensure
(readiness + Permission Auto) plus Event Service observation live here so
hygiene stays delete/classify-focused.
"""

from __future__ import annotations

from typing import Any

_LAST_BARE_COMPOSE_ENSURE: dict[str, Any] | None = None


def last_bare_compose_ensure() -> dict[str, Any] | None:
    """Last bare ``/new`` ensure result (for runner quota + model-chip readback)."""
    return _LAST_BARE_COMPOSE_ENSURE


def compose_runner_quota_fields() -> dict[str, Any]:
    """Project quota fields from the last bare compose ensure onto runner payloads.

    Always emits ``quota_weekly_pct`` (``None`` when no weekly-limit banner was
    observed or no bare compose ensure ran) and ``quota_near`` (``bool``) so
    ``cdp_ask.runner`` result payloads carry explicit JSON ``null``/``false``.
    """
    snap = (
        _LAST_BARE_COMPOSE_ENSURE if isinstance(_LAST_BARE_COMPOSE_ENSURE, dict) else {}
    )
    pct = snap.get("quota_weekly_pct")
    quota_near = bool(
        snap.get("step") == "quota_near" or snap.get("failure_class") == "quota_near"
    )
    return {
        "quota_weekly_pct": pct if pct is not None else None,
        "quota_near": quota_near,
    }


async def ensure_bare_new_compose(
    page,
    *,
    ensure_cowork_auto: bool = True,
    model_request: str | None = None,
    stargate_execution_id: str = "",
    satellite_execution_id: str = "",
) -> dict[str, Any]:
    """Run compose v2 ensure on bare ``/new`` and emit compose attest both arms.

    Returns the ensure result dict. Caller raises structured setup errors when
    ``ok`` is false. Emit is best-effort and never overrides fail-closed ensure.

    When ``model_request`` is set (wire family or ``family-effort``), the
    default ensure path runs ``ensure_compose_model_chip`` with readback before
    send.
    """
    global _LAST_BARE_COMPOSE_ENSURE

    from claude_bundles.chat_cowork_mode import ensure_chat_compose, ensure_cowork_auto
    from claude_bundles.chat_model_match import parse_model_request
    from claude_bundles.events_compose_attest import emit_compose_attested_from_result

    chip_family: str | None = None
    chip_effort: str | None = None
    if model_request:
        chip_family, chip_effort = parse_model_request(model_request)

    if ensure_cowork_auto:
        result = await ensure_cowork_auto(
            page,
            model_request=chip_family,
            model_effort=chip_effort,
        )
    else:
        result = await ensure_chat_compose(page)
    _LAST_BARE_COMPOSE_ENSURE = result
    emit_compose_attested_from_result(
        result,
        surface="bare_new",
        execution_id=stargate_execution_id,
        satellite_execution_id=satellite_execution_id,
    )
    return result
