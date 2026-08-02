"""Observation plane for live CDP ports — reads ground truth; never writes registry."""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane, cdp_registry

LIVENESS_AUTHORITY_ATTACHMENT_ONLY = "attachment_only"
LIVENESS_AUTHORITY_OBSERVED = "observed"

_CSE_URL_MARKER = "claude.ai/cowork/cse_"
_REG_PROFILE_PREFIX = "claude-ai-chrome-profile-reg-"
_PROBE_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class LivePort:
    port: int
    profile: Path | None
    page_urls: tuple[str, ...]
    has_live_cse: bool


@dataclass(frozen=True)
class Orphan:
    port: int
    pid: int | None
    profile: Path | None
    has_live_cse: bool
    uptime_s: float | None


@dataclass(frozen=True)
class RejectedPort:
    port: int
    pid: int | None
    profile: Path | None
    has_live_cse: bool
    reason: str


@dataclass(frozen=True)
class UnevaluablePort:
    port: int
    pid: int | None
    has_live_cse: bool
    reason: str


@dataclass(frozen=True)
class OrphanScanResult:
    matched: tuple[Orphan, ...]
    rejected: tuple[RejectedPort, ...]
    unevaluable: tuple[UnevaluablePort, ...]


def is_primary_profile(profile: Path) -> bool:
    """Public wrapper — attended primary must never classify as orphan."""
    return cdp_registry.is_primary_profile(profile)


def _is_gateway_reg_profile(profile: Path | None) -> bool:
    if profile is None:
        return False
    return profile.name.startswith(_REG_PROFILE_PREFIX)


def _pid_listening_on(port: int) -> int | None:
    import subprocess

    try:
        out = subprocess.check_output(
            ["ss", "-ltnpH", f"sport = :{port}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    for tok in out.replace(",", " ").split():
        if tok.startswith("pid="):
            with contextlib.suppress(ValueError):
                return int(tok.split("=", 1)[1].split(",")[0])
    return None


def _profile_from_pid(pid: int) -> Path | None:
    """Resolve Chrome profile from /proc cmdline via shared lane token parser."""
    try:
        blob = Path(f"/proc/{pid}/cmdline").read_bytes().decode(errors="replace")
    except OSError:
        return None
    _, udd = cdp_lane.parse_chrome_lane(blob)
    return Path(udd) if udd else None


def _process_uptime_s(pid: int) -> float | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
        fields = stat.split()
        start_ticks = int(fields[21])
        uptime = float(Path("/proc/uptime").read_text().split()[0])
        hz = os_clk_tck()
        return max(0.0, uptime - start_ticks / hz)
    except (OSError, ValueError, IndexError):
        return None


def os_clk_tck() -> float:
    import os

    return os.sysconf("SC_CLK_TCK")  # type: ignore[attr-defined]


def _fetch_json(url: str) -> Any | None:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT_S) as resp:
            return json.loads(resp.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _page_urls_from_list(payload: Any) -> tuple[str, ...]:
    if not isinstance(payload, list):
        return ()
    urls: list[str] = []
    for item in payload:
        if isinstance(item, dict):
            url = item.get("url")
            if isinstance(url, str):
                urls.append(url)
    return tuple(urls)


def probe_live_ports(port_range: range | None = None) -> list[LivePort]:
    """Probe CDP /json/* on each port in the registry pool; never raises."""
    ports = cdp_registry.PORT_RANGE if port_range is None else port_range
    out: list[LivePort] = []
    for port in ports:
        version = _fetch_json(f"http://127.0.0.1:{port}/json/version")
        if version is None:
            continue
        page_list = _fetch_json(f"http://127.0.0.1:{port}/json/list")
        page_urls = _page_urls_from_list(page_list)
        pid = _pid_listening_on(port)
        profile = _profile_from_pid(pid) if pid is not None else None
        has_live_cse = any(_CSE_URL_MARKER in url for url in page_urls)
        out.append(
            LivePort(
                port=port,
                profile=profile,
                page_urls=page_urls,
                has_live_cse=has_live_cse,
            )
        )
    return out


def _registered_ports() -> set[int]:
    return {reg.port for reg in cdp_registry.list_active()}


def find_orphans() -> OrphanScanResult:
    """Classify listening CDP ports without a registry row.

    Every unregistered candidate lands in ``matched``, ``rejected``, or
    ``unevaluable`` — skipped and examined-and-rejected must never share the
    same outward shape (zero-found vs zero-examined).
    """
    registered = _registered_ports()
    matched: list[Orphan] = []
    rejected: list[RejectedPort] = []
    unevaluable: list[UnevaluablePort] = []
    for live in probe_live_ports():
        if live.port in registered:
            continue
        pid = _pid_listening_on(live.port)
        if live.profile is None:
            unevaluable.append(
                UnevaluablePort(
                    port=live.port,
                    pid=pid,
                    has_live_cse=live.has_live_cse,
                    reason="profile_unresolved",
                )
            )
            continue
        if is_primary_profile(live.profile):
            rejected.append(
                RejectedPort(
                    port=live.port,
                    pid=pid,
                    profile=live.profile,
                    has_live_cse=live.has_live_cse,
                    reason="primary_profile",
                )
            )
            continue
        if not _is_gateway_reg_profile(live.profile):
            rejected.append(
                RejectedPort(
                    port=live.port,
                    pid=pid,
                    profile=live.profile,
                    has_live_cse=live.has_live_cse,
                    reason="non_reg_profile",
                )
            )
            continue
        matched.append(
            Orphan(
                port=live.port,
                pid=pid,
                profile=live.profile,
                has_live_cse=live.has_live_cse,
                uptime_s=_process_uptime_s(pid) if pid is not None else None,
            )
        )
    return OrphanScanResult(
        matched=tuple(sorted(matched, key=lambda o: o.port)),
        rejected=tuple(sorted(rejected, key=lambda r: r.port)),
        unevaluable=tuple(sorted(unevaluable, key=lambda u: u.port)),
    )


def orphan_as_dict(orphan: Orphan) -> dict[str, Any]:
    d = asdict(orphan)
    if orphan.profile is not None:
        d["profile"] = str(orphan.profile)
    return d


def _rejected_as_dict(item: RejectedPort) -> dict[str, Any]:
    d = asdict(item)
    if item.profile is not None:
        d["profile"] = str(item.profile)
    return d


def orphan_scan_as_dict(scan: OrphanScanResult) -> dict[str, Any]:
    return {
        "matched": [orphan_as_dict(o) for o in scan.matched],
        "rejected": [_rejected_as_dict(r) for r in scan.rejected],
        "unevaluable": [asdict(u) for u in scan.unevaluable],
    }


def _registration_as_dict(reg: cdp_registry.Registration) -> dict[str, Any]:
    d = asdict(reg)
    d["profile"] = str(reg.profile)
    return d


def registered_lane_dicts() -> list[dict[str, Any]]:
    """Registry rows visible on the list surface (active + orphaned_alive)."""
    active = cdp_registry._load_active()
    out: list[dict[str, Any]] = []
    for rid, row in active.items():
        status = row.get("status")
        if status not in {"active", "orphaned_alive"}:
            continue
        d = _registration_as_dict(cdp_registry._row_to_registration(row))
        d["status"] = status
        holder_pid = row.get("holder_pid")
        d["driver_pid"] = holder_pid if isinstance(holder_pid, int) else None
        d["attached"] = cdp_registry.is_driver_lock_held(rid)
        chrome_pid = row.get("chrome_pid")
        d["chrome_pid"] = chrome_pid if isinstance(chrome_pid, int) else None
        if status == "orphaned_alive":
            d["orphan_reason"] = row.get("orphan_reason")
        out.append(d)
    return sorted(out, key=lambda item: int(item["port"]))


def list_surface_payload() -> dict[str, Any]:
    scan = find_orphans()
    return {
        "lanes": registered_lane_dicts(),
        "orphans": [orphan_as_dict(o) for o in scan.matched],
        "orphan_scan": orphan_scan_as_dict(scan),
        "liveness_authority": LIVENESS_AUTHORITY_ATTACHMENT_ONLY,
    }
