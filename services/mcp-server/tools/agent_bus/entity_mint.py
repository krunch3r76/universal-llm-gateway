"""agent_bus ``substrate_entity_mint`` — cortex entity_create request-surface verb.

Life and code seats call this instead of round-tripping a code seat to mint
an entity or owner. The module validates identity + rejects silent-drop
fields, then delegates the Cortex POST to ``substrate_entity_mint.mint_entity``
so mcp-server never authors the HTTP body itself.
"""

from __future__ import annotations

from typing import Any

from mcp_events import record
from substrate_entity_mint import mint_entity, resolve_create_slot

# HTTP EntityCreate fields the dispatch op does not consume — accepting and
# omitting them would silently drop (V3 claim+note class). Named 422 instead.
_RETENTION_KEYS = ("retention_policy", "retention_ttl_days")
_TRAIT_KEYS = ("confidence_band", "lifecycle", "adoption")


def _reject(reason: str, *, message: str) -> dict[str, Any]:
    record("mcp.agentbus.entity_mint.rejected", reason=reason)
    return {"error": message, "reason": reason, "status_code": 422}


def _present(value: object) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _entity_mint_dispatch(
    *,
    id: str = "",
    type: str = "",
    name: str = "",
    entity_id: str = "",
    entity_type: str = "",
    title: str = "",
    description: str | None = None,
    status: str | None = None,
    workflow_state: str | None = None,
    notes: str | None = None,
    aliases: list[str] | str | None = None,
    attributes: dict[str, Any] | str | None = None,
    source_uri: str | None = None,
    content_hash: str | None = None,
    retention_policy: str | None = None,
    retention_ttl_days: int | str | None = None,
    confidence_band: str | None = None,
    lifecycle: str | None = None,
    adoption: str | None = None,
    density_triage: str | None = None,
) -> dict[str, Any]:
    """Validate + dispatch ``agent_bus.substrate_entity_mint``.

    Requires ``id`` (or ``entity_id``), ``type`` (or ``entity_type``), and
    ``name`` (or ``title``); POSTs cortex ``entity_create`` via the shared
    ``substrate_entity_mint`` lib. Forwards every dispatch-consumed field
    verbatim. Retention, Option-C traits, and top-level ``density_triage``
    reject with named 422 rather than silent drop. Hop/request envelope
    fields are rejected by the dispatch unknown-argument gate.
    """
    if _present(density_triage):
        return _reject(
            "entity_mint_density_triage_is_attribute",
            message=(
                "substrate_entity_mint: density_triage is attributes.density_triage, "
                "not a top-level field — pass it inside attributes="
            ),
        )
    if any(_present(v) for v in (retention_policy, retention_ttl_days)):
        return _reject(
            "entity_mint_retention_not_on_dispatch",
            message=(
                "substrate_entity_mint: retention_policy / retention_ttl_days are "
                "HTTP EntityCreate fields; cortex dispatch entity_create does not "
                "consume them. Omit them (do not silently drop)."
            ),
        )
    if any(_present(v) for v in (confidence_band, lifecycle, adoption)):
        return _reject(
            "entity_mint_trait_not_settable_at_create",
            message=(
                "substrate_entity_mint: Option-C traits confidence_band / lifecycle "
                "/ adoption are not settable at create (use entity_update after)."
            ),
        )

    resolved_id, id_err = resolve_create_slot(
        primary=id, alias=entity_id, primary_name="id", alias_name="entity_id"
    )
    if id_err:
        return _reject("entity_mint_id_alias_conflict", message=id_err)
    resolved_type, type_err = resolve_create_slot(
        primary=type, alias=entity_type, primary_name="type", alias_name="entity_type"
    )
    if type_err:
        return _reject("entity_mint_type_alias_conflict", message=type_err)
    resolved_name, name_err = resolve_create_slot(
        primary=name, alias=title, primary_name="name", alias_name="title"
    )
    if name_err:
        return _reject("entity_mint_name_alias_conflict", message=name_err)

    if not resolved_id:
        return _reject(
            "entity_mint_id_required",
            message="substrate_entity_mint: id is required (entity_id= alias accepted)",
        )
    if not resolved_type:
        return _reject(
            "entity_mint_type_required",
            message="substrate_entity_mint: type is required (entity_type= alias accepted)",
        )
    if not resolved_name:
        return _reject(
            "entity_mint_name_required",
            message="substrate_entity_mint: name is required (title= alias accepted)",
        )

    result = mint_entity(
        id=resolved_id,
        type=resolved_type,
        name=resolved_name,
        description=description,
        status=status,
        workflow_state=workflow_state,
        notes=notes,
        aliases=aliases,
        attributes=attributes,
        source_uri=source_uri,
        content_hash=content_hash,
        seat="mcp",
        via_adapter=True,
        surface="code",
    )
    if "error" in result:
        record(
            "mcp.agentbus.entity_mint.failed",
            entity_id=resolved_id,
            status_code=result.get("status_code"),
        )
        return result

    created_id = result.get("id")
    if not created_id and isinstance(result.get("item"), dict):
        created_id = result["item"].get("id")
    stamped = dict(result)
    if created_id is not None:
        stamped["entity_id"] = created_id
    record("mcp.agentbus.entity_mint.posted", entity_id=created_id)
    return stamped
