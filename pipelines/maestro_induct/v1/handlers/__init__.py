"""maestro_induct v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .assemble import MaestroInductAssembleHandler
from .discover_scores import MaestroInductDiscoverScoresHandler
from .emit import MaestroInductEmitHandler
from .enumerate_lanes import MaestroInductEnumerateLanesHandler
from .fetch_checkpoint import MaestroInductFetchCheckpointHandler
from .fetch_consults import MaestroInductFetchConsultsHandler
from .fetch_continuity import MaestroInductFetchContinuityHandler
from .fetch_house import MaestroInductFetchHouseHandler
from .fetch_journal import MaestroInductFetchJournalHandler
from .fetch_lanes import MaestroInductFetchLanesHandler
from .fetch_runbook import MaestroInductFetchRunbookHandler
from .fetch_scores import MaestroInductFetchScoresHandler
from .resolve import MaestroInductResolveHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    pairs = [
        ("maestro_induct_resolve_v1", MaestroInductResolveHandler),
        ("maestro_induct_fetch_house_v1", MaestroInductFetchHouseHandler),
        ("maestro_induct_fetch_checkpoint_v1", MaestroInductFetchCheckpointHandler),
        ("maestro_induct_fetch_continuity_v1", MaestroInductFetchContinuityHandler),
        ("maestro_induct_fetch_journal_v1", MaestroInductFetchJournalHandler),
        ("maestro_induct_fetch_runbook_v1", MaestroInductFetchRunbookHandler),
        ("maestro_induct_discover_scores_v1", MaestroInductDiscoverScoresHandler),
        ("maestro_induct_fetch_scores_v1", MaestroInductFetchScoresHandler),
        ("maestro_induct_enumerate_lanes_v1", MaestroInductEnumerateLanesHandler),
        ("maestro_induct_fetch_lanes_v1", MaestroInductFetchLanesHandler),
        ("maestro_induct_fetch_consults_v1", MaestroInductFetchConsultsHandler),
        ("maestro_induct_assemble_v1", MaestroInductAssembleHandler),
        ("maestro_induct_emit_v1", MaestroInductEmitHandler),
    ]
    for step_type, cls in pairs:
        router.register_domain_handler_class("maestro_induct", step_type, cls)


__all__ = ["register_handlers"]
