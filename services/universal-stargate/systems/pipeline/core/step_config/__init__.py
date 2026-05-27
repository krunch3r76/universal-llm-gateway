"""Public surface for the StepConfig package.

Re-exports :class:`StepConfig` and :class:`ResolvedTargetModel` so callers using
``from .step_config import StepConfig`` or
``from .step_config import ResolvedTargetModel`` require no import-path changes
after the package-shadow split. Internal helpers (parsing validators, model
resolution helpers, map config builder) remain private to the package.
"""

from .config import StepConfig
from .resolved_target_model import ResolvedTargetModel

__all__ = [
    "ResolvedTargetModel",
    "StepConfig",
]
