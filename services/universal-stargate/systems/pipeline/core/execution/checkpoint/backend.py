"""
Checkpoint storage backends.

Provides protocol for pluggable storage and filesystem implementation.
"""

import json
import logging
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

logger = logging.getLogger(__name__)


@dataclass
class CheckpointData:
    """Serializable checkpoint payload."""

    step_name: str
    inputs_fingerprint: str
    output_raw: str
    output_json: dict | None
    output_meta: dict
    saved_at: str  # ISO format
    pipeline_version: str = "1.0"
    checksum: str | None = None  # SHA256 of output_raw if enabled

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "CheckpointData":
        return cls(**data)


class AbstractCheckpointBackend(ABC):
    """
    Abstract base class for checkpoint storage backends.

    Implement this class to add custom checkpoint storage (database,
    cloud storage, etc.). The default implementation is FilesystemCheckpointBackend.

    Contract Requirements:
    ----------------------

    **Properties (Required):**

    - `backend_name: str` - Identifier for logging/events (e.g., "filesystem", "redis")

    **Methods (Required):**

    - `save()` - Store checkpoint atomically
    - `load()` - Retrieve checkpoint (None if not found)
    - `exists()` - Check if checkpoint exists
    - `delete()` - Remove checkpoint
    - `cleanup_expired()` - Remove old checkpoints

    Invariants:
    -----------

    - ∀ save(k, d): load(k) = d (until deleted/expired)
    - ∀ save(k, d): exists(k) = True
    - ∀ k where ¬∃ checkpoint: load(k) = None ∧ ¬raises
    - Atomicity: save() is all-or-nothing (no partial writes)

    Thread/Async Safety:
    --------------------

    - All methods are async (await I/O operations)
    - Implementations must be safe for concurrent calls
    - Use atomic operations (write-then-rename for files)

    Example:
    --------

    ```python
    class RedisCheckpointBackend(AbstractCheckpointBackend):
        def __init__(self, redis_client, prefix: str = "checkpoint:"):
            self._redis = redis_client
            self._prefix = prefix

        @property
        def backend_name(self) -> str:
            return "redis"

        async def save(self, key: str, data: CheckpointData) -> None:
            redis_key = f"{self._prefix}{key}"
            await self._redis.set(redis_key, json.dumps(data.to_dict()))

        async def load(self, key: str) -> CheckpointData | None:
            redis_key = f"{self._prefix}{key}"
            raw = await self._redis.get(redis_key)
            if raw is None:
                return None
            return CheckpointData.from_dict(json.loads(raw))

        async def exists(self, key: str) -> bool:
            return await self._redis.exists(f"{self._prefix}{key}")

        async def delete(self, key: str) -> None:
            await self._redis.delete(f"{self._prefix}{key}")

        async def cleanup_expired(self, ttl_seconds: int) -> int:
            # Redis TTL handled via EXPIRE, return 0
            return 0
    ```

    See Also:
    ---------
    - `FilesystemCheckpointBackend` - Reference implementation
    - `CheckpointData` - Serializable checkpoint payload
    - `CheckpointManager` - Orchestrates checkpoint operations
    """

    @property
    @abstractmethod
    def backend_name(self) -> str:
        """
        Backend identifier for events and logging.

        Returns:
            Short identifier string (e.g., "filesystem", "redis", "s3")

        Used in:
            - CheckpointSaved/Loaded/Failed event payloads
            - Debug logging
            - Metrics/observability
        """
        ...

    @abstractmethod
    async def save(self, key: str, data: "CheckpointData") -> None:
        """
        Save checkpoint data atomically.

        Args:
            key: Unique checkpoint identifier
                 Format: "{pipeline_id}:{execution_id}:{step_name}"
            data: CheckpointData instance to persist

        Raises:
            IOError: If write fails
            Any storage-specific exceptions

        Atomicity:
            Implementations MUST ensure atomic writes.
            For files: write to temp, then rename.
            For databases: use transactions.
            Never leave partial data on failure.
        """
        ...

    @abstractmethod
    async def load(self, key: str) -> "CheckpointData | None":
        """
        Load checkpoint data by key.

        Args:
            key: Checkpoint identifier

        Returns:
            CheckpointData if found, None if not exists.
            NEVER raises for missing checkpoints.

        Raises:
            IOError: If read fails (corruption, permissions)
            json.JSONDecodeError: If data corrupted
        """
        ...

    @abstractmethod
    async def exists(self, key: str) -> bool:
        """
        Check if checkpoint exists without loading data.

        Args:
            key: Checkpoint identifier

        Returns:
            True if checkpoint exists, False otherwise.

        Note:
            This is a lightweight existence check.
            Prefer this over load() when only checking presence.
        """
        ...

    @abstractmethod
    async def delete(self, key: str) -> None:
        """
        Delete checkpoint by key.

        Args:
            key: Checkpoint identifier

        Note:
            Silently succeeds if checkpoint doesn't exist.
            Idempotent: delete(k); delete(k) is safe.
        """
        ...

    @abstractmethod
    async def cleanup_expired(self, ttl_seconds: int) -> int:
        """
        Delete checkpoints older than TTL.

        Args:
            ttl_seconds: Maximum age in seconds

        Returns:
            Number of checkpoints deleted.

        Note:
            Called periodically by CheckpointManager.
            For backends with native TTL (Redis EXPIRE), return 0.
        """
        ...


class CheckpointBackend(Protocol):
    """Protocol for checkpoint storage backends."""

    @property
    def backend_name(self) -> str:
        """Backend identifier for events."""
        ...

    async def save(self, key: str, data: CheckpointData) -> None:
        """Save checkpoint data."""
        ...

    async def load(self, key: str) -> CheckpointData | None:
        """Load checkpoint data (returns None if not found)."""
        ...

    async def exists(self, key: str) -> bool:
        """Check if checkpoint exists."""
        ...

    async def delete(self, key: str) -> None:
        """Delete checkpoint."""
        ...

    async def cleanup_expired(self, ttl_seconds: int) -> int:
        """Delete checkpoints older than TTL. Returns count deleted."""
        ...


class FilesystemCheckpointBackend:
    """
    Filesystem-based checkpoint storage.

    File layout: {base_path}/{key}.json
    Atomic writes: Write to {key}.tmp, then rename to {key}.json
    """

    def __init__(self, base_path: str | Path):
        self._base_path = Path(base_path)
        self._base_path.mkdir(parents=True, exist_ok=True)

    @property
    def backend_name(self) -> str:
        return "filesystem"

    @staticmethod
    def _sanitize_key(key: str) -> str:
        """Sanitize checkpoint key for filesystem safety."""
        return key.replace(":", "_").replace("/", "_")

    def _get_path(self, key: str) -> Path:
        """Get checkpoint file path with sanitized key."""
        safe_key = self._sanitize_key(key)
        return self._base_path / f"{safe_key}.json"

    async def save(self, key: str, data: CheckpointData) -> None:
        """Save checkpoint with atomic write."""
        import aiofiles

        path = self._get_path(key)
        temp_path = path.with_suffix(".tmp")
        content = json.dumps(data.to_dict(), indent=2)

        async with aiofiles.open(temp_path, "w") as f:
            await f.write(content)

        temp_path.rename(path)
        logger.debug("Saved checkpoint: %s", key)

    async def load(self, key: str) -> CheckpointData | None:
        """Load checkpoint from file."""
        import aiofiles

        path = self._get_path(key)
        if not path.exists():
            return None

        async with aiofiles.open(path) as f:
            content = await f.read()

        data = json.loads(content)
        return CheckpointData.from_dict(data)

    async def exists(self, key: str) -> bool:
        """Check if checkpoint file exists."""
        return self._get_path(key).exists()

    async def delete(self, key: str) -> None:
        """Delete checkpoint file."""
        path = self._get_path(key)
        if path.exists():
            path.unlink()

    async def cleanup_expired(self, ttl_seconds: int) -> int:
        """Delete checkpoints older than TTL."""
        import time

        cutoff = time.time() - ttl_seconds
        count = 0

        for path in self._base_path.glob("*.json"):
            if path.stat().st_mtime < cutoff:
                path.unlink()
                count += 1

        if count:
            logger.info("Cleaned up %d expired checkpoints", count)
        return count
