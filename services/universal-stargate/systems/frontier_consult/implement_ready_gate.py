"""Stargate dispatch adapter for todo-sourced implement-readiness admission."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from implement_admission.implement_ready import evaluate_implement_ready
from implement_admission.source_ref import parse_source_ref
from implement_admission.spec import SourceKind

from .admission import FrontierEndpointError
from .handoff import _resolve_packet_file, _workspaces_root
from .implement_admission_bridge import StargateCortexReader, _repo_base

_DENSE_SPEC_RE = re.compile(r"tasks/specs/[^/\s#?]+\.md", re.IGNORECASE)


def _decode_attributes(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _coerce_assertion_id(raw: Any) -> int | None:
    if raw is None:
        return None
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str) and raw.strip().isdigit():
        return int(raw.strip())
    return None


def _spec_basename(uri: str) -> str | None:
    match = _DENSE_SPEC_RE.search(uri)
    if not match:
        return None
    return match.group(0).split("/")[-1]


def _select_cited_dense_spec_uri(
    evidence_uris: list[str] | None,
    *,
    source_uri: str | None,
) -> str | None:
    if not evidence_uris:
        return None
    cited = [u for u in evidence_uris if _DENSE_SPEC_RE.search(u)]
    if not cited:
        return None
    source_base = _spec_basename(source_uri or "")
    if source_base is None:
        return cited[0]
    for uri in cited:
        if _spec_basename(uri) == source_base:
            return uri
    return None


def _read_dense_spec_text(
    cited_uri: str,
    *,
    workspaces_root: Path | None = None,
) -> str | None:
    root = (workspaces_root or _workspaces_root()).resolve()
    uri = cited_uri.strip()
    for prefix in ("workspaces://", "cortex://", "ws://"):
        if uri.lower().startswith(prefix):
            uri = uri[len(prefix) :]
    uri = uri.lstrip("/")
    candidate = _resolve_packet_file(root, uri)
    if candidate is None:
        candidate = _resolve_packet_file(_repo_base(root), uri)
    if candidate is None:
        return None
    try:
        return candidate.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None


def require_implement_ready(
    *,
    request_id: str,
    source_ref: str | None,
    cortex: StargateCortexReader,
) -> None:
    """Hard gate for todo-sourced implement dispatch. No-op for non-todo sources."""
    if source_ref is None:
        return

    ref = parse_source_ref(source_ref)
    if ref.source_kind != SourceKind.TODO.value:
        return

    entity = cortex.entity_get(ref.canonical_ref)
    attrs = _decode_attributes(entity.get("attributes"))
    aid = _coerce_assertion_id(attrs.get("implement_ready_assertion_id"))
    assertion: dict[str, Any] | None = None
    if aid is not None:
        loaded = cortex.assertion_get(aid)
        if isinstance(loaded, dict) and "error" not in loaded:
            assertion = loaded

    evidence = assertion.get("evidence_uris") if assertion else None
    cited_uri: str | None = None
    dense_spec_text: str | None = None
    if isinstance(evidence, list):
        cited_uri = _select_cited_dense_spec_uri(
            evidence, source_uri=entity.get("source_uri")
        )
        if cited_uri is not None:
            dense_spec_text = _read_dense_spec_text(cited_uri)

    verdict = evaluate_implement_ready(
        todo_id=ref.canonical_ref,
        density_triage=attrs.get("density_triage"),
        source_uri=entity.get("source_uri"),
        implement_ready_assertion_id=aid,
        assertion=assertion,
        now_iso=datetime.now(UTC).isoformat(),
        dense_spec_uri=cited_uri,
        dense_spec_text=dense_spec_text,
    )
    if not verdict.admitted:
        raise FrontierEndpointError(
            request_id=request_id,
            field="source_ref",
            reason=verdict.reason or verdict.code or "implement_not_ready",
            status_code=422,
            code=verdict.code or "implement_not_ready",
        )


__all__ = ["require_implement_ready"]
