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

from ._cortex import (
    MISSION_PREFIX,
    RESUME_PREFIX,
    SEEDED_BY,
    WATERMARK_PREFIX,
    dispatch,
)

SINGLETON_PREFIXES: tuple[tuple[str, str], ...] = (
    ("watermark", WATERMARK_PREFIX),
    ("mission", MISSION_PREFIX),
    ("resume", RESUME_PREFIX),
)

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

# S4-A — per-kind caps on distill output (drop oldest when over cap).
CLAIM_KIND_CAPS: dict[str, int] = {
    "closed": 8,
    "open": 4,
    "decided": 4,
    "artifact": 4,
    "superseded": 2,
}


def quoted_mission(residue: str | None) -> str | None:
    """First ``Mission:`` line of the tip CHECKPOINT residue, verbatim."""
    match = _MISSION_LINE_RE.search(residue or "")
    return match.group("text").strip() if match else None


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def cap_distill_claims(claims: list[Any]) -> tuple[list[dict[str, Any]], int]:
    """Cap per claim kind; drop oldest rows when distill exceeds S4-A limits."""
    by_kind: dict[str, list[tuple[int, dict[str, Any]]]] = {
        kind: [] for kind in CLAIM_KIND_CAPS
    }
    passthrough: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(claims):
        if not isinstance(item, dict):
            continue
        kind = str(item.get("kind") or "").lower()
        if kind in CLAIM_KIND_CAPS:
            by_kind[kind].append((index, item))
        else:
            passthrough.append((index, item))

    keep_indices: set[int] = {index for index, _ in passthrough}
    dropped = 0
    for kind, cap in CLAIM_KIND_CAPS.items():
        items = by_kind[kind]
        if len(items) > cap:
            dropped += len(items) - cap
            items = items[-cap:]
        keep_indices.update(index for index, _ in items)

    capped = [
        item
        for index, item in enumerate(claims)
        if isinstance(item, dict) and index in keep_indices
    ]
    return capped, dropped


def _row_active(row: dict[str, Any]) -> bool:
    return not row.get("superseded_by")


def active_pipeline_ids_by_prefix(
    rows: list[dict[str, Any]], prefix: str
) -> list[int]:
    """Live pipeline rows for ``prefix``, newest first — F1 repair input."""
    ids = [
        int(row["id"])
        for row in rows
        if row.get("seeded_by") == SEEDED_BY
        and _row_active(row)
        and str(row.get("claim") or "").startswith(prefix)
    ]
    return sorted(ids, reverse=True)


def prior_ids_by_prefix(rows: list[dict[str, Any]], prefix: str) -> list[int]:
    """Active assertions this pipeline wrote with ``prefix``, newest first.

    Singleton rows (watermark, mission, resume) should have exactly one active
    instance; anything beyond the newest is debris from an earlier failed run
    and gets chained onto the new row as well.
    """
    return active_pipeline_ids_by_prefix(rows, prefix)


def singleton_active_counts(rows: list[dict[str, Any]]) -> dict[str, int]:
    """P7 F1 probe — active pipeline singleton rows per prefix."""
    return {
        kind: len(active_pipeline_ids_by_prefix(rows, prefix))
        for kind, prefix in SINGLETON_PREFIXES
    }


def matching_prior_id(rows: list[dict[str, Any]], claim: str) -> int | None:
    """Newest active pipeline row whose claim equals ``claim`` (whitespace/case-folded)."""
    ids = [
        int(row["id"])
        for row in rows
        if row.get("seeded_by") == SEEDED_BY
        and norm(str(row.get("claim") or "")) == norm(claim)
    ]
    return max(ids) if ids else None


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
        # Chain every other live prior — burst replay can leave sibling rows
        # when two applies both supersede the same parent (prior_ids[1:] alone
        # misses the concurrent sibling).
        await self.chain_stale(
            client,
            kind,
            keep_id=new_id,
            stale_ids=[row_id for row_id in prior_ids if row_id != new_id],
        )

    async def repair_pipeline_singletons(
        self, client: Any, rows: list[dict[str, Any]]
    ) -> dict[str, int]:
        """Chain duplicate pipeline singletons; keep newest per prefix (F1 guard)."""
        chained: dict[str, int] = {}
        for kind, prefix in SINGLETON_PREFIXES:
            ids = active_pipeline_ids_by_prefix(rows, prefix)
            if len(ids) <= 1:
                chained[kind] = 0
                continue
            await self.chain_stale(client, kind, keep_id=ids[0], stale_ids=ids[1:])
            chained[kind] = len(ids) - 1
        return chained

    async def chain_stale(
        self, client: Any, kind: str, *, keep_id: int, stale_ids: list[int]
    ) -> None:
        """Supersede every ``stale_ids`` row with ``keep_id`` (singleton repair).

        Used after a fresh write and when a singleton is unchanged but earlier
        runs left duplicates behind — the graph converges either way.
        """
        for stale in stale_ids:
            if stale == keep_id:
                continue
            await self.run(
                client,
                f"{kind}_chain",
                "assertion_update",
                {"assertion_id": stale, "superseded_by": keep_id},
            )
