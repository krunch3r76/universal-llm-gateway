"""Validate journal extraction proposals against Cortex schema."""

from __future__ import annotations

import json
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput
from universal_event_bus.events.debug import emit_debug_event

VALID_ENTITY_TYPES = frozenset(
    {
        "person",
        "event",
        "legal_matter",
        "organization",
        "property",
        "document",
        "deadline",
    }
)

VALID_RELATIONSHIP_TYPES = frozenset(
    {
        "associated_with",
        "co_owns",
        "deadline_for",
        "depicts",
        "employed_by",
        "filed_in",
        "involves",
        "org_party_to",
        "owns",
        "party_to",
        "preceded_by",
        "references",
        "related_to",
        "represents",
    }
)

VALID_CONFIDENCES = frozenset(
    {
        "confirmed",
        "believed",
        "suspected",
        "hypothesized",
        "observed",
    }
)


def _validate_entity(entity: dict[str, Any]) -> str | None:
    """Return error string if entity is invalid, else None."""
    eid = entity.get("id", "")
    etype = entity.get("type", "")
    if not eid or not isinstance(eid, str):
        return f"Entity missing or invalid 'id': {eid!r}"
    if etype not in VALID_ENTITY_TYPES:
        return f"Entity {eid!r}: unknown type {etype!r}"
    if ":" not in eid:
        return f"Entity {eid!r}: id must be {{type}}:{{slug}} format"
    id_prefix = eid.split(":")[0]
    if id_prefix != etype:
        return f"Entity {eid!r}: id prefix {id_prefix!r} != type {etype!r}"
    if not entity.get("name"):
        return f"Entity {eid!r}: missing 'name'"
    return None


def _validate_relationship(
    rel: dict[str, Any],
    known_entity_ids: frozenset[str],
) -> str | None:
    """Return error string if relationship is invalid, else None."""
    rtype = rel.get("type", "")
    if rtype not in VALID_RELATIONSHIP_TYPES:
        return f"Relationship: unknown type {rtype!r}"
    from_e = rel.get("from_entity", "")
    to_e = rel.get("to_entity", "")
    if not from_e or not to_e:
        return f"Relationship {rtype!r}: missing from_entity or to_entity"
    missing = []
    if from_e not in known_entity_ids:
        missing.append(f"from_entity={from_e!r}")
    if to_e not in known_entity_ids:
        missing.append(f"to_entity={to_e!r}")
    if missing:
        return (
            f"Relationship {rtype!r}: references unknown entities: {', '.join(missing)}"
        )
    return None


def _validate_assertion(
    assertion: dict[str, Any],
    known_entity_ids: frozenset[str],
) -> str | None:
    """Return error string if assertion is invalid, else None."""
    eid = assertion.get("entity_id", "")
    if not eid:
        return "Assertion: missing 'entity_id'"
    if eid not in known_entity_ids:
        return f"Assertion: references unknown entity {eid!r}"
    if not assertion.get("claim"):
        return f"Assertion on {eid!r}: missing 'claim'"
    conf = assertion.get("confidence", "")
    if conf not in VALID_CONFIDENCES:
        return f"Assertion on {eid!r}: unknown confidence {conf!r}"
    if not assertion.get("evidence"):
        return f"Assertion on {eid!r}: missing 'evidence'"
    return None


def _validate_event(event: dict[str, Any]) -> str | None:
    """Return error string if event entity is invalid, else None."""
    eid = event.get("id", "")
    if not eid or ":" not in eid:
        return f"Event missing or malformed 'id': {eid!r}"
    if event.get("type") != "event":
        return f"Event {eid!r}: type must be 'event'"
    if not event.get("name"):
        return f"Event {eid!r}: missing 'name'"
    return None


class ValidateHandler(BaseHandler):
    step_type = "journal_extract_validate_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        available_keys = list(context.outputs.keys())
        extract_output = context.get_output("extract")

        await emit_debug_event(
            "pipeline.debug.validate",
            {
                "execution_id": str(getattr(context, "execution_id", "")),
                "phase": "step_entry",
                "outputs_available": available_keys,
                "extract_found": extract_output is not None,
                "extract_type": type(extract_output).__name__
                if extract_output is not None
                else None,
                "extract_json_type": (
                    type(extract_output.json).__name__
                    if extract_output is not None and hasattr(extract_output, "json")
                    else None
                ),
                "extract_raw_len": (
                    len(extract_output.raw or "")
                    if extract_output is not None and hasattr(extract_output, "raw")
                    else None
                ),
            },
            source="pipeline.journal_extract.validate",
        )

        if extract_output is None:
            return StepOutput(
                raw=json.dumps(
                    {
                        "error": "Extract step output not found in context",
                        "available_outputs": available_keys,
                        "entities": [],
                        "relationships": [],
                        "assertions": [],
                        "events": [],
                        "validation_errors": [
                            f"Extract step output missing. Available: {available_keys}"
                        ],
                    }
                ),
            )

        entry_id = context.get_option("entry_id")
        entry_date = context.get_option("entry_date")

        proposals = extract_output.json
        if proposals is None:
            try:
                raw = (extract_output.raw or "").strip()
                if not raw:
                    raise json.JSONDecodeError("empty response", doc="", pos=0)
                if raw.startswith("```"):
                    if "\n" not in raw:
                        raise json.JSONDecodeError("malformed fenced json", raw, 0)
                    raw = raw.split("\n", 1)[1].rsplit("```", 1)[0].strip()
                proposals = json.loads(raw)
            except json.JSONDecodeError as e:
                await emit_debug_event(
                    "pipeline.debug.validate",
                    {
                        "execution_id": str(getattr(context, "execution_id", "")),
                        "phase": "json_parse_failure",
                        "error": str(e),
                        "raw_len": len(extract_output.raw or ""),
                        "raw_preview": (extract_output.raw or "")[:500],
                    },
                    source="pipeline.journal_extract.validate",
                )
                return StepOutput(
                    raw=json.dumps(
                        {
                            "entry_id": entry_id,
                            "entry_date": entry_date,
                            "error": f"JSON parse failure: {e}",
                            "entities": [],
                            "relationships": [],
                            "assertions": [],
                            "events": [],
                            "validation_errors": [f"JSON parse failure: {e}"],
                        }
                    ),
                )

        errors: list[str] = []
        valid_entities: list[dict[str, Any]] = []
        valid_relationships: list[dict[str, Any]] = []
        valid_assertions: list[dict[str, Any]] = []
        valid_events: list[dict[str, Any]] = []

        for entity in proposals.get("entities", []):
            err = _validate_entity(entity)
            if err:
                errors.append(err)
            else:
                valid_entities.append(entity)

        for event in proposals.get("events", []):
            err = _validate_event(event)
            if err:
                errors.append(err)
            else:
                valid_events.append(event)

        all_entity_ids = frozenset(e["id"] for e in valid_entities + valid_events)

        for rel in proposals.get("relationships", []):
            err = _validate_relationship(rel, all_entity_ids)
            if err:
                errors.append(err)
            else:
                valid_relationships.append(rel)

        for assertion in proposals.get("assertions", []):
            err = _validate_assertion(assertion, all_entity_ids)
            if err:
                errors.append(err)
            else:
                valid_assertions.append(assertion)

        result = {
            "entry_id": entry_id or proposals.get("entry_id"),
            "entry_date": entry_date or proposals.get("entry_date"),
            "entities": valid_entities,
            "relationships": valid_relationships,
            "assertions": valid_assertions,
            "events": valid_events,
            "validation_errors": errors,
            "summary": {
                "entities_accepted": len(valid_entities),
                "entities_rejected": len(proposals.get("entities", []))
                - len(valid_entities),
                "relationships_accepted": len(valid_relationships),
                "relationships_rejected": len(proposals.get("relationships", []))
                - len(valid_relationships),
                "assertions_accepted": len(valid_assertions),
                "assertions_rejected": len(proposals.get("assertions", []))
                - len(valid_assertions),
                "events_accepted": len(valid_events),
                "events_rejected": len(proposals.get("events", [])) - len(valid_events),
                "total_errors": len(errors),
            },
        }

        raw_output = json.dumps(result, indent=2)

        await emit_debug_event(
            "pipeline.debug.validate",
            {
                "execution_id": str(getattr(context, "execution_id", "")),
                "phase": "complete",
                "entry_id": entry_id,
                "entities_accepted": len(valid_entities),
                "relationships_accepted": len(valid_relationships),
                "assertions_accepted": len(valid_assertions),
                "events_accepted": len(valid_events),
                "total_errors": len(errors),
                "errors": errors[:10] if errors else [],
            },
            source="pipeline.journal_extract.validate",
        )

        return StepOutput(raw=raw_output, json=result)
