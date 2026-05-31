"""Signal dispatch: map incoming event signals to gate open/close transitions."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from model_id import ModelId

from ._state_changes import _close_gate, _open_gate

if TYPE_CHECKING:
    from .gate import AdmissionGate

logger = logging.getLogger(__name__)


def _apply_gateway_signal(
    gate: AdmissionGate, signal: str, payload: dict[str, object]
) -> bool:
    if signal not in (
        "federation.gateway.degraded",
        "federation.gateway.recovered",
    ):
        return False
    raw_gateway = payload.get("gateway_id")
    if not isinstance(raw_gateway, str) or not raw_gateway:
        return True
    reason = f"gateway:{raw_gateway}"
    for key in gate._tracked:
        if signal == "federation.gateway.degraded":
            _close_gate(gate, key, reason=reason, signal=signal, payload=payload)
        else:
            _open_gate(gate, key, reason=reason, signal=signal, payload=payload)
    return True


def _apply_signal(gate: AdmissionGate, signal: str, payload: dict[str, object]) -> None:
    if _apply_gateway_signal(gate, signal, payload):
        return

    raw_model = payload.get("model_id")
    if not isinstance(raw_model, str):
        return
    try:
        key = ModelId.parse(raw_model).routing_key
    except (ValueError, TypeError):
        return
    ev = gate._tracked.get(key)
    if ev is None:
        # model_id divergence detection: the residual risk after the
        # ContextualizeModelCoordinator removal is that the catalog
        # routing layer (for capacity.admission.*) or gateway worker
        # telemetry (for model.*) ever reports a routing_key that
        # differs from RagConfig.contextualize_model. Log once per
        # unknown key on a relevant signal so operators can spot the
        # mismatch from logs without resolving the architectural
        # question prospectively. Unrelated model loads are logged
        # exactly once and then go silent.
        if (
            signal
            in (
                "capacity.admission.paused",
                "capacity.admission.resumed",
                "model.loaded",
                "model.loading.started",
                "model.load.failed",
            )
            and key not in gate._unknown_seen
        ):
            gate._unknown_seen.add(key)
            logger.warning(
                "AdmissionGate received %s for untracked model %s; "
                "configured tracking: %s. If this looks like a variant "
                "of a tracked model (different context-length suffix or "
                "normalization), investigate model_id mismatch between "
                "RagConfig.contextualize_model and the request path.",
                signal,
                key,
                sorted(gate._tracked.keys()),
            )
        return
    # CLOSE gate signals: admission paused (starvation_drain) OR model
    # cold-loading started. Both indicate workers should hold off.
    if signal in ("capacity.admission.paused", "model.loading.started"):
        reason = (
            "capacity.admission"
            if signal == "capacity.admission.paused"
            else "model.loading"
        )
        _close_gate(gate, key, reason=reason, signal=signal, payload=payload)
    # OPEN gate signals:
    #   - capacity.admission.resumed: drain window ended
    #   - model.loaded: cold load completed
    #   - model.load.failed: restore optimism so the next worker request
    #     triggers a retry (Stargate re-loads on demand and that request
    #     fails loudly, which is the correctness signal). Without this
    #     branch, a failed cold load leaves the gate CLOSED until each
    #     waiting worker hits its full client_timeout_s. Preserved from
    #     the deleted ContextualizeModelCoordinator.
    elif signal in (
        "capacity.admission.resumed",
        "model.loaded",
        "model.load.failed",
    ):
        reason = (
            "capacity.admission"
            if signal == "capacity.admission.resumed"
            else "model.loading"
        )
        _open_gate(gate, key, reason=reason, signal=signal, payload=payload)
