"""Confer write-fence overlay for guarded cortex corpus paths."""

from __future__ import annotations

import re

from services.git_integration_worker.cursor_auto.closeout_relay_common import (
    CloseoutRelayPayload,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_fields import (
    extract_field_section,
)
from services.git_integration_worker.cursor_auto.closeout_relay_cortex_uri import (
    cortex_relpath,
    normalize_cortex_uri,
)
from services.git_integration_worker.cursor_auto.closeout_relay_effects import (
    machine_write_uris,
)

_FENCE_EXCEPTION_RE = re.compile(
    r"(?im)^fence_exception:\s*(?P<uri>cortex://.+?)\s*(?:—|–)\s*(?P<reason>.+)$"
)


def extract_fence_exception_lines(prose: str) -> list[str]:
    """Return ``fence_exception:`` lines authored in executor §2 prose."""
    return [match.group(0).strip() for match in _FENCE_EXCEPTION_RE.finditer(prose or "")]


def guard_matches_write(guard_uri: str, write_uri: str) -> bool:
    """True when normalized guard relpath equals or prefixes the write relpath."""
    guard_norm = normalize_cortex_uri(guard_uri)
    write_norm = normalize_cortex_uri(write_uri)
    guard_rel = cortex_relpath(guard_norm or "")
    write_rel = cortex_relpath(write_norm or "")
    if guard_rel is None or write_rel is None:
        return False
    if guard_rel == write_rel:
        return True
    prefix = guard_rel.rstrip("/") + "/"
    return write_rel.startswith(prefix)


def _announced_fence_exceptions(body: str) -> set[str]:
    """Normalized cortex URIs explicitly exempted via ``fence_exception:`` lines."""
    announced: set[str] = set()
    for match in _FENCE_EXCEPTION_RE.finditer(body):
        normalized = normalize_cortex_uri(match.group("uri"))
        if normalized is not None:
            announced.add(normalized)
    return announced


def _deltas_announces_write(body: str, write_uri: str) -> bool:
    """True when *deltas_to_spec* substantively announces *write_uri*."""
    normalized = normalize_cortex_uri(write_uri)
    if normalized is None:
        return False
    rel = cortex_relpath(normalized) or ""
    deltas = extract_field_section(body, "deltas_to_spec") or ""
    stripped = deltas.strip()
    if not stripped:
        return False
    if stripped.startswith("unresolved — not read:"):
        return False
    if stripped.startswith("unauthored —"):
        return False
    if stripped.startswith("none — field not authored"):
        return False
    return normalized in deltas or rel in deltas


def _write_announced_in_body(body: str, write_uri: str) -> bool:
    """True when *write_uri* is named in deltas_to_spec or fence_exception."""
    normalized = normalize_cortex_uri(write_uri)
    if normalized is None:
        return False
    if normalized in _announced_fence_exceptions(body):
        return True
    return _deltas_announces_write(body, write_uri)


def guarded_write_violations(
    *,
    wrapper_text: str | None,
    guard_uris: frozenset[str],
    body: str,
) -> list[str]:
    """Return guarded cortex writes lacking deltas_to_spec / fence_exception."""
    if not guard_uris:
        return []
    violations: list[str] = []
    seen: set[str] = set()
    for write_uri in machine_write_uris(wrapper_text):
        write_norm = normalize_cortex_uri(write_uri)
        if write_norm is None:
            continue
        matched_guard = any(
            guard_matches_write(guard, write_norm) for guard in guard_uris
        )
        if not matched_guard:
            continue
        if _write_announced_in_body(body, write_norm):
            continue
        if write_norm not in seen:
            seen.add(write_norm)
            violations.append(write_norm)
    return violations


def apply_write_fence(
    payload: CloseoutRelayPayload,
    *,
    wrapper_text: str | None,
    guard_uris: frozenset[str],
) -> CloseoutRelayPayload:
    """Fail-closed overlay when guarded corpus paths were written without announce."""
    violations = guarded_write_violations(
        wrapper_text=wrapper_text,
        guard_uris=guard_uris,
        body=payload.body,
    )
    if not violations:
        return payload
    violation_list = ", ".join(violations)
    body = payload.body.rstrip()
    if "fence_violation:" not in body.lower():
        body = (
            f"{body}\n\nfence_violation: true\n"
            f"guarded_writes_without_announce: {violation_list}\n"
        )
    status = "blocked" if payload.status != "partial" else "partial"
    if payload.status == "complete":
        status = "blocked"
    return CloseoutRelayPayload(
        body=body,
        status=status,
        source=payload.source,
        body_full=payload.body_full,
        clamped=payload.clamped,
        relay_note=payload.relay_note,
        deployment_state=payload.deployment_state,
    )


__all__ = [
    "apply_write_fence",
    "extract_fence_exception_lines",
    "guard_matches_write",
    "guarded_write_violations",
]
