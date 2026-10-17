"""Closeout artifact URI assembly moved out of cursor_sdk_deliverables.

Pure move so the deliverable module stays at or below 400 lines.
"""

from __future__ import annotations

import hashlib
from typing import Any


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
