"""13518 miss-cell expectations, retargeted onto relay.* after cursor_auto deletion.

The deleted ``test_cursor_auto_closeout_relay.py`` covered
``project_section2_table``. That projector now lives in
``relay/closeout_relay_project.py``; colon-less presence is
``relay/closeout_relay_cortex_fields.py``.

``select_closeout_relay_payload`` and ``finalize_relay_payload`` (the confer
fixture's status downgrade) left with ``cursor_auto``. Envelope status is
``resolve_measurement_status`` / ``resolve_relay_status``, which do not read
§2 cells. Specimen 022a7351 is locked here as: a complete wrapper that names
an off-git URI stays ``complete``.
"""

from __future__ import annotations

import json

from services.git_integration_worker.relay.closeout_relay_common import (
    relay_parse_failure_detected,
    resolve_measurement_status,
    resolve_relay_status,
)
from services.git_integration_worker.relay.closeout_relay_effects import (
    _extract_table_cell,
)
from services.git_integration_worker.relay.closeout_relay_project import (
    project_section2_table,
)

_OFFGIT_URI = (
    "cortex://notes/system/threads/friction-26462-cdp-explore/consult-brief.md"
)


def test_miss_cell_colonless_midline_backtick_mention_is_relay_miss() -> None:
    """Colon-less mid-line backtick mention is absence, not parse_failed.

    Breaks when a mid-line `` `field` `` is treated as a heading: the cell
    becomes parse_failed and a later status gate can downgrade a complete
    wrapper. Offset 0 after leading-decoration strip is the only presence.
    """
    prose = (
        "**status_claim:** partial\n"
        "**ac_verdict:** PASS — scoped work delivered\n"
        "**evidence:** pytest output quoted inline\n"
        "I did not fill `deltas_to_spec` this run.\n"
    )
    provenance = "workspaces://test/closeout-relay-miss-colonless-backtick"
    body, _status = project_section2_table(prose, provenance=provenance)
    cell = _extract_table_cell(body, "deltas_to_spec") or ""
    assert cell.startswith("relay could not locate"), cell
    assert relay_parse_failure_detected(body) is False


def test_miss_cell_colonless_midline_bold_mention_is_relay_miss() -> None:
    """Colon-less mid-line bold mention is absence, not parse_failed.

    Breaks when ``**field**`` inside a sentence counts as a heading and the
    empty rest is recorded as parse_failed.
    """
    prose = (
        "**status_claim:** partial\n"
        "**ac_verdict:** PASS — scoped work delivered\n"
        "**evidence:** pytest output quoted inline\n"
        "Note: skipped **deltas_to_spec** entirely.\n"
    )
    provenance = "workspaces://test/closeout-relay-miss-colonless-bold"
    body, _status = project_section2_table(prose, provenance=provenance)
    cell = _extract_table_cell(body, "deltas_to_spec") or ""
    assert cell.startswith("relay could not locate"), cell
    assert relay_parse_failure_detected(body) is False


def test_miss_cell_colonless_whole_line_backtick_is_not_relay_miss() -> None:
    """Whole-line colon-less backtick is presence; the next line stays unextracted.

    Breaks when offset-0 backtick headings are classified as absence and the
    authored line disappears from the relay table.
    """
    prose = (
        "**status_claim:** partial\n"
        "`deltas_to_spec`\n"
        "Diagnosis only.\n"
        "**ac_verdict:** PASS\n"
    )
    provenance = "workspaces://test/closeout-relay-miss-whole-line-backtick"
    body, _status = project_section2_table(prose, provenance=provenance)
    cell = _extract_table_cell(body, "deltas_to_spec") or ""
    assert cell.startswith("parse_failed"), cell
    assert "relay could not locate" not in cell.casefold()


def test_miss_cell_archived_open_forks_parenthetical_is_not_relay_miss() -> None:
    """Archived offset-0 colon-less headings stay presence, not a relay miss.

    Breaks when bullet or heading-only labels at column 0 are ignored and
    the cell reports that the relay could not locate the field.
    """
    provenance = "workspaces://test/closeout-relay-miss-archived-offset0"
    open_forks = project_section2_table(
        "**open_forks** (verbatim from spec):\n"
        "- lane stays unlanded\n"
        "- no fifth regex\n"
        "- offset 0 only\n"
        "- extraction unchanged\n",
        provenance=provenance,
    )[0]
    open_forks_cell = _extract_table_cell(open_forks, "open forks") or ""
    assert "relay could not locate" not in open_forks_cell.casefold()

    bullet = project_section2_table(
        "- **deltas_to_spec**\n",
        provenance=provenance,
    )[0]
    bullet_cell = _extract_table_cell(bullet, "deltas_to_spec") or ""
    assert "relay could not locate" not in bullet_cell.casefold()

    scope_delta = project_section2_table(
        "**status_claim:** partial\n"
        "**SCOPE DELTA** — none material\n"
        "**ac_verdict:** PASS\n",
        provenance=provenance,
    )[0]
    scope_cell = _extract_table_cell(scope_delta, "deltas_to_spec") or ""
    assert "relay could not locate" not in scope_cell.casefold()


def test_complete_wrapper_with_offgit_uri_measurement_stays_complete() -> None:
    """Specimen 022a7351 — off-git URI in the wrapper does not downgrade complete.

    Breaks when measurement re-reads §2 miss cells (the deleted
    ``finalize_relay_payload`` path) and flips a complete wrapper to
    ``relay_parse_failed`` because an off-git URI is present.
    """
    wrapper = json.dumps(
        {
            "schema_version": 1,
            "status": "complete",
            "summary": "dispatch auto-1181167b84c3",
            "files_created": [],
            "files_modified": [],
            "files_deleted": [],
            "effects": [],
            "files_offgit_produced": [_OFFGIT_URI],
            "capture_status": "partial",
            "effects_manifest": {"schema_version": 1, "surfaces": {}},
        }
    )
    measured = resolve_measurement_status(
        wrapper_text=wrapper,
        ledger_fallback="partial",
    )
    assert measured == "complete"
    assert _OFFGIT_URI in wrapper
    assert resolve_relay_status("ignored", measured) == "complete"
