"""Seal the ImplementCloseout publication surface (slice 2).

Installs ``seal()`` on the dumped closeout payload so an undeclared bare
scalar cannot publish. Called from ``_render_body`` after extras are attached.
SLOC lives here so ``cursor_sdk_closeout.py`` does not grow.
"""

from __future__ import annotations

from typing import Any

from admission_common.qualified_scalar import (
    PUBLICATION_BUILDER_CENSUS,
    SurfaceDecl,
    UnqualifiedScalarError,
    seal,
)
from universal_event_bus import Event, event_factory

from services.git_integration_worker.cursor_sdk_events import emit_frontier_event

_USAGE_SCOPE = "this dispatch token accounting"
_USAGE_AUTHORITY = "recorded"
_USAGE_TOKEN_KEYS = (
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "cache_read_tokens",
    "cache_write_tokens",
    "reasoning_tokens",
)

# Intentionally-plain bare scalars. Count is the slice-2 halt (~15). Usage
# token counts are qualified at seal-prep (they have real scope/authority)
# and do not consume this list.
_PLAIN: tuple[tuple[str, str], ...] = (
    ("schema_version", "envelope/manifest version pin, not a measurement"),
    ("public_api_changed", "boolean flag; no scoped count"),
    ("commits_ahead", "lane-B gauge; plane classifier reads presence"),
    ("commits_ahead_unfiltered", "lane-B gauge sibling"),
    ("isolation_materialized", "lane isolation bit"),
    ("landed", "land plane bit"),
    ("output_truncated", "stream cap flag on Verification"),
    ("tool_call_count", "dispatch tool-call tally attached after model_dump"),
    (
        "exit_code",
        "qualified by sibling exit_code_register (run-plane absence law)",
    ),
    (
        "wrapper_exit_code",
        "audit-only integer; no reader grades it",
    ),
    ("expected_x_mcp_count", "PropagationRow served-artifact bound"),
    ("mint_turn", "PropagationRow bus turn stamp"),
    ("force", "PropagationRow restart-drain narrowing"),
    ("allow_self_preempt", "PropagationRow restart-drain default"),
)


def closeout_surface_decl() -> SurfaceDecl:
    """Build the closeout SurfaceDecl. Raises if the plain census exceeds 15."""
    if len(_PLAIN) > 15:
        raise RuntimeError(
            f"closeout plain census is {len(_PLAIN)}; slice 2 halt is ~15"
        )
    decl = SurfaceDecl(surface="ImplementCloseout.model_dump")
    for name, reason in _PLAIN:
        decl.plain(name, reason=reason)
    return decl


def _qualify_bare_ints(
    bag: dict[str, Any], *, scope: str, authority: str
) -> None:
    """Attach qualifier siblings on int/float leaves without growing the plain list."""
    for key, value in list(bag.items()):
        if key.endswith("_scope") or key.endswith("_authority"):
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        if f"{key}_scope" in bag and f"{key}_authority" in bag:
            continue
        bag[f"{key}_scope"] = scope
        bag[f"{key}_authority"] = authority


def _qualify_usage_tokens(payload: dict[str, Any]) -> None:
    """Attach QualifiedScalar siblings on usage token counts (not plain)."""
    usage = payload.get("usage")
    if isinstance(usage, dict):
        _qualify_bare_ints(usage, scope=_USAGE_SCOPE, authority=_USAGE_AUTHORITY)


def _qualify_surface_counts(payload: dict[str, Any]) -> None:
    """Qualify dynamic surface_counts keys — the map is unbounded, so not plain."""
    manifest = payload.get("effects_manifest")
    if not isinstance(manifest, dict):
        return
    counts = manifest.get("surface_counts")
    if isinstance(counts, dict):
        _qualify_bare_ints(
            counts,
            scope="this closeout effects_manifest surface entry counts",
            authority="derived",
        )


@event_factory
def CloseoutSealRefused(path: str, surface: str) -> Event:  # noqa: N802
    return Event(
        signal="closeout.seal.refused",
        payload={"path": path, "surface": surface},
        scope="node",
        role="observation",
    )


def seal_closeout_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Run ``seal()`` on a dumped closeout. Refuses undeclared bare scalars.

    Census flip without this call is a defect — the live call is the gate.
    """
    if PUBLICATION_BUILDER_CENSUS.get("ImplementCloseout.model_dump") != "sealed":
        raise RuntimeError(
            "ImplementCloseout.model_dump census is not sealed; refuse to publish"
        )
    _qualify_usage_tokens(payload)
    _qualify_surface_counts(payload)
    try:
        return seal(payload, closeout_surface_decl())
    except UnqualifiedScalarError as exc:
        emit_frontier_event(
            CloseoutSealRefused(path=str(exc), surface="ImplementCloseout.model_dump")
        )
        raise
