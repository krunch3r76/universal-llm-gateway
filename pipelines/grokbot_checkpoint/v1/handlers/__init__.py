"""grokbot_checkpoint v1 handlers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .admit import GrokbotCheckpointAdmitHandler
from .await_verify import GrokbotCheckpointAwaitVerifyHandler
from .card import GrokbotCheckpointCardHandler
from .envelope import GrokbotCheckpointEnvelopeHandler
from .fact import GrokbotCheckpointFactHandler
from .harvest_adapter import GrokbotCheckpointHarvestAdapterHandler
from .harvest_normalise import GrokbotCheckpointHarvestNormaliseHandler
from .harvest_pour import GrokbotCheckpointHarvestPourHandler
from .post import GrokbotCheckpointPostHandler
from .result import GrokbotCheckpointResultHandler
from .seal import GrokbotCheckpointSealHandler
from .source import GrokbotCheckpointSourceHandler
from .verify_resume import GrokbotCheckpointVerifyResumeHandler

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter

_DOMAIN = "grokbot_checkpoint"


def register_handlers(router: DomainRouter) -> None:
    pairs = (
        ("grokbot_checkpoint_admit_v1", GrokbotCheckpointAdmitHandler),
        ("grokbot_checkpoint_harvest_normalise_v1", GrokbotCheckpointHarvestNormaliseHandler),
        ("grokbot_checkpoint_harvest_adapter_v1", GrokbotCheckpointHarvestAdapterHandler),
        ("grokbot_checkpoint_harvest_pour_v1", GrokbotCheckpointHarvestPourHandler),
        ("grokbot_checkpoint_source_v1", GrokbotCheckpointSourceHandler),
        ("grokbot_checkpoint_envelope_v1", GrokbotCheckpointEnvelopeHandler),
        ("grokbot_checkpoint_seal_v1", GrokbotCheckpointSealHandler),
        ("grokbot_checkpoint_card_v1", GrokbotCheckpointCardHandler),
        ("grokbot_checkpoint_post_v1", GrokbotCheckpointPostHandler),
        ("grokbot_checkpoint_fact_v1", GrokbotCheckpointFactHandler),
        ("grokbot_checkpoint_await_verify_v1", GrokbotCheckpointAwaitVerifyHandler),
        ("grokbot_checkpoint_verify_resume_v1", GrokbotCheckpointVerifyResumeHandler),
        ("grokbot_checkpoint_result_v1", GrokbotCheckpointResultHandler),
    )
    for step_type, cls in pairs:
        router.register_domain_handler_class(_DOMAIN, step_type, cls)


__all__ = ["register_handlers"]
