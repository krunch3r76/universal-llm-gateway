"""Per-family folds. Each owns one signal family and knows nothing of the others.

Cross-family questions are answered in ``derive`` via
:class:`~dispatch_monitor_core.correlation.CorrelationIndex`, never by one fold
reading another's state.
"""

from __future__ import annotations

from .cdp import CdpFold
from .charter import CharterFold
from .conveyor import ConveyorFold
from .sdk import SdkFold

__all__ = ["CdpFold", "CharterFold", "ConveyorFold", "SdkFold"]
