"""ingest — graph read + watermark gate for consolidate-continuity.

Reads the root house's hub (entity, active assertions, relationships) from
cortex-api, decides whether this trigger still needs folding, and renders
the single text payload the distill step sees. All bus material arrives
pre-fetched in ``pipeline_options`` (see the trigger module) — this step
never talks to agent-bus.

Skip sentinels (``json.skip``), each terminal for the run:
- ``missing_options``          — hook payload malformed (no root/trigger)
- ``no_hub``                   — hub bootstrap failed (``ensure_hub`` could not mint)
- ``superseded_by_watermark``  — an equal-or-newer trigger already consolidated
"""

from __future__ import annotations

import json
import logging
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._cortex import (
    SEEDED_BY,
    active_assertions,
    compact,
    cortex_client,
    ensure_hub,
    parse_watermark,
    relationships,
)

logger = logging.getLogger(__name__)

_CLAIM_CAP = 600
_RESIDUE_CAP = 4000
_TRIGGER_CAP = 6000


def _skip(reason: str, **extra: Any) -> StepOutput:
    payload = {"ok": True, "skip": reason, **extra}
    return StepOutput(raw=json.dumps(payload, default=str), json=payload)


def _trigger_is_stale(
    trigger: dict[str, Any], watermark: dict[str, Any] | None
) -> bool:
    """True when the watermark already covers this trigger (same lane, turn ≥ ours)."""
    if watermark is None:
        return False
    same_lane = str(watermark.get("thread")) == str(trigger.get("thread"))
    return same_lane and int(watermark.get("turn") or 0) >= int(
        trigger.get("turn") or 0
    )


def render_payload(
    *,
    hub: dict[str, Any],
    assertions: list[dict[str, Any]],
    relationships_rows: list[dict[str, Any]],
    options: dict[str, Any],
) -> str:
    """Compact, model-facing view: hub → graph → tip CHECKPOINT residue → trigger."""
    root = options.get("root") or {}
    tip = options.get("tip_checkpoint") or {}
    trigger = options.get("trigger") or {}
    lines: list[str] = [
        f"# Root house agent-bus:{options.get('root_thread')} — {root.get('slug')}",
        f"summary: {compact(root.get('summary') or '', 400)}",
        f"tags: {', '.join(root.get('tags') or [])}",
        "",
        f"## Hub entity {hub.get('id')}",
        f"name: {hub.get('name')}",
        f"description: {compact(hub.get('description') or '', 600)}",
        "",
        "## Current hub assertions (id · seeded_by · claim)",
    ]
    for row in assertions:
        lines.append(
            f"- [{row.get('id')}] ({row.get('seeded_by') or 'unattributed'}) "
            f"{compact(row.get('claim') or '', _CLAIM_CAP)}"
        )
    if not assertions:
        lines.append("- (none)")
    lines += ["", "## Current hub relationships (type → target)"]
    for rel in relationships_rows:
        target = (
            rel.get("target_id")
            if rel.get("source_id") == hub.get("id")
            else rel.get("source_id")
        )
        lines.append(
            f"- {rel.get('type_id')} → {target} ({rel.get('target_name') or rel.get('source_name') or ''})"
        )
    if not relationships_rows:
        lines.append("- (none)")
    lines += [
        "",
        f"## Tip CHECKPOINT (turn {tip.get('turn')}, {tip.get('from_agent')}, {tip.get('created_at')}) — authored residue",
        compact(tip.get("residue") or "(no CHECKPOINT on the root yet)", _RESIDUE_CAP),
        "",
        f"## Trigger CLOSEOUT agent-bus:{trigger.get('thread')}#{trigger.get('turn')} "
        f"({trigger.get('from_agent')}, {trigger.get('created_at')})",
        f"subject: {trigger.get('subject')}",
        compact(trigger.get("body") or "", _TRIGGER_CAP),
    ]
    return "\n".join(lines)


class ContinuityConsolidateIngestHandler(BaseHandler):
    step_type = "continuity_consolidate_ingest_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        options: dict[str, Any] = getattr(context, "options", {}) or {}
        root_thread = str(options.get("root_thread") or "").strip()
        trigger = options.get("trigger") or {}
        if not root_thread or not trigger.get("thread") or trigger.get("turn") is None:
            return _skip(
                "missing_options",
                expected=["root_thread", "trigger.thread", "trigger.turn"],
            )

        root_meta = options.get("root") or {}
        async with cortex_client() as client:
            hub, bootstrap = await ensure_hub(client, root_thread, root_meta)
            if hub is None:
                return _skip("no_hub", root_thread=root_thread, detail=bootstrap)
            hub_id = str(hub["id"])
            rows = await active_assertions(client, hub_id)
            rels = await relationships(client, hub_id)

        watermark = parse_watermark(rows)
        if not options.get("force") and _trigger_is_stale(trigger, watermark):
            return _skip(
                "superseded_by_watermark",
                hub_id=hub_id,
                watermark=watermark,
                trigger=trigger,
            )

        payload_text = render_payload(
            hub=hub, assertions=rows, relationships_rows=rels, options=options
        )
        result = {
            "ok": True,
            "hub_id": hub_id,
            "hub_bootstrap": bootstrap,
            "hub": {"name": hub.get("name"), "description": hub.get("description")},
            "assertion_ids": [row.get("id") for row in rows],
            "claims": [row.get("claim") for row in rows],
            # Only what this pipeline wrote before — the supersede targets for apply.
            "pipeline_rows": [
                {
                    "id": row.get("id"),
                    "claim": row.get("claim"),
                    "seeded_by": row.get("seeded_by"),
                }
                for row in rows
                if row.get("seeded_by") == SEEDED_BY
            ],
            "relationship_targets": [
                rel.get("target_id")
                if rel.get("source_id") == hub_id
                else rel.get("source_id")
                for rel in rels
            ],
            "watermark": watermark,
            "trigger": trigger,
            "payload_chars": len(payload_text),
            "payload_text": payload_text,
        }
        logger.info(
            "continuity ingest root=%s hub=%s assertions=%d relationships=%d payload_chars=%d",
            root_thread,
            hub_id,
            len(rows),
            len(rels),
            len(payload_text),
        )
        return StepOutput(raw=json.dumps(result, default=str), json=result)
