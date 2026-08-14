"""Frozen catalog of California unclaimed-property surfaces the hunter knows.

Callers: CLI ``surfaces`` and ``report``. A later seat learns a surface exists
by running the hunter, not by reading a closeout. Gated surfaces stay listed
so a report can say NOT EXECUTED instead of going silent.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from unclaimed_property_hunter.transport import BULK_ZIP_URL, CLAIMIT_URL

Gate = Literal["ungated", "turnstile"]

ESTATES_XLSX_URL = (
    "https://www.sco.ca.gov/Files-UPD/estates_of_deceased_persons_file.xlsx"
)
CLAIMIT_SEARCH_URL = "https://claimit.ca.gov/SWS/properties"


@dataclass(frozen=True)
class Surface:
    """One CA surface: how to reach it, what a completed search still cannot see."""

    id: str
    name: str
    url: str
    gate: Gate
    hunter_cmd: str
    run_kinds: tuple[str, ...]
    cannot_reach: tuple[str, ...]
    refresh: str
    automate: bool


SURFACES: tuple[Surface, ...] = (
    Surface(
        id="estates_xlsx",
        name="SCO Estates of Deceased Persons",
        url=ESTATES_XLSX_URL,
        gate="ungated",
        hunter_cmd="estates",
        run_kinds=("estates_extract",),
        cannot_reach=(
            "ordinary escheats in the decedent's own name (those sit in All_Records)",
            "remittances after the April/October first-business-day cutoff",
        ),
        refresh="first business day of April and October",
        automate=True,
    ),
    Surface(
        id="all_records_zip",
        name="ClaimIt All_Records zip",
        url=BULK_ZIP_URL,
        gate="ungated",
        hunter_cmd="extract",
        run_kinds=("bulk_extract",),
        cannot_reach=(
            "EIN/FEIN/SSN/TIN (no such column)",
            "report/escheat date (no such column)",
            "post-zip remittances (parametric vs live UI)",
        ),
        refresh="SCO publishes updates every Thursday",
        automate=True,
    ),
    Surface(
        id="claimit_interactive",
        name="ClaimIt interactive POST /SWS/properties",
        url=CLAIMIT_SEARCH_URL,
        gate="turnstile",
        hunter_cmd="sweep",
        run_kinds=(
            "transport_probe",
            "ingest_json",
            "ingest_html",
            "ingest_unparsed",
        ),
        cannot_reach=(
            "any automated search — POST requires X-SWS-Turnstile-Token",
        ),
        refresh="live UI; not a dated bulk file",
        automate=False,
    ),
)


def catalog_dicts() -> list[dict[str, object]]:
    """JSON-ready catalog rows for the ``surfaces`` command — no network I/O."""
    return [
        {
            "id": s.id,
            "name": s.name,
            "url": s.url,
            "gate": s.gate,
            "hunter_cmd": s.hunter_cmd,
            "run_kinds": list(s.run_kinds),
            "cannot_reach": list(s.cannot_reach),
            "refresh": s.refresh,
            "automate": s.automate,
            "landing_url": CLAIMIT_URL if s.id == "claimit_interactive" else s.url,
        }
        for s in SURFACES
    ]
