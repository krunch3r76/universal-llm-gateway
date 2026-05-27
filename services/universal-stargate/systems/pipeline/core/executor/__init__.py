"""Package-shadow split of the former ``core/executor.py``.

Public surface consumed by sibling pipeline modules, the proxy lifecycle
layer, and the async-tracker test:

- ``PipelineExecutor`` — orchestrator class (sync + async entry points).
- ``_normalize_pipeline_exception`` — error→tuple mapping used by both
  the proxy lifecycle path and the async-tracker passthrough tests.
- ``PreparedPipelineExecution`` — shared prepared-state dataclass; kept
  on the package surface as the canonical sync↔async handoff shape.
"""

from .exception_mapping import _normalize_pipeline_exception
from .pipeline_executor import PipelineExecutor
from .prepared import PreparedPipelineExecution

__all__ = [
    "PipelineExecutor",
    "PreparedPipelineExecution",
    "_normalize_pipeline_exception",
]
