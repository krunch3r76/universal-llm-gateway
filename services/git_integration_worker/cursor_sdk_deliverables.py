"""Cortex-pinned deliverable resolution + repo closeout sidecar helpers (cursor-sdk)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from implement_admission.closeout_helpers import cortex_files_root
from transport_utils import DEFAULT_STARGATE_URL, make_async_client
from universal_logging import get_logger

logger = get_logger(__name__)

_WORKSPACES_REPO = "universal-llm-gateway"
_SIDECAR_DIR = "tmp/reviews/closeouts"
# Admitted by cortex pinned_deliverable sandbox (notes/ file-root dir).
_CLOSEOUT_CORTEX_REL_DIR = "notes/system/threads"

PinnedWriteResult = dict[str, Any]
PinnedWriteFn = Callable[..., Coroutine[Any, Any, PinnedWriteResult | None]]


@dataclass(frozen=True)
class PinnedResolution:
    uris: list[str]
    satisfied_rels: tuple[str, ...]
    divergent_rels: tuple[str, ...]


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


def _has_cortex_scheme(raw: str) -> bool:
    s = raw.strip().lower()
    return s.startswith("cortex://") or s.startswith("cortex:")


def cortex_expected_rels(files_expected: list[str]) -> list[str]:
    seen: set[str] = set()
    rels: list[str] = []
    for raw in files_expected:
        if not _has_cortex_scheme(raw):
            continue
        rel = normalize_cortex_rel(raw)
        if rel and rel not in seen:
            seen.add(rel)
            rels.append(rel)
    return rels


def artifact_paths_for_closeout(
    sidecar_ref: str,
    cortex_uris: list[str],
    *,
    cortex_first: bool = False,
    offgit_deliverable_uris: list[str] | None = None,
) -> list[str]:
    if cortex_first and cortex_uris:
        paths: list[str] = []
        seen: set[str] = set()
        for uri in cortex_uris:
            if uri == sidecar_ref:
                continue
            if uri not in seen:
                paths.append(uri)
                seen.add(uri)
        if sidecar_ref not in seen:
            paths.append(sidecar_ref)
        for uri in offgit_deliverable_uris or []:
            if uri not in seen:
                paths.append(uri)
                seen.add(uri)
        return paths
    paths = [sidecar_ref]
    seen = {sidecar_ref}
    for uri in cortex_uris:
        if uri not in seen:
            paths.append(uri)
            seen.add(uri)
    for uri in offgit_deliverable_uris or []:
        if uri not in seen:
            paths.append(uri)
            seen.add(uri)
    return paths


def closeout_cortex_sidecar_rel_path(thread_id: str, dispatch_id: str) -> str:
    return (
        f"{_CLOSEOUT_CORTEX_REL_DIR}/{thread_id}-cursor-sdk-closeout-{dispatch_id}.md"
    )


def body_relocated_meta(
    full_body: str,
    uri: str,
    *,
    sha256: str | None = None,
) -> dict[str, Any]:
    return {
        "uri": uri,
        "sha256": sha256 or hashlib.sha256(full_body.encode("utf-8")).hexdigest(),
        "body_chars": len(full_body),
    }


def append_structured_closeout_full_to_repo_sidecar(
    sidecar_path: Path,
    full_body: str,
) -> None:
    existing = sidecar_path.read_text(encoding="utf-8")
    sidecar_path.write_text(
        existing + "\n\n## structured_closeout_full\n\n" + full_body,
        encoding="utf-8",
    )


async def post_closeout_sidecar(
    *,
    full_body: str,
    dispatch_id: str,
    thread_id: str,
    post_pinned: PinnedWriteFn | None = None,
) -> PinnedWriteResult | None:
    writer = post_pinned or default_post_pinned_deliverable
    return await writer(
        rel_path=closeout_cortex_sidecar_rel_path(thread_id, dispatch_id),
        content=full_body,
        write_if_absent=True,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
    )


def pretty_relocated_closeout_body(full_body: str) -> str:
    """Multi-line JSON for relocated sidecars — readable under fs-read line limits."""
    return json.dumps(json.loads(full_body), indent=2)


async def relocate_oversize_closeout_body_async(
    *,
    full_body: str,
    sidecar_path: Path,
    sidecar_ref: str,
    dispatch_id: str,
    thread_id: str,
    post_closeout_sidecar_fn: PinnedWriteFn | None = None,
) -> tuple[dict[str, Any], str]:
    pretty_body = pretty_relocated_closeout_body(full_body)
    result = await post_closeout_sidecar(
        full_body=pretty_body,
        dispatch_id=dispatch_id,
        thread_id=thread_id,
        post_pinned=post_closeout_sidecar_fn,
    )
    if result and "error" not in result and isinstance(result.get("uri"), str):
        return (
            body_relocated_meta(
                pretty_body,
                result["uri"],
                sha256=result.get("sha256")
                if isinstance(result.get("sha256"), str)
                else None,
            ),
            "cortex",
        )
    append_structured_closeout_full_to_repo_sidecar(sidecar_path, pretty_body)
    return body_relocated_meta(pretty_body, sidecar_ref), "repo_sidecar"


def relocate_oversize_closeout_body_sync(
    *,
    full_body: str,
    sidecar_path: Path,
    sidecar_ref: str,
    dispatch_id: str,
    thread_id: str,
    post_closeout_sidecar_fn: Callable[..., PinnedWriteResult | None] | None = None,
) -> tuple[dict[str, Any], str]:
    pretty_body = pretty_relocated_closeout_body(full_body)
    if post_closeout_sidecar_fn is not None:
        result = post_closeout_sidecar_fn(
            full_body=pretty_body,
            dispatch_id=dispatch_id,
            thread_id=thread_id,
        )
        if result and "error" not in result and isinstance(result.get("uri"), str):
            return (
                body_relocated_meta(
                    pretty_body,
                    result["uri"],
                    sha256=result.get("sha256")
                    if isinstance(result.get("sha256"), str)
                    else None,
                ),
                "cortex",
            )
    append_structured_closeout_full_to_repo_sidecar(sidecar_path, pretty_body)
    return body_relocated_meta(pretty_body, sidecar_ref), "repo_sidecar"


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
    cortex_root: Path | None = None,
) -> PinnedResolution:
    """Ensure pinned cortex deliverables exist; return resolution metadata."""
    writer = post_pinned or default_post_pinned_deliverable
    root = cortex_root or cortex_files_root()
    uris: list[str] = []
    satisfied: list[str] = []
    divergent: list[str] = []
    for rel in cortex_expected_rels(files_expected):
        try:
            cortex_path = root / rel
            repo_path = source_repo / rel
            if cortex_path.is_dir() or repo_path.is_dir():
                divergent.append(f"pinned_deliverable_invalid_target:{rel}")
                continue
            cortex_is_file = cortex_path.is_file()
            repo_is_file = repo_path.is_file()
        except OSError as exc:
            logger.warning(
                "skipping malformed deliverable rel (len=%d): %s", len(rel), exc
            )
            continue
        if cortex_is_file:
            content = cortex_path.read_text(encoding="utf-8")
            result = await writer(
                rel_path=rel,
                content=content,
                write_if_absent=False,
                dispatch_id=dispatch_id,
                thread_id=thread_id,
            )
        elif repo_is_file:
            content = repo_path.read_text(encoding="utf-8")
            divergent.append(f"pinned_deliverable_wrong_sandbox:{rel}")
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
            divergent.append(f"pinned_deliverable_write_failed:{rel}")
            continue
        uri = result.get("uri")
        if isinstance(uri, str) and uri.startswith("cortex://"):
            uris.append(uri)
            if f"pinned_deliverable_wrong_sandbox:{rel}" not in divergent:
                satisfied.append(rel)
    return PinnedResolution(
        uris=uris,
        satisfied_rels=tuple(satisfied),
        divergent_rels=tuple(divergent),
    )
