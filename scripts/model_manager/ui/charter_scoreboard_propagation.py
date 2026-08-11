"""Render open propagation *obligation* rows into charter scoreboard markdown.

This table is the owed-restart worklist (open events), not a liveness board.
Absence of an open row means no obligation *attempt* was recorded — not that
a restart never ran and not that a ``code_ref`` is unserved. Incidental /
manage-path restarts outside ``contract: propagate`` leave no row by design
(posture A / ``7065#24``). Absence ≠ live and absence ≠ unserved; a frozen
failed ledger event is invisible here and must not be inferred as current
not-live. For ``is code_ref live on service?`` use
``charter_runner_store.propagation_liveness.observe_code_ref_live``.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_OPEN_ROWS_HEADING = "## Open propagation obligations"
_NEXT_SECTION = re.compile(r"^##\s+", re.MULTILINE)
_NO_CONSUMER_NOTE = "no_unprompted_settle_consumer"
# Prior heading kept so patch_scoreboard can replace legacy sections in place.
_LEGACY_OPEN_ROWS_HEADING = "## Open propagation rows"


def unprompted_settle_consumer_services(*, fleet_attached: bool = False) -> frozenset[str]:
    """Services with an unprompted post-restart settle hook (GIW drain + lifecycle wrapper)."""
    from scripts.model_manager.ui.api_dispatch import VALID_SERVICES
    from scripts.model_manager.ui.controller.restart_window_store import (
        FLEET_WINDOW_SERVICES,
    )

    consumers = set(VALID_SERVICES)
    if fleet_attached:
        consumers.update(FLEET_WINDOW_SERVICES)
    return frozenset(consumers)


def service_has_unprompted_settle_consumer(
    service: str, *, fleet_attached: bool = False
) -> bool:
    """True when *service* has GIW drain and/or lifecycle-wrapper settle coverage."""
    return service in unprompted_settle_consumer_services(fleet_attached=fleet_attached)


def render_open_propagation_table(rows: list[dict[str, Any]]) -> str:
    """Render open obligation rows as a markdown table with harvest age."""
    lines = [
        _OPEN_ROWS_HEADING,
        "",
        "Open *obligations* only (owed sync_restart events) — not current liveness. "
        "Absence of a row ⇒ no obligation attempt recorded (incidental/manage-path "
        "restarts outside `contract: propagate` leave none); absence ≠ unserved. "
        "Liveness answers cite `observe_code_ref_live` (process probe + "
        "`code_ref_relation`), never this table, a missing SHA, or a frozen "
        "`status=failed` row. "
        "READ-CAVEAT: open rows with `defer=version_superseded_by_newer_code` "
        "(ancestry-satisfied, including legacy premature proof stamps) stay "
        "listed — do not close them from the proof string; re-ask "
        "`observe_code_ref_live`.",
        "",
        "Schema: `service · action · code_ref · safe_window · age_in_harvests · proof_class · minting thread/turn`.",
        "",
        "| service | action | code_ref | safe_window | age | proof_class | minted |",
        "|---|---|---|---|---|---|---|",
    ]
    if not rows:
        lines.append("| _none open_ | | | | | | |")
    else:
        for row in rows:
            minted = ""
            thread = row.get("mint_thread")
            turn = row.get("mint_turn")
            if thread:
                minted = str(thread)
                if turn is not None:
                    minted = f"{minted} t{turn}"
            defer = row.get("defer_reason")
            if defer:
                minted = f"{minted}; defer={defer}" if minted else f"defer={defer}"
            service = str(row.get("service", ""))
            if not service_has_unprompted_settle_consumer(service):
                minted = (
                    f"{minted}; {_NO_CONSUMER_NOTE}"
                    if minted
                    else _NO_CONSUMER_NOTE
                )
            lines.append(
                "| {service} | sync_restart | {code_ref} | {safe_window} | {age} | {proof_class} | {minted} |".format(
                    service=row.get("service", ""),
                    code_ref=row.get("code_ref", ""),
                    safe_window=row.get("safe_window", ""),
                    age=row.get("age_in_harvests", 0),
                    proof_class=row.get("proof_class", ""),
                    minted=minted,
                )
            )
    return "\n".join(lines)


def patch_scoreboard_open_rows(markdown: str, rows: list[dict[str, Any]]) -> str:
    """Replace or insert the open-obligation section (legacy heading accepted)."""
    rendered = render_open_propagation_table(rows)
    for heading in (_OPEN_ROWS_HEADING, _LEGACY_OPEN_ROWS_HEADING):
        if heading in markdown:
            start = markdown.index(heading)
            tail = markdown[start + len(heading) :]
            end_match = _NEXT_SECTION.search(tail)
            end = start + len(heading) + end_match.start() if end_match else len(markdown)
            return (
                markdown[:start].rstrip()
                + "\n\n"
                + rendered
                + "\n"
                + markdown[end:].lstrip("\n")
            )
    return markdown.rstrip() + "\n\n" + rendered + "\n"


def write_scoreboard_open_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    """Read scoreboard markdown, patch open rows, write back."""
    text = path.read_text(encoding="utf-8")
    path.write_text(patch_scoreboard_open_rows(text, rows), encoding="utf-8")


__all__ = [
    "patch_scoreboard_open_rows",
    "render_open_propagation_table",
    "service_has_unprompted_settle_consumer",
    "unprompted_settle_consumer_services",
    "write_scoreboard_open_rows",
]
