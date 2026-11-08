"""Load ``model_bans`` rows from route_policy.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from model_id import ModelId
from model_id.entity import canonical_model_slug

from implement_admission.routing import default_policy_path, load_route_policy

_EFFORT_DASH_SUFFIXES = ("-fast", "-high", "-max", "-low", "-medium", "-xhigh")


@dataclass(frozen=True, slots=True)
class ModelBanEntry:
    model: str
    match_slug: str
    scope_all: bool
    roles: frozenset[str]
    reason: str
    ref: str
    since: str


class ModelBansUnloadableError(Exception):
    """``model_bans`` block missing or malformed (fail-closed at admit)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.code = "MODEL_BANS_UNLOADABLE"


class ModelBanUnparseableError(ValueError):
    """Non-empty model id could not be normalized for ban matching."""


def _strip_dash_effort_suffix(slug: str) -> str:
    lower = slug.lower()
    for suf in _EFFORT_DASH_SUFFIXES:
        if lower.endswith(suf) and len(lower) > len(suf):
            return lower[: -len(suf)]
    return lower


def _wire_base_slug(model: str) -> str:
    raw = str(model).strip()
    if not raw:
        raise ModelBanUnparseableError("empty model id")
    if "__effort_" in raw:
        raw = raw.split("__effort_", 1)[0].strip()
    parsed = ModelId.parse(raw)
    return canonical_model_slug(parsed).lower()


def normalize_model_for_ban(model: str) -> str:
    return _wire_base_slug(model)


def ban_slug_variants(model: str) -> frozenset[str]:
    base = _wire_base_slug(model)
    stripped = _strip_dash_effort_suffix(base)
    if stripped == base:
        return frozenset({base})
    return frozenset({base, stripped})


def _parse_scope(raw: Any) -> tuple[bool, frozenset[str]]:
    if raw == "all" or raw is None:
        return True, frozenset()
    if isinstance(raw, dict):
        roles = raw.get("roles")
        if isinstance(roles, list):
            parsed = frozenset(str(r).strip().lower() for r in roles if str(r).strip())
            if not parsed:
                msg = (
                    "model_bans scope.roles must be non-empty when scope is role-scoped"
                )
                raise ValueError(msg)
            return False, parsed
    msg = f"model_bans scope must be 'all' or {{roles: [...]}}; got {raw!r}"
    raise ValueError(msg)


def parse_model_bans(policy: dict[str, Any]) -> tuple[ModelBanEntry, ...]:
    if "model_bans" not in policy:
        msg = "route policy missing model_bans key"
        raise ValueError(msg)
    raw = policy["model_bans"]
    if raw is None:
        msg = "model_bans must not be null"
        raise ValueError(msg)
    if raw == []:
        return ()
    if not isinstance(raw, list):
        raise ValueError("model_bans must be a list")
    out: list[ModelBanEntry] = []
    for idx, row in enumerate(raw):
        if not isinstance(row, dict):
            raise ValueError(f"model_bans[{idx}] must be a mapping")
        model = str(row.get("model") or "").strip()
        if not model:
            raise ValueError(f"model_bans[{idx}].model is required")
        reason = str(row.get("reason") or "").strip()
        ref = str(row.get("ref") or "").strip()
        since = str(row.get("since") or "").strip()
        if not reason or not ref or not since:
            raise ValueError(f"model_bans[{idx}] requires reason, ref, and since")
        scope_all, roles = _parse_scope(row.get("scope"))
        out.append(
            ModelBanEntry(
                model=model,
                match_slug=normalize_model_for_ban(model),
                scope_all=scope_all,
                roles=roles,
                reason=reason,
                ref=ref,
                since=since,
            )
        )
    return tuple(out)


def load_model_bans_strict(*, path: Path | None = None) -> tuple[ModelBanEntry, ...]:
    policy_path = path or default_policy_path()
    try:
        if not policy_path.is_file():
            msg = f"route policy missing at {policy_path}"
            raise ModelBansUnloadableError(msg)
        policy = load_route_policy(policy_path)
        return parse_model_bans(policy)
    except ModelBansUnloadableError:
        raise
    except yaml.YAMLError as exc:
        raise ModelBansUnloadableError(str(exc)) from exc
    except ValueError as exc:
        raise ModelBansUnloadableError(str(exc)) from exc
    except OSError as exc:
        raise ModelBansUnloadableError(str(exc)) from exc


@lru_cache(maxsize=4)
def _cached_bans(policy_path: str, mtime_ns: int) -> tuple[ModelBanEntry, ...]:
    del mtime_ns
    return load_model_bans_strict(path=Path(policy_path))


def load_model_bans(path: Path | None = None) -> tuple[ModelBanEntry, ...]:
    policy_path = path or default_policy_path()
    try:
        if path is not None:
            return load_model_bans_strict(path=path)
        mtime_ns = policy_path.stat().st_mtime_ns
        return _cached_bans(str(policy_path), mtime_ns)
    except ModelBansUnloadableError:
        raise
    except OSError as exc:
        raise ModelBansUnloadableError(str(exc)) from exc


def clear_model_ban_cache() -> None:
    _cached_bans.cache_clear()


def _role_in_scope(entry: ModelBanEntry, role: str | None) -> bool:
    if entry.scope_all:
        return True
    if not role or not str(role).strip():
        return False
    return str(role).strip().lower() in entry.roles


def _entry_slug_variants(entry: ModelBanEntry) -> frozenset[str]:
    base = entry.match_slug
    stripped = _strip_dash_effort_suffix(base)
    if stripped == base:
        return frozenset({base})
    return frozenset({base, stripped})


def find_matching_ban(
    resolved_model: str,
    *,
    role: str | None = None,
    bans: tuple[ModelBanEntry, ...] | None = None,
) -> ModelBanEntry | None:
    if not str(resolved_model or "").strip():
        return None
    entries = load_model_bans() if bans is None else bans
    try:
        variants = ban_slug_variants(resolved_model)
    except (TypeError, ValueError, ModelBanUnparseableError):
        raise ModelBanUnparseableError(
            f"unparseable model id for ban check: {resolved_model!r}"
        ) from None
    for entry in entries:
        if variants & _entry_slug_variants(entry) and _role_in_scope(entry, role):
            return entry
    return None


def advisor_wire_from_policy(policy: dict[str, Any] | None = None) -> str:
    pol = policy if policy is not None else load_route_policy()
    imp = pol.get("impresario") or {}
    adv = imp.get("advisor") or {}
    return str(adv.get("model") or "").strip()


def advisor_fallback_model_from_policy(policy: dict[str, Any] | None = None) -> str:
    pol = policy if policy is not None else load_route_policy()
    imp = pol.get("impresario") or {}
    mech = imp.get("mechanical") or {}
    model = str(mech.get("model") or "").strip()
    if model:
        return model
    workflows = pol.get("workflows") or {}
    auto = workflows.get("auto_judgment") or {}
    return str(auto.get("model") or "cursor/composer-2.5").strip()
