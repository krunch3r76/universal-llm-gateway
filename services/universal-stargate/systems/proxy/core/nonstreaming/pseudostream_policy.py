"""Pseudostream query validation for /v1/chat/completions."""

from __future__ import annotations

from model_id import ModelId, infer_cloud_provider_from_bare

from ..errors import RequestErrorBuilder


def is_local_pseudostream_eligible(model: str | ModelId) -> bool:
    """True when model is a bare local id (no provider slash, not bare-cloud family)."""
    parsed = ModelId.parse(model)
    if parsed.provider is not None:
        return False
    if infer_cloud_provider_from_bare(parsed.base_id) is not None:
        return False
    if infer_cloud_provider_from_bare(parsed.original) is not None:
        return False
    return True


def validate_pseudostream_request(
    *,
    pseudostream: bool,
    client_stream: bool | None,
    is_pipeline: bool,
    model: str,
) -> None:
    """Raise HTTP 400 when pseudostream conflicts with the bound contract.

    Contract:
      - body stream true + pseudostream → 400
      - pipeline model → 400
      - cloud / OpenRouter / provider-prefixed → 400
    """
    if not pseudostream:
        return
    if client_stream:
        raise RequestErrorBuilder.invalid_request(
            "pseudostream=true conflicts with body stream=true — "
            "use stream=false (or omit) for JSON output with upstream SSE",
            param="pseudostream",
        )
    if is_pipeline:
        raise RequestErrorBuilder.invalid_request(
            "pseudostream=true is not supported for pipeline models",
            param="pseudostream",
        )
    if not is_local_pseudostream_eligible(model):
        raise RequestErrorBuilder.invalid_request(
            "pseudostream=true is local-models only "
            "(bare GGUF ids; not provider/… or OpenRouter)",
            param="pseudostream",
        )
