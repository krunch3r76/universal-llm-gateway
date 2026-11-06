"""Transcript entity resolution for boot continuation."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote, urlencode

from cortex_store.handoff_surface import build_handoff_surface

from .._cortex_relay import cx
from .._file_helpers import read_file_result


def resolve_transcript(
    transcript_id: str,
) -> dict[str, Any] | None:
    """Verify transcript entity exists, load markdown, traverse continues chain.

    Returns a continuation dict on success, or a dict with 'error' key on failure.
    None if transcript_id is empty.
    """
    if not transcript_id:
        return None

    clean_id = transcript_id.removeprefix("transcript:")
    entity_key = f"transcript:{clean_id}"

    entity_raw = cx("GET", f"/entities/{quote(entity_key, safe=':')}?intent=full")
    if "error" in entity_raw:
        return {
            "error": "transcript_not_found",
            "transcript_id": clean_id,
            "transcript_entity_id": entity_key,
            "detail": (
                f"Entity {entity_key} not found in Cortex. "
                "If the session that supplied this transcript_id is still active, "
                "the entity will not be committed until that session closes — "
                "this is expected, not an error. "
                "Boot will proceed without continuation context."
            ),
        }

    source_uri = entity_raw.get("source_uri") or ""
    composed_md = ""
    if source_uri:
        rel = source_uri.lstrip("/")
        read_out = read_file_result(rel, export=True)
        if read_out.get("code") == "sparse_manifest_requires_expansion":
            return {
                "error": "transcript_source_sparse_manifest",
                "transcript_id": clean_id,
                "transcript_entity_id": entity_key,
                "detail": (
                    "Transcript source_uri read returned manifest without body; "
                    "export=true failed or source is not expandable."
                ),
                "read_sha256": read_out.get("read_sha256"),
            }
        if "error" in read_out or read_out.get("code"):
            return {
                "error": "transcript_source_read_failed",
                "transcript_id": clean_id,
                "transcript_entity_id": entity_key,
                "detail": str(read_out.get("error") or read_out.get("message")),
            }
        body = read_out.get("content")
        if not isinstance(body, str) or not body.strip():
            return {
                "error": "transcript_source_empty",
                "transcript_id": clean_id,
                "transcript_entity_id": entity_key,
                "detail": "Transcript markdown body empty after explicit export read.",
            }
        composed_md = body

    chain_qs = urlencode({"node": entity_key, "edge_type": "continues", "hops": 5})
    chain_raw = cx("GET", f"/edges/traverse?{chain_qs}")
    chain_edges: list[dict[str, Any]] = []
    if isinstance(chain_raw, dict):
        chain_edges = chain_raw.get("items", [])

    attrs = entity_raw.get("attributes")
    handoff_surface = build_handoff_surface(attrs) if isinstance(attrs, dict) else None
    handoff_prompt = attrs.get("handoff_prompt") if isinstance(attrs, dict) else None

    return {
        "transcript_id": clean_id,
        "entity_id": entity_key,
        "name": entity_raw.get("name", clean_id),
        "description": entity_raw.get("description", ""),
        "source_uri": source_uri,
        "markdown": composed_md,
        "assertions": entity_raw.get("assertions", []),
        "chain": chain_edges,
        "handoff_prompt": handoff_prompt,
        "handoff_surface": handoff_surface,
    }
