"""Catalog and four-token report — no network, no live search."""

from __future__ import annotations

import json

from unclaimed_property_hunter.cli_surfaces import cmd_report, cmd_surfaces
from unclaimed_property_hunter.models import Query, RunRecord
from unclaimed_property_hunter.result_surface import surface_report_row, verdict_token
from unclaimed_property_hunter.surfaces import SURFACES, catalog_dicts


def test_catalog_names_estates_xlsx_as_ungated():
    """Estates workbook is catalogued as an ungated, automatable surface."""
    ids = {row["id"]: row for row in catalog_dicts()}
    assert "estates_xlsx" in ids
    assert ids["estates_xlsx"]["gate"] == "ungated"
    assert ids["estates_xlsx"]["automate"] is True
    assert ids["claimit_interactive"]["gate"] == "turnstile"
    assert ids["claimit_interactive"]["automate"] is False


def test_verdict_four_tokens():
    """The four honesty tokens map from executed/count/absent, nothing else."""
    assert verdict_token(search_executed=True, hit_count=0) == "EXECUTED ZERO"
    assert verdict_token(search_executed=True, hit_count=25) == "EXECUTED HITS 25"
    assert verdict_token(search_executed=False, hit_count=None) == "NOT EXECUTED"
    assert verdict_token(
        search_executed=False, hit_count=None, field_absent=True
    ) == "NOT-RETRIEVED"


def test_surface_report_missing_run_is_not_executed_not_zero():
    """A catalog surface with no persisted run is NOT EXECUTED, never zero."""
    row = surface_report_row(SURFACES[0], None)
    assert row["verdict"] == "NOT EXECUTED"
    assert row["search_executed"] is False
    assert row["hit_count"] is None
    assert row["cannot_reach"]


def test_surface_report_completed_zero():
    """A completed estates run with no hits is EXECUTED ZERO."""
    record = RunRecord(
        run_id="estates-x-1",
        utc_timestamp="2026-08-14T12:00:00Z",
        query=Query(surname="X"),
        run_kind="estates_extract",
        search_executed=True,
        raw_payload_uri="cortex://x",
        raw_sha256="a",
        hits=[],
    )
    row = surface_report_row(SURFACES[0], record)
    assert row["verdict"] == "EXECUTED ZERO"
    assert row["hit_count"] == 0


def test_cli_surfaces_no_network(capsys):
    """``surfaces`` prints all three catalog rows without I/O beyond stdout."""
    class _Args:
        pass

    assert cmd_surfaces(_Args()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["surfaces"]) == 3


def test_cli_report_unknown_surname_is_not_executed(capsys):
    """``report`` for an unseen surname is NOT EXECUTED on every surface."""
    class _Args:
        surname = "NoSuchSurnameForReportTest"

    assert cmd_report(_Args()) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["surname"] == "NoSuchSurnameForReportTest"
    assert len(payload["surfaces"]) == 3
    for row in payload["surfaces"]:
        assert row["verdict"] == "NOT EXECUTED"
        assert row["cannot_reach"]
