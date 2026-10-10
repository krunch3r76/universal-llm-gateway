"""Restart-plan dry run (maestro-landed-live slice 1)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from maestro_landed.disposition import draft_disposition
from maestro_landed.liveness import service_live
from maestro_landed.manage_client import ManageClient
from maestro_landed.porcelain import porcelain_blocks
from maestro_landed.serving_map import ServingMap, propagation_service_diff
from maestro_landed.source_pins import verify_source_pins


@dataclass
class DryRunResult:
    ok: bool
    status: str
    restart_set: list[str] = field(default_factory=list)
    skipped_live: list[str] = field(default_factory=list)
    porcelain_block: list[str] = field(default_factory=list)
    unmapped: list[str] = field(default_factory=list)
    giw_deferred: bool = False
    disposition_draft: str = ""
    chars: int = 0
    source_pins_ok: bool = True
    propagation_diff: list[str] = field(default_factory=list)
    pre_restart: dict[str, Any] = field(default_factory=dict)
    trace: list[dict[str, Any]] = field(default_factory=list)
    refuse_reason: str | None = None

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "status": self.status,
            "restart_set": self.restart_set,
            "skipped_live": self.skipped_live,
            "porcelain_block": self.porcelain_block,
            "unmapped": self.unmapped,
            "giw_deferred": self.giw_deferred,
            "disposition_draft": self.disposition_draft,
            "chars": self.chars,
            "source_pins_ok": self.source_pins_ok,
            "propagation_diff": self.propagation_diff,
            "pre_restart": self.pre_restart,
            "trace": self.trace,
            "refuse_reason": self.refuse_reason,
        }


def compute_dry_run(
    *,
    land_sha: str,
    touched_paths: list[str],
    porcelain_raw: str,
    serving_map: ServingMap,
    manage: ManageClient,
    propagation: list[Any] | None = None,
    source_pins_ok: bool | None = None,
    files_root: Any = None,
) -> DryRunResult:
    pins_ok = source_pins_ok
    if pins_ok is None:
        pins_ok, _details = verify_source_pins(serving_map.source_pins, files_root=files_root)
    if not pins_ok:
        return DryRunResult(
            ok=False,
            status="REFUSE",
            source_pins_ok=False,
            refuse_reason="source_pin_sha_drift",
            trace=list(manage.trace),
        )

    blocked = porcelain_blocks(porcelain_raw, touched_paths)
    if blocked:
        text, chars, _ = draft_disposition(land_sha=land_sha, restart_set=[])
        return DryRunResult(
            ok=True,
            status="STOP",
            porcelain_block=blocked,
            restart_set=[],
            disposition_draft=text,
            chars=chars,
            source_pins_ok=True,
            trace=list(manage.trace),
        )

    services: set[str] = set()
    unmapped: list[str] = []
    for path in touched_paths:
        mapped = serving_map.services_for_path(path)
        if mapped is None:
            unmapped.append(path)
        else:
            services.update(mapped)
    if unmapped:
        text, chars, _ = draft_disposition(land_sha=land_sha, restart_set=[])
        return DryRunResult(
            ok=False,
            status="STOP",
            unmapped=sorted(set(unmapped)),
            restart_set=[],
            disposition_draft=text,
            chars=chars,
            source_pins_ok=True,
            trace=list(manage.trace),
        )

    prop_diff = propagation_service_diff(services, propagation)
    restart_list = sorted(services)

    snapshot = manage.call(
        "fleet_liveness",
        {"code_ref": land_sha, "services": restart_list},
    )
    skipped: list[str] = []
    still_needed: list[str] = []
    for svc in restart_list:
        if service_live(snapshot, svc):
            skipped.append(svc)
        else:
            still_needed.append(svc)

    giw_deferred = False
    if "git_integration_worker" in still_needed:
        busy = manage.call("busy_status", {"service": "git_integration_worker"})
        busy_flag = bool(busy.get("busy") or busy.get("state") == "busy")
        if busy.get("error"):
            busy_flag = False
        if busy_flag:
            giw_deferred = True
            still_needed = [s for s in still_needed if s != "git_integration_worker"]

    pre_restart = {
        "label": "pre_restart",
        "snapshot": snapshot,
        "services": restart_list,
    }

    text, chars, within = draft_disposition(land_sha=land_sha, restart_set=still_needed)
    if not within:
        return DryRunResult(
            ok=False,
            status="STOP",
            restart_set=still_needed,
            skipped_live=skipped,
            giw_deferred=giw_deferred,
            disposition_draft=text,
            chars=chars,
            source_pins_ok=True,
            propagation_diff=prop_diff,
            pre_restart=pre_restart,
            refuse_reason="disposition_over_2000",
            trace=list(manage.trace),
        )

    return DryRunResult(
        ok=True,
        status="OK",
        restart_set=still_needed,
        skipped_live=skipped,
        giw_deferred=giw_deferred,
        disposition_draft=text,
        chars=chars,
        source_pins_ok=True,
        propagation_diff=prop_diff,
        pre_restart=pre_restart,
        trace=list(manage.trace),
    )
