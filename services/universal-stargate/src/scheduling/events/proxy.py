"""Proxy-layer event signals.

Covers events emitted by the federated request execution path: prompt
schema transformation decisions.

Signals:
    federated.request.prompt.transformation.applied — transformation ran,
        messages→prompt
    federated.request.prompt.transformation.failed  — transformation raised,
        original body forwarded
    federated.request.prompt.transformation.skipped — engine absent or
        schema != prompt
"""

from universal_event_bus import Event, event_factory

FEDERATED_REQUEST_PROMPT_TRANSFORMATION_APPLIED = (
    "federated.request.prompt.transformation.applied"
)
FEDERATED_REQUEST_PROMPT_TRANSFORMATION_FAILED = (
    "federated.request.prompt.transformation.failed"
)
FEDERATED_REQUEST_PROMPT_TRANSFORMATION_SKIPPED = (
    "federated.request.prompt.transformation.skipped"
)


@event_factory
def federated_request_prompt_transformation_applied(
    request_id: str,
    model_id: str,
    gateway_id: str,
    prompt_chars: int,
) -> Event:
    """
    Messages-based request body successfully transformed to prompt format.

    Args:
        request_id: Request that was transformed.
        model_id: routing_key of the target model.
        gateway_id: Target federated gateway ID.
        prompt_chars: Character count of the resulting prompt string (not token count).
    """
    return Event(
        signal=FEDERATED_REQUEST_PROMPT_TRANSFORMATION_APPLIED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "prompt_chars": prompt_chars,
        },
    )


@event_factory
def federated_request_prompt_transformation_failed(
    request_id: str,
    model_id: str,
    gateway_id: str,
    error: str,
) -> Event:
    """
    Transformation raised an exception; the original (untransformed) request body
    was forwarded.

    Args:
        request_id: Request whose transformation failed.
        model_id: routing_key of the target model.
        gateway_id: Target federated gateway ID.
        error: Exception message from the transformation engine.
    """
    return Event(
        signal=FEDERATED_REQUEST_PROMPT_TRANSFORMATION_FAILED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "error": error,
        },
    )


@event_factory
def federated_request_prompt_transformation_skipped(
    request_id: str,
    model_id: str,
    gateway_id: str,
    reason: str,
) -> Event:
    """
    Transformation was not attempted, either because no transformation engine was
    available or because the model's input schema was not 'prompt'.

    Args:
        request_id: Request for which transformation was skipped.
        model_id: routing_key of the target model.
        gateway_id: Target federated gateway ID.
        reason: Human-readable reason: "no_engine" or "schema_not_prompt".
    """
    return Event(
        signal=FEDERATED_REQUEST_PROMPT_TRANSFORMATION_SKIPPED,
        payload={
            "request_id": request_id,
            "model_id": model_id,
            "gateway_id": gateway_id,
            "reason": reason,
        },
    )
