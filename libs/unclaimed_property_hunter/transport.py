"""Honest probe of the live ClaimIt landing page — not a surname search.

The SPA requires JS, Cloudflare Turnstile, and a `/SWS` session. This module
only GETs the shell so a run can attach verbatim transport evidence.
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

CLAIMIT_URL = "https://claimit.ca.gov/"
PACKET_HOST_CLAIMIT_SCO = "https://claimit.sco.ca.gov/"
PACKET_HOST_UCPI = "https://ucpi.sco.ca.gov/"
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


@dataclass(frozen=True)
class TransportProbe:
    """Verbatim HTTP outcomes for the packet hosts plus the live landing page."""

    claimit_sco_error: str
    ucpi_status: int
    ucpi_location: str
    landing_status: int
    landing_url: str
    landing_body: str
    landing_content_type: str


def intended_query_string(surname: str, first_name: str = "", city: str = "") -> str:
    """Build the lastName-first query string the operator would type in ClaimIt."""
    parts = [f"lastName={surname.strip()}"]
    if first_name.strip():
        parts.append(f"firstName={first_name.strip()}")
    if city.strip():
        parts.append(f"city={city.strip()}")
    return "&".join(parts)


def probe_transport(*, timeout_s: float = 25.0) -> TransportProbe:
    """GET the packet hosts and live landing page; return raw status and HTML.

    Does not POST a search and does not send lastName. DNS failures are stored
    as the curl-equivalent error string, not rewritten as HTTP status.
    """
    headers = {"User-Agent": USER_AGENT}
    claimit_sco_error = ""
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            client.get(PACKET_HOST_CLAIMIT_SCO, headers=headers)
    except httpx.RequestError as exc:
        claimit_sco_error = f"{type(exc).__name__}: {exc}"

    ucpi_status = 0
    ucpi_location = ""
    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=False) as client:
            ucpi = client.get(PACKET_HOST_UCPI, headers=headers)
        ucpi_status = ucpi.status_code
        ucpi_location = ucpi.headers.get("location", "")
    except httpx.RequestError as exc:
        ucpi_location = f"{type(exc).__name__}: {exc}"

    with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
        landing = client.get(CLAIMIT_URL, headers=headers)
    return TransportProbe(
        claimit_sco_error=claimit_sco_error,
        ucpi_status=ucpi_status,
        ucpi_location=ucpi_location,
        landing_status=landing.status_code,
        landing_url=str(landing.url),
        landing_body=landing.text,
        landing_content_type=landing.headers.get("content-type", ""),
    )
