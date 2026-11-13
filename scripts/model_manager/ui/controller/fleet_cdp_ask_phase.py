"""Fleet ownership of remote cdp-ask stop/start (AC1).

When Jupiter uses the shared NFS checkout, the local fleet phase already stops/starts
remote cdp-ask via SSH; the remote deploy phase must not restart again. When NFS is
unavailable, rsync+restart stays in the remote phase only — the local phase omits cdp-ask.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .fleet_decision_events import record_fleet_cdp_ask_restart_decision
from .service_config import (
    cdp_ask_url_config,
    is_cdp_ask_local_host,
    is_cdp_ask_manage_enabled,
    resolve_cdp_ask_remote_target,
)

if TYPE_CHECKING:
    from pathlib import Path


async def include_cdp_ask_in_local_fleet_phase(root: Path) -> bool:
    """True when this fleet op should stop/start cdp-ask in the local phase."""
    if not is_cdp_ask_manage_enabled():
        await record_fleet_cdp_ask_restart_decision(
            phase="local",
            action="skip",
            reason="cdp_ask_manage_disabled",
        )
        return False
    cfg = cdp_ask_url_config()
    if cfg is None:
        await record_fleet_cdp_ask_restart_decision(
            phase="local",
            action="restart",
            reason="nfs_local_phase_owns",
        )
        return True
    host, _port, _base = cfg
    if is_cdp_ask_local_host(host):
        await record_fleet_cdp_ask_restart_decision(
            phase="local",
            action="restart",
            reason="nfs_local_phase_owns",
            hostname=host,
        )
        return True
    from .service_ctl.cdp_ask_remote import nfs_checkout_ready

    resolved = resolve_cdp_ask_remote_target(host)
    remote_hostname = resolved[0] if resolved else host
    if await nfs_checkout_ready(root):
        await record_fleet_cdp_ask_restart_decision(
            phase="local",
            action="restart",
            reason="nfs_local_phase_owns",
            hostname=remote_hostname,
        )
        return True
    await record_fleet_cdp_ask_restart_decision(
        phase="local",
        action="skip",
        reason="rsync_remote_phase_owns",
        hostname=remote_hostname,
    )
    return False
