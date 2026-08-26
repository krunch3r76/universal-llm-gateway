"""Degraded-reason vocabulary: exception mapping, run-health guards, status projection.

Maps SDK/bridge exceptions to ``degraded_reasons[]`` tokens (class-derived,
subclass-before-parent), folds implement/empty-output/empty-assistant-turn
guards, and projects a singular reason onto ``CloseoutStatus`` via
``_map_closeout_status``. Exception-class imports stay function-local inside
``degraded_reasons_from_exception`` to avoid import cycles with ``cursor_sdk``
and ``cursor_home``.
"""

from __future__ import annotations

from implement_admission.spec import NO_RUN_DEGRADED_REASONS, CloseoutStatus

from services.git_integration_worker.cursor_sdk_manifest import (
    no_capture_degraded_reason,
)

from .closeout_records import SdkRunOutcome


def merge_degraded_reasons(
    singular: str | None,
    *extra: str,
) -> tuple[str, ...]:
    """Dual-emit compat: singular reason first, then additive extras."""
    reasons: list[str] = []
    if singular:
        reasons.append(singular)
    for reason in extra:
        if reason and reason not in reasons:
            reasons.append(reason)
    return tuple(reasons)


def degraded_reasons_from_exception(exc: BaseException) -> tuple[str, ...]:
    """Map SDK/bridge failures to class-derived ``degraded_reasons[]`` tokens.

    Tokens derive from the exception **class** (roadmap item 4), not
    stringified ``exc.code``.  Subclass-before-parent ``isinstance`` order
    respects the ``cursor_sdk.errors`` hierarchy.
    """
    from cursor_sdk.errors import (
        AgentBusyError,
        AgentNotFoundError,
        APITimeoutError,
        AuthenticationError,
        BadRequestError,
        ConfigurationError,
        CursorSDKError,
        IntegrationNotConnectedError,
        InternalServerError,
        NetworkError,
        NotFoundError,
        PermissionDeniedError,
        RateLimitError,
        UnsupportedRunOperationError,
    )

    from services.git_integration_worker.cursor_home import (
        CursorHomeConfigError,
        CursorVenvConfigError,
    )

    _sdk_class_tokens: tuple[tuple[type[BaseException], str], ...] = (
        (RateLimitError, "sdk_rate_limited"),
        (AgentBusyError, "sdk_agent_busy"),
        (AuthenticationError, "sdk_auth_failed"),
        (PermissionDeniedError, "sdk_permission_denied"),
        (AgentNotFoundError, "sdk_agent_not_found"),
        (NotFoundError, "sdk_run_not_found"),
        (APITimeoutError, "sdk_timeout"),
        (IntegrationNotConnectedError, "sdk_integration_not_connected"),
        (UnsupportedRunOperationError, "sdk_unsupported_run_operation"),
        (BadRequestError, "sdk_bad_request"),
        (ConfigurationError, "sdk_configuration"),
        (InternalServerError, "sdk_internal_server"),
        (NetworkError, "sdk_network"),
    )
    for exc_type, token in _sdk_class_tokens:
        if isinstance(exc, exc_type):
            return (token,)
    if isinstance(exc, CursorSDKError):
        code = getattr(exc, "code", None) or "unknown"
        return (f"sdk_error:{code}",)
    if type(exc).__name__ == "SdkRunAbortedError":
        cause = exc.__cause__
        if cause is not None:
            inner = degraded_reasons_from_exception(cause)
            if inner != ("worker_dispatch_failed",):
                return inner
        return ("bridge_read_timeout",)
    if isinstance(exc, CursorHomeConfigError):
        return ("bridge_env_config",)
    if isinstance(exc, CursorVenvConfigError):
        return ("bridge_env_config",)
    return ("worker_dispatch_failed",)


def degraded_implement_reason(outcome: SdkRunOutcome) -> str | None:
    """Return a machine reason when an implement closeout must not claim success."""
    if outcome.status != "finished":
        return f"run_status={outcome.status}"
    if outcome.tool_call_count == 0:
        return "zero_tool_calls"
    if outcome.capture_branch:
        no_capture = no_capture_degraded_reason(outcome.capture_branch)
        if no_capture:
            return no_capture
    return None


def empty_output_degraded_reason(outcome: SdkRunOutcome) -> str | None:
    """Contract-independent invariant guard for friction 19819.

    A finished run whose captured body (after transcript reconstruction by
    ``resolve_run_body``) is empty must never report ``status: complete`` with a
    0-byte sidecar. Returns ``"empty_terminal_output"`` (mapped to FAILED by
    ``_map_closeout_status``) so the silent-success failure mode is surfaced
    explicitly. Non-finished runs are already covered by the ``run_status=`` reason.
    """
    if outcome.status == "finished" and not outcome.body.strip():
        return "empty_terminal_output"
    return None


def empty_assistant_turn_reason(outcome: SdkRunOutcome) -> str | None:
    """Hollow-model-no-op guard for friction 24299 — contract- and status-independent.

    A run whose captured body is empty AND which made zero tool calls produced
    nothing: an empty assistant turn (``content: []``). This is a run-health
    failure that must outrank downstream deliverable-completeness reasons
    (``pinned_deliverable_*``), otherwise a secondary pin-write miss becomes the
    primary ``degraded_reason`` operators see and the model no-op is misdiagnosed.

    Distinct from ``empty_output_degraded_reason`` (finished-gated, body-only, so
    it misses a non-``finished`` empty stop) and from ``degraded_implement_reason``'s
    ``zero_tool_calls`` (implement-only). This fires for every contract and every
    status, closing the hole that let a light-bounded/consult hollow no-op reach
    the pin path with ``degraded_reason=None``.
    """
    if not outcome.body.strip() and outcome.tool_call_count == 0:
        return "empty_assistant_turn"
    return None


def conductor_g1_pin_s4b_degraded_reason(
    *,
    body: str,
    packet_text: str | None = None,
    packet_kind: str | None = None,
) -> str | None:
    """Fail-closed degrade when a conductor G1-pin closeout lacks S4b rich-seed evidence."""
    is_conductor = packet_kind == "conductor"
    if not is_conductor and packet_text:
        from services.git_integration_worker.cursor_sdk_packet import (
            extract_packet_kind_from_packet,
        )

        is_conductor = extract_packet_kind_from_packet(packet_text) == "conductor"
    if not is_conductor:
        return None
    from claude_bundles.conductor_stop import validate_conductor_closeout

    verdict = validate_conductor_closeout(body, packet_text=packet_text)
    if not verdict.ok and verdict.reason == "s4b_g1_pin_missing":
        return "s4b_g1_pin_missing"
    return None


def _map_closeout_status(degraded_reason: str | None) -> CloseoutStatus:
    """Map the worker's degraded_reason to an ImplementCloseout status.

    None              -> COMPLETE (clean finished run with tool calls)
    "run_status=..."  -> FAILED   (the SDK run itself did not finish)
    NO_RUN_*          -> FAILED   (run produced nothing — see NO_RUN_DEGRADED_REASONS)
    anything else     -> PARTIAL  (ran but degraded, e.g. pinned write miss)
    """
    if degraded_reason is None:
        return CloseoutStatus.COMPLETE
    if degraded_reason.startswith("run_status="):
        return CloseoutStatus.FAILED
    if degraded_reason in NO_RUN_DEGRADED_REASONS:
        return CloseoutStatus.FAILED
    return CloseoutStatus.PARTIAL
