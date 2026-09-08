"""Write plan + provenance shaping for the consolidate-continuity apply step.

Two provenance classes, chosen by who authored the text:

- **quoted** — copied from a bus turn (watermark, CHECKPOINT mission line):
  ``direct_observation`` / ``confirmed``.
- **folded** — composed by the distill model (resume line, claims, mission
  fallback): ``inference`` / ``believed``.

Everything carries ``seeded_by=continuity-consolidate`` so the pipeline's
whole footprint stays auditable and supersedable as one set.
"""

from __future__ import annotations

import re
from typing import Any

from ._cortex import SEEDED_BY, dispatch

_MISSION_LINE_RE = re.compile(
    r"^\s*\**Mission:?\**\s*(?P<text>.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
_QUOTED = {
    "derivation_type": "direct_observation",
    "confidence": "confirmed",
    "confidence_score": 0.95,
}
# `compression` is reserved for RAG-chunk provenance (requires chunk_id);
# a fold of bus turns is session-originated → `inference`.
_FOLDED = {
    "derivation_type": "inference",
    "confidence": "believed",
    "confidence_score": 0.7,
}


def quoted_mission(residue: str | None) -> str | None:
    """First ``Mission:`` line of the tip CHECKPOINT residue, verbatim."""
    match = _MISSION_LINE_RE.search(residue or "")
    return match.group("text").strip() if match else None


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def prior_ids_by_prefix(rows: list[dict[str, Any]], prefix: str) -> list[int]:
    """Active assertions this pipeline wrote with ``prefix``, newest first.

    Singleton rows (watermark, mission, resume) should have exactly one active
    instance; anything beyond the newest is debris from an earlier failed run
    and gets chained onto the new row as well.
    """
    ids = [
        int(row["id"])
        for row in rows
        if row.get("seeded_by") == SEEDED_BY
        and str(row.get("claim") or "").startswith(prefix)
    ]
    return sorted(ids, reverse=True)


def assert_args(
    hub_id: str,
    claim: str,
    *,
    quoted: bool,
    evidence: str,
    evidence_uris: list[str],
    supersedes: int | None = None,
) -> dict[str, Any]:
    args: dict[str, Any] = {
        "entity_id": hub_id,
        "claim": claim,
        "seeded_by": SEEDED_BY,
        "evidence": evidence,
        "evidence_uris": evidence_uris,
        **(_QUOTED if quoted else _FOLDED),
    }
    if supersedes:
        # cortex-api chains lineage only for force=true + supersedes_id; without
        # force the id is ignored and the prior row stays active (live run
        # 6e96f80e left two WATERMARK rows).
        args["supersedes_id"] = supersedes
        args["force"] = True
    return args


def describe_hub(
    *, mission: str, resume: dict[str, Any], trigger_ref: str, stamp: str
) -> str:
    """Hub description = the resume surface: mission, settled/live/next, watermark."""
    parts = [
        f"Mission: {mission.rstrip('.')}." if mission else "Mission: (none folded yet)."
    ]
    for key in ("settled", "live", "next"):
        value = str(resume.get(key) or "").strip()
        if value:
            parts.append(f"{key.capitalize()}: {value.rstrip('.')}.")
    parts.append(
        f"Consolidated through {trigger_ref} at {stamp} (consolidate-continuity v1)."
    )
    return " ".join(parts)[:1200]


def _written_id(reply: dict[str, Any]) -> Any:
    for key in ("item", "assertion", "relationship", "entity"):
        nested = reply.get(key)
        if isinstance(nested, dict) and nested.get("id") is not None:
            return nested["id"]
    return reply.get("id")


class WritePlan:
    """Ordered write ledger; ``run`` executes, or under ``dry_run`` validates/records."""

    def __init__(self, *, dry_run: bool) -> None:
        self.dry_run = dry_run
        self.entries: list[dict[str, Any]] = []

    def skip(self, kind: str, reason: str, **detail: Any) -> None:
        self.entries.append(
            {"kind": kind, "status": "skipped", "reason": reason, **detail}
        )

    def count(self, status: str) -> int:
        return sum(1 for e in self.entries if e.get("status") == status)

    def errors(self) -> list[dict[str, Any]]:
        return [e for e in self.entries if e.get("status") == "error"]

    async def run(
        self, client: Any, kind: str, tool: str, arguments: dict[str, Any]
    ) -> dict[str, Any] | None:
        entry: dict[str, Any] = {"kind": kind, "tool": tool, "arguments": arguments}
        if self.dry_run:
            # `assert` validates server-side without writing; the other ops have
            # no dry-run form and are only recorded.
            if tool != "assert":
                entry["status"] = "planned"
                self.entries.append(entry)
                return None
            reply = await dispatch(client, tool, {**arguments, "dry_run": True})
            entry["status"] = "error" if "error" in reply else "planned"
            if "error" in reply:
                entry["error"] = str(reply["error"])[:300]
            self.entries.append(entry)
            return None
        reply = await dispatch(client, tool, arguments)
        if "error" in reply:
            entry.update(status="error", error=str(reply["error"])[:300])
            self.entries.append(entry)
            return None
        entry.update(status="written", id=_written_id(reply))
        self.entries.append(entry)
        return reply

    async def write_singleton(
        self,
        client: Any,
        kind: str,
        *,
        hub_id: str,
        claim: str,
        quoted: bool,
        evidence: str,
        evidence_uris: list[str],
        prior_ids: list[int],
    ) -> None:
        """Assert a row that must be the only active one of its kind.

        The newest prior is superseded atomically by the assert (force +
        supersedes_id); any older survivors are chained onto the new row
        afterwards so the hub never carries two live watermarks or missions.
        """
        newest = prior_ids[0] if prior_ids else None
        reply = await self.run(
            client,
            kind,
            "assert",
            assert_args(
                hub_id,
                claim,
                quoted=quoted,
                evidence=evidence,
                evidence_uris=evidence_uris,
                supersedes=newest,
            ),
        )
        new_id = _written_id(reply) if reply else None
        if new_id is None:
            return
        for stale in prior_ids[1:]:
            await self.run(
                client,
                f"{kind}_chain",
                "assertion_update",
                {"assertion_id": stale, "superseded_by": new_id},
            )
