"""``source_ref_provenance`` attribute block construction."""

from __future__ import annotations

from typing import Any


def build_source_ref_provenance(
    *,
    external_ref: str,
    canonical_ref: str | None,
    source_kind: str | None,
    parent_ref: str | None,
    selector: str | None,
    derivation: str | None,
    captured_at: str,
    unparseable: bool = False,
) -> dict[str, Any]:
    """Build the ``source_ref_provenance`` block stamped on the transcript attribute."""
    prov: dict[str, Any] = {
        "external_ref": external_ref,
        "canonical_ref": canonical_ref,
        "captured_at": captured_at,
    }
    if source_kind is not None:
        prov["source_kind"] = source_kind
    if parent_ref is not None:
        prov["parent_ref"] = parent_ref
    if selector is not None:
        prov["selector"] = selector
    if derivation is not None:
        prov["derivation"] = derivation
    if unparseable:
        prov["source_ref_unparseable"] = True
    return prov
