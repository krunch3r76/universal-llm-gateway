#!/usr/bin/env python3
"""Live-fire verification of the ``ensure_cowork_auto`` selector fix (a:31319).

Opens a brand-new isolated tab (never touches an existing live CSE session),
lands on ``https://claude.ai/new``, and calls the real ``ensure_cowork_auto``
end-to-end — the same call every CDP dispatch makes before Start task. Prints
the full result so the fix can be confirmed against production, not just the
hermetic test fixtures.

Run on Jupiter (CDP host):
    ~/.venvs/universal/bin/python scripts/cortex/cdp_approval_census.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO / "libs"))

from claude_bundles.chat_cowork_mode import ensure_cowork_auto  # noqa: E402
from claude_bundles.compose_attest import (  # noqa: E402
    compose_mode_fingerprint,
    cowork_auto_refuse_reason,
)
from claude_bundles.skills_ui_panel import DEFAULT_CDP_URL, connect_cdp  # noqa: E402


async def main() -> int:
    pw, _browser, ctx, _existing_page = await connect_cdp(DEFAULT_CDP_URL)
    page = await ctx.new_page()  # isolated — never touch a live CSE tab
    try:
        await page.goto("https://claude.ai/new", wait_until="domcontentloaded", timeout=30000)
        result = await ensure_cowork_auto(page)
        fp = await compose_mode_fingerprint(page)
        refuse = cowork_auto_refuse_reason(fp)
        report = {
            "url": page.url,
            "ensure_cowork_auto_result": result,
            "final_fingerprint": fp,
            "cowork_auto_refuse_reason": refuse,
            "verdict": "PASS" if bool(result.get("ok")) and refuse is None else "FAIL",
        }
        print(json.dumps(report, indent=2))
        return 0 if report["verdict"] == "PASS" else 1
    finally:
        await page.close()
        await pw.stop()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
