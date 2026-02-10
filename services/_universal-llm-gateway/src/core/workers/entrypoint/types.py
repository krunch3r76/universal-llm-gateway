"""Worker entrypoint types."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal


@dataclass(slots=True, frozen=True)
class WorkerEntrypoint:
    """
    Worker process entrypoint specification.

    Attributes:
        kind: "module" for `python -m`, "script" for direct file execution
        value: module name (e.g., "src.core.workers.worker") or script path
        cwd: working directory for subprocess (required for module imports)
    """

    kind: Literal["module", "script"]
    value: str
    cwd: Path

    @classmethod
    def as_module(cls, module_name: str, cwd: Path) -> "WorkerEntrypoint":
        """Create module-based entrypoint (python -m ...)."""
        return cls(kind="module", value=module_name, cwd=cwd)

    @classmethod
    def as_script(cls, script_path: str, cwd: Path) -> "WorkerEntrypoint":
        """Create script-based entrypoint (python path/to/script.py)."""
        return cls(kind="script", value=script_path, cwd=cwd)
