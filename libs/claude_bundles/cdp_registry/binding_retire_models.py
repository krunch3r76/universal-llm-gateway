"""Typed outcomes for dormant binding retire."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from universal_protocol.errors import ProtocolError

RETIRE_LOG_EVENT = "dormant_binding_retired"
UNBIND_FINISHED_LOG_EVENT = "dormant_binding_unbind_finished"
SATELLITE_RETIRE_HINT = "cse_session(op=retire_binding, ...)"


@dataclass(frozen=True, slots=True)
class RetireBindingResult:
    """Outcome of ``retire_dormant_binding`` (non-refused paths only)."""

    outcome: str
    registration_id: str | None
    chat_url: str | None
    dry_run: bool
    idempotent: bool
    detail: dict[str, Any]


def result_payload(result: RetireBindingResult) -> dict[str, Any]:
    return asdict(result)


def refuse_retire(
    *,
    reason: str,
    message: str,
    registration_id: str | None = None,
    chat_url: str | None = None,
    **extra: Any,
) -> None:
    """Raise a typed 422-class refusal for retire entry points."""
    data: dict[str, Any] = {"reason": reason, **extra}
    if registration_id:
        data["registration_id"] = registration_id
    if chat_url:
        data["chat_url"] = chat_url
    if reason == "not_registry_authority":
        data["recovery"] = SATELLITE_RETIRE_HINT
    raise ProtocolError(
        code="cse.retire_binding.refused",
        message=message,
        source="rpc",
        retryable=False,
        data=data,
    )
