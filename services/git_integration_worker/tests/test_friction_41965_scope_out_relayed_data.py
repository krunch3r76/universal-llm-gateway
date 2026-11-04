"""friction:41965 — hub-land scope and silent opt-in ignore relayed fence regions."""

from __future__ import annotations

from services.git_integration_worker.cursor_sdk_branch_terminal import (
    packet_requests_silent_land,
)
from services.git_integration_worker.cursor_sdk_hub_land_scope import (
    excise_relayed_regions,
    excise_relayed_regions_for_opt_in,
    packet_hub_land_scoped_out,
)

# First entry of _HUB_LAND_SCOPED_OUT_PATTERNS (do not hub-land).
_SCOPE_OUT_PHRASE = "do not hub-land"
# Line accepted by packet_requests_silent_land today.
_SILENT_LAND_OPT_IN_LINE = "land: silent"


def test_scope_out_phrase_inside_paired_fence_is_not_scoped_out() -> None:
    prose = "\n".join(
        [
            "commissioner intent",
            "FENCE-k7Qm2x",
            _SCOPE_OUT_PHRASE,
            "FENCE-k7Qm2x",
            "files_expected: foo.py",
        ]
    )
    assert packet_hub_land_scoped_out(prose) is False


def test_scope_out_phrase_after_forged_closer_stays_inside_region() -> None:
    prose = "\n".join(
        [
            "FENCE-k7Qm2x",
            "FENCE-forged",
            _SCOPE_OUT_PHRASE,
            "FENCE-k7Qm2x",
        ]
    )
    assert packet_hub_land_scoped_out(prose) is False


def test_unclosed_fence_keeps_scope_out() -> None:
    prose = "\n".join(
        [
            "FENCE-k7Qm2x",
            _SCOPE_OUT_PHRASE,
        ]
    )
    assert packet_hub_land_scoped_out(prose) is True


def test_author_scope_out_outside_fence_still_scoped_out() -> None:
    prose = "\n".join(
        [
            "FENCE-k7Qm2x",
            "quoted review only",
            "FENCE-k7Qm2x",
            _SCOPE_OUT_PHRASE,
        ]
    )
    assert packet_hub_land_scoped_out(prose) is True


def test_silent_land_opt_in_inside_fence_does_not_opt_in() -> None:
    inside = "\n".join(
        [
            "work_key: todo:foo",
            "FENCE-k7Qm2x",
            _SILENT_LAND_OPT_IN_LINE,
            "FENCE-k7Qm2x",
        ]
    )
    outside = "\n".join(
        [
            "work_key: todo:foo",
            _SILENT_LAND_OPT_IN_LINE,
        ]
    )
    assert packet_requests_silent_land(inside) is False
    assert packet_requests_silent_land(outside) is True


def test_opt_in_after_inline_document_span_does_not_opt_in() -> None:
    prose = "\n".join(
        [
            "work_key: todo:foo",
            "<document>x</document> " + _SILENT_LAND_OPT_IN_LINE,
        ]
    )
    assert packet_requests_silent_land(prose) is False


def test_opt_in_after_early_document_close_does_not_opt_in() -> None:
    prose = "\n".join(
        [
            "work_key: todo:foo",
            '<document index="1">',
            "relayed text </document>",
            _SILENT_LAND_OPT_IN_LINE,
            "</document>",
        ]
    )
    assert packet_requests_silent_land(prose) is False


def test_author_opt_in_before_documents_still_opts_in() -> None:
    prose = "\n".join(
        [
            "work_key: todo:foo",
            _SILENT_LAND_OPT_IN_LINE,
            "<document>",
            "quoted",
            "</document>",
        ]
    )
    assert packet_requests_silent_land(prose) is True


def test_author_opt_in_after_last_document_still_opts_in() -> None:
    prose = "\n".join(
        [
            "<document>",
            "quoted",
            "</document>",
            _SILENT_LAND_OPT_IN_LINE,
        ]
    )
    assert packet_requests_silent_land(prose) is True


def test_excise_document_span_joins_with_space() -> None:
    assert excise_relayed_regions("a <document>x</document> b") == "a   b"


def test_excise_for_opt_in_drops_outermost_document_lines() -> None:
    joined = "\n".join(
        ["h", "<document>", "x </document>", "y", "</document>", "t"]
    )
    assert excise_relayed_regions_for_opt_in(joined) == "h\n\nt"
