"""Cortex-optional durable persistence for RAG recon and session-close sidecars."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import yaml
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client
from universal_logging import get_logger

logger = get_logger(__name__)

_STARGATE_CONFIG_PATH = Path.home() / ".gateway" / "stargate.yaml"
_CORTEX_PROBE_TIMEOUT = 2.0
DispatchFn = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass(frozen=True)
class SinkResult:
    uri: str
    sha256: str
    location: str


@dataclass(frozen=True)
class SinkSelectionMetadata:
    selected_backend: str
    selection_reason: str
    cortex_probe_status: str
    fallback_used: bool


@dataclass(frozen=True)
class ResolvedDurableSink:
    sink: DurableSink
    metadata: SinkSelectionMetadata


@runtime_checkable
class DurableSink(Protocol):
    def write_recon_sidecar(
        self,
        label: str,
        theme: str,
        body: str,
        *,
        scopes: list[str] | None = None,
        queries: list[str] | None = None,
        sink_backend: str | None = None,
    ) -> SinkResult | None: ...


def _load_stargate_config() -> dict[str, Any]:
    if not _STARGATE_CONFIG_PATH.exists():
        return {}
    try:
        data = yaml.safe_load(_STARGATE_CONFIG_PATH.read_text()) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _configured_backend() -> str:
    env = os.environ.get("DURABLE_SINK", "").strip().lower()
    if env in {"cortex", "filesystem", "null", "auto"}:
        return env
    data = _load_stargate_config()
    raw = data.get("durable_sink")
    if isinstance(raw, str) and raw.strip().lower() in {
        "cortex",
        "filesystem",
        "null",
        "auto",
    }:
        return raw.strip().lower()
    return "auto"


def _filesystem_root() -> Path | None:
    env = os.environ.get("DURABLE_SINK_FS_ROOT", "").strip()
    if env:
        return Path(env).expanduser()
    data = _load_stargate_config()
    section = data.get("durable_sink")
    if isinstance(section, dict):
        root = section.get("filesystem_root")
        if isinstance(root, str) and root.strip():
            return Path(root).expanduser()
    return None


def probe_cortex() -> str:
    try:
        with make_sync_client(DEFAULT_CORTEX_URL, timeout=_CORTEX_PROBE_TIMEOUT) as client:
            response = client.get("/health")
            if response.status_code == 200:
                return "ok"
    except Exception as exc:  # noqa: BLE001 — probe boundary
        logger.debug("cortex probe failed: %s", exc)
    return "unreachable"


def _default_dispatch(tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    from cortex_store.dispatch_ops import execute_op

    return execute_op(tool, arguments)


class CortexSink:
    def __init__(self, dispatch_fn: DispatchFn | None = None) -> None:
        self._dispatch = dispatch_fn or _default_dispatch

    def write_recon_sidecar(
        self,
        label: str,
        theme: str,
        body: str,
        *,
        scopes: list[str] | None = None,
        queries: list[str] | None = None,
        sink_backend: str | None = None,
    ) -> SinkResult | None:
        result = self._dispatch(
            "recon_sidecar_write",
            {
                "label": label,
                "theme": theme,
                "body": body,
                "scopes": scopes,
                "queries": queries,
                "sink_backend": sink_backend or "cortex",
            },
        )
        if not isinstance(result, dict) or result.get("error"):
            raise RuntimeError(result.get("error", "recon_sidecar_write failed"))
        return SinkResult(
            uri=str(result["uri"]),
            sha256=str(result["sha256"]),
            location="cortex",
        )


class FilesystemSink:
    def __init__(self, root: Path) -> None:
        self._root = root.expanduser().resolve()

    def write_recon_sidecar(
        self,
        label: str,
        theme: str,
        body: str,
        *,
        scopes: list[str] | None = None,
        queries: list[str] | None = None,
        sink_backend: str | None = None,
    ) -> SinkResult | None:
        from cortex_store.dispatch_ops._recon_sidecar import (
            content_sha256,
            render_recon_sidecar_markdown,
            resolve_recon_target,
        )

        resolved = resolve_recon_target(label, theme)
        if resolved is None:
            raise ValueError("unsafe recon sidecar path")
        label_slug, theme_slug, _target = resolved
        backend = sink_backend or "filesystem"
        digest = content_sha256(body)
        rendered = render_recon_sidecar_markdown(
            label=label,
            theme=theme,
            body=body,
            scopes=scopes,
            queries=queries,
            sink_backend=backend,
            sha256=digest,
        )
        rel = Path("recon") / label_slug / f"{theme_slug}.md"
        target = (self._root / rel).resolve()
        try:
            target.relative_to(self._root)
        except ValueError as exc:
            raise ValueError("filesystem recon path escapes configured root") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(rendered, encoding="utf-8")
        return SinkResult(
            uri=f"file://{target}",
            sha256=content_sha256(body),
            location="filesystem",
        )


class NullSink:
    def write_recon_sidecar(
        self,
        label: str,
        theme: str,
        body: str,
        *,
        scopes: list[str] | None = None,
        queries: list[str] | None = None,
        sink_backend: str | None = None,
    ) -> SinkResult | None:
        return None


def resolve_durable_sink(
    *,
    backend_override: str | None = None,
    dispatch_fn: DispatchFn | None = None,
    probe_fn: Callable[[], str] | None = None,
) -> ResolvedDurableSink:
    """Return sink + selection metadata; sole cortex-availability branch."""
    configured = (backend_override or _configured_backend()).strip().lower()
    probe = probe_fn or probe_cortex
    cortex_status = "not_probed" if configured == "null" else probe()
    fs_root = _filesystem_root()

    if configured == "cortex":
        if cortex_status != "ok":
            raise RuntimeError(
                "durable_sink=cortex but cortex is unreachable; refusing silent NullSink"
            )
        return ResolvedDurableSink(
            sink=CortexSink(dispatch_fn),
            metadata=SinkSelectionMetadata(
                selected_backend="cortex",
                selection_reason="explicit_config",
                cortex_probe_status=cortex_status,
                fallback_used=False,
            ),
        )

    if configured == "filesystem":
        if fs_root is None:
            raise RuntimeError("durable_sink=filesystem but no filesystem root configured")
        return ResolvedDurableSink(
            sink=FilesystemSink(fs_root),
            metadata=SinkSelectionMetadata(
                selected_backend="filesystem",
                selection_reason="explicit_config",
                cortex_probe_status=cortex_status,
                fallback_used=False,
            ),
        )

    if configured == "null":
        return ResolvedDurableSink(
            sink=NullSink(),
            metadata=SinkSelectionMetadata(
                selected_backend="null",
                selection_reason="explicit_config",
                cortex_probe_status="not_probed",
                fallback_used=False,
            ),
        )

    # auto
    if cortex_status == "ok":
        return ResolvedDurableSink(
            sink=CortexSink(dispatch_fn),
            metadata=SinkSelectionMetadata(
                selected_backend="cortex",
                selection_reason="auto_probe_ok",
                cortex_probe_status=cortex_status,
                fallback_used=False,
            ),
        )
    if fs_root is not None:
        return ResolvedDurableSink(
            sink=FilesystemSink(fs_root),
            metadata=SinkSelectionMetadata(
                selected_backend="filesystem",
                selection_reason="auto_probe_failed_fallback",
                cortex_probe_status=cortex_status,
                fallback_used=True,
            ),
        )
    return ResolvedDurableSink(
        sink=NullSink(),
        metadata=SinkSelectionMetadata(
            selected_backend="null",
            selection_reason="auto_probe_failed_fallback",
            cortex_probe_status=cortex_status,
            fallback_used=True,
        ),
    )


def write_session_rag_query_sidecar(
    session_id: str,
    label: str,
    theme: str,
    body: str,
    *,
    scopes: list[str] | None = None,
    queries: list[str] | None = None,
    dispatch_fn: DispatchFn | None = None,
    backend_override: str | None = None,
) -> dict[str, Any]:
    """Session-close RAG-query appendix consumer via DurableSink."""
    resolved = resolve_durable_sink(
        backend_override=backend_override,
        dispatch_fn=dispatch_fn,
    )
    write_label = label or session_id
    try:
        result = resolved.sink.write_recon_sidecar(
            write_label,
            theme,
            body,
            scopes=scopes,
            queries=queries,
            sink_backend=resolved.metadata.selected_backend,
        )
    except Exception as exc:  # noqa: BLE001 — surface to session-close caller
        return {
            "error": str(exc),
            "selected_backend": resolved.metadata.selected_backend,
            "fallback_used": resolved.metadata.fallback_used,
        }
    payload: dict[str, Any] = {
        "selected_backend": resolved.metadata.selected_backend,
        "selection_reason": resolved.metadata.selection_reason,
        "cortex_probe_status": resolved.metadata.cortex_probe_status,
        "fallback_used": resolved.metadata.fallback_used,
    }
    if result is not None:
        payload["uri"] = result.uri
        payload["sha256"] = result.sha256
        payload["location"] = result.location
    return payload


__all__ = [
    "CortexSink",
    "DurableSink",
    "FilesystemSink",
    "NullSink",
    "ResolvedDurableSink",
    "SinkResult",
    "SinkSelectionMetadata",
    "probe_cortex",
    "resolve_durable_sink",
    "write_session_rag_query_sidecar",
]
