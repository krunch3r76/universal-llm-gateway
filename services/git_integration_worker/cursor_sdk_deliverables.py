"""Cortex-pinned deliverable resolution + repo closeout sidecar helpers (cursor-sdk)."""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from pathlib import Path
from typing import Any

from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_WORKSPACES_REPO = "universal-llm-gateway"
_SIDECAR_DIR = "tmp/reviews/closeouts"

PinnedWriteResult = dict[str, Any]
PinnedWriteFn = Callable[..., Coroutine[Any, Any, PinnedWriteResult | None]]


def _sidecar_rel_path(dispatch_id: str) -> str:
    return f"{_SIDECAR_DIR}/{dispatch_id}.md"


def sidecar_workspaces_ref(dispatch_id: str) -> str:
    return f"workspaces://{_WORKSPACES_REPO}/{_sidecar_rel_path(dispatch_id)}"


def full_result_text(body: str, degraded_reason: str | None) -> str:
    if degraded_reason:
        return f"status: degraded\nreason: {degraded_reason}\n\n{body}"
    return body


def write_repo_sidecar(source_repo: Path, dispatch_id: str, content: str) -> Path:
    path = source_repo / _sidecar_rel_path(dispatch_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def normalize_cortex_rel(raw: str) -> str | None:
    path = raw.strip()
    for prefix in ("cortex://", "cortex:"):
        if path.lower().startswith(prefix):
            path = path[len(prefix) :]
            break
    path = path.lstrip("/")
    if not path or ".." in Path(path).parts:
        return None
    return path


def cortex_expected_rels(files_expected: list[str]) -> list[str]:
    seen: set[str] = set()
    rels: list[str] = []
    for raw in files_expected:
        rel = normalize_cortex_rel(raw)
        if rel and rel not in seen:
            seen.add(rel)
            rels.append(rel)
    return rels


def artifact_paths_for_closeout(
    sidecar_ref: str,
    cortex_uris: list[str],
) -> list[str]:
    paths = [sidecar_ref]
    seen = {sidecar_ref}
    for uri in cortex_uris:
        if uri not in seen:
            paths.append(uri)
            seen.add(uri)
    return paths


async def default_post_pinned_deliverable(
    *,
    rel_path: str,
    content: str,
    write_if_absent: bool,
    dispatch_id: str,
    thread_id: str,
) -> PinnedWriteResult | None:
    payload = {
        "rel_path": rel_path,
        "content": content,
        "write_if_absent": write_if_absent,
        "dispatch_id": dispatch_id,
        "thread_id": thread_id,
    }
    try:
        async with make_async_client(DEFAULT_STARGATE_URL, timeout=15.0) as client:
            resp = await client.post(
                "/api/v1/implement/pinned-deliverable", json=payload
            )
        if resp.status_code >= 400:
            logger.warning(
                "pinned deliverable ingress rejected: rel=%s status=%s body=%s",
                rel_path,
                resp.status_code,
                resp.text[:300],
            )
            return None
        return resp.json()
    except Exception as exc:
        logger.warning(
            "pinned deliverable ingress transport error: rel=%s err=%s",
            rel_path,
            exc,
        )
        return None


async def resolve_cortex_pinned_deliverables(
    *,
    files_expected: list[str],
    full_text: str,
    source_repo: Path,
    dispatch_id: str,
    thread_id: str,
    post_pinned: PinnedWriteFn | None = None,
) -> tuple[list[str], tuple[str, ...]]:
    """Ensure pinned cortex deliverables exist; return (uris, gate_d_created_rels)."""
    writer = post_pinned or default_post_pinned_deliverable
    uris: list[str] = []
    satisfied: list[str] = []
    for rel in cortex_expected_rels(files_expected):
        # A malformed rel (e.g. a multi-KB blob the packet parser over-captured)
        # must degrade gracefully, not crash the closeout envelope: Path.is_file()
        # surfaces OSError ENAMETOOLONG (errno 36) rather than returning False.
        try:
            repo_path = source_repo / rel
            is_file = repo_path.is_file()
        except OSError as exc:
            logger.warning(
                "skipping malformed deliverable rel (len=%d): %s", len(rel), exc
            )
            continue
        if is_file:
            content = repo_path.read_text(encoding="utf-8")
            result = await writer(
                rel_path=rel,
                content=content,
                write_if_absent=False,
                dispatch_id=dispatch_id,
                thread_id=thread_id,
            )
        else:
            result = await writer(
                rel_path=rel,
                content=full_text,
                write_if_absent=True,
                dispatch_id=dispatch_id,
                thread_id=thread_id,
            )
        if not result or "error" in result:
            continue
        uri = result.get("uri")
        if isinstance(uri, str) and uri.startswith("cortex://"):
            uris.append(uri)
            satisfied.append(rel)
    return uris, tuple(satisfied)
