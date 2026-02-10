"""
Checkpoint persistence for pipeline step outputs.

Async-safety:
- FilesystemCheckpointBackend uses aiofiles for async I/O
- Atomic writes via temp file + rename
- No shared mutable state (stateless backend)
"""

from .adapters import StepOutputCheckpointAdapter
from .backend import CheckpointBackend, CheckpointData, FilesystemCheckpointBackend
from .manager import CheckpointManager

__all__ = [
    "CheckpointBackend",
    "CheckpointData",
    "CheckpointManager",
    "FilesystemCheckpointBackend",
    "StepOutputCheckpointAdapter",
]
