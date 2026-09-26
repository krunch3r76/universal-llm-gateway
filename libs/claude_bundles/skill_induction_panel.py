"""Poll the open Context grid until induction slugs are listed.

A single scrape 1.5s after ``Use the {slug} skill`` reads an empty grid.
The row is painted after that turn is processed (thread 12829: Context
header already open, ``observed=[]``, assistant reply seconds later).
A still-closed header is not waited out — ``scrape_loaded_skills`` raises
``ChatContextSkillsError`` and this wait lets that refusal through.
"""

from __future__ import annotations

import time

from playwright.async_api import Page

from claude_bundles.chat_context_skills import LoadedSkillsReport, scrape_loaded_skills
from claude_bundles.cowork_skill_delivery import (
    SkillDeliveryError,
    induction_panel_ready,
)

# Witness 12829 scraped an empty open grid well before the induction reply.
# Replies on the prior witnesses landed about 9–21s after the user line.
INDUCTION_PANEL_TIMEOUT_S = 45.0
INDUCTION_PANEL_INTERVAL_MS = 1000


async def wait_for_induction_panel(
    page: Page,
    required: list[str],
    *,
    timeout_s: float = INDUCTION_PANEL_TIMEOUT_S,
    interval_ms: int = INDUCTION_PANEL_INTERVAL_MS,
) -> LoadedSkillsReport:
    """Return the first open-grid scrape that lists every required slug.

    Reads immediately, then again every ``interval_ms`` until ``timeout_s``.
    An open grid with the slug missing is retried. A collapsed Context
    header raises on the first read and is not retried.
    """
    deadline = time.monotonic() + timeout_s
    started = time.monotonic()
    last_observed: list[str] = []
    attempts = 0
    while True:
        attempts += 1
        report = await scrape_loaded_skills(page)
        last_observed = list(report.skills)
        if induction_panel_ready(required, last_observed):
            return report
        if time.monotonic() >= deadline:
            elapsed = time.monotonic() - started
            raise SkillDeliveryError(
                "induction Context → Skills panel not ready before work prompt: "
                f"required={required} observed={last_observed} "
                f"open_grid_reads={attempts} elapsed_s={elapsed:.1f} — fail closed "
                "(decision:web-seat-skill-body-delivery)"
            )
        await page.wait_for_timeout(interval_ms)
