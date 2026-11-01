"""Role-less impresario critic generate admission (API cloud models).

Documented code-review dispatch omits ``role`` and ``seat`` when ``model`` is an
``impresario.allowed_critics`` cloud entry (non ``cdp/``, non ``cursor/``) and
``contract`` is a review class job. Admit resolves an API ``generate`` role whose
``allowed_models`` includes that model.
"""

from __future__ import annotations

from agent_seat.dispatch_review_contract import REVIEW_CONTRACTS
from agent_seat.dispatch_role_catalog import generate_roles
from agent_seat.registry import load_roles
from model_id import ModelId

from implement_admission.check_review_substrate import CHECK_REVIEW_API_ROLES
from implement_admission.impresario_policy import (
    ImpresarioPolicy,
    load_impresario_policy,
)

_ROLELESS_CRITIC_ROLE_PREFERENCE: tuple[str, ...] = (
    "synthesizer",
    "skeptic",
)


def cloud_impresario_critic_models(
    *,
    policy: ImpresarioPolicy | None = None,
) -> tuple[str, ...]:
    """Sorted cloud entries from ``impresario.allowed_critics`` (non cdp/cursor)."""
    pol = policy or load_impresario_policy()
    return tuple(
        sorted(model for model in pol.allowed_critics if _cloud_allowed_critic(model, pol))
    )


def _cloud_allowed_critic(model: str, policy: ImpresarioPolicy) -> bool:
    normalized = (model or "").strip()
    if not normalized or normalized not in policy.allowed_critics:
        return False
    try:
        backend = ModelId.parse(normalized).backend_type
    except (TypeError, ValueError):
        return False
    return backend not in {"cdp", "cursor_sdk"}


def is_roleless_impresario_critic_generate(
    op: str,
    role: str | None,
    seat: str | None,
    job: str | None,
    model: str | None,
    *,
    policy: ImpresarioPolicy | None = None,
) -> bool:
    """True when the call shape is the documented role-less impresario critic generate."""
    if (op or "").strip() != "generate":
        return False
    if role and str(role).strip():
        return False
    if seat and str(seat).strip():
        return False
    if (job or "").strip().lower() not in REVIEW_CONTRACTS:
        return False
    if not isinstance(model, str) or not model.strip():
        return False
    pol = policy or load_impresario_policy()
    if not _cloud_allowed_critic(model, pol):
        return False
    try:
        resolve_roleless_impresario_critic_api_role(str(model).strip(), policy=pol)
    except ValueError:
        return False
    return True


def resolve_roleless_impresario_critic_api_role(
    model: str,
    *,
    policy: ImpresarioPolicy | None = None,
) -> str:
    """Map an admitted critic model to an API generate role (config-derived)."""
    normalized = (model or "").strip()
    pol = policy or load_impresario_policy()
    if not _cloud_allowed_critic(normalized, pol):
        raise ValueError(f"model {normalized!r} is not a cloud impresario critic")

    roles_cfg = load_roles()
    candidates: list[str] = []
    for role_slug in generate_roles():
        profile = roles_cfg.get(role_slug)
        if profile is None:
            continue
        allowed = profile.allowed_models or []
        if normalized in allowed:
            candidates.append(role_slug)
    if not candidates:
        raise ValueError(
            f"no API generate role allows impresario critic model {normalized!r}"
        )

    for preferred in _ROLELESS_CRITIC_ROLE_PREFERENCE:
        if preferred in candidates:
            return preferred
    for check_review_role in sorted(CHECK_REVIEW_API_ROLES):
        if check_review_role in candidates:
            return check_review_role
    return candidates[0]
