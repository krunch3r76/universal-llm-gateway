"""Post-submit Context → Skills receipt (non-gating observation).

Composer chips gate submit (``attach_skills_verified``). The session right-rail
**Context → Skills** list is the post-submit receipt — observable only after
send, and absent on bare ``/new`` before the first turn. This module records
observed vs required without failing the submit path.
"""

from __future__ import annotations

import logging
from typing import Any

from playwright.async_api import Page

from claude_bundles.chat_context_skills import LoadedSkillsReport, scrape_loaded_skills
from claude_bundles.events_skill_delivery import emit_skill_context_loaded

logger = logging.getLogger(__name__)


def _merge_compose_receipt(out: dict[str, Any], compose_receipt: dict[str, Any] | None) -> None:
    if not compose_receipt:
        return
    for key in ("model_ran", "approval_mode", "current_model"):
        val = compose_receipt.get(key)
        if val:
            out[key] = val


def _code_rail_scrape_optional(exc: BaseException) -> bool:
    from claude_bundles.chat_context_skills import ChatContextSkillsError

    if not isinstance(exc, ChatContextSkillsError):
        return False
    msg = str(exc)
    return "not on the rail" in msg or "still collapsed" in msg or "no toggle" in msg


async def record_post_submit_skills_receipt(
    page: Page,
    *,
    required: list[str],
    execution_id: str = "",
    satellite_execution_id: str = "",
    settle_ms: int = 1500,
    claude_surface: str | None = None,
    compose_receipt: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Best-effort scrape Context → Skills; emit observation; never raise.

    Missing required slugs annotate the event (``ok=False``) — they do **not**
    abort the dispatch. Callers may surface the returned ``missing`` in closeout.
    """
    req = [str(s).strip() for s in required if str(s).strip()]
    out: dict[str, Any] = {
        "ok": True,
        "required": req,
        "observed": [],
        "missing": [],
        "context_found": False,
        "skills_heading_found": False,
        "pre_use_skills_rail": False,
        "skipped": False,
        "error": None,
    }
    _merge_compose_receipt(out, compose_receipt)
    if not req:
        out["skipped"] = True
        return out
    try:
        if settle_ms > 0:
            await page.wait_for_timeout(settle_ms)
        report: LoadedSkillsReport = await scrape_loaded_skills(page)
    except Exception as exc:  # noqa: BLE001 — receipt must never fail submit
        if (claude_surface or "").strip().lower() == "code" and _code_rail_scrape_optional(
            exc
        ):
            logger.info(
                "post-submit skills receipt skipped on /code (Context rail unavailable): %s",
                exc,
            )
            out["ok"] = True
            out["skipped"] = True
            out["rail_unavailable"] = True
            out["observed"] = []
            out["missing"] = []
            emit_skill_context_loaded(
                ok=True,
                required=req,
                observed=[],
                missing=[],
                execution_id=execution_id,
                satellite_execution_id=satellite_execution_id,
                context_found=False,
                skills_heading_found=False,
            )
            return out
        logger.info(
            "post-submit skills receipt scrape failed closed-soft: %s",
            exc,
        )
        out["ok"] = False
        out["error"] = f"{type(exc).__name__}: {exc}"
        emit_skill_context_loaded(
            ok=False,
            required=req,
            observed=[],
            missing=list(req),
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
            error=out["error"],
        )
        return out

    observed = list(report.skills)
    missing = list(report.missing(req))
    out.update(
        {
            "ok": not missing and report.context_found and report.skills_rail_post_use,
            "observed": observed,
            "missing": missing,
            "context_found": report.context_found,
            "skills_heading_found": report.skills_heading_found,
            "pre_use_skills_rail": report.pre_use_skills_rail,
        }
    )
    emit_skill_context_loaded(
        ok=bool(out["ok"]),
        required=req,
        observed=observed,
        missing=missing,
        execution_id=execution_id,
        satellite_execution_id=satellite_execution_id,
        context_found=report.context_found,
        skills_heading_found=report.skills_heading_found,
    )
    return out
