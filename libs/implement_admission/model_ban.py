"""Config-driven model bans enforced at team_dispatch admit."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from implement_admission.model_ban_load import (
    ModelBanEntry,
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
    "check_model_ban",
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


def _reject_details(entry: ModelBanEntry, *, resolved_model: str) -> dict[str, Any]:
    return {
        "model": resolved_model,
        "reason": entry.reason,
        "ref": entry.ref,
        **ban_override_fields(),
    }


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
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    pair_err = validate_ban_override_pair(ban_override, ban_override_reason)
    if pair_err:
        field = (
            "ban_override_reason"
            if pair_err == "ban_override_reason_required"
            else "ban_override"
        )
        return (
            {
                "admitted": False,
                "status_code": 422,
                "code": pair_err,
                "field": field,
                "reason": (
                    "ban_override_reason is required when ban_override=true"
                    if pair_err == "ban_override_reason_required"
                    else "ban_override=true is required when ban_override_reason is set"
                ),
                "details": ban_override_fields(),
            },
            None,
        )
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
    omit_key = workflow_key or "auto_judgment"
    if explicit_pin:
        reason = f"model {resolved_model!r} is banned ({entry.ref})"
    else:
        reason = f"omit-model resolves to banned model via {omit_key}"
    details = _reject_details(entry, resolved_model=resolved_model)
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
