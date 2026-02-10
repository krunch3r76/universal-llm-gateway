"""Async request snapshot writer for debugging request transformations."""

import json
import os
from datetime import datetime
from pathlib import Path

import aiofiles
from universal_logging import get_logger

logger = get_logger(__name__)

_SNAPSHOT_ENABLED: bool | None = None
_SNAPSHOT_DIR: Path | None = None


def _is_enabled() -> bool:
    """Check if snapshot writing is enabled (cached)."""
    global _SNAPSHOT_ENABLED
    if _SNAPSHOT_ENABLED is None:
        env_value = os.getenv("STARGATE_DEBUG_REQUEST_SNAPSHOTS", "false").lower()
        _SNAPSHOT_ENABLED = env_value in ("true", "1", "yes")
        if _SNAPSHOT_ENABLED:
            logger.info("Request snapshot debugging ENABLED")
    return _SNAPSHOT_ENABLED


def _get_snapshot_dir() -> Path:
    """Get snapshot directory, creating if needed."""
    global _SNAPSHOT_DIR
    if _SNAPSHOT_DIR is None:
        data_dir = os.getenv("DATA_DIR", "/tmp")
        _SNAPSHOT_DIR = Path(data_dir) / "stargate-request-snapshots"
        _SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    return _SNAPSHOT_DIR


async def write_request_snapshot(
    data: dict,
    request_id: str,
    stage: str,
) -> None:
    """
    Write request snapshot to file if debugging enabled.

    Args:
        data: Request dictionary to snapshot
        request_id: Unique request identifier
        stage: "before" or "after" modification

    Safe no-op if STARGATE_DEBUG_REQUEST_SNAPSHOTS is not set.
    Errors are logged but do not interrupt request flow.

    Files are written to subdirectories:
        - before: {snapshot_dir}/before/
        - after: {snapshot_dir}/after/
    """
    if not _is_enabled():
        return

    try:
        base_dir = _get_snapshot_dir()
        stage_dir = base_dir / stage
        stage_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().isoformat(timespec="microseconds")
        safe_request_id = request_id.replace("/", "-")[:32]
        filename = f"{timestamp}_{safe_request_id}.json"
        filepath = stage_dir / filename

        json_content = json.dumps(data, indent=2, ensure_ascii=False)

        async with aiofiles.open(filepath, "w") as f:
            await f.write(json_content)

        logger.debug(f"Wrote request snapshot: {stage}/{filename}")
    except Exception as e:
        logger.warning(f"Failed to write request snapshot: {e}")


async def write_response_snapshot(
    data: dict | bytes,
    request_id: str,
    stage: str,
) -> None:
    """
    Write response snapshot to file if debugging enabled.

    Args:
        data: Response dictionary or bytes to snapshot
        request_id: Unique request identifier
        stage: "response-from-gateway" or "response-to-client"

    Safe no-op if STARGATE_DEBUG_REQUEST_SNAPSHOTS is not set.
    Errors are logged but do not interrupt request flow.

    Files are written to subdirectories:
        - response-from-gateway: {snapshot_dir}/response-from-gateway/
        - response-to-client: {snapshot_dir}/response-to-client/
    """
    if not _is_enabled():
        return

    try:
        base_dir = _get_snapshot_dir()
        stage_dir = base_dir / stage
        stage_dir.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now().isoformat(timespec="microseconds")
        safe_request_id = request_id.replace("/", "-")[:32]
        filename = f"{timestamp}_{safe_request_id}.json"
        filepath = stage_dir / filename

        # Convert bytes to dict if needed
        if isinstance(data, bytes):
            try:
                data = json.loads(data.decode("utf-8"))
            except (json.JSONDecodeError, UnicodeDecodeError) as e:
                logger.warning(f"Failed to decode response bytes as JSON: {e}")
                return

        json_content = json.dumps(data, indent=2, ensure_ascii=False)

        async with aiofiles.open(filepath, "w") as f:
            await f.write(json_content)

        logger.debug(f"Wrote response snapshot: {stage}/{filename}")
    except Exception as e:
        logger.warning(f"Failed to write response snapshot: {e}")
