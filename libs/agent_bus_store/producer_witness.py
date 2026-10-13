"""Pipeline-keyed producer liveness probe (Stargate CDP leg route or GIW dispatch-status)."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Literal

import httpx
from transport_utils import DEFAULT_STARGATE_URL, make_sync_client

logger = logging.getLogger("agent-bus.producer_witness")

CDP_GENERATE_PIPELINE_ID = "cdp-generate"
_PROBE_TIMEOUT_S: float = float(os.getenv("AGENT_BUS_CDP_LEG_PROBE_TIMEOUT_S", "2"))
_CDP_LEG_PATH = "/api/v1/frontier/cdp-legs/{execution_id}"


class WitnessVerdict(StrEnum):
    LIVE = "live"
    ABSENT = "absent"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class CdpLegProbeResult:
    verdict: WitnessVerdict
    reason: str
    payload: dict[str, Any] | None


def _stargate_base_url() -> str:
    return os.environ.get("STARGATE_URL", DEFAULT_STARGATE_URL).rstrip("/")


def probe_cdp_leg_status(execution_id: str) -> CdpLegProbeResult:
    """GET the Stargate leg route; classify for reconcile and reader witness."""
    path = _CDP_LEG_PATH.format(execution_id=execution_id)
    try:
        with make_sync_client(_stargate_base_url(), timeout=_PROBE_TIMEOUT_S) as client:
            resp = client.get(path, headers={"Accept": "application/json"})
    except httpx.TimeoutException as exc:
        return CdpLegProbeResult(WitnessVerdict.UNKNOWN, f"probe_timeout:{exc}", None)
    except httpx.HTTPError as exc:
        return CdpLegProbeResult(
            WitnessVerdict.UNKNOWN, f"probe_unreachable:{exc}", None
        )

    if resp.status_code in (404, 405) or resp.status_code >= 500:
        return CdpLegProbeResult(
            WitnessVerdict.UNKNOWN,
            f"probe_http_{resp.status_code}",
            None,
        )
    if resp.status_code >= 400:
        return CdpLegProbeResult(
            WitnessVerdict.UNKNOWN,
            f"probe_http_{resp.status_code}",
            None,
        )

    try:
        payload = json.loads(resp.text)
    except json.JSONDecodeError:
        return CdpLegProbeResult(WitnessVerdict.UNKNOWN, "malformed_json", None)
    if not isinstance(payload, dict):
        return CdpLegProbeResult(WitnessVerdict.UNKNOWN, "malformed_json", None)

    return classify_cdp_leg_payload(payload)


def classify_cdp_leg_payload(payload: dict[str, Any]) -> CdpLegProbeResult:
    """Classify a 200 leg-status body (shared with unit tests)."""
    found = payload.get("found")
    if found is False:
        return CdpLegProbeResult(WitnessVerdict.ABSENT, "leg_not_found", payload)
    if found is not True:
        return CdpLegProbeResult(WitnessVerdict.UNKNOWN, "malformed_found", payload)

    open_flag = payload.get("open")
    if open_flag is True:
        return CdpLegProbeResult(WitnessVerdict.LIVE, "leg_open", payload)
    if open_flag is False:
        return CdpLegProbeResult(WitnessVerdict.UNKNOWN, "leg_closed", payload)
    return CdpLegProbeResult(WitnessVerdict.UNKNOWN, "malformed_open", payload)


def reader_witness_from_cdp_probe(
    probe: CdpLegProbeResult,
) -> Literal["live", "dead"] | None:
    """Map a CDP leg probe to wait/CHECKPOINT witness (no terminal writes)."""
    if probe.verdict is WitnessVerdict.LIVE:
        return "live"
    if probe.verdict is WitnessVerdict.ABSENT:
        return "dead"
    return None
