"""apply — validated Cortex writes for consolidate-continuity.

Takes the distill fold, checks every reference against what ingest actually
saw in the graph, and writes the delta through cortex-api. Provenance is
split by author:

- **bus-quoted** (watermark, mission when the tip CHECKPOINT states one):
  ``direct_observation`` / ``confirmed`` — the text is copied, not composed.
- **model-authored** (resume line, claims, relationships, mission fallback):
  ``compression`` / ``believed`` — a fold, tagged ``seeded_by`` so a later
  pass can audit or supersede everything this pipeline ever wrote.

Boundaries kept deliberately narrow for v1: stale flags are *reported*, not
applied (superseding another author's claim is an attended call); the hub is
never renamed unless ``allow_retitle`` is set; targets of new relationships
must already exist. ``dry_run`` plans every write and performs none.
"""

from __future__ import annotations

import json
import logging
import re
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
    cortex_client,
    dispatch,
)

logger = logging.getLogger(__name__)

_MISSION_LINE_RE = re.compile(
    r"^\s*\**Mission:?\**\s*(?P<text>.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
_QUOTED = {
    "derivation_type": "direct_observation",
    "confidence": "confirmed",
    "confidence_score": 0.95,
}
_FOLDED = {
    "derivation_type": "compression",
    "confidence": "believed",
    "confidence_score": 0.7,
}


def quoted_mission(residue: str | None) -> str | None:
    """First ``Mission:`` line of the tip CHECKPOINT residue, verbatim."""
    match = _MISSION_LINE_RE.search(residue or "")
    return match.group("text").strip() if match else None


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _prior_by_prefix(rows: list[dict[str, Any]], prefix: str) -> int | None:
    """Newest assertion this pipeline wrote with ``prefix`` — the one to supersede."""
    ids = [
        int(row["id"])
        for row in rows
        if row.get("seeded_by") == SEEDED_BY
        and str(row.get("claim") or "").startswith(prefix)
    ]
    return max(ids) if ids else None


class _Plan:
    """Ordered write plan; ``run`` executes or (dry_run) merely records it."""

    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.entries: list[dict[str, Any]] = []

    def skip(self, kind: str, reason: str, **detail: Any) -> None:
        self.entries.append(
            {"kind": kind, "status": "skipped", "reason": reason, **detail}
        )

    async def run(
        self, client: Any, kind: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        entry: dict[str, Any] = {"kind": kind, "tool": tool, "arguments": arguments}
        if self.dry_run:
            entry["status"] = "planned"
            self.entries.append(entry)
            return None
        reply = await dispatch(client, tool, arguments)
        if "error" in reply:
            entry.update(status="error", error=str(reply["error"])[:300])
            self.entries.append(entry)
            return None
        entry.update(
            status="written",
            id=reply.get("id") or (reply.get("assertion") or {}).get("id"),
        )
        self.entries.append(entry)
        return reply


def _assert_args(
    hub_id: str,
    claim: str,
    *,
    quoted: bool,
    evidence_uris: list[str],
    supersedes: int | None,
) -> dict:
    args: dict[str, Any] = {
        "entity_id": hub_id,
        "claim": claim,
        "seeded_by": SEEDED_BY,
        "evidence_uris": evidence_uris,
        **(_QUOTED if quoted else _FOLDED),
    }
    if supersedes:
        args["supersedes_id"] = supersedes
    return args


class ContinuityConsolidateApplyHandler(BaseHandler):
    step_type = "continuity_consolidate_apply_v1"

    @override
    async def execute(self, step: Any, context: Any) -> StepOutput:
        ingest = context.get_output("ingest")
        ingest_json = (ingest.json if ingest is not None else None) or {}
        if ingest_json.get("skip"):
            payload = {"ok": True, "skip": ingest_json["skip"], "detail": ingest_json}
            return StepOutput(raw=json.dumps(payload, default=str), json=payload)

        distill = context.get_output("distill")
        fold = (distill.json if distill is not None else None) or {}
        if not fold or fold.get("_skipped"):
            err = {
                "ok": False,
                "error": "distill produced no JSON fold",
                "json_parse_error": getattr(distill, "json_parse_error", None),
                "raw_head": (getattr(distill, "raw", "") or "")[:400],
            }
            return StepOutput(raw=json.dumps(err), json=err, error=err["error"])

        options: dict[str, Any] = getattr(context, "options", {}) or {}
        plan = _Plan(dry_run=bool(options.get("dry_run", False)))
        hub_id: str = ingest_json["hub_id"]
        trigger: dict[str, Any] = ingest_json.get("trigger") or {}
        trigger_ref = f"agent-bus:{trigger.get('thread')}#{trigger.get('turn')}"
        payload_text: str = ingest_json.get("payload_text") or ""
        prior_rows: list[dict[str, Any]] = ingest_json.get("pipeline_rows") or []
        known_ids = {
            int(i) for i in ingest_json.get("assertion_ids") or [] if i is not None
        }
        existing_targets = set(ingest_json.get("relationship_targets") or [])
        existing_claims = {_norm(str(c)) for c in ingest_json.get("claims") or []}
        tip = (
            (options.get("tip_checkpoint") or {})
            if isinstance(options.get("tip_checkpoint"), dict)
            else {}
        )

        async with cortex_client() as client:
            # 1. Watermark — the idempotency anchor; always first so a crash
            #    after it never re-folds the same trigger.
            stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
            await plan.run(
                client,
                "watermark",
                "assert",
                _assert_args(
                    hub_id,
                    f"{WATERMARK_PREFIX}{trigger.get('thread')}#{trigger.get('turn')} at {stamp} "
                    f"(consolidate-continuity v1; root agent-bus:{options.get('root_thread')})",
                    quoted=True,
                    evidence_uris=[trigger_ref],
                    supersedes=_prior_by_prefix(prior_rows, WATERMARK_PREFIX),
                ),
            )

            # 2. Mission — bus-quoted from the tip CHECKPOINT when it states one;
            #    the model's line is the fallback and is tagged as a fold.
            mission_quote = quoted_mission(tip.get("residue"))
            mission_text = mission_quote or str(fold.get("mission") or "").strip()
            if mission_text:
                mission_claim = f"{MISSION_PREFIX}{mission_text}"
                if _norm(mission_claim) in existing_claims:
                    plan.skip("mission", "unchanged")
                else:
                    await plan.run(
                        client,
                        "mission",
                        "assert",
                        _assert_args(
                            hub_id,
                            mission_claim,
                            quoted=mission_quote is not None,
                            evidence_uris=[
                                f"agent-bus:{options.get('root_thread')}#{tip.get('turn')}"
                            ]
                            if mission_quote and tip.get("turn") is not None
                            else [trigger_ref],
                            supersedes=_prior_by_prefix(prior_rows, MISSION_PREFIX),
                        ),
                    )

            # 3. Resume line — settled | live | next, always model-authored.
            resume = fold.get("resume") or {}
            if isinstance(resume, dict) and any(
                resume.get(k) for k in ("settled", "live", "next")
            ):
                resume_claim = (
                    f"{RESUME_PREFIX}settled={resume.get('settled', '').strip()} | "
                    f"live={resume.get('live', '').strip()} | next={resume.get('next', '').strip()} "
                    f"(after {trigger_ref})"
                )
                await plan.run(
                    client,
                    "resume",
                    "assert",
                    _assert_args(
                        hub_id,
                        resume_claim,
                        quoted=False,
                        evidence_uris=[trigger_ref],
                        supersedes=_prior_by_prefix(prior_rows, RESUME_PREFIX),
                    ),
                )

            # 4. Claims — bounded, deduplicated, evidence limited to the payload.
            max_claims = int(options.get("max_claims", 6))
            for item in (fold.get("claims") or [])[:max_claims]:
                if not isinstance(item, dict):
                    continue
                prefix = CLAIM_PREFIXES.get(str(item.get("kind") or "").lower())
                text = str(item.get("claim") or "").strip()
                if not prefix or not text:
                    plan.skip("claim", "unknown_kind_or_empty", item=item)
                    continue
                claim = f"{prefix}{text}"
                if _norm(claim) in existing_claims:
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
                    _assert_args(
                        hub_id,
                        claim,
                        quoted=False,
                        evidence_uris=uris or [trigger_ref],
                        supersedes=None,
                    ),
                )
                existing_claims.add(_norm(claim))

            # 5. Relationships — target must be quoted from the payload and exist.
            for rel in fold.get("relationships") or []:
                if not isinstance(rel, dict):
                    continue
                target = str(rel.get("target_entity_id") or "").strip()
                rel_type = str(rel.get("type") or "").strip()
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
                        "type_id": rel_type or "references",
                        "evidence": str(
                            rel.get("evidence") or f"folded from {trigger_ref}"
                        )[:300],
                        "agent": SEEDED_BY,
                    },
                )
                existing_targets.add(target)

            # 6. Retitle — opt-in only.
            retitle = fold.get("retitle")
            if isinstance(retitle, dict) and (
                retitle.get("name") or retitle.get("description")
            ):
                if options.get("allow_retitle"):
                    args = {"entity_id": hub_id}
                    if retitle.get("name"):
                        args["name"] = str(retitle["name"])[:120]
                    if retitle.get("description"):
                        args["description"] = str(retitle["description"])[:600]
                    await plan.run(client, "retitle", "entity_update", args)
                else:
                    plan.skip("retitle", "allow_retitle=false", proposed=retitle)

        stale_flags = [
            s
            for s in fold.get("stale") or []
            if isinstance(s, dict) and int(s.get("assertion_id") or -1) in known_ids
        ]
        errors = [e for e in plan.entries if e.get("status") == "error"]
        result = {
            "ok": not errors,
            "dry_run": plan.dry_run,
            "hub_id": hub_id,
            "trigger": trigger_ref,
            "mission_source": "checkpoint"
            if mission_quote
            else ("model" if mission_text else "none"),
            "counts": {
                "written": sum(1 for e in plan.entries if e.get("status") == "written"),
                "planned": sum(1 for e in plan.entries if e.get("status") == "planned"),
                "skipped": sum(1 for e in plan.entries if e.get("status") == "skipped"),
                "errors": len(errors),
            },
            "writes": plan.entries,
            "stale_flags": stale_flags,
        }
        logger.info(
            "continuity apply hub=%s trigger=%s dry_run=%s counts=%s",
            hub_id,
            trigger_ref,
            plan.dry_run,
            result["counts"],
        )
        return StepOutput(
            raw=json.dumps(result, default=str),
            json=result,
            error=None
            if result["ok"]
            else "; ".join(e.get("error", "") for e in errors)[:500],
        )
