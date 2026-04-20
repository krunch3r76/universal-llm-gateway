#!/usr/bin/env python3
"""
One-way sync: agent-identity/*.md (Cortex data layer) → Cortex entities.

For every ``{slug}-birth.md`` file under $AGENT_IDENTITY_DIR, this script
upserts a matching ``prompt:{slug}-birth`` entity and a ``derived_from`` edge
from ``ai_agent:{slug}`` to that prompt. The agent's identity is "derived from"
its birth prompt — that's the closest semantics in the existing edge taxonomy.
(A dedicated ``has_birth`` type could be registered later; the ID coupling
``prompt:{slug}-birth`` ↔ ``ai_agent:{slug}`` already carries the binding.)

Idempotence: the sync compares the stored ``content_hash`` (sha256 of the
file) and only issues an update when it changes. Edges are created only when
missing. Re-runs with unchanged files are no-ops.

Usage::

    python scripts/cortex/sync-agent-identity.py            # sync all
    python scripts/cortex/sync-agent-identity.py --dry-run  # preview only
    python scripts/cortex/sync-agent-identity.py --only orion
    python scripts/cortex/sync-agent-identity.py --verbose
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "libs"))

from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

# ────────────────────────────────────────────────────────────────────────────
# Agent registry — filename stem → canonical metadata. The filename drives
# the mapping; this table adds the human-readable name and native provider
# persisted on the Cortex entity.
# ────────────────────────────────────────────────────────────────────────────

AGENT_REGISTRY: dict[str, dict[str, str]] = {
    "orion": {"name": "Orion", "provider": "OpenAI"},
    "oppie": {"name": "Oppie", "provider": "xAI"},
    "bard": {"name": "Bard", "provider": "Google"},
    "web-claude": {"name": "Web Claude", "provider": "Anthropic"},
    "api-claude": {"name": "API Claude", "provider": "Anthropic"},
    "cursor-claude": {"name": "Cursor Claude", "provider": "Anthropic"},
}

EDGE_TYPE = "derived_from"  # ai_agent:{slug} → prompt:{slug}-birth
DEFAULT_SESSION_ID = "agent-identity-sync"
DEFAULT_AGENT = "cursor-claude"


# ────────────────────────────────────────────────────────────────────────────
# Core types
# ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SyncPlan:
    slug: str
    name: str
    provider: str
    file_path: Path
    content: str
    content_hash: str

    @property
    def entity_id(self) -> str:
        return f"prompt:{self.slug}-birth"

    @property
    def agent_id(self) -> str:
        return f"ai_agent:{self.slug}"


@dataclass
class SyncOutcome:
    slug: str
    entity_action: str  # "created" | "updated" | "unchanged"
    edge_action: str  # "created" | "present" | "skipped"


# ────────────────────────────────────────────────────────────────────────────
# Plan building
# ────────────────────────────────────────────────────────────────────────────


def _resolve_identity_dir() -> Path:
    """Read $AGENT_IDENTITY_DIR; fail loudly if unset."""
    raw = os.environ.get("AGENT_IDENTITY_DIR")
    if not raw:
        print(
            "error: AGENT_IDENTITY_DIR is not set.\n"
            "The agent-identity directory lives in the Cortex data layer "
            "(not the repo). Export AGENT_IDENTITY_DIR before running, e.g.:\n"
            "  export AGENT_IDENTITY_DIR=/mnt/torus/mcp-data/files/agent-identity",
            file=sys.stderr,
        )
        sys.exit(2)
    base = Path(raw).resolve()
    if not base.is_dir():
        print(f"error: AGENT_IDENTITY_DIR does not exist: {base}", file=sys.stderr)
        sys.exit(2)
    return base


def _build_plans(base: Path, only: str | None) -> list[SyncPlan]:
    """Walk AGENT_IDENTITY_DIR and build one SyncPlan per birth prompt file."""
    plans: list[SyncPlan] = []
    seen: set[str] = set()
    for path in sorted(base.glob("*-birth.md")):
        slug = path.stem.removesuffix("-birth")
        if only and slug != only:
            continue
        meta = AGENT_REGISTRY.get(slug)
        if meta is None:
            print(
                f"warn: skipping {path.name} — slug {slug!r} is not in "
                f"AGENT_REGISTRY (add it to scripts/cortex/sync-agent-identity.py)",
                file=sys.stderr,
            )
            continue
        content = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        plans.append(
            SyncPlan(
                slug=slug,
                name=meta["name"],
                provider=meta["provider"],
                file_path=path,
                content=content,
                content_hash=digest,
            )
        )
        seen.add(slug)
    if only and only not in seen:
        print(
            f"error: --only {only!r} matched no file under {base}",
            file=sys.stderr,
        )
        sys.exit(2)
    return plans


# ────────────────────────────────────────────────────────────────────────────
# Cortex operations
# ────────────────────────────────────────────────────────────────────────────


def _entity_payload(plan: SyncPlan) -> dict[str, object]:
    """Shape an EntityCreate body for the cortex-store REST API."""
    return {
        "id": plan.entity_id,
        "type": "prompt",
        "name": f"{plan.name} birth prompt",
        "description": (
            f"Canonical birth prompt for {plan.name}. Synced from the "
            f"Cortex data layer (agent-identity/{plan.file_path.name})."
        ),
        "status": "confirmed",
        "source_uri": f"agent-identity/{plan.file_path.name}",
        "content_hash": plan.content_hash,
        "attributes": {
            "content": plan.content,
            "agent_id": plan.agent_id,
            "native_provider": plan.provider,
            "synced_at": datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "synced_by": "scripts/cortex/sync-agent-identity.py",
        },
    }


def _update_payload(plan: SyncPlan) -> dict[str, object]:
    """EntityUpdate shape — description + status not resent on update."""
    body = _entity_payload(plan)
    return {
        "name": body["name"],
        "source_uri": body["source_uri"],
        "content_hash": body["content_hash"],
        "attributes": body["attributes"],
    }


def _sync_entity(client, plan: SyncPlan, dry_run: bool, verbose: bool) -> str:
    """Upsert prompt:{slug}-birth; return action taken."""
    resp = client.get(f"/entities/{plan.entity_id}")
    if resp.status_code == 404:
        if dry_run:
            if verbose:
                print(f"  [dry-run] would CREATE {plan.entity_id}")
            return "created"
        client.post("/entities", json=_entity_payload(plan)).raise_for_status()
        return "created"
    resp.raise_for_status()
    existing = resp.json()
    if existing.get("content_hash") == plan.content_hash:
        if verbose:
            print(f"  content_hash match — {plan.entity_id} unchanged")
        return "unchanged"
    if dry_run:
        if verbose:
            print(f"  [dry-run] would UPDATE {plan.entity_id}")
        return "updated"
    r = client.patch(f"/entities/{plan.entity_id}", json=_update_payload(plan))
    r.raise_for_status()
    return "updated"


def _edge_exists(client, from_node: str, to_node: str) -> bool:
    r = client.get(
        "/edges",
        params={
            "from_node": from_node,
            "to_node": to_node,
            "edge_type": EDGE_TYPE,
            "limit": 1,
        },
    )
    r.raise_for_status()
    return r.json().get("count", 0) > 0


def _sync_edge(
    client, plan: SyncPlan, session_id: str, agent: str, dry_run: bool, verbose: bool
) -> str:
    """Ensure ai_agent:{slug} --derived_from--> prompt:{slug}-birth exists."""
    # Don't seed an edge when the target ai_agent entity isn't in Cortex.
    agent_probe = client.get(f"/entities/{plan.agent_id}")
    if agent_probe.status_code == 404:
        if verbose:
            print(f"  skip edge — {plan.agent_id} missing (seed ai_agent entity first)")
        return "skipped"
    agent_probe.raise_for_status()

    if _edge_exists(client, plan.agent_id, plan.entity_id):
        if verbose:
            print(f"  edge present — {plan.agent_id} derived_from {plan.entity_id}")
        return "present"
    if dry_run:
        if verbose:
            print(
                f"  [dry-run] would CREATE edge "
                f"{plan.agent_id} --derived_from--> {plan.entity_id}"
            )
        return "created"
    payload = {
        "session_id": session_id,
        "agent": agent,
        "from_node": plan.agent_id,
        "to_node": plan.entity_id,
        "edge_type": EDGE_TYPE,
        "strength": 1.0,
        "context": (
            "Canonical birth prompt binding — agent identity is derived from "
            "the birth prompt at registration time. Synced from "
            f"agent-identity/{plan.file_path.name} in the Cortex data layer."
        ),
    }
    client.post("/edges", json=payload).raise_for_status()
    return "created"


# ────────────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────────────


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    p.add_argument("--dry-run", action="store_true", help="preview without writing")
    p.add_argument("--only", metavar="SLUG", help="sync only this agent slug")
    p.add_argument("--verbose", "-v", action="store_true", help="per-item logging")
    p.add_argument(
        "--session-id",
        default=DEFAULT_SESSION_ID,
        help=f"edge provenance session_id (default: {DEFAULT_SESSION_ID})",
    )
    p.add_argument(
        "--agent",
        default=DEFAULT_AGENT,
        help=f"edge provenance agent (default: {DEFAULT_AGENT})",
    )
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    base = _resolve_identity_dir()
    plans = _build_plans(base, args.only)
    if not plans:
        print(f"no birth prompts found under {base}", file=sys.stderr)
        return 1

    print(f"syncing {len(plans)} birth prompt(s) from {base}")
    if args.dry_run:
        print("  (dry-run — no writes)")

    outcomes: list[SyncOutcome] = []
    with make_sync_client(DEFAULT_CORTEX_URL) as client:
        for plan in plans:
            print(f"- {plan.slug}")
            entity_action = _sync_entity(client, plan, args.dry_run, args.verbose)
            edge_action = _sync_edge(
                client,
                plan,
                session_id=args.session_id,
                agent=args.agent,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
            outcomes.append(
                SyncOutcome(
                    slug=plan.slug,
                    entity_action=entity_action,
                    edge_action=edge_action,
                )
            )

    print()
    print("summary:")
    for o in outcomes:
        print(f"  {o.slug:<14s} entity={o.entity_action:<9s} edge={o.edge_action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
