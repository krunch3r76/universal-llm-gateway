"""Inline-only server-side rule/skill body injection (G3)."""

from __future__ import annotations

import hashlib
import os
import time
from collections import OrderedDict
from threading import Lock
from typing import Any

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client

INJECTED_BODY_BUDGET_BYTES = int(os.getenv("INJECTED_BODY_BUDGET_BYTES", "24000"))
INJECTED_INDEX_TIMEOUT_MS = int(os.getenv("INJECTED_INDEX_TIMEOUT_MS", "300"))
INJECTED_BODY_TIMEOUT_MS = int(os.getenv("INJECTED_BODY_TIMEOUT_MS", "300"))
INJECTED_TOTAL_DEADLINE_MS = int(os.getenv("INJECTED_TOTAL_DEADLINE_MS", "1500"))

# Web Slice-F auto-inject: appended to system prompt after cortex_boot (proxy path)
# and treated as loaded by skill_suggest for web seats (cortex pair only).
WEB_BOOT_INJECT_ENTITY_IDS: tuple[str, ...] = (
    "agent_skill:cortex-orientation",
    "agent_skill:cortex-provenance-discipline",
)

INVARIANT_PAIR_ENTITY_IDS: tuple[str, ...] = (
    "agent_skill:architecture-invariants",
    "agent_skill:ulg-architecture",
)

CODING_SESSION_BUNDLE: dict[str, tuple[str, ...]] = {
    "inject": INVARIANT_PAIR_ENTITY_IDS,
    "advertise": (
        "implement-work-item",
        "git-posture",
        "service-lifecycle",
        "completion-provenance-discipline",
        "fs",
    ),
}


def web_auto_inject_skill_slugs() -> tuple[str, ...]:
    """Bare slugs server-injected on web seats (Slice F + skill_suggest preload)."""
    return tuple(
        entity_id.removeprefix("agent_skill:") for entity_id in WEB_BOOT_INJECT_ENTITY_IDS
    )


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
        payload, _reason = _fetch_body_sync(
            entity_id, None, include_non_active=True
        )
        if not payload:
            continue
        digest = str(payload.get("digest") or "")
        body = str(payload.get("body") or "")
        if digest and body:
            _cache_put((entity_id, digest), body)
        entries.append(
            {
                "id": entity_id,
                "name": entity_id.removeprefix("agent_skill:"),
                "digest": digest,
                "body": body,
            }
        )
    return entries


def fetch_invariant_pair_entries() -> list[dict[str, Any]]:
    """Resolve the architecture invariant pair for code-touching generate."""
    return _fetch_invariant_entries_for(INVARIANT_PAIR_ENTITY_IDS)


def fetch_web_invariant_entries() -> list[dict[str, Any]]:
    """Resolve invariant-skill bodies for the web Slice-F path."""
    return _fetch_invariant_entries_for(WEB_BOOT_INJECT_ENTITY_IDS)


def _invariant_presence_sentinel(block: str, injected: list[dict[str, Any]]) -> str:
    """Grep-able marker confirming the full invariant block loaded."""
    count = len([i for i in injected if str(i.get("id") or "").strip()])
    if count == 0:
        return ""
    normalized = block.replace("\r\n", "\n").replace("\r", "\n")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return (
        f"<!-- cortex:invariant-skills-autoappend sha256={digest} count={count} -->"
    )


def append_invariant_pair_bodies(
    content: str,
    *,
    already_present: str = "",
    entries: list[dict[str, Any]] | None = None,
) -> tuple[str, dict[str, Any]]:
    """Append invariant skill bodies with sentinel; seat-agnostic."""
    resolved_entries = (
        entries if entries is not None else fetch_invariant_pair_entries()
    )
    present = f"{already_present}{content}"
    block, injected, dropped = build_injected_bodies_md(
        "",
        resolved_entries,
        already_present=present,
        marker_prefix="invariant-skill",
        budget_bytes=None,
    )
    meta: dict[str, Any] = {"injected": injected, "dropped": dropped, "block": block}
    if not block:
        return content, meta
    sentinel = _invariant_presence_sentinel(block, injected)
    if sentinel and "cortex:invariant-skills-autoappend" not in present:
        block = f"\n\n{sentinel}{block}"
    return content + block, meta
