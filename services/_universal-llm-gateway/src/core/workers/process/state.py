"""
Shared state container for worker process management.

This container holds all shared state that multiple component managers
need to access, ensuring consistency and clear ownership.
"""

from dataclasses import dataclass, field

from model_id import ModelId
from process_ipc import ProcessSupervisor


def _normalize_key(model_id: str | ModelId) -> str:
    """Get normalized string key for dict lookup."""
    if isinstance(model_id, ModelId):
        return model_id.normalized
    return ModelId.parse(model_id).normalized


@dataclass
class ProcessState:
    """
    Shared state container for process management.

    This state is owned by WorkerController (or ProcessManager during transition)
    and shared with all component managers via constructor injection.

    All model_id parameters accept str or ModelId. Keys are normalized strings:
    'model-8192-hybrid' and 'model-8192' are treated as the same key since
    -hybrid suffix is informational.

    Attributes:
        supervisors: ProcessSupervisor instances per model (keyed by normalized string)
        socket_paths: IPC socket paths per model (keyed by normalized string)
        failed_workers: Set of model names that have failed
    """

    supervisors: dict[str, ProcessSupervisor] = field(default_factory=dict)
    socket_paths: dict[str, str] = field(default_factory=dict)
    failed_workers: set[str] = field(default_factory=set)

    def clear(self):
        """Clear all state (useful for testing)."""
        self.supervisors.clear()
        self.socket_paths.clear()
        self.failed_workers.clear()

    def has_supervisor(self, model_id: str | ModelId) -> bool:
        """Check if supervisor exists for model."""
        return _normalize_key(model_id) in self.supervisors

    def get_supervisor(self, model_id: str | ModelId) -> ProcessSupervisor | None:
        """Get supervisor for model, or None if not found."""
        return self.supervisors.get(_normalize_key(model_id))

    def set_supervisor(
        self, model_id: str | ModelId, supervisor: ProcessSupervisor
    ) -> None:
        """Set supervisor for model."""
        self.supervisors[_normalize_key(model_id)] = supervisor

    def remove_supervisor(self, model_id: str | ModelId) -> ProcessSupervisor | None:
        """Remove and return supervisor for model, or None if not found."""
        return self.supervisors.pop(_normalize_key(model_id), None)

    def get_socket_path(self, model_id: str | ModelId) -> str | None:
        """Get socket path for model, or None if not found."""
        return self.socket_paths.get(_normalize_key(model_id))

    def set_socket_path(self, model_id: str | ModelId, path: str) -> None:
        """Set socket path for model."""
        self.socket_paths[_normalize_key(model_id)] = path

    def remove_socket_path(self, model_id: str | ModelId) -> str | None:
        """Remove and return socket path for model, or None if not found."""
        return self.socket_paths.pop(_normalize_key(model_id), None)
