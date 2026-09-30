"""cdp_ask in-flight witness — reads the registry rows, not the process being restarted.

When the live ``/drain-state`` probe does not answer (CPU-pinned process,
a:36939) or the process is already gone, the gate still needs in-flight truth.
That truth is durable: ``execution_state`` on each row of the cdp_ask host's
``~/.gateway/cdp-registry/active.json`` (authority
``claude_bundles.cdp_registry.execution_state``). This witness reads that file
locally when cdp_ask runs on this host, otherwise over the same SSH path
``what_is_running`` uses, and applies ``in_flight_rows``.

Verdicts (``restart_drain_witness`` merge: busy > idle > unknown):

- ``busy``   — rows in flight; holders name each ``execution_id``.
- ``idle``   — the file was read and nothing is in flight. Permitted because
  submit stamps ``seated`` at registration: work that exists is in the file.
  The seconds between admission and registration are the known gap
  (todo:cdp-ask-durable-execution-state a:36961); the live probe covers them.
- ``unknown`` — the file could not be read.
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import time
from pathlib import Path
from typing import Any

from .restart_drain_witness import WitnessReport
from .service_config import (
    cdp_ask_url_config,
    is_cdp_ask_local_host,
    resolve_cdp_ask_remote_target,
)

_SOURCE = "cdp_registry_execution_state"
_LOCAL_ACTIVE = Path.home() / ".gateway" / "cdp-registry" / "active.json"


def _read_active() -> tuple[dict[str, dict[str, Any]], str]:
    """Return ``(active, where)``; raises when neither local nor SSH read succeeds."""
    cfg = cdp_ask_url_config()
    if cfg is None:
        raise RuntimeError("project_ask_url not configured")
    host, _port, _base = cfg
    if is_cdp_ask_local_host(host):
        data = json.loads(_LOCAL_ACTIVE.read_text(encoding="utf-8") or "{}")
        if not isinstance(data, dict):
            raise RuntimeError("active.json is not an object")
        return data, f"local:{_LOCAL_ACTIVE}"
    resolved = resolve_cdp_ask_remote_target(host)
    if resolved is None:
        raise RuntimeError(f"no ssh target for cdp_ask host {host!r}")
    _hostname, address, ssh_user = resolved
    target = f"{ssh_user}@{address}"
    return _fetch_registry_via_ssh(target), f"ssh:{target}"


def _fetch_registry_via_ssh(ssh_target: str) -> dict[str, dict[str, Any]]:
    """Read the remote ``active.json`` (same read ``scripts/cortex/what_is_running`` does)."""
    remote = (
        'python3 -c "import json,pathlib;'
        "p=pathlib.Path.home()/'.gateway/cdp-registry/active.json';"
        "print(p.read_text() if p.exists() else '{}')\""
    )
    proc = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", ssh_target, remote],
        check=False,
        capture_output=True,
        text=True,
        timeout=20,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            f"registry ssh failed rc={proc.returncode}: {proc.stderr.strip()}"
        )
    data = json.loads(proc.stdout or "{}")
    if not isinstance(data, dict):
        raise RuntimeError("registry payload is not an object")
    return data


class CdpAskInFlightWitness:
    """``InFlightWitness`` over the cdp_ask host's registry ``execution_state`` rows."""

    async def observe(self, service: str) -> WitnessReport:
        from claude_bundles.cdp_registry.execution_state import in_flight_rows

        try:
            active, where = await asyncio.to_thread(_read_active)
        except Exception as exc:  # noqa: BLE001 — unreadable file is unknown, never idle
            return WitnessReport(
                verdict="unknown",
                source=_SOURCE,
                note=f"registry read failed: {type(exc).__name__}: {exc}",
            )
        now = time.time()
        in_flight = in_flight_rows(active, now=now)
        holders: list[dict[str, Any]] = []
        rows: list[dict[str, Any]] = []
        for rid, row in in_flight.items():
            entry = row.get("execution_state") or {}
            eid = str(entry.get("execution_id") or "")
            subject = " ".join(
                str(row[k]).strip() for k in ("holder", "purpose") if row.get(k)
            )
            holder: dict[str, Any] = {
                "kind": str(entry.get("kind") or "execution"),
                "op_id": eid,
            }
            if subject:
                holder["subject_preview"] = subject
            holders.append(holder)
            rows.append(
                {
                    "registration_id": rid,
                    "execution_id": eid,
                    "state": entry.get("state"),
                    "age_s": round(now - float(entry.get("started_at") or now)),
                    "chat_url": row.get("chat_url"),
                }
            )
        detail = {"in_flight": rows, "source": where, "row_count": len(active)}
        if holders:
            return WitnessReport(
                verdict="busy",
                source=_SOURCE,
                holders=holders,
                detail=detail,
                note=f"{len(holders)} in-flight execution_state row(s) at {where}",
            )
        return WitnessReport(
            verdict="idle",
            source=_SOURCE,
            detail=detail,
            note=f"no in-flight execution_state rows at {where}",
        )


__all__ = ["CdpAskInFlightWitness"]
