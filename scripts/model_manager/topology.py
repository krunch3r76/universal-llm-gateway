"""Topology snapshot — aggregates service state, config, and model probe."""

import json
import logging
import sys
import time
import urllib.request
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse

import yaml

from scripts.model_manager.ui.model.service_state import (
    ServiceInfo,
    ServiceState,
    ServiceStatus,
)

logger = logging.getLogger(__name__)

_GATEWAY_DIR = Path.home() / ".gateway"
_CONFIG_FILE = _GATEWAY_DIR / "stargate.yaml"
_NODES_DIR = _GATEWAY_DIR / "nodes"
TOPOLOGY_FILE = _GATEWAY_DIR / "topology.yaml"

_PROBE_TIMEOUT = 3


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class MasterInfo:
    """State and config of the Master Stargate host process."""

    stargate_id: str
    status: str
    pid: int | None
    port: int
    config: str


@dataclass(slots=True, kw_only=True)
class EdgeInfo:
    """State of the local Edge container (Gateway + Stargate over UDS)."""

    stargate_id: str
    status: str
    container: str | None
    socket: str
    gateway_port: int


@dataclass(slots=True, kw_only=True)
class RemoteInfo:
    """State of a federated remote relay node."""

    stargate_id: str
    url: str
    status: str
    node_env: str | None
    model_count: int | None = None
    status_reason: str | None = None


_REMOTE_REASON_TEXT: dict[str, str] = {
    "master_down": "master stargate is not running",
    "node_env_missing": "node env file missing under ~/.gateway/nodes",
    "no_recent_telemetry": "no recent federation telemetry for relay",
    "connected_no_models": "relay connected but zero models advertised",
    "connected_models_unknown": "relay connected but model source probe unavailable",
}


@dataclass(slots=True, kw_only=True)
class ModelSummary:
    """Aggregate model counts for the topology snapshot."""

    total: int
    available_via_api: int | None


@dataclass(slots=True, kw_only=True)
class Diagnostic:
    """A human-readable warning or error surfaced in the topology view."""

    level: str
    message: str


def _node_icon(status: str) -> str:
    match status:
        case "running" | "connected":
            return "●"
        case "unreachable":
            return "◌"
        case _:
            return "○"


@dataclass(slots=True, kw_only=True)
class TopologySnapshot:
    generated_at: str
    master: MasterInfo
    local_edge: EdgeInfo | None
    remotes: list[RemoteInfo]
    models: ModelSummary
    diagnostics: list[Diagnostic]

    def to_dict(self) -> dict[str, object]:
        """Nested dict suitable for ``yaml.dump()``."""
        d: dict[str, object] = asdict(self)
        d["topology_diagram"] = self.to_diagram()
        return d

    def to_yaml(self) -> str:
        return yaml.dump(self.to_dict(), default_flow_style=False, sort_keys=False)

    def to_diagram(self) -> str:
        """ASCII tree with status icons."""
        lines: list[str] = []
        m = self.master
        icon = "●" if m.status == "running" else "○"
        pid_part = f", PID {m.pid}" if m.pid else ""
        lines.append(f"Master ({m.stargate_id}, :{m.port}{pid_part}) {icon} {m.status}")

        has_remotes = bool(self.remotes)

        if self.local_edge:
            e = self.local_edge
            e_icon = "●" if e.status == "running" else "○"
            branch = "├─" if has_remotes else "└─"
            container = f" → {e.container}" if e.container else ""
            lines.append(f"  {branch} local_edge (UDS){container} {e_icon} {e.status}")
            trunk = "│" if has_remotes else " "
            lines.append(f"  {trunk}   └─ Gateway (:{e.gateway_port})")

        if self.remotes:
            lines.append("  └─ remotes")
            for i, r in enumerate(self.remotes):
                r_icon = _node_icon(r.status)
                branch = "└─" if i == len(self.remotes) - 1 else "├─"
                models = (
                    f", {r.model_count} models" if r.model_count is not None else ""
                )
                lines.append(
                    f"      {branch} {r.stargate_id} ({r.url}) {r_icon} {r.status}{models}"
                )

        if self.models.total > 0 or self.models.available_via_api is not None:
            api = ""
            if self.models.available_via_api is not None:
                api = f", {self.models.available_via_api} via API"
            lines.append(f"  Models: {self.models.total} in catalog{api}")

        for d in self.diagnostics:
            lines.append(f"  ⚠ {d.message}")

        return "\n".join(lines)

    def write(self, path: Path | None = None) -> Path:
        """Write snapshot YAML to disk (default: ``~/.gateway/topology.yaml``)."""
        target = path or TOPOLOGY_FILE
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(self.to_yaml())
        return target


# ---------------------------------------------------------------------------
# Builder helpers
# ---------------------------------------------------------------------------


def _read_config(diagnostics: list[Diagnostic]) -> dict[str, object]:
    if not _CONFIG_FILE.exists():
        diagnostics.append(
            Diagnostic(level="warn", message=f"Config not found: {_CONFIG_FILE}")
        )
        return {}
    try:
        return yaml.safe_load(_CONFIG_FILE.read_text()) or {}
    except yaml.YAMLError as e:
        diagnostics.append(
            Diagnostic(level="error", message=f"Config parse error: {e}")
        )
        return {}


def _build_local_edge(
    config: dict[str, object], gw_info: ServiceInfo | None
) -> EdgeInfo | None:
    fed: dict[str, object] = config.get("federation", {})  # type: ignore[assignment]
    edge_cfg: dict[str, object] | None = fed.get("local_edge")  # type: ignore[assignment]
    if not edge_cfg:
        return None
    return EdgeInfo(
        stargate_id=edge_cfg.get("stargate_id", "edge-localhost"),
        status=gw_info.status.value if gw_info else "unknown",
        container=gw_info.container_name if gw_info else None,
        socket=str(edge_cfg.get("socket_path", "")),
        gateway_port=ServiceState.GATEWAY_PORT,
    )


def _hostname_from_url(url: str) -> str:
    try:
        return urlparse(url).hostname or "unknown"
    except Exception:
        return "unknown"


_EVENTS_FILE = Path("/tmp/stargate-events/current.jsonl")
# A relay broadcasting telemetry within this window is considered connected.
_TELEMETRY_RECENCY_S = 30.0


def probe_recent_relay_states(
    events_file: Path = _EVENTS_FILE,
    *,
    recency_seconds: float = _TELEMETRY_RECENCY_S,
) -> dict[str, str]:
    """Return best-known relay state reason keyed by remote_id."""
    if not events_file.exists():
        return {}

    cutoff = time.time() - recency_seconds
    states: dict[str, str] = {}
    try:
        with events_file.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 16384))
            tail = fh.read().decode(errors="replace")
    except OSError as e:
        logger.warning("Could not read events file %s: %s", events_file, e)
        return {}

    for line in tail.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue

        ts_str = event.get("timestamp", "")
        try:
            ts = datetime.fromisoformat(ts_str).timestamp()
        except (ValueError, TypeError):
            continue
        if ts < cutoff:
            continue

        signal = event.get("signal")
        payload = event.get("payload", {})
        remote_id = payload.get("remote_id")
        if not remote_id:
            continue

        if signal == "federation.connection.lost":
            states[remote_id] = "no_recent_telemetry"
        elif signal == "federation.telemetry.marked.stale":
            states[remote_id] = "no_recent_telemetry"

    return states


def probe_connected_relays(events_file: Path = _EVENTS_FILE) -> set[str]:
    """Return remote_ids that sent federation.telemetry.received recently.

    Scans the tail of the master's events file for recent telemetry signals.
    A relay present here is live even when it has zero models loaded — the
    model-source probe alone cannot distinguish "connected, no models" from
    "unreachable".
    """
    if not events_file.exists():
        return set()
    connected: set[str] = set()
    cutoff = time.time() - _TELEMETRY_RECENCY_S
    try:
        # Read from near the end; telemetry arrives every ~5 s so 4 KB is ample.
        with events_file.open("rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 4096))
            tail = fh.read().decode(errors="replace")
        for line in tail.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("signal") != "federation.telemetry.received":
                continue
            ts_str: str = event.get("timestamp", "")
            try:
                ts = datetime.fromisoformat(ts_str).timestamp()
            except (ValueError, TypeError):
                continue
            if ts >= cutoff:
                remote_id = event.get("payload", {}).get("remote_id", "")
                if remote_id:
                    connected.add(remote_id)
    except OSError as e:
        logger.warning("Could not read events file %s: %s", events_file, e)
    return connected


def probe_federation_sources(master_port: int) -> dict[str, int] | None:
    """Query local master for federated model sources.

    Calls ``GET localhost:{port}/v1/models?include_sources=true`` and extracts
    ``_debug_sources`` to build a ``{stargate_id: model_count}`` mapping.
    One localhost call replaces N possibly-failing remote probes.
    """
    url = f"http://localhost:{master_port}/v1/models?include_sources=true"
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
            data = json.loads(resp.read())
    except Exception as e:
        logger.warning("Federation source probe failed: %s", e)
        return None

    debug_sources: dict[str, list[str]] = data.get("_debug_sources", {})
    counts: dict[str, int] = {}
    for stargate_ids in debug_sources.values():
        for sid in stargate_ids:
            counts[sid] = counts.get(sid, 0) + 1
    return counts


def _build_remotes(
    config: dict[str, object],
    diagnostics: list[Diagnostic],
    *,
    master_port: int,
    master_running: bool,
) -> list[RemoteInfo]:
    fed: dict[str, object] = config.get("federation", {})  # type: ignore[assignment]
    remote_cfgs: list[dict[str, object]] = fed.get("remotes") or []  # type: ignore[assignment]

    sources = probe_federation_sources(master_port) if master_running else None
    connected_relays = probe_connected_relays() if master_running else set()
    recent_states = probe_recent_relay_states() if master_running else {}

    remotes: list[RemoteInfo] = []
    for rc in remote_cfgs:
        sid = str(rc.get("stargate_id", "unknown"))
        url = str(rc.get("url", ""))
        hostname = _hostname_from_url(url)
        node_env_path = _NODES_DIR / f"{hostname}.env"
        node_env = str(node_env_path) if node_env_path.exists() else None

        model_count = sources.get(sid) if sources is not None else None
        status_reason: str | None = None
        if model_count is not None:
            status = "running"
            if sid in connected_relays and model_count == 0:
                status_reason = "connected_no_models"
        elif sid in connected_relays:
            status = "running"
            status_reason = (
                "connected_models_unknown" if sources is None else "connected_no_models"
            )
        elif not master_running:
            status = "configured"
            status_reason = "master_down"
        else:
            status = "unreachable"
            status_reason = recent_states.get(sid, "no_recent_telemetry")

        if node_env is None:
            diagnostics.append(
                Diagnostic(
                    level="warn",
                    message=f"{sid}: {_REMOTE_REASON_TEXT['node_env_missing']}",
                )
            )
            # node_env missing is always the most actionable status_reason:
            # even a "running" relay can't be deployed to without the env file.
            status_reason = "node_env_missing"

        remotes.append(
            RemoteInfo(
                stargate_id=sid,
                url=url,
                status=status,
                node_env=node_env,
                model_count=model_count,
                status_reason=status_reason,
            )
        )
    return remotes


def _probe_models(
    port: int, sg_info: ServiceInfo | None, diagnostics: list[Diagnostic]
) -> ModelSummary:
    available_via_api: int | None = None
    if sg_info and sg_info.status == ServiceStatus.RUNNING:
        try:
            url = f"http://localhost:{port}/v1/models"
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=_PROBE_TIMEOUT) as resp:
                data = json.loads(resp.read())
                available_via_api = len(data.get("data", []))
        except Exception as e:
            logger.warning("Model probe failed: %s", e)
            diagnostics.append(
                Diagnostic(level="warn", message=f"Model probe failed: {e}")
            )
    total = available_via_api or 0
    return ModelSummary(total=total, available_via_api=available_via_api)


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------


def build_snapshot(
    workspace_root: Path,
    *,
    services: list[ServiceInfo] | None = None,
) -> TopologySnapshot:
    """Build a point-in-time topology snapshot.

    Args:
        workspace_root: Repository root path.
        services: Pre-checked service info list. When ``None``, runs
            ``ServiceState.check_all()`` internally (used by CLI).
    """
    diagnostics: list[Diagnostic] = []

    if services is None:
        services = ServiceState(workspace_root).check_all()

    gw_info = next((s for s in services if s.name == "Gateway"), None)
    sg_info = next((s for s in services if s.name == "Stargate"), None)

    config = _read_config(diagnostics)
    fed: dict[str, object] = config.get("federation", {})  # type: ignore[assignment]
    stargate_id = str(fed.get("stargate_id", "master-localhost"))
    proxy_cfg: dict[str, object] = config.get("proxy") or {}  # type: ignore[assignment]
    port = int(proxy_cfg.get("port", 9999))  # type: ignore[arg-type]

    master = MasterInfo(
        stargate_id=stargate_id,
        status=sg_info.status.value if sg_info else "unknown",
        pid=sg_info.pid if sg_info else None,
        port=port,
        config=str(_CONFIG_FILE) if _CONFIG_FILE.exists() else "missing",
    )

    sg_running = sg_info is not None and sg_info.status == ServiceStatus.RUNNING

    return TopologySnapshot(
        generated_at=datetime.now(UTC).isoformat(),
        master=master,
        local_edge=_build_local_edge(config, gw_info),
        remotes=_build_remotes(
            config,
            diagnostics,
            master_port=port,
            master_running=sg_running,
        ),
        models=_probe_models(port, sg_info, diagnostics),
        diagnostics=diagnostics,
    )


# ---------------------------------------------------------------------------
# CLI entry point: ./manage topology | python -m scripts.model_manager.topology
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from scripts.model_manager.ensure_venv import find_workspace_root

    logging.basicConfig(level=logging.WARNING)
    snapshot = build_snapshot(find_workspace_root())
    yaml_text = snapshot.to_yaml()
    print(yaml_text, end="")
    out = snapshot.write()
    print(f"# Written to {out}", file=sys.stderr)
    sys.exit(0)
