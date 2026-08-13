"""Compose-spawn attest observation events (arc 6928 Fork A / 7034 B1 producer).

Publishes success **and** failure ``/new`` compose outcomes so the fleet can
join success-vs-failure on chip census / gate-reject / radiogroup axes.
Mirrors ``events_skill_delivery`` key discipline: Stargate hyphen-UUID under
``execution_id`` and satellite hex under ``satellite_execution_id`` on every row.
"""

from __future__ import annotations

import contextlib
import json
import os
import socket
import time
from typing import Any

from universal_event_bus.events.event import Event
from universal_event_bus.events.factory import event_factory


@event_factory
def cdp_generate_compose_attested(
    *,
    ok: bool,
    surface: str = "bare_new",
    step: str = "",
    via: str = "",
    wanted: str = "",
    chip_candidate_count: int = 0,
    surface_radiogroup_count: int = 0,
    radiogroup_names: list[str] | None = None,
    gate_rejects: list[dict[str, Any]] | None = None,
    polled_ms: float | int = 0,
    fingerprint: dict[str, Any] | None = None,
    execution_id: str = "",
    satellite_execution_id: str = "",
) -> Event:
    """Observation event for ``/new`` compose toggle attest (success or fail).

    ``execution_id`` is the **Stargate** seating id; ``satellite_execution_id``
    is the cdp_ask admit id. Both keys are always present (may be empty when
    callers lack seating context). ``ok=True`` is B1-admissible for hop cadence
    only when the producer cannot emit until compose chrome + mode attest exist.
    """
    return Event(
        signal="cdp.generate.compose_attested",
        role="observation",
        scope="node",
        payload={
            "ok": bool(ok),
            "surface": str(surface or ""),
            "step": str(step or ""),
            "via": str(via or ""),
            "wanted": str(wanted or ""),
            "chip_candidate_count": int(chip_candidate_count or 0),
            "surface_radiogroup_count": int(surface_radiogroup_count or 0),
            "radiogroup_names": list(radiogroup_names or []),
            "gate_rejects": list(gate_rejects or []),
            "polled_ms": int(polled_ms or 0),
            "fingerprint": dict(fingerprint or {}),
            "execution_id": str(execution_id or ""),
            "satellite_execution_id": str(satellite_execution_id or ""),
        },
    )


def emit_compose_attested(
    *,
    ok: bool,
    surface: str = "bare_new",
    step: str = "",
    via: str = "",
    wanted: str = "",
    chip_candidate_count: int = 0,
    surface_radiogroup_count: int = 0,
    radiogroup_names: list[str] | None = None,
    gate_rejects: list[dict[str, Any]] | None = None,
    polled_ms: float | int = 0,
    fingerprint: dict[str, Any] | None = None,
    execution_id: str = "",
    satellite_execution_id: str = "",
) -> Event | None:
    """Build + best-effort-mirror ``cdp.generate.compose_attested``; never raises.

    Returns the Event when construction succeeds (tests assert payload); None
    only if factory construction fails. Compose path must not fail on telemetry.
    """
    try:
        event = cdp_generate_compose_attested(
            ok=ok,
            surface=surface,
            step=step,
            via=via,
            wanted=wanted,
            chip_candidate_count=chip_candidate_count,
            surface_radiogroup_count=surface_radiogroup_count,
            radiogroup_names=radiogroup_names,
            gate_rejects=gate_rejects,
            polled_ms=polled_ms,
            fingerprint=fingerprint,
            execution_id=execution_id,
            satellite_execution_id=satellite_execution_id,
        )
    except Exception:  # noqa: BLE001 — attest path must not fail on telemetry
        return None
    _mirror_to_event_service(event)
    return event


def emit_compose_attested_from_result(
    result: dict[str, Any],
    *,
    surface: str = "bare_new",
    execution_id: str = "",
    satellite_execution_id: str = "",
) -> Event | None:
    """Project ensure_cowork_auto result onto dual-id compose_attested telemetry.

    Prefers ``approval.after`` so a successful Auto flip is not reported as the
    pre-flip Manual fingerprint from the mode block. Best-effort emit; never
    raises. ``ok`` follows the ensure result, not the nested mode chip alone.
    """
    mode_block = result.get("mode") if isinstance(result.get("mode"), dict) else result
    if not isinstance(mode_block, dict):
        mode_block = {}
    approval_block = (
        result.get("approval") if isinstance(result.get("approval"), dict) else {}
    )
    probe = (
        mode_block.get("click_probe")
        if isinstance(mode_block.get("click_probe"), dict)
        else {}
    )
    # Prefer approval.after so Cowork+Auto emit is not the pre-flip Manual
    # fingerprint from the mode block (ensure_cowork_auto sequences mode then Auto).
    fp = (
        approval_block.get("after")
        or mode_block.get("compose_mode_fingerprint")
        or mode_block.get("after")
        or mode_block.get("before")
        or {}
    )
    if not isinstance(fp, dict):
        fp = {}
    candidates = mode_block.get("candidates")
    if not isinstance(candidates, list):
        candidates = (
            probe.get("candidates") if isinstance(probe.get("candidates"), list) else []
        )
    return emit_compose_attested(
        ok=bool(result.get("ok")),
        surface=surface,
        step=str(result.get("step") or mode_block.get("step") or ""),
        via=str(mode_block.get("via") or probe.get("via") or ""),
        wanted=str(mode_block.get("wanted") or ""),
        chip_candidate_count=len(candidates),
        surface_radiogroup_count=int(
            mode_block.get("surface_radiogroup_count")
            or probe.get("surface_radiogroup_count")
            or 0
        ),
        radiogroup_names=list(
            mode_block.get("radiogroup_names") or probe.get("radiogroup_names") or []
        ),
        gate_rejects=list(
            mode_block.get("gate_rejects") or probe.get("gate_rejects") or []
        ),
        polled_ms=mode_block.get("polled_ms") or mode_block.get("elapsed_ms") or 0,
        fingerprint=fp,
        execution_id=execution_id,
        satellite_execution_id=satellite_execution_id,
    )


def _parse_tcp_target(raw: str) -> tuple[str, int] | None:
    """Parse ``host:port`` (IPv4/hostname). Returns None when malformed."""
    text = raw.strip()
    if not text or ":" not in text:
        return None
    host, _, port_s = text.rpartition(":")
    host = host.strip()
    if not host or not port_s.strip().isdigit():
        return None
    return host, int(port_s)


def _resolve_tcp_target() -> tuple[str, int] | None:
    """TCP target when set: ``EVENTS_INGEST_TCP`` or host+port env pair.

    Jupiter ``cdp_ask`` remote start exports hub ``EVENTS_INGEST_TCP`` because
    local UDS on the satellite does not reach hub Event Service (MONITOR AC-2).
    """
    combined = os.environ.get("EVENTS_INGEST_TCP", "").strip()
    if combined:
        return _parse_tcp_target(combined)
    host = (
        os.environ.get("EVENT_SERVICE_INGEST_HOST", "").strip()
        or os.environ.get("EVENTS_INGEST_HOST", "").strip()
    )
    if not host:
        return None
    port_s = (
        os.environ.get("EVENTS_INGEST_PORT", "").strip()
        or os.environ.get("EVENT_INGEST_TCP_PORT", "").strip()
        or "7101"
    )
    if not port_s.isdigit():
        return None
    return host, int(port_s)


def _mirror_to_event_service(event: Event) -> None:
    """Best-effort Event Service ingest — TCP when configured, else UDS; never raises.

    Prefer ``EVENTS_INGEST_TCP=host:port`` so Jupiter ``cdp_ask`` / bundle emits
    reach hub ``:7101``. UDS-only silently dropped every post-``567a9b49`` row
    (arc 6928 COUNT=0) because the sock is local to the satellite.
    """
    payload: dict[str, Any] = {
        "signal": event.signal,
        "source": "cdp-compose-attest",
        "role": event.role,
        "scope": event.scope,
        "ts_unix_ms": int(time.time() * 1000),
        "payload": event.payload,
    }
    line = (json.dumps(payload) + "\n").encode()
    with contextlib.suppress(Exception):
        tcp = _resolve_tcp_target()
        if tcp is not None:
            host, port = tcp
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.settimeout(1.0)
                sock.connect((host, port))
                sock.sendall(line)
            return
        sock_path = os.environ.get(
            "EVENTS_INGEST_SOCK", "/tmp/universal-protocol/events.sock"
        )
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(1.0)
            sock.connect(sock_path)
            sock.sendall(line)
