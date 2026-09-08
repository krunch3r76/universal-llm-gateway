"""apply — validated Cortex writes for consolidate-continuity.

Takes the distill fold, checks every reference against what ingest actually
saw in the graph, and writes the delta through cortex-api in a fixed order:
watermark → mission → resume → claims → relationships → hub description
(name change opt-in).
Provenance classes and the write ledger live in ``_plan``.

Boundaries kept deliberately narrow for v1: stale flags are *reported*, not
applied (superseding another author's claim is an attended call); the hub is
never renamed unless ``allow_retitle`` is set; targets of new relationships
must be quoted from the payload and already exist. ``dry_run`` validates the
assertion writes server-side and performs none.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from typing import Any, override

from systems.pipeline.core.handlers.builtin import BaseHandler
from systems.pipeline.core.handlers.protocol import StepOutput

from ._cortex import (
    CLAIM_PREFIXES,
    MISSION_PREFIX,
    RESUME_PREFIX,
    SEEDED_BY,
    WATERMARK_PREFIX,
    active_assertions,
    cortex_client,
    dispatch,
)
from ._plan import (
    CLAIM_KIND_CAPS,
    WritePlan,
    assert_args,
    cap_distill_claims,
    describe_hub,
    matching_prior_id,
    norm,
    prior_ids_by_prefix,
    quoted_mission,
)

logger = logging.getLogger(__name__)


def _passthrough(payload: dict[str, Any], error: str | None = None) -> StepOutput:
    return StepOutput(raw=json.dumps(payload, default=str), json=payload, error=error)


class ContinuityConsolidateApplyHandler(BaseHandler):
    """Validate a distill fold against ingest and write the Cortex delta.

    Order: singleton repair, watermark, mission, resume, S4-A capped claims,
    relationships, hub description. ``dry_run`` validates asserts only.
    """

    step_type = "continuity_consolidate_apply_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        ingest = context.get_output("ingest")
        ingest_json = (ingest.json if ingest is not None else None) or {}
        if ingest_json.get("skip"):
            return _passthrough(
                {"ok": True, "skip": ingest_json["skip"], "detail": ingest_json}
            )

        distill = context.get_output("distill")
        fold = (distill.json if distill is not None else None) or {}
        if not fold or fold.get("_skipped"):
            err = {
                "ok": False,
                "error": "distill produced no JSON fold",
                "json_parse_error": getattr(distill, "json_parse_error", None),
                "raw_head": (getattr(distill, "raw", "") or "")[:400],
            }
            return _passthrough(err, err["error"])

        options: dict[str, Any] = getattr(context, "options", {}) or {}
        plan = WritePlan(dry_run=bool(options.get("dry_run", False)))
        hub_id: str = ingest_json["hub_id"]
        trigger: dict[str, Any] = ingest_json.get("trigger") or {}
        trigger_ref = f"agent-bus:{trigger.get('thread')}#{trigger.get('turn')}"
        payload_text: str = ingest_json.get("payload_text") or ""
        prior_rows: list[dict[str, Any]] = ingest_json.get("pipeline_rows") or []
        known_ids = {
            int(i) for i in ingest_json.get("assertion_ids") or [] if i is not None
        }
        existing_targets = set(ingest_json.get("relationship_targets") or [])
        existing_claims = {norm(str(c)) for c in ingest_json.get("claims") or []}
        tip = (
            options.get("tip_checkpoint")
            if isinstance(options.get("tip_checkpoint"), dict)
            else {}
        )

        root_ref = f"agent-bus:{options.get('root_thread')}"
        tip_ref = (
            f"{root_ref}#{tip.get('turn')}" if tip.get("turn") is not None else None
        )
        tip_note = f"; tip CHECKPOINT {tip_ref}" if tip_ref else ""
        quoted_evidence = (
            f"consolidate-continuity v1: copied from bus turn {trigger_ref}{tip_note}"
        )
        folded_evidence = f"consolidate-continuity v1: model fold of CLOSEOUT {trigger_ref} against hub {hub_id}{tip_note}"

        async with cortex_client() as client:
            live_rows = await active_assertions(client, hub_id)
            singleton_repair = await plan.repair_pipeline_singletons(client, live_rows)
            prior_rows = [
                {
                    "id": row.get("id"),
                    "claim": row.get("claim"),
                    "seeded_by": row.get("seeded_by"),
                    "superseded_by": row.get("superseded_by"),
                }
                for row in live_rows
                if row.get("seeded_by") == SEEDED_BY
            ]

            # 1. Watermark — the idempotency anchor; first, so a crash after it
            #    never re-folds the same trigger.
            stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            await plan.write_singleton(
                client,
                "watermark",
                hub_id=hub_id,
                claim=f"{WATERMARK_PREFIX}{trigger.get('thread')}#{trigger.get('turn')} at {stamp} "
                f"(consolidate-continuity v1; root {root_ref})",
                quoted=True,
                evidence=quoted_evidence,
                evidence_uris=[trigger_ref],
                prior_ids=prior_ids_by_prefix(prior_rows, WATERMARK_PREFIX),
            )

            # 2. Mission — quoted from the tip CHECKPOINT when it states one; the
            #    model's line is the fallback and is tagged as a fold.
            mission_quote = quoted_mission(tip.get("residue"))
            mission_text = mission_quote or str(fold.get("mission") or "").strip()
            if mission_text:
                mission_claim = f"{MISSION_PREFIX}{mission_text}"
                keep_id = matching_prior_id(prior_rows, mission_claim)
                if keep_id is not None:
                    # Unchanged mission: keep the matching row, fold any other
                    # live MISSION rows (earlier failed runs) into it.
                    plan.skip("mission", "unchanged", id=keep_id)
                    await plan.chain_stale(
                        client,
                        "mission",
                        keep_id=keep_id,
                        stale_ids=prior_ids_by_prefix(prior_rows, MISSION_PREFIX),
                    )
                elif norm(mission_claim) in existing_claims:
                    plan.skip("mission", "stated_by_other_author")
                else:
                    await plan.write_singleton(
                        client,
                        "mission",
                        hub_id=hub_id,
                        claim=mission_claim,
                        quoted=mission_quote is not None,
                        evidence=quoted_evidence if mission_quote else folded_evidence,
                        evidence_uris=[tip_ref]
                        if mission_quote and tip_ref
                        else [trigger_ref],
                        prior_ids=prior_ids_by_prefix(prior_rows, MISSION_PREFIX),
                    )

            # 3. Resume line — settled | live | next, always model-authored.
            resume = fold.get("resume") or {}
            if isinstance(resume, dict) and any(
                resume.get(k) for k in ("settled", "live", "next")
            ):
                await plan.write_singleton(
                    client,
                    "resume",
                    hub_id=hub_id,
                    claim=(
                        f"{RESUME_PREFIX}settled={str(resume.get('settled') or '').strip()} | "
                        f"live={str(resume.get('live') or '').strip()} | "
                        f"next={str(resume.get('next') or '').strip()} (after {trigger_ref})"
                    ),
                    quoted=False,
                    evidence=folded_evidence,
                    evidence_uris=[trigger_ref],
                    prior_ids=prior_ids_by_prefix(prior_rows, RESUME_PREFIX),
                )

            # 4. Claims — S4-A per-kind caps, then dedup + payload evidence.
            # options.max_claims>0 is an emergency overall slice AFTER the
            # table (YAML default 0 so CLAIM_KIND_CAPS actually binds).
            raw_claims = fold.get("claims") or []
            capped_claims, claims_dropped = cap_distill_claims(raw_claims)
            kept_ids = {id(item) for item in capped_claims}
            for item in raw_claims:
                if not isinstance(item, dict) or id(item) in kept_ids:
                    continue
                kind = str(item.get("kind") or "").lower()
                if kind in CLAIM_KIND_CAPS:
                    plan.skip(
                        "claim",
                        "kind_cap",
                        kind=kind,
                        claim=item.get("claim"),
                    )
            overall_cap = int(options.get("max_claims") or 0)
            if overall_cap > 0 and len(capped_claims) > overall_cap:
                extra = capped_claims[overall_cap:]
                claims_dropped += len(extra)
                for item in extra:
                    plan.skip("claim", "max_claims", item=item)
                capped_claims = capped_claims[:overall_cap]
            for item in capped_claims:
                if not isinstance(item, dict):
                    continue
                prefix = CLAIM_PREFIXES.get(str(item.get("kind") or "").lower())
                text = str(item.get("claim") or "").strip()
                if not prefix or not text:
                    plan.skip("claim", "unknown_kind_or_empty", item=item)
                    continue
                claim = f"{prefix}{text}"
                if norm(claim) in existing_claims:
                    plan.skip("claim", "duplicate", claim=claim)
                    continue
                uris = [
                    u
                    for u in item.get("evidence_uris") or []
                    if isinstance(u, str) and u in payload_text
                ]
                await plan.run(
                    client,
                    "claim",
                    "assert",
                    assert_args(
                        hub_id,
                        claim,
                        quoted=False,
                        evidence=folded_evidence,
                        evidence_uris=uris or [trigger_ref],
                    ),
                )
                existing_claims.add(norm(claim))

            # 5. Relationships — target must be quoted from the payload and exist.
            for rel in fold.get("relationships") or []:
                if not isinstance(rel, dict):
                    continue
                target = str(rel.get("target_entity_id") or "").strip()
                if not target or target == hub_id or target not in payload_text:
                    plan.skip("relationship", "target_not_in_payload", target=target)
                    continue
                if target in existing_targets:
                    plan.skip("relationship", "exists", target=target)
                    continue
                probe = await dispatch(
                    client, "entity_get", {"entity_id": target, "intent": "card"}
                )
                if "error" in probe or not probe.get("id"):
                    plan.skip("relationship", "target_missing", target=target)
                    continue
                await plan.run(
                    client,
                    "relationship",
                    "relationship_create",
                    {
                        "source_id": hub_id,
                        "target_id": target,
                        "type_id": str(rel.get("type") or "").strip() or "references",
                        "evidence": str(
                            rel.get("evidence") or f"folded from {trigger_ref}"
                        )[:300],
                        "agent": SEEDED_BY,
                    },
                )
                existing_targets.add(target)

            # 6. Hub description — the card's summary_row, the first thing a cold
            #    seat reads. Owned by this pipeline as a projection of the current
            #    fold; `name` stays opt-in (a rename is visible everywhere).
            entity_args: dict[str, Any] = {
                "entity_id": hub_id,
                "description": describe_hub(
                    mission=mission_text,
                    resume=resume if isinstance(resume, dict) else {},
                    trigger_ref=trigger_ref,
                    stamp=stamp,
                ),
            }
            retitle = fold.get("retitle")
            if isinstance(retitle, dict) and retitle.get("name"):
                if options.get("allow_retitle"):
                    entity_args["name"] = str(retitle["name"])[:120]
                else:
                    plan.skip("retitle", "allow_retitle=false", proposed=retitle)
            await plan.run(client, "describe", "entity_update", entity_args)

        errors = plan.errors()
        result = {
            "ok": not errors,
            "dry_run": plan.dry_run,
            "hub_id": hub_id,
            "trigger": trigger_ref,
            "mission_source": "checkpoint"
            if mission_quote
            else ("model" if mission_text else "none"),
            "singleton_repair": singleton_repair,
            "claims_dropped_by_cap": claims_dropped,
            "counts": {
                "written": plan.count("written"),
                "planned": plan.count("planned"),
                "skipped": plan.count("skipped"),
                "errors": len(errors),
            },
            "writes": plan.entries,
            # Reported, never applied: another author's claim is theirs to supersede.
            "stale_flags": [
                s
                for s in fold.get("stale") or []
                if isinstance(s, dict) and int(s.get("assertion_id") or -1) in known_ids
            ],
        }
        logger.info(
            "continuity apply hub=%s trigger=%s dry_run=%s counts=%s",
            hub_id,
            trigger_ref,
            plan.dry_run,
            result["counts"],
        )
        return _passthrough(
            result,
            None
            if result["ok"]
            else "; ".join(e.get("error", "") for e in errors)[:500],
        )
