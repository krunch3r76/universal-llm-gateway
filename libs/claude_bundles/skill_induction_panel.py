"""Poll the open Context grid until induction slugs are listed post-use.

Cowork paints the Context → Skills group only after Claude executes a
``Use the {slug} skill`` or ``/<slug>`` line (a:38612). Pre-use scrapes
return ``pre_use_skills_rail`` (open Context, no Skills heading yet) and
are retried until slugs appear or the deadline passes.

A still-closed Context header is not waited out — ``scrape_loaded_skills``
raises ``ChatContextSkillsError`` and this wait lets that refusal through.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from playwright.async_api import Page

from claude_bundles.chat_context_skills import LoadedSkillsReport, scrape_loaded_skills
from claude_bundles.cowork_skill_delivery import (
    SkillDeliveryError,
    induction_panel_ready,
)
from claude_bundles.cse_idle_probe import CSE_LIVENESS_PROBE_JS
from claude_bundles.events_skill_delivery import emit_skill_fetch_decision

# Witness 12829 scraped an empty open grid well before the induction reply.
# Replies on the prior witnesses landed about 9–21s after the user line.
INDUCTION_PANEL_TIMEOUT_S = 45.0
INDUCTION_PANEL_INTERVAL_MS = 1000


def _grid_activity_key(report: LoadedSkillsReport) -> tuple[tuple[str, ...], str, bool]:
    return (
        tuple(report.skills),
        report.raw_section_text or "",
        report.skills_heading_found,
    )


async def _sample_page_liveness(page: Page) -> dict:
    """Harvest triple for idle-budget extension and outer generate liveness."""
    try:
        value = await page.evaluate(CSE_LIVENESS_PROBE_JS)
    except Exception:  # noqa: BLE001 — liveness is advisory; grid scrape is authority
        return {
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "url": page.url or "",
        }
    if not isinstance(value, dict):
        return {
            "streaming": False,
            "stop": False,
            "tool_pause": False,
            "url": page.url or "",
        }
    return value


async def wait_for_induction_panel(
    page: Page,
    required: list[str],
    *,
    timeout_s: float = INDUCTION_PANEL_TIMEOUT_S,
    interval_ms: int = INDUCTION_PANEL_INTERVAL_MS,
    on_harvest: Callable[[dict], Awaitable[None]] | None = None,
) -> LoadedSkillsReport:
    """Return the first open-grid scrape that lists every required slug.

    Polls every ``interval_ms`` until every required slug appears. ``timeout_s``
    is an *idle* budget: it re-arms while the page shows generation activity
    (``streaming`` / ``stop`` / ``tool_pause``) or the open Context grid text
    changes. Only continuous quiet with the rail still pre-use may emit
    ``pre_use_timeout``; post-use quiet with missing slugs emits
    ``missing_required_slugs``.
    """
    idle_deadline = time.monotonic() + max(timeout_s, 0.0)
    started = time.monotonic()
    last_observed: list[str] = []
    last_report: LoadedSkillsReport | None = None
    saw_post_use_rail = False
    attempts = 0
    prev_grid_key: tuple[tuple[str, ...], str, bool] | None = None
    while True:
        attempts += 1
        report = await scrape_loaded_skills(page)
        liveness = await _sample_page_liveness(page)
        if on_harvest is not None:
            await on_harvest(
                {
                    "streaming": bool(liveness.get("streaming")),
                    "stop": bool(liveness.get("stop")),
                    "tool_pause": bool(liveness.get("tool_pause")),
                    "liveness_observed_at": time.time(),
                    "url": str(liveness.get("url") or page.url or ""),
                }
            )
        last_report = report
        last_observed = list(report.skills)
        if report.skills_rail_post_use:
            saw_post_use_rail = True
        if induction_panel_ready(required, last_observed):
            _emit_fetch_decisions(
                required,
                observed=last_observed,
                decision="in_context",
                reason="",
            )
            return report
        grid_key = _grid_activity_key(report)
        page_active = bool(
            liveness.get("streaming")
            or liveness.get("stop")
            or liveness.get("tool_pause")
            or (prev_grid_key is not None and grid_key != prev_grid_key)
        )
        prev_grid_key = grid_key
        if page_active:
            idle_deadline = time.monotonic() + max(timeout_s, 0.0)
        if time.monotonic() >= idle_deadline:
            elapsed = time.monotonic() - started
            timeout_reason = (
                "missing_required_slugs" if saw_post_use_rail else "pre_use_timeout"
            )
            _emit_fetch_decisions(
                required,
                observed=last_observed,
                decision="skipped",
                reason=timeout_reason,
            )
            heading = (
                last_report.skills_heading_found if last_report is not None else False
            )
            raise SkillDeliveryError(
                "induction Context → Skills receipt not ready after Use/<slug> "
                f"activation: required={required} observed={last_observed} "
                f"skills_heading_found={heading} post_use_rail={saw_post_use_rail} "
                f"open_grid_reads={attempts} elapsed_s={elapsed:.1f} "
                f"reason={timeout_reason} — fail closed "
                "(decision:web-seat-skill-body-delivery; a:38612)"
            )
        await page.wait_for_timeout(interval_ms)


def _emit_fetch_decisions(
    required: list[str],
    *,
    observed: list[str],
    decision: str,
    reason: str,
) -> None:
    """One ``cdp.skill.fetch_decision`` row per required slug. Never raises."""
    for slug in required:
        token = str(slug).strip()
        if not token:
            continue
        emit_skill_fetch_decision(
            ref=token,
            decision=decision,
            reason=reason,
            required=list(required),
            observed=list(observed),
        )
