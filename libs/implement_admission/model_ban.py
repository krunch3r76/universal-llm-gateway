"""Config-driven model bans read from ``route_policy.yaml`` ``model_bans:``."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from model_id import ModelId
from model_id.entity import canonical_model_slug

from implement_admission.routing import default_policy_path, load_route_policy

_ARGON_IMPRESARIO_BARRED_CODE = "ARGON_ROLE_BARRED"


@dataclass(frozen=True, slots=True)
class ModelBanEntry:
    """One ``model_bans`` row after normalization."""

    model: str
    match_slug: str
    scope_all: bool
    roles: frozenset[str]
    reason: str
    ref: str
    since: str


def normalize_model_for_ban(model: str) -> str:
    """Slug used to match a resolved or configured model id (incl. xai/ aliases)."""
    return canonical_model_slug(ModelId.parse(str(model).strip()))


def _parse_scope(raw: Any) -> tuple[bool, frozenset[str]]:
    if raw == "all" or raw is None:
        return True, frozenset()
    if isinstance(raw, dict):
        roles = raw.get("roles")
        if isinstance(roles, list):
            return False, frozenset(
                str(r).strip().lower() for r in roles if str(r).strip()
            )
    msg = f"model_bans scope must be 'all' or {{roles: [...]}}; got {raw!r}"
    raise ValueError(msg)


def parse_model_bans(policy: dict[str, Any]) -> tuple[ModelBanEntry, ...]:
    """Parse ``model_bans`` from a route policy mapping."""
    raw = policy.get("model_bans")
    if not raw:
        return ()
    if not isinstance(raw, list):
        msg = "model_bans must be a list"
        raise ValueError(msg)
    out: list[ModelBanEntry] = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            msg = f"model_bans[{idx}] must be a mapping"
            raise ValueError(msg)
        model = str(row.get("model") or "").strip()
        if not model:
            msg = f"model_bans[{idx}].model is required"
            raise ValueError(msg)
        reason = str(row.get("reason") or "").strip()
        ref = str(row.get("ref") or "").strip()
        since = str(row.get("since") or "").strip()
        if not reason or not ref or not since:
            msg = f"model_bans[{idx}] requires reason, ref, and since"
            raise ValueError(msg)
        scope_all, roles = _parse_scope(row.get("scope"))
        match_slug = normalize_model_for_ban(model)
        out.append(
            ModelBanEntry(
                model=model,
                match_slug=match_slug,
                scope_all=scope_all,
                roles=roles,
                reason=reason,
                ref=ref,
                since=since,
            )
        )
    return tuple(out)


@lru_cache(maxsize=4)
def _cached_bans(policy_path: str, mtime_ns: int) -> tuple[ModelBanEntry, ...]:
    del mtime_ns
    return parse_model_bans(load_route_policy())


def load_model_bans(path: Path | None = None) -> tuple[ModelBanEntry, ...]:
    """Load and cache ban entries from the route policy file."""
    policy_path = path or default_policy_path()
    if path is not None:
        return parse_model_bans(load_route_policy(path))
    mtime = policy_path.stat().st_mtime_ns
    return _cached_bans(str(policy_path), mtime)


def clear_model_ban_cache() -> None:
    _cached_bans.cache_clear()


def _role_in_scope(entry: ModelBanEntry, role: str | None) -> bool:
    if entry.scope_all:
        return True
    if not role or not str(role).strip():
        return False
    return str(role).strip().lower() in entry.roles


def find_matching_ban(
    resolved_model: str,
    *,
    role: str | None = None,
    bans: tuple[ModelBanEntry, ...] | None = None,
) -> ModelBanEntry | None:
    entries = load_model_bans() if bans is None else bans
    try:
        slug = normalize_model_for_ban(resolved_model)
    except (TypeError, ValueError):
        return None
    for entry in entries:
        if entry.match_slug == slug and _role_in_scope(entry, role):
            return entry
    return None


def ban_override_fields() -> dict[str, Any]:
    return {
        "ban_override": "Pass ban_override=true with non-empty ban_override_reason to waive.",
        "ban_override_reason": "Required audit string when ban_override=true.",
    }


def validate_ban_override_pair(
    ban_override: Any,
    ban_override_reason: Any,
) -> str | None:
    """Return a refusal ``code`` when override knobs are mismatched."""
    enabled = ban_override is True
    reason = str(ban_override_reason or "").strip()
    if enabled and not reason:
        return "ban_override_reason_required"
    if reason and not enabled:
        return "ban_override_required"
    return None


def model_ban_reject_details(
    entry: ModelBanEntry, *, resolved_model: str
) -> dict[str, Any]:
    details = {
        "model": resolved_model,
        "reason": entry.reason,
        "ref": entry.ref,
        **ban_override_fields(),
    }
    return details


def evaluate_dispatch_model_ban(
    *,
    resolved_model: str,
    explicit_pin: bool,
    workflow_key: str | None,
    role: str | None,
    ban_override: bool,
    ban_override_reason: str | None,
    caller_agent: str | None,
    bans: tuple[ModelBanEntry, ...] | None = None,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Return ``(reject_envelope, override_event_payload)``."""
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
        payload = {
            "caller": (caller_agent or "").strip() or None,
            "model": resolved_model,
            "reason": str(ban_override_reason or "").strip(),
            "ref": entry.ref,
        }
        return None, payload
    code = "MODEL_BANNED" if explicit_pin else "MODEL_BANNED_DEFAULT"
    omit_key = workflow_key or "auto_judgment"
    if explicit_pin:
        reason = f"model {resolved_model!r} is banned ({entry.ref})"
    elif omit_key.startswith("workflows."):
        reason = f"omit-model resolves to banned model via {omit_key}"
    elif omit_key.startswith("team-dispatch-admit."):
        reason = f"omit-model resolves to banned model via {omit_key}"
    else:
        reason = f"omit-model resolves to banned model via workflows.{omit_key}"
    details = model_ban_reject_details(entry, resolved_model=resolved_model)
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


def impresario_argon_ban_roles(
    bans: tuple[ModelBanEntry, ...] | None = None,
) -> frozenset[str]:
    """Roles barred for advisor-wire argon, derived from config (no hardcoded slug)."""
    from implement_admission.impresario_policy import ADVISOR_MODEL

    entries = load_model_bans() if bans is None else bans
    try:
        advisor_slug = normalize_model_for_ban(ADVISOR_MODEL)
    except (TypeError, ValueError):
        return frozenset()
    roles: set[str] = set()
    for entry in entries:
        if entry.match_slug == advisor_slug and not entry.scope_all:
            roles.update(entry.roles)
    return frozenset(roles)


def argon_role_barred(
    impresario_role: str | None, *, bans: tuple[ModelBanEntry, ...] | None = None
) -> bool:
    if not impresario_role:
        return False
    return impresario_role.strip().lower() in impresario_argon_ban_roles(bans=bans)


def impresario_argon_refusal(
    *,
    model: str,
    impresario_role: str | None,
    bans: tuple[ModelBanEntry, ...] | None = None,
) -> tuple[str, str] | None:
    """Return ``(code, message)`` when impresario advisor route hits a role-scoped ban."""
    from implement_admission.impresario_policy import ADVISOR_MODEL

    normalized = (model or "").strip()
    if normalized != ADVISOR_MODEL:
        return None
    role = (impresario_role or "").strip().lower()
    if role == "advisor":
        return None
    entry = find_matching_ban(normalized, role=impresario_role, bans=bans)
    if entry is None:
        return None
    return (
        _ARGON_IMPRESARIO_BARRED_CODE,
        f"cursor/gemini-4-argon is barred for impresario role {impresario_role!r}",
    )
