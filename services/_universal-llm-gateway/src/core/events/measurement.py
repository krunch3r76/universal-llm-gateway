"""Measurement event signals and factories."""

from universal_event_bus import Event, event_factory

MEASUREMENT_EMBEDDING_DETECTED = "measurement.embedding.detected"
"""
Emitted at the vLLM measurement dispatch decision point when embedding
detection overrides the default step-down probing strategy.

∀ vLLM embedding measurement: exactly one emission before probe begins.

Payload:
    model_id: str - Model being measured
    context_length: int - Single probe context size (training_context_length)
"""


@event_factory
def MeasurementEmbeddingDetected(model_id: str, context_length: int) -> Event:  # noqa: N802
    """
    Create MEASUREMENT_EMBEDDING_DETECTED event.

    Args:
        model_id: Model being measured
        context_length: Single probe context size derived from training_context_length

    Returns:
        Event with MeasurementEmbeddingDetected signal
    """
    return Event(
        signal=MEASUREMENT_EMBEDDING_DETECTED,
        payload={"model_id": model_id, "context_length": context_length},
    )
