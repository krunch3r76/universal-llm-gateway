"""Observation plane for live CDP ports and CSE targets used by drain safety.

The probe reads CDP truth without mutating Chrome, filters target types, and
emits orphan-scan observations for later reconciliation or hygiene actions.
"""

from __future__ import annotations

import contextlib
import json
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from claude_bundles import cdp_lane, cdp_registry
from claude_bundles.cdp_orphan_cse_classify import (
    CseClassification,
    CseTarget,
    classify_port_cse_targets,
    cse_pages_from_list,
)
from claude_bundles.cdp_reclaim_refuse import guard_cse_reclaim
from claude_bundles.cse_url import normalize_cse_url

LIVENESS_AUTHORITY_ATTACHMENT_ONLY = "attachment_only"

_CSE_URL_MARKER = "claude.ai/cowork/cse_"
_REG_PROFILE_PREFIX = "claude-ai-chrome-profile-reg-"
_PROBE_TIMEOUT_S = 1.5


@dataclass(frozen=True)
class LivePort:
    """Observed CDP host plus qualifying CSE-page evidence from one port."""

    port: int
    profile: Path | None
    page_urls: tuple[str, ...]
    has_live_cse: bool
    cse_urls: tuple[str, ...] = ()
    cse_target_count: int = 0


@dataclass(frozen=True)
class Orphan:
    port: int
    pid: int | None
    profile: Path | None
    has_live_cse: bool
    uptime_s: float | None
    cse_targets: tuple[CseTarget, ...] = ()


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
    ports_live: int
    ports_skipped_registered: int

    @property
    def ports_examined(self) -> int:
        return self.ports_live - self.ports_skipped_registered


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
    """Return the host clock-tick frequency used to calculate process uptime."""
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
    """Extract top-level browser page URLs from a CDP target listing."""
    if not isinstance(payload, list):
        return ()
    urls: list[str] = []
    for item in payload:
        if isinstance(item, dict) and item.get("type") == "page":
            url = item.get("url")
            if isinstance(url, str):
                urls.append(url)
    return tuple(urls)


def _unique_cse_urls(page_urls: tuple[str, ...]) -> tuple[str, ...]:
    """Normalize qualifying page URLs and preserve first-seen session order."""
    seen: set[str] = set()
    out: list[str] = []
    for url in page_urls:
        if _CSE_URL_MARKER not in url:
            continue
        normalized = normalize_cse_url(url)
        if normalized and normalized not in seen:
            seen.add(normalized)
            out.append(normalized)
    return tuple(out)


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
        cse_pages = cse_pages_from_list(page_list)
        cse_page_urls = tuple(str(page.get("url") or "") for page in cse_pages)
        cse_urls = _unique_cse_urls(cse_page_urls)
        pid = _pid_listening_on(port)
        profile = _profile_from_pid(pid) if pid is not None else None
        has_live_cse = bool(cse_urls)
        out.append(
            LivePort(
                port=port,
                profile=profile,
                page_urls=page_urls,
                has_live_cse=has_live_cse,
                cse_urls=cse_urls,
                cse_target_count=len(cse_page_urls),
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
    live_ports = probe_live_ports()
    matched: list[Orphan] = []
    rejected: list[RejectedPort] = []
    unevaluable: list[UnevaluablePort] = []
    skipped_registered = 0
    for live in live_ports:
        if live.port in registered:
            skipped_registered += 1
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
        page_list = _fetch_json(f"http://127.0.0.1:{live.port}/json/list")
        cse_targets = classify_port_cse_targets(
            live.port,
            profile=live.profile,
            page_list=page_list,
            fetch_json=_fetch_json,
        )
        matched.append(
            Orphan(
                port=live.port,
                pid=pid,
                profile=live.profile,
                has_live_cse=live.has_live_cse,
                uptime_s=_process_uptime_s(pid) if pid is not None else None,
                cse_targets=cse_targets,
            )
        )
    result = OrphanScanResult(
        matched=tuple(sorted(matched, key=lambda o: o.port)),
        rejected=tuple(sorted(rejected, key=lambda r: r.port)),
        unevaluable=tuple(sorted(unevaluable, key=lambda u: u.port)),
        ports_live=len(live_ports),
        ports_skipped_registered=skipped_registered,
    )
    cdp_registry.log_orphan_scan(result)
    return result


def _cse_target_as_dict(target: CseTarget) -> dict[str, Any]:
    return asdict(target)


def orphan_as_dict(orphan: Orphan) -> dict[str, Any]:
    """Render one orphan port and its scan-ephemeral CSE classifications for JSON."""
    d = asdict(orphan)
    if orphan.profile is not None:
        d["profile"] = str(orphan.profile)
    d["cse_targets"] = [_cse_target_as_dict(t) for t in orphan.cse_targets]
    d["closable_count"] = sum(
        1 for t in orphan.cse_targets if t.classification == "closable"
    )
    d["protected_count"] = sum(
        1 for t in orphan.cse_targets if t.classification == "protected"
    )
    return d


def _rejected_as_dict(item: RejectedPort) -> dict[str, Any]:
    d = asdict(item)
    if item.profile is not None:
        d["profile"] = str(item.profile)
    return d


def reclaim_enabled() -> bool:
    """S3 CSE-close flag — hardcoded off until an actuator ships."""
    return False


def attempt_reclaim_cse_target(target: CseTarget) -> dict[str, Any]:
    """Actuator chokepoint for closing a classified CSE target.

    Always runs by-id refuse (6893) before the S3 enable flag. When reclaim is
    disabled this is a dry refuse; when enabled, close primitives must still
    pass ``guard_cse_reclaim`` first.
    """
    refuse = guard_cse_reclaim(target.url)
    if refuse is not None:
        return {
            "ok": False,
            "reclaimed": False,
            "reason": refuse,
            "url": target.url,
        }
    if not reclaim_enabled():
        return {
            "ok": False,
            "reclaimed": False,
            "reason": "reclaim_disabled",
            "url": target.url,
        }
    if target.classification != "closable":
        return {
            "ok": False,
            "reclaimed": False,
            "reason": f"not_closable:{target.classification_reason}",
            "url": target.url,
        }
    # S3 close primitive not implemented — fail closed.
    return {
        "ok": False,
        "reclaimed": False,
        "reason": "close_primitive_absent",
        "url": target.url,
    }


def orphan_scan_as_dict(scan: OrphanScanResult) -> dict[str, Any]:
    """Render orphan-scan counts and classified ports for downstream observation consumers."""
    matched_dicts = [orphan_as_dict(o) for o in scan.matched]
    closable_total = sum(d.get("closable_count", 0) for d in matched_dicts)
    protected_total = sum(d.get("protected_count", 0) for d in matched_dicts)
    return {
        "ports_live": scan.ports_live,
        "ports_skipped_registered": scan.ports_skipped_registered,
        "ports_examined": scan.ports_examined,
        "matched_count": len(scan.matched),
        "rejected_count": len(scan.rejected),
        "unevaluable_count": len(scan.unevaluable),
        "closable_count": closable_total,
        "protected_count": protected_total,
        "cse_classification": "scan_ephemeral",
        "reclaim_enabled": reclaim_enabled(),
        "matched": matched_dicts,
        "rejected": [_rejected_as_dict(r) for r in scan.rejected],
        "unevaluable": [asdict(u) for u in scan.unevaluable],
    }


def _registration_as_dict(reg: cdp_registry.Registration) -> dict[str, Any]:
    d = asdict(reg)
    d["profile"] = str(reg.profile)
    return d


def registered_lane_dicts() -> list[dict[str, Any]]:
    """Registry rows visible on the list surface (active + orphaned_alive + retained)."""
    active = cdp_registry._load_active()
    out: list[dict[str, Any]] = []
    for rid, row in active.items():
        status = row.get("status")
        if status not in cdp_registry._HOST_LISTABLE_STATUSES:
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
        if status == "retained":
            d["retain_reason"] = row.get("retain_reason")
        out.append(d)
    return sorted(out, key=lambda item: int(item["port"]))


def list_surface_payload() -> dict[str, Any]:
    """Return registered lanes, fresh orphan observations, and liveness authority for the registry list surface."""
    scan = find_orphans()
    return {
        "lanes": registered_lane_dicts(),
        "orphans": [orphan_as_dict(o) for o in scan.matched],
        "orphan_scan": orphan_scan_as_dict(scan),
        "liveness_authority": LIVENESS_AUTHORITY_ATTACHMENT_ONLY,
    }


__all__ = [
    "CseClassification",
    "CseTarget",
    "LivePort",
    "Orphan",
    "OrphanScanResult",
    "RejectedPort",
    "UnevaluablePort",
    "find_orphans",
    "is_primary_profile",
    "list_surface_payload",
    "orphan_as_dict",
    "orphan_scan_as_dict",
    "probe_live_ports",
    "registered_lane_dicts",
]
