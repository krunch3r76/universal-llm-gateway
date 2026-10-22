"""Refuse a code-contract cursor-sdk admit that silently swaps models.

Callers are ``prepare_cursor_sdk_generate`` (before mint) and the admit
FACT stampers. An omitted ``model`` still resolves through the workflow
registry. A supplied pin must be the model that is admitted. This module
does not ban ``cursor/composer-2.5``; a pin that names it is admitted.
"""

from __future__ import annotations

from cursor_capabilities import canonical_cursor_bare_id

from .admission import FrontierEndpointError
from .cursor_sdk_generate_signals import emit_sdk_model_mismatch

# Cursor-sdk jobs that run code. Judgment jobs (investigate, conductor)
# are outside this gate. freeform is included because the 19040/19044/
# 19064/(br) admits were freeform code lanes.
CODE_CONTRACTS = frozenset(
    {
        "freeform",
        "implement",
        "mechanical",
        "pure-mechanical",
        "wrap",
    }
)


def bare_model_id(model: str) -> str:
    """Canonical bare id, or a lowercased cursor-prefix strip when parse fails."""
    try:
        return canonical_cursor_bare_id(model)
    except ValueError:
        text = model.strip().lower()
        if text.startswith("cursor/"):
            return text.removeprefix("cursor/")
        return text


def models_identify(requested: str, resolved: str) -> bool:
    """True when both strings name the same cursor model after canonicalization."""
    return bare_model_id(requested) == bare_model_id(resolved)


def append_resolved_model_fact(body: str, resolved_model: str) -> str:
    """Append ``resolved_model:`` to an admit FACT body.

    The line is the quote later seats read. A body that already carries
    the same line is returned unchanged.
    """
    line = f"resolved_model: {resolved_model}"
    if line in body.splitlines():
        return body
    if not body:
        return line
    if body.endswith("\n"):
        return f"{body}{line}\n"
    return f"{body}\n{line}"


def refuse_code_contract_model_mismatch(
    *,
    request_id: str,
    contract: str | None,
    requested_model: str | None,
    resolved_model: str,
) -> None:
    """Abort before admit when a code contract would swap the pinned model.

    No-op when the contract is not a code contract, or when the caller
    omitted ``model`` (the workflow default is not a substitution of a
    pin). A matching pin, including ``cursor/composer-2.5``, returns.
    On mismatch, emits ``frontier.sdk.model.mismatch`` and raises
    ``FrontierEndpointError`` with code ``resolved_model_mismatch``.
    """
    wire = (contract or "").strip().lower()
    if wire not in CODE_CONTRACTS:
        return
    requested = (requested_model or "").strip()
    if not requested:
        return
    if models_identify(requested, resolved_model):
        return
    emit_sdk_model_mismatch(
        request_id=request_id,
        contract=wire,
        requested_model=requested,
        resolved_model=resolved_model,
    )
    raise FrontierEndpointError(
        request_id=request_id,
        field="model",
        reason=(
            "code-contract admit refused: resolved_model "
            f"{resolved_model!r} differs from requested model {requested!r}; "
            "no silent substitution"
        ),
        status_code=422,
        code="resolved_model_mismatch",
        details={
            "requested_model": requested,
            "resolved_model": resolved_model,
            "contract": wire,
            "retryable": False,
        },
    )
