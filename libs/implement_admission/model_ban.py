"""Config-driven model bans enforced at team_dispatch admit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from implement_admission.model_ban_load import (
    ModelBanEntry,
    ModelBansUnloadableError,
    ModelBanUnparseableError,
    clear_model_ban_cache,
    find_matching_ban,
    load_model_bans,
    normalize_model_for_ban,
    parse_model_bans,
)

__all__ = [
    "ModelBanEntry",
    "ModelBanVerdict",
    "argon_role_barred",
    "ban_override_fields",
    "ban_override_pair_reject",
    "check_model_ban",
    "check_model_ban_safe",
    "clear_model_ban_cache",
    "evaluate_dispatch_model_ban",
    "find_matching_ban",
    "impresario_argon_refusal",
    "load_model_bans",
    "model_ban_wire_fields",
    "normalize_model_for_ban",
    "parse_model_bans",
    "validate_ban_override_pair",
]

from implement_admission.model_ban_impresario import (  # noqa: E402
    argon_role_barred,
    impresario_argon_refusal,
)


@dataclass(frozen=True, slots=True)
class ModelBanVerdict:
    allowed: bool
    reject_envelope: dict[str, Any] | None
    override_payload: dict[str, Any] | None
    matched: ModelBanEntry | None


def ban_override_fields() -> dict[str, Any]:
    return {
        "ban_override": "Pass ban_override=true with non-empty ban_override_reason to waive.",
        "ban_override_reason": "Required audit string when ban_override=true.",
    }


def model_ban_wire_fields(opts: dict[str, Any]) -> tuple[bool, str | None, str | None]:
    """Top-level wire only — ``generation_options`` cannot supply ban override."""
    enabled = opts.get("ban_override") is True
    reason_raw = opts.get("ban_override_reason")
    reason = str(reason_raw).strip() if reason_raw is not None else None
    caller = opts.get("caller_agent")
    caller_s = str(caller).strip() if caller is not None else None
    return enabled, reason or None, caller_s or None


def validate_ban_override_pair(
    ban_override: Any,
    ban_override_reason: Any,
) -> str | None:
    enabled = ban_override is True
    reason = str(ban_override_reason or "").strip()
    if enabled and not reason:
        return "ban_override_reason_required"
    if reason and not enabled:
        return "ban_override_required"
    return None


def ban_override_pair_reject(
    ban_override: Any,
    ban_override_reason: Any,
    *,
    status_code: int = 422,
    advertise_ban_override: bool = True,
) -> dict[str, Any] | None:
    code = validate_ban_override_pair(ban_override, ban_override_reason)
    if code is None:
        return None
    field = (
        "ban_override_reason"
        if code == "ban_override_reason_required"
        else "ban_override"
    )
    details = ban_override_fields() if advertise_ban_override else None
    envelope: dict[str, Any] = {
        "admitted": False,
        "status_code": status_code,
        "code": code,
        "field": field,
        "reason": (
            "ban_override_reason is required when ban_override=true"
            if code == "ban_override_reason_required"
            else "ban_override=true is required when ban_override_reason is set"
        ),
    }
    if details is not None:
        envelope["details"] = details
    return envelope


def _reject_details(
    entry: ModelBanEntry,
    *,
    resolved_model: str,
    advertise_ban_override: bool = True,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "model": resolved_model,
        "reason": entry.reason,
        "ref": entry.ref,
    }
    if advertise_ban_override:
        out.update(ban_override_fields())
    return out


def evaluate_dispatch_model_ban(
    *,
    resolved_model: str,
    explicit_pin: bool,
    workflow_key: str | None,
    role: str | None,
    ban_override: bool,
    ban_override_reason: str | None,
    caller_agent: str | None,
    dispatch_id: str | None = None,
    bans: tuple[ModelBanEntry, ...] | None = None,
    advertise_ban_override: bool = True,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    pair_reject = ban_override_pair_reject(
        ban_override,
        ban_override_reason,
        advertise_ban_override=advertise_ban_override,
    )
    if pair_reject is not None:
        return pair_reject, None
    entry = find_matching_ban(resolved_model, role=role, bans=bans)
    if entry is None:
        return None, None
    if ban_override:
        payload: dict[str, Any] = {
            "caller": (caller_agent or "").strip() or None,
            "model": resolved_model,
            "reason": str(ban_override_reason or "").strip(),
            "ref": entry.ref,
        }
        if dispatch_id and str(dispatch_id).strip():
            payload["dispatch_id"] = str(dispatch_id).strip()
        return None, payload
    code = "MODEL_BANNED" if explicit_pin else "MODEL_BANNED_DEFAULT"
    omit_key = workflow_key or "omit_unlabeled"
    if explicit_pin:
        reason = f"model {resolved_model!r} is banned ({entry.ref})"
    else:
        reason = f"omit-model resolves to banned model via {omit_key}"
    details = _reject_details(
        entry,
        resolved_model=resolved_model,
        advertise_ban_override=advertise_ban_override,
    )
    if not explicit_pin:
        details["omit_config_key"] = omit_key
    return (
        {
            "admitted": False,
            "status_code": 403,
            "code": code,
            "field": "model",
            "reason": reason,
            "details": details,
        },
        None,
    )


def _unloadable_verdict(exc: ModelBansUnloadableError) -> ModelBanVerdict:
    return ModelBanVerdict(
        allowed=False,
        reject_envelope={
            "admitted": False,
            "status_code": 503,
            "code": exc.code,
            "field": "model_bans",
            "reason": str(exc),
        },
        override_payload=None,
        matched=None,
    )


def _unparseable_verdict(model: str) -> ModelBanVerdict:
    return ModelBanVerdict(
        allowed=False,
        reject_envelope={
            "admitted": False,
            "status_code": 422,
            "code": "MODEL_BAN_UNPARSEABLE",
            "field": "model",
            "reason": f"model id {model!r} is not parseable for ban check",
        },
        override_payload=None,
        matched=None,
    )


def check_model_ban(
    *,
    resolved_model: str,
    explicit_pin: bool = True,
    workflow_key: str | None = None,
    role: str | None = None,
    ban_override: bool = False,
    ban_override_reason: str | None = None,
    caller_agent: str | None = None,
    dispatch_id: str | None = None,
    bans: tuple[ModelBanEntry, ...] | None = None,
    advertise_ban_override: bool = True,
) -> ModelBanVerdict:
    try:
        return _check_model_ban_inner(
            resolved_model=resolved_model,
            explicit_pin=explicit_pin,
            workflow_key=workflow_key,
            role=role,
            ban_override=ban_override,
            ban_override_reason=ban_override_reason,
            caller_agent=caller_agent,
            dispatch_id=dispatch_id,
            bans=bans,
            advertise_ban_override=advertise_ban_override,
        )
    except ModelBanUnparseableError:
        return _unparseable_verdict(resolved_model)


def _check_model_ban_inner(
    *,
    resolved_model: str,
    explicit_pin: bool,
    workflow_key: str | None,
    role: str | None,
    ban_override: bool,
    ban_override_reason: str | None,
    caller_agent: str | None,
    dispatch_id: str | None,
    bans: tuple[ModelBanEntry, ...] | None,
    advertise_ban_override: bool,
) -> ModelBanVerdict:
    reject, override = evaluate_dispatch_model_ban(
        resolved_model=resolved_model,
        explicit_pin=explicit_pin,
        workflow_key=workflow_key,
        role=role,
        ban_override=ban_override,
        ban_override_reason=ban_override_reason,
        caller_agent=caller_agent,
        dispatch_id=dispatch_id,
        bans=bans,
        advertise_ban_override=advertise_ban_override,
    )
    if reject is not None:
        entry = find_matching_ban(resolved_model, role=role, bans=bans)
        return ModelBanVerdict(
            allowed=False,
            reject_envelope=reject,
            override_payload=None,
            matched=entry,
        )
    if override is not None:
        entry = find_matching_ban(resolved_model, role=role, bans=bans)
        return ModelBanVerdict(
            allowed=True,
            reject_envelope=None,
            override_payload=override,
            matched=entry,
        )
    return ModelBanVerdict(
        allowed=True,
        reject_envelope=None,
        override_payload=None,
        matched=None,
    )


def check_model_ban_safe(
    **kwargs: Any,
) -> ModelBanVerdict:
    try:
        return check_model_ban(**kwargs)
    except ModelBansUnloadableError as exc:
        return _unloadable_verdict(exc)
