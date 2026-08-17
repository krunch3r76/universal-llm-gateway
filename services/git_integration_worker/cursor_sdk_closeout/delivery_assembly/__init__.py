"""Closeout delivery assembly sub-package: staged sequence behind the prep entry points.

Re-exports the sequencers for the sub-package surface. In-package callers
(``delivery_prep``) import from ``.delivery_assembly.orchestration`` directly
and must not import names from this ``__init__`` (arch §5.4).
"""

from .orchestration import (
    _assemble_closeout_delivery,
    _assemble_closeout_delivery_async,
)

__all__ = [
    "_assemble_closeout_delivery",
    "_assemble_closeout_delivery_async",
]
