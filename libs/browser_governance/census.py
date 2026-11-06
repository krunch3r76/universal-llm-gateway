"""Browser resource census and warn-only admission preflight (R1)."""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import subprocess
import time
import urllib.error
import urllib.request
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from browser_governance.contract import (
    admission_preflight_mode,
    census_enabled,
    warn_disk_free_gib,
    warn_mem_available_mib,
    warn_swap_free_mib,
)
from browser_governance.events import (
    browser_admission_warn,
    browser_resource_census,
    emit_browser_event,
)

logger = logging.getLogger(__name__)

_CDP_PROBE_TIMEOUT_S = 0.8
_JUPITER_PROBE_TIMEOUT_S = 2.0
_JUPITER_CDP_PORT_RANGE = range(9222, 9291)
_JUPITER_TUNNEL_BASE = 10_000

_BROWSER_NAME_MARKERS = ("chrome", "chromium", "electron", "cursor")
_XVFB_DISPLAY_RE = re.compile(r"Xvfb\s+:(\d+)", re.IGNORECASE)


def _read_meminfo_kib(proc_root: Path) -> dict[str, float | None]:
    out: dict[str, float | None] = {
        "MemAvailable": None,
        "SwapFree": None,
        "SwapTotal": None,
    }
    try:
        text = (proc_root / "meminfo").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return out
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2:
            continue
        key = parts[0].rstrip(":")
        if key not in out:
            continue
        with contextlib.suppress(ValueError):
            out[key] = float(parts[1])
    return out


def _kib_to_mib(value: float | None) -> float | None:
    if value is None:
        return None
    return value / 1024.0


def _read_disk_statvfs(disk_path: str) -> tuple[float | None, float | None]:
    try:
        st = os.statvfs(disk_path)
    except OSError:
        return None, None
    free_bytes = st.f_bavail * st.f_frsize
    disk_free_gib = free_bytes / (1024.0**3)
    if st.f_files <= 0:
        return disk_free_gib, None
    inode_free_pct = 100.0 * st.f_favail / st.f_files
    return disk_free_gib, inode_free_pct


def _parse_cmdline_blob(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace").replace("\x00", " ")


def _profile_from_cmdline(cmdline: str) -> str | None:
    tokens = cmdline.split()
    for idx, tok in enumerate(tokens):
        if tok.startswith("--user-data-dir="):
            return tok.split("=", 1)[1]
        if tok == "--user-data-dir" and idx + 1 < len(tokens):
            return tokens[idx + 1]
    return None


def _is_browser_cmdline(cmdline: str) -> bool:
    lower = cmdline.lower()
    if "xvfb" in lower:
        return False
    return any(marker in lower for marker in _BROWSER_NAME_MARKERS)


def _read_pss_mib(proc_root: Path, pid: str) -> float | None:
    try:
        text = (proc_root / pid / "smaps_rollup").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None
    for line in text.splitlines():
        if line.startswith("Pss:"):
            with contextlib.suppress(ValueError, IndexError):
                kb = int(line.split()[1])
                return kb / 1024.0
    return None


def _read_rss_mib(proc_root: Path, pid: str) -> tuple[float | None, int | None]:
    try:
        text = (proc_root / pid / "status").read_text(
            encoding="utf-8", errors="replace"
        )
    except OSError:
        return None, None
    rss: float | None = None
    threads: int | None = None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            with contextlib.suppress(ValueError, IndexError):
                rss = int(line.split()[1]) / 1024.0
        elif line.startswith("Threads:"):
            with contextlib.suppress(ValueError, IndexError):
                threads = int(line.split()[1])
    return rss, threads


def _xvfb_display(cmdline: str) -> str | None:
    match = _XVFB_DISPLAY_RE.search(cmdline)
    if match:
        return match.group(1)
    for tok in cmdline.split():
        if tok.startswith(":") and tok[1:].isdigit():
            return tok[1:]
    return None


def _enumerate_processes(
    proc_root: Path,
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, float]]:
    profiles: dict[str, list[dict[str, Any]]] = defaultdict(list)
    xvfb_rss: dict[str, float] = defaultdict(float)
    try:
        entries = list(proc_root.iterdir())
    except OSError:
        return {}, {}

    for entry in entries:
        if not entry.name.isdigit():
            continue
        pid = entry.name
        try:
            cmdline = _parse_cmdline_blob((entry / "cmdline").read_bytes())
        except OSError:
            continue
        if not cmdline.strip():
            continue

        rss_mib, threads = _read_rss_mib(proc_root, pid)
        pss_mib = _read_pss_mib(proc_root, pid)
        mem_mib = pss_mib if pss_mib is not None else rss_mib

        if "xvfb" in cmdline.lower():
            display = _xvfb_display(cmdline)
            if display is not None and mem_mib is not None:
                xvfb_rss[display] += mem_mib
            continue

        if not _is_browser_cmdline(cmdline):
            continue

        profile = _profile_from_cmdline(cmdline) or "__unprofiled__"
        profiles[profile].append(
            {
                "pid": int(pid),
                "pss_mib": pss_mib,
                "rss_mib": rss_mib,
                "threads": threads,
            }
        )

    return dict(profiles), dict(xvfb_rss)


def _probe_cdp_json_list(
    host: str, port: int, *, timeout: float
) -> tuple[bool, int, int]:
    url = f"http://{host}:{port}/json/list"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8", errors="replace"))
    except Exception:
        return False, 0, 0
    if not isinstance(payload, list):
        return True, 0, 0
    page_targets = sum(
        1 for item in payload if isinstance(item, dict) and item.get("type") == "page"
    )
    return True, len(payload), page_targets


def _probe_jupiter_cdp() -> dict[str, Any]:
    """Best-effort Jupiter CDP counts; unreachable host never raises."""
    live = 0
    targets_total = 0
    page_targets = 0
    per_port_timeout = min(0.2, _JUPITER_PROBE_TIMEOUT_S / max(len(_JUPITER_CDP_PORT_RANGE), 1))
    for port in _JUPITER_CDP_PORT_RANGE:
        local_port = _JUPITER_TUNNEL_BASE + port
        is_live, total, pages = _probe_cdp_json_list(
            "127.0.0.1", local_port, timeout=per_port_timeout
        )
        if not is_live:
            continue
        live += 1
        targets_total += total
        page_targets += pages
    if live:
        return {
            "cdp_ports_live": live,
            "cdp_targets_total": targets_total,
            "cdp_page_targets": page_targets,
            "ports_checked": len(_JUPITER_CDP_PORT_RANGE),
            "via": "local_tunnel",
        }

    with contextlib.suppress(Exception):
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                "ConnectTimeout=1",
                "jupiter",
                "curl",
                "-sf",
                "--max-time",
                "1",
                "http://127.0.0.1:9222/json/list",
            ],
            capture_output=True,
            text=True,
            timeout=_JUPITER_PROBE_TIMEOUT_S,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout.strip():
            payload = json.loads(proc.stdout)
            if isinstance(payload, list):
                pages = sum(
                    1
                    for item in payload
                    if isinstance(item, dict) and item.get("type") == "page"
                )
                return {
                    "cdp_ports_live": 1,
                    "cdp_targets_total": len(payload),
                    "cdp_page_targets": pages,
                    "ports_checked": len(_JUPITER_CDP_PORT_RANGE),
                    "via": "ssh",
                }
    return {
        "cdp_ports_live": 0,
        "cdp_targets_total": 0,
        "cdp_page_targets": 0,
        "ports_checked": len(_JUPITER_CDP_PORT_RANGE),
        "via": "unreachable",
    }


def collect_local_browser_census(
    *,
    host: str = "io",
    proc_root: Path = Path("/proc"),
    disk_path: str = "/",
    cdp_ports: Sequence[int] = range(9338, 9344),
    include_jupiter: bool = False,
) -> dict[str, Any]:
    mem_kib = _read_meminfo_kib(proc_root)
    disk_free_gib, inode_free_pct = _read_disk_statvfs(disk_path)
    profiles, xvfb_rss_mib = _enumerate_processes(proc_root)

    total_pss = 0.0
    total_rss = 0.0
    pss_seen = False
    rss_seen = False
    profile_summaries: dict[str, Any] = {}
    for profile, procs in profiles.items():
        prof_pss = 0.0
        prof_rss = 0.0
        for proc in procs:
            if proc.get("pss_mib") is not None:
                prof_pss += float(proc["pss_mib"])
                pss_seen = True
            if proc.get("rss_mib") is not None:
                prof_rss += float(proc["rss_mib"])
                rss_seen = True
        total_pss += prof_pss
        total_rss += prof_rss
        profile_summaries[profile] = {
            "process_count": len(procs),
            "pss_mib": prof_pss if prof_pss else None,
            "rss_mib": prof_rss if prof_rss else None,
        }

    cdp_ports_live = 0
    cdp_targets_total = 0
    cdp_page_targets = 0
    cdp_by_port: dict[str, dict[str, int]] = {}
    for port in cdp_ports:
        is_live, total, pages = _probe_cdp_json_list(
            "127.0.0.1", int(port), timeout=_CDP_PROBE_TIMEOUT_S
        )
        if not is_live:
            continue
        cdp_ports_live += 1
        cdp_targets_total += total
        cdp_page_targets += pages
        cdp_by_port[str(port)] = {"targets": total, "page_targets": pages}

    details: dict[str, Any] = {
        "profiles": profile_summaries,
        "xvfb_rss_mib": xvfb_rss_mib,
        "cdp_by_port": cdp_by_port,
    }
    if include_jupiter:
        details["jupiter"] = _probe_jupiter_cdp()

    profile_count = sum(
        1 for key in profiles if key != "__unprofiled__" and profiles[key]
    )

    return {
        "host": host,
        "mem_available_mib": _kib_to_mib(mem_kib.get("MemAvailable")),
        "swap_free_mib": _kib_to_mib(mem_kib.get("SwapFree")),
        "swap_total_mib": _kib_to_mib(mem_kib.get("SwapTotal")),
        "disk_free_gib": disk_free_gib,
        "inode_free_pct": inode_free_pct,
        "total_pss_mib": total_pss if pss_seen else None,
        "total_rss_mib": total_rss if rss_seen else None,
        "xvfb_rss_mib": xvfb_rss_mib,
        "cdp_ports_live": cdp_ports_live,
        "cdp_targets_total": cdp_targets_total,
        "cdp_page_targets": cdp_page_targets,
        "profile_count": profile_count,
        "details": details,
    }


def run_census_and_emit(
    *,
    host: str = "io",
    include_jupiter: bool = False,
    source: str = "cdp_ask",
) -> dict[str, Any] | None:
    if not census_enabled():
        return None
    census = collect_local_browser_census(host=host, include_jupiter=include_jupiter)
    event = browser_resource_census(
        host=str(census["host"]),
        mem_available_mib=census.get("mem_available_mib"),
        swap_free_mib=census.get("swap_free_mib"),
        swap_total_mib=census.get("swap_total_mib"),
        disk_free_gib=census.get("disk_free_gib"),
        inode_free_pct=census.get("inode_free_pct"),
        total_pss_mib=census.get("total_pss_mib"),
        total_rss_mib=census.get("total_rss_mib"),
        cdp_ports_live=int(census.get("cdp_ports_live") or 0),
        cdp_page_targets=int(census.get("cdp_page_targets") or 0),
        profile_count=int(census.get("profile_count") or 0),
        details=census.get("details"),
    )
    emit_browser_event(event, source=source)
    return census


def check_ephemeral_admission_preflight(
    *,
    caller: str,
    snapshot: Mapping[str, Any] | None = None,
    host: str = "io",
) -> dict[str, Any]:
    mode = admission_preflight_mode()
    if mode == "off":
        return {"allowed": True, "mode": "off", "warnings": []}

    data = dict(snapshot) if snapshot is not None else collect_local_browser_census(host=host)
    warnings: list[str] = []
    mem = data.get("mem_available_mib")
    swap = data.get("swap_free_mib")
    disk = data.get("disk_free_gib")

    if mem is not None and float(mem) < warn_mem_available_mib():
        warnings.append(
            f"mem_available_mib={mem} below warn threshold {warn_mem_available_mib()}"
        )
    if swap is not None and float(swap) < warn_swap_free_mib():
        warnings.append(
            f"swap_free_mib={swap} below warn threshold {warn_swap_free_mib()}"
        )
    if disk is not None and float(disk) < warn_disk_free_gib():
        warnings.append(
            f"disk_free_gib={disk} below warn threshold {warn_disk_free_gib()}"
        )

    if warnings:
        emit_browser_event(
            browser_admission_warn(
                host=host,
                caller=caller,
                warnings=warnings,
                mem_available_mib=mem if isinstance(mem, (int, float)) else None,
                swap_free_mib=swap if isinstance(swap, (int, float)) else None,
                disk_free_gib=disk if isinstance(disk, (int, float)) else None,
            ),
            source="browser-governance",
        )
        for msg in warnings:
            logger.warning("browser admission preflight (%s): %s", caller, msg)

    return {
        "allowed": True,
        "mode": "warn",
        "warnings": warnings,
        "mem_available_mib": mem,
        "swap_free_mib": swap,
        "disk_free_gib": disk,
    }


# Hygiene throttle state (monotonic seconds).
_last_census_monotonic: float = 0.0


def census_interval_s() -> float:
    raw = os.environ.get("ULG_BROWSER_CENSUS_INTERVAL_S", "60.0").strip()
    try:
        value = float(raw)
    except ValueError:
        return 60.0
    return value if value > 0 else 60.0


def run_census_throttled(*, source: str = "cdp_ask") -> dict[str, Any] | None:
    """Run census+emit at most once per ``ULG_BROWSER_CENSUS_INTERVAL_S``."""
    global _last_census_monotonic
    now = time.monotonic()
    if now - _last_census_monotonic < census_interval_s():
        return None
    _last_census_monotonic = now
    return run_census_and_emit(source=source)
