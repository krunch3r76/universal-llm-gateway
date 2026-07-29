"""Render open propagation rows into charter scoreboard markdown."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

_OPEN_ROWS_HEADING = "## Open propagation rows"
_NEXT_SECTION = re.compile(r"^##\s+", re.MULTILINE)


def render_open_propagation_table(rows: list[dict[str, Any]]) -> str:
    """Render ledger projection rows as a markdown table with age."""
    lines = [
        _OPEN_ROWS_HEADING,
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
    """Replace or insert the open propagation rows section."""
    rendered = render_open_propagation_table(rows)
    if _OPEN_ROWS_HEADING in markdown:
        start = markdown.index(_OPEN_ROWS_HEADING)
        tail = markdown[start + len(_OPEN_ROWS_HEADING) :]
        end_match = _NEXT_SECTION.search(tail)
        end = start + len(_OPEN_ROWS_HEADING) + end_match.start() if end_match else len(markdown)
        return markdown[:start].rstrip() + "\n\n" + rendered + "\n" + markdown[end:].lstrip("\n")
    return markdown.rstrip() + "\n\n" + rendered + "\n"


def write_scoreboard_open_rows(path: Path, rows: list[dict[str, Any]]) -> None:
    """Read scoreboard markdown, patch open rows, write back."""
    text = path.read_text(encoding="utf-8")
    path.write_text(patch_scoreboard_open_rows(text, rows), encoding="utf-8")


__all__ = [
    "patch_scoreboard_open_rows",
    "render_open_propagation_table",
    "write_scoreboard_open_rows",
]
