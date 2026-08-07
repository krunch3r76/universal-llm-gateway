"""CSE attach ladder + closable/protected classification for orphan scan (S1)."""

from __future__ import annotations

import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from claude_bundles import cdp_registry
from claude_bundles.cdp_reclaim_refuse import reclaim_refuse_reason
from claude_bundles.cse_idle_probe import in_flight_from_state, probe_page_liveness_sync

CSE_URL_MARKER = "claude.ai/cowork/cse_"
_DEFAULT_CSE_IDLE_DWELL_S = 300.0

CseClassification = Literal["closable", "protected"]
AttachResolution = Literal["chat_url", "registration_id", "execution_id"]

# Scan-ephemeral dwell tracking — ``closable`` is not persisted on registry rows (S2).
_idle_since: dict[tuple[int, str], float] = {}

FetchJsonFn = Callable[[str], Any | None]


@dataclass(frozen=True)
class CseTarget:
    """One observed ``/cowork/cse_*`` page on a probed port."""

    url: str
    target_id: str | None
    classification: CseClassification
    attach_resolution: AttachResolution | None
    attach_registration_id: str | None
    idle_probe_ok: bool
    in_flight: bool | None
    idle_dwell_s: float | None
    classification_reason: str


def cse_idle_dwell_s() -> float:
    raw = os.environ.get("CDP_CSE_IDLE_DWELL_S", "").strip()
    if not raw:
        return _DEFAULT_CSE_IDLE_DWELL_S
    try:
        value = float(raw)
    except ValueError:
        return _DEFAULT_CSE_IDLE_DWELL_S
    return value if value > 0 else _DEFAULT_CSE_IDLE_DWELL_S


def normalize_cse_url(url: str) -> str:
    """Normalize CSE URLs for exact comparison (strip fragment, trailing slash)."""
    from urllib.parse import urlsplit, urlunsplit

    raw = (url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    path = parts.path.rstrip("/") or parts.path
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, ""))


def cse_pages_from_list(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, list):
        return []
    out: list[dict[str, Any]] = []
    for item in payload:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if isinstance(url, str) and CSE_URL_MARKER in url:
            out.append(item)
    return out


def build_chat_url_index(fetch_json: FetchJsonFn) -> dict[str, str]:
    """Map normalized CSE URL → registration_id from attached registry lanes."""
    index: dict[str, str] = {}
    for lane in cdp_registry.list_active():
        page_list = fetch_json(f"{lane.cdp_url.rstrip('/')}/json/list")
        for page in cse_pages_from_list(page_list):
            url = normalize_cse_url(str(page.get("url") or ""))
            if url:
                index[url] = lane.registration_id
    return index


def registration_id_for_profile(profile: Path) -> str | None:
    resolved = profile.resolve()
    for rid, row in cdp_registry._load_active().items():
        if row.get("status") not in {"active", "orphaned_alive"}:
            continue
        row_profile = cdp_registry._profile_path_from_row(row)
        if row_profile is not None and row_profile.resolve() == resolved:
            return rid
    return None


def running_registration_ids() -> set[str]:
    """Running-store registration ids when cdp-ask execution store is importable."""
    try:
        from cdp_ask import app as cdp_app_module  # noqa: PLC0415

        app = getattr(cdp_app_module, "app", None)
        if app is not None and hasattr(app, "state"):
            store = app.state.execution_store
            snap = getattr(store, "running_registration_ids_snapshot", None)
            if callable(snap):
                return snap()
    except Exception:
        pass
    return set()


def resolve_attach(
    cse_url: str,
    *,
    profile: Path | None,
    chat_url_index: dict[str, str],
    running_registration_ids: set[str],
) -> tuple[AttachResolution | None, str | None]:
    norm = normalize_cse_url(cse_url)
    if norm and norm in chat_url_index:
        return "chat_url", chat_url_index[norm]
    if profile is not None:
        rid = registration_id_for_profile(profile)
        if rid is not None:
            return "registration_id", rid
        if running_registration_ids:
            resolved = profile.resolve()
            for run_rid in running_registration_ids:
                row = cdp_registry._load_active().get(run_rid, {})
                row_profile = cdp_registry._profile_path_from_row(row)
                if row_profile is not None and row_profile.resolve() == resolved:
                    return "execution_id", run_rid
    return None, None


def update_idle_dwell(
    port: int,
    cse_url: str,
    *,
    is_idle: bool,
    now: float,
) -> float:
    key = (port, normalize_cse_url(cse_url))
    if not is_idle:
        _idle_since.pop(key, None)
        return 0.0
    started = _idle_since.setdefault(key, now)
    return max(0.0, now - started)


def classify_cse_target(
    page: dict[str, Any],
    *,
    port: int,
    profile: Path | None,
    chat_url_index: dict[str, str],
    running_registration_ids: set[str],
    now: float,
) -> CseTarget:
    url = str(page.get("url") or "")
    target_id = page.get("id")
    tid = str(target_id) if target_id is not None else None
    # Arc 6893: by-id refuse is a code gate (not ops prose). Runs before attach
    # so a TTL-killed / deregistered seat stays protected by CSE id alone.
    by_id_reason = reclaim_refuse_reason(url)
    if by_id_reason is not None:
        return CseTarget(
            url=url,
            target_id=tid,
            classification="protected",
            attach_resolution=None,
            attach_registration_id=None,
            idle_probe_ok=True,
            in_flight=None,
            idle_dwell_s=None,
            classification_reason=by_id_reason,
        )
    attach_path, attach_rid = resolve_attach(
        url,
        profile=profile,
        chat_url_index=chat_url_index,
        running_registration_ids=running_registration_ids,
    )
    if attach_path is not None:
        return CseTarget(
            url=url,
            target_id=tid,
            classification="protected",
            attach_resolution=attach_path,
            attach_registration_id=attach_rid,
            idle_probe_ok=True,
            in_flight=None,
            idle_dwell_s=None,
            classification_reason=f"attach_resolved:{attach_path}",
        )
    ws_url = page.get("webSocketDebuggerUrl")
    if not isinstance(ws_url, str) or not ws_url.strip():
        return CseTarget(
            url=url,
            target_id=tid,
            classification="protected",
            attach_resolution=None,
            attach_registration_id=None,
            idle_probe_ok=False,
            in_flight=None,
            idle_dwell_s=None,
            classification_reason="idle_probe_unavailable:no_websocket",
        )
    state, probe_ok = probe_page_liveness_sync(port, ws_url)
    if not probe_ok or state is None:
        return CseTarget(
            url=url,
            target_id=tid,
            classification="protected",
            attach_resolution=None,
            attach_registration_id=None,
            idle_probe_ok=False,
            in_flight=None,
            idle_dwell_s=None,
            classification_reason="idle_probe_unavailable:evaluate_failed",
        )
    if in_flight_from_state(state):
        update_idle_dwell(port, url, is_idle=False, now=now)
        return CseTarget(
            url=url,
            target_id=tid,
            classification="protected",
            attach_resolution=None,
            attach_registration_id=None,
            idle_probe_ok=True,
            in_flight=True,
            idle_dwell_s=0.0,
            classification_reason="in_flight:streaming_or_stop_or_tool_pause",
        )
    dwell = update_idle_dwell(port, url, is_idle=True, now=now)
    dwell_required = cse_idle_dwell_s()
    if dwell >= dwell_required:
        return CseTarget(
            url=url,
            target_id=tid,
            classification="closable",
            attach_resolution=None,
            attach_registration_id=None,
            idle_probe_ok=True,
            in_flight=False,
            idle_dwell_s=dwell,
            classification_reason=f"unattachable_idle_dwell>={dwell_required:.0f}s",
        )
    return CseTarget(
        url=url,
        target_id=tid,
        classification="protected",
        attach_resolution=None,
        attach_registration_id=None,
        idle_probe_ok=True,
        in_flight=False,
        idle_dwell_s=dwell,
        classification_reason=f"idle_dwell<{dwell_required:.0f}s",
    )


def classify_port_cse_targets(
    port: int,
    *,
    profile: Path | None,
    page_list: Any,
    fetch_json: FetchJsonFn,
) -> tuple[CseTarget, ...]:
    pages = cse_pages_from_list(page_list)
    if not pages:
        return ()
    chat_index = build_chat_url_index(fetch_json)
    running = running_registration_ids()
    now = time.time()
    return tuple(
        classify_cse_target(
            page,
            port=port,
            profile=profile,
            chat_url_index=chat_index,
            running_registration_ids=running,
            now=now,
        )
        for page in pages
    )
