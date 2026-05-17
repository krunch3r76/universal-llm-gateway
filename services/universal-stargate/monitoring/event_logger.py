"""
Stable EventLogger façade for monitoring event publication.

The concrete logging methods are split by event domain into sibling
modules (request lifecycle, streaming chunks, parameter comparison, and
stargate errors). This thin class preserves the exact import contract and
instantiation signature used by StargateMonitor in monitor.py.
"""

from .parameter_comparison_logging import _ParameterComparisonLogging
from .request_lifecycle_logging import _RequestLifecycleLogging
from .stargate_error_logging import _StargateErrorLogging
from .streaming_chunk_logging import _StreamingChunkLogging


class EventLogger(
    _RequestLifecycleLogging,
    _StreamingChunkLogging,
    _ParameterComparisonLogging,
    _StargateErrorLogging,
):
    """Publishes monitoring events through EventBus using domain mixins.

    Invariants:
    - Constructor signature remains compatible with StargateMonitor.
    - Public async logging methods remain available on this class.
    - EventBus publishing behavior and event payload shapes are preserved.
    """

    def __init__(self, event_bus, ensure_serializable_func):
        self.event_bus = event_bus
        self._ensure_serializable = ensure_serializable_func
