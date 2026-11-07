"""Load ``model_bans`` rows from route_policy.yaml."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from model_id import ModelId
from model_id.entity import canonical_model_slug

from implement_admission.routing import default_policy_path, load_route_policy


@dataclass(frozen=True, slots=True)
class ModelBanEntry:
    model: str
    match_slug: str
    scope_all: bool
    roles: frozenset[str]
    reason: str
    ref: str
    since: str


def normalize_model_for_ban(model: str) -> str:
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
    raw = policy.get("model_bans")
    if not raw:
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


@lru_cache(maxsize=4)
def _cached_bans(policy_path: str, mtime_ns: int) -> tuple[ModelBanEntry, ...]:
    del mtime_ns
    return parse_model_bans(load_route_policy())


def load_model_bans(path: Path | None = None) -> tuple[ModelBanEntry, ...]:
    policy_path = path or default_policy_path()
    if path is not None:
        return parse_model_bans(load_route_policy(path))
    return _cached_bans(str(policy_path), policy_path.stat().st_mtime_ns)


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
