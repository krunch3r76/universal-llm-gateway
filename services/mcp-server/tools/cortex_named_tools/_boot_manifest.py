"""Manifest of artifacts injected into agent context at cortex_boot.

The manifest is the canonical answer to 'what bytes reach the agent at
boot?' Each InjectedArtifact carries mode, source, bytes, sha256, and
per-fetch provenance.

sha256 hashes raw bytes as actually produced (no canonicalization). This
keeps the manifest a faithful audit record. Phase 4's diff layer is
responsible for canonicalizing inline content (stripping known
boot-timestamp patterns) before comparison — see phase4.md.

Concurrency: FetchRecorder.records is appended to from
ThreadPoolExecutor workers in _boot_runner.py. Append is guarded by an
explicit threading.Lock rather than relying on CPython GIL atomicity of
list.append (forward-compat with free-threaded Python / PEP 703).
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Literal

from mcp_events import record

InjectionMode = Literal["inline", "written_file", "manifest_only", "auto_postfile"]

# Sentinel returned by _byte_count when JSON serialization fails. Distinct
# from 0 (which legitimately means "empty result").
BYTES_UNAVAILABLE = -1


@dataclass
class FetchRecord:
    """Provenance for a single fetch made during boot.

    `tool` carries the canonical provenance string (e.g.
    "cortex GET /assertions?entity_id=...") — full request path including
    query string. This is the audit-grade identifier; it is what a reader
    diffs across boots.

    `params` is reserved for structured provenance (e.g. parsed query
    parameters keyed by name). Currently always empty — the recorder
    treats `tool` as the canonical record. If future audit needs require
    structured filtering, populate `params` from the query string at
    record time. Empty dict is the contract today, not a placeholder.
    """

    tool: str
    params: dict[str, Any]
    rows: int
    bytes: int
    duration_ms: int


@dataclass
class InjectedArtifact:
    name: str
    mode: InjectionMode
    source: str
    bytes: int
    sha256: str
    path: str | None = None
    fetches: list[FetchRecord] = field(default_factory=list)
    sections: list[dict[str, Any]] | None = None

    @classmethod
    def from_text(
        cls,
        name: str,
        mode: InjectionMode,
        source: str,
        text: str,
        path: str | None = None,
        fetches: list[FetchRecord] | None = None,
        sections: list[dict[str, Any]] | None = None,
    ) -> InjectedArtifact:
        encoded = text.encode("utf-8")
        return cls(
            name=name,
            mode=mode,
            source=source,
            bytes=len(encoded),
            sha256=hashlib.sha256(encoded).hexdigest(),
            path=path,
            fetches=fetches or [],
            sections=sections,
        )


class FetchRecorder:
    """Wraps a fetch callable to capture FetchRecord on each invocation.

    Thread-safe: records.append is guarded by an internal lock so concurrent
    workers from ThreadPoolExecutor cannot race. (Lock cost is negligible —
    a handful of fetches per boot.)

    Usage:
        rec = FetchRecorder()
        wrapped_cx = rec.wrap("cortex", _cx)
        result = wrapped_cx("GET", "/assertions?...")
        # rec.records is now populated
    """

    def __init__(self) -> None:
        self.records: list[FetchRecord] = []
        self._lock = threading.Lock()

    def wrap(self, sandbox_label: str, fn: Any) -> Any:
        def proxied(*args: Any, **kwargs: Any) -> Any:
            # Relay calls: (sandbox, method, path) — args[1]=method, args[2]=path
            # Cortex calls: (method, path)          — args[0]=method, args[1]=path
            # RAG/other:   (url,)                   — no method/path fields
            if len(args) >= 3:
                method = args[1]
                path = args[2]
            elif len(args) == 2:
                method = args[0]
                path = args[1]
            else:
                method = kwargs.get("method", "?")
                path = kwargs.get("path", "?")
            t0 = time.monotonic()
            result = fn(*args, **kwargs)
            duration_ms = int((time.monotonic() - t0) * 1000)
            rec = FetchRecord(
                tool=f"{sandbox_label} {method} {path}",
                params={},  # query string already in path; could parse if useful
                rows=_row_count(result),
                bytes=_byte_count(result),
                duration_ms=duration_ms,
            )
            with self._lock:
                self.records.append(rec)
            return result

        return proxied


def _row_count(result: Any) -> int:
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        for key in ("items", "turns", "threads", "results"):
            v = result.get(key)
            if isinstance(v, list):
                return len(v)
        return 1
    return 0


def _byte_count(result: Any) -> int:
    """Serialize result and return its byte size.

    Returns BYTES_UNAVAILABLE (-1) on serialization failure; emits a
    `mcp.cortex.boot.fetch.failed` event so the failure is observable
    rather than silently reported as bytes=0 (which would conflate with
    "empty result" and quietly corrupt the manifest's audit guarantee).
    """
    try:
        return len(json.dumps(result, default=str).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        record(
            "mcp.cortex.boot.fetch.failed",
            error=str(exc),
            error_type=type(exc).__name__,
        )
        return BYTES_UNAVAILABLE


def serialize_manifest(artifacts: list[InjectedArtifact]) -> list[dict[str, Any]]:
    return [asdict(a) for a in artifacts]
