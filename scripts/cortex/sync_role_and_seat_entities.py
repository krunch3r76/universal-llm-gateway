"""Sync role:* and family:* Cortex entities from the CapabilityProfile registry.

Phase 7 of the agent-naming cleanup arc replaced the static AGENT_REGISTRY
dict (persona-named slugs → metadata) with a derived view over load_profiles()
and load_roles(). This script generates and upserts:

  family:claude / family:gpt / family:grok / family:gemini
    — primary memory anchors; type=model_family

  role:web-consult / role:cursor-consult / role:cursor-implement
  role:reviewer / role:gatherer / role:synthesizer / role:artisan / role:skeptic
  role:investigator (legacy)
    — functional team seats; type=role

Old persona role:* entities (role:oppie, role:forge, etc.) are retired in
Phase 7.7 — see run_retirement() in this script.

Usage:
    python scripts/cortex/sync_role_and_seat_entities.py [--dry-run] [--retire]

    --dry-run  Print entity dicts without writing to Cortex.
    --retire   Run the Phase-7.7 soft-retirement sweep (ONE WEEK after 7.1–7.6).
"""

from __future__ import annotations

import argparse
import json
import sys

import httpx

sys.path.insert(0, "libs/")

from agent_seat.profiles import get_profile, load_roles
from agent_seat.role_entity_sync import build_role_execution_attributes
from role_lint import RoleLintError, lint_role_payload

_CORTEX_BASE = "http+unix://%2Ftmp%2Funiversal-protocol%2Fcortex-api.sock"
_HEADERS = {"Content-Type": "application/json"}

# Persona role entities targeted for soft-retirement in Phase 7.7.
_PERSONA_ENTITIES_TO_RETIRE = [
    "role:cursor-claude",
    "role:web-claude",
    "role:api-claude",
    "role:oppie",
    "role:forge",
    "role:orion",
    "role:bard",
    "role:web-grok",
    "role:superheavy",
    "role:cursor_orion",
]


def build_family_entities() -> dict[str, dict[str, object]]:
    """Generate family:* entity payloads (memory anchors only — no dispatch metadata)."""
    display = {"gpt": "GPT", "claude": "Claude", "grok": "Grok", "gemini": "Gemini"}
    return {
        f"family:{family}": {
            "type": "model_family",
            "name": display.get(family, family.title()),
            "description": f"Memory anchor for the {family} model family.",
            "attributes": {},
        }
        for family in ("claude", "gpt", "grok", "gemini")
    }


def build_role_entities() -> dict[str, dict[str, object]]:
    """Generate role:* entity payloads from load_roles()."""
    out = {}
    for role_name, role in load_roles().items():
        profile = get_profile(role.default_family, role.default_platform)
        provider = profile.provider
        attrs: dict[str, object] = {
            "default_family": role.default_family,
            "default_platform": role.default_platform,
            "default_model": role.default_model,
            "allowed_models": list(role.allowed_models),
            "frontier_kind": provider,
        }
        attrs.update(build_role_execution_attributes(role_name, role, profile))
        out[f"role:{role_name}"] = {
            "type": "role",
            "name": role_name.title(),
            "description": role.description,
            "attributes": attrs,
        }
    return out


def _cx_request(method: str, path: str, body: dict | None = None) -> tuple[int, dict]:
    """Execute a request against the Cortex API via its Unix socket.

    Returns (status_code, body_dict).
    """
    transport = httpx.HTTPTransport(uds="/tmp/universal-protocol/cortex-api.sock")
    with httpx.Client(transport=transport, base_url="http://localhost") as client:
        if method == "GET":
            resp = client.get(path)
        elif method == "POST":
            resp = client.post(path, json=body)
        elif method == "PATCH":
            resp = client.patch(path, json=body)
        else:
            raise ValueError(f"Unsupported method: {method}")
    try:
        return resp.status_code, resp.json()
    except Exception:
        return resp.status_code, {
            "error": f"HTTP {resp.status_code}: {resp.text[:300]}"
        }


def upsert_entity(entity_id: str, payload: dict[str, object], dry_run: bool) -> None:
    """Create or update a Cortex entity. Idempotent — re-run safe."""
    if dry_run:
        print(f"[dry-run] upsert {entity_id}:")
        print(json.dumps(payload, indent=2))
        return

    if payload.get("type") == "role":
        lint_payload = {
            "id": entity_id,
            "type": "role",
            "name": str(payload.get("name") or ""),
            "description": str(payload.get("description") or ""),
            "attributes": dict(payload.get("attributes") or {}),
        }
        try:
            lint_role_payload(lint_payload)
        except RoleLintError as exc:
            print(f"  ERROR role lint {entity_id}: {exc}")
            raise

    # Try GET first — if 200, patch; if 404, create.
    get_status, existing = _cx_request("GET", f"/entities/{entity_id}")
    if get_status == 404:
        # Create new
        create_body = {"id": entity_id, **payload}
        post_status, result = _cx_request("POST", "/entities", create_body)
        if post_status in (200, 201) or "id" in result:
            print(f"  created {entity_id}")
        else:
            print(f"  ERROR creating {entity_id}: {result}")
    elif get_status == 200:
        # Update existing — patch name/description/attributes
        update_body = {
            k: v
            for k, v in payload.items()
            if k in ("name", "description", "attributes")
        }
        patch_status, result = _cx_request(
            "PATCH", f"/entities/{entity_id}", update_body
        )
        if patch_status in (200, 201) or "id" in result:
            print(f"  updated {entity_id}")
        else:
            print(f"  ERROR updating {entity_id}: {result}")
    else:
        print(f"  ERROR fetching {entity_id}: HTTP {get_status} {existing}")


def run_sync(dry_run: bool = False) -> None:
    """Sync all family:* and role:* entities to Cortex."""
    print("Building entity payloads...")
    families = build_family_entities()
    roles = build_role_entities()
    all_entities = {**families, **roles}
    print(f"  {len(families)} family entities, {len(roles)} role entities")

    print("\nSyncing to Cortex...")
    for entity_id, payload in all_entities.items():
        upsert_entity(entity_id, payload, dry_run)

    if not dry_run:
        print(f"\nSync complete — {len(all_entities)} entities written.")


def run_retirement(dry_run: bool = False) -> None:
    """Phase 7.7: soft-retire persona role:* entities (ONE WEEK AFTER 7.1–7.6).

    Sets status=deprecated, workflow_state=superseded on each persona entity.
    Hard-delete is not exposed in the cortex MCP surface — soft-retire is
    the dissolution mechanism.
    """
    print(f"Retiring {len(_PERSONA_ENTITIES_TO_RETIRE)} persona role entities...")
    for entity_id in _PERSONA_ENTITIES_TO_RETIRE:
        if dry_run:
            print(f"  [dry-run] retire {entity_id}")
            continue
        patch_status, result = _cx_request(
            "PATCH",
            f"/entities/{entity_id}",
            {"status": "deprecated", "workflow_state": "superseded"},
        )
        if patch_status in (200, 201) or "id" in result:
            print(f"  retired {entity_id}")
        else:
            print(f"  ERROR retiring {entity_id}: {result}")
    print("Retirement sweep complete.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Print without writing")
    parser.add_argument(
        "--retire",
        action="store_true",
        help="Run Phase-7.7 soft-retirement (ONE WEEK after 7.1-7.6)",
    )
    args = parser.parse_args()

    if args.retire:
        run_retirement(dry_run=args.dry_run)
    else:
        run_sync(dry_run=args.dry_run)
