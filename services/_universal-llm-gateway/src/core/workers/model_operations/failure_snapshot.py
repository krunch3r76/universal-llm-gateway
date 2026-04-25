"""Build a worker/process/resource snapshot at the moment of a load failure.

This snapshot is attached to MODEL_LOAD_FAILED events and forwarded to
Stargate so that operators can see the live state of the gateway when a
load actually failed (peer workers, llama-cpp/vLLM child processes,
hardware VRAM/RAM), not the lagging WebSocket-cached view.

All capture is best-effort — psutil/pynvml errors degrade gracefully to
None or partial fields. A snapshot failure must never block the failure
event itself.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from universal_logging import get_logger

if TYPE_CHECKING:
    from ..controller import WorkerController

logger = get_logger(__name__)

_PROC_TREE_NAME_LIMIT = 8  # Cap to avoid pathological process trees in payload


def build_worker_snapshot(
    controller: WorkerController,
    failed_model_id: str,
) -> dict[str, Any] | None:
    """Capture worker/process/resource state at the moment of a load failure.

    Returns a JSON-serializable dict with three sections:
      - failed_worker: process info for the model that failed to load
      - peer_workers: process info for all other supervised workers
      - resources: hardware VRAM/RAM totals at snapshot time

    Returns None on catastrophic capture failure (snapshot must never
    raise into the failure path).
    """
    try:
        snapshot: dict[str, Any] = {
            "failed_worker": _capture_failed_worker(controller, failed_model_id),
            "peer_workers": _capture_peer_workers(controller, failed_model_id),
            "resources": _capture_resources(),
        }
        return snapshot
    except Exception as e:
        logger.warning(
            "Failed to build worker_snapshot for %s: %s",
            failed_model_id,
            e,
            exc_info=True,
        )
        return None


def _capture_failed_worker(
    controller: WorkerController, model_id: str
) -> dict[str, Any]:
    procs = _safe_get_all_process_info(controller)
    info = procs.get(model_id) if isinstance(procs, dict) else None

    pid: int | None = None
    supervisor_status: str | None = None
    if isinstance(info, dict):
        pid_val = info.get("pid")
        pid = int(pid_val) if isinstance(pid_val, int) else None
        status_val = info.get("status")
        supervisor_status = str(status_val) if status_val is not None else None

    out: dict[str, Any] = {
        "model_id": model_id,
        "pid": pid,
        "supervisor_status": supervisor_status,
        "child_processes": _describe_process_tree(pid) if pid else [],
    }
    return out


def _capture_peer_workers(
    controller: WorkerController, failed_model_id: str
) -> list[dict[str, Any]]:
    procs = _safe_get_all_process_info(controller)
    if not isinstance(procs, dict):
        return []

    peers: list[dict[str, Any]] = []
    for peer_model_id, info in procs.items():
        if peer_model_id == failed_model_id:
            continue
        if not isinstance(info, dict):
            continue
        pid_val = info.get("pid")
        pid = int(pid_val) if isinstance(pid_val, int) else None
        peers.append(
            {
                "model_id": peer_model_id,
                "pid": pid,
                "status": info.get("status"),
                "child_processes": _describe_process_tree(pid) if pid else [],
            }
        )
    return peers


def _capture_resources() -> dict[str, Any]:
    from src.core.resources.hardware import get_ram_info, get_vram_info

    vram = get_vram_info()
    ram = get_ram_info()
    total_vram = int(vram.get("total_vram_mb", 0))
    avail_vram = int(vram.get("available_vram_mb", 0))
    total_ram = int(ram.get("total_ram_mb", 0))
    avail_ram = int(ram.get("available_ram_mb", 0))
    return {
        "total_vram_mb": total_vram,
        "available_vram_mb": avail_vram,
        "hardware_used_vram_mb": max(0, total_vram - avail_vram),
        "total_ram_mb": total_ram,
        "available_ram_mb": avail_ram,
        "hardware_used_ram_mb": max(0, total_ram - avail_ram),
    }


def _safe_get_all_process_info(
    controller: WorkerController,
) -> dict[str, Any] | None:
    try:
        return controller.get_all_process_info()
    except Exception as e:
        logger.warning("get_all_process_info failed during snapshot: %s", e)
        return None


def _describe_process_tree(pid: int) -> list[dict[str, Any]]:
    """Enumerate parent + descendant processes with name and RSS.

    Returns up to _PROC_TREE_NAME_LIMIT entries. Captures llama-server /
    vLLM EngineCore subprocesses by walking psutil.children(recursive=True).
    """
    from src.core.resources.hardware import (
        PSUTIL_AVAILABLE,
        get_process_gpu_memory,
        psutil,
    )

    if not PSUTIL_AVAILABLE or not pid:
        return []

    out: list[dict[str, Any]] = []
    try:
        parent = psutil.Process(pid)
        out.append(_describe_process(parent))
        for child in parent.children(recursive=True):
            if len(out) >= _PROC_TREE_NAME_LIMIT:
                break
            out.append(_describe_process(child))
    except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
        return out
    except Exception as e:
        logger.warning("describe_process_tree failed for pid=%s: %s", pid, e)
        return out

    tree_vram = get_process_gpu_memory(pid)
    if tree_vram is not None and out:
        out[0]["tree_vram_mb"] = tree_vram
    return out


def _describe_process(proc: Any) -> dict[str, Any]:
    """Return per-process descriptor; tolerates dead/inaccessible processes."""
    from src.core.resources.hardware import psutil  # noqa: PLC0415

    _dead = (psutil.AccessDenied, psutil.NoSuchProcess, psutil.ZombieProcess)
    descriptor: dict[str, Any] = {"pid": proc.pid}
    try:
        descriptor["name"] = proc.name()
    except _dead:
        descriptor["name"] = None
    try:
        descriptor["rss_mb"] = int(proc.memory_info().rss / (1024 * 1024))
    except _dead:
        descriptor["rss_mb"] = None
    try:
        descriptor["status"] = proc.status()
    except _dead:
        descriptor["status"] = None
    return descriptor
