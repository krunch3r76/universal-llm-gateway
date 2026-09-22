"""operator_hop_harvest v1 handler registration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .assemble import OperatorHopHarvestAssembleHandler
from .emit import OperatorHopHarvestEmitHandler
from .fetch_bus import OperatorHopHarvestFetchBusHandler
from .fetch_ledger import OperatorHopHarvestFetchLedgerHandler
from .fetch_scoreboard import OperatorHopHarvestFetchScoreboardHandler
from .fetch_wait import OperatorHopHarvestFetchWaitHandler
from .parse import OperatorHopHarvestParseHandler
from .resolve import OperatorHopHarvestResolveHandler
from .summarize import OperatorHopHarvestSummarizeHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    domain = "operator_hop_harvest"
    pairs = [
        ("operator_hop_harvest_resolve_v1", OperatorHopHarvestResolveHandler),
        ("operator_hop_harvest_fetch_ledger_v1", OperatorHopHarvestFetchLedgerHandler),
        ("operator_hop_harvest_fetch_bus_v1", OperatorHopHarvestFetchBusHandler),
        ("operator_hop_harvest_fetch_scoreboard_v1", OperatorHopHarvestFetchScoreboardHandler),
        ("operator_hop_harvest_fetch_wait_v1", OperatorHopHarvestFetchWaitHandler),
        ("operator_hop_harvest_parse_v1", OperatorHopHarvestParseHandler),
        ("operator_hop_harvest_assemble_v1", OperatorHopHarvestAssembleHandler),
        ("operator_hop_harvest_summarize_v1", OperatorHopHarvestSummarizeHandler),
        ("operator_hop_harvest_emit_v1", OperatorHopHarvestEmitHandler),
    ]
    for step_type, cls in pairs:
        router.register_domain_handler_class(domain, step_type, cls)


__all__ = ["register_handlers"]
