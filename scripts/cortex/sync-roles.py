#!/usr/bin/env python3
"""One-way sync: ``ROLE_REGISTRY`` (canonical execution-contract source) → Cortex
``role:{slug}`` entities.

Phase 5 of the agent-naming cleanup arc replaces the legacy
``sync-agent-identity.py`` (which wrote ``prompt:{slug}-birth`` entities AND
patched ``ai_agent:{slug}.attributes``) with this focused script — writes
``role:{slug}`` entities only. Persona prose lives in the filesystem at
``$AGENT_IDENTITY_DIR/{slug}-birth.md`` and is referenced by
``role:{slug}.attributes.persona_seed_ref`` (a URI, never inlined).

Validation:
  - Every payload is run through ``role_lint.lint_role_payload`` before any
    write. R1-R3 (self-concept) violations abort the sync with a structured
    error; R4 warnings are surfaced but accepted.

Idempotence:
  - Compares stored role: payload to desired payload field-by-field; PATCHes
    only on diff. Re-runs with unchanged registry are no-ops.

Usage::

    python scripts/cortex/sync-roles.py            # sync all roles
    python scripts/cortex/sync-roles.py --dry-run  # preview only
    python scripts/cortex/sync-roles.py --only orion
    python scripts/cortex/sync-roles.py --verbose
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Any

_REPO_ROOT = __import__("pathlib").Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_REPO_ROOT / "libs"))

from role_lint import RoleLintError, lint_role_payload  # noqa: E402
from sync_agent_identity_registry import AGENT_REGISTRY  # noqa: E402
from transport_utils import DEFAULT_CORTEX_URL, make_sync_client  # noqa: E402

# Slugs in AGENT_REGISTRY that ARE dispatch targets (have an executable
# allowed_models list and default_model). Everything else is a "seat" role —
# self-reflection / continuity anchor only, no dispatch resolution.
_DISPATCH_TARGET_SLUGS: frozenset[str] = frozenset(
    {"orion", "oppie", "bard", "api-claude", "forge"}
)


@dataclass(frozen=True, slots=True)
class RoleSyncPlan:
    slug: str
    payload: dict[str, Any]

    @property
    def entity_id(self) -> str:
        return f"role:{self.slug}"


def _build_role_payload(slug: str, registry_entry: dict[str, Any]) -> dict[str, Any]:
    """Translate a ``sync_agent_identity_registry`` entry into a role: payload.

    Dispatch-target slugs (orion, oppie, bard, api-claude, forge) get full
    execution-contract attributes. Seat slugs (web-claude, cursor-claude,
    cursor_orion, web-grok) get a minimal contract with empty allowed_models
    and a "not a dispatch target" purpose statement.
    """
    is_dispatch_target = slug in _DISPATCH_TARGET_SLUGS
    frontier_kind = str(registry_entry["frontier_kind"])
    default_model = registry_entry["default_model"]
    allowed_models = list(registry_entry["allowed_models"])
    persona_seed_ref = registry_entry["persona_seed_ref"]

    if is_dispatch_target:
        purpose = (
            f"Frontier consult role on the {frontier_kind} family "
            f"(default {default_model}). Dispatched via team_dispatch / "
            "frontier_dispatch admission; runs through the agent_seat hydration "
            "and tool-loop substrate."
        )
        # capability_tier and required_model_substring carry forward from
        # libs/agent_seat/registry.py for the slugs that need them.
        capability_tier = "inline-only" if slug == "oppie" else None
        required_substr = "multi-agent" if slug == "oppie" else None
        mcp_required = frontier_kind != "xai" or slug == "forge"
    else:
        purpose = (
            "Self-reflection assertion attribution and recollection-continuity "
            "anchor. Not a dispatch target; team_dispatch / frontier_dispatch "
            f"with role={slug!r} is rejected at admission."
        )
        capability_tier = None
        required_substr = None
        mcp_required = False

    description = (
        f"Frontier consult role on the {frontier_kind} family."
        if is_dispatch_target
        else f"{frontier_kind.capitalize()}-family seat — entity exists for "
        "self-reflection assertion attribution. Not a dispatch target."
    )

    return {
        "id": f"role:{slug}",
        "type": "role",
        "name": str(registry_entry["name"]),
        "description": description,
        "status": "confirmed",
        "workflow_state": "active",
        "attributes": {
            "purpose": purpose,
            "allowed_models": allowed_models,
            "default_model": default_model,
            "required_model_substring": required_substr,
            "frontier_kind": frontier_kind,
            "capability_tier": capability_tier,
            "required_tools": (
                ["cortex", "agent_bus"]
                if frontier_kind == "anthropic" and is_dispatch_target
                else []
            ),
            "mcp_required": mcp_required,
            "verification": [],
            "failure_mode": {
                "on_tool_unavailable": "continue-with-warning",
                "on_uncertainty": "surface-to-operator",
                "on_verification_fail": "warn-and-emit",
            },
            "output_schema": {
                "primary": {
                    "kind": "markdown" if is_dispatch_target else "none",
                    "target": "inline" if is_dispatch_target else "none",
                },
                "secondary": [],
            },
            "persona_seed_ref": persona_seed_ref,
        },
    }


def _build_plans(only: str | None) -> list[RoleSyncPlan]:
    plans: list[RoleSyncPlan] = []
    seen: set[str] = set()
    for slug, entry in AGENT_REGISTRY.items():
        if only and slug != only:
            continue
        payload = _build_role_payload(slug, entry)
        try:
            warnings = lint_role_payload(payload)
        except RoleLintError as exc:
            print(
                f"FATAL: role payload for {slug!r} fails self-concept lint:",
                file=sys.stderr,
            )
            for v in exc.violations:
                print(
                    f"  - {v.field_path} [{v.rule_class}] matched "
                    f"{v.matched_fragment!r}",
                    file=sys.stderr,
                )
            sys.exit(2)
        if warnings:
            for w in warnings:
                print(
                    f"WARN ({slug}): {w.field_path} [{w.rule_class}] "
                    f"matched {w.matched_fragment!r}",
                    file=sys.stderr,
                )
        plans.append(RoleSyncPlan(slug=slug, payload=payload))
        seen.add(slug)
    if only and only not in seen:
        print(f"FATAL: --only {only!r} not in AGENT_REGISTRY", file=sys.stderr)
        sys.exit(2)
    return plans


def _attrs_match(existing: dict[str, Any], desired: dict[str, Any]) -> bool:
    """Field-by-field equality of the desired attribute payload against stored."""
    for k, v in desired.items():
        if existing.get(k) != v:
            return False
    return True


def _sync_one(client: Any, plan: RoleSyncPlan, dry_run: bool, verbose: bool) -> str:
    resp = client.get(f"/entities/{plan.entity_id}")
    if resp.status_code == 404:
        if dry_run:
            if verbose:
                print(f"[dry-run] would create {plan.entity_id}")
            return "created"
        client.post("/entities", json=plan.payload).raise_for_status()
        if verbose:
            print(f"created {plan.entity_id}")
        return "created"
    resp.raise_for_status()
    body = resp.json()
    existing_attrs = (body.get("attributes") or {}) if isinstance(body, dict) else {}
    desired_attrs = plan.payload["attributes"]
    if (
        body.get("name") == plan.payload["name"]
        and body.get("description") == plan.payload["description"]
        and _attrs_match(existing_attrs, desired_attrs)
    ):
        if verbose:
            print(f"unchanged {plan.entity_id}")
        return "unchanged"
    if dry_run:
        if verbose:
            print(f"[dry-run] would update {plan.entity_id}")
        return "updated"
    update_body = {
        "name": plan.payload["name"],
        "description": plan.payload["description"],
        "attributes": {**existing_attrs, **desired_attrs},
    }
    client.patch(f"/entities/{plan.entity_id}", json=update_body).raise_for_status()
    if verbose:
        print(f"updated {plan.entity_id}")
    return "updated"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--dry-run", action="store_true", help="preview without writing")
    p.add_argument("--only", metavar="SLUG", help="sync only this role slug")
    p.add_argument("--verbose", "-v", action="store_true", help="per-item logging")
    return p.parse_args()


def main() -> int:
    args = _parse_args()
    plans = _build_plans(args.only)
    if not plans:
        print("no roles to sync", file=sys.stderr)
        return 1

    counts = {"created": 0, "updated": 0, "unchanged": 0}
    with make_sync_client(DEFAULT_CORTEX_URL) as client:
        for plan in plans:
            action = _sync_one(client, plan, args.dry_run, args.verbose)
            counts[action] += 1

    summary = (
        f"role sync complete: created={counts['created']} "
        f"updated={counts['updated']} unchanged={counts['unchanged']} "
        f"total={len(plans)}"
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
