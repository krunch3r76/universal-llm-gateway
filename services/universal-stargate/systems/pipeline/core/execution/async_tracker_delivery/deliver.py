"""Public delivery entry point — routes on ``record.op`` to the matching path.

This module is the thin router above ``on_behalf.py`` (bus-mode) and
``legacy_path.py`` (legacy ``result_delivery`` envelope). The public
``deliver_result`` function is exposed at the package root via
``__init__.py`` so consumers continue to import:

    from systems.pipeline.core.execution.async_tracker_delivery import deliver_result

with no source change required.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from transport_utils import DEFAULT_AGENT_BUS_URL

from .legacy_path import _deliver_legacy_envelope
from .on_behalf import _post_content_on_behalf
from .outcome import DeliveryOutcome
from .protocol import _EventBusProtocol

if TYPE_CHECKING:
    from ..async_tracker import PipelineExecutionRecord


async def deliver_result(
    record: PipelineExecutionRecord,
    *,
    event_bus: _EventBusProtocol | None,
    auth_token: str,
    url: str = DEFAULT_AGENT_BUS_URL,
) -> DeliveryOutcome:
    """Route delivery based on op: legacy envelope-post OR bus-mode on-behalf post.

    Returns ``DeliveryOutcome`` so the tracker can act on failures for
    ``op="to_thread"`` records (status demotion on any non-delivered outcome).
    """
    if record.op == "to_thread":
        return await _post_content_on_behalf(
            record,
            event_bus=event_bus,
            auth_token=auth_token,
            url=url,
        )
    return await _deliver_legacy_envelope(
        record,
        event_bus=event_bus,
        auth_token=auth_token,
        url=url,
    )
