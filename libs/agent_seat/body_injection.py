"""Inline-only server-side rule/skill body injection (G3)."""

from __future__ import annotations

import hashlib
import time
from collections import OrderedDict
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

from agent_seat.guidance_entity import entity_slug_from_id
from agent_seat.inject_budget import (
    INJECTED_BODY_BUDGET_BYTES,
    INJECTED_BODY_TIMEOUT_MS,
    INJECTED_INDEX_TIMEOUT_MS,
    INJECTED_TOTAL_DEADLINE_MS,
)
from agent_seat.role_entity_sync import resolve_dispatch_capabilities

INJECTED_BODY_BUDGET_BYTES = INJECTED_BODY_BUDGET_BYTES  # re-export for callers
INJECTED_INDEX_TIMEOUT_MS = INJECTED_INDEX_TIMEOUT_MS
INJECTED_BODY_TIMEOUT_MS = INJECTED_BODY_TIMEOUT_MS
INJECTED_TOTAL_DEADLINE_MS = INJECTED_TOTAL_DEADLINE_MS


class SkillDeliveryChannel(StrEnum):
    LAYER_A_FS = "layer_a"
    LAYER_B_PROVIDER = "layer_b"
    LAYER_C_BODY = "layer_c"


@dataclass(frozen=True, slots=True)
class DispatchSkillContext:
    """Effective per-dispatch context (F3) — after model/mcp/role overrides."""

    model: str
    mcp_enabled: bool
    role: str | None = None
    platform: str = "*"
    inject_profile: str | None = None
    code_touching: bool = False
    provider_mount_slugs: frozenset[str] = frozenset()


def build_dispatch_skill_context(
    *,
    model: str,
    mcp_enabled: bool | None = None,
    role: str | None = None,
    platform: str = "*",
    inject_profile: str | None = None,
    code_touching: bool = False,
    provider_mount_slugs: frozenset[str] | None = None,
) -> DispatchSkillContext:
    from implement_admission.skill_source_table import canonical_table_key

    caps = resolve_dispatch_capabilities(model=model, mcp_enabled=mcp_enabled)
    mounts = provider_mount_slugs or frozenset()
    canonical_mounts = frozenset(canonical_table_key(slug) for slug in mounts)
    return DispatchSkillContext(
        model=model,
        mcp_enabled=bool(caps["mcp_connector_active"]),
        role=role,
        platform=platform,
        inject_profile=inject_profile,
        code_touching=code_touching,
        provider_mount_slugs=canonical_mounts,
    )


def select_skill_delivery_channel(
    slug_or_entity_id: str,
    ctx: DispatchSkillContext,
) -> SkillDeliveryChannel:
    """Capability-determined exactly-one channel with precedence B > C > A (D3)."""
    from implement_admission.skill_source_table import canonical_table_key

    canonical = canonical_table_key(slug_or_entity_id)
    if canonical in ctx.provider_mount_slugs:
        return SkillDeliveryChannel.LAYER_B_PROVIDER
    if not ctx.mcp_enabled:
        return SkillDeliveryChannel.LAYER_C_BODY
    return SkillDeliveryChannel.LAYER_A_FS


def emit_layer_a_fs_line(slug_or_entity_id: str) -> str:
    """Layer-A packet fs-line for MCP-capable dispatch roles."""
    from implement_admission.skill_fs_line import skill_slug_to_fs_line
    from implement_admission.skill_source_table import canonical_table_key

    slug = canonical_table_key(slug_or_entity_id)
    return skill_slug_to_fs_line(slug)


def filter_double_load_excluded(
    slugs: tuple[str, ...],
    *,
    already_delivered: frozenset[str],
) -> tuple[str, ...]:
    """Exclude slugs whose canonical agent_skill id was already delivered (D3)."""
    from implement_admission.skill_source_table import canonical_agent_skill_id

    out: list[str] = []
    for slug in slugs:
        key = canonical_agent_skill_id(slug)
        if key in already_delivered:
            continue
        out.append(slug)
    return tuple(out)


def web_auto_inject_skill_slugs() -> tuple[str, ...]:
    """Registry auto-inject slugs for web seats — empty since claude.ai UI attach."""
    return ()


# Channel-2/3 maps: agent_seat.inject_channels (shared-lib SOT for skill_suggest).


def is_web_seat_slug(seat: str) -> bool:
    """True when ``seat`` resolves to a profile with platform=web."""
    from agent_seat.profiles import load_profiles

    parts = seat.split("-", 1)
    if len(parts) != 2:
        return False
    profile = load_profiles().get((parts[0], parts[1]))
    return profile is not None and profile.platform == "web"


_PAYLOAD_CACHE: OrderedDict[tuple[str, str], str] = OrderedDict()
_PAYLOAD_CACHE_LOCK = Lock()
_PAYLOAD_CACHE_MAX = 256


class RequiredBodyUnresolved(Exception):  # noqa: N818 — public API; rename is out of scope
    """A delivery_criticality=required body failed to resolve."""

    def __init__(self, dropped: list[dict[str, Any]]) -> None:
        self.dropped = dropped
        super().__init__("required injected body unresolved")


def _delivery_priority(row: dict[str, Any]) -> int:
    raw = row.get("delivery_priority")
    if raw is None:
        return 100
    return int(raw)


def _slug_from_entry(entry: dict[str, Any]) -> str:
    name = str(entry.get("name") or "").strip()
    if name:
        return name
    entity_id = str(entry.get("id") or "")
    return entity_id.split(":", 1)[-1] if ":" in entity_id else entity_id


def _marker(marker_prefix: str, slug: str, digest: str) -> str:
    return f"<!-- {marker_prefix}:{slug} digest:{digest} -->"


def _block_bytes(marker_prefix: str, slug: str, digest: str, body: str) -> int:
    return len(f"\n\n{_marker(marker_prefix, slug, digest)}\n```markdown\n{body}\n```")


def _cache_get(key: tuple[str, str]) -> str | None:
    with _PAYLOAD_CACHE_LOCK:
        value = _PAYLOAD_CACHE.get(key)
        if value is not None:
            _PAYLOAD_CACHE.move_to_end(key)
        return value


def _cache_put(key: tuple[str, str], body: str) -> None:
    with _PAYLOAD_CACHE_LOCK:
        _PAYLOAD_CACHE[key] = body
        _PAYLOAD_CACHE.move_to_end(key)
        while len(_PAYLOAD_CACHE) > _PAYLOAD_CACHE_MAX:
            _PAYLOAD_CACHE.popitem(last=False)


def clear_payload_cache_for_tests() -> None:
    """Test helper — reset the process-local payload LRU."""
    with _PAYLOAD_CACHE_LOCK:
        _PAYLOAD_CACHE.clear()


def _fetch_skill_index_sync(
    seat: str,
    layer: str = "all",
    *,
    timeout_ms: int = INJECTED_INDEX_TIMEOUT_MS,
) -> list[dict[str, Any]]:
    try:
        with make_sync_client(
            DEFAULT_CORTEX_URL, timeout=timeout_ms / 1000.0
        ) as client:
            resp = client.get(
                "/skills",
                params={"layer": layer, "for_agent": seat},
            )
            if resp.status_code != 200:
                return []
            payload = resp.json()
            if not isinstance(payload, dict):
                return []
            items = payload.get("items")
            return items if isinstance(items, list) else []
    except Exception:
        return []


def _fetch_body_sync(
    entity_id: str,
    expected_digest: str | None,
    *,
    include_non_active: bool = False,
    timeout_ms: int = INJECTED_BODY_TIMEOUT_MS,
) -> tuple[dict[str, Any] | None, str | None]:
    """Return (payload, drop_reason). ``drop_reason`` set on non-200 responses."""
    try:
        with make_sync_client(
            DEFAULT_CORTEX_URL, timeout=timeout_ms / 1000.0
        ) as client:
            params: dict[str, str] = {"id": entity_id}
            if expected_digest:
                params["expected_digest"] = expected_digest
            if include_non_active:
                params["include_non_active"] = "true"
            resp = client.get("/skills/body", params=params)
            if resp.status_code == 200:
                payload = resp.json()
                return (payload if isinstance(payload, dict) else None), None
            if resp.status_code == 409:
                return None, "digest_mismatch"
            if resp.status_code == 404:
                return None, "body_missing"
            return None, "unreachable"
    except Exception as exc:
        if exc.__class__.__name__.endswith("Timeout"):
            return None, "timeout"
        return None, "unreachable"


def _fetch_payload(
    entity_id: str,
    digest: str,
    *,
    timeout_ms: int,
    metrics: dict[str, Any],
) -> tuple[str | None, str | None]:
    key = (entity_id, digest)
    cached = _cache_get(key)
    if cached is not None:
        metrics["cache_hit"] = True
        return cached, None
    payload, reason = _fetch_body_sync(entity_id, digest, timeout_ms=timeout_ms)
    if payload is None:
        return None, reason or "unreachable"
    body = str(payload.get("body") or "")
    _cache_put(key, body)
    metrics["cold_fetches"] = int(metrics.get("cold_fetches", 0)) + 1
    return body, None


def build_injected_bodies_md(
    seat: str,
    entries: list[dict[str, Any]],
    already_present: str = "",
    *,
    budget_bytes: int | None = INJECTED_BODY_BUDGET_BYTES,
    marker_prefix: str = "injected-body",
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]]]:
    """Dedup (marker-based), wrap, and continue-after-drop budget packing."""
    del seat  # reserved for future seat-specific wrap policy
    blocks: list[str] = []
    injected: list[dict[str, Any]] = []
    dropped: list[dict[str, Any]] = []
    remaining = budget_bytes

    for entry in entries:
        entity_id = str(entry.get("id") or "")
        digest = str(entry.get("digest") or "")
        body = entry.get("body")
        if not entity_id or not digest or not isinstance(body, str):
            dropped.append({"id": entity_id or None, "reason": "body_missing"})
            continue
        slug = _slug_from_entry(entry)
        if _marker(marker_prefix, slug, digest) in already_present:
            continue
        block_size = _block_bytes(marker_prefix, slug, digest, body)
        if budget_bytes is not None and block_size > remaining:
            dropped.append({"id": entity_id, "reason": "budget"})
            continue
        block = f"\n\n{_marker(marker_prefix, slug, digest)}\n```markdown\n{body}\n```"
        blocks.append(block)
        injected.append({"id": entity_id, "digest": digest, "bytes": block_size})
        if budget_bytes is not None:
            remaining -= block_size

    return "".join(blocks), injected, dropped


def resolve_inline_only_bodies(
    seat: str,
    *,
    budget_bytes: int = INJECTED_BODY_BUDGET_BYTES,
    already_present: str = "",
    total_deadline_ms: int = INJECTED_TOTAL_DEADLINE_MS,
    index_timeout_ms: int = INJECTED_INDEX_TIMEOUT_MS,
    body_timeout_ms: int = INJECTED_BODY_TIMEOUT_MS,
) -> tuple[str, list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Index fetch → priority sort → body resolve → per-request assembly."""
    start = time.monotonic()
    metrics: dict[str, Any] = {
        "cache_hit": False,
        "cold_fetches": 0,
        "deadline_hit": False,
    }

    index = _fetch_skill_index_sync(seat, timeout_ms=index_timeout_ms)
    if not index:
        elapsed_ms = int((time.monotonic() - start) * 1000)
        metrics["elapsed_ms"] = elapsed_ms
        return "", [], [{"id": None, "reason": "unreachable"}], metrics

    sorted_index = sorted(
        index,
        key=lambda row: (
            _delivery_priority(row),
            str(row.get("name") or row.get("id") or ""),
        ),
    )

    resolved: list[dict[str, Any]] = []
    prefetch_dropped: list[dict[str, Any]] = []
    deadline_hit = False

    for row in sorted_index:
        if not deadline_hit:
            elapsed_ms = int((time.monotonic() - start) * 1000)
            if elapsed_ms >= total_deadline_ms:
                deadline_hit = True
                metrics["deadline_hit"] = True
        if deadline_hit:
            prefetch_dropped.append({"id": row.get("id"), "reason": "timeout"})
            continue

        entity_id = str(row.get("id") or "")
        digest = str(row.get("digest") or "")
        if not entity_id or not digest:
            prefetch_dropped.append({"id": entity_id or None, "reason": "body_missing"})
            continue

        body, reason = _fetch_payload(
            entity_id, digest, timeout_ms=body_timeout_ms, metrics=metrics
        )
        if body is None:
            prefetch_dropped.append(
                {"id": entity_id, "reason": reason or "unreachable"}
            )
            criticality = (row.get("delivery_criticality") or "").strip()
            if criticality == "required":
                raise RequiredBodyUnresolved(prefetch_dropped)
            continue

        resolved.append({**row, "body": body})

    block_md, injected, pack_dropped = build_injected_bodies_md(
        seat,
        resolved,
        already_present=already_present,
        budget_bytes=budget_bytes,
    )
    dropped = prefetch_dropped + pack_dropped

    for drop in pack_dropped:
        entity_id = drop.get("id")
        if not entity_id:
            continue
        row = next((r for r in sorted_index if r.get("id") == entity_id), None)
        if row and (row.get("delivery_criticality") or "").strip() == "required":
            raise RequiredBodyUnresolved(dropped)

    metrics["elapsed_ms"] = int((time.monotonic() - start) * 1000)
    return block_md, injected, dropped, metrics


def _fetch_invariant_entries_for(
    entity_ids: tuple[str, ...],
) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for entity_id in entity_ids:
        payload, _reason = _fetch_body_sync(entity_id, None, include_non_active=True)
        if not payload:
            continue
        digest = str(payload.get("digest") or "")
        body = str(payload.get("body") or "")
        if digest and body:
            _cache_put((entity_id, digest), body)
        entries.append(
            {
                "id": entity_id,
                "name": entity_slug_from_id(entity_id),
                "digest": digest,
                "body": body,
            }
        )
    return entries


def fetch_invariant_pair_entries() -> list[dict[str, Any]]:
    """Thin shim — coding-scope entries from the shared registry."""
    from agent_seat.inject_registry import coding_scope_inject_entity_ids

    return _fetch_invariant_entries_for(coding_scope_inject_entity_ids())


def fetch_web_invariant_entries() -> list[dict[str, Any]]:
    """Thin shim — universal-scope entries from the shared registry."""
    from agent_seat.inject_registry import INJECT_REGISTRY, InjectScope

    entity_ids = tuple(
        entry.entity_id
        for entry in INJECT_REGISTRY
        if entry.scope == InjectScope.UNIVERSAL
    )
    return _fetch_invariant_entries_for(entity_ids)


def _invariant_presence_sentinel(block: str, injected: list[dict[str, Any]]) -> str:
    """Grep-able marker confirming the full invariant block loaded."""
    count = len([i for i in injected if str(i.get("id") or "").strip()])
    if count == 0:
        return ""
    normalized = block.replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return f"<!-- cortex:invariant-skills-autoappend sha256={digest} count={count} -->"


def append_invariant_pair_bodies(
    content: str,
    *,
    already_present: str = "",
    entries: list[dict[str, Any]] | None = None,
    role: str | None = None,
    platform: str = "*",
    inject_profile: str | None = None,
    code_touching: bool = True,
    packet_invariant_ids: tuple[str, ...] = (),
) -> tuple[str, dict[str, Any]]:
    """Thin shim over ``resolve_injected_bodies`` with sentinel marker."""
    from agent_seat.inject_registry import resolve_injected_bodies

    del entries  # legacy kwarg ignored — resolver is authoritative
    present = f"{already_present}{content}"
    resolution = resolve_injected_bodies(
        "",
        role=role,
        platform=platform,
        inject_profile=inject_profile,
        code_touching=code_touching,
        packet_invariant_ids=packet_invariant_ids,
        already_present=present,
        budget_bytes=None,
        inline_only_dispatch=platform != "cursor",
    )
    block = resolution.block_md
    injected = resolution.injected
    dropped = resolution.dropped
    meta: dict[str, Any] = {
        "injected": injected,
        "dropped": dropped,
        "block": block,
        "telemetry": resolution.telemetry,
    }
    if not block:
        return content, meta
    sentinel = _invariant_presence_sentinel(block, injected)
    if sentinel and "cortex:invariant-skills-autoappend" not in present:
        block = f"\n\n{sentinel}{block}"
    return content + block, meta
