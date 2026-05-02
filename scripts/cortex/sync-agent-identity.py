#!/usr/bin/env python3
"""One-way sync: agent-identity/*.md (Cortex data layer) → Cortex entities.

For every ``{slug}-birth.md`` file under $AGENT_IDENTITY_DIR, this script
upserts a matching ``prompt:{slug}-birth`` entity and a ``derived_from`` edge
from ``ai_agent:{slug}`` to that prompt. The agent's identity is "derived from"
its birth prompt — that's the closest semantics in the existing edge taxonomy.
(A dedicated ``has_birth`` type could be registered later; the ID coupling
``prompt:{slug}-birth`` ↔ ``ai_agent:{slug}`` already carries the binding.)

Also patches ``ai_agent:{slug}.attributes`` with the persona contract
(frontier_kind, default_model, allowed_models, allowed_options,
persona_seed_ref) from ``AGENT_REGISTRY``, for consumers such as
``libs/agent_seat/hydrate_agent`` and the Stargate ``/api/v1/team/generate``
endpoint.

Tools allowlist retired (todo:retire-tools-allowlist-as-caller-concern); tool
surface is now universal with provider-derived silent coercion for quirks (e.g.
xAI multi-agent models). No per-persona tools field in contract.

Idempotence: the sync compares the stored ``content_hash`` (sha256 of the
file) and only issues an update when it changes. Edges are created only when
missing. Persona attributes are patched only when they differ from the desired
contract. Re-runs with unchanged files and attrs are no-ops.

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
from typing import Any, cast

_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "libs"))

from sync_agent_identity_registry import AGENT_REGISTRY  # noqa: E402
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

# ────────────────────────────────────────────────────────────────────────────
# Agent registry — filename stem → canonical metadata. Drives:
# (a) the prompt entity (name / native_provider on prompt:{slug}-birth)
# (b) the persona contract written onto ai_agent:{slug}.attributes
#     (frontier_kind, default_model, allowed_models, allowed_options,
#      persona_seed_ref) — consumed by libs/agent_seat/hydrate_agent and the
#     Stargate /api/v1/team/generate endpoint.
#
# Tools allowlist retired (todo:retire-tools-allowlist-as-caller-concern):
# tool surface is universal; provider quirks (xAI multi-agent no client-side
# function tools) handled by silent coercion + telemetry in dispatch handler.
# No caller-facing allowlist or per-persona tools field.
# ────────────────────────────────────────────────────────────────────────────

DEFAULT_SESSION_ID = "agent-identity-sync"
DEFAULT_AGENT = "cursor-claude"
EDGE_TYPE = "derived_from"

_PERSONA_ATTR_KEYS = (
    "frontier_kind",
    "default_model",
    "allowed_models",
    "allowed_options",
    "persona_seed_ref",
)


# ────────────────────────────────────────────────────────────────────────────
# Core types
# ────────────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SyncPlan:
    slug: str
    name: str
    provider: str
    frontier_kind: str
    default_model: str | None
    allowed_models: list[str]
    allowed_options: list[str] | None
    persona_seed_ref: str | None
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
    persona_attrs_action: str  # "updated" | "unchanged" | "skipped"


# ────────────────────────────────────────────────────────────────────────────
# Plan building
# ────────────────────────────────────────────────────────────────────────────


def _resolve_identity_dir() -> Path:
    """Read $AGENT_IDENTITY_DIR; fail loudly if unset."""
    raw = os.environ.get("AGENT_IDENTITY_DIR")
    if not raw:
        sys.exit(2)
    base = Path(raw).resolve()
    if not base.is_dir():
        sys.exit(2)
    return base


def _meta_str_list(key: str, meta: dict[str, object]) -> list[str]:
    raw = meta[key]
    return [str(x) for x in cast("list[object]", raw)]


def _meta_optional_str_list(key: str, meta: dict[str, object]) -> list[str] | None:
    raw = meta[key]
    if raw is None:
        return None
    return [str(x) for x in cast("list[object]", raw)]


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
            continue
        content = path.read_text(encoding="utf-8")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        dm_raw = meta["default_model"]
        default_model = None if dm_raw is None else str(dm_raw)
        seed_raw = meta["persona_seed_ref"]
        persona_seed_ref = None if seed_raw is None else str(seed_raw)
        plans.append(
            SyncPlan(
                slug=slug,
                name=str(meta["name"]),
                provider=str(meta["provider"]),
                frontier_kind=str(meta["frontier_kind"]),
                default_model=default_model,
                allowed_models=_meta_str_list("allowed_models", meta),
                allowed_options=_meta_optional_str_list("allowed_options", meta),
                persona_seed_ref=persona_seed_ref,
                file_path=path,
                content=content,
                content_hash=digest,
            ),
        )
        seen.add(slug)
    if only and only not in seen:
        sys.exit(2)
    return plans


# ────────────────────────────────────────────────────────────────────────────
# Cortex operations
# ────────────────────────────────────────────────────────────────────────────


def _persona_attrs_payload(plan: SyncPlan) -> dict[str, object]:
    """Build persona contract attributes (tools field retired per
    todo:retire-tools-allowlist-as-caller-concern).
    """
    return {
        "frontier_kind": plan.frontier_kind,
        "default_model": plan.default_model,
        "allowed_models": list(plan.allowed_models),
        "allowed_options": (
            list(plan.allowed_options) if plan.allowed_options is not None else None
        ),
        "persona_seed_ref": plan.persona_seed_ref,
    }


def _sync_agent_persona_attrs(
    client: Any, plan: SyncPlan, dry_run: bool, verbose: bool,
) -> str:
    """Patch ai_agent:{slug}.attributes with the persona contract.

    No-op when stored attrs already match. Returns
    ``"updated" | "unchanged" | "skipped"``.
    """
    probe = client.get(f"/entities/{plan.agent_id}")
    if probe.status_code == 404:
        if verbose:
            pass
        return "skipped"
    probe.raise_for_status()
    body = probe.json()
    existing_attrs = (body.get("attributes") or {}) if isinstance(body, dict) else {}
    desired = _persona_attrs_payload(plan)
    if all(existing_attrs.get(k) == desired[k] for k in _PERSONA_ATTR_KEYS):
        if verbose:
            pass
        return "unchanged"
    merged_attrs = {**existing_attrs, **desired}
    if dry_run:
        if verbose:
            pass
        return "updated"
    r = client.patch(
        f"/entities/{plan.agent_id}",
        json={"attributes": merged_attrs},
    )
    r.raise_for_status()
    return "updated"


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


def _sync_entity(client: Any, plan: SyncPlan, dry_run: bool, verbose: bool) -> str:
    """Upsert prompt:{slug}-birth; return action taken."""
    resp = client.get(f"/entities/{plan.entity_id}")
    if resp.status_code == 404:
        if dry_run:
            if verbose:
                pass
            return "created"
        client.post("/entities", json=_entity_payload(plan)).raise_for_status()
        return "created"
    resp.raise_for_status()
    existing = resp.json()
    if existing.get("content_hash") == plan.content_hash:
        if verbose:
            pass
        return "unchanged"
    if dry_run:
        if verbose:
            pass
        return "updated"
    r = client.patch(f"/entities/{plan.entity_id}", json=_update_payload(plan))
    r.raise_for_status()
    return "updated"


def _edge_exists(client: Any, from_node: str, to_node: str) -> bool:
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
    client: Any,
    plan: SyncPlan,
    session_id: str,
    agent: str,
    dry_run: bool,
    verbose: bool,
) -> str:
    """Ensure ai_agent:{slug} --derived_from--> prompt:{slug}-birth exists."""
    # Don't seed an edge when the target ai_agent entity isn't in Cortex.
    agent_probe = client.get(f"/entities/{plan.agent_id}")
    if agent_probe.status_code == 404:
        if verbose:
            pass
        return "skipped"
    agent_probe.raise_for_status()

    if _edge_exists(client, plan.agent_id, plan.entity_id):
        if verbose:
            pass
        return "present"
    if dry_run:
        if verbose:
            pass
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
        return 1

    if args.dry_run:
        pass

    outcomes: list[SyncOutcome] = []
    with make_sync_client(DEFAULT_CORTEX_URL) as client:
        for plan in plans:
            entity_action = _sync_entity(client, plan, args.dry_run, args.verbose)
            edge_action = _sync_edge(
                client,
                plan,
                session_id=args.session_id,
                agent=args.agent,
                dry_run=args.dry_run,
                verbose=args.verbose,
            )
            persona_attrs_action = _sync_agent_persona_attrs(
                client, plan, args.dry_run, args.verbose,
            )
            outcomes.append(
                SyncOutcome(
                    slug=plan.slug,
                    entity_action=entity_action,
                    edge_action=edge_action,
                    persona_attrs_action=persona_attrs_action,
                ),
            )

    for _o in outcomes:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
