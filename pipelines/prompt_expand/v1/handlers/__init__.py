"""prompt_expand v1 handlers — validate, profile lookup, retrieve, classify, format."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .profile_lookup import (
    PromptExpandClassifyRetrieveHandler,
    PromptExpandFormatOutputHandler,
    PromptExpandProfileLookupHandler,
    PromptExpandRetrieveHandler,
    PromptExpandValidateOptionsHandler,
)

if TYPE_CHECKING:
    from systems.pipeline.core.domain_router import DomainRouter


def register_handlers(router: DomainRouter) -> None:
    router.register_domain_handler_class(
        "prompt_expand",
        "prompt_expand_validate_options_v1",
        PromptExpandValidateOptionsHandler,
    )
    router.register_domain_handler_class(
        "prompt_expand",
        "prompt_expand_profile_lookup_v1",
        PromptExpandProfileLookupHandler,
    )
    router.register_domain_handler_class(
        "prompt_expand",
        "prompt_expand_retrieve_v1",
        PromptExpandRetrieveHandler,
    )
    router.register_domain_handler_class(
        "prompt_expand",
        "prompt_expand_classify_retrieve_v1",
        PromptExpandClassifyRetrieveHandler,
    )
    router.register_domain_handler_class(
        "prompt_expand",
        "prompt_expand_format_output_v1",
        PromptExpandFormatOutputHandler,
    )
